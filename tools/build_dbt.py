"""
Writes the dbt property files from the same descriptions as the data dictionary and the Genie context, so
Unity Catalog comments (persist_docs), the dbt docs site and the copilot all describe each column the same way.

  dbt/models/marts/_marts.yml                 descriptions and tests of the gold models
  dbt/models/staging/_staging.yml             one line per staging model
  dbt/models/intermediate/_intermediate.yml   one line per intermediate model

  python tools/build_dbt.py          (after python local/run_local.py, which the column lists come from)
"""
from __future__ import annotations

import os
import sys

import yaml

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "tools"))
from build_docs import TABLE_COMMENT, describe, schema  # noqa: E402

DBT = os.path.join(ROOT, "dbt", "models")
COUNTRIES = ["MX", "CO", "BR"]
CARD_TYPES = ["credit", "debit"]
ACQUIRERS = ["A", "B", "C"]

# Grain of each gold model (tested as a unique combination), and the columns that must never be empty.
MARTS = {
    "gold_approval_daily": dict(grain=["business_date", "country", "card_type", "acquirer_id", "is_cross_border"]),
    "gold_approval_hourly": dict(grain=["hour_local_ts", "country", "card_type", "acquirer_id", "is_cross_border"]),
    "gold_decline_reasons_daily": dict(grain=["business_date", "country", "card_type", "acquirer_id", "attempt_seq",
                                              "canonical_status", "reason_code", "reason", "decline_type", "retryable",
                                              "mapping_version"]),
    "gold_cascade_daily": dict(grain=["business_date", "country", "card_type", "acquirer_id", "first_reason_code",
                                      "first_reason", "first_decline_type", "first_retryable",
                                      "first_mapping_version"]),
    "gold_cascade_alerts_daily": dict(grain=["business_date", "country", "acquirer_id", "first_reason_code"]),
    "gold_issuer_daily": dict(grain=["business_date", "country", "issuer_id", "card_type", "acquirer_id"]),
    "gold_funnel_daily": dict(grain=["business_date", "country", "card_type"]),
    "gold_anomalies_daily": dict(grain=["business_date", "country", "card_type"]),
    "gold_change_log": dict(grain=["change_id"]),
    "gold_change_guardrail": dict(grain=["change_id", "country", "card_type", "is_cross_border", "hours_since_change"]),
    "gold_dq_summary": dict(grain=["source_system", "failed_rule", "raw_status", "raw_code"]),
}
NOT_NULL = {"business_date", "hour_local_ts", "country", "card_type", "acquirer_id", "issuer_id", "change_id",
            "payments_sent", "is_anomaly", "is_alert", "failed_rule"}
ACCEPTED = {"country": COUNTRIES, "card_type": CARD_TYPES, "acquirer_id": ACQUIRERS, "source_system": ACQUIRERS,
            "decline_type": ["soft", "hard", "technical"], "first_decline_type": ["soft", "hard", "technical"]}
RELATIONSHIPS = {("gold_issuer_daily", "issuer_id"): ("ref_issuers", "issuer_id")}

STAGING = {
    "stg_payments": "silver_payments, one row per payment intent with its first attempt, retry and outcome.",
    "stg_auth_attempts": "silver_auth_attempts, authorization attempts that passed the quality rules.",
    "stg_dq_quarantine": "silver_dq_quarantine, attempts rejected by the quality rules.",
    "stg_change_log": "ref_change_log, production changes.",
    "stg_country_tz": "ref_country_tz, time zone and currency per country.",
}
INTERMEDIATE = {
    "int_payments_sent": "Payments that reached an acquirer, the denominator of every approval rate.",
    "int_failed_attempts": "Attempts the issuer did not approve, with the local business date of each attempt.",
}


def column_tests(model: str, column: str) -> list:
    tests = []
    if column in NOT_NULL:
        tests.append("not_null")
    if column in ACCEPTED and not (model == "gold_dq_summary" and column == "acquirer_id"):
        tests.append({"accepted_values": {"arguments": {"values": list(ACCEPTED[column])}}})
    if (model, column) in RELATIONSHIPS:
        table, field = RELATIONSHIPS[(model, column)]
        tests.append({"relationships": {"arguments": {"to": f"source('pipeline', '{table}')", "field": field}}})
    return tests


def marts_yml() -> dict:
    models = []
    for name, spec in MARTS.items():
        cols = []
        for col, _ in schema(name):
            entry = {"name": col, "description": describe(name, col)}
            tests = column_tests(name, col)
            if tests:
                entry["data_tests"] = tests
            cols.append(entry)
        models.append({"name": name, "description": TABLE_COMMENT[name],
                       "data_tests": [{"unique_combination_of_columns": {"arguments": {"combination_of_columns": list(spec["grain"])}}}],
                       "columns": cols})
    return {"version": 2, "models": models}


def simple_yml(models: dict) -> dict:
    return {"version": 2, "models": [{"name": n, "description": d} for n, d in models.items()]}


def write(path: str, data: dict) -> None:
    header = "# Generated by tools/build_dbt.py from the descriptions in tools/build_docs.py. Do not edit by hand.\n"
    with open(path, "w", encoding="utf-8") as f:
        f.write(header + yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=120))
    print(f"wrote {os.path.relpath(path, ROOT)}")


def main() -> None:
    write(os.path.join(DBT, "marts", "_marts.yml"), marts_yml())
    write(os.path.join(DBT, "staging", "_staging.yml"), simple_yml(STAGING))
    write(os.path.join(DBT, "intermediate", "_intermediate.yml"), simple_yml(INTERMEDIATE))


if __name__ == "__main__":
    main()
