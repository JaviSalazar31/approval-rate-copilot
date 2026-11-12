"""
Backtest of the change guardrail (dbt/models/marts/gold_change_guardrail.sql): how soon it fires after a change that
hurts approval, what it would have saved, which changes it misses, and how often it fires when nothing changed.

  python analysis/change_guardrail.py --seeds-dir DIR

The rule is the same as the dbt model's, written here in pandas so it can run on other datasets and on made-up
changes: for each production change, each segment in its scope (country, card type, domestic or cross-border) is
followed for 24 hours, counted in UTC. Each hour adds up the payments sent since the change, and the guardrail fires
when their final approval is at least 2 points below the segment's 14 days before the change, the binomial z-score is
-3.5 or lower and at least 30 payments were sent, in an hour the data has fully reached.
tests/test_change_guardrail.py checks that this module and the dbt model give the same rows on the reference data.

In sample is the reference run (seed 42, local/out). Out of sample are the datasets of other seeds that
analysis/detector_backtest.py uses (how to build DIR is in that script). They come from the same generator, so they
test new noise around the same planted incidents. The threshold of -3.5 was set after a first run at -3 on all four
datasets, so they are not a clean out-of-sample test of it.

False alarms: every segment is also followed from 10:00 local time on each day with no entry of any kind in the change
log (partner notices included) the day before, the day itself or the day after, from the 16th day of data on. Each of
those 24-hour windows is a change that did nothing, so a window in which the guardrail fires is a false alarm.
Cross-border segments send about 25 to 75 payments a day, so the rule can rarely fire there within a few hours; the results
also count the domestic windows alone.

The alternative, hourly detection on every segment whether or not anything changed, is scored the same way: each
hour, the last 3 hours of each segment against its 14 days before them, with the same minimum drop and volume. It is
reported for two thresholds: hours from the start of the two planted incidents it can see (the routing change and the
three-hour maintenance of issuer BR-ISS-03 on 21 July) to the first alert, and alerts on segment-days with no planted
event (analysis/detector_backtest.py lists them), which are false alarms. The partner notice of that maintenance is
also followed with the guardrail's rule, to show what the guardrail would do if it followed notices.
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
COLS = ["created_ts_utc", "created_ts_local", "country", "card_type", "is_cross_border", "sent_to_acquirer",
        "final_approved", "amount_usd", "approved_usd", "fee_usd", "first_acquirer_id", "first_iso_code",
        "is_cascaded", "recovered_by_cascade"]
# The incident the guardrail is meant to catch, the rollback that ended it, the change it misses and the notice it skips.
INCIDENT, ROLLBACK, MAP_CHANGE, NOTICE = "CHG-0813", "CHG-0814", "CHG-0806", "EXT-0721"
# Hourly detection on every segment (the alternative): window, thresholds, and the planted incidents it can see.
HOURLY_WINDOW, HOURLY_THRESHOLDS = 3, [-3.5, -4.0]
HOURLY_INCIDENTS = {"routing change (CO debit domestic)": (("CO", "debit", False), "2026-08-13 14:00"),
                    "issuer maintenance (BR, 09:00 to 12:00)": (("BR", None, None), "2026-07-21 09:00")}


def seg_name(seg: tuple) -> str:
    return f"{seg[0]} {seg[1]} {'cross-border' if seg[2] else 'domestic'}"


def load(gold_dir: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    pay = pd.read_parquet(os.path.join(gold_dir, "silver_payments.parquet"), columns=COLS)
    sent = pay[pay["sent_to_acquirer"]].drop(columns="sent_to_acquirer").copy()
    sent["final_approved"] = sent["final_approved"].astype(int)
    sent = sent.sort_values("created_ts_utc", kind="stable").reset_index(drop=True)
    changes = pd.read_parquet(os.path.join(gold_dir, "gold_change_log.parquet"))
    return sent, changes


def data_end(sent: pd.DataFrame) -> dict[str, pd.Timestamp]:
    """Last payment of each country (UTC): an hour after a change counts only once the data has reached its end."""
    return sent.groupby("country")["created_ts_utc"].max().to_dict()


def follow(seg: pd.DataFrame, t0: pd.Timestamp, end: pd.Timestamp, z_threshold: float = Z_THRESHOLD) -> dict | None:
    """The guardrail on one segment from t0 (UTC): baseline and cumulative rows for hours 1 to 24, unrounded."""
    ts = seg["created_ts_utc"].values
    lo, mid, hi = np.searchsorted(ts, [np.datetime64(t0 - pd.Timedelta(days=BASELINE_DAYS)), np.datetime64(t0),
                                       np.datetime64(t0 + pd.Timedelta(hours=HOURS))])
    base, post = seg.iloc[lo:mid], seg.iloc[mid:hi]
    if base.empty:
        return None
    p0 = base["final_approved"].sum() / len(base)
    approved_base = base["approved_usd"].sum()
    volume_rate = approved_base / base["amount_usd"].sum()
    fee_rate = base["fee_usd"].sum() / approved_base if approved_base else np.nan
    hour = ((post["created_ts_utc"] - t0) // pd.Timedelta(hours=1)).astype(int) + 1
    g = (post.assign(hour=hour).groupby("hour")
         .agg(payments_sent=("final_approved", "size"), final_approved=("final_approved", "sum"),
              attempted_usd=("amount_usd", "sum"), approved_usd=("approved_usd", "sum"), fees_usd=("fee_usd", "sum"))
         .reindex(range(1, HOURS + 1), fill_value=0).cumsum())
    n, a = g["payments_sent"], g["final_approved"]
    complete = pd.Series([t0 + pd.Timedelta(hours=h) <= end for h in g.index], index=g.index)
    rate = a / n.where(n > 0)
    z = (a - n * p0) / np.sqrt(n * p0 * (1 - p0)).where(n > 0)
    fires = complete & (n >= MIN_SENT) & (rate <= p0 - MIN_DROP) & (z <= z_threshold)
    fired = fires[fires].index
    return dict(baseline_days=(t0 - base["created_ts_utc"].min()) / pd.Timedelta(days=1),
                baseline_payments=len(base), baseline_rate=p0, baseline_volume_rate=volume_rate,
                baseline_fee_rate=fee_rate, rows=g, complete=complete, rate=rate, z=z, fires=fires,
                first_fire_hours=int(fired[0]) if len(fired) else None)


def at_hour(f: dict, h: int) -> dict:
    """The guardrail's figures h hours after the change, unrounded (the docs round them once)."""
    r, p0 = f["rows"].loc[h], f["baseline_rate"]
    return dict(hours=h, payments_sent=int(r["payments_sent"]), final_approval_rate=f["rate"][h], baseline_rate=p0,
                z_score=f["z"][h], lost_approvals=max(r["payments_sent"] * p0 - r["final_approved"], 0),
                lost_approved_usd=max(r["attempted_usd"] * f["baseline_volume_rate"] - r["approved_usd"], 0),
                fees_saved_usd=r["approved_usd"] * f["baseline_fee_rate"] - r["fees_usd"])


