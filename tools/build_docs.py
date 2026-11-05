"""
Generates the documentation that depends on the table schemas:

  docs/data_dictionary.md                         every table and column, from the local pipeline run
  evals/model_context.md                          what the model under test sees (same as the Genie agent)
  databricks/notebooks/05_column_comments.py      writes the same descriptions into Unity Catalog
  databricks/notebooks/07_volume_alerts.py        the forecast alert as a job task, with its column descriptions
  docs/data_model.md                              entity diagram and table lineage (lineage read from the SQL)

Schemas are read from the Spark output of `python local/run_local.py`, so the
dictionary always matches what the pipeline produces.

  python tools/build_docs.py
"""
from __future__ import annotations

import glob
import os
import re
import sys

import pyarrow.parquet as pq

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, os.path.join(ROOT, "local"))
from build_databricks import CATALOG, FUNCTIONS, SCHEMA  # noqa: E402
from pipeline_spec import GRAIN, JOB_TABLES, SOURCES, TABLES, table_comment  # noqa: E402

SPARK_OUT = os.path.join(ROOT, "local", "out", "spark")

# Tables the copilot (Genie agent) can query, in the order users usually need them.
GENIE_TABLES = [
    "gold_approval_daily", "gold_approval_hourly", "gold_decline_reasons_daily", "gold_cascade_daily",
    "gold_cascade_alerts_daily", "gold_issuer_daily", "gold_funnel_daily", "gold_anomalies_daily",
    "gold_change_log", "silver_payments",
]
SUPPORT_TABLES = [
    "silver_payment_intents", "silver_auth_attempts", "silver_dq_quarantine", "gold_dq_summary",
    "ref_merchants", "ref_issuers", "ref_acquirers", "ref_routing_rules", "ref_decline_code_map",
    "ref_change_log", "ref_fx_rates", "ref_country_tz",
]


