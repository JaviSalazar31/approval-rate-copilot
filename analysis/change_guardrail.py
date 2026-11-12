"""
Backtest of the change guardrail (dbt/models/marts/gold_change_guardrail.sql): how soon it fires after a change that
hurts approval, what it would have saved, and how often it fires when nothing changed.

  python analysis/change_guardrail.py [--seeds-dir DIR]

The rule is the same as the dbt model's, written here in pandas so it can run on other datasets and on made-up
changes: for each production change, each segment in its scope (country, card type, domestic or cross-border) is
followed for 24 hours. Each hour adds up the payments sent since the change, and the guardrail fires when their final
approval is at least 2 points below the segment's 14 days before the change, the binomial z-score is -3.5 or lower and
at least 30 payments were sent. tests/test_change_guardrail.py checks that this module and the dbt model give the same
rows on the reference data.

In sample is the reference run (seed 42, local/out). Out of sample are the datasets of other seeds that
analysis/detector_backtest.py uses (how to build them is in that script). They come from the same generator, so they
test new noise around the same planted incidents.

False alarms: every segment is also followed from 10:00 local time on each day with no change in its country the
day before, the day itself or the day after, from the 15th day of data on (the baseline needs 14 days). Each of
those 24-hour windows is a change that did nothing, so a window in which the guardrail fires is a false alarm.

The alternative, hourly detection on every segment whether or not anything changed, is scored the same way: each
hour, the last 3 hours of each segment against its 14 days before them, with the same minimum drop and volume. It is
reported for two thresholds: hours from the start of the two planted incidents it can see (the routing change and the
three-hour maintenance of issuer BR-ISS-03 on 21 July) to the first alert, and alerts on segment-days with no planted
event (analysis/detector_backtest.py lists them), which are false alarms.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np
import pandas as pd

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "analysis"))
from detector_backtest import PLANTED  # noqa: E402

OUT = os.path.join(ROOT, "analysis", "results", "change_guardrail.json")
HOURS, BASELINE_DAYS, MIN_SENT, MIN_DROP, Z_THRESHOLD = 24, 14, 30, 0.02, -3.5
SENSITIVITY = [-3.0, -3.5, -4.0]
LOCAL_TIME = {"MX": "changed_at_mexico_city", "CO": "changed_at_bogota", "BR": "changed_at_sao_paulo"}
SEGMENT = ["country", "card_type", "is_cross_border"]
COLS = ["created_ts_local", "country", "card_type", "is_cross_border", "sent_to_acquirer", "final_approved",
        "amount_usd", "approved_usd", "fee_usd", "first_acquirer_id"]
# The incident the guardrail is meant to catch, and the rollback that ended it.
INCIDENT, ROLLBACK = "CHG-0813", "CHG-0814"
# Hourly detection on every segment (the alternative): window, thresholds, and the planted incidents it can see.
HOURLY_WINDOW, HOURLY_THRESHOLDS = 3, [-3.5, -4.0]
HOURLY_INCIDENTS = {"routing change (CO debit domestic)": (("CO", "debit", False), "2026-08-13 14:00"),
                    "issuer maintenance (BR, 09:00 to 12:00)": (("BR", None, None), "2026-07-21 09:00")}


def load(gold_dir: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    pay = pd.read_parquet(os.path.join(gold_dir, "silver_payments.parquet"), columns=COLS)
    sent = pay[pay["sent_to_acquirer"]].drop(columns="sent_to_acquirer").copy()
    sent["final_approved"] = sent["final_approved"].astype(int)
    sent = sent.sort_values("created_ts_local", kind="stable").reset_index(drop=True)
    changes = pd.read_parquet(os.path.join(gold_dir, "gold_change_log.parquet"))
    return sent, changes


def follow(seg: pd.DataFrame, t0: pd.Timestamp, z_threshold: float = Z_THRESHOLD) -> dict | None:
    """The guardrail on one segment from t0: baseline and cumulative rows for hours 1 to 24."""
    ts = seg["created_ts_local"].values
    lo, mid, hi = np.searchsorted(ts, [np.datetime64(t0 - pd.Timedelta(days=BASELINE_DAYS)), np.datetime64(t0),
                                       np.datetime64(t0 + pd.Timedelta(hours=HOURS))])
    base, post = seg.iloc[lo:mid], seg.iloc[mid:hi]
    if base.empty:
        return None
    p0 = base["final_approved"].sum() / len(base)
    approved_base = base["approved_usd"].sum()
    volume_rate = approved_base / base["amount_usd"].sum()
    fee_rate = base["fee_usd"].sum() / approved_base if approved_base else np.nan
    hour = ((post["created_ts_local"] - t0) // pd.Timedelta(hours=1)).astype(int) + 1
    g = (post.assign(hour=hour).groupby("hour")
         .agg(payments_sent=("final_approved", "size"), final_approved=("final_approved", "sum"),
              attempted_usd=("amount_usd", "sum"), approved_usd=("approved_usd", "sum"), fees_usd=("fee_usd", "sum"))
         .reindex(range(1, HOURS + 1), fill_value=0).cumsum())
    n, a = g["payments_sent"], g["final_approved"]
    rate = a / n.where(n > 0)
    z = (a - n * p0) / np.sqrt(n * p0 * (1 - p0)).where(n > 0)
    fires = (n >= MIN_SENT) & (rate <= p0 - MIN_DROP) & (z <= z_threshold)
    fired = fires[fires].index
    return dict(baseline_payments=len(base), baseline_rate=p0, baseline_volume_rate=volume_rate,
                baseline_fee_rate=fee_rate, rows=g, rate=rate, z=z,
                fires=fires, first_fire_hours=int(fired[0]) if len(fired) else None)


def monitored(changes: pd.DataFrame, sent: pd.DataFrame) -> list[tuple]:
    """(change row, segment, local change time) for each segment in each change's scope, as the dbt model picks them."""
    segments = sent[SEGMENT].drop_duplicates().sort_values(SEGMENT).itertuples(index=False, name=None)
    segments = list(segments)
    out = []
    for _, ch in changes[changes["change_type"] != "external_notice"].sort_values("change_id").iterrows():
        scope = [s.strip() for s in ch["scope"].split(",")]
        for seg in segments:
            t0 = pd.Timestamp(ch[LOCAL_TIME[seg[0]]])
            if ch["scope"] == "ALL" or seg[0] in scope:
                out.append((ch, seg, t0))
                continue
            in_seg = sent[(sent["country"] == seg[0]) & (sent["card_type"] == seg[1])
                          & (sent["is_cross_border"] == seg[2])]
            window = in_seg[(in_seg["created_ts_local"] >= t0 - pd.Timedelta(days=BASELINE_DAYS))
                            & (in_seg["created_ts_local"] < t0)]
            if (window["first_acquirer_id"] == ch["scope"]).any():
                out.append((ch, seg, t0))
    return out


