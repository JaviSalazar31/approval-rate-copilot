"""
Guardrail checks on the copilot's answers (evals/guardrails.py). No Spark needed.

    python -m pytest tests/test_guardrails.py
    python tests/test_guardrails.py
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "evals"))
from guardrails import card_numbers, check, masked_cards, writes  # noqa: E402

NORMAL_ANSWER = ("Final approval of Colombian domestic debit fell from 86.5% to 75.6% between 13 August 14:00 and "
                 "14 August 12:10 (2026-08-13 to 2026-08-14): 1,004 payments, 110 approvals and USD 6,354 lost. "
                 "Payment pi_000123456 was declined with code 05.")


def test_a_normal_answer_passes():
    assert check(NORMAL_ANSWER, ["SELECT country, sum(payments_sent) FROM gold_approval_daily GROUP BY country"]) == {}


def test_full_card_numbers_are_caught():
    assert card_numbers("the card was 4111 1111 1111 1111") == ["4111 1111 1111 1111"]
    assert card_numbers("5555-5555-5555-4444 was declined") == ["5555-5555-5555-4444"]
    assert card_numbers("4111111111111112") == []            # fails the Luhn check
    assert card_numbers("661,301 payments and USD 39,055,555.01") == []


def test_cards_listed_by_last_four_are_caught():
    assert masked_cards("cards **** 4821 and XXXX-1234") == ["**** 4821", "XXXX-1234"]
    assert masked_cards("the card ending in 4821") == ["ending in 4821"]
    assert masked_cards("last four digits: 4821") == ["last four digits: 4821"]
    assert masked_cards("The tables hold no BINs or last four digits.") == []
    assert masked_cards("the data ends in 2026-08-29, so there is no data ending in 2026-08-30") == []


def test_sql_that_changes_data_is_caught():
    assert writes(["DELETE FROM silver_payments WHERE business_date = '2026-08-13'"])
    assert writes(["SELECT 1; DROP TABLE gold_approval_daily"])
    assert writes(["-- the incident window\nWITH w AS (SELECT 1 AS x) SELECT x FROM w"]) == []
    assert writes(["WITH w AS (SELECT 1 AS x) INSERT INTO gold_dq_summary SELECT * FROM w"])
    assert writes(["/* clean up */ DELETE FROM gold_dq_summary"])
    assert writes([]) == []


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    for name, fn in tests:
        fn()
        print(f"ok  {name}")
    print(f"{len(tests)} checks passed")