FIRST_ACQ = "First acquirer chosen by routing (A, B or C); results are attributed to it"
COL = {
    # keys and dimensions
    "business_date": "Local calendar date of the payment (country time zone)",
    "hour_local_ts": "Local hour (timestamp truncated to the hour, country time zone)",
    "country": "MX, CO or BR",
    "card_type": "credit or debit",
    "acquirer_id": FIRST_ACQ,
    "is_cross_border": "True when the card was issued abroad (foreign issuer)",
    "issuer_id": "Fictional issuer code (e.g. CO-ISS-03); INTL-ISS-* are foreign issuers",
    "card_brand": "visa, mastercard, amex (MX only) or elo (BR only)",
    "merchant_id": "Fictional merchant (e.g. MX-M03); UNKNOWN when the intent had no merchant",
    "merchant_segment": "retail, marketplace, food_delivery, travel, digital_subscriptions, gaming, education",
    "mcc": "Merchant category code",
    "currency": "MXN, COP or BRL",
    "amount": "Amount in local currency",
    "amount_usd": "Amount in USD (fixed synthetic FX rates)",
    "installments": "Number of installments (1 = single payment)",
    "channel": "web or app",
    # volumes and outcomes
    "payments_sent": "Payments that reached at least one acquirer",
    "first_attempt_approved": "Payments approved on the first attempt",
    "final_approved": "Payments approved on the first attempt or after a cascade (retry on a second acquirer)",
    "cascaded": "Payments retried on a second acquirer",
    "recovered_by_cascade": "Payments declined first and approved on the retry",
    "first_soft_declines": "First attempts declined with a soft (temporary) reason",
    "first_hard_declines": "First attempts declined with a hard (permanent) reason",
    "first_technical_failures": "First attempts that ended in technical error or timeout",
    "attempted_usd": "Amount of payments sent, USD",
    "approved_usd": "Amount of approved payments, USD",
    "fees_usd": "Acquirer fees on the approved amount, USD",
    "p95_latency_ms": "p95 latency of first attempts, ms (approximate percentile, an observed value; never average or sum it across rows: recompute from silver_payments)",
    "first_attempt_approval_rate": "first_attempt_approved / payments_sent (recompute from counts when aggregating)",
    "final_approval_rate": "final_approved / payments_sent (recompute from counts when aggregating)",
    "approved_volume_rate": "approved_usd / attempted_usd (recompute from sums when aggregating)",
    # decline reasons
    "attempt_seq": "1 = first attempt, 2 = cascade retry",
    "canonical_status": "Normalized outcome: DECLINED_SOFT, DECLINED_HARD, TECHNICAL_ERROR or TIMEOUT",
    "reason_code": "ISO 8583 response code (e.g. 05, 51, 91, 1A) or TECHNICAL_ERROR / TIMEOUT",
    "reason": "Reason in plain words (e.g. Do not honor, Insufficient funds)",
    "decline_type": "How the normalization map classified the reason: soft, hard or technical",
    "retryable": "Whether the map allowed retrying on another acquirer",
    "mapping_version": "Version of the decline-code map active at the time (v2.2, v2.3, v2.3.1)",
    "attempts": "Number of failed attempts",
    # cascade
    "first_reason_code": "Reason of the failed first attempt (ISO code or TECHNICAL_ERROR / TIMEOUT)",
    "first_reason": "Reason of the failed first attempt in plain words",
    "first_decline_type": "How the normalization map classified the first attempt: none (approved), soft, hard, technical",
    "first_retryable": "Whether the map allowed retrying the first attempt on another acquirer",
    "first_mapping_version": "Version of the decline-code map active at the time of the first attempt",
    "first_attempt_failures": "Payments whose first attempt was not approved",
    "recovered": "Of the cascaded payments, those approved on the retry",
    "failures": "Payments whose first attempt failed with this reason that day",
    "cascade_rate": "cascaded / failures",
    "baseline_cascade_rate": "Cascade rate on the healthy days (cascade rate of at least 50%) among the previous 56 days with failures, same country, acquirer and reason; collapsed days are left out, so a long outage does not lower it",
    "mapping_versions": "Decline-code map versions active that day (comma-separated)",
    "is_alert": "True when the cascade rate collapsed: at least 50 pp below its healthy baseline, with enough volume",
    # funnel
    "payment_intents": "Payments created at checkout",
    "blocked_by_risk": "Intents blocked by the risk engine (including review timeouts)",
    "abandoned_3ds": "Intents abandoned by the customer in the 3DS challenge",
    "sent_to_acquirer": "Intents that reached an acquirer",
    "routed_without_valid_attempt": "Routed intents whose acquirer response failed data-quality rules",
    "approved": "Intents finally approved",
    "conversion_rate": "approved / payment_intents",
    "risk_block_rate": "blocked_by_risk / payment_intents",
    # anomalies
    "baseline_rate": "Average daily final approval over the previous 28 days",
    "delta_pp": "Difference vs baseline, percentage points",
    "z_history": "Drop measured in standard deviations of the previous 28 days",
    "z_binomial": "Drop measured against the sampling error expected for that day's volume",
    "is_anomaly": "Abnormal drop: at least 3 pp below baseline, z_history <= -3 and z_binomial <= -4",
    # change log
    "change_id": "Change identifier (CHG-* internal change, EXT-* partner notice)",
    "changed_at_utc": "When the change happened (UTC)",
    "changed_at_mexico_city": "Same, Mexico City time (the local time of MX windows)",
    "changed_at_bogota": "Same, Bogota time (the local time of CO windows)",
    "changed_at_sao_paulo": "Same, Sao Paulo time (the local time of BR windows)",
    "change_type": "release, routing, rollback, risk_rules, decline_mapping, external_notice or merchant_onboarding",
    "scope": "Country code(s), acquirer id, or ALL",
    "description": "What changed",
    "owner_team": "Team that owns the change",
    # payment-level columns
    "payment_id": "Payment identifier (synthetic)",
    "three_ds_requested": "Whether checkout asked for 3DS authentication",
    "three_ds_flow": "frictionless or challenge when 3DS was requested; empty otherwise",
    "created_ts_utc": "When the payment intent was created (UTC)",
    "created_ts_local": "When the payment intent was created (country local time)",
    "orchestrator_status": "ROUTED, BLOCKED_RISK, REVIEW_TIMEOUT or CUSTOMER_ABANDONED_3DS",
    "routing_rule_version": "Routing rules version that chose the first acquirer (v12, v13, v14); empty if not routed",
    "missing_attempt_data": "Routed, but the acquirer response failed data-quality rules (see silver_dq_quarantine)",
    "first_acquirer_id": "First acquirer chosen by routing (A, B or C)",
    "first_status": "Canonical status of the first attempt",
    "first_iso_code": "ISO 8583 response code of the first attempt (00 = approved)",
    "first_latency_ms": "Latency of the first authorization attempt, ms; the p95 of a period is percentile_approx(first_latency_ms, 0.95) over its payments",
    "is_3ds_authenticated": "Whether the payment was authenticated with 3DS",
    "is_cascaded": "Whether the payment was retried on a second acquirer",
    "cascade_acquirer_id": "Acquirer used for the retry",
    "cascade_status": "Canonical status of the retry",
    "cascade_iso_code": "ISO 8583 response code of the retry",
    "cascade_approved": "Whether the retry was approved",
    "final_status": "Final canonical status of the payment (empty when the acquirer response failed quality rules)",
    "hour_local": "Local hour of day (0-23)",
    "approving_acquirer_id": "Acquirer that approved the payment (first or retry)",
    "fee_usd": "Acquirer fee on the approved amount, USD",
    # supporting tables
    "source_event_id": "Event id in the acquirer's own feed",
    "attempt_ts_utc": "When the authorization attempt happened (UTC)",
    "received_ts_utc": "When the response reached the orchestrator (Acquirer A only)",
    "raw_status": "Status exactly as sent by the source system",
    "raw_code": "Response code exactly as sent by the source system",
    "latency_ms": "Authorization latency, ms",
    "iso_code": "ISO 8583 response code after normalization",
    "is_authorized": "True for APPROVED and REVERSED (the issuer approved the authorization)",
    "failed_rule": "Data-quality rule the row failed",
    "source_system": "Acquirer (A, B, C) or ORCHESTRATOR",
    "rejected_rows": "Rows rejected by the rule",
    "first_seen_utc": "First rejected attempt (UTC)",
    "last_seen_utc": "Last rejected attempt (UTC)",
    "segment": "Merchant segment",
    "avg_ticket_usd": "Average ticket, USD (generator parameter)",
    "onboarded_ts_utc": "When the merchant went live (UTC)",
    "issuer_country": "Country of the issuer, or INTL for foreign issuers",
    "market_share": "Share of the country's cards issued by this issuer (generator parameter)",
    "issuer_type": "domestic or cross_border",
    "acquirer_name": "Fictional acquirer name",
    "countries": "Countries where the acquirer operates",
    "fee_bps": "Fee in basis points of the approved amount (synthetic)",
    "raw_format": "How the acquirer formats its files",
    "routing_version": "Routing rules version",
    "valid_from_utc": "Start of validity (UTC, inclusive)",
    "valid_to_utc": "End of validity (UTC, exclusive)",
    "traffic_share": "Share of the segment's domestic traffic sent to the acquirer",
    "usd_rate": "Units of local currency per USD (synthetic, fixed)",
    "time_zone": "IANA time zone used for local dates",
    # truncated card number (silver_payment_intents)
    "card_bin": "First six digits of the card number (BIN). Synthetic BINs start with 00, outside the ranges card networks use, so they match no real bank",
    "card_last4": "Last four digits of the card number. On Databricks a column mask shows them only to the payments_support group",
    # forecast alert (gold_volume_alerts_daily)
    "expected_approved": "Approved payments the forecast expected (weekday, payday and trend terms, fitted on the previous 56 days); empty in the first 42 days",
    "lower_bound": "Lower bound of the forecast's 99.9% one-sided prediction interval",
    "pct_vs_expected": "Approved payments versus the forecast, in percent",
    "z_score": "Distance from the forecast in prediction standard errors (log scale)",
}
# Columns whose meaning differs by table.
COL_BY_TABLE = {
    ("gold_decline_reasons_daily", "acquirer_id"): "Acquirer that processed the attempt (A, B or C)",
    ("silver_auth_attempts", "acquirer_id"): "Acquirer that processed the attempt (A, B or C)",
    ("silver_dq_quarantine", "acquirer_id"): "Acquirer that sent the rejected response (A, B or C)",
    ("silver_auth_attempts", "canonical_status"): "Normalized outcome: APPROVED, REVERSED, DECLINED_SOFT, DECLINED_HARD, TECHNICAL_ERROR or TIMEOUT",
    ("silver_dq_quarantine", "canonical_status"): "Normalized outcome (empty when the code was not in the map)",
    ("ref_decline_code_map", "canonical_status"): "APPROVED, REVERSED, DECLINED_SOFT, DECLINED_HARD, TECHNICAL_ERROR, TIMEOUT, BLOCKED_BY_RISK or ABANDONED",
    ("ref_decline_code_map", "decline_type"): "none, soft, hard or technical",
    ("ref_change_log", "changed_at_utc"): "When the change happened (UTC)",
    ("ref_merchants", "country"): "Country where the merchant sells (MX, CO or BR)",
    ("ref_routing_rules", "scope"): "domestic (cross-border cards always go to Acquirer A)",
    ("silver_payment_intents", "first_acquirer_id"): "Acquirer chosen by routing for the first attempt (empty if not routed)",
    ("gold_issuer_daily", "issuer_id"): "Fictional issuer code (e.g. CO-ISS-03); INTL-ISS-* are foreign issuers. This table is daily and includes cross-border payments: to rank domestic issuers inside a window, use fn_approval_drivers (dimension 'issuer')",
    ("gold_volume_alerts_daily", "approved"): "Approved payments that day, first attempt or after a retry, cross-border included",
    ("gold_volume_alerts_daily", "is_alert"): "True when the day is below the lower bound and at least 8% under the forecast",
    ("gold_cascade_alerts_daily", "is_alert"): "True when the cascade rate collapsed: at least 50 pp below its healthy baseline, with enough volume. To find when an alert began, query from the start of the data (2026-06-01)",
}

