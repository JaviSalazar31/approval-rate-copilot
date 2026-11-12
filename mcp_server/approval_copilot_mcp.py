"""
MCP server for the Approval Rate Copilot: the copilot's five diagnostic functions, read-only, for any MCP client
(Claude Desktop, Claude Code or another agent).

  python mcp_server/approval_copilot_mcp.py                   # the functions in Unity Catalog, on Databricks
  python mcp_server/approval_copilot_mcp.py --backend local   # the same SQL bodies on the local pipeline output

Databricks backend: the Databricks SDK's default authentication (a profile created with `databricks auth login`, or
DATABRICKS_HOST and DATABRICKS_TOKEN) and the SQL warehouse in DATABRICKS_WAREHOUSE_ID. Each call is one SELECT on
one of the five functions, run through the Statement Execution API with every argument bound as a parameter.

Local backend: run `python local/run_local.py` first; the functions run on local Spark with the SQL bodies of
sql/copilot, as in the offline evaluation.

The server builds no SQL from free text: arguments are checked against the values the data holds (countries, card
types, dimensions, timestamps, bounded integers) before any statement runs.
"""
from __future__ import annotations

import argparse
import datetime
import os
import re
import sys
import time
from typing import Any, TypedDict

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SCHEMA = os.environ.get("APPROVAL_COPILOT_SCHEMA", "workspace.approval_copilot")
MAX_ROWS = 200
COUNTRIES = ("MX", "CO", "BR")
CARD_TYPES = ("credit", "debit")
DIMENSIONS = ("acquirer", "issuer", "card_brand", "three_ds_authenticated", "merchant_segment",
              "routing_rule_version")
RATES = ("final", "first_attempt")
RANKS = ("lowest", "highest")
TS_FORMAT = "%Y-%m-%d %H:%M:%S"

# function name -> its parameters in order: (name, SQL type)
SIGNATURES = {
    "fn_drop_impact": [("p_country", "STRING"), ("p_start_local", "STRING"), ("p_end_local", "STRING"),
                       ("p_card_type", "STRING"), ("p_baseline_days", "INT"), ("p_incident_date", "STRING")],
    "fn_approval_drivers": [("p_country", "STRING"), ("p_start_local", "STRING"), ("p_end_local", "STRING"),
                            ("p_card_type", "STRING"), ("p_baseline_days", "INT"), ("p_dimension", "STRING"),
                            ("p_rate", "STRING"), ("p_rank", "STRING"), ("p_incident_date", "STRING"),
                            ("p_segment_values", "STRING")],
    "fn_decline_shift": [("p_country", "STRING"), ("p_start_local", "STRING"), ("p_end_local", "STRING"),
                         ("p_card_type", "STRING"), ("p_baseline_days", "INT"), ("p_incident_date", "STRING")],
    "fn_cascade_health": [("p_country", "STRING"), ("p_start_local", "STRING"), ("p_end_local", "STRING"),
                          ("p_card_type", "STRING"), ("p_baseline_days", "INT"), ("p_issuer", "STRING"),
                          ("p_incident_date", "STRING")],
    "fn_changes_near": [("p_country", "STRING"), ("p_start_local", "STRING"), ("p_end_local", "STRING"),
                        ("p_hours_before", "INT"), ("p_hours_after", "INT"), ("p_incident_date", "STRING")],
}


# ---------------------------------------------------------------------------------------------------------
# Argument checks
# ---------------------------------------------------------------------------------------------------------

def _empty(value) -> bool:
    """A model may pass the text "NULL" for an optional argument; it means no value."""
    return value is None or (isinstance(value, str) and value.strip().upper() in ("", "NULL", "NONE"))


def _choice(name: str, value, allowed: tuple[str, ...], optional: bool = False):
    if optional and _empty(value):
        return None
    v = str(value).strip()
    for a in allowed:
        if v.lower() == a.lower():
            return a
    raise ValueError(f"{name} must be one of {', '.join(allowed)}; got {value!r}")