def monitored(changes: pd.DataFrame, sent: pd.DataFrame) -> list[tuple]:
    """(change row, segment, change time in UTC) for each segment in each change's scope, as the dbt model picks them."""
    segments = list(sent[SEGMENT].drop_duplicates().sort_values(SEGMENT).itertuples(index=False, name=None))
    out = []
    for _, ch in changes[changes["change_type"] != "external_notice"].sort_values("change_id").iterrows():
        scope = [s.strip() for s in ch["scope"].split(",")]
        t0 = pd.Timestamp(ch["changed_at_utc"])
        for seg in segments:
            if ch["scope"] == "ALL" or seg[0] in scope:
                out.append((ch, seg, t0))
                continue
            in_seg = sent[(sent["country"] == seg[0]) & (sent["card_type"] == seg[1])
                          & (sent["is_cross_border"] == seg[2])]
            window = in_seg[(in_seg["created_ts_utc"] >= t0 - pd.Timedelta(days=BASELINE_DAYS))
                            & (in_seg["created_ts_utc"] < t0)]
            if (window["first_acquirer_id"] == ch["scope"]).any():
                out.append((ch, seg, t0))
    return out


def guardrail_table(sent: pd.DataFrame, changes: pd.DataFrame) -> pd.DataFrame:
    """The rows of gold_change_guardrail, computed in pandas (same columns and rounding)."""
    groups = {k: v.reset_index(drop=True) for k, v in sent.groupby(SEGMENT)}
    ends = data_end(sent)
    rows = []
    for ch, seg, t0 in monitored(changes, sent):
        f = follow(groups[seg], t0, ends[seg[0]])
        if f is None:
            continue
        g, p0 = f["rows"], f["baseline_rate"]
        for h in range(1, HOURS + 1):
            r, m = g.loc[h], at_hour(f, h)
            empty = r["payments_sent"] == 0
            rows.append(dict(
                change_id=ch["change_id"], change_type=ch["change_type"], scope=ch["scope"], country=seg[0],
                card_type=seg[1], is_cross_border=seg[2], changed_at_local=pd.Timestamp(ch[LOCAL_TIME[seg[0]]]),
                hours_since_change=h, hour_complete=bool(f["complete"][h]), baseline_days=round(f["baseline_days"], 1),
                baseline_payments=f["baseline_payments"], baseline_rate=round(p0, 4),
                payments_sent=int(r["payments_sent"]), final_approved=int(r["final_approved"]),
                final_approval_rate=None if empty else round(m["final_approval_rate"], 4),
                rate_change_pp=None if empty else round((m["final_approval_rate"] - p0) * 100, 2),
                z_score=None if empty else round(m["z_score"], 2),
                lost_approvals=round(m["lost_approvals"], 1), lost_approved_usd=round(m["lost_approved_usd"], 2),
                fees_saved_usd=round(m["fees_saved_usd"], 2), fires=bool(f["fires"][h]),
                first_fire_hours=f["first_fire_hours"]))
    return pd.DataFrame(rows)


