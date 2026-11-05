"""
Feed checks against the data contracts (checks/feed_checks.py), on the sample day and on broken copies of it.
No Spark needed.

    python -m pytest tests/test_feed_checks.py
    python tests/test_feed_checks.py
"""
from __future__ import annotations

import atexit
import csv
import os
import shutil
import sys
import tempfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "checks"))
from feed_checks import load_contracts, looks_like_pan, run  # noqa: E402

SAMPLE = os.path.join(ROOT, "data", "sample")
DAY = "2026-08-13"
CONTRACTS = load_contracts()


def copy_sample() -> str:
    tmp = tempfile.mkdtemp(prefix="feeds_")
    atexit.register(shutil.rmtree, tmp, ignore_errors=True)
    shutil.copytree(os.path.join(SAMPLE, "raw"), os.path.join(tmp, "raw"))
    return tmp


def rewrite(path: str, fn) -> None:
    with open(path, newline="") as f:
        rows = list(csv.reader(f))
    rows = fn(rows)
    with open(path, "w", newline="") as f:
        csv.writer(f).writerows(rows)


def by_feed(data_dir: str, date: str = DAY) -> dict:
    return {r["feed"]: r for r in run(data_dir, date, contracts=CONTRACTS)}


def checks_failed(report: dict) -> set:
    return {v["check"] for v in report["violations"]}


def test_sample_day_passes():
    reports = by_feed(SAMPLE)
    assert all(r["status"] == "pass" for r in reports.values()), reports


def test_renamed_column_stops_the_run():
    tmp = copy_sample()
    path = os.path.join(tmp, "raw", "acquirer_b", f"{DAY}.csv")
    rewrite(path, lambda rows: [["response_code" if c == "reason_code" else c for c in rows[0]]] + rows[1:])
    assert "schema" in checks_failed(by_feed(tmp)["acquirer_b"])


def test_new_timestamp_format_stops_the_run():
    tmp = copy_sample()
    path = os.path.join(tmp, "raw", "acquirer_a", f"{DAY}.csv")

    def epoch_style(rows):
        i = rows[0].index("created_at_utc")
        return [rows[0]] + [r[:i] + ["1786639901458"] + r[i + 1:] for r in rows[1:]]
    rewrite(path, epoch_style)
    report = by_feed(tmp)["acquirer_a"]
    assert report["status"] == "fail" and "values" in checks_failed(report)


def test_a_few_bad_rows_are_left_to_the_quarantine():
    tmp = copy_sample()
    path = os.path.join(tmp, "raw", "acquirer_a", f"{DAY}.csv")

    def five_bad_amounts(rows):
        i = rows[0].index("amount")
        for r in rows[1:6]:
            r[i] = "n/a"
        return rows
    rewrite(path, five_bad_amounts)
    assert by_feed(tmp)["acquirer_a"]["status"] == "pass"


def test_missing_file_stops_the_run():
    tmp = copy_sample()
    os.remove(os.path.join(tmp, "raw", "acquirer_c", f"{DAY}.csv"))
    assert checks_failed(by_feed(tmp)["acquirer_c"]) == {"freshness"}


def test_truncated_acquirer_file_does_not_reconcile():
    tmp = copy_sample()
    path = os.path.join(tmp, "raw", "acquirer_b", f"{DAY}.csv")
    rewrite(path, lambda rows: rows[: len(rows) // 2])
    assert "reconciliation" in checks_failed(by_feed(tmp)["acquirer_b"])


def test_truncated_orchestrator_file_fails_the_volume_check():
    tmp = copy_sample()
    src = os.path.join(tmp, "raw", "orchestrator", f"{DAY}.csv")
    for d in range(6, 13):  # seven earlier days with a normal volume
        shutil.copy(src, os.path.join(tmp, "raw", "orchestrator", f"2026-08-{d:02d}.csv"))
    rewrite(src, lambda rows: rows[: len(rows) // 4])
    assert "volume" in checks_failed(by_feed(tmp)["orchestrator"])


def test_orchestrator_file_delivered_twice_stops_the_run():
    tmp = copy_sample()
    path = os.path.join(tmp, "raw", "orchestrator", f"{DAY}.csv")
    rewrite(path, lambda rows: rows + rows[1:])
    assert "unique" in checks_failed(by_feed(tmp)["orchestrator"])


def test_missing_orchestrator_file_is_caught_when_acquirers_sent_theirs():
    tmp = copy_sample()
    for feed in ("acquirer_a", "acquirer_b", "acquirer_c"):     # the acquirers already sent the next day
        shutil.copy(os.path.join(tmp, "raw", feed, f"{DAY}.csv"), os.path.join(tmp, "raw", feed, "2026-08-14.csv"))
    reports = {r["feed"]: r for r in run(tmp, None, contracts=CONTRACTS)}
    assert reports["orchestrator"]["date"] == "2026-08-14"
    assert checks_failed(reports["orchestrator"]) == {"freshness"}


def test_one_full_card_number_stops_the_run():
    tmp = copy_sample()
    path = os.path.join(tmp, "raw", "orchestrator", f"{DAY}.csv")

    def leak(rows):
        i = rows[0].index("merchant_id")
        rows[1][i] = "4111 1111 1111 1111"   # a published test card number
        return rows
    rewrite(path, leak)
    assert "card_number" in checks_failed(by_feed(tmp)["orchestrator"])


def test_card_number_detection():
    assert looks_like_pan("4111111111111111")
    assert looks_like_pan("5555-5555-5555-4444")
    assert not looks_like_pan("4111111111111112")        # fails the Luhn check
    assert not looks_like_pan("1786639901458")           # an epoch timestamp; about 1 in 10 would pass Luhn,
    #                                                       which is why timestamp columns are not scanned
    assert not looks_like_pan("B1375847777")
    assert not looks_like_pan("002031")                  # a synthetic BIN


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    for name, fn in tests:
        fn()
        print(f"ok  {name}")
    print(f"{len(tests)} checks passed")
