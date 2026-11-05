# Databricks notebook source
# MAGIC %md
# MAGIC # 07 · Forecast alert on approved payments
# MAGIC Runs after the pipeline. Forecasts each day's approved payments per country and card type from
# MAGIC the previous 56 days, with weekday and payday terms, and flags the days that fall well below.
# MAGIC Writes `gold_volume_alerts_daily`. The backtest is in `analysis/detector_backtest.py`.
# MAGIC
# MAGIC Copy of `monitoring/volume_forecast.py` made by `tools/build_docs.py`. Do not edit by hand.

# COMMAND ----------

"""
Daily forecast of approved payments per country and card type, with weekday and payday terms, and an
alert when a day falls well below it.

The approval-rate detector (gold_anomalies_daily) cannot see losses that happen before a payment
reaches an acquirer, such as risk blocks, 3DS abandonment or traffic that never arrives, because
those payments are not in its denominator. A count of approved payments sees all of them, but counts
move with the calendar: in this data Sundays run about 30% below Fridays, and paydays 12 to 18% above
a normal day. A trailing mean would either fire every weekend or need a band so wide that it misses
real drops, so the forecast models both effects.

Model, refitted for every day and segment on the previous `HISTORY_DAYS` days only:

    log(approved) = level + trend * t + weekday effect + payday effect + noise

Days that alerted are left out of the fits for later days. The alert fires when the day is more than 3.09
prediction standard errors below the forecast (z <= -3.09) and at least `MIN_DROP` below it, so that an
alert is both unlikely to be noise and large enough to act on.

Used by notebook 07 (the job task that writes gold_volume_alerts_daily), the local run and the backtest
in analysis/detector_backtest.py.
"""

import numpy as np
import pandas as pd

HISTORY_DAYS = 56
MIN_HISTORY_DAYS = 42
Z_THRESHOLD = -3.09
MIN_DROP = 0.08

# Output table gold_volume_alerts_daily: one row per local date x country x card type. Days without
# enough history keep NULL forecasts and is_alert = false.
SPARK_SCHEMA = ("business_date DATE, country STRING, card_type STRING, approved BIGINT, expected_approved DOUBLE, "
                "lower_bound DOUBLE, pct_vs_expected DOUBLE, z_score DOUBLE, is_alert BOOLEAN")

# Business calendar input: the days of the month when salaries are paid in each market
# (quincenas in Mexico and Colombia, the fifth business day in Brazil, simplified to fixed dates).
PAYDAYS = {"MX": {1, 15, 16, 30, 31}, "CO": {1, 15, 16, 30, 31}, "BR": {5, 6, 7}}


def daily_approved(gold_approval_daily: pd.DataFrame) -> pd.DataFrame:
    """Approved payments per business date, country and card type, all traffic included. Every segment gets a
    row for every date in the data, with 0 on a day when nothing was approved, so a day without traffic is
    scored instead of missing."""
    d = (gold_approval_daily.groupby(["business_date", "country", "card_type"], as_index=False)
         .agg(approved=("final_approved", "sum")))
    d["business_date"] = pd.to_datetime(d["business_date"])
    calendar = pd.DataFrame({"business_date": pd.date_range(d["business_date"].min(), d["business_date"].max())})
    full = d[["country", "card_type"]].drop_duplicates().merge(calendar, how="cross")
    d = full.merge(d, on=["country", "card_type", "business_date"], how="left")
    d["approved"] = d["approved"].fillna(0).astype("int64")
    return d.sort_values(["country", "card_type", "business_date"]).reset_index(drop=True)


def _design(dates: pd.Series, t: np.ndarray, country: str) -> np.ndarray:
    weekday = dates.dt.day_of_week.to_numpy()
    payday = np.array([d.day in PAYDAYS[country] for d in dates], dtype=float)
    cols = [np.ones(len(t)), t] + [(weekday == k).astype(float) for k in range(1, 7)] + [payday]
    return np.column_stack(cols)


def log_count(approved) -> np.ndarray:
    """log of approved payments; a day with none counts as one, so the log stays finite."""
    return np.log(np.maximum(np.asarray(approved, dtype=float), 1.0))


def score_segment(s: pd.DataFrame, country: str, history_days: int = HISTORY_DAYS, min_history: int = MIN_HISTORY_DAYS,
                  z_threshold: float = Z_THRESHOLD, min_drop: float = MIN_DROP) -> pd.DataFrame:
    """Forecast each day of one segment from the days before it (`s` has business_date and approved).

    Returns the log forecast and its prediction standard error, unrounded. A day that alerts is left out of the
    fits for later days, so an outage does not drag the baseline down and hide the next one."""
    s = s.sort_values("business_date").reset_index(drop=True)
    y = log_count(s["approved"])
    t_all = (s["business_date"] - s["business_date"].iloc[0]).dt.days.to_numpy(dtype=float)
    X_all = _design(s["business_date"], t_all, country)
    pred, se = np.full(len(s), np.nan), np.full(len(s), np.nan)
    usable = np.ones(len(s), dtype=bool)
    for i in range(len(s)):
        past = np.where((t_all < t_all[i]) & (t_all >= t_all[i] - history_days) & usable)[0]
        if len(past) < min_history:
            continue
        X, yy = X_all[past], y[past]
        beta, *_ = np.linalg.lstsq(X, yy, rcond=None)
        sigma2 = float(((yy - X @ beta) ** 2).sum() / (len(past) - X.shape[1]))
        x0 = X_all[i]
        pred[i] = float(x0 @ beta)
        se[i] = float(np.sqrt(sigma2 * (1 + x0 @ np.linalg.pinv(X.T @ X) @ x0)))
        if (y[i] - pred[i]) / se[i] <= z_threshold and np.exp(y[i] - pred[i]) - 1 <= -min_drop:
            usable[i] = False
    return pd.DataFrame({"business_date": s["business_date"], "approved": s["approved"],
                         "log_expected": pred, "se_log": se})


