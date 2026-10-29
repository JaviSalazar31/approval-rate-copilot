"""
Runs Databricks-style SQL locally: strips the catalog/schema prefix and expands calls to the
copilot's Unity Catalog table functions (fn_*) into subqueries built from the same SQL bodies.
"""
from __future__ import annotations

import os
import re
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "tools"))
from build_databricks import FUNCTIONS, TIMESTAMP_TEXT  # noqa: E402

SPEC = {f["name"]: f["params"] for f in FUNCTIONS}
CAST_BY_TYPE = {"TIMESTAMP": "TIMESTAMP", "INT": "INT", "STRING": "STRING"}


def _split_args(s):
    args, depth, cur, quote = [], 0, "", None
    for ch in s:
        if quote:
            cur += ch
            if ch == quote:
                quote = None
            continue
        if ch in ("'", '"'):
            quote = ch
            cur += ch
        elif ch == "(":
            depth += 1
            cur += ch
        elif ch == ")":
            depth -= 1
            cur += ch
        elif ch == "," and depth == 0:
            args.append(cur.strip())
            cur = ""
        else:
            cur += ch
    if cur.strip():
        args.append(cur.strip())
    return args


def _render_function(name, raw_args):
    params = SPEC[name]
    values = {}
    positional = []
    for a in _split_args(raw_args):
        m = re.match(r"^(p_[a-z_]+)\s*=>\s*(.+)$", a, re.S)
        if m:
            values[m.group(1)] = m.group(2).strip()
        else:
            positional.append(a)
    for (pname, *_), v in zip(params, positional):
        values.setdefault(pname, v)
    rendered = {}
    for pname, ptype, default, _ in params:
        expr = values.get(pname, default)
        if expr is None:
            raise ValueError(f"{name}: missing argument {pname}")
        rendered[pname] = f"CAST({expr} AS {CAST_BY_TYPE[ptype]})"
        if pname in TIMESTAMP_TEXT:  # declared STRING, converted inside the function as in Unity Catalog
            rendered[pname] = f"CAST({rendered[pname]} AS TIMESTAMP)"
    body = open(os.path.join(ROOT, "sql", "copilot", f"{name}.sql")).read().replace("{schema}", "")
    body = re.sub(r"--[^\n]*", "", body)
    return re.sub(r"\$\{(p_[a-z_]+)\}", lambda m: rendered[m.group(1)], body)


def expand(sql: str) -> str:
    sql = re.sub(r"\b(?:workspace\.)?approval_copilot\.", "", sql)
    for name in SPEC:
        while True:
            m = re.search(rf"\b{name}\s*\(", sql)
            if not m:
                break
            start = m.end()
            depth, i = 1, start
            while depth and i < len(sql):
                if sql[i] == "(":
                    depth += 1
                elif sql[i] == ")":
                    depth -= 1
                i += 1
            raw = sql[start:i - 1]
            sql = sql[:m.start()] + "(" + _render_function(name, raw) + ")" + sql[i:]
    return sql


def run_sql(spark, sql: str, limit: int = 50):
    df = spark.sql(expand(sql.strip().rstrip(";")))
    return df.limit(limit).toPandas()
