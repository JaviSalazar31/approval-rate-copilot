"""
Executes the SQL written by the model under test against the local pipeline output.

Input  (JSON): {"<question id>": ["SELECT ...", "SELECT ..."], ...}
Output (JSON): {"<question id>": [{"sql", "ok", "error", "columns", "rows", "truncated"}], ...}
       and a Markdown rendering per question, which is what the model sees back.

  python evals/harness/run_queries.py queries.json results.json

Function calls (fn_*) are expanded by local/sql_runner.py exactly as Unity Catalog would run them.
"""
from __future__ import annotations

import json
import math
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "local"))

MAX_ROWS = 50


def clean(v):
    if v is None:
        return None
    if isinstance(v, float):
        return None if math.isnan(v) else v
    if hasattr(v, "isoformat"):
        return v.isoformat(sep=" ") if hasattr(v, "hour") else v.isoformat()
    if hasattr(v, "item"):
        return v.item()
    return v


def render(results):
    """Markdown the model reads: one block per query."""
    out = []
    for i, r in enumerate(results, start=1):
        out.append(f"Query {i}:\n```sql\n{r['sql']}\n```")
        if not r["ok"]:
            out.append(f"ERROR: {r['error']}")
            continue
        if not r["rows"]:
            out.append("(no rows)")
            continue
        out.append("| " + " | ".join(r["columns"]) + " |")
        out.append("|" + "---|" * len(r["columns"]))
        for row in r["rows"]:
            out.append("| " + " | ".join("" if v is None else str(v) for v in row) + " |")
        if r["truncated"]:
            out.append(f"(only the first {MAX_ROWS} rows are shown)")
    return "\n".join(out)


def main(src, dst):
    from run_local import load_outputs
    from sql_runner import run_sql

    spark = load_outputs()
    queries = json.load(open(src))
    results = {}
    for qid, sqls in queries.items():
        results[qid] = []
        for sql in sqls:
            try:
                pdf = run_sql(spark, sql, limit=MAX_ROWS + 1)
                rows = [[clean(v) for v in row] for row in pdf.itertuples(index=False, name=None)]
                results[qid].append(dict(sql=sql, ok=True, error=None, columns=list(pdf.columns),
                                         rows=rows[:MAX_ROWS], truncated=len(rows) > MAX_ROWS))
            except Exception as e:  # report the error to the model, as Genie would
                msg = str(e).strip().splitlines()[0][:400]
                results[qid].append(dict(sql=sql, ok=False, error=msg, columns=[], rows=[], truncated=False))
    json.dump(results, open(dst, "w"), indent=1, default=str)
    md_dir = os.path.splitext(dst)[0] + "_md"
    os.makedirs(md_dir, exist_ok=True)
    for qid, res in results.items():
        open(os.path.join(md_dir, f"{qid}.md"), "w").write(render(res))
    ok = sum(r["ok"] for res in results.values() for r in res)
    total = sum(len(res) for res in results.values())
    print(f"{ok}/{total} queries ran without error -> {dst}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
