"""
Builds the two payloads that create the dashboard and the Genie agent through the Databricks APIs:

  databricks/dashboard/approval_health.lvdash.json   AI/BI dashboard "Approval Health" (serialized_dashboard)
  databricks/genie/approval_copilot_agent.json       Genie agent "Approval Rate Copilot" (serialized_space, v2)

Both are generated from the same sources as the manual setup, so the API path and the UI path build the
same thing:
  - dataset SQL from databricks/notebooks/03_dashboard_queries.sql, widgets from docs/dashboard_setup.md;
  - instructions, example SQL, sample questions, tables and functions from docs/genie_space_setup.md;
  - benchmarks from evals/eval_set.json, split by mode as in docs/genie_space_setup.md, section 5.

  python tools/build_deploy_payloads.py
"""
from __future__ import annotations

import hashlib
import json
import os
import re

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
CATALOG_SCHEMA = "workspace.approval_copilot"

CHAT_MODE = ["A1", "A2", "A3", "A4", "A5", "A6", "B3", "C1", "C2", "C4"]
AGENT_MODE = ["B1", "B2", "B4", "B5", "B6", "C3", "D1", "D2", "D3", "D4"]


def read(path):
    with open(os.path.join(ROOT, path), encoding="utf-8") as f:
        return f.read()


def hexid(*parts):
    """Stable 32-character lowercase hex id, so rebuilding the payload does not change the ids."""
    return hashlib.md5("|".join(parts).encode()).hexdigest()


# ---------------------------------------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------------------------------------

def dashboard_datasets():
    text = read("databricks/notebooks/03_dashboard_queries.sql")
    datasets = {}
    for cell in text.split("-- COMMAND ----------")[1:]:
        lines = [ln for ln in cell.strip().splitlines()]
        name = re.match(r"-- (ds_[a-z_]+):", lines[0]).group(1)
        sql = [ln for ln in lines if not ln.lstrip().startswith("--") and ln.strip()]
        sql[-1] = sql[-1].rstrip().rstrip(";")
        datasets[name] = [ln + "\n" for ln in sql]
    return datasets


PCT1 = {"type": "number-percent", "decimalPlaces": {"type": "exact", "places": 1}}


def measure(name, expression, description):
    return {"displayName": name, "description": description, "expression": expression}


def period_ratio(num, den, period, scale=""):
    return (f"{scale}SUM(CASE WHEN `period` = '{period}' THEN `{num}` END) / "
            f"SUM(CASE WHEN `period` = '{period}' THEN `{den}` END)")


KPI_MEASURES = []
COUNTERS = []  # (widget name, title, measure base name, format)
for base, title, num, den, scale, fmt in [
    ("final_approval", "Final approval", "final_approved", "payments_sent", "", PCT1),
    ("first_attempt_approval", "First-attempt approval", "first_attempt_approved", "payments_sent", "", PCT1),
    ("approved_volume_rate", "Approved volume rate", "approved_usd", "attempted_usd", "", PCT1),
    ("retry_recovery_rate", "Retry recovery rate", "recovered_by_cascade", "first_attempt_failures", "", PCT1),
    ("effective_fee_bps", "Effective fee (bps)", "fees_usd", "approved_usd", "10000 * ",
     {"type": "number-plain", "decimalPlaces": {"type": "exact", "places": 1}}),
]:
    for period, suffix in [("last_7_days", "l7"), ("previous_7_days", "p7")]:
        KPI_MEASURES.append(measure(f"{base}_{suffix}", period_ratio(num, den, period, scale),
                                    f"{title}, {period.replace('_', ' ')} (domestic)"))
    COUNTERS.append((f"kpi-{base.replace('_', '-')}", title, base, fmt))

MEASURES = {
    "ds_kpis": KPI_MEASURES,
    "ds_hourly_by_acquirer": [measure(
        "hourly_final_approval", "SUM(`final_approval_rate` * `payments_sent`) / SUM(`payments_sent`)",
        "Final approval weighted by payments sent")],
    "ds_cascade_daily": [
        measure("cascade_rate", "SUM(`cascaded`) / SUM(`failures`)",
                "Share of failed first attempts retried on a second acquirer"),
        measure("retry_recovery_rate", "SUM(`recovered`) / SUM(`failures`)",
                "Share of failed first attempts that a retry turned into an approval")],
    "ds_funnel": [
        measure("conversion", "SUM(`approved`) / SUM(`intents`)", "Approved / payment intents"),
        measure("risk_block_rate", "SUM(`blocked_by_risk`) / SUM(`intents`)", "Blocked by risk / payment intents")],
}


def field(name, expression=None):
    return {"name": name, "expression": expression or f"`{name}`"}


def mfield(name):
    return {"name": f"measure({name})", "expression": f"MEASURE(`{name}`)"}