def incident_loss(sent: pd.DataFrame, changes: pd.DataFrame, seg: tuple) -> dict:
    """What the incident cost from the routing change to the rollback, against the same 14-day baseline (unrounded)."""
    by_id = changes.set_index("change_id")
    t0, t1 = pd.Timestamp(by_id.loc[INCIDENT, "changed_at_utc"]), pd.Timestamp(by_id.loc[ROLLBACK, "changed_at_utc"])
    s = sent[(sent["country"] == seg[0]) & (sent["card_type"] == seg[1]) & (sent["is_cross_border"] == seg[2])]
    base = s[(s["created_ts_utc"] >= t0 - pd.Timedelta(days=BASELINE_DAYS)) & (s["created_ts_utc"] < t0)]
    w = s[(s["created_ts_utc"] >= t0) & (s["created_ts_utc"] < t1)]
    p0 = base["final_approved"].sum() / len(base)
    volume_rate = base["approved_usd"].sum() / base["amount_usd"].sum()
    fee_rate = base["fee_usd"].sum() / base["approved_usd"].sum()
    return dict(hours=(t1 - t0) / pd.Timedelta(hours=1), payments_sent=len(w),
                lost_approvals=len(w) * p0 - w["final_approved"].sum(),
                lost_approved_usd=w["amount_usd"].sum() * volume_rate - w["approved_usd"].sum(),
                fees_saved_usd=w["approved_usd"].sum() * fee_rate - w["fee_usd"].sum())


def utc_offsets(sent: pd.DataFrame) -> dict[str, pd.Timedelta]:
    """Local time minus UTC, per country (fixed: Mexico, Colombia and Brazil have no daylight saving time)."""
    return (sent["created_ts_local"] - sent["created_ts_utc"]).groupby(sent["country"]).agg(lambda x: x.mode()[0]).to_dict()