def guardrail_table(sent: pd.DataFrame, changes: pd.DataFrame) -> pd.DataFrame:
    """The rows of gold_change_guardrail, computed in pandas (same columns and rounding)."""
    groups = {k: v.reset_index(drop=True) for k, v in sent.groupby(SEGMENT)}
    rows = []
    for ch, seg, t0 in monitored(changes, sent):
        f = follow(groups[seg], t0)
        if f is None:
            continue
        g, p0 = f["rows"], f["baseline_rate"]
        for h in range(1, HOURS + 1):
            r = g.loc[h]
            rows.append(dict(
                change_id=ch["change_id"], change_type=ch["change_type"], scope=ch["scope"], country=seg[0],
                card_type=seg[1], is_cross_border=seg[2], changed_at_local=t0, hours_since_change=h,
                baseline_payments=f["baseline_payments"], baseline_rate=round(p0, 4),
                payments_sent=int(r["payments_sent"]), final_approved=int(r["final_approved"]),
                final_approval_rate=None if r["payments_sent"] == 0 else round(f["rate"][h], 4),
                rate_change_pp=None if r["payments_sent"] == 0 else round((f["rate"][h] - p0) * 100, 2),
                z_score=None if r["payments_sent"] == 0 else round(f["z"][h], 2),
                lost_approvals=round(max(r["payments_sent"] * p0 - r["final_approved"], 0), 1),
                lost_approved_usd=round(max(r["attempted_usd"] * f["baseline_volume_rate"] - r["approved_usd"], 0), 2),
                fees_saved_usd=round(r["approved_usd"] * f["baseline_fee_rate"] - r["fees_usd"], 2),
                fires=bool(f["fires"][h]), first_fire_hours=f["first_fire_hours"]))
    return pd.DataFrame(rows)


