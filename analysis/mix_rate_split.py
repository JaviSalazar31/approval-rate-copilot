"""
Splits the Colombian debit drop of 13-14 August into mix and rate effects with the copilot's own
function, fn_approval_drivers, run on the local pipeline output.

  python analysis/mix_rate_split.py           # needs local/out (python local/run_local.py)

For each dimension, the function's rate effects plus mix effects add up to the total change. A mix
effect means traffic moved toward segments that approve less; a rate effect means segments approved
less than before. The acquirer dimension is left out: Acquirer B had no Colombian debit in the
baseline, so the function gives it the baseline average and books all of its change as rate.
"""
from __future__ import annotations

import json
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "local"))
OUT = os.path.join(ROOT, "analysis", "results", "mix_rate_split.json")

QUERY = ("SELECT * FROM fn_approval_drivers('CO', '2026-08-13 14:00:00', '2026-08-14 12:10:00', "
         "'debit', 14, NULL)")
DIMENSIONS = ["issuer", "three_ds_authenticated", "merchant_segment", "card_brand"]


def main() -> dict:
    from run_local import load_outputs
    from sql_runner import run_sql

    spark = load_outputs()
    rows = run_sql(spark, QUERY, limit=500)
    total = float(rows.total_change_pp.iloc[0])
    result = {"query": QUERY, "total_change_pp": total, "dimensions": []}
    for dim in DIMENSIONS:
        d = rows[rows.dimension == dim].copy()
        d = d.sort_values("rate_effect_pp")
        result["dimensions"].append({
            "dimension": dim,
            "rate_effect_pp": round(float(d.rate_effect_pp.sum()), 2),
            "mix_effect_pp": round(float(d.mix_effect_pp.sum()), 2),
            "largest_rate_effects": [
                {"segment": r.segment_value, "baseline_rate": float(r.baseline_rate),
                 "window_rate": float(r.window_rate), "window_share": float(r.window_share),
                 "rate_effect_pp": float(r.rate_effect_pp)}
                for r in d.head(3).itertuples()
            ],
        })
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as f:
        json.dump(result, f, indent=1)
    return result


if __name__ == "__main__":
    res = main()
    print(f"Total change: {res['total_change_pp']} pp")
    for d in res["dimensions"]:
        top = ", ".join(f"{x['segment']} {x['rate_effect_pp']}" for x in d["largest_rate_effects"])
        print(f"{d['dimension']:24s} rate {d['rate_effect_pp']:>7}  mix {d['mix_effect_pp']:>6}  | {top}")
