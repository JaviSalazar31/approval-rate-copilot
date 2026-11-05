"""
The Unity Catalog metric views (databricks/metric_views/*.yml) compute the same numbers as the evaluation's
reference SQL. Local Spark has no metric views, so each measure's expression is grouped by the requested
dimensions over the local gold table, which is what MEASURE() does on Databricks.

Run after `python local/run_local.py`:
    python -m pytest tests/test_metric_views.py
    python tests/test_metric_views.py
"""
from __future__ import annotations

import functools
import json
import os
import sys

import yaml

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "local"))
VIEWS = os.path.join(ROOT, "databricks", "metric_views")


@functools.lru_cache(maxsize=1)
def spark():
    from run_local import load_outputs
    return load_outputs()


def spec(view: str) -> dict:
    with open(os.path.join(VIEWS, f"{view}.yml"), encoding="utf-8") as f:
        return yaml.safe_load(f)


def measure(view: str, measures: list[str], dims: list[str], where: str) -> list[dict]:
    """SELECT dims, MEASURE(m) ... GROUP BY dims, spelled out for local Spark."""
    s = spec(view)
    dim = {d["name"]: d["expr"] for d in s["dimensions"]}
    mea = {m["name"]: m["expr"] for m in s["measures"]}
    table = s["source"].split(".")[-1]
    cols = [f"{dim[d]} AS {d}" for d in dims] + [f"{mea[m]} AS {m}" for m in measures]
    sql = f"SELECT {', '.join(cols)} FROM {table} WHERE {where}"
    if dims:
        sql += " GROUP BY " + ", ".join(dim[d] for d in dims)
    return [r.asDict() for r in spark().sql(sql).collect()]


def reference(qid: str) -> dict:
    """First row of the evaluation's reference SQL for a question, run locally."""
    q = next(x for x in json.load(open(os.path.join(ROOT, "evals", "eval_set.json"))) if x["id"] == qid)
    sql = q["gold_sql"][0].replace("workspace.approval_copilot.", "")
    return spark().sql(sql).collect()[0].asDict()


def test_definitions_are_valid():
    for view in ("approval_metrics", "decline_metrics"):
        s = spec(view)
        assert s["version"] == 1.1 and s["source"].startswith("workspace.approval_copilot.gold_")
        names = [d["name"] for d in s["dimensions"]] + [m["name"] for m in s["measures"]]
        assert len(names) == len(set(names)), view
        assert all(m["expr"].upper().startswith("SUM(") for m in s["measures"]), "measures must aggregate"


def test_final_approval_matches_the_benchmark():
    # A1: final approval of Colombian domestic debit on 13 August
    got = measure("approval_metrics", ["final_approval_rate", "payments_sent"], [],
                  "country = 'CO' AND card_type = 'debit' AND NOT is_cross_border AND business_date = DATE'2026-08-13'")[0]
    assert round(got["final_approval_rate"], 4) == reference("A1")["final_approval_rate"]
    assert got["payments_sent"] == 1150


def test_first_attempt_rate_and_volume_match_the_benchmark():
    # A2: first-attempt approval, Mexican domestic credit, July; A6: approved USD of Acquirer C in Brazil, August
    a2 = measure("approval_metrics", ["first_attempt_approval_rate"], [],
                 "country = 'MX' AND card_type = 'credit' AND NOT is_cross_border "
                 "AND business_date BETWEEN DATE'2026-07-01' AND DATE'2026-07-31'")[0]
    assert round(a2["first_attempt_approval_rate"], 4) == reference("A2")["first_attempt_approval_rate"]
    a6 = measure("approval_metrics", ["approved_usd"], [],
                 "country = 'BR' AND acquirer_id = 'C' AND business_date BETWEEN DATE'2026-08-01' AND DATE'2026-08-31'")[0]
    assert abs(a6["approved_usd"] - reference("A6")["approved_usd"]) < 1


def test_rates_are_ratios_of_sums_not_averages_of_rates():
    by_day = measure("approval_metrics", ["final_approved", "payments_sent"], ["business_date"],
                     "country = 'MX' AND business_date BETWEEN DATE'2026-07-01' AND DATE'2026-07-31'")
    month = measure("approval_metrics", ["final_approval_rate"], ["month"],
                    "country = 'MX' AND business_date BETWEEN DATE'2026-07-01' AND DATE'2026-07-31'")[0]
    expected = sum(r["final_approved"] for r in by_day) / sum(r["payments_sent"] for r in by_day)
    assert abs(month["final_approval_rate"] - expected) < 1e-12


def test_decline_metrics_split_first_attempts_and_retries():
    rows = measure("decline_metrics", ["failed_attempts"], ["attempt"],
                   "country = 'CO' AND acquirer_id = 'B' AND business_date = DATE'2026-08-13'")
    by_attempt = {r["attempt"]: r["failed_attempts"] for r in rows}
    total = spark().sql("SELECT sum(attempts) AS n FROM gold_decline_reasons_daily WHERE country = 'CO' "
                        "AND acquirer_id = 'B' AND business_date = DATE'2026-08-13'").collect()[0]["n"]
    assert sum(by_attempt.values()) == total and by_attempt["first attempt"] > 0


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    for name, fn in tests:
        fn()
        print(f"ok  {name}")
    print(f"{len(tests)} checks passed")