def placebo_days(sent: pd.DataFrame, changes: pd.DataFrame) -> dict[str, list[pd.Timestamp]]:
    """Local days with no entry in the change log the day before, that day or the day after, from the 16th day on."""
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
    """Every hour, the last HOURLY_WINDOW hours of each segment against its 14 days before them (local hours)."""
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
                false_alert_examples=[f"{seg_name(s)} {t}" for s, t in false[:8]])


def switched_off_retries(seg: pd.DataFrame, t0: pd.Timestamp) -> tuple[float, float]:
    """What the map change cost a segment in its first 24 hours through the retries it switched off: Acquirer B's
    "do not honor" (05) declines that were not retried, at the share of those declines that a retry recovered in the
    14 days before. Returns the approvals and the points of final approval of the payments sent in those 24 hours."""
    b05 = seg[(seg["first_acquirer_id"] == "B") & (seg["first_iso_code"] == "05")]
    before = b05[(b05["created_ts_utc"] >= t0 - pd.Timedelta(days=BASELINE_DAYS)) & (b05["created_ts_utc"] < t0)]
    window = (seg["created_ts_utc"] >= t0) & (seg["created_ts_utc"] < t0 + pd.Timedelta(hours=HOURS))
    after = b05[window.loc[b05.index]]
    if before.empty or not window.any():
        return 0.0, 0.0
    not_retried = int((~after["is_cascaded"].fillna(False).astype(bool)).sum())
    lost = not_retried * float(before["recovered_by_cascade"].fillna(False).astype(float).mean())
    return lost, lost / int(window.sum()) * 100


