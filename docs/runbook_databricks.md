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

**Option A, recommended: Git folder.** Workspace → Create → Git folder → paste the GitHub URL of this
repository → Create. All paths below are relative to that folder.

**Option B: import files.** Workspace → your user folder → Import → upload, one by one:
`databricks/notebooks/00_setup.sql`, `01_generate_data.py`, `02_build_tables_without_pipeline.sql`,
`03_dashboard_queries.sql`, `04_copilot_functions.sql`, `05_column_comments.py` and
`databricks/pipeline/approval_copilot_pipeline.sql`. Notebook 01 contains its own copy of the data
generator, so it works without the rest of the repository.

## 2. Create the schema and the landing volume

Open `00_setup`, attach **Serverless**, Run all. It creates schema `workspace.approval_copilot` and volume
`landing`.

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

1. **New → ETL pipeline** (also under **Jobs & Pipelines → New → ETL Pipeline**). New pipelines use
   Unity Catalog and serverless compute by default.
2. Name it `approval_copilot` at the top. Click the default catalog and schema next to the name and set
   them to `workspace` and `approval_copilot`.
3. Next to **Use sample code**, open the menu and choose **Add existing source code**; select
   `databricks/pipeline/approval_copilot_pipeline.sql` (or **Settings → Source code → Configure paths →
   Add path**).
4. Click **Run pipeline** (5–10 minutes).

Check in the **Pipeline graph** tab: 27 datasets (4 streaming tables and 23 materialized views). In the
**Tables** view, `silver_auth_attempts` shows 776 rows dropped by expectations; they appear, with the rule
they failed, in `silver_dq_quarantine`.

**Fallback:** if the pipeline cannot run in your workspace, open `02_build_tables_without_pipeline` and Run
all. It creates the same tables as plain Delta tables (see "Choose one build path" in step 0).

## 5. Create the copilot functions and column descriptions

1. Open `04_copilot_functions`, Run all. The last cell is a smoke test: one row with 1,004 window payments
   and 110 lost approvals.
2. Open `05_column_comments`, Run all. It writes a description on every column the copilot uses. Run it
   again after any full refresh of the pipeline; it is idempotent.

## 6. Build the dashboard

Follow [dashboard_setup.md](dashboard_setup.md) with the queries in `03_dashboard_queries`.

## 7. Create the Genie agent (the copilot)

Follow [genie_space_setup.md](genie_space_setup.md): **Genie Agents → New**, add the tables, paste the
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

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `read_files` finds no files | Notebook 01 did not finish, or the volume path differs | Re-run 01; check `/Volumes/workspace/approval_copilot/landing/raw/` |
| Notebook 01 fails on a pandas or numpy call | Old serverless environment | Environment side panel → version 4 or later |
| Pipeline error on a table | Workspace-specific limit or a runtime difference | Switch to notebook 02 as in step 0 (delete the pipeline, drop the schema) and keep the error message |
| A table "already exists" error | Both build paths were run into the same schema | Delete the pipeline, drop the schema and use one path (step 0) |
| Local dates look shifted by a few hours | The session time zone was changed from the UTC default | `SET TIME ZONE 'UTC'` (SQL) or remove the override from the pipeline settings |
| Function not found in Genie | Functions were created in another schema | Re-run 04 with catalog `workspace`, schema `approval_copilot` |
| "Quota exceeded" on Free Edition | Daily compute limit | Wait until the next day; nothing is lost |
| Column descriptions missing after a full refresh | The pipeline recreated the tables | Re-run 05 |