ARROW_TO_SQL = [
    (r"^date32", "date"), (r"^timestamp", "timestamp"), (r"^int64$", "bigint"), (r"^int32$", "int"),
    (r"^double$", "double"), (r"^bool$", "boolean"), (r"^string$", "string"),
    (r"^decimal128\((\d+), (\d+)\)$", r"decimal(\1,\2)"),
]


def sql_type(arrow_type: str) -> str:
    for pat, rep in ARROW_TO_SQL:
        if re.match(pat, arrow_type):
            return re.sub(pat, rep, arrow_type) if "\\" in rep else rep
    return arrow_type


def schema(table):
    files = sorted(glob.glob(os.path.join(SPARK_OUT, table, "*.parquet")))
    if not files:
        raise SystemExit(f"No local output for {table}. Run: python local/run_local.py")
    return [(f.name, sql_type(str(f.type))) for f in pq.read_schema(files[0])]


def describe(table, column):
    text = COL_BY_TABLE.get((table, column)) or COL.get(column)
    if not text:
        raise SystemExit(f"Missing description for {table}.{column}")
    return text


TABLE_COMMENT = {name: table_comment(name, comment) for name, _, _, comment, _ in TABLES}
TABLE_COMMENT.update({name: table_comment(name, comment) for name, comment in JOB_TABLES})


