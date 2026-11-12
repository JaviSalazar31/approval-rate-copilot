"""
The MCP server (mcp_server/approval_copilot_mcp.py): argument checks, the statement it sends to Databricks, typed
rows from the Statement Execution API, and the five tools called through the MCP protocol on the local pipeline
output, in process and over stdio.
"""
from __future__ import annotations

import asyncio
import os
import sys
from types import SimpleNamespace

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "mcp_server"))
import approval_copilot_mcp as srv  # noqa: E402

WINDOW = dict(country="CO", start_local="2026-08-13 14:00:00", end_local="2026-08-14 12:10:00")
LOCAL_OUTPUT = os.path.isdir(os.path.join(ROOT, "local", "out", "spark"))


def test_window_and_choices_are_checked():
    assert srv.check_window("co", "2026-08-13T14:00", "2026-08-14 12:10:00") == {
        "p_country": "CO", "p_start_local": "2026-08-13 14:00:00", "p_end_local": "2026-08-14 12:10:00"}
    for bad in [("AR", "2026-08-13 14:00:00", "2026-08-14 12:10:00"),        # not a country in the data
                ("CO", "2026-08-14 12:10:00", "2026-08-13 14:00:00"),        # end before start
                ("CO", "2026-08-13 14:00:00+00:00", "2026-08-14 12:10:00"),  # local time has no offset
                ("CO", "13/08/2026", "2026-08-14 12:10:00"),
                ("CO", "2026-05-01 00:00:00", "2026-09-01 00:00:00")]:       # longer than 92 days
        with pytest.raises(ValueError):
            srv.check_window(*bad)
    assert srv._choice("dimension", "Issuer", srv.DIMENSIONS) == "issuer"
    assert srv._choice("rank", "Lowest", srv.RANKS) == "lowest" and srv._choice("rate", "NULL", srv.RATES, True) is None
    with pytest.raises(ValueError):
        srv._choice("card_type", "prepaid", srv.CARD_TYPES)
    with pytest.raises(ValueError):
        srv._integer("baseline_days", 365, 1, 90, 14)
    assert srv._date("incident_date", " 2026-08-13") == "2026-08-13"
    assert srv._issuer("br-iss-03") == "BR-ISS-03" and srv._issuer("NULL") is None
    for bad in ("13/08/2026", "2026-08-13'; DROP TABLE x; --"):
        with pytest.raises(ValueError):
            srv._date("incident_date", bad)
    with pytest.raises(ValueError):
        srv._issuer("BR-ISS-03' OR 1=1 --")


def test_a_text_null_means_no_value():
    for empty in (None, "NULL", "null", " ", "None"):
        assert srv._choice("card_type", empty, srv.CARD_TYPES, optional=True) is None
        assert srv._integer("baseline_days", empty, 1, 90, 14) == 14


def test_no_value_from_the_caller_reaches_the_sql_text():
    for country in ("CO'; DROP TABLE gold_change_log; --", "CO OR 1=1"):
        with pytest.raises(ValueError):
            srv.check_window(country, "2026-08-13 14:00:00", "2026-08-14 12:10:00")
    with pytest.raises(ValueError):
        srv.check_window("CO", "2026-08-13 14:00:00'; DELETE FROM silver_payments; --", "2026-08-14 12:10:00")
    args = {**srv.check_window(**WINDOW), "p_card_type": "debit", "p_baseline_days": 14}
    sql, params = srv.build_statement("fn_drop_impact", args)
    assert sql == ("SELECT * FROM workspace.approval_copilot.fn_drop_impact(p_country => :p_country, "
                   "p_start_local => :p_start_local, p_end_local => :p_end_local, p_card_type => :p_card_type, "
                   "p_baseline_days => :p_baseline_days)")
    assert params[0] == {"name": "p_country", "value": "CO", "type": "STRING"}
    assert params[-1] == {"name": "p_baseline_days", "value": "14", "type": "INT"}
    sql, params = srv.build_statement("fn_approval_drivers", {**args, "p_card_type": None, "p_dimension": None})
    assert "p_card_type" not in sql and "p_dimension" not in sql  # left out: the function's default applies
    sql, params = srv.build_statement("fn_approval_drivers", {**args, "p_rate": "first_attempt", "p_rank": "lowest"})
    assert sql.endswith("p_baseline_days => :p_baseline_days, p_rate => :p_rate, p_rank => :p_rank)")
    with pytest.raises(ValueError):
        srv.build_statement("fn_drop_impact", args, schema="workspace.approval_copilot; DROP SCHEMA x")


