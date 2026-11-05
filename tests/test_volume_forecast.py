"""
The approved-payments forecast alert (monitoring/volume_forecast.py) on a synthetic series with weekday and
payday swings. No Spark needed.

    python -m pytest tests/test_volume_forecast.py
    python tests/test_volume_forecast.py
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "monitoring"))
from volume_forecast import PAYDAYS, volume_alerts  # noqa: E402

WEEKDAY = [0.98, 0.97, 1.00, 1.02, 1.12, 0.88, 0.80]


def series(drop_on: str | None = None, drop: float = 0.0, seed: int = 1) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2026-06-01", periods=90, freq="D")
    level = np.array([1000 * WEEKDAY[d.weekday()] * (1.15 if d.day in PAYDAYS["MX"] else 1.0) for d in dates])
    approved = np.round(level * rng.normal(1, 0.03, len(dates))).astype(int)
    df = pd.DataFrame({"business_date": dates, "country": "MX", "card_type": "debit", "approved": approved})
    if drop_on:
        df.loc[df.business_date == drop_on, "approved"] = (df.loc[df.business_date == drop_on, "approved"]
                                                           * (1 - drop)).round().astype(int)
    return df


def test_weekends_and_paydays_do_not_alert():
    res = volume_alerts(series())
    scored = res.dropna(subset=["z_score"])
    assert len(scored) == 90 - 42
    assert not res["is_alert"].any()
    # the forecast follows the calendar: Sundays and paydays sit close to it, not 15-20% away
    assert scored["pct_vs_expected"].abs().max() < 10


def test_a_30_percent_drop_alerts_on_that_day_only():
    res = volume_alerts(series(drop_on="2026-08-20", drop=0.30))
    flagged = res[res["is_alert"]]
    assert [str(d) for d in flagged["business_date"]] == ["2026-08-20"]


def test_a_day_with_no_approvals_alerts_and_does_not_blind_the_next_weeks():
    df = series(drop_on="2026-08-10", drop=1.0)                     # nothing approved that day
    df = pd.concat([df, df.assign(card_type="credit")]).reset_index(drop=True)
    df.loc[(df.card_type == "credit") & (df.business_date == "2026-08-20"), "approved"] //= 2
    res = volume_alerts(df)
    flagged = {(str(r.business_date), r.card_type) for r in res[res["is_alert"]].itertuples()}
    assert ("2026-08-10", "debit") in flagged and ("2026-08-20", "credit") in flagged
    after = res[(res.card_type == "debit") & (pd.to_datetime(res.business_date) > "2026-08-10")]
    assert after["z_score"].notna().all()                          # later days still get a forecast


def test_a_day_without_any_row_is_scored_as_zero():
    from volume_forecast import daily_approved
    df = series()
    gold = df.rename(columns={"approved": "final_approved"})
    gold = gold[gold.business_date != "2026-08-12"]                # the feed sent nothing that day
    daily = daily_approved(gold.assign(business_date=gold.business_date.dt.date))
    row = daily[daily.business_date == "2026-08-12"]
    assert len(row) == 1 and row["approved"].iloc[0] == 0
    res = volume_alerts(daily)
    assert res[pd.to_datetime(res.business_date) == "2026-08-12"]["is_alert"].all()


def test_an_alerted_day_is_left_out_of_later_fits():
    # A 60% outage on Monday 10 August, kept in the fit, would pull the Monday term down by about a tenth.
    # Left out, the later Mondays keep the forecast they have without the outage.
    clean = volume_alerts(series())
    cut = volume_alerts(series(drop_on="2026-08-10", drop=0.6))
    dates = pd.to_datetime(clean.business_date)
    later_mondays = (dates > "2026-08-10") & (dates.dt.weekday == 0)
    ratio = cut.loc[later_mondays, "expected_approved"].values / clean.loc[later_mondays, "expected_approved"].values
    assert cut.loc[dates == "2026-08-10", "is_alert"].all()
    assert abs(ratio - 1).max() < 0.03


def test_days_without_enough_history_are_not_scored():
    res = volume_alerts(series())
    early = res[pd.to_datetime(res["business_date"]) < "2026-07-13"]
    assert early["z_score"].isna().all() and not early["is_alert"].any()


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    for name, fn in tests:
        fn()
        print(f"ok  {name}")
    print(f"{len(tests)} checks passed")