def _timestamp(name: str, value) -> datetime.datetime:
    try:
        ts = datetime.datetime.fromisoformat(str(value).strip().replace("T", " "))
    except ValueError:
        raise ValueError(f"{name} must be a local time as 'YYYY-MM-DD HH:MM:SS'; got {value!r}") from None
    if ts.tzinfo is not None:
        raise ValueError(f"{name} is the country's local time, without a time zone offset; got {value!r}")
    return ts


def _date(name: str, value) -> str:
    try:
        return datetime.date.fromisoformat(str(value).strip()).isoformat()
    except ValueError:
        raise ValueError(f"{name} must be a date as 'YYYY-MM-DD'; got {value!r}") from None


def _issuer(value):
    if _empty(value):
        return None
    v = str(value).strip().upper()
    if not re.fullmatch(r"(MX|CO|BR)-ISS-\d{2}", v):
        raise ValueError(f"issuer must be a code such as 'BR-ISS-03'; got {value!r}")
    return v


def _integer(name: str, value, low: int, high: int, default: int) -> int:
    if _empty(value):
        return default
    try:
        v = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a whole number; got {value!r}") from None
    if not low <= v <= high:
        raise ValueError(f"{name} must be between {low} and {high}; got {v}")
    return v


def _segments(value):
    """Values to keep in approval_drivers, comma-separated: 'A,B', or a list such as ['A', 'B']."""
    if _empty(value):
        return None
    items = value if isinstance(value, (list, tuple)) else str(value).split(",")
    items = [str(v).strip() for v in items if str(v).strip()]
    if not items or len(items) > 20 or not all(re.fullmatch(r"[A-Za-z0-9_.\- ]{1,40}", v) for v in items):
        raise ValueError(f"segments must be up to 20 values such as 'A,B' or 'CO-ISS-03'; got {value!r}")
    return ",".join(items)


def window_args(country, start_local, end_local, incident_date) -> dict:
    """A window passed as start and end, or the incident window the functions read from the change log."""
    if not _empty(start_local) or not _empty(end_local):
        return check_window(country, start_local, end_local)
    if _empty(incident_date):
        raise ValueError("pass start_local and end_local, or incident_date")
    return {"p_country": _choice("country", country, COUNTRIES), "p_incident_date": _date("incident_date", incident_date)}


def check_window(country, start_local, end_local) -> dict:
    start, end = _timestamp("start_local", start_local), _timestamp("end_local", end_local)
    if end <= start:
        raise ValueError("end_local must be after start_local")
    if end - start > datetime.timedelta(days=92):
        raise ValueError("the window can be at most 92 days long")
    return {"p_country": _choice("country", country, COUNTRIES),
            "p_start_local": start.strftime(TS_FORMAT), "p_end_local": end.strftime(TS_FORMAT)}


# ---------------------------------------------------------------------------------------------------------
# Backends
# ---------------------------------------------------------------------------------------------------------

def build_statement(function: str, args: dict, schema: str = SCHEMA) -> tuple[str, list[dict]]:
    """The SELECT for one function call, with a parameter marker per argument that has a value. Arguments left
    out take the function's default. Returns the statement and its parameters for the Statement Execution API."""
    if not re.fullmatch(r"[A-Za-z_]\w*\.[A-Za-z_]\w*", schema):
        raise ValueError(f"APPROVAL_COPILOT_SCHEMA must be catalog.schema; got {schema!r}")
    types = dict(SIGNATURES[function])
    named, params = [], []
    for name, ptype in SIGNATURES[function]:
        if args.get(name) is None:
            continue
        named.append(f"{name} => :{name}")
        params.append({"name": name, "value": str(args[name]), "type": types[name]})
    return f"SELECT * FROM {schema}.{function}({', '.join(named)})", params


