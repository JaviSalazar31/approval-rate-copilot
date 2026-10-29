"""
Invariants of the pipeline output and of the planted scenario.

Run after `python local/run_local.py`:
    python -m pytest tests          (if pytest is installed)
    python tests/test_pipeline.py   (plain Python)
"""
import glob
import os

import pandas as pd

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
OUT = os.path.join(ROOT, "local", "out", "spark")
RAW = os.path.join(ROOT, "data", "full", "raw")


def table(name):
    return pd.read_parquet(os.path.join(OUT, name))


def raw(source):
    return pd.concat([pd.read_csv(f, dtype=str) for f in sorted(glob.glob(os.path.join(RAW, source, "*.csv")))])


def test_every_attempt_is_in_silver_or_quarantine():
    events = (raw("acquirer_a")["event_id"].nunique() + len(raw("acquirer_b")) + len(raw("acquirer_c")))
    assert len(table("silver_auth_attempts")) + len(table("silver_dq_quarantine")) == events


def test_one_row_per_payment():
    sp = table("silver_payments")
    assert sp["payment_id"].is_unique
    assert len(sp) == len(raw("orchestrator"))


def test_gold_reconciles_with_silver():
    sp = table("silver_payments")
    gold = table("gold_approval_daily")
    sent = sp[sp["sent_to_acquirer"]]
    assert gold["payments_sent"].sum() == len(sent)
    assert gold["final_approved"].sum() == sent["final_approved"].sum()
    assert abs(gold["approved_usd"].sum() - sent["approved_usd"].sum()) < 1


def test_rates_are_valid():
    gold = table("gold_approval_daily")
    for col in ["first_attempt_approval_rate", "final_approval_rate", "approved_volume_rate"]:
        assert gold[col].between(0, 1).all(), col
    assert (gold["final_approved"] >= gold["first_attempt_approved"]).all()


def test_anomaly_detector_flags_only_the_incident():
    a = table("gold_anomalies_daily")
    flagged = a[a["is_anomaly"]]
    assert len(flagged) == 1
    row = flagged.iloc[0]
    assert (str(row["business_date"]), row["country"], row["card_type"]) == ("2026-08-13", "CO", "debit")


def test_cascade_alerts_catch_the_mapping_bug_on_day_one():
    al = table("gold_cascade_alerts_daily")
    alerts = al[al["is_alert"]]
    assert set(zip(alerts["acquirer_id"], alerts["first_reason_code"])) == {("B", "05")}
    assert set(alerts["country"]) == {"MX", "CO"}
    assert str(alerts["business_date"].min()) == "2026-08-06"


def test_mapping_versions_are_point_in_time():
    att = table("silver_auth_attempts")
    b05 = att[(att["acquirer_id"] == "B") & (att["raw_code"] == "R05")]
    by_version = b05.groupby("mapping_version")["retryable"].unique().to_dict()
    assert list(by_version["v2.2"]) == [True]
    assert list(by_version["v2.3"]) == [False]
    assert list(by_version["v2.3.1"]) == [True]


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    for name, fn in tests:
        fn()
        print(f"ok  {name}")
    print(f"{len(tests)} checks passed")
