# Databricks notebook source
# MAGIC %md
# MAGIC # 10 · Update the Genie agent and the dashboard from the repository
# MAGIC Applies the serialized Genie space (`databricks/genie/approval_copilot_agent.json`: instructions, example SQL,
# MAGIC functions, tables, sample questions and benchmarks) to the existing agent, and optionally the dashboard file
# MAGIC (`databricks/dashboard/approval_health.lvdash.json`) to the existing dashboard, then republishes it.
# MAGIC The deploy workflow runs it after the bundle deploy; it can also run on its own.
# MAGIC
# MAGIC Changing what the agent sees requires a new evaluation (docs/decisions.md, ADR-09).

# COMMAND ----------

import json

from databricks.sdk import WorkspaceClient

dbutils.widgets.text("space_json_path", "")  # noqa: F821
dbutils.widgets.text("space_title", "Approval Rate Copilot")  # noqa: F821
dbutils.widgets.text("dashboard_json_path", "")  # noqa: F821
dbutils.widgets.text("dashboard_name", "Approval Health")  # noqa: F821
dbutils.widgets.text("warehouse_id", "")  # noqa: F821
args = {k: dbutils.widgets.get(k).strip() for k in  # noqa: F821
        ("space_json_path", "space_title", "dashboard_json_path", "dashboard_name", "warehouse_id")}
api = WorkspaceClient().api_client

# COMMAND ----------

def checks(space):
    """Every example query and benchmark answer runs, every function and table exists: nothing broken reaches the
    agent. Returns the failures."""
    failed = []
    ins = space["instructions"]
    runs = [("example: " + "".join(e["question"])[:60], "".join(e["sql"])) for e in ins["example_question_sqls"]]
    runs += [("benchmark: " + "".join(q["question"])[:60], a["content"][0])
             for q in space["benchmarks"]["questions"] for a in q["answer"] if a.get("content")]
    runs += [(f["identifier"], f"DESCRIBE FUNCTION {f['identifier']}") for f in ins["sql_functions"]]
    runs += [(t["identifier"], f"DESCRIBE TABLE {t['identifier']}") for t in space["data_sources"]["tables"]]
    for name, sql in runs:
        try:
            spark.sql(sql).limit(1).collect()  # noqa: F821
        except Exception as e:
            failed.append((name, str(e).strip().splitlines()[0][:200]))
    print(f"{len(runs) - len(failed)} of {len(runs)} checks passed")
    return failed


if args["space_json_path"]:
    serialized = open(args["space_json_path"], encoding="utf-8").read()
    space = json.loads(serialized)
    problems = checks(space)
    if problems:
        raise RuntimeError(f"{len(problems)} checks failed, so the agent was not updated: {problems}")
    spaces = api.do("GET", "/api/2.0/genie/spaces").get("spaces", [])
    match = [s for s in spaces if s.get("title") == args["space_title"]]
    if len(match) != 1:
        raise RuntimeError(f"expected one Genie space titled {args['space_title']!r}, found {len(match)}")
    space_id = match[0]["space_id"]
    body = {"serialized_space": serialized}
    if args["warehouse_id"]:
        body["warehouse_id"] = args["warehouse_id"]
    api.do("PATCH", f"/api/2.0/genie/spaces/{space_id}", body=body)
    stored = json.loads(api.do("GET", f"/api/2.0/genie/spaces/{space_id}",
                               query={"include_serialized_space": "true"})["serialized_space"])
    if stored.get("instructions") != space.get("instructions") or stored.get("data_sources") != space.get("data_sources"):
        print("note: the stored space differs from the file in instructions or data sources (Genie may normalize them)")
    instructions = space["instructions"]["text_instructions"][0]["content"]
    print(f"Genie space {space_id} updated: {len(space['data_sources']['tables'])} tables, "
          f"{len(space['instructions']['sql_functions'])} functions, {len(instructions)} instruction lines, "
          f"{len(space['instructions']['example_question_sqls'])} example queries, "
          f"{len(space['benchmarks']['questions'])} benchmark questions")

# COMMAND ----------

if args["dashboard_json_path"]:
    dashboard = open(args["dashboard_json_path"], encoding="utf-8").read()
    found = [d for d in api.do("GET", "/api/2.0/lakeview/dashboards", query={"page_size": 100}).get("dashboards", [])
             if d.get("display_name") == args["dashboard_name"]]
    if len(found) != 1:
        raise RuntimeError(f"expected one dashboard named {args['dashboard_name']!r}, found {len(found)}")
    dashboard_id = found[0]["dashboard_id"]
    api.do("PATCH", f"/api/2.0/lakeview/dashboards/{dashboard_id}", body={"serialized_dashboard": dashboard})
    publish = {"embed_credentials": True}
    if args["warehouse_id"]:
        publish["warehouse_id"] = args["warehouse_id"]
    api.do("POST", f"/api/2.0/lakeview/dashboards/{dashboard_id}/published", body=publish)
    print(f"Dashboard {dashboard_id} updated and republished: {len(json.loads(dashboard)['datasets'])} datasets")
