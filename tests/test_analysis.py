"""
The incident analysis (analysis/incident_impact.py) on the local pipeline output: the before/after arithmetic
matches what fn_drop_impact returns in the evaluation, and the difference-in-differences estimate behaves.

Run after `python local/run_local.py`:
    python -m pytest tests/test_analysis.py
    python tests/test_analysis.py
"""
from __future__ import annotations

import os
import sys

import numpy as np

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "analysis"))
import incident_impact as ii  # noqa: E402

SP = ii.load(os.path.join(ROOT, "local", "out"))


def test_before_after_matches_the_copilot_function():
    r = ii.copilot_before_after(SP, n_boot=200, rng=np.random.default_rng(0))
    assert (r["final_rate_change_pp"], r["lost_approvals"], r["lost_approved_usd"]) == (-10.95, 110, 6354)


def test_difference_in_differences_excludes_zero():
    r = ii.did(SP, ii.CHANGE_START, ii.ROLLBACK, ii.PRE_DAYS, n_boot=1000, rng=np.random.default_rng(1))
    lo, hi = r["did_ci95_pp"]
    assert lo < r["did_pp"] < hi < 0


def test_placebo_dates_stay_near_zero():
    window = ii.ROLLBACK - ii.CHANGE_START
    starts = ii.placebo_starts()
    assert len(starts) >= 50
    for start in starts:
        r = ii.did(SP, start, start + window, ii.PRE_DAYS, n_boot=1, rng=np.random.default_rng(2))
        assert abs(r["did_pp"]) < 5, (start, r["did_pp"])


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    for name, fn in tests:
        fn()
        print(f"ok  {name}")
    print(f"{len(tests)} checks passed")
