# Evaluating the copilot

The copilot is only useful if its numbers are right and its explanations hold up. This folder holds the
question set, the context the model receives, the harness that runs its SQL, and the results of each run.

## The question set

`eval_set.json` has 20 questions in four groups, each with a scoring rule. The 16 KPI, diagnosis and
comparison questions have ground-truth SQL (`gold_sql`); the 4 guardrail questions have an expected
behavior instead.

| Group | Questions | What it tests | How it is scored |
|---|---|---|---|
| KPI lookups | A1–A6 | Right table, filters, definitions and time zone | Headline number within a tolerance |
| Diagnoses | B1–B6 | Finding the cause of a drop and sizing it | Required facts present, no material error |
| Comparisons | C1–C4 | Acquirers, issuers, early warnings | Right winner and gap within tolerance, or required facts |
| Guardrails | D1–D4 | Data the copilot does not have, guessing, forecasting, writing | Expected behavior |

The diagnosis and comparison questions are built on the scenario planted in the data (see the main README):
a routing change, a decline-code map bug, an issuer outage and a risk-rule change.

## Protocol

1. **Model under test.** A Claude model (Sonnet tier) receives `model_context.md`: the same general
   instructions, example SQL, table descriptions and function signatures configured in the Genie agent.
   It gets one question per conversation and no access to the data, the answer key or any other file.
2. **Harness.** The model replies with up to six SQL queries. `harness/run_queries.py` runs them on the
   local Spark copy of the pipeline output (the five functions run with the same SQL bodies as in Unity
   Catalog, inlined with typed arguments) and returns up to 50 rows per query. The model can ask once for up to four more queries, then writes the
   answer the user would see.
3. **Tool audit.** Every tool call of every run was logged and checked: each model read only its context
   file and its own query results.
4. **Judge.** A separate, larger model (Opus tier) grades each answer against the scoring rule, the
   ground-truth results and the query results the model saw. Any number that cannot be traced to a query,
   and any wrong cause, fails the question even if the headline number is right.
5. **Author review.** I read every verdict. I can overrule the judge only to make a verdict stricter, and
   every overrule is published with its reason (one so far, below).

This is an offline proxy for Genie. It measures the design of the copilot (instructions, tables, functions)
with a capable model; Genie's own benchmark feature runs the same questions inside Databricks (see
`docs/genie_space_setup.md`, section 5).

## Results

| Run | Context | Passed | SQL that ran without error | Guardrails |
|---|---|---|---|---|
| 1 | [`results/run1_model_context.md`](results/run1_model_context.md) | 18 / 20 (the judge passed 19) | 108 / 108 | 4 / 4 |
| 2 | [`results/run2_model_context.md`](results/run2_model_context.md): instructions fixed after run 1 | 20 / 20 | 111 / 111 | 4 / 4 |
| 3 | [`results/run3_model_context.md`](results/run3_model_context.md): four wording changes | 19 / 20 | 111 / 111 | 4 / 4 |
| 4 | [`results/run4_model_context.md`](results/run4_model_context.md): a new rule, and the retry alert's new baseline | 20 / 20 | 110 / 110 | 4 / 4 |
| 5 | [`results/run5_model_context.md`](results/run5_model_context.md): a stale table description fixed | 20 / 20 | 105 / 105 | 4 / 4 |
| 6 | [`results/run6_model_context.md`](results/run6_model_context.md): ten table and function descriptions clarified; the final context, identical to [`model_context.md`](model_context.md) | 20 / 20 | 111 / 111 | 4 / 4 |

Per-question answers, the SQL each run wrote and the judge's notes: [`results/run1.json`](results/run1.json)
to [`results/run6.json`](results/run6.json).

**Overruled verdict (run 1, B1).** The judge passed it. I count it as a failure: the scoring rule asks for
the impact of the incident window, and the answer sized the calendar day (−7.1 pp and 82 approvals instead
of about −11 pp and 110).

### What run 1 found and what changed