def _convert(value: str | None, type_name: str):
    if value is None:
        return None
    t = (type_name or "").upper()
    if t in ("INT", "LONG", "BIGINT", "SHORT", "BYTE"):
        return int(value)
    if t in ("DOUBLE", "FLOAT", "DECIMAL"):
        return float(value)
    if t == "BOOLEAN":
        return value.lower() == "true"
    return value


class DatabricksBackend:
    """Runs the functions in Unity Catalog through the SQL Statement Execution API."""

    def __init__(self, client=None, warehouse_id: str | None = None, timeout_s: float = 120):
        self.warehouse_id = warehouse_id or os.environ.get("DATABRICKS_WAREHOUSE_ID")
        if not self.warehouse_id:
            raise SystemExit("Set DATABRICKS_WAREHOUSE_ID to the ID of a SQL warehouse (Databricks > SQL Warehouses)")
        if client is None:
            from databricks.sdk import WorkspaceClient
            client = WorkspaceClient()  # default authentication: environment variables or a CLI profile
        self.client, self.timeout_s = client, timeout_s

    def run(self, function: str, args: dict) -> Table:
        from databricks.sdk.service.sql import StatementParameterListItem
        statement, params = build_statement(function, args)
        api = self.client.statement_execution
        resp = api.execute_statement(statement=statement, warehouse_id=self.warehouse_id, wait_timeout="30s",
                                     parameters=[StatementParameterListItem(**p) for p in params],
                                     row_limit=MAX_ROWS + 1)
        deadline = time.monotonic() + self.timeout_s
        while _state(resp) in ("PENDING", "RUNNING"):
            if time.monotonic() > deadline:
                api.cancel_execution(resp.statement_id)
                raise TimeoutError(f"{function} did not finish in {self.timeout_s:.0f} seconds")
            time.sleep(2)
            resp = api.get_statement(resp.statement_id)
        if _state(resp) != "SUCCEEDED":
            error = getattr(resp.status, "error", None)
            raise RuntimeError(f"{function} failed: {getattr(error, 'message', None) or _state(resp)}")
        columns = [(c.name, c.type_name.value if hasattr(c.type_name, "value") else c.type_name)
                   for c in resp.manifest.schema.columns]
        data = (resp.result.data_array if resp.result else None) or []
        rows = [[_convert(v, t) for v, (_, t) in zip(row, columns)] for row in data]
        return _table(function, [c for c, _ in columns], rows)


def _state(resp) -> str:
    state = resp.status.state
    return state.value if hasattr(state, "value") else str(state)


class LocalBackend:
    """Runs the same SQL bodies on the local pipeline output (local/run_local.py), as the offline evaluation did."""

    def __init__(self):
        sys.path.insert(0, os.path.join(ROOT, "local"))
        if not os.path.isdir(os.path.join(ROOT, "local", "out", "spark")):
            raise SystemExit("No local pipeline output: run `python local/run_local.py` first")
        from run_local import load_outputs
        from sql_runner import run_sql
        self.spark, self._run_sql = load_outputs(), run_sql

    def run(self, function: str, args: dict) -> Table:
        def literal(name, ptype):
            v = args[name]
            return str(int(v)) if ptype == "INT" else "'" + str(v).replace("'", "''") + "'"
        named = [f"{n} => {literal(n, t)}" for n, t in SIGNATURES[function] if args.get(n) is not None]
        pdf = self._run_sql(self.spark, f"SELECT * FROM {function}({', '.join(named)})", limit=MAX_ROWS + 1)
        rows = [[None if v != v else (v.item() if hasattr(v, "item") else v) for v in row]  # NaN to None
                for row in pdf.itertuples(index=False, name=None)]
        return _table(function, list(pdf.columns), rows)


class Table(TypedDict):
    """A function's result: its columns and rows (rates as fractions, _pp columns in percentage points)."""
    function: str
    columns: list[str]
    rows: list[list[Any]]
    row_count: int
    truncated: bool


