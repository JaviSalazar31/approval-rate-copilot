"""
Backtest of the approved-payments forecast alert (monitoring/volume_forecast.py) against two simpler
baselines, in sample and on datasets the thresholds were not set on.

  python analysis/detector_backtest.py [--seeds-dir DIR]

In sample is the reference run (seed 42, local/out). Out of sample are datasets generated with other seeds
and run through the same local pipeline:

  python data_generator/generate_data.py --out DIR/s7/data --seed 7
  python local/run_local.py --data DIR/s7/data --out DIR/s7/out

What the out-of-sample datasets test: they come from the same generator, whose daily volume follows the same
weekday, payday and trend terms the forecast fits, so they test new noise. Holidays, promotions and real
outages are not in them.

Detectors, all with the same 8% minimum drop:
- forecast: more than 3.09 prediction standard errors below the forecast (the production rule);
- trailing mean: z-score against the mean and standard deviation of the previous 28 days;
- same weekday: percent below the mean of the previous four same weekdays.
The two baselines have their threshold set on seed 42 so that they raise exactly as many false alerts there as
the forecast does; the comparison is then at the same false-alert rate, on the out-of-sample datasets.

None of the planted incidents moves daily approved payments enough to test detection (Colombian debit on
13 August is 8% below forecast), so every clean day is cut by 10% to 30% in turn and scored again. Each
detector reads only earlier days, so a cut changes that day's score and nothing else.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np
import pandas as pd

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "monitoring"))
from volume_forecast import MIN_DROP, Z_THRESHOLD, daily_approved, log_count, volume_alerts  # noqa: E402

OUT = os.path.join(ROOT, "analysis", "results", "detector_backtest.json")
DROPS = [0.10, 0.15, 0.20, 0.30]
KEYS = ["business_date", "country", "card_type"]
DETECTORS = ["forecast", "trailing_mean", "same_weekday"]
# Segment-days touched by a planted event, left out of the false-alert count and of the injections.
PLANTED = [("BR", None, "2026-06-18", "2026-06-21"), ("BR", None, "2026-07-21", "2026-07-21"),
           ("CO", "debit", "2026-08-13", "2026-08-14"), ("MX", None, "2026-08-20", "2026-08-20"),
           # the decline-code map bug: Acquirer B's "do not honor" declines were not retried, 6 to 15 August
           ("MX", "debit", "2026-08-06", "2026-08-15"), ("CO", None, "2026-08-06", "2026-08-15")]


def baselines(daily: pd.DataFrame) -> pd.DataFrame:
    """Trailing 28-day mean and sd, and the mean of the previous four same weekdays, per segment."""
    parts = []
    for _, s in daily.groupby(["country", "card_type"]):
        s = s.sort_values("business_date").copy()
        roll = s["approved"].shift(1).rolling(28, min_periods=28)
        s["trailing_mean"], s["trailing_sd"] = roll.mean(), roll.std()
        s["same_weekday_mean"] = (s.groupby(s["business_date"].dt.day_of_week)["approved"]
                                  .transform(lambda x: x.shift(1).rolling(4, min_periods=4).mean()))
        parts.append(s)
    return pd.concat(parts)


def planted_mask(df: pd.DataFrame) -> pd.Series:
    m = pd.Series(False, index=df.index)
    dates = pd.to_datetime(df["business_date"])
    for country, card_type, start, end in PLANTED:
        hit = (df["country"] == country) & (dates >= start) & (dates <= end)
        if card_type:
            hit &= df["card_type"] == card_type
        m |= hit
    return m


def score(out_dir: str) -> pd.DataFrame:
    """Every segment-day the forecast can score, with what each detector needs. Planted days are dropped."""
    daily = daily_approved(pd.read_parquet(os.path.join(out_dir, "gold", "gold_approval_daily.parquet")))
    alerts = volume_alerts(daily)[KEYS + ["log_expected", "se_log"]]
    base = baselines(daily).assign(business_date=lambda d: d["business_date"].dt.date)
    res = base.merge(alerts, on=KEYS)
    res = res[res["log_expected"].notna() & ~planted_mask(res)]
    return res.reset_index(drop=True)


def scores(res: pd.DataFrame, drop: float) -> dict:
    """Score of each detector (lower means more anomalous) and its relative gap, with the day cut by `drop`."""
    y = res["approved"] * (1 - drop)
    gap_forecast = np.exp(log_count(y) - res["log_expected"]) - 1
    return {
        "forecast": ((log_count(y) - res["log_expected"]) / res["se_log"], gap_forecast),
        "trailing_mean": ((y - res["trailing_mean"]) / res["trailing_sd"], y / res["trailing_mean"] - 1),
        "same_weekday": (y / res["same_weekday_mean"] - 1, y / res["same_weekday_mean"] - 1),
    }


def flags(res: pd.DataFrame, drop: float, thresholds: dict) -> dict:
    return {name: ((score <= thresholds[name]) & (gap <= -MIN_DROP)).to_numpy()
            for name, (score, gap) in scores(res, drop).items()}


def calibrate(res: pd.DataFrame, target: int) -> dict:
    """Thresholds that give each baseline exactly `target` false alerts on this data (midpoint between the
    target-th and the next eligible score)."""
    thresholds = {"forecast": Z_THRESHOLD}
    for name, (score, gap) in scores(res, 0.0).items():
        if name == "forecast":
            continue
        eligible = np.sort(score[(gap <= -MIN_DROP).to_numpy()].to_numpy())
        if len(eligible) <= target:
            thresholds[name] = float(eligible.max()) + 1e-9 if len(eligible) else -np.inf
        else:
            thresholds[name] = float((eligible[target - 1] + eligible[target]) / 2) if target else float(eligible[0]) - 1e-9
    return thresholds


def evaluate(res: pd.DataFrame, thresholds: dict) -> dict:
    clean = flags(res, 0.0, thresholds)
    out = {"scored_segment_days": int(len(res)),
           "false_alerts": {name: int(clean[name].sum()) for name in DETECTORS},
           "detection_rate_by_drop": []}
    for drop in DROPS:
        f = flags(res, drop, thresholds)
        out["detection_rate_by_drop"].append({"drop": drop, **{name: round(float(f[name].mean()), 5) for name in DETECTORS}})
    out["band_below_forecast_pct"] = round(float(100 * (1 - np.exp(Z_THRESHOLD * res["se_log"])).median()), 1)
    return out


def main(seeds_dir: str | None) -> dict:
    in_sample = score(os.path.join(ROOT, "local", "out"))
    target = int(flags(in_sample, 0.0, {"forecast": Z_THRESHOLD, "trailing_mean": -np.inf, "same_weekday": -np.inf})
                 ["forecast"].sum())
    thresholds = calibrate(in_sample, target)
    result = {"min_drop": MIN_DROP, "thresholds": {k: round(v, 4) for k, v in thresholds.items()},
              "thresholds_set_on": "seed 42: the baselines match the forecast's false alerts there",
              "runs": {"seed 42 (in sample)": evaluate(in_sample, thresholds)}}
    pooled = []
    if seeds_dir and os.path.isdir(seeds_dir):
        for name in sorted(os.listdir(seeds_dir), key=lambda n: int(n.lstrip("s")) if n.lstrip("s").isdigit() else 0):
            path = os.path.join(seeds_dir, name, "out")
            if os.path.isdir(os.path.join(path, "gold")):
                res = score(path)
                result["runs"][f"seed {name.lstrip('s')} (out of sample)"] = evaluate(res, thresholds)
                pooled.append(res)
    if pooled:
        result["out_of_sample_pooled"] = evaluate(pd.concat(pooled, ignore_index=True), thresholds)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as f:
        json.dump(result, f, indent=1)
    return result


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds-dir", default=None)
    print(json.dumps(main(ap.parse_args().seeds_dir), indent=1))