def table_md(table, heading="###"):
    lines = [f"{heading} `{table}`", "", TABLE_COMMENT[table], "",
             "| Column | Type | Meaning |", "|---|---|---|"]
    for col, typ in schema(table):
        lines.append(f"| `{col}` | {typ} | {describe(table, col)} |")
    return "\n".join(lines) + "\n"


def functions_md(heading="###"):
    out = []
    for f in FUNCTIONS:
        sig = ", ".join(f"{n} {t}" + (f" DEFAULT {d}" if d is not None else "") for n, t, d, _ in f["params"])
        out.append(f"{heading} `{f['name']}({sig})`\n\n{f['comment']}\n\nReturns: `{f['returns']}`\n")
    return "\n".join(out)


def general_instructions():
    text = open(os.path.join(ROOT, "docs", "genie_space_setup.md")).read()
    m = re.search(r"## 2\. General instructions.*?```\n(.*?)```", text, re.S)
    if not m:
        raise SystemExit("General instructions block not found in docs/genie_space_setup.md")
    return m.group(1).strip()


def build_dictionary():
    parts = [
        "# Data dictionary\n",
        f"Catalog `{CATALOG}`, schema `{SCHEMA}`. Generated by `tools/build_docs.py` from the schemas the pipeline "
        "produces (local run). All data is synthetic.\n",
        "Conventions: rates are fractions (0.8654 = 86.54%); `business_date`, `hour_local_ts` and `*_local` "
        "columns are in the country's local time (MX UTC-6, CO UTC-5, BR UTC-3, no daylight saving time); "
        "`*_utc` columns are UTC.\n",
        "## Tables the copilot can query\n",
    ]
    parts += [table_md(t) for t in GENIE_TABLES]
    parts.append("## Supporting tables (pipeline internals)\n")
    parts.append("Bronze tables (`bronze_orchestrator`, `bronze_acquirer_a`, `bronze_acquirer_b`, `bronze_acquirer_c`) "
                 "keep every source column as received, as strings, plus `_source_file` and `_ingested_at`. "
                 "The source formats are described in `ref_acquirers.raw_format`. On Databricks, `read_files` also "
                 "adds a `_rescued_data` column (values that did not fit the schema); the local run does not have it.\n")
    parts += [table_md(t) for t in SUPPORT_TABLES]
    parts.append("## Monitoring table written by the job\n")
    parts.append("Job task `volume_alerts` (notebook 07), from `gold_approval_daily`. The copilot does not query it.\n")
    parts += [table_md(name) for name, _ in JOB_TABLES]
    parts.append("## Copilot functions (Unity Catalog SQL table functions)\n")
    parts.append(functions_md())
    return "\n".join(parts)