def incident_loss(sent: pd.DataFrame, changes: pd.DataFrame, seg: tuple) -> dict:
    """What the incident cost from the routing change to the rollback, against the same 14-day baseline."""
    by_id = changes.set_index("change_id")
    t0 = pd.Timestamp(by_id.loc[INCIDENT, LOCAL_TIME[seg[0]]])
    t1 = pd.Timestamp(by_id.loc[ROLLBACK, LOCAL_TIME[seg[0]]])
    s = sent[(sent["country"] == seg[0]) & (sent["card_type"] == seg[1]) & (sent["is_cross_border"] == seg[2])]
    base = s[(s["created_ts_local"] >= t0 - pd.Timedelta(days=BASELINE_DAYS)) & (s["created_ts_local"] < t0)]
    w = s[(s["created_ts_local"] >= t0) & (s["created_ts_local"] < t1)]
    p0 = base["final_approved"].sum() / len(base)
    volume_rate = base["approved_usd"].sum() / base["amount_usd"].sum()
    fee_rate = base["fee_usd"].sum() / base["approved_usd"].sum()
    return dict(hours=round((t1 - t0) / pd.Timedelta(hours=1), 1), payments_sent=len(w),
                lost_approvals=round(len(w) * p0 - w["final_approved"].sum(), 1),
                lost_approved_usd=round(w["amount_usd"].sum() * volume_rate - w["approved_usd"].sum(), 2),
                fees_saved_usd=round(w["approved_usd"].sum() * fee_rate - w["fee_usd"].sum(), 2))


def placebo_days(sent: pd.DataFrame, changes: pd.DataFrame) -> dict[str, list[pd.Timestamp]]:
    """Days with no change in the country the day before, the day itself or the day after, from day 15 on."""
    first = sent["created_ts_local"].min().normalize() + pd.Timedelta(days=BASELINE_DAYS + 1)
    last = sent["created_ts_local"].max().normalize() - pd.Timedelta(days=1)
    out = {}
    for country, col in LOCAL_TIME.items():
        busy = set()
        for t in changes[col]:
            d = pd.Timestamp(t).normalize()
            busy |= {d - pd.Timedelta(days=1), d, d + pd.Timedelta(days=1)}
        out[country] = [d for d in pd.date_range(first, last) if d not in busy]
    return out


def planted(country: str, card_type: str, day: pd.Timestamp) -> bool:
    for c, ct, start, end in PLANTED:
        if country == c and (ct is None or card_type == ct) and pd.Timestamp(start) <= day <= pd.Timestamp(end):
            return True
    return False


def hourly_everywhere(sent: pd.DataFrame, z_threshold: float) -> dict:
    """Every hour, the last HOURLY_WINDOW hours of each segment against its 14 days before them."""
    hourly = (sent.assign(hour=sent["created_ts_local"].dt.floor("h"))
              .groupby(SEGMENT + ["hour"])["final_approved"].agg(["size", "sum"]))
    first_scored = sent["created_ts_local"].min().normalize() + pd.Timedelta(days=BASELINE_DAYS + 1)
    episodes = []
    for seg, g in hourly.groupby(level=[0, 1, 2]):
        g = g.droplevel([0, 1, 2])
        grid = pd.date_range(g.index.min().normalize(), g.index.max().normalize() + pd.Timedelta(hours=23), freq="h")
        g = g.reindex(grid, fill_value=0)
        n, a = g["size"].rolling(HOURLY_WINDOW).sum(), g["sum"].rolling(HOURLY_WINDOW).sum()
        nb = g["size"].rolling(BASELINE_DAYS * 24).sum().shift(HOURLY_WINDOW)
        ab = g["sum"].rolling(BASELINE_DAYS * 24).sum().shift(HOURLY_WINDOW)
        p0 = ab / nb
        z = (a - n * p0) / np.sqrt(n * p0 * (1 - p0))
        fire = ((n >= MIN_SENT) & (a / n <= p0 - MIN_DROP) & (z <= z_threshold)).fillna(False)
        fire = fire[fire.index >= first_scored]
        starts = fire.index[fire & ~fire.shift(1, fill_value=False)]
        episodes += [(seg, t + pd.Timedelta(hours=1)) for t in starts]  # the alert goes out at the end of the hour
    found = {}
    for name, ((country, card_type, xb), start) in HOURLY_INCIDENTS.items():
        start = pd.Timestamp(start)
        hits = [t for (c, ct, x), t in episodes if c == country and (card_type is None or ct == card_type)
                and (xb is None or x == xb) and start < t <= start + pd.Timedelta(hours=24)]
        found[name] = round((min(hits) - start) / pd.Timedelta(hours=1), 1) if hits else None
    false = [(seg, t) for seg, t in episodes if not planted(seg[0], seg[1], (t - pd.Timedelta(hours=1)).normalize())]
    days = (sent["created_ts_local"].max().normalize() - first_scored).days + 1
    return dict(hours_to_first_alert=found, false_alerts=len(false), scored_days=days,
                false_alert_examples=[f"{s[0]} {s[1]} {'cross-border' if s[2] else 'domestic'} {t}" for s, t in false[:8]])


