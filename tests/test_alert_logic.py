"""
Logic tests for the cascade alert (sql/gold/gold_cascade_alerts_daily.sql), on small synthetic inputs.

1. A 20-day collapse of retries after 60 healthy days: the alert fires on every collapsed day, because its
   baseline leaves collapsed days out. The first version, a plain rolling 28-day baseline, learned the
   outage and went quiet after about two weeks.
2. Where it stops: the baseline needs healthy days adding up to at least 50 failures among the previous
   56 days with failures. At 10 failures a day that is 5 healthy days, so a collapse alerts on its days
   1 to 52 and goes quiet from day 53. The decision log (ADR-08) explains why this limit is accepted.

  python -m pytest tests                (if pytest is installed)
  python tests/test_alert_logic.py      (plain Python; both need Java and pyspark, like local/run_local.py)
"""
from __future__ import annotations

import datetime as dt
import functools
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "local"))
from run_local import spark_session  # noqa: E402

ALERT_SQL = os.path.join(ROOT, "sql", "gold", "gold_cascade_alerts_daily.sql")
FAILURES = 10  # failed first attempts per day


@functools.lru_cache(maxsize=1)
def spark():
    session = spark_session()
    session.sparkContext.setLogLevel("ERROR")
    return session


def run_alert(healthy_days, collapsed_days, failures=FAILURES):
    """Runs the alert on one acquirer/reason pair: healthy days with every failure retried, then collapsed
    days with none. Returns the output rows of the healthy and the collapsed days, in date order."""
    start = dt.date(2026, 1, 1)
    rows = [(start + dt.timedelta(days=i), "CO", "B", "05", failures, failures if i < healthy_days else 0, "v1")
            for i in range(healthy_days + collapsed_days)]
    cols = ("business_date DATE, country STRING, acquirer_id STRING, first_reason_code STRING, "
            "first_attempt_failures BIGINT, cascaded BIGINT, first_mapping_version STRING")
    spark().createDataFrame(rows, cols).createOrReplaceTempView("gold_cascade_daily")
    with open(ALERT_SQL) as f:
        out = spark().sql(f.read()).orderBy("business_date").collect()
    assert len(out) == healthy_days + collapsed_days
    return out[:healthy_days], out[healthy_days:]


def test_alert_fires_through_a_20_day_collapse():
    healthy, collapsed = run_alert(60, 20)
    assert not any(r.is_alert for r in healthy), "an alert fired on a healthy day"
    missed = [str(r.business_date) for r in collapsed if not r.is_alert]
    assert not missed, f"the alert went quiet during the collapse on {missed}"
    assert all(r.baseline_cascade_rate == 1.0 for r in collapsed), "the baseline learned the outage"


def test_alert_stops_when_the_window_runs_out_of_healthy_days():
    _, collapsed = run_alert(60, 80)
    fired = [day for day, r in enumerate(collapsed, start=1) if r.is_alert]
    assert fired == list(range(1, 53)), f"expected alerts on collapsed days 1-52, got {fired}"


if __name__ == "__main__":
    test_alert_fires_through_a_20_day_collapse()
    print("ok  the alert fired on all 20 collapsed days; the baseline stayed at 100%")
    test_alert_stops_when_the_window_runs_out_of_healthy_days()
    print("ok  in an 80-day collapse at 10 failures a day it fired on days 1-52 and stopped from day 53")
