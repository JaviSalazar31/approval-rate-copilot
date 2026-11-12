"""
Runs a question set against the deployed Genie agent and logs every answer to MLflow as a trace: the question, the
answer, the SQL Genie ran with its row count, the latency and the guardrail checks (evals/guardrails.py). The judge
then grades each answer with the question's scoring rule, and the verdicts are attached to the same traces.

On Databricks this is notebook 11, the task of the evaluation job (weekly schedule, paused; or run on demand):

  action        ask: run the questions | log_verdicts: attach a graded file's verdicts to the run's traces
  question_set  benchmark | heldout_2 | heldout_3 | heldout_4 | heldout_5 | heldout_6 | heldout_7
  run_name      e.g. "Genie run 5"; the answers go to <volume>/evals/runs/<run_name>.json

The protocol follows evals/README.md:
- benchmark: the ten chat-mode questions run through Genie's benchmark runner, which grades them by comparing
  result sets (one sample each; it reports no latency); the ten agent-mode questions run through the agent API,
  B1 and C3 three times each.
- heldout_2 to heldout_7: every question three times, each in a new conversation: KPI and comparison questions in
  chat mode (Conversation API), diagnoses and guardrails in agent mode.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import statistics
import time

# Which benchmark questions run in which mode (the same split as the Genie space's benchmarks and runs 2 to 4)
BENCHMARK_CHAT = ["A1", "A2", "A3", "A4", "A5", "A6", "B3", "C1", "C2", "C4"]
BENCHMARK_AGENT = ["B1", "B2", "B4", "B5", "B6", "C3", "D1", "D2", "D3", "D4"]
REPEATED = {"B1": 3, "C3": 3}          # benchmark questions with three agent-mode samples since run 2
HELDOUT_SAMPLES = 3
TERMINAL = ("COMPLETED", "FAILED", "CANCELLED", "QUERY_RESULT_EXPIRED")


def plan(question_set: str, questions: list[dict]) -> list[dict]:
    """The calls a run makes: one entry per (question, sample) with its mode."""
    by_id = {q["id"]: q for q in questions}
    if question_set == "benchmark":
        calls = [dict(id=i, mode="chat_benchmark", sample=1) for i in BENCHMARK_CHAT]
        calls += [dict(id=i, mode="agent", sample=s) for i in BENCHMARK_AGENT for s in range(1, REPEATED.get(i, 1) + 1)]
    else:
        calls = [dict(id=q["id"], mode=q["mode"], sample=s) for q in questions for s in range(1, HELDOUT_SAMPLES + 1)]
    for c in calls:
        c["question"] = by_id[c["id"]]["question"]
    return calls


def result_table(resp) -> dict:
    r = (resp or {}).get("sql_execution_result") or (resp or {}).get("statement_response") or resp or {}
    cols = [c.get("name") for c in (r.get("manifest", {}).get("schema", {}).get("columns") or [])]
    rows = (r.get("result") or {}).get("data_array") or []
    return {"columns": cols, "rows": rows[:50], "row_count": len(rows)}


def ask_chat(api, space: str, text: str, poll_seconds: float = 3, max_polls: int = 120) -> dict:
    """One question in a new chat-mode conversation (Conversation API)."""
    rec = {"answer": "", "sql": [], "results": []}
    r = api.do("POST", f"/api/2.0/genie/spaces/{space}/start-conversation", body={"content": text})
    conv = r.get("conversation_id") or r["conversation"]["id"]
    msg = r.get("message_id") or r["message"]["id"]
    m = {}
    for _ in range(max_polls):
        m = api.do("GET", f"/api/2.0/genie/spaces/{space}/conversations/{conv}/messages/{msg}")
        if m.get("status") in TERMINAL:
            break
        time.sleep(poll_seconds)
    rec["status"] = m.get("status")
    for a in m.get("attachments") or []:
        if a.get("text"):
            rec["answer"] += (a["text"].get("content") or "") + "\n"
        if a.get("query"):
            rec["sql"].append(a["query"].get("query"))
            if a["query"].get("description"):
                rec["answer"] += a["query"]["description"] + "\n"
            aid = a.get("attachment_id") or a.get("id")
            try:
                qr = api.do("GET", f"/api/2.0/genie/spaces/{space}/conversations/{conv}/messages/{msg}"
                                   f"/attachments/{aid}/query-result")
                rec["results"].append(result_table(qr))
            except Exception as e:  # the answer still counts; the missing result is recorded
                rec["results"].append({"error": repr(e)[:300]})
    rec["answer"] = rec["answer"].strip()
    return rec


def ask_agent(api, space: str, text: str) -> dict:
    """One question in agent mode (Agent mode API, beta): the final answer, and each SQL call with its output."""
    rec = {"answer": "", "sql": [], "results": []}
    resp = api.do("POST", f"/api/2.0/genie/agents/{space}/responses",
                  headers={"Accept": "text/event-stream", "Content-Type": "application/json"},
                  body={"input": [{"type": "message", "role": "user", "content": [{"type": "input_text", "text": text}]}]},
                  raw=True)
    raw = resp["contents"].read().decode("utf-8", "replace")
    final, failed = None, None
    for line in raw.splitlines():
        if not line.startswith("data:"):
            continue
        try:
            ev = json.loads(line[5:].strip())
        except ValueError:
            continue
        if ev.get("type") == "response.completed":
            final = ev.get("response", {})
        elif ev.get("type") == "response.failed":
            failed = ev.get("response", {}).get("error") or ev
    if failed is not None:
        return {**rec, "status": "FAILED", "error": json.dumps(failed)[:1500]}
    if final is None:
        return {**rec, "status": "NO_FINAL_EVENT", "error": raw[-1500:]}
    rec["status"] = str(final.get("status", "completed")).upper()
    for o in final.get("output", []):
        if o.get("type") == "message":
            rec["answer"] += "".join(c.get("text", "") for c in o.get("content", []))
        elif o.get("type") == "function_call":
            try:
                rec["sql"].append(json.loads(o.get("arguments") or "{}").get("sql"))
            except ValueError:
                rec["sql"].append(o.get("arguments"))
        elif o.get("type") == "function_call_output":
            rec["results"].append({"output": str(o.get("output"))[:3000]})
    return rec


def run_benchmark_chat(api, space: str, calls: list[dict], poll_seconds: float = 6, max_polls: int = 200) -> list[dict]:
    """The chat-mode benchmark questions through Genie's benchmark runner, with its verdict for each."""
    serialized = json.loads(api.do("GET", f"/api/2.0/genie/spaces/{space}",
                                   query={"include_serialized_space": "true"})["serialized_space"])
    by_text = {"".join(b["question"]): b["id"] for b in serialized.get("benchmarks", {}).get("questions", [])}
    ids = {c["id"]: by_text.get(c["question"]) for c in calls}
    run = api.do("POST", f"/api/2.0/genie/spaces/{space}/eval-runs",
                 body={"benchmark_question_ids": [v for v in ids.values() if v]})
    run_id = run.get("eval_run_id") or run.get("id")
    for _ in range(max_polls):
        status = api.do("GET", f"/api/2.0/genie/spaces/{space}/eval-runs/{run_id}")
        if status.get("num_questions") and status.get("num_done") == status.get("num_questions"):
            break
        if str(status.get("eval_run_status", "")).upper() in ("DONE", "FAILED", "CANCELLED", "EVALUATION_FAILED"):
            break
        time.sleep(poll_seconds)
    text_to_id = {c["question"]: c["id"] for c in calls}
    out = {}
    listing = api.do("GET", f"/api/2.0/genie/spaces/{space}/eval-runs/{run_id}/results", query={"page_size": 50})
    for res in listing.get("eval_results", []):
        rid = res.get("result_id") or res.get("id")
        d = api.do("GET", f"/api/2.0/genie/spaces/{space}/eval-runs/{run_id}/results/{rid}") if rid else {}
        actual = [x for x in (d.get("actual_response") or []) if x.get("response_type") == "SQL"]
        out[text_to_id.get(res.get("question"))] = {
            "status": "COMPLETED", "answer": "\n".join(x.get("response") or "" for x in (d.get("actual_response") or [])
                                                     if x.get("response_type") == "TEXT"),
            "sql": [x.get("response") for x in actual], "results": [],
            "genie_assessment": d.get("assessment"), "genie_reasons": d.get("assessment_reasons"),
            "eval_run_id": run_id}
    return [{**c, **out.get(c["id"], {"status": "MISSING"})} for c in calls]