def _table(function: str, columns: list[str], rows: list[list]) -> Table:
    return {"function": function, "columns": columns, "rows": rows[:MAX_ROWS], "row_count": min(len(rows), MAX_ROWS),
            "truncated": len(rows) > MAX_ROWS}


# ---------------------------------------------------------------------------------------------------------
# The MCP server
# ---------------------------------------------------------------------------------------------------------

INSTRUCTIONS = """\
Diagnostic functions of the Approval Rate Copilot, on synthetic card payments of a fictional payment orchestrator
in Mexico (MX), Colombia (CO) and Brazil (BR), routed to acquirers A, B and C. Data covers 2026-06-01 to 2026-08-29.
Windows are in the country's local time ('YYYY-MM-DD HH:MM:SS'; MX UTC-6, CO UTC-5, BR UTC-3), the end exclusive.
Every function covers domestic payments and compares the window with a baseline of the days before it.
To explain a drop: find the window with changes_near, or pass the date of the change that started an incident as
incident_date and every function reads the same window from the change log; size it with drop_impact, then use
approval_drivers, decline_shift and cascade_health. In cascade_health, map_stopped_retries = true means a decline-map change
stopped retries the acquirer used to get, which is a cause, not a correct classification. Rates are fractions
(0.756 is 75.6%), and _pp columns are percentage points.
All data is synthetic: issuers, merchants and acquirers are fictional codes, never real companies."""