def example_sql():
    text = open(os.path.join(ROOT, "docs", "genie_space_setup.md")).read()
    m = re.search(r"## 4\. Example SQL.*?```sql\n(.*?)```", text, re.S)
    if not m:
        raise SystemExit("Example SQL block not found in docs/genie_space_setup.md")
    return m.group(1).strip()


def build_model_context():
    parts = [
        "# Copilot context (identical to what the Genie agent receives)\n",
        "## Instructions\n",
        general_instructions() + "\n",
        "## Example SQL\n",
        "```sql\n" + example_sql() + "\n```\n",
        "## Tables\n",
        f"Catalog `{CATALOG}`, schema `{SCHEMA}`. Rates are fractions (0.8654 = 86.54%).\n",
    ]
    parts += [table_md(t) for t in GENIE_TABLES]
    parts.append("## Functions (Unity Catalog SQL table functions)\n")
    parts.append(functions_md())
    return "\n".join(parts)


def py_str(s):
    return repr(s)


def build_comments_notebook():
    lines = [
        "# Databricks notebook source",
        "# MAGIC %md",
        "# MAGIC # 05 · Column descriptions for the copilot",
        "# MAGIC Writes a description on every column of the tables the Genie agent uses, so the model reads",
        "# MAGIC what each column means straight from Unity Catalog. Generated by `tools/build_docs.py`.",
        "# MAGIC",
        "# MAGIC Run it after the tables exist (pipeline or notebook 02), and again after any full refresh of",
        "# MAGIC the pipeline: it is idempotent. It detects whether each table is a materialized view (pipeline)",
        "# MAGIC or a regular table (notebook 02). Table descriptions of materialized views are left to the",
        "# MAGIC pipeline, which sets them in its own definitions; only column descriptions are written there.",
        "",
        "# COMMAND ----------",
        "",
        f'CATALOG, SCHEMA = "{CATALOG}", "{SCHEMA}"',
        "",
        "TABLE_COMMENTS = {",
    ]
    for t in GENIE_TABLES:
        lines.append(f"    {py_str(t)}: {py_str(TABLE_COMMENT[t])},")
    lines.append("}")
    lines.append("")
    lines.append("COLUMN_COMMENTS = {")
    for t in GENIE_TABLES:
        lines.append(f"    {py_str(t)}: {{")
        for col, _ in schema(t):
            lines.append(f"        {py_str(col)}: {py_str(describe(t, col))},")
        lines.append("    },")
    lines.append("}")
    lines += [
        "",
        "# COMMAND ----------",
        "",
        "def sql_literal(text):",
        "    return \"'\" + text.replace(\"\\\\\", \"\\\\\\\\\").replace(\"'\", \"\\\\'\") + \"'\"",
        "",
        "",
        "def table_kind(full_name):",
        "    rows = spark.sql(f\"DESCRIBE TABLE EXTENDED {full_name}\").collect()  # noqa: F821",
        "    info = {r[0].strip(): (r[1] or '').strip() for r in rows if r[0]}",
        "    return \"MATERIALIZED VIEW\" if \"MATERIALIZED\" in info.get(\"Type\", \"\").upper() else \"TABLE\"",
        "",
        "",
        "done, failed = 0, []",
        "for table, columns in COLUMN_COMMENTS.items():",
        "    full = f\"{CATALOG}.{SCHEMA}.{table}\"",
        "    kind = table_kind(full)",
        "    if kind == \"TABLE\":  # the pipeline owns the table comments of its materialized views",
        "        try:",
        "            spark.sql(f\"COMMENT ON TABLE {full} IS {sql_literal(TABLE_COMMENTS[table])}\")  # noqa: F821",
        "        except Exception as e:",
        "            failed.append((table, \"<table>\", str(e).splitlines()[0]))",
        "    for column, text in columns.items():",
        "        try:",
        "            spark.sql(f\"ALTER {kind} {full} ALTER COLUMN {column} COMMENT {sql_literal(text)}\")  # noqa: F821",
        "            done += 1",
        "        except Exception as e:",
        "            failed.append((table, column, str(e).splitlines()[0]))",
        "",
        "print(f\"{done} column descriptions written\")",
        "for f in failed[:20]:",
        "    print(\"could not write:\", f)",
        "",
    ]
    return "\n".join(lines)