def context_hash(api, space: str) -> str:
    """SHA-256 of the agent's instructions, examples, functions and tables: ties a run to the exact context."""
    s = json.loads(api.do("GET", f"/api/2.0/genie/spaces/{space}",
                          query={"include_serialized_space": "true"})["serialized_space"])
    keep = {"data_sources": s.get("data_sources"), "instructions": s.get("instructions")}
    return hashlib.sha256(json.dumps(keep, sort_keys=True).encode()).hexdigest()


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    values = sorted(values)
    k = (len(values) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(values) - 1)
    return round(values[lo] + (values[hi] - values[lo]) * (k - lo), 1)


def run_eval(api, space: str, question_set: str, questions: list[dict], run_name: str, experiment: str,
             guardrail_check, mlflow, workers: int = 3) -> dict:
    """Asks every planned question, logs one MLflow trace per answer inside one run, and returns the records."""
    from concurrent.futures import ThreadPoolExecutor
    calls = plan(question_set, questions)
    started = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    bench = run_benchmark_chat(api, space, [c for c in calls if c["mode"] == "chat_benchmark"]) \
        if any(c["mode"] == "chat_benchmark" for c in calls) else []

    def ask(c):  # the span is logged afterwards, in the run's thread: its latency is the attribute latency_s
        t0 = time.time()
        try:
            rec = ask_agent(api, space, c["question"]) if c["mode"] == "agent" else ask_chat(api, space, c["question"])
        except Exception as e:
            rec = {"status": "EXCEPTION", "error": repr(e)[:1500], "answer": "", "sql": [], "results": []}
        return {**c, **rec, "latency_s": round(time.time() - t0, 1)}

    with ThreadPoolExecutor(max_workers=workers) as pool:
        asked = list(pool.map(ask, [c for c in calls if c["mode"] != "chat_benchmark"]))
    records = bench + asked
    mlflow.set_experiment(experiment)
    with mlflow.start_run(run_name=run_name) as run:
        mlflow.log_params({"space_id": space, "question_set": question_set, "calls": len(records),
                           "context_sha256": context_hash(api, space)})
        mlflow.set_tags({"started_utc": started, "protocol": "evals/README.md"})
        for r in records:
            r["guardrail_failures"] = guardrail_check(r.get("answer", ""), [s for s in r.get("sql") or [] if s])
            with mlflow.start_span(name=f"{r['id']} sample {r['sample']}", span_type="AGENT") as span:
                span.set_inputs({"question": r["question"], "mode": r["mode"]})
                results = r.get("results") or []
                # each statement keeps the result at its own position, so a tool call without SQL shifts nothing
                pairs = [(sql, results[j] if j < len(results) else {}) for j, sql in enumerate(r.get("sql") or []) if sql]
                for i, (sql, res) in enumerate(pairs):
                    with mlflow.start_span(name=f"sql {i + 1}", span_type="TOOL") as tool:
                        tool.set_inputs({"sql": sql})
                        res = res if isinstance(res, dict) else {"output": str(res)[:200]}
                        tool.set_outputs({"row_count": res.get("row_count"), "error": res.get("error")})
                span.set_outputs({"answer": r.get("answer", ""), "status": r.get("status")})
                span.set_attributes({"question_id": r["id"], "sample": r["sample"], "mode": r["mode"],
                                     "latency_s": r.get("latency_s"), "genie_assessment": r.get("genie_assessment"),
                                     "guardrail_failures": json.dumps(r["guardrail_failures"])})
                r["trace_id"] = span.trace_id
        timed = [r for r in records if r.get("latency_s") is not None]
        metrics = {"answers": len(records),
                   "failed_calls": sum(1 for r in records if r.get("status") not in ("COMPLETED",)),
                   "guardrail_failures": sum(1 for r in records if r["guardrail_failures"])}
        for mode in sorted({r["mode"] for r in timed}):
            lat = [r["latency_s"] for r in timed if r["mode"] == mode and r.get("status") == "COMPLETED"]
            if lat:
                metrics[f"latency_p50_s_{mode}"] = percentile(lat, 0.5)
                metrics[f"latency_p95_s_{mode}"] = percentile(lat, 0.95)
        mlflow.log_metrics(metrics)
        result = {"run_name": run_name, "question_set": question_set, "space_id": space, "started_utc": started,
                  "mlflow_run_id": run.info.run_id, "metrics": metrics, "records": records}
        mlflow.log_dict(result, "answers.json")
    return result