def evaluate(sent: pd.DataFrame, changes: pd.DataFrame) -> dict:
    table = guardrail_table(sent, changes)
    fired = table[table["first_fire_hours"].notna()].drop_duplicates(["change_id", "country", "card_type",
                                                                     "is_cross_border"])
    result = {"changes": [], "incident": None, "false_alarms": {}}
    for change_id, part in table.groupby("change_id", sort=True):
        segs = part.drop_duplicates(["country", "card_type", "is_cross_border"])
        hits = fired[fired["change_id"] == change_id]
        result["changes"].append(dict(
            change_id=change_id, change_type=part["change_type"].iloc[0], scope=part["scope"].iloc[0],
            segments_followed=len(segs),
            fired=[dict(segment=f"{r.country} {r.card_type} {'cross-border' if r.is_cross_border else 'domestic'}",
                        first_fire_hours=int(r.first_fire_hours)) for r in hits.itertuples()]))
    # The incident: when the guardrail fires on Colombian domestic debit, and what a rollback an hour later would cost.
    seg = ("CO", "debit", False)
    rows = table[(table["change_id"] == INCIDENT) & (table["country"] == "CO") & (table["card_type"] == "debit")
                 & (~table["is_cross_border"])].set_index("hours_since_change")
    h = rows["first_fire_hours"].iloc[0]
    whole = incident_loss(sent, changes, seg)
    if pd.notna(h):
        h = int(h)
        at_fire, rolled = rows.loc[h], rows.loc[min(h + 1, HOURS)]
        result["incident"] = dict(
            change_id=INCIDENT, segment="CO debit domestic", first_fire_hours=h,
            at_fire=dict(payments_sent=int(at_fire["payments_sent"]), final_approval_rate=at_fire["final_approval_rate"],
                         baseline_rate=at_fire["baseline_rate"], z_score=at_fire["z_score"],
                         lost_approvals=at_fire["lost_approvals"], lost_approved_usd=at_fire["lost_approved_usd"],
                         fees_saved_usd=at_fire["fees_saved_usd"]),
            rollback_one_hour_after_firing=dict(hours=h + 1, lost_approvals=rolled["lost_approvals"],
                                                lost_approved_usd=rolled["lost_approved_usd"]),
            until_the_actual_rollback=whole,
            share_of_loss_avoided=round(1 - rolled["lost_approvals"] / whole["lost_approvals"], 3))
    # False alarms: 24-hour windows from 10:00 on days with no change, at the rule's threshold and two others.
    groups = {k: v.reset_index(drop=True) for k, v in sent.groupby(SEGMENT)}
    days = placebo_days(sent, changes)
    for zt in SENSITIVITY:
        n = hits = 0
        for seg_key, seg_df in groups.items():
            for d in days[seg_key[0]]:
                f = follow(seg_df, d + pd.Timedelta(hours=10), zt)
                if f is None:
                    continue
                n += 1
                hits += f["first_fire_hours"] is not None
        inc = follow(groups[seg], pd.Timestamp(changes.set_index("change_id").loc[INCIDENT, LOCAL_TIME["CO"]]), zt)
        result["false_alarms"][f"z <= {zt}"] = dict(windows=n, fired=hits, rate=round(hits / n, 4),
                                                    incident_first_fire_hours=inc["first_fire_hours"])
    result["hourly_on_every_segment"] = {f"z <= {zt}": hourly_everywhere(sent, zt) for zt in HOURLY_THRESHOLDS}
    return result


def main(seeds_dir: str | None) -> dict:
    runs = {"seed 42 (in sample)": evaluate(*load(os.path.join(ROOT, "local", "out", "gold")))}
    if seeds_dir and os.path.isdir(seeds_dir):
        for name in sorted(os.listdir(seeds_dir), key=lambda n: int(n.lstrip("s")) if n.lstrip("s").isdigit() else 0):
            path = os.path.join(seeds_dir, name, "out", "gold")
            if os.path.isdir(path):
                runs[f"seed {name.lstrip('s')} (out of sample)"] = evaluate(*load(path))
    result = {"rule": dict(hours=HOURS, baseline_days=BASELINE_DAYS, min_payments=MIN_SENT, min_drop_pp=MIN_DROP * 100,
                           z_threshold=Z_THRESHOLD, hourly_window_hours=HOURLY_WINDOW),
              "runs": runs}
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as fh:
        json.dump(result, fh, indent=1, default=lambda o: o.isoformat() if hasattr(o, "isoformat") else float(o))
    return result


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds-dir", default=None)
    print(json.dumps(main(ap.parse_args().seeds_dir), indent=1,
                     default=lambda o: o.isoformat() if hasattr(o, "isoformat") else float(o)))
