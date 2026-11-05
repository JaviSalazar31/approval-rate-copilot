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

Check in the **Pipeline graph** tab: 27 datasets (4 streaming tables and 23 materialized views). In the
**Tables** view, `silver_auth_attempts` shows 776 rows dropped by expectations; they appear, with the rule
they failed, in `silver_dq_quarantine`.

If the pipeline cannot run in your workspace, use the fallback: open `02_build_tables_without_pipeline` and
Run all. It creates the same tables as plain Delta tables (see "Choose one build path" in step 0).

## 5. Create the copilot functions and column descriptions

1. Open `04_copilot_functions`, Run all. The last cell is a smoke test: one row with 1,004 window payments
   and 110 lost approvals.
2. Open `05_column_comments`, Run all. It writes a description on every column the copilot uses. Run it
   again after any full refresh of the pipeline; it is idempotent.

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
GRANT EXECUTE ON FUNCTION workspace.approval_copilot.fn_drop_impact TO `analysts`;  -- and the other four functions
```

SQL functions run with their owner's privileges
([authorized user](https://docs.databricks.com/aws/en/sql/language-manual/sql-ref-authorized-user)), so
analysts need no grant on the reference tables the functions read.

## 8. Run the benchmarks

Follow section 5 of [genie_space_setup.md](genie_space_setup.md). Two runs, one per mode, each with
**Run selected**:

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
| `volume_alerts` | Notebook 07: the forecast alert, written to `gold_volume_alerts_daily` | 2 |
| `copilot_functions` | Notebook 04 | 2 |
| `column_comments` | Notebook 05 | 2 |
| `governance` | Notebook 08: layer and domain tags, tags on the card columns, a check that the mask hides the last four digits, and the lineage Unity Catalog recorded | 2 |

The feed checks never retry, because a broken contract does not fix itself. A failed task sends an email to the
owner, and the tasks after it do not run. The daily schedule (07:00 Bogotá) is defined but paused: on Free
Edition the job runs when you start it, which keeps it inside the compute quota.

Create it with the Databricks CLI; the `${...}` placeholders are filled from your environment:

```bash
export NOTEBOOK_DIR=/Workspace/Users/<you>/approval-rate-copilot/databricks/notebooks
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