def create_server(backend):
    from mcp.server.mcpserver import MCPServer
    from mcp.server.mcpserver.exceptions import ToolError
    from mcp.types import ToolAnnotations

    server = MCPServer("approval-rate-copilot", instructions=INSTRUCTIONS)
    read_only = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)

    def call(function, build_args):
        """Checks the arguments, then runs the function; a bad argument or a failed query goes back to the client
        as a tool error with its reason."""
        try:
            return backend.run(function, build_args())
        except (ValueError, RuntimeError, TimeoutError) as exc:
            raise ToolError(str(exc)) from None

    @server.tool(annotations=read_only)
    def drop_impact(country: str, start_local: str | None = None, end_local: str | None = None,
                    card_type: str | None = None, baseline_days: int = 14, incident_date: str | None = None) -> Table:
        """Size an incident or an approval drop against the baseline rate: payments, first-attempt and final approval
        rates, the change in points, lost approvals, lost approved volume (USD) and fees, plus the funnel of the
        same segment (conversion, risk blocks, 3DS abandonment). Pass incident_date, the date of the change that
        started an incident, and the window is read from the change log (window_start_local, window_end_local);
        or pass the window of the drop, from the change or hour it started to the change or hour it ended, not a
        calendar day. The lost_ columns are what the window approved below its baseline rates and the gained_
        columns what it approved above them; none is negative. lost_approvals counts the payments sent to an
        acquirer; lost_conversions counts all payment intents, including those that risk blocks (extra_risk_blocks)
        or 3DS abandonment (extra_3ds_abandons) stopped before any acquirer, so it already holds lost_approvals."""
        return call("fn_drop_impact", lambda: {
            **window_args(country, start_local, end_local, incident_date),
            "p_card_type": _choice("card_type", card_type, CARD_TYPES, optional=True),
            "p_baseline_days": _integer("baseline_days", baseline_days, 1, 90, 14)})

    @server.tool(annotations=read_only)
    def approval_drivers(country: str, start_local: str | None = None, end_local: str | None = None,
                         card_type: str | None = None, baseline_days: int = 14, dimension: str | None = None,
                         rate: str | None = None, rank: str | None = None, incident_date: str | None = None,
                         segments: str | None = None) -> Table:
        """Which segments moved approval between the baseline and the window: for each acquirer, issuer, card
        brand, 3DS outcome, merchant segment and routing version, the rate effect and the mix effect in points.
        The rate is final approval unless rate is 'first_attempt' (rate_type says which). Pass a dimension to get
        only its rows, ordered from the lowest window rate, with pp_below_next (how far each value sits below the
        next one). Pass rank ('lowest' or 'highest') to keep the three values at that end of each dimension, in
        ranking order, with gap_to_next_pp, and segments ('A,B') to keep only the values named. Pass the window,
        or incident_date to use the incident window that drop_impact reads from the change log."""
        return call("fn_approval_drivers", lambda: {**window_args(country, start_local, end_local, incident_date),
                                            "p_card_type": _choice("card_type", card_type, CARD_TYPES, optional=True),
                                            "p_baseline_days": _integer("baseline_days", baseline_days, 1, 90, 14),
                                            "p_dimension": _choice("dimension", dimension, DIMENSIONS, optional=True),
                                            "p_rate": _choice("rate", rate, RATES, optional=True),
                                            "p_rank": _choice("rank", rank, RANKS, optional=True),
                                            "p_segment_values": _segments(segments)})

    @server.tool(annotations=read_only)
    def decline_shift(country: str, start_local: str | None = None, end_local: str | None = None,
                      card_type: str | None = None, baseline_days: int = 14, incident_date: str | None = None) -> Table:
        """Which first-attempt decline codes grew in the window, per acquirer, as a share of the segment's payments,
        and the same code summed over acquirers (all_acquirers columns), which shows how the code itself moved when
        traffic moved from one acquirer to another. Pass the window, or incident_date for the incident window."""
        return call("fn_decline_shift", lambda: {**window_args(country, start_local, end_local, incident_date),
                                         "p_card_type": _choice("card_type", card_type, CARD_TYPES, optional=True),
                                         "p_baseline_days": _integer("baseline_days", baseline_days, 1, 90, 14)})

    @server.tool(annotations=read_only)
    def cascade_health(country: str, start_local: str | None = None, end_local: str | None = None,
                       card_type: str | None = None, baseline_days: int = 14, issuer: str | None = None,
                       incident_date: str | None = None) -> Table:
        """Whether failed first attempts were retried on another acquirer and recovered, per acquirer and code, in
        the baseline and the window, with how the decline-code map classified each code and whether it allowed a
        retry. map_stopped_retries is true when a decline-map change stopped retries the acquirer used to get.
        lost_recoveries is what stopped retries cost: the recoveries the window missed at the baseline recovery
        rate of the same acquirer and code. Pass issuer (such as 'BR-ISS-03') to look at one issuer. Pass the window,
        or incident_date for the incident window."""
        return call("fn_cascade_health", lambda: {**window_args(country, start_local, end_local, incident_date),
                                          "p_card_type": _choice("card_type", card_type, CARD_TYPES, optional=True),
                                          "p_baseline_days": _integer("baseline_days", baseline_days, 1, 90, 14),
                                          "p_issuer": _issuer(issuer)})

    @server.tool(annotations=read_only)
    def changes_near(country: str, start_local: str | None = None, end_local: str | None = None,
                     hours_before: int = 72, hours_after: int = 72, incident_date: str | None = None) -> Table:
        """Production changes that affect the country (routing, risk rules, decline-code maps, releases, partner
        notices) from hours_before the window to hours_after it, with each change's position: before, during or
        after the window. The changes after a drop show what ended it. Pass the window, or incident_date for the
        incident window."""
        return call("fn_changes_near", lambda: {**window_args(country, start_local, end_local, incident_date),
                                        "p_hours_before": _integer("hours_before", hours_before, 0, 720, 72),
                                        "p_hours_after": _integer("hours_after", hours_after, 0, 720, 72)})

    return server


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument("--backend", choices=("databricks", "local"), default="databricks")
    args = ap.parse_args(argv)
    backend = LocalBackend() if args.backend == "local" else DatabricksBackend()
    create_server(backend).run("stdio")


if __name__ == "__main__":
    main()