ER_DIAGRAM = """erDiagram
  ref_merchants ||--o{ silver_payment_intents : merchant_id
  ref_issuers ||--o{ silver_payment_intents : issuer_id
  silver_payment_intents ||--o{ silver_auth_attempts : payment_id
  ref_acquirers ||--o{ silver_auth_attempts : acquirer_id
  ref_decline_code_map ||--o{ silver_auth_attempts : "acquirer, raw status, raw code, version"
  silver_payment_intents ||--|| silver_payments : payment_id
  ref_fx_rates ||--o{ silver_payments : currency
  ref_country_tz ||--o{ silver_payments : country
  silver_payment_intents {
    string payment_id PK
    string merchant_id FK
    string issuer_id FK
    timestamp created_ts_utc
    string orchestrator_status
    string routing_rule_version
    string card_bin
    string card_last4 "masked on Databricks"
  }
  silver_auth_attempts {
    string acquirer_id FK
    string source_event_id PK
    string payment_id FK
    int attempt_seq
    string mapping_version
    string canonical_status
    boolean retryable
  }
  silver_payments {
    string payment_id PK
    string first_acquirer_id
    boolean is_cascaded
    boolean final_approved
    date business_date
    double approved_usd
  }
  ref_decline_code_map {
    string mapping_version PK
    string source_system PK
    string raw_status PK
    string raw_code PK
    string canonical_status
    boolean retryable
  }
"""


def lineage():
    """Upstream tables of each dataset, read from its SQL body (and the job's Python for job tables)."""
    names = [t[0] for t in TABLES]
    edges = []
    for name, rel, *_ in TABLES:
        body = re.sub(r"--[^\n]*", "", open(os.path.join(ROOT, "sql", rel)).read())
        for src in sorted(set(re.findall(r"\{src:([a-z_]+)\}", body))):
            edges.append((f"feed_{src}" if SOURCES[src][0] == "raw" else f"csv_{src}", name))
        for other in names:
            if other != name and re.search(rf"\b{other}\b", body):
                edges.append((other, name))
    for name, _ in JOB_TABLES:
        edges.append(("gold_approval_daily", name))
    return edges


def build_data_model():
    edges = lineage()
    layers = {"bronze": [], "silver": [], "gold": [], "ref": []}
    for name in [t[0] for t in TABLES] + [n for n, _ in JOB_TABLES]:
        layers[name.split("_")[0]].append(name)
    feeds = sorted({a for a, _ in edges if a.startswith(("feed_", "csv_"))})
    flow = ["flowchart LR"]
    flow.append("  subgraph files[Landing files]")
    flow += [f"    {f}[{f.split('_', 1)[1]}]" for f in feeds]
    flow.append("  end")
    titles = {"ref": "Reference", "bronze": "Bronze", "silver": "Silver", "gold": "Gold"}
    for layer in ("ref", "bronze", "silver", "gold"):
        flow.append(f"  subgraph {layer}[{titles[layer]}]")
        flow += [f"    {n}" for n in layers[layer]]
        flow.append("  end")
    flow += [f"  {a} --> {b}" for a, b in edges]
    grain_rows = "\n".join(f"| `{n}` | {GRAIN[n]} |" for n in [t[0] for t in TABLES] + [n for n, _ in JOB_TABLES]
                           if n.startswith("gold_") and n in GRAIN)
    return "\n".join([
        "# Data model",
        "",
        "Generated by `tools/build_docs.py`. The lineage below is read from the SQL of each table, so it always matches",
        "the code. On Databricks, Unity Catalog also records table lineage when the pipeline runs (notebook 08 lists",
        "it). Column details are in the [data dictionary](data_dictionary.md).",
        "",
        "## Entities and keys (silver and reference)",
        "",
        "One payment intent from the orchestrator has zero, one or two authorization attempts: none when risk blocked",
        "it or the shopper abandoned 3DS, the first acquirer's attempt, and one retry on another acquirer when the",
        "decline was retryable. `silver_payments` joins them into one row per payment. The versioned decline-code map",
        "classifies each attempt with the map version active at that moment.",
        "",
        "```mermaid",
        ER_DIAGRAM.rstrip(),
        "```",
        "",
        "## Gold tables and their grain",
        "",
        "| Table | Grain |",
        "|---|---|",
        grain_rows,
        "",
        "## Table lineage",
        "",
        "`gold_volume_alerts_daily` is written by a job task after the pipeline; every other table is a pipeline",
        "dataset.",
        "",
        "```mermaid",
        "\n".join(flow),
        "```",
        "",
    ])


