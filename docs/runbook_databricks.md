# Runbook · Deploy on Databricks Free Edition

About an hour end to end, most of it waiting for compute. Everything runs on serverless compute in the
`workspace` catalog that Free Edition provides. Menu names follow the Databricks documentation as of
October 2026; if a label differs in your workspace, look for the closest equivalent.

## 0. Before you start

- A Databricks Free Edition account (sign up with your own email at databricks.com; it is free and
  non-commercial).
- Choose one build path for the tables: the pipeline (step 4) or notebook 02 (the fallback). Both write
  the same table names into `workspace.approval_copilot`, so do not run both into the same schema. To
  switch, delete the pipeline if you created one (it is listed under **Jobs & Pipelines**), drop
  the schema (`DROP SCHEMA workspace.approval_copilot CASCADE`; this also deletes the landing volume and
  its files) and start again at step 2.
- No time-zone setting is needed: serverless compute runs in UTC by default, which the pipeline assumes.

## 1. Get the code into the workspace

Option A (recommended) uses a Git folder: Workspace > Create > Git folder, then paste the GitHub URL of this
repository and click Create. All paths below are relative to that folder.

Option B imports the files one by one: in Workspace, open your user folder, choose Import and upload
`databricks/notebooks/00_setup.sql`, `01_generate_data.py`, `02_build_tables_without_pipeline.sql`,
`03_dashboard_queries.sql`, `04_copilot_functions.sql`, `05_column_comments.py` and
`databricks/pipeline/approval_copilot_pipeline.sql`. Notebook 01 contains its own copy of the data
generator, so it works without the rest of the repository.

## 2. Create the schema and the landing volume

Open `00_setup`, attach **Serverless**, Run all. It creates schema `workspace.approval_copilot`, volume
`landing` and `mask_card_last4`, the column mask the pipeline attaches to the card's last four digits (so it
has to exist before step 4).

## 3. Generate the data

Open `01_generate_data`. In the notebook's **Environment** side panel, choose environment version 4 or
later. Attach Serverless and Run all (a few minutes). Expected output:

```
numpy 2.1.3 | pandas 2.2.3        (or newer)
661,301 payment intents | 700,038 authorization attempts
raw/orchestrator      90 files
raw/acquirer_a        90 files
...
```

The generator was tested with the package versions of environment version 4 (numpy 2.1.3, pandas
2.2.3): it writes files byte-identical to the reference run, so every number in the docs reproduces.

## 4. Build the tables with the pipeline

1. **New > ETL pipeline** (also under **Jobs & Pipelines > New > ETL Pipeline**). New pipelines use
   Unity Catalog and serverless compute by default.
2. Name it `approval_copilot` at the top. Click the default catalog and schema next to the name and set
   them to `workspace` and `approval_copilot`.
3. Next to **Use sample code**, open the menu and choose **Add existing source code**; select
   `databricks/pipeline/approval_copilot_pipeline.sql` (or **Settings > Source code > Configure paths > Add path**).
4. Click **Run pipeline** (5–10 minutes).

Check in the **Pipeline graph** tab: 17 datasets (4 streaming tables and 13 materialized views), from bronze to
silver. In the **Tables** view, `silver_auth_attempts` shows 776 rows dropped by expectations; they appear, with the
rule they failed, in `silver_dq_quarantine`.

If the pipeline cannot run in your workspace, use the fallback: open `02_build_tables_without_pipeline` and
Run all. It creates the same tables, gold included, as plain Delta tables (see "Choose one build path" in step 0),
so skip step 4b.

## 4b. Build the gold tables with dbt

The dbt project in `dbt/` builds the ten gold tables and runs its 76 tests. In **Jobs & Pipelines > Create job**,
add a task of type **dbt**: source **Workspace**, project directory: the `dbt` folder in your Git folder, command
`dbt build`, SQL warehouse **Serverless Starter Warehouse**, catalog `workspace`, schema `approval_copilot`, and an
environment with the dependency `dbt-databricks>=1.10,<2`. Run it (about two minutes); the run log ends with
`PASS=93`. The job in step 10 includes this task.

## 5. Create the copilot functions and column descriptions

1. Open `04_copilot_functions`, Run all. The last cell is a smoke test: one row with 1,004 window payments
   and 110 lost approvals.
2. Open `05_column_comments`, Run all. It writes a description on every column the copilot uses. Run it
   again after each dbt run or full refresh of the pipeline; it is idempotent.
3. Open `09_metric_views`, Run all. It creates the metric views `approval_metrics`, `decline_metrics` and
   `funnel_metrics`, which the Genie agent reads; two dashboard datasets read the first two.

## 6. Build the dashboard

