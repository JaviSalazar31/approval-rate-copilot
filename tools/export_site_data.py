"""
Exports the small datasets behind the charts of the project page (site/data.json), computed from the
local pipeline output so the page shows what the pipeline produced.

  python tools/export_site_data.py
"""
from __future__ import annotations

import glob
import json
import os
import re
import sys

import pandas as pd

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
OUT = os.path.join(ROOT, "local", "out", "spark")
RAW = os.path.join(ROOT, "data", "full", "raw")
sys.path.insert(0, os.path.join(ROOT, "local"))
from pipeline_spec import TABLES  # noqa: E402

T_MAP = pd.Timestamp("2026-08-06 13:00")      # CHG-0806 mapping v2.3 (UTC)
T_ROUTE = pd.Timestamp("2026-08-13 19:00")    # CHG-0813 routing v14
T_ROLLBACK = pd.Timestamp("2026-08-14 17:10")  # CHG-0814 rollback
T_FIX = pd.Timestamp("2026-08-15 13:00")      # CHG-0815 mapping hotfix
FEE_BPS = {"A": 290, "B": 250, "C": 270}


def t(name):
    return pd.read_parquet(os.path.join(OUT, name))


def r(x, n=4):
    return None if pd.isna(x) else round(float(x), n)


def main():
    sp = t("silver_payments")
    sp["ts"] = pd.to_datetime(sp["created_ts_utc"])
    sent = sp[sp["sent_to_acquirer"]]
    dom = sent[~sent["is_cross_border"]]

    # Headline numbers ---------------------------------------------------------------------------
    attempts = t("silver_auth_attempts")
    quarantine = t("silver_dq_quarantine")
    headline = dict(
        payment_intents=int(len(sp)),
        payments_sent=int(len(sent)),
        attempts=int(len(attempts) + len(quarantine)),
        quarantined=int(len(quarantine)),
        attempted_usd=r(sent["amount_usd"].sum(), 0),
        approved_usd=r(sent["approved_usd"].sum(), 0),
        domestic_final_rate=r(dom["final_approved"].mean()),
        domestic_first_rate=r(dom["first_attempt_approved"].mean()),
        domestic_cascade_pp=r(100 * dom["recovered_by_cascade"].mean(), 2),
        days=int(sp["business_date"].nunique()),
    )

    # Chart 1: hourly final approval, CO domestic debit, 12-15 Aug (local) -----------------------
    h = t("gold_approval_hourly")
    h = h[(h.country == "CO") & (h.card_type == "debit") & (~h.is_cross_border)]
    h["hour"] = pd.to_datetime(h["hour_local_ts"])
    h = h[(h.hour >= "2026-08-12 00:00") & (h.hour < "2026-08-16 00:00")]
    hourly = (h.groupby("hour").agg(sent=("payments_sent", "sum"), ok=("final_approved", "sum"))
              .reset_index().sort_values("hour"))
    # 3-hour rolling (volume weighted) to make the shape readable; raw values kept for tooltips
    hourly["rolling"] = hourly["ok"].rolling(3, min_periods=1).sum() / hourly["sent"].rolling(3, min_periods=1).sum()
    hourly_out = [dict(hour=x.hour.strftime("%Y-%m-%d %H:00"), sent=int(x.sent), rate=r(x.ok / x.sent),
                       rolling=r(x.rolling)) for x in hourly.itertuples()]

    # Chart 2: daily cascade rate of Acquirer B, code 05, MX and CO --------------------------------
    al = t("gold_cascade_alerts_daily")
    al["d"] = pd.to_datetime(al["business_date"])
    b05 = al[(al.acquirer_id == "B") & (al.first_reason_code == "05") & (al.d >= "2026-07-20") & (al.d <= "2026-08-25")]
    daily_b05 = (b05.groupby("d").agg(failures=("failures", "sum"), cascaded=("cascaded", "sum"),
                                      alert=("is_alert", "max"),
                                      versions=("mapping_versions", lambda v: ",".join(sorted(set(",".join(v).split(","))))))
                 .reset_index().sort_values("d"))
    cascade_out = [dict(date=x.d.strftime("%Y-%m-%d"), failures=int(x.failures), cascaded=int(x.cascaded),
                        cascade_rate=r(x.cascaded / x.failures), alert=bool(x.alert), map_versions=x.versions)
                   for x in daily_b05.itertuples()]

    # Incident sizing (window = routing v14 in effect) ---------------------------------------------
    co = dom[(dom.country == "CO") & (dom.card_type == "debit")]
    win = co[(co.ts >= T_ROUTE) & (co.ts < T_ROLLBACK)]
    base = co[(co.ts < T_ROUTE) & (co.ts >= T_ROUTE - pd.Timedelta(days=14))]
    base_rate = base["final_approved"].mean()
    lost = round(len(win) * base_rate) - int(win["final_approved"].sum())
    vol_rate = base["approved_usd"].sum() / base["amount_usd"].sum()
    lost_usd = win["amount_usd"].sum() * vol_rate - win["approved_usd"].sum()
    # Fees saved = what the window's approved volume would have paid on Acquirer A minus what it paid
    # (payments recovered on A by a retry already paid A's fee).
    approved_win = win["approved_usd"].sum()
    fees_saved = approved_win * FEE_BPS["A"] / 10000 - win["fee_usd"].sum()

    # Retries switched off by map v2.3: Acquirer B "do not honor" (05) first-attempt declines, domestic.
    # Expected recoveries use each country's recovery rate in the 28 days before the map change.
    b05_all = dom[(dom.first_acquirer_id == "B") & (dom.first_iso_code == "05")]
    b05_base = b05_all[(b05_all.ts < T_MAP) & (b05_all.ts >= T_MAP - pd.Timedelta(days=28))]
    rate_by_country = b05_base.groupby("country")["recovered_by_cascade"].mean()
    rec_rate = b05_base["recovered_by_cascade"].mean()                     # Mexico and Colombia pooled
    co_rate = rate_by_country["CO"]
    co_rate_long = b05_all[(b05_all.country == "CO") & (b05_all.ts < T_MAP)]["recovered_by_cascade"].mean()
    bug = b05_all[(b05_all.ts >= T_MAP) & (b05_all.ts < T_FIX)]
    bug_rate = bug["country"].map(rate_by_country)
    bug_in_win = bug[(bug.country == "CO") & (bug.card_type == "debit") & (bug.ts >= T_ROUTE) & (bug.ts < T_ROLLBACK)]
    # Split of the window's drop: missing retries at B's Colombian recovery rate; the rest is B approving less.
    # The copilot does not make this split (it says so); it is the author's estimate, shown with its range.
    missing_cascade_pp = 100 * len(bug_in_win) * co_rate / len(win)
    missing_low_pp = 100 * len(bug_in_win) * min(co_rate_long, rec_rate) / len(win)
    missing_high_pp = 100 * len(bug_in_win) * max(co_rate_long, rec_rate) / len(win)
    total_drop_pp = 100 * (win["final_approved"].mean() - base_rate)

    incident = dict(
        window_payments=int(len(win)),
        baseline_rate=r(base_rate),
        window_rate=r(win["final_approved"].mean()),
        baseline_first_rate=r(base["first_attempt_approved"].mean()),
        window_first_rate=r(win["first_attempt_approved"].mean()),
        drop_pp=r(total_drop_pp, 2),
        lost_approvals=int(lost),
        lost_approved_usd=r(lost_usd, 0),
        fees_saved_usd=r(fees_saved, 0),
        loss_to_saving_ratio=r((lost_usd) / fees_saved, 1),
        one_in=int(round(len(win) / lost)),                               # "1 in N" payments lost
        b05_baseline_failures=int(len(b05_base)),
        b05_baseline_recovery_rate=r(rec_rate),
        b05_co_recovery_rate=r(co_rate),
        b05_mx_recovery_rate=r(rate_by_country["MX"]),
        b05_bug_failures=int(len(bug)),
        b05_bug_cascaded=int(bug["is_cascaded"].sum()),
        b05_window_failures=int(len(bug_in_win)),
        b05_lost_recoveries=r((bug_rate).sum(), 0),
        b05_lost_recoveries_usd=r((bug["amount_usd"] * bug_rate).sum(), 0),
        b05_lost_in_window=r(len(bug_in_win) * co_rate, 0),
        drop_from_missing_retries_pp=r(-missing_cascade_pp, 2),
        drop_from_weaker_acquirer_pp=r(total_drop_pp + missing_cascade_pp, 2),
        missing_retries_low_pp=r(missing_low_pp, 1),
        missing_retries_high_pp=r(missing_high_pp, 1),
        b05_first_day_cascade_rate=next(x["cascade_rate"] for x in cascade_out if x["date"] == "2026-08-06"),
        first_alert_date=str(al[al.is_alert]["d"].min().date()),
        # the daily run for a business date happens the next morning, so the warning starts one day later
        days_of_early_warning=int((pd.Timestamp("2026-08-13") - al[al.is_alert]["d"].min()).days) - 1,
    )

    # Risk change in Brazil: daily risk block rate and conversion, June ----------------------------
    f = t("gold_funnel_daily")
    f["d"] = pd.to_datetime(f["business_date"])
    fb = f[(f.country == "BR") & (f.d >= "2026-06-08") & (f.d <= "2026-06-28")].groupby("d")[
        ["payment_intents", "blocked_by_risk", "approved"]].sum().reset_index()
    risk_out = [dict(date=x.d.strftime("%Y-%m-%d"), risk_block_rate=r(x.blocked_by_risk / x.payment_intents),
                     conversion_rate=r(x.approved / x.payment_intents)) for x in fb.itertuples()]

    # Pipeline facts shown on the page ------------------------------------------------------------
    raw_attempt_rows = sum(sum(1 for _ in open(f)) - 1
                           for f in glob.glob(os.path.join(RAW, "acquirer_*", "**", "*.csv"), recursive=True))
    anomalies = t("gold_anomalies_daily")
    tests = open(os.path.join(ROOT, "tests", "test_pipeline.py")).read()
    pipeline = dict(
        objects=len(TABLES),                                              # tables and views declared
        duplicates=int(raw_attempt_rows - len(attempts) - len(quarantine)),  # webhook re-deliveries removed
        segment_days=int(len(anomalies)),
        anomalies_flagged=int(anomalies["is_anomaly"].sum()),
        checks=len(re.findall(r"^def test_", tests, flags=re.M)),
    )

    data = dict(headline=headline, incident=incident, pipeline=pipeline, hourly_co_debit=hourly_out,
                cascade_b05=cascade_out, br_risk_june=risk_out)
    os.makedirs(os.path.join(ROOT, "site"), exist_ok=True)
    path = os.path.join(ROOT, "site", "data.json")
    json.dump(data, open(path, "w"), indent=1)
    print(json.dumps(dict(headline=headline, incident=incident, pipeline=pipeline), indent=1))
    print(f"wrote {path}: {len(hourly_out)} hourly points, {len(cascade_out)} cascade points, {len(risk_out)} risk points")


if __name__ == "__main__":
    main()
