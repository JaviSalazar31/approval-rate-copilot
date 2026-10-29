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
| 6 | [`results/run6_model_context.md`](results/run6_model_context.md): ten table and function descriptions clarified; the last offline run (the deployed context differs in six lines, see "Deployment on Databricks") | 20 / 20 | 111 / 111 | 4 / 4 |
| Genie | [`model_context.md`](model_context.md), deployed as a Genie agent on Databricks (29 Oct 2026) | 13 / 20 | 45 / 45 | 4 / 4 |

Per-question answers, the SQL each run wrote and the judge's notes: [`results/run1.json`](results/run1.json)
to [`results/run6.json`](results/run6.json), and for Genie
[`results/genie_run_2026-10-29.json`](results/genie_run_2026-10-29.json).

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

### Deployment on Databricks (29 October 2026)

The deployment on Databricks Free Edition found two problems that the local harness could not see: it
inlines each function body with literal arguments instead of creating the function in Unity Catalog.

- **`fn_changes_near` could not be created.** Unity Catalog does not resolve a function parameter inside a
  subquery nested in a filter (`scope IN (SELECT … WHERE … p_country)`). The subquery became a join; five
  test windows return the same rows as before.
- **Genie rejected the agent.** Every question failed with "certified answer argument type is not
  supported: timestamp": functions attached to a Genie agent cannot take timestamp arguments. The window
  bounds are now strings in local time, converted inside the functions
  ([ADR-11](../docs/decisions.md)).

The second fix changed six lines of the context after run 6: the five function signatures and one example
query. All 111 SQL statements of run 6, re-run against the new signatures, return identical results. As
ADR-09 requires, the 20 questions then ran on the deployed version, as Genie benchmarks (next section); the
offline evaluation was not repeated.

Other checks of the deployment:

- the 367 files written by notebook 01 match the local reference run byte for byte;
- the pipeline built the 27 datasets and dropped the same 776 rows;
- the five function bodies in Unity Catalog match the repository;
- every column of the ten copilot tables has a description;
- the smoke test returns 1,004 window payments and 110 lost approvals;
- the 42 queries behind the 21 widgets of the dashboard run without errors (its layout is checked when the
  demo is recorded).

### Databricks Genie run (29 October 2026)

The 20 questions ran on the deployed Genie agent, split by mode as in `docs/genie_space_setup.md`, section 5.

- **Chat mode** (A1–A6, B3, C1, C2, C4) ran as a Genie benchmark through the API and was graded by Genie's
  own grader, which compares the result of Genie's SQL with the result of the reference query (`gold_sql`).
  The grader sent one question to manual review; I graded it with its scoring rule.
- **Agent mode** (B1, B2, B4, B5, B6, C3, D1–D4). Benchmark runs through the API grade in chat mode only,
  and the Databricks console does not support automated browser control, so these questions went to Genie's
  Agent mode API (beta), one conversation each. The offline judge (Claude Opus) graded the answers with the
  same scoring rules, against the ground truth and the query results Genie saw.

| Mode | Questions | Passed | Failed | Graded by |
|---|---|---|---|---|
| Chat | A1–A6, B3, C1, C2, C4 (10) | 6 | 4: A4, B3, C1, C4 | Genie's grader (5 good, 4 bad); A5, sent to manual review, by hand |
| Agent | B1, B2, B4, B5, B6, C3, D1–D4 (10) | 7 | 3: B1, B6, C3 | Claude Opus, with the offline scoring rules |
| **Total** | 20 | **13** | 7 | |

All 45 SQL statements Genie wrote returned results, and the four guardrails passed. No verdict was
overruled. Answers, SQL, results and verdicts:
[`results/genie_run_2026-10-29.json`](results/genie_run_2026-10-29.json).

### What the Genie run found

Genie named the right cause in all four diagnoses that ask for one (B1, B4, B5, B6), yet it passed 13 of
20 questions, against 20 of 20 for the offline model with the same context. The failures fall into four
patterns, and the first two are failure modes the offline runs had already found and fixed with
instructions:

- **It sized the calendar day again (B1, and B3 in chat mode).** B1 found both causes and wrote that the
  drop started at 14:00, then sized 13 August as a whole day against a 28-day baseline: −7.3 pp and 84 lost
  approvals, against −10.9 pp and 110 in the incident window. B3 sized 13 and 14 August as whole days
  (106 approvals and USD 6,134 instead of 110 and USD 6,354). This is the failure of run 1, and the first
  step of the method ("size that window, not the whole calendar day") did not prevent it.
- **It claimed more than it had checked (C3).** Its alert query started on 8 August, and the answer said
  the alerts started on 8 August, five days before the incident. They started on 6 August, in Colombia and
  Mexico. This is the failure mode of run 3, and the rule added after it ("give the period you checked")
  did not prevent it either. The answer also left out what broke the retries: the decline-code map v2.3.
- **It used the wrong table or metric (A4, C4).** A4 averaged the daily p95 latencies (1,748.5 ms) instead
  of taking the p95 of the month (1,746 ms). C4 joined the hourly table to the daily issuer table, so it
  measured whole days instead of the window, and named a foreign issuer (INTL-ISS-03) for a question about
  domestic cards, although the issuer table's description says it includes cross-border payments.
- **It explained a recovery it had not checked (B6).** It found the risk rule behind the drop, but called
  the recovery "self-corrected" and recommended reviewing the rule's thresholds. A logged change, risk rules
  v3.1 on 20 June, had already tuned them.

C1 failed on the shape of its result, not on the answer: Genie returned the right winner and gap in one row
instead of one row per acquirer, and the chat grader compares result sets. By the offline scoring rules C1
would pass, and so would A4 and B3 on their numbers; a verdict is never made more lenient, so Genie's
stricter grading stands. One passing answer has a flaw the scoring rules do not grade: B5 recommends
routing the issuer's traffic "to alternative issuers", which cannot be done, because the card determines
the issuer.

**What it means.** The offline evaluation measured the design with a Claude model and overstated how well
Genie follows it: two rules that fixed the offline model did not hold in Genie. The PRD asks for 90% of the
question set (R8): met offline, not yet on Genie. The next step is to carry those rules in the agent's
examples as well as in its text (example SQL that sizes an incident window, and one that ranks issuers
inside a window, on dates outside the benchmark) and in the descriptions (`p95_latency_ms` is a daily
percentile and cannot be averaged into a monthly one), then run the 20 questions on Genie again (ADR-09).

### Limits of this evaluation

- 20 questions, one sample per question and run. Runs 2 to 6 re-ask the questions that drove the fixes,
  so they show the fixes work on those cases, not that they generalize. Runs 4 to 6 differ only in table
  and function descriptions, so together they are three samples of almost the same design: still few. A held-out question
  set and several samples per question are the next step.
- The judge is a model. Its verdicts and notes are published in `results/` so anyone can re-check them. On
  Genie it also graded the agent-mode answers, in place of Genie's own agent-mode grader.
- The offline model under test is not Genie, and the gap is large: 20 of 20 offline, 13 of 20 on Genie
  with the same context. The offline runs show whether the design can work; only Genie runs show whether
  it works in the product.

## Files

| File | Purpose |
|---|---|
| `eval_set.json` | Questions, ground-truth SQL, scoring rules |
| `model_context.md` | What the model receives (generated by `tools/build_docs.py`) |
| `harness/run_queries.py` | Runs the model's SQL on the local pipeline output |
| `results/` | Context used in each run, answers and verdicts of each run, summary for the project page |