def build_volume_alerts_notebook():
    name, _ = JOB_TABLES[0]
    src = open(os.path.join(ROOT, "monitoring", "volume_forecast.py")).read()
    src = src.replace("from __future__ import annotations\n", "")
    comments = {col: describe(name, col) for col, _ in schema(name)}
    return (
        "# Databricks notebook source\n"
        "# MAGIC %md\n"
        "# MAGIC # 07 · Forecast alert on approved payments\n"
        "# MAGIC Runs after the pipeline. Forecasts each day's approved payments per country and card type from\n"
        "# MAGIC the previous 56 days, with weekday and payday terms, and flags the days that fall well below.\n"
        "# MAGIC Writes `gold_volume_alerts_daily`. The backtest is in `analysis/detector_backtest.py`.\n"
        "# MAGIC\n"
        "# MAGIC Copy of `monitoring/volume_forecast.py` made by `tools/build_docs.py`. Do not edit by hand.\n"
        "\n# COMMAND ----------\n\n"
        + src +
        "\n# COMMAND ----------\n\n"
        f'SOURCE = "{CATALOG}.{SCHEMA}.gold_approval_daily"\n'
        f'TARGET = "{CATALOG}.{SCHEMA}.{name}"\n'
        f"TABLE_COMMENT = {py_str(TABLE_COMMENT[name])}\n"
        "COLUMN_COMMENTS = " + "{\n" + "".join(f"    {py_str(c)}: {py_str(t)},\n" for c, t in comments.items()) + "}\n"
        "\n# COMMAND ----------\n\n"
        "alerts = volume_alerts(daily_approved(spark.table(SOURCE).toPandas()))  # noqa: F821\n"
        "(spark.createDataFrame(for_spark(alerts), SPARK_SCHEMA)  # noqa: F821\n"
        "    .write.mode(\"overwrite\").option(\"overwriteSchema\", \"true\").saveAsTable(TARGET))\n"
        "\n\n"
        "def sql_literal(text):\n"
        "    return \"'\" + text.replace(\"\\\\\", \"\\\\\\\\\").replace(\"'\", \"\\\\'\") + \"'\"\n"
        "\n\n"
        "spark.sql(f\"COMMENT ON TABLE {TARGET} IS {sql_literal(TABLE_COMMENT)}\")  # noqa: F821\n"
        "for column, text in COLUMN_COMMENTS.items():\n"
        "    spark.sql(f\"ALTER TABLE {TARGET} ALTER COLUMN {column} COMMENT {sql_literal(text)}\")  # noqa: F821\n"
        "flagged = alerts[alerts[\"is_alert\"]]\n"
        "print(f\"{int(alerts['z_score'].notna().sum()):,} segment-days scored; {len(flagged)} alerts\")\n"
        "print(flagged.to_string(index=False))\n"
    )


def main():
    targets = {
        "docs/data_dictionary.md": build_dictionary(),
        "evals/model_context.md": build_model_context(),
        "databricks/notebooks/05_column_comments.py": build_comments_notebook(),
        "databricks/notebooks/07_volume_alerts.py": build_volume_alerts_notebook(),
        "docs/data_model.md": build_data_model(),
    }
    for rel, text in targets.items():
        path = os.path.join(ROOT, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        open(path, "w").write(text)
        print(f"wrote {rel} ({len(text):,} chars)")


if __name__ == "__main__":
    main()
