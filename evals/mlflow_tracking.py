"""
Logs the copilot's earlier evaluation runs to MLflow, so every run, its scores and each question's answer sit in
one experiment and can be compared side by side. From Genie run 5 on, the evaluation job (notebook 11, built from
evals/genie_eval.py) logs each run itself, with a full trace per answer; this script imports the result files of
the runs before it and skips any result file that records an MLflow run.

  pip install "mlflow>=3"
  python evals/mlflow_tracking.py                   # local store mlflow.db; browse with `mlflow ui --backend-store-uri sqlite:///mlflow.db`
  python evals/mlflow_tracking.py --experiment /Users/<you>/approval-copilot-evals    # in a Databricks notebook

One MLflow run per imported evaluation run (offline runs 1 to 6, Genie runs 1 to 4 and the first held-out set) with:
- params: the engine (offline model or the deployed Genie agent), the question set and the run number;
- metrics: questions passed, pass rate, and the pass rate per mode and per question group; SQL statements that
  ran without errors (offline runs); median and 95th-percentile latency for runs that recorded it;
- the context the model saw (instructions, tables, functions) as an artifact, with its SHA-256 as a tag;
- one trace per question: the question as input, the answer text and its SQL as outputs, and the verdict,
  group and grading note as attributes.

Runs already in the experiment are skipped (matched on the run_key tag), so the script can run after every
evaluation. Traces of runs imported from result files carry the time they were logged, not the time of the
run; the run's date is a tag.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import statistics

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
RESULTS = os.path.join(ROOT, "evals", "results")


def read(path: str):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def categories() -> dict:
    cats = {q["id"]: q["category"] for q in read(os.path.join(ROOT, "evals", "eval_set.json"))}
    for name in sorted(os.listdir(os.path.join(ROOT, "evals"))):
        if name.startswith("heldout") and name.endswith(".json"):
            cats.update({q["id"]: q["category"] for q in read(os.path.join(ROOT, "evals", name))})
    return cats


def offline_runs(summary: dict, cats: dict) -> list[dict]:
    runs = []
    for n in summary["runs"]:
        res = read(os.path.join(RESULTS, f"run{n}.json"))
        questions = []
        for q in res["questions"]:
            sql = [x["sql"] if isinstance(x, dict) else x for x in q.get("queries", [])]
            questions.append(dict(id=q["id"], question=q["question"], category=cats.get(q["id"]), mode="offline",
                                  answer=q.get("answer", ""), sql=sql, verdict=q["verdict"],
                                  note=q.get("override_note") or "; ".join(q.get("material_errors") or [])))
        runs.append(dict(key=f"offline-{n}", name=f"Offline run {n}", engine="offline model (Claude) as the copilot",
                         question_set="benchmark (20)", run_no=n, date=None,
                         label=summary["run_labels"].get(str(n), ""),
                         context=os.path.join(RESULTS, f"run{n}_model_context.md"),
                         sql_ok=summary.get("sql_by_run", {}).get(str(n)), questions=questions))
    return runs


def genie_runs(cats: dict) -> list[dict]:
    runs = []
    for name in sorted(os.listdir(RESULTS)):
        if not (name.startswith(("genie_run", "heldout")) and name.endswith(".json")):
            continue
        res = read(os.path.join(RESULTS, name))
        if res.get("mlflow"):  # logged by the evaluation job itself, with its traces
            continue
        heldout = name.startswith("heldout")
        n = int("".join(ch for ch in name.rsplit("_run", 1)[1] if ch.isdigit()))
        questions = []
        for q in res["questions"]:
            text = q.get("genie_text") or []
            questions.append(dict(id=q["id"], question=q["question"], category=q.get("category") or cats.get(q["id"]),
                                  mode=q.get("mode"), answer="\n\n".join(text) if isinstance(text, list) else text,
                                  sql=q.get("genie_sql") or [], verdict=q["verdict"],
                                  note=q.get("override_note") or q.get("note") or "",
                                  latency_s=q.get("latency_s")))
        context = os.path.join(RESULTS, f"genie_run{n}_model_context.md")
        runs.append(dict(key=f"{'heldout' if heldout else 'genie'}-{n}",
                         name=f"{'Held-out set' if heldout else 'Genie run'} {n}",
                         engine="deployed Databricks Genie agent", run_no=n,
                         question_set=f"held-out ({len(questions)})" if heldout else f"benchmark ({len(questions)})",
                         date=res.get("deployment") or res.get("run"), label=res.get("run", ""),
                         context=context if os.path.exists(context) else None, sql_ok=None, questions=questions))
    return runs


def metrics_of(run: dict) -> dict:
    qs = run["questions"]
    passed = sum(q["verdict"] == "PASS" for q in qs)
    m = {"questions": len(qs), "passed": passed, "pass_rate": round(passed / len(qs), 4)}
    for key in ("mode", "category"):
        for value in sorted({q[key] for q in qs if q.get(key)}):
            group = [q for q in qs if q.get(key) == value]
            m[f"pass_rate_{key}_{value}"] = round(sum(q["verdict"] == "PASS" for q in group) / len(group), 4)
    if run.get("sql_ok"):
        ok, total = (int(x) for x in str(run["sql_ok"]).split("/"))
        m["sql_ran_without_errors"] = round(ok / total, 4)
    latencies = sorted(q["latency_s"] for q in qs if q.get("latency_s") is not None)
    if latencies:
        m["latency_median_s"] = round(statistics.median(latencies), 2)
        m["latency_p95_s"] = round(latencies[min(len(latencies) - 1, int(0.95 * len(latencies)))], 2)
    return m


def log_run(mlflow, run: dict) -> None:
    tags = {"run_key": run["key"], "engine": run["engine"], "label": run["label"] or ""}
    if run["date"]:
        tags["run_date"] = str(run["date"])
    if run["context"]:
        with open(run["context"], "rb") as f:
            tags["context_sha256"] = hashlib.sha256(f.read()).hexdigest()
    with mlflow.start_run(run_name=run["name"], tags=tags):
        mlflow.log_params({"engine": run["engine"], "question_set": run["question_set"], "run_no": run["run_no"]})
        mlflow.log_metrics(metrics_of(run))
        if run["context"]:
            mlflow.log_artifact(run["context"], artifact_path="context")
        for q in run["questions"]:
            with mlflow.start_span(name=q["id"], span_type="AGENT") as span:
                span.set_inputs({"question": q["question"]})
                span.set_outputs({"answer": q["answer"], "sql": q["sql"]})
                span.set_attributes({"verdict": q["verdict"], "category": q.get("category") or "",
                                     "mode": q.get("mode") or "", "note": q.get("note") or "",
                                     "run_key": run["key"]})
                if q.get("latency_s") is not None:
                    span.set_attribute("latency_s", q["latency_s"])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--experiment", default="approval-copilot-evals")
    ap.add_argument("--tracking-uri", default=None, help="default: MLFLOW_TRACKING_URI, else sqlite:///mlflow.db")
    args = ap.parse_args()
    import mlflow

    on_databricks = bool(os.environ.get("DATABRICKS_RUNTIME_VERSION"))
    mlflow.set_tracking_uri(args.tracking_uri or os.environ.get("MLFLOW_TRACKING_URI")
                            or ("databricks" if on_databricks else "sqlite:///mlflow.db"))
    exp = mlflow.set_experiment(args.experiment)
    done = {r.data.tags.get("run_key") for r in mlflow.search_runs([exp.experiment_id], output_format="list")}
    summary = read(os.path.join(RESULTS, "summary.json"))
    cats = categories()
    logged = 0
    for run in offline_runs(summary, cats) + genie_runs(cats):
        if run["key"] in done:
            continue
        log_run(mlflow, run)
        logged += 1
        print(f"logged {run['name']}: {metrics_of(run)['passed']}/{len(run['questions'])}")
    print(f"{logged} runs logged to experiment {args.experiment}, {len(done)} were already there")


if __name__ == "__main__":
    main()