**It ruled out the true cause because of a change description (B3, failed).** Asked only what the incident
cost, the copilot got both numbers right (110 approvals, USD 6,354), then volunteered a cause: it dismissed
the decline-code map release because the change log said the release only added code R65. That release had
also turned R05 into a hard decline. *Change:* two rules in the instructions. A change's description may not
list all its effects, so check its footprint in the data before ruling it out. And name only causes you have
checked: if the question asks for numbers, answer the numbers and say which checks you did not run.
*Runs 2 to 6:* same numbers, no cause asserted, unchecked steps listed.

**It sized the calendar day instead of the incident (B1, failed after review).** Asked why approval dropped
on 13 August, the copilot found both causes but sized the whole day. The incident ran from 14:00 to 12:10
the next day and cost 110 approvals. *Change:* a new first step in the method: find the window with the
hourly data and the change log, size that window and say which one was used. *Runs 2 to 6:* sized
13 Aug 14:00 → 14 Aug 12:10.

### What changed between runs 2 and 3

Run 3 re-ran all 20 questions after four wording changes in the context:

- issuers and acquirers are described as fictional codes (the context used to say "anonymized", which
  suggested real banks behind them);
- the example SQL uses fully qualified names (`workspace.approval_copilot.*`), which Databricks needs when
  the default schema is different;
- the p95 latency column is described as an approximate percentile: in A4 the run-2 model noticed it can be
  some milliseconds off the exact value (up to about 30 ms in that slice);
- "Genie space" became "Genie agent", the product's current name.

### What run 3 found and what changed

**It claimed something was absent beyond what it had checked (C3, failed).** Asked whether there was an
early warning before the Colombia incident, the copilot found the retry alert from 6 August and sized the
incident correctly. But its change-log query stopped at 15 August 00:00, and it wrote that the log showed
no mapping change through 15 August. The hotfix was logged at 08:00 that day, so its first recommendation
("restore code 05") rested on a false premise. *Change:* a new rule: when the answer says something did not
happen (no change, no alert, no notice), it gives the period it checked and does not extend the claim
beyond it. *Runs 4 to 6:* C3 passed, and every "did not happen" statement came with the period checked,
for example "change log for CO, 1 Jun to 15 Aug 00:00" (run 4), "no external notice for Colombia between
1 Jul and 15 Aug" (run 5) and "I did not check the change log after 14 Aug 00:00, so I cannot say what ended
it" (run 6).

Run 2 passed C3 with the same rules as run 3, a reminder that one run per question is a single
sample of a model that varies from run to run.

**The retry alert learned the outage (found in a design review, not by a failed answer).** The first
version of the alert compared each day's retry rate with a plain rolling 28-day baseline, which falls while
retries stay broken: after a week or two of collapsed days the alert would have switched itself off with
the bug still live. The baseline now uses only healthy days among the previous 56 days with failures. One
test covers a 20-day collapse and another pins where the alert stops, about eight weeks into a collapse
([decision log](../docs/decisions.md), ADR-08). It raises the same 18 alerts as before (B / 05 in Mexico
and Colombia, 6–14 Aug); only the baseline values changed, and the column descriptions in the context
changed with them.

Run 4 re-ran all 20 questions on the new rule and the new alert table.

### What changed between runs 4 and 5

- **One table description was stale.** The description of `gold_cascade_alerts_daily` still said the
  baseline was "the previous 28 days", while its columns described the new healthy-days baseline. Found in
  review after run 4 had started. It was corrected, and because every change to the context goes through
  the full question set first (ADR-09), run 5 re-ran all 20 questions. The two contexts differ by that one
  sentence.
- **Ground truth refreshed.** The expected results of B4 and C3 include rows of the alert table, so they
  were regenerated from the current tables. Run 4's answers to B4 and C3 were graded again against the new
  ground truth and passed again; the published verdicts are the regraded ones.
