"""
Generates the documentation that depends on the table schemas:

  docs/data_dictionary.md                         every table and column, from the local pipeline run
  evals/model_context.md                          what the model under test sees (same as the Genie agent)
  databricks/notebooks/05_column_comments.py      writes the same descriptions into Unity Catalog

Schemas are read from the Spark output of `python local/run_local.py`, so the
dictionary always matches what the pipeline really produces.

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
from pipeline_spec import TABLES, table_comment  # noqa: E402

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
        "actually produces (local run). All data is synthetic.\n",
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


def main():
    targets = {
        "docs/data_dictionary.md": build_dictionary(),
        "evals/model_context.md": build_model_context(),
        "databricks/notebooks/05_column_comments.py": build_comments_notebook(),
    }
    for rel, text in targets.items():
        path = os.path.join(ROOT, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        open(path, "w").write(text)
        print(f"wrote {rel} ({len(text):,} chars)")


if __name__ == "__main__":
    main()