def widget(name, dataset, fields, spec, position, disaggregated=False):
    return {"widget": {"name": name,
                       "queries": [{"name": "main_query",
                                    "query": {"datasetName": dataset, "fields": fields,
                                              "disaggregated": disaggregated}}],
                       "spec": spec},
            "position": dict(zip(("x", "y", "width", "height"), position))}


def frame(title, description=None):
    out = {"showTitle": True, "title": title}
    if description:
        out.update(description=description, showDescription=True)
    return out


def counter(name, title, base, fmt, position):
    return widget(name, "ds_kpis", [mfield(f"{base}_l7"), mfield(f"{base}_p7")], {
        "version": 2, "widgetType": "counter",
        "encodings": {"value": {"fieldName": f"measure({base}_l7)", "displayName": "Last 7 days", "format": fmt},
                      "target": {"fieldName": f"measure({base}_p7)", "displayName": "Previous 7 days"}},
        "frame": frame(title, "Last 7 days vs the previous 7, domestic")}, position)


def chart(kind, name, dataset, title, x, y, color=None, position=(0, 0, 12, 6), disaggregated=False,
          y_format=PCT1, y_min=None, description=None, mark=None):
    """x, y, color: (field dict, displayName, scale type)."""
    fields = [x[0], y[0]] + ([color[0]] if color else [])
    y_scale = {"type": "quantitative"}
    if y_min is not None:
        y_scale["domain"] = {"min": y_min}
    enc = {"x": {"fieldName": x[0]["name"], "displayName": x[1], "scale": {"type": x[2]}},
           "y": {"fieldName": y[0]["name"], "displayName": y[1], "scale": y_scale}}
    if y_format:
        enc["y"]["format"] = y_format
    if color:
        enc["color"] = {"fieldName": color[0]["name"], "displayName": color[1], "scale": {"type": "categorical"}}
    spec = {"version": 3, "widgetType": kind, "encodings": enc, "frame": frame(title, description)}
    if mark:
        spec["mark"] = mark
    return widget(name, dataset, fields, spec, position, disaggregated)


def table(name, dataset, title, columns, position):
    return widget(name, dataset, [field(c) for c, _ in columns], {
        "version": 2, "widgetType": "table",
        "encodings": {"columns": [{"fieldName": c, "displayName": d} for c, d in columns]},
        "frame": frame(title)}, position, disaggregated=True)


def filter_widget(name, kind, title, column, datasets, position):
    queries = [{"name": f"{name}_{ds}", "query": {"datasetName": ds, "fields": [field(column)],
                                                   "disaggregated": False}} for ds in datasets]
    return {"widget": {"name": name, "queries": queries,
                       "spec": {"version": 2, "widgetType": kind,
                                "encodings": {"fields": [{"fieldName": column, "displayName": title,
                                                          "queryName": q["name"]} for q in queries]},
                                "frame": frame(title)}},
            "position": dict(zip(("x", "y", "width", "height"), position))}