- **Seven questions re-run.** A pause for a usage limit stopped seven run-5 questions (B1–B6 and C3)
  before they had a final answer. They were run again from the start the next day with the same context;
  the stopped attempts had produced no answer, so nothing was graded or discarded after grading.

### What run 5 found

All 20 questions passed and all 105 SQL statements ran. The judge noted only minor imprecisions, none material; for example, one recommendation in B1 says the retries of B's code 05 were off "every day from 6 to 14 Aug", while its own evidence section correctly gives 5% on 6 Aug. Runs 4 and 5 both passed 20 of 20 and differ by one sentence of context.

### Other checks

- **ANSI mode.** Databricks serverless compute and SQL warehouses run with ANSI SQL mode on. All the
  statements the models wrote in runs 1 and 2 (219) were re-run with ANSI on: no errors and identical
  results. The local pipeline and the harness also run with ANSI on, so runs 3 to 6 used it from the start.
- **Harness bug, caught before any model saw it.** The first batch of run-1 queries failed because the
  container's time zone leaked into Spark. The harness now forces UTC; every query was re-run before its
  results were sent, so no model saw an error caused by the harness.

### What changed between runs 5 and 6

A review after run 5 found three gaps in the table and function descriptions. All 20 answers had passed
with them, but they could mislead other questions:

- The grain of `gold_decline_reasons_daily` and `gold_cascade_daily` left out the decline-code map version.
  On the days of a map release (6 and 15 Aug) a segment has one row per version, so a query that expects
  one row per day and reason could misread those days.
- The descriptions of `gold_anomalies_daily` and `gold_cascade_alerts_daily` read as if they held only
  flagged days. They hold every day; `is_anomaly` and `is_alert` mark the flagged ones.
- Five gold tables include cross-border payments and have no flag to remove them, while four of the five
  functions cover domestic payments only, and neither side said so. A retry rate from `gold_cascade_daily`
  and one from `fn_cascade_health` differ by 2–4 pp for that reason.

The descriptions now say all of this. They are written once, in the table and function definitions, so
Unity Catalog, the data dictionary and the context say the same. Because every change to the context goes
through the full question set first (ADR-09), run 6 re-ran all 20 questions; the two contexts differ only in
those ten descriptions.

### What run 6 found

All 20 questions passed and all 111 SQL statements ran; the tool audit was clean. Answers used the new
descriptions: B1, for example, labeled a figure from `gold_cascade_daily` "cross-border included". The
judge noted only minor imprecisions, none material. B1 gives the hourly range on Acquirer B for 13 August
only, and C3 sized the incident on whole hours, to 12:00 instead of 12:10 (108 lost approvals instead of
110), and said so. Runs 4 to 6 all passed 20 of 20.

### Databricks Genie run

Not run yet: the benchmark follows the deployment (`docs/runbook_databricks.md`, step 8). Results go here,
split by mode as in `docs/genie_space_setup.md`, section 5.

| Mode | Questions | Good | Bad | Manual review |
|---|---|---|---|---|
| Chat | A1–A6, B3, C1, C2, C4 (10) | – | – | – |
| Agent | B1, B2, B4, B5, B6, C3, D1–D4 (10) | – | – | – |

### Limits of this evaluation

- 20 questions, one sample per question and run. Runs 2 to 6 re-ask the questions that drove the fixes,
  so they show the fixes work on those cases, not that they generalize. Runs 4 to 6 differ only in table
  and function descriptions, so together they are three samples of almost the same design: still few. A held-out question
  set and several samples per question are the next step.
- The judge is a model. Its verdicts and notes are published in `results/` so anyone can re-check them.
- The model under test is not Genie. The same questions should be run as a Genie benchmark once the agent
  is live (section above).

## Files

| File | Purpose |
|---|---|
| `eval_set.json` | Questions, ground-truth SQL, scoring rules |
| `model_context.md` | What the model receives (generated by `tools/build_docs.py`) |
| `harness/run_queries.py` | Runs the model's SQL on the local pipeline output |
| `results/` | Context used in each run, answers and verdicts of each run, summary for the project page |
