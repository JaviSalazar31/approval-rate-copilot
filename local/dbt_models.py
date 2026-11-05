"""
The dbt project in dbt/ builds the gold layer. This module reads its models for the tools that need plain SQL or
the dependency graph without running dbt:
  - tools/build_databricks.py   notebook 02, the fallback that builds every table without the pipeline or dbt
  - tools/build_docs.py         the data model page (which table feeds which)

The models use only ref() and source(), so each one renders to plain SQL by replacing those calls with table names.
local/run_local.py and the Databricks job run dbt itself.
"""
from __future__ import annotations

import os
import re

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DBT_DIR = os.path.join(ROOT, "dbt")
MODELS_DIR = os.path.join(DBT_DIR, "models")
LAYERS = ("staging", "intermediate", "marts")

REF = re.compile(r"\{\{\s*ref\(\s*'([a-z0-9_]+)'\s*\)\s*\}\}")
SOURCE = re.compile(r"\{\{\s*source\(\s*'[a-z0-9_]+'\s*,\s*'([a-z0-9_]+)'\s*\)\s*\}\}")


def models(layer: str) -> list[str]:
    folder = os.path.join(MODELS_DIR, layer)
    return sorted(f[:-4] for f in os.listdir(folder) if f.endswith(".sql"))


def layer_of(name: str) -> str:
    for layer in LAYERS:
        if name in models(layer):
            return layer
    raise KeyError(f"no dbt model named {name}")


def model_path(name: str) -> str:
    return os.path.join(MODELS_DIR, layer_of(name), f"{name}.sql")


def body(name: str) -> str:
    return open(model_path(name), encoding="utf-8").read()


def render(name: str) -> str:
    """The model's SQL with ref() and source() replaced by plain table names in the current schema."""
    text = REF.sub(lambda m: m.group(1), body(name))
    text = SOURCE.sub(lambda m: m.group(1), text)
    if "{{" in text or "{%" in text:
        raise ValueError(f"{name} uses Jinja beyond ref() and source(); extend local/dbt_models.py")
    return text.strip().rstrip(";")


def upstream(name: str) -> set[str]:
    """Pipeline tables and gold models this model reads, through any staging and intermediate models."""
    text = re.sub(r"--[^\n]*", "", body(name))
    found = set(SOURCE.findall(text))
    for ref in REF.findall(text):
        found |= upstream(ref) if layer_of(ref) in ("staging", "intermediate") else {ref}
    return found


def build_order() -> list[str]:
    """Staging and intermediate models in dependency order (they become views); marts follow in TABLES order."""
    order, pending = [], models("staging") + models("intermediate")
    while pending:
        ready = [n for n in pending if set(REF.findall(body(n))) <= set(order)]
        if not ready:
            raise ValueError(f"cannot order {pending}: they reference models outside staging and intermediate")
        order += ready
        pending = [n for n in pending if n not in ready]
    return order