def build_dashboard():
    sql = dashboard_datasets()
    datasets = []
    for name, lines in sql.items():
        ds = {"name": name, "displayName": name, "queryLines": lines}
        if name in MEASURES:
            ds["columns"] = MEASURES[name]
        datasets.append(ds)

    widths = [3, 3, 2, 2, 2]
    overview, x = [], 0
    for (name, title, base, fmt), w in zip(COUNTERS, widths):
        overview.append(counter(name, title, base, fmt, (x, 0, w, 3)))
        x += w
    overview += [
        chart("line", "trend-final-approval-by-segment", "ds_daily_by_segment", "Final approval by segment",
              (field("business_date"), "Date", "temporal"),
              (field("final_approval_rate"), "Final approval", "quantitative"),
              (field("segment"), "Segment", "categorical"), (0, 3, 12, 6), disaggregated=True, y_min=0.7,
              description="Domestic traffic"),
        table("table-abnormal-days", "ds_anomalies", "Abnormal days",
              [("business_date", "Date"), ("country", "Country"), ("card_type", "Card type"),
               ("final_approval_rate", "Final approval"), ("baseline_rate", "Baseline"),
               ("delta_pp", "Delta (pp)"), ("z_binomial", "z (binomial)")], (0, 9, 6, 6)),
        table("table-retry-alerts", "ds_cascade_alerts", "Retry alerts",
              [("business_date", "Date"), ("country", "Country"), ("acquirer_id", "First acquirer"),
               ("first_reason_code", "First reason"), ("cascade_rate", "Retry rate"),
               ("baseline_cascade_rate", "Baseline"), ("mapping_versions", "Map versions")], (6, 9, 6, 6)),
        table("table-production-changes", "ds_change_log", "Production changes",
              [("change_id", "Change"), ("changed_at_utc", "Changed at (UTC)"), ("change_type", "Type"),
               ("scope", "Scope"), ("description", "Description")], (0, 15, 12, 6)),
    ]

    diagnose = [
        chart("line", "hourly-approval-by-acquirer", "ds_hourly_by_acquirer", "Hourly approval by acquirer",
              (field("hour_local_ts"), "Local hour", "temporal"),
              (mfield("hourly_final_approval"), "Final approval", "quantitative"),
              (field("acquirer_id"), "First acquirer", "categorical"), (0, 0, 12, 6),
              description="Tip: set the dates to 12-15 Aug and the country to CO"),
        chart("bar", "failure-reasons-by-acquirer", "ds_decline_reasons", "Failure reasons by acquirer",
              (field("acquirer_id"), "Acquirer", "categorical"),
              (field("sum(failed_attempts)", "SUM(`failed_attempts`)"), "Failed first attempts", "quantitative"),
              (field("reason"), "Reason", "categorical"), (0, 6, 6, 6), y_format=None),
        chart("bar", "retryable-vs-not", "ds_decline_reasons", "Retryable vs not",
              (field("decline_type"), "Decline type", "categorical"),
              (field("sum(failed_attempts)", "SUM(`failed_attempts`)"), "Failed first attempts", "quantitative"),
              (field("retryable"), "Retryable", "categorical"), (6, 6, 6, 6), y_format=None),
        chart("line", "cascade-rate-weekly", "ds_cascade_daily", "Cascade rate",
              (field("weekly(business_date)", 'DATE_TRUNC("WEEK", `business_date`)'), "Week", "temporal"),
              (mfield("cascade_rate"), "Cascade rate", "quantitative"),
              (field("acquirer_id"), "First acquirer", "categorical"), (0, 12, 12, 5)),
        chart("line", "retry-recovery-weekly", "ds_cascade_daily", "Retry recovery rate",
              (field("weekly(business_date)", 'DATE_TRUNC("WEEK", `business_date`)'), "Week", "temporal"),
              (mfield("retry_recovery_rate"), "Retry recovery rate", "quantitative"),
              (field("acquirer_id"), "First acquirer", "categorical"), (0, 17, 12, 5)),
    ]

    funnel = [
        chart("line", "conversion-by-country", "ds_funnel", "Conversion",
              (field("business_date"), "Date", "temporal"), (mfield("conversion"), "Conversion", "quantitative"),
              (field("country"), "Country", "categorical"), (0, 0, 12, 5)),
        chart("line", "risk-blocks-by-country", "ds_funnel", "Risk blocks",
              (field("business_date"), "Date", "temporal"),
              (mfield("risk_block_rate"), "Blocked by risk", "quantitative"),
              (field("country"), "Country", "categorical"), (0, 5, 12, 5)),
        {"widget": {"name": "fee-vs-approval",
                    "queries": [{"name": "main_query", "query": {
                        "datasetName": "ds_cost_vs_approval",
                        "fields": [field(c) for c in ("effective_fee_bps", "final_approval_rate", "payments_sent",
                                                      "acquirer_id", "country", "card_type")],
                        "disaggregated": True}}],
                    "spec": {"version": 3, "widgetType": "scatter",
                             "encodings": {
                                 "x": {"fieldName": "effective_fee_bps", "displayName": "Effective fee (bps)",
                                       "scale": {"type": "quantitative"}},
                                 "y": {"fieldName": "final_approval_rate", "displayName": "Final approval",
                                       "scale": {"type": "quantitative"}, "format": PCT1},
                                 "size": {"fieldName": "payments_sent", "displayName": "Payments sent",
                                          "scale": {"type": "quantitative"}},
                                 "color": {"fieldName": "acquirer_id", "displayName": "First acquirer",
                                           "scale": {"type": "categorical"}}},
                             "frame": frame("Fee vs approval", "One point per country, card type and first "
                                                               "acquirer, whole period")}},
         "position": {"x": 0, "y": 10, "width": 6, "height": 6}},
        table("table-rejected-rows", "ds_data_quality", "Rejected rows",
              [("source_system", "Source"), ("failed_rule", "Rule"), ("raw_status", "Raw status"),
               ("raw_code", "Raw code"), ("rejected_rows", "Rows"), ("first_seen_utc", "First seen (UTC)"),
               ("last_seen_utc", "Last seen (UTC)")], (6, 10, 6, 6)),
    ]

    with_country = ["ds_kpis", "ds_daily_by_segment", "ds_hourly_by_acquirer", "ds_decline_reasons",
                    "ds_cascade_daily", "ds_funnel", "ds_cost_vs_approval", "ds_anomalies", "ds_cascade_alerts"]
    with_card_type = [d for d in with_country if d != "ds_cascade_alerts"]
    with_date = ["ds_daily_by_segment", "ds_hourly_by_acquirer", "ds_decline_reasons", "ds_cascade_daily",
                 "ds_funnel", "ds_anomalies", "ds_cascade_alerts"]
    filters = [
        filter_widget("filter-country", "filter-multi-select", "Country", "country", with_country, (0, 0, 4, 2)),
        filter_widget("filter-card-type", "filter-multi-select", "Card type", "card_type", with_card_type,
                      (4, 0, 4, 2)),
        filter_widget("filter-dates", "filter-date-range-picker", "Dates", "business_date", with_date,
                      (8, 0, 4, 2)),
    ]

    def page(name, title, layout, kind="PAGE_TYPE_CANVAS"):
        return {"name": name, "displayName": title, "pageType": kind, "layoutVersion": "GRID_V1",
                "layout": layout}

    return {"datasets": datasets,
            "pages": [page("overview", "Overview", overview),
                      page("diagnose", "Diagnose", diagnose),
                      page("funnel-cost-dq", "Funnel, cost and data quality", funnel),
                      page("filters", "Filters", filters, "PAGE_TYPE_GLOBAL_FILTERS")]}


