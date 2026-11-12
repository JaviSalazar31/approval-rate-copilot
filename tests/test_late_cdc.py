"""
The AUTO CDC late-data test (notebooks 12 and 13, databricks/pipeline/late_cdc_test.sql) runs only on Databricks.
These checks cover its design: the two batches hold the cases notebook 13 checks, and the current status that the
acquirer's sequence numbers give differs from the one the arrival order would give.
"""
from __future__ import annotations

import ast
import os

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
NOTEBOOK = os.path.join(ROOT, "databricks", "notebooks", "12_late_cdc_feed.py")
CHECK = os.path.join(ROOT, "databricks", "notebooks", "13_late_cdc_check.py")
PIPELINE = os.path.join(ROOT, "databricks", "pipeline", "late_cdc_test.sql")


def events():
    tree = ast.parse(open(NOTEBOOK, encoding="utf-8").read())
    for node in tree.body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "EVENTS":
            return ast.literal_eval(node.value)
    raise AssertionError("EVENTS not found")


def current(evts, key):
    state = {}
    for e in sorted(evts, key=key):
        pid, status, op = e[0], e[1], e[5]
        if op == "DELETE":
            state.pop(pid, None)
        else:
            state[pid] = status
    return state


def test_sequence_and_arrival_disagree_where_the_data_is_late():
    evts = events()
    by_sequence = current(evts, key=lambda e: e[4])                 # what AUTO CDC keeps
    by_arrival = current(evts, key=lambda e: (e[6], evts.index(e)))  # the last event to arrive wins
    assert by_sequence == {"PAY-CDC-01": "SETTLED", "PAY-CDC-02": "REVERSED", "PAY-CDC-03": "REVERSED",
                           "PAY-CDC-04": "SETTLED", "PAY-CDC-06": "SETTLED"}
    assert by_arrival["PAY-CDC-02"] == "SETTLED" and by_arrival["PAY-CDC-06"] == "APPROVED"


def test_every_case_is_in_the_feed():
    evts = events()
    late_older = [e for e in evts if e[6] == 2 and any(o[0] == e[0] and o[6] == 1 and o[4] > e[4] for o in evts)]
    assert [e[0] for e in late_older] == ["PAY-CDC-02"]
    assert any(e[5] == "DELETE" for e in evts) and {e[6] for e in evts} == {1, 2}
    assert len({(e[0], e[4]) for e in evts}) == len(evts), "one event per payment and sequence number"


def test_the_check_reads_the_scd_columns_through_an_alias():
    # pyspark's Row refuses attribute names that start with "__" (r.__START_AT raises), which failed the first run
    tree = ast.parse(open(CHECK, encoding="utf-8").read())
    assert not [n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute) and n.attr.startswith("__")]


def test_the_pipeline_stays_in_its_own_schema():
    sql = open(PIPELINE, encoding="utf-8").read()
    assert "AUTO CDC INTO payment_status_current" in sql and "STORED AS SCD TYPE 2" in sql
    assert "workspace.approval_copilot." not in sql and "approval_copilot_cdc.payment_status_feed" in sql