def volume_alerts(daily: pd.DataFrame, z_threshold: float = Z_THRESHOLD, min_drop: float = MIN_DROP,
                  **kw) -> pd.DataFrame:
    """Scores every segment-day; is_alert marks the days to act on. log_expected and se_log are kept unrounded
    for the backtest; the other columns are what gold_volume_alerts_daily stores."""
    parts = []
    for (country, card_type), s in daily.groupby(["country", "card_type"]):
        r = score_segment(s, country, z_threshold=z_threshold, min_drop=min_drop, **kw)
        r.insert(1, "country", country)
        r.insert(2, "card_type", card_type)
        parts.append(r)
    res = pd.concat(parts, ignore_index=True)
    gap = log_count(res["approved"]) - res["log_expected"]
    z = gap / res["se_log"]
    res["expected_approved"] = np.round(np.exp(res["log_expected"]), 1)
    res["lower_bound"] = np.round(np.exp(res["log_expected"] + z_threshold * res["se_log"]), 1)
    res["pct_vs_expected"] = np.round(100 * (np.exp(gap) - 1), 2)
    res["z_score"] = np.round(z, 2)
    res["is_alert"] = ((z <= z_threshold) & (np.exp(gap) - 1 <= -min_drop)).fillna(False).astype(bool)
    res["business_date"] = res["business_date"].dt.date
    return res.sort_values(["business_date", "country", "card_type"]).reset_index(drop=True)


def _plain(v):
    """numpy scalars to Python values, NaN to None."""
    if isinstance(v, np.generic):
        v = v.item()
    return None if isinstance(v, float) and np.isnan(v) else v


def for_spark(alerts: pd.DataFrame) -> list[tuple]:
    """Rows ready for spark.createDataFrame(rows, SPARK_SCHEMA), with NULL instead of NaN."""
    cols = ["business_date", "country", "card_type", "approved", "expected_approved", "lower_bound",
            "pct_vs_expected", "z_score", "is_alert"]
    return [tuple(_plain(v) for v in r) for r in alerts[cols].itertuples(index=False)]

# COMMAND ----------

SOURCE = "workspace.approval_copilot.gold_approval_daily"
TARGET = "workspace.approval_copilot.gold_volume_alerts_daily"
TABLE_COMMENT = 'Daily approved payments per country and card type against a forecast with weekday and payday terms, fitted on the previous 56 days (monitoring/volume_forecast.py). Holds every day; is_alert marks the days below the 99.9% lower bound and at least 8% under the forecast. Written by the job after the pipeline. Grain: one row per local date × country × card type (all traffic).'
COLUMN_COMMENTS = {
    'business_date': 'Local calendar date of the payment (country time zone)',
    'country': 'MX, CO or BR',
    'card_type': 'credit or debit',
    'approved': 'Approved payments that day, first attempt or after a retry, cross-border included',
    'expected_approved': 'Approved payments the forecast expected (weekday, payday and trend terms, fitted on the previous 56 days); empty in the first 42 days',
    'lower_bound': "Lower bound of the forecast's 99.9% one-sided prediction interval",
    'pct_vs_expected': 'Approved payments versus the forecast, in percent',
    'z_score': 'Distance from the forecast in prediction standard errors (log scale)',
    'is_alert': 'True when the day is below the lower bound and at least 8% under the forecast',
}

# COMMAND ----------

alerts = volume_alerts(daily_approved(spark.table(SOURCE).toPandas()))  # noqa: F821
(spark.createDataFrame(for_spark(alerts), SPARK_SCHEMA)  # noqa: F821
    .write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(TARGET))


def sql_literal(text):
    return "'" + text.replace("\\", "\\\\").replace("'", "\\'") + "'"


spark.sql(f"COMMENT ON TABLE {TARGET} IS {sql_literal(TABLE_COMMENT)}")  # noqa: F821
for column, text in COLUMN_COMMENTS.items():
    spark.sql(f"ALTER TABLE {TARGET} ALTER COLUMN {column} COMMENT {sql_literal(text)}")  # noqa: F821
flagged = alerts[alerts["is_alert"]]
print(f"{int(alerts['z_score'].notna().sum()):,} segment-days scored; {len(flagged)} alerts")
print(flagged.to_string(index=False))