# ---------------------------------------------------------------------------------------------------------
# Genie agent
# ---------------------------------------------------------------------------------------------------------

def section(text, number):
    return text.split(f"\n## {number}. ")[1].split("\n## ")[0]


def build_genie():
    doc = read("docs/genie_space_setup.md")

    s1 = section(doc, 1)
    tables = sorted(f"{CATALOG_SCHEMA}.{t}" for t in re.findall(r"^\| `([a-z_]+)` \|", s1, re.M))
    functions = re.findall(r"`(fn_[a-z_]+)`", s1.split("Trusted functions")[1].split("\n\n")[0])

    instructions = section(doc, 2).split("```")[1].strip("\n").splitlines()

    questions = re.findall(r"^- (.+)$", section(doc, 3), re.M)

    examples, current = [], None
    for line in section(doc, 4).split("```sql")[1].split("```")[0].strip().splitlines():
        if line.startswith("-- "):
            current = {"question": line[3:].strip(), "sql": []}
            examples.append(current)
        elif line.strip():
            current["sql"].append(line)

    evals = {q["id"]: q for q in json.load(open(os.path.join(ROOT, "evals", "eval_set.json")))}
    benchmarks = []
    for qid in CHAT_MODE:
        q = evals[qid]
        assert len(q["gold_sql"]) == 1, qid
        benchmarks.append({"id": hexid("benchmark", qid), "question": [q["question"]],
                           "answer": [{"format": "SQL", "content": [q["gold_sql"][0]]}]})
    for qid in AGENT_MODE:
        q, s = evals[qid], evals[qid]["scoring"]
        if s["type"] == "guardrail":
            note = [s["expected_behavior"]]
        else:
            note = ([f"Required: {s['facts'][k]}" for k in s["required"]]
                    + [f"Also credited: {v}" for k, v in s["facts"].items() if k not in s["required"]])
        benchmarks.append({"id": hexid("benchmark", qid), "question": [q["question"]],
                           "answer": [{"format": "SQL", "content": []}], "evaluation_note": note})

    by_id = lambda items: sorted(items, key=lambda i: i["id"])
    space = {
        "version": 2,
        "config": {"sample_questions": by_id(
            [{"id": hexid("sample", q), "question": [q]} for q in questions])},
        "data_sources": {"tables": [{"identifier": t} for t in tables]},
        "instructions": {
            "text_instructions": [{"id": hexid("instructions"), "content": [ln + "\n" for ln in instructions]}],
            "example_question_sqls": by_id(
                [{"id": hexid("example", e["question"]), "question": [e["question"]],
                  "sql": [ln + "\n" for ln in e["sql"]]} for e in examples]),
            "sql_functions": sorted(
                [{"id": hexid("function", f), "identifier": f"{CATALOG_SCHEMA}.{f}"} for f in functions],
                key=lambda i: (i["id"], i["identifier"])),
        },
        "benchmarks": {"questions": by_id(benchmarks)},
    }
    return space, {"tables": len(tables), "functions": functions, "instruction_lines": len(instructions),
                   "sample_questions": len(questions), "examples": len(examples), "benchmarks": len(benchmarks)}


def write(path, obj):
    full = os.path.join(ROOT, path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)
        f.write("\n")
    return full


def main():
    dash = build_dashboard()
    write("databricks/dashboard/approval_health.lvdash.json", dash)
    widgets = sum(len(p["layout"]) for p in dash["pages"])
    print(f"dashboard: {len(dash['datasets'])} datasets, {len(dash['pages'])} pages, {widgets} widgets")
    space, stats = build_genie()
    write("databricks/genie/approval_copilot_agent.json", space)
    print("genie agent:", stats)


if __name__ == "__main__":
    main()
