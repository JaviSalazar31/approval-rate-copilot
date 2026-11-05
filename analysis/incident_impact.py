"""
Impact of routing change v14 on Colombian domestic debit, measured with a difference-in-differences
design and a bootstrap confidence interval.

  python analysis/incident_impact.py          # reads local/out, writes analysis/results/incident_impact.json

The copilot sizes the drop against the same segment's previous 14 days (fn_drop_impact). That
comparison is right only if nothing else moved in the window. Here the control group is Colombian
domestic credit: same market, same hours, same days, and routing v14 did not touch it. Whatever hit
both segments (a daily shock in Colombia, the time-of-day pattern) cancels out, and what is left is
the effect of the change.

Design choices:
- Window: 13 Aug 14:00 to 14 Aug 12:10 local time (19:00 to 17:10 UTC), from the change to the rollback.
- Pre-period: the 7 days before the change. It starts after decline-code map v2.3 (6 Aug, 08:00
  local), which also affects the control group (30% of Colombian credit goes to Acquirer B). A longer
  pre-period would mix days before and after that change into the control's baseline.
- Uncertainty: payments in the same hour share shocks, so the bootstrap resamples whole clock hours
  (cluster bootstrap), separately in the pre-period and the window.
- Placebo test: the same estimate on every date whose window and pre-period touch no change to Colombian
  traffic. If the method is sound, those estimates sit near zero.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
OUT = os.path.join(ROOT, "analysis", "results", "incident_impact.json")

CHANGE_START = datetime(2026, 8, 13, 14, 0)     # routing v14, local time (UTC-5)
ROLLBACK = datetime(2026, 8, 14, 12, 10)        # rollback to v13, local time
PRE_DAYS = 7
COPILOT_BASELINE_DAYS = 14                      # fn_drop_impact default
# Changes that touch Colombian traffic (scope CO or ALL, plus Acquirer B's decline-code map, which also
# covers the 30% of Colombian credit routed to B), local time: no placebo window or pre-period may span one.
CO_CHANGES = [datetime(2026, 6, 3, 10, 0), datetime(2026, 7, 28, 11, 0), datetime(2026, 8, 6, 8, 0),
              datetime(2026, 8, 13, 14, 0), datetime(2026, 8, 14, 12, 10), datetime(2026, 8, 15, 8, 0)]
DATA_START, DATA_END = datetime(2026, 6, 1, 0, 0), datetime(2026, 8, 30, 0, 0)
N_BOOT = 5000
SEED = 20260813


def r2(x) -> float:
    return round(float(x), 2)


def load(out_dir: str) -> pd.DataFrame:
    sp = pd.read_parquet(os.path.join(out_dir, "gold", "silver_payments.parquet"),
                         columns=["country", "card_type", "is_cross_border", "sent_to_acquirer",
                                  "created_ts_local", "final_approved", "amount_usd", "approved_usd"])
    sp = sp[(sp.country == "CO") & sp.sent_to_acquirer & ~sp.is_cross_border].copy()
    sp["ok"] = sp.final_approved.astype(int)
    sp["approved_usd"] = sp.approved_usd.fillna(0.0)
    sp["hour"] = sp.created_ts_local.dt.floor("h")
    return sp


def hourly(sp: pd.DataFrame, card_type: str, start: datetime, end: datetime) -> pd.DataFrame:
    """Payments, approvals and USD per clock hour for one segment and period."""
    s = sp[(sp.card_type == card_type) & (sp.created_ts_local >= start) & (sp.created_ts_local < end)]
    return s.groupby("hour").agg(n=("ok", "size"), ok=("ok", "sum"),
                                 usd=("amount_usd", "sum"), ok_usd=("approved_usd", "sum"))


def did(sp: pd.DataFrame, start: datetime, end: datetime, pre_days: int, n_boot: int, rng) -> dict:
    pre_start = start - timedelta(days=pre_days)
    cells = {(g, p): hourly(sp, g, *span)
             for g in ("debit", "credit")
             for p, span in (("pre", (pre_start, start)), ("post", (start, end)))}

    def rate(t, col="ok", base="n"):
        return t[col].sum() / t[base].sum()

    est = ((rate(cells["debit", "post"]) - rate(cells["debit", "pre"]))
           - (rate(cells["credit", "post"]) - rate(cells["credit", "pre"])))
    est_vol = ((rate(cells["debit", "post"], "ok_usd", "usd") - rate(cells["debit", "pre"], "ok_usd", "usd"))
               - (rate(cells["credit", "post"], "ok_usd", "usd") - rate(cells["credit", "pre"], "ok_usd", "usd")))

    # Cluster bootstrap: resample clock hours, keeping both segments of an hour together.
    boot, boot_vol = np.empty(n_boot), np.empty(n_boot)
    arrays = {}
    for p in ("pre", "post"):
        hours = cells["debit", p].index.union(cells["credit", p].index)
        d = cells["debit", p].reindex(hours, fill_value=0)
        c = cells["credit", p].reindex(hours, fill_value=0)
        arrays[p] = {k: np.column_stack([d[k].to_numpy(float), c[k].to_numpy(float)])
                     for k in ("n", "ok", "usd", "ok_usd")}
    for b in range(n_boot):
        r, rv = {}, {}
        for p in ("pre", "post"):
            a = arrays[p]
            idx = rng.integers(0, len(a["n"]), len(a["n"]))
            sums = {k: v[idx].sum(axis=0) for k, v in a.items()}
            r[p] = sums["ok"] / sums["n"]
            rv[p] = sums["ok_usd"] / sums["usd"]
        boot[b] = (r["post"][0] - r["pre"][0]) - (r["post"][1] - r["pre"][1])
        boot_vol[b] = (rv["post"][0] - rv["pre"][0]) - (rv["post"][1] - rv["pre"][1])

    win = cells["debit", "post"]
    n_win, usd_win = int(win.n.sum()), float(win.usd.sum())
    lo, hi = np.percentile(boot, [2.5, 97.5])
    lo_v, hi_v = np.percentile(boot_vol, [2.5, 97.5])
    return {
        "window_local": [str(start), str(end)],
        "pre_period_local": [str(pre_start), str(start)],
        "treated": {p: {"payments": int(cells["debit", p].n.sum()),
                        "approved": int(cells["debit", p].ok.sum()),
                        "final_rate": round(float(rate(cells["debit", p])), 4)} for p in ("pre", "post")},
        "control": {p: {"payments": int(cells["credit", p].n.sum()),
                        "approved": int(cells["credit", p].ok.sum()),
                        "final_rate": round(float(rate(cells["credit", p])), 4)} for p in ("pre", "post")},
        "did_pp": r2(100 * est),
        "did_ci95_pp": [r2(100 * lo), r2(100 * hi)],
        "lost_approvals": round(float(-est * n_win)),
        "lost_approvals_ci95": [round(float(-hi * n_win)), round(float(-lo * n_win))],
        "lost_approved_usd": round(float(-est_vol * usd_win)),
        "lost_approved_usd_ci95": [round(float(-hi_v * usd_win)), round(float(-lo_v * usd_win))],
        "window_payments": n_win,
        "control_volume_rate_change_pp": r2(100 * (rate(cells["credit", "post"], "ok_usd", "usd")
                                                   - rate(cells["credit", "pre"], "ok_usd", "usd"))),
        "did_volume_rate_pp": r2(100 * est_vol),
    }


def copilot_before_after(sp: pd.DataFrame, n_boot: int, rng) -> dict:
    """Same arithmetic as fn_drop_impact (the window against the segment's previous 14 days),
    plus the cluster-bootstrap interval that the copilot does not report."""
    deb = sp[sp.card_type == "debit"]
    win = deb[(deb.created_ts_local >= CHANGE_START) & (deb.created_ts_local < ROLLBACK)]
    base = deb[(deb.created_ts_local >= CHANGE_START - timedelta(days=COPILOT_BASELINE_DAYS))
               & (deb.created_ts_local < CHANGE_START)]
    base_rate = base.ok.mean()
    vol_rate = base.approved_usd.sum() / base.amount_usd.sum()
    hw, hb = win.groupby("hour").ok.agg(["size", "sum"]), base.groupby("hour").ok.agg(["size", "sum"])
    boot = np.empty(n_boot)
    for b in range(n_boot):
        iw = rng.integers(0, len(hw), len(hw))
        ib = rng.integers(0, len(hb), len(hb))
        boot[b] = (hw["sum"].to_numpy()[iw].sum() / hw["size"].to_numpy()[iw].sum()
                   - hb["sum"].to_numpy()[ib].sum() / hb["size"].to_numpy()[ib].sum())
    lo, hi = np.percentile(boot, [2.5, 97.5])
    return {
        "baseline_days": COPILOT_BASELINE_DAYS,
        "final_rate_change_pp": r2(100 * (win.ok.mean() - base_rate)),
        "final_rate_change_ci95_pp": [r2(100 * lo), r2(100 * hi)],
        "lost_approvals": int(round(len(win) * base_rate) - win.ok.sum()),
        "lost_approved_usd": round(float(win.amount_usd.sum() * vol_rate - win.approved_usd.sum())),
    }


def pre_trend(sp: pd.DataFrame) -> list[dict]:
    """Daily gap between debit and credit approval in the pre-period (parallel-trends check)."""
    s = sp[(sp.created_ts_local >= CHANGE_START - timedelta(days=PRE_DAYS)) & (sp.created_ts_local < CHANGE_START)]
    s = s.assign(day=s.created_ts_local.dt.date)
    r = s.groupby(["day", "card_type"]).ok.mean().unstack()
    return [{"date": str(d), "debit_minus_credit_pp": r2(100 * (row.debit - row.credit))}
            for d, row in r.iterrows()]


def placebo_starts() -> list[datetime]:
    """Every date at 14:00 whose 7-day pre-period and 22-hour window touch no Colombian change."""
    window = ROLLBACK - CHANGE_START
    starts, day = [], datetime(2026, 6, 1, 14, 0)
    while day + window <= DATA_END:
        lo, hi = day - timedelta(days=PRE_DAYS), day + window
        if lo >= DATA_START and day.date() != CHANGE_START.date() and not any(lo <= c < hi for c in CO_CHANGES):
            starts.append(day)
        day += timedelta(days=1)
    return starts


def main(out_dir: str = os.path.join(ROOT, "local", "out")) -> dict:
    sp = load(out_dir)
    rng = np.random.default_rng(SEED)
    result = {
        "question": "Effect of routing v14 on final approval of Colombian domestic debit",
        "control_group": "Colombian domestic credit (not touched by routing v14)",
        "copilot_before_after": copilot_before_after(sp, N_BOOT, rng),
        "did": did(sp, CHANGE_START, ROLLBACK, PRE_DAYS, N_BOOT, rng),
        # 10 and 14 days reach back before decline-code map v2.3 (6 August), which also moved the control
        "pre_period_sensitivity": [
            {"pre_days": k, "did_pp": did(sp, CHANGE_START, ROLLBACK, k, 1, rng)["did_pp"]} for k in (3, 5, 10, 14)
        ],
        "pre_trend": pre_trend(sp),
        "placebo": [],
    }
    window = ROLLBACK - CHANGE_START
    for start in placebo_starts():
        r = did(sp, start, start + window, PRE_DAYS, 2000, rng)
        result["placebo"].append({"start_local": str(start), "did_pp": r["did_pp"], "did_ci95_pp": r["did_ci95_pp"]})
    est = np.array([p["did_pp"] for p in result["placebo"]])
    result["placebo_summary"] = {
        "dates": len(est), "min_pp": r2(est.min()), "max_pp": r2(est.max()), "sd_pp": r2(est.std(ddof=1)),
        "intervals_excluding_zero": sum(1 for p in result["placebo"] if p["did_ci95_pp"][1] < 0 or p["did_ci95_pp"][0] > 0),
        "incident_in_placebo_sds": r2(result["did"]["did_pp"] / est.std(ddof=1)),
    }
    d = result["did"]
    result["did"]["control_change_pp"] = r2(100 * (d["control"]["post"]["final_rate"] - d["control"]["pre"]["final_rate"]))
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as f:
        json.dump(result, f, indent=1)
    return result


if __name__ == "__main__":
    res = main()
    d = res["did"]
    c = res["copilot_before_after"]
    print(f"Before/after (copilot, 14-day baseline): {c['final_rate_change_pp']} pp, 95% CI {c['final_rate_change_ci95_pp']}")
    print(f"Difference-in-differences: {d['did_pp']} pp, 95% CI {d['did_ci95_pp']}")
    print(f"Lost approvals: {d['lost_approvals']} {d['lost_approvals_ci95']}; "
          f"lost approved USD: {d['lost_approved_usd']} {d['lost_approved_usd_ci95']}")
    print("Placebo:", res["placebo_summary"])
    print("Pre-period sensitivity:", res["pre_period_sensitivity"])
    print("Pre-trend gaps:", [g["debit_minus_credit_pp"] for g in res["pre_trend"]])