class FakeStatements:
    """Stands in for WorkspaceClient().statement_execution: one pending response, then the result."""

    def __init__(self):
        self.calls = []

    def execute_statement(self, **kw):
        self.calls.append(kw)
        return SimpleNamespace(statement_id="s1", status=SimpleNamespace(state="PENDING"))

    def get_statement(self, statement_id):
        cols = [SimpleNamespace(name=n, type_name=SimpleNamespace(value=t))
                for n, t in [("window_payments", "LONG"), ("final_rate_change_pp", "DOUBLE"), ("note", "STRING")]]
        return SimpleNamespace(statement_id=statement_id, status=SimpleNamespace(state="SUCCEEDED"),
                               manifest=SimpleNamespace(schema=SimpleNamespace(columns=cols)),
                               result=SimpleNamespace(data_array=[["1004", "-10.95", None]]))


def test_databricks_backend_binds_parameters_and_types_the_rows(monkeypatch):
    pytest.importorskip("databricks.sdk")
    monkeypatch.setattr(srv.time, "sleep", lambda s: None)
    fake = FakeStatements()
    backend = srv.DatabricksBackend(client=SimpleNamespace(statement_execution=fake), warehouse_id="wh1")
    out = backend.run("fn_drop_impact", {**srv.check_window(**WINDOW), "p_card_type": "debit", "p_baseline_days": 14})
    call = fake.calls[0]
    assert call["warehouse_id"] == "wh1" and ":p_start_local" in call["statement"]
    assert [(p.name, p.value, p.type) for p in call["parameters"]][:2] == [
        ("p_country", "CO", "STRING"), ("p_start_local", "2026-08-13 14:00:00", "STRING")]
    assert out == {"function": "fn_drop_impact", "columns": ["window_payments", "final_rate_change_pp", "note"],
                   "rows": [[1004, -10.95, None]], "row_count": 1, "truncated": False}


async def _call_tools(server_or_params):
    from mcp import Client
    async with Client(server_or_params, read_timeout_seconds=240) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
        impact = await client.call_tool("drop_impact", {**WINDOW, "card_type": "debit"})
        drivers = await client.call_tool("approval_drivers", {**WINDOW, "card_type": "debit", "dimension": "issuer",
                                                              "rank": "lowest"})
        bad = await client.call_tool("drop_impact", {**WINDOW, "country": "AR"})
        incident = await client.call_tool("drop_impact", {"country": "CO", "card_type": "debit",
                                                          "incident_date": "2026-08-13"})
        no_window = await client.call_tool("drop_impact", {"country": "CO"})
        return tools, impact, drivers, bad, incident, no_window


def _rows(result) -> list[dict]:
    data = result.structured_content
    return [dict(zip(data["columns"], row)) for row in data["rows"]]


@pytest.mark.skipif(not LOCAL_OUTPUT, reason="needs the local pipeline output (python local/run_local.py)")
def test_tools_over_mcp_return_the_copilot_numbers():
    pytest.importorskip("mcp")
    tools, impact, drivers, bad, incident, no_window = asyncio.run(_call_tools(srv.create_server(srv.LocalBackend())))
    assert sorted(tools) == ["approval_drivers", "cascade_health", "changes_near", "decline_shift", "drop_impact"]
    assert all(t.annotations.read_only_hint for t in tools.values())
    row = _rows(impact)[0]
    assert (row["window_payments"], row["final_rate_change_pp"], row["lost_approvals"]) == (1004, -10.95, 110)
    assert round(row["lost_approved_usd"], 1) == 6354.2
    assert row["lost_conversions"] == 110 and row["window_intents"] == 1026
    issuers = _rows(drivers)   # the lowest issuer and the next two, with the gap to the next one
    assert [r["segment_value"] for r in issuers] == ["CO-ISS-07", "CO-ISS-03", "CO-ISS-05"]
    assert issuers[0]["gap_to_next_pp"] == 7.13 and {r["rate_type"] for r in issuers} == {"final"}
    assert bad.is_error and "country must be one of MX, CO, BR" in bad.content[0].text
    inc = _rows(incident)[0]   # the window comes from the change log: CHG-0813 to its rollback, CHG-0814
    assert (inc["window_start_local"], inc["window_end_local"], inc["lost_approvals"]) == (
        "2026-08-13 14:00:00", "2026-08-14 12:10:00", row["lost_approvals"])
    assert no_window.is_error and "incident_date" in no_window.content[0].text


@pytest.mark.skipif(not LOCAL_OUTPUT, reason="needs the local pipeline output (python local/run_local.py)")
def test_server_runs_over_stdio():
    pytest.importorskip("mcp")
    from mcp import StdioServerParameters
    params = StdioServerParameters(command=sys.executable, cwd=ROOT,
                                   args=[os.path.join(ROOT, "mcp_server", "approval_copilot_mcp.py"), "--backend", "local"])
    tools, impact, *_ = asyncio.run(_call_tools(params))
    assert len(tools) == 5 and _rows(impact)[0]["lost_approvals"] == 110