Follow [dashboard_setup.md](dashboard_setup.md) with the queries in `03_dashboard_queries`.

## 7. Create the Genie agent (the copilot)

Follow [genie_space_setup.md](genie_space_setup.md): **Genie Agents > New**, add the tables, paste the
general instructions, attach the five functions, add the example SQL and the sample questions.

Genie evaluates data access with each user's own Unity Catalog permissions. Free Edition has a single user,
so nothing more is needed there; in a shared workspace, give analysts read access only:

```sql
GRANT USE CATALOG ON CATALOG workspace TO `analysts`;
GRANT USE SCHEMA ON SCHEMA workspace.approval_copilot TO `analysts`;
GRANT SELECT ON TABLE workspace.approval_copilot.gold_approval_daily TO `analysts`;  -- and the other nine copilot tables
GRANT SELECT ON TABLE workspace.approval_copilot.approval_metrics TO `analysts`;     -- and the other two views
GRANT EXECUTE ON FUNCTION workspace.approval_copilot.fn_drop_impact TO `analysts`;  -- and the other four functions
```

SQL functions run with their owner's privileges
([authorized user](https://docs.databricks.com/aws/en/sql/language-manual/sql-ref-authorized-user)), so
analysts need no grant on the reference tables the functions read.

## 8. Run the benchmarks

The evaluation job `approval_copilot_eval` (notebook 11) asks the questions and logs each answer to MLflow
(section 11). Run it with `question_set` set to `benchmark` or one of `heldout_2` to `heldout_5` and a
`run_name`; the answers land in `/Volumes/workspace/approval_copilot/landing/evals/runs/<run_name>.json`. From the
repository, run **Actions > Evaluate the Genie agent** with the same inputs: it waits for the job, keeps the
answers file as a workflow artifact and prints it to the log, compressed, between a `BEGIN` and an `END` line;
`fetch` reads back the answers of an earlier run without asking the questions again. Genie's benchmark grader
grades the chat-mode benchmark questions inside the run. Grade the other answers with the judge guide and the
scoring rules ([evals/README.md](../evals/README.md), [evals/judge_guide.md](../evals/judge_guide.md)), set
`verdict` (true or false), `note`, `grader` and `grader_type` (`LLM_JUDGE` for the judge, `CODE` for Genie's
grader, `HUMAN` for a manual review or an overrule) on each record of the answers file, upload it to the
volume, and run the job again with `action` set to `log_verdicts` and `graded_path` pointing to the file: the
verdicts are attached to the same traces. With the workflow, commit the graded file under `evals/results/` (as
`verdicts_<run>.json`) and pass its path as `graded_file`; the workflow uploads it and runs the job. Running
it again with a corrected file overrides only the verdicts that changed, and MLflow keeps the old ones in each
trace's history.

The benchmarks also run by hand in the Genie UI: follow section 5 of [genie_space_setup.md](genie_space_setup.md),
with two runs, one per mode, each with **Run selected**:

- Chat mode: A1–A6, B3, C1, C2, C4, graded against their `gold_sql`.
- Agent mode: B1, B2, B4, B5, B6, C3 and D1–D4, graded against their scoring facts or expected behavior
  as the evaluation note; anything marked "Manual review needed" is graded by hand.

Record the results in the "Databricks Genie run" table of [evals/README.md](../evals/README.md).

## 9. Record the demo

Follow [demo_video_script.md](demo_video_script.md).

## 10. Run everything as one job

After the first deployment, one job runs every step in order (`databricks/jobs/approval_copilot_job.json`):

| Task | What it does | Retries |
|---|---|---|
| `setup` | Notebook 00: schema, volume and column mask, if missing | 2 |
| `land_data` | Notebook 01: generates the files, or keeps them when `regenerate_data` is `false` | 2 |
| `check_feeds` | Notebook 06: each feed against its contract in `contracts/`; a violation stops the job here | none |
| `refresh_pipeline` | The pipeline from step 4 | 2 |
| `dbt_build` | `dbt build` on the SQL warehouse: the gold tables and their tests (step 4b) | none |
| `volume_alerts` | Notebook 07: the forecast alert, written to `gold_volume_alerts_daily` | 2 |
| `copilot_functions` | Notebook 04 | 2 |
| `column_comments` | Notebook 05 | 2 |
| `metric_views` | Notebook 09 | 2 |
| `governance` | Notebook 08: layer and domain tags, tags on the card columns, a check that the mask hides the last four digits, and the lineage Unity Catalog recorded | 2 |

The feed checks and dbt never retry, because a broken contract or a failed test does not fix itself. A failed task sends an email to the
owner, and the tasks after it do not run. The daily schedule (07:00 Bogotá) is defined but paused: on Free
Edition the job runs when you start it, which keeps it inside the compute quota.

Create it with the Databricks CLI; the `${...}` placeholders are filled from your environment:

```bash
export NOTEBOOK_DIR=/Workspace/Users/<you>/approval-rate-copilot/databricks/notebooks
export DBT_PROJECT_DIR=/Workspace/Users/<you>/approval-rate-copilot/dbt WAREHOUSE_ID=<your SQL warehouse id>
export PIPELINE_ID=<the pipeline id from step 4> OWNER_EMAIL=<your email>
envsubst < databricks/jobs/approval_copilot_job.json > /tmp/approval_copilot_job.json
databricks jobs create --json @/tmp/approval_copilot_job.json
```

Job parameters: `run_date` (empty checks the newest files) and `regenerate_data` (`false`). After a change to
the generator, run it once with `regenerate_data` set to `true` and a full refresh of the pipeline, because the
streaming tables do not re-read files that keep their names:

```bash
databricks jobs run-now --json '{"job_id": <JOB_ID>, "job_parameters": {"regenerate_data": "true"}, "pipeline_params": {"full_refresh": true}}'
```

To let someone see the last four digits, add them to an account-level group named `payments_support` (the mask
calls `is_account_group_member`, which does not see workspace-local groups); everyone else reads `****`.
Lineage is also visible per table in Catalog Explorer, under **Lineage**.

## 11. Deploy from GitHub Actions with the Asset Bundle

`databricks.yml` describes the two pipelines (the copilot's and the late-data test), the four jobs (daily,
evaluation, agent update, late-data test) and the dashboard; it is generated from the same definitions as
`databricks/jobs/`. The workflow `.github/workflows/deploy.yml` validates it
on every push to main that touches it, and deploys when you run it by hand.

1. In Databricks, create a personal access token (**Settings > Developer > Access tokens > Generate new token**).
2. In the GitHub repository, add two secrets under **Settings > Secrets and variables > Actions**:
   `DATABRICKS_HOST` (the workspace URL) and `DATABRICKS_TOKEN` (the token).
3. In **Actions > Deploy to Databricks > Run workflow**, tick **bind_existing** the first time: it binds the
   pipeline, jobs and dashboard you already created, found by name, so the deploy updates them instead of creating
   copies. Tick it only once; a resource that is already bound cannot be bound again. The deploy refuses any change
   that would delete or recreate a resource, so a plan like that stops the run instead of replacing the dashboard
   or a table. The bundle keeps the dashboard in the folder it was created in (`parent_path`), because moving it
   would give it a new ID and URL. After the first deploy the jobs show as managed by the bundle and are locked in
   the UI; change them in the repository.
4. Tick **run_daily_job** when a release changes the functions, the column descriptions or the metric views: the
   daily job recreates them from the deployed files before anything else reads them.
5. Tick **update_agent** only for a release that changes what the agent sees, and evaluate it right after
   (section 8) before publishing it: it applies `databricks/genie/approval_copilot_agent.json` to the Genie agent,
   once its example queries, benchmark answers, functions and tables pass a check.

The evaluation job runs notebook 11 on a question set and logs every answer to the MLflow experiment
`approval-copilot-evals` in your user folder, as a trace with its latency, its SQL and the guardrail checks; the
answers also go to `/Volumes/workspace/approval_copilot/landing/evals/runs/`. Its weekly schedule on the benchmark
is defined but paused.

## 12. Use the functions from another assistant

[`mcp_server/`](../mcp_server/README.md) offers the five functions as read-only tools over the Model Context
Protocol, with a configuration example for Claude Desktop. It needs a SQL warehouse ID and the same kind of
credentials as the CLI. **Actions > MCP server on Databricks** starts it with the repository's secrets and calls
its tools on the deployed functions.

## 13. Test late data with AUTO CDC

The job `approval_copilot_late_cdc` checks how a live status feed would be applied ([ADR-22](decisions.md)).
It writes only to the schema `workspace.approval_copilot_cdc`: notebook 12 lands the first batch of status events
and a checksum of `gold_approval_daily`, the pipeline of the same name (`databricks/pipeline/late_cdc_test.sql`)
applies them with a full refresh, notebook 12 lands the late batch, the pipeline runs again, and notebook 13
checks the result. Run it from **Jobs & Pipelines**; it takes about three minutes and fails if a check fails. The
deploy creates the job and the pipeline, and nothing schedules them.

## Alternative: deploy through the APIs

The same deployment can run without the UI. That is how it was done on 29 October 2026: the Databricks console
does not support automated browser control, so the agent that deployed it worked through the Databricks APIs,
using the MCP server of the [AI Dev Kit](https://github.com/databricks-solutions/ai-dev-kit) on the author's
computer.

| Step | API route |
|---|---|
| 2 to 3. Schema, volume and data | Notebooks 00 and 01 as serverless runs; 01 needs environment version 4 |
| 4. Pipeline | Pipelines API, with `databricks/pipeline/approval_copilot_pipeline.sql` as a workspace file and catalog `workspace`, schema `approval_copilot` |
| 5. Functions and descriptions | Notebooks 04 and 05 as serverless runs |
| 10. Job | `databricks/jobs/approval_copilot_job.json` (Jobs API), as in step 10 |
| 6. Dashboard | `databricks/dashboard/approval_health.lvdash.json` (Lakeview API) |
| 7. Genie agent | `databricks/genie/approval_copilot_agent.json`: the serialized space (version 2) with the instructions, example SQL, functions, sample questions and the 20 benchmarks |
| 8. Benchmarks | Chat mode: the benchmark evaluation API of the Genie agent. Its runs grade in chat mode only, so the agent-mode questions went to the Agent mode API (beta) and were graded with the offline judge ([evals/README.md](../evals/README.md)) |

Both payloads are generated from the same sources as the manual setup (`python tools/build_deploy_payloads.py`),
so the two paths build the same dashboard and agent. These are the REST calls the deployment made, here with the
Databricks CLI and `jq` (the dataset queries use fully qualified names, so no default catalog is needed):

```bash
WAREHOUSE=<WAREHOUSE_ID>; FOLDER=/Workspace/Users/<you>/approval-rate-copilot
databricks api post /api/2.0/lakeview/dashboards --json "$(jq -n --arg w "$WAREHOUSE" --arg p "$FOLDER" \
  --rawfile d databricks/dashboard/approval_health.lvdash.json \
  '{display_name: "Approval Health", warehouse_id: $w, parent_path: $p, serialized_dashboard: $d}')"
databricks api post /api/2.0/lakeview/dashboards/<DASHBOARD_ID>/published \
  --json "{\"embed_credentials\": true, \"warehouse_id\": \"$WAREHOUSE\"}"
databricks api post /api/2.0/genie/spaces --json "$(jq -n --arg w "$WAREHOUSE" --arg p "$FOLDER" \
  --rawfile s databricks/genie/approval_copilot_agent.json \
  '{warehouse_id: $w, parent_path: $p, title: "Approval Rate Copilot", serialized_space: $s}')"
```

`<DASHBOARD_ID>` is the `dashboard_id` returned by the first call.

The files that notebook 01 wrote in that run were checked against the local reference run: the 367 files match
byte for byte. On 4 November 2026, after the card fields were added to the orchestrator feed, the job was created
the same way and run once with `regenerate_data` set to `true` and a full refresh of the pipeline. All eight
tasks succeeded in about ten minutes: the 367 files it wrote match the local run byte for byte, the 360
feed-days passed their contracts, the pipeline dropped the same 776 rows, and the forecast alert flagged the
same two days. A second run with the default parameters, which keeps the files and refreshes the pipeline
incrementally, finished in under five minutes and left the tables unchanged.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `read_files` finds no files | Notebook 01 did not finish, or the volume path differs | Re-run 01; check `/Volumes/workspace/approval_copilot/landing/raw/` |
| Notebook 01 fails on a pandas or numpy call | Old serverless environment | Choose version 4 or later in the Environment side panel |
| Pipeline error on a table | Workspace-specific limit or a runtime difference | Switch to notebook 02 as in step 0 (delete the pipeline, drop the schema) and keep the error message |
| A table "already exists" error | Both build paths were run into the same schema | Delete the pipeline, drop the schema and use one path (step 0) |
| Local dates look shifted by a few hours | The session time zone was changed from the UTC default | `SET TIME ZONE 'UTC'` (SQL) or remove the override from the pipeline settings |
| Function not found in Genie | Functions were created in another schema | Re-run 04 with catalog `workspace`, schema `approval_copilot` |
| "Quota exceeded" on Free Edition | Daily compute limit | Wait until the next day; nothing is lost |
| Column descriptions missing after a full refresh | The pipeline recreated the tables | Re-run 05 |
| The job stops at `check_feeds` | A feed broke its contract | The task output names the feed, the check and the detail. Fix the feed or, for a format change the acquirer announced, update the contract. Replace or remove the bad file before the next run: the check covers only the run date, and the pipeline reads every file in the landing volume |
| The pipeline fails on `mask_card_last4` | The mask function does not exist yet | Run `00_setup` (or the job's `setup` task) first |