def evaluate(sent: pd.DataFrame, changes: pd.DataFrame) -> dict:
    table = guardrail_table(sent, changes)
    groups = {k: v.reset_index(drop=True) for k, v in sent.groupby(SEGMENT)}
    ends, by_id = data_end(sent), changes.set_index("change_id")
    fired = table[table["first_fire_hours"].notna()].drop_duplicates(["change_id", "country", "card_type",
                                                                     "is_cross_border"])
    result = {"changes": [], "incident": None, "map_change": [], "partner_notice": [], "false_alarms": {}}
    for change_id, part in table.groupby("change_id", sort=True):
        segs = part.drop_duplicates(["country", "card_type", "is_cross_border"])
        hits = fired[fired["change_id"] == change_id]
        result["changes"].append(dict(
            change_id=change_id, change_type=part["change_type"].iloc[0], scope=part["scope"].iloc[0],
            segments_followed=len(segs), baseline_days=float(segs["baseline_days"].min()),
            fired=[dict(segment=seg_name((r.country, r.card_type, r.is_cross_border)),
                        first_fire_hours=int(r.first_fire_hours)) for r in hits.itertuples()]))
    # The incident: when the guardrail fires on Colombian domestic debit, and what a rollback an hour later would cost.
    seg = ("CO", "debit", False)
    t0 = pd.Timestamp(by_id.loc[INCIDENT, "changed_at_utc"])
    f = follow(groups[seg], t0, ends["CO"])
    whole = incident_loss(sent, changes, seg)
    if f["first_fire_hours"]:
        h = f["first_fire_hours"]
        rolled = at_hour(f, min(h + 1, HOURS))
        result["incident"] = dict(
            change_id=INCIDENT, segment=seg_name(seg), first_fire_hours=h, at_fire=at_hour(f, h),
            rollback_one_hour_after_firing=dict(hours=rolled["hours"], lost_approvals=rolled["lost_approvals"],
                                                lost_approved_usd=rolled["lost_approved_usd"]),
            until_the_actual_rollback=whole,
            share_of_loss_avoided=1 - rolled["lost_approvals"] / whole["lost_approvals"])
    # The decline-map change of 6 August (Acquirer B), which the retry alert caught: what the guardrail saw, and how much
    # of the drop the retries it switched off explain (the rest is noise and declines the old map had quarantined).
    t_map = pd.Timestamp(by_id.loc[MAP_CHANGE, "changed_at_utc"])
    for ch, s, _ in [m for m in monitored(changes, sent) if m[0]["change_id"] == MAP_CHANGE]:
        fm = follow(groups[s], t_map, ends[s[0]])
        last = at_hour(fm, HOURS)
        retries_lost, retries_pp = switched_off_retries(groups[s], t_map)
        result["map_change"].append(dict(segment=seg_name(s), rate_change_pp=(last["final_approval_rate"]
                                                                              - last["baseline_rate"]) * 100,
                                         lost_approvals=last["lost_approvals"], z_score_at_24h=last["z_score"],
                                         lowest_z_score=float(fm["z"].min()), fired=fm["first_fire_hours"],
                                         lost_to_switched_off_retries=retries_lost,
                                         switched_off_retries_pp=-retries_pp))
    # The partner notice of 21 July, which the guardrail leaves out: the same rule followed from the notice.
    t_notice = pd.Timestamp(by_id.loc[NOTICE, "changed_at_utc"])
    for s in [k for k in groups if k[0] == "BR" and not k[2]]:
        fn = follow(groups[s], t_notice, ends["BR"])
        result["partner_notice"].append(dict(segment=seg_name(s), first_fire_hours=fn["first_fire_hours"]))
    # False alarms: 24-hour windows from 10:00 local on quiet days, at the rule's threshold and two others.
    offsets, days = utc_offsets(sent), placebo_days(sent, changes)
    for zt in SENSITIVITY:
        n = hits = n_dom = hits_dom = 0
        for seg_key, seg_df in groups.items():
            for d in days[seg_key[0]]:
                fp = follow(seg_df, d + pd.Timedelta(hours=10) - offsets[seg_key[0]], ends[seg_key[0]], zt)
                if fp is None:
                    continue
                hit = fp["first_fire_hours"] is not None
                n, hits = n + 1, hits + hit
                if not seg_key[2]:
                    n_dom, hits_dom = n_dom + 1, hits_dom + hit
        inc = follow(groups[seg], t0, ends["CO"], zt)
        result["false_alarms"][f"z <= {zt}"] = dict(windows=n, fired=hits, rate=hits / n, domestic_windows=n_dom,
                                                    domestic_fired=hits_dom, incident_first_fire_hours=inc["first_fire_hours"])
    result["hourly_on_every_segment"] = {f"z <= {zt}": hourly_everywhere(sent, zt) for zt in HOURLY_THRESHOLDS}
    return result


def rounded(o):
    if isinstance(o, dict):
        return {k: rounded(v) for k, v in o.items()}
    if isinstance(o, list):
        return [rounded(v) for v in o]
    if isinstance(o, (float, np.floating)):
        return round(float(o), 4)
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    return o


def main(seeds_dir: str) -> dict:
    runs = {"seed 42 (in sample)": evaluate(*load(os.path.join(ROOT, "local", "out", "gold")))}
    for name in sorted(os.listdir(seeds_dir), key=lambda n: int(n.lstrip("s")) if n.lstrip("s").isdigit() else 0):
        path = os.path.join(seeds_dir, name, "out", "gold")
        if os.path.isdir(path):
            runs[f"seed {name.lstrip('s')} (out of sample)"] = evaluate(*load(path))
    result = rounded({"rule": dict(hours=HOURS, baseline_days=BASELINE_DAYS, min_payments=MIN_SENT,
                                   min_drop_pp=MIN_DROP * 100, z_threshold=Z_THRESHOLD,
                                   hourly_window_hours=HOURLY_WINDOW),
                      "runs": runs})
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as fh:
        json.dump(result, fh, indent=1)
    return result


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds-dir", required=True,
                    help="datasets of other seeds (see analysis/detector_backtest.py); the page reads all four")
    print(json.dumps(main(ap.parse_args().seeds_dir), indent=1))
