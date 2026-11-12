"""
The change guardrail (dbt/models/marts/gold_change_guardrail.sql) on the local pipeline output: it fires on the
routing change of 13 August within the first hours and on no other change, and the pandas version that the backtest
(analysis/change_guardrail.py) runs on other datasets gives the same rows as the dbt model.

Run after `python local/run_local.py`:
    python -m pytest tests/test_change_guardrail.py
    python tests/test_change_guardrail.py
"""
from __future__ import annotations

import os
import sys

import pandas as pd

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "analysis"))
import change_guardrail as cg  # noqa: E402

GOLD = os.path.join(ROOT, "local", "out", "gold")
KEY = ["change_id", "country", "card_type", "is_cross_border", "hours_since_change"]


def table() -> pd.DataFrame:
    return pd.read_parquet(os.path.join(GOLD, "gold_change_guardrail.parquet")).sort_values(KEY).reset_index(drop=True)


def test_fires_on_the_routing_change_only():
    t = table()
    fired = t[t["first_fire_hours"].notna()].drop_duplicates(KEY[:4])
    assert list(zip(fired["change_id"], fired["country"], fired["card_type"], fired["is_cross_border"])) == [
        ("CHG-0813", "CO", "debit", False)]
    assert int(fired["first_fire_hours"].iloc[0]) <= 6


def test_follows_every_change_but_partner_notices():
    t = table()
    changes = pd.read_parquet(os.path.join(GOLD, "gold_change_log.parquet"))
    expected = set(changes.loc[changes["change_type"] != "external_notice", "change_id"])
    assert set(t["change_id"]) == expected
    assert (t.groupby(KEY[:4]).size() == cg.HOURS).all()


def test_acquirer_scope_follows_the_segments_it_served():
    t = table()
    segs = t[t["change_id"] == "CHG-0806"].drop_duplicates(KEY[:4])
    assert sorted(zip(segs["country"], segs["card_type"], segs["is_cross_border"])) == [
        ("CO", "credit", False), ("MX", "debit", False)]


def test_backtest_rule_matches_the_dbt_model():
    sent, changes = cg.load(GOLD)
    py = cg.guardrail_table(sent, changes).sort_values(KEY).reset_index(drop=True)
    sql = table()
    assert len(py) == len(sql)
    for col in ["change_id", "country", "card_type", "is_cross_border", "hours_since_change", "baseline_payments",
                "payments_sent", "final_approved", "fires"]:
        assert (py[col].values == sql[col].values).all(), col
    # rounded columns: Spark rounds half up and Python half to even, so allow one unit of the last decimal
    for col, unit in [("baseline_rate", 1e-4), ("final_approval_rate", 1e-4), ("rate_change_pp", 0.01),
                      ("z_score", 0.01), ("lost_approvals", 0.1), ("lost_approved_usd", 0.01),
                      ("fees_saved_usd", 0.01)]:
        diff = (pd.to_numeric(py[col]) - pd.to_numeric(sql[col])).abs()
        assert (diff.fillna(0) <= unit * 1.01).all(), (col, diff.max())
        assert (py[col].isna() == sql[col].isna()).all(), col
    assert (py["first_fire_hours"].fillna(-1).astype(int).values
            == sql["first_fire_hours"].fillna(-1).astype(int).values).all()


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    for name, fn in tests:
        fn()
        print(f"ok  {name}")
    print(f"{len(tests)} checks passed")
