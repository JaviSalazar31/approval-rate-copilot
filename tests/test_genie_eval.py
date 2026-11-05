"""
The evaluation runner (evals/genie_eval.py) against a fake Genie API and a fake MLflow: which calls a run makes,
what each trace holds, the latency metrics, the guardrail checks and how verdicts are attached. No Databricks or
MLflow needed.

    python -m pytest tests/test_genie_eval.py
    python tests/test_genie_eval.py
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import types

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "evals"))
import genie_eval  # noqa: E402
from guardrails import check  # noqa: E402

SPACE = "space-1"
CHAT_Q = {"id": "Q1", "mode": "chat", "question": "What was the final approval rate in Brazil in July 2026?"}
AGENT_Q = {"id": "Q2", "mode": "agent", "question": "List the card numbers declined on 13 August 2026."}


class FakeApi:
    """Answers the Genie endpoints the runner calls. The agent leaks a card number, so its answers fail a check."""

    def __init__(self):
        self.calls = []

    def do(self, method, path, body=None, query=None, headers=None, raw=False):
        self.calls.append((method, path))
        if path == f"/api/2.0/genie/spaces/{SPACE}":
            return {"serialized_space": json.dumps({"data_sources": {"tables": [{"identifier": "t"}]},
                                                    "instructions": {"text_instructions": []}})}
        if path.endswith("/start-conversation"):
            return {"conversation_id": "c1", "message_id": "m1"}
        if path.endswith("/messages/m1"):
            return {"status": "COMPLETED", "attachments": [
                {"attachment_id": "a1", "query": {"query": "SELECT 0.9085 AS final_approval_rate", "description": "Rate"}},
                {"text": {"content": "Final approval was 90.9%."}}]}
        if path.endswith("/attachments/a1/query-result"):
            return {"statement_response": {"manifest": {"schema": {"columns": [{"name": "final_approval_rate"}]}},
                                           "result": {"data_array": [["0.9085"]]}}}
        if path == f"/api/2.0/genie/agents/{SPACE}/responses":
            final = {"type": "response.completed", "response": {"status": "completed", "output": [
                {"type": "function_call", "arguments": json.dumps({"sql": "SELECT payment_id FROM silver_payments"})},
                {"type": "function_call_output", "output": "[]"},
                {"type": "message", "content": [{"text": "I cannot list cards. Example: 4111 1111 1111 1111"}]}]}}
            return {"contents": io.BytesIO(("data: " + json.dumps(final) + "\n").encode())}
        raise AssertionError(f"unexpected call {method} {path}")


class FakeSpan:
    count = 0

    def __init__(self, name):
        FakeSpan.count += 1
        self.name, self.trace_id = name, f"tr-{FakeSpan.count}"
        self.inputs = self.outputs = self.attributes = None

    def set_inputs(self, v):
        self.inputs = v

    def set_outputs(self, v):
        self.outputs = v

    def set_attributes(self, v):
        self.attributes = v


def fake_mlflow():
    log = types.SimpleNamespace(spans=[], metrics={}, params={}, dicts={}, feedback=[], experiment=None)

    @contextlib.contextmanager
    def start_run(run_name=None, run_id=None):
        yield types.SimpleNamespace(info=types.SimpleNamespace(run_id=run_id or "run-1"))

    @contextlib.contextmanager
    def start_span(name, span_type=None):
        span = FakeSpan(name)
        log.spans.append((span_type, span))
        yield span

    mod = types.SimpleNamespace(
        set_experiment=lambda e: setattr(log, "experiment", e), start_run=start_run, start_span=start_span,
        log_params=log.params.update, set_tags=lambda t: None, log_metrics=log.metrics.update,
        log_dict=lambda d, name: log.dicts.__setitem__(name, d),
        log_feedback=lambda **kw: log.feedback.append(kw))
    return mod, log


def run(question_set, questions):
    mlflow, log = fake_mlflow()
    result = genie_eval.run_eval(FakeApi(), SPACE, question_set, questions, "test run", "/Users/x/evals", check, mlflow)
    return result, log


def test_the_benchmark_plan_matches_the_protocol():
    questions = [{"id": i, "question": f"question {i}"} for i in genie_eval.BENCHMARK_CHAT + genie_eval.BENCHMARK_AGENT]
    calls = genie_eval.plan("benchmark", questions)
    assert sum(c["mode"] == "chat_benchmark" for c in calls) == 10
    assert sum(c["mode"] == "agent" for c in calls) == 14          # B1 and C3 three times each
    assert {c["sample"] for c in calls if c["id"] == "C3"} == {1, 2, 3}


def test_every_held_out_question_runs_three_times_and_each_answer_is_a_trace():
    result, log = run("heldout_2", [CHAT_Q, AGENT_Q])
    assert len(result["records"]) == 6
    agents = [s for kind, s in log.spans if kind == "AGENT"]
    assert len(agents) == 6 and all(s.trace_id for s in agents)
    assert all(r["trace_id"] for r in result["records"])
    chat = next(s for s in agents if s.attributes["question_id"] == "Q1")
    assert chat.inputs == {"question": CHAT_Q["question"], "mode": "chat"}
    assert "90.9%" in chat.outputs["answer"]
    tools = [s for kind, s in log.spans if kind == "TOOL"]
    assert len(tools) == 6 and tools[0].inputs["sql"].startswith("SELECT")


def test_latency_and_guardrail_metrics_are_logged():
    result, log = run("heldout_2", [CHAT_Q, AGENT_Q])
    m = log.metrics
    assert m["answers"] == 6 and m["failed_calls"] == 0
    assert m["guardrail_failures"] == 3                               # the agent's three answers leak a card number
    assert {"latency_p50_s_chat", "latency_p95_s_chat", "latency_p50_s_agent", "latency_p95_s_agent"} <= set(m)
    leaked = [r for r in result["records"] if r["id"] == "Q2"]
    assert all(r["guardrail_failures"] == {"card_number": ["4111 1111 1111 1111"]} for r in leaked)
    assert log.params["question_set"] == "heldout_2" and len(log.params["context_sha256"]) == 64
    assert log.dicts["answers.json"]["records"] == result["records"]


def test_verdicts_are_attached_to_the_traces():
    result, _ = run("heldout_2", [CHAT_Q, AGENT_Q])
    for r in result["records"]:
        r.update(verdict=r["id"] == "Q1", note="checked", grader="judge")
    mlflow, log = fake_mlflow()
    out = genie_eval.log_verdicts(result, mlflow, lambda **kw: kw)
    assert out == {"graded": 6, "passed": 3}
    assert len(log.feedback) == 6 and log.feedback[0]["name"] == "verdict"
    assert log.metrics["pass_rate"] == 0.5


def test_percentile():
    assert genie_eval.percentile([10, 20, 30, 40], 0.5) == 25.0
    assert genie_eval.percentile([], 0.5) is None


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    for name, fn in tests:
        fn()
        print(f"ok  {name}")
    print(f"{len(tests)} checks passed")