def verdict_value(v) -> bool:
    """A graded record's verdict as a boolean: true or false, or the strings PASS and FAIL; anything else is an error."""
    if v is True or v == "PASS":
        return True
    if v is False or v == "FAIL":
        return False
    raise ValueError(f"a verdict must be true, false, PASS or FAIL, not {v!r}")


def assessment_value(a):
    value = getattr(a, "value", None)
    if value is None and getattr(a, "feedback", None) is not None:
        value = a.feedback.value
    return value


def current_verdict(mlflow, trace_id: str):
    """The verdict in force on a trace, or None. An overridden verdict is no longer valid and does not count."""
    found = [a for a in (mlflow.get_trace(trace_id).info.assessments or [])
             if a.name == "verdict" and getattr(a, "valid", True) is not False]
    return found[-1] if found else None


def log_verdicts(graded: dict, mlflow, assessment_source) -> dict:
    """Attaches the verdicts to the traces of a run and logs the pass counts on the run. `graded` is the answers
    file with "verdict" (true or false), "note", "grader" and "grader_type" (LLM_JUDGE for the judge, CODE for Genie's
    benchmark grader, HUMAN for a manual review or the author's stricter override) filled in per record;
    `assessment_source` is mlflow.entities.AssessmentSource. Grading a run again leaves unchanged verdicts alone and
    replaces one whose value or note changed with MLflow's override, which keeps the old one in the trace's history."""
    passed = graded_n = changed = 0
    for r in graded["records"]:
        if r.get("trace_id") is None or r.get("verdict") is None:
            continue
        ok = verdict_value(r["verdict"])
        graded_n += 1
        passed += ok
        old, note = current_verdict(mlflow, r["trace_id"]), r.get("note") or ""
        if old is not None and assessment_value(old) == ok and (getattr(old, "rationale", None) or "") == note:
            continue
        source = assessment_source(source_type=r.get("grader_type", "LLM_JUDGE"), source_id=r.get("grader", "judge"))
        if old is None:
            mlflow.log_feedback(trace_id=r["trace_id"], name="verdict", value=ok, rationale=note, source=source)
        else:
            mlflow.override_feedback(trace_id=r["trace_id"], assessment_id=old.assessment_id, value=ok,
                                     rationale=note, source=source)
        changed += 1
    with mlflow.start_run(run_id=graded["mlflow_run_id"]):
        mlflow.log_metrics({"answers_graded": graded_n, "answers_passed": passed,
                            "pass_rate": round(passed / graded_n, 4) if graded_n else 0.0})
    run = mlflow.get_run(graded["mlflow_run_id"])
    return {"graded": graded_n, "passed": passed, "changed": changed,
            "context_sha256": run.data.params.get("context_sha256"),
            "run_started_utc": datetime.datetime.fromtimestamp(run.info.start_time / 1000, datetime.timezone.utc)
            .isoformat(timespec="seconds")}
