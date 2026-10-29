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
   every overrule is published with its reason (two so far: B1 in run 1, below, and V6 in Genie run 4).

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
| Genie 1 | [`results/genie_run1_model_context.md`](results/genie_run1_model_context.md): the run-6 context with string window bounds, deployed as a Genie agent (29 Oct 2026) | 13 / 20 | 45 / 45 | 4 / 4 |
| Genie 2 | [`results/genie_run2_model_context.md`](results/genie_run2_model_context.md): the method carried in functions and examples ([ADR-12](../docs/decisions.md)) | 14 / 20 | 47 / 47 | 4 / 4 |
| Genie 3 | [`results/genie_run3_model_context.md`](results/genie_run3_model_context.md): retry flags in `fn_cascade_health`, and fixes for chat mode | 15 / 20 | 61 / 61 | 4 / 4 |
| Genie 4 | [`results/genie_run4_model_context.md`](results/genie_run4_model_context.md): a dimension parameter in `fn_approval_drivers` and each code across acquirers in `fn_decline_shift` | 18 / 20 | 55 / 55 | 4 / 4 |
| Held-out | The Genie 4 agent on 14 questions it was never tuned on ([`heldout_set.json`](heldout_set.json)), run once | 11 / 14 | 42 / 42 | 3 / 3 |

Per-question answers, the SQL each run wrote and the judge's notes: [`results/run1.json`](results/run1.json)
to [`results/run6.json`](results/run6.json), and for Genie
[`results/genie_run1.json`](results/genie_run1.json) to [`results/genie_run4.json`](results/genie_run4.json), and for
the held-out set [`results/heldout_run4.json`](results/heldout_run4.json).

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

### Databricks Genie, run 1 (29 October 2026)

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
[`results/genie_run1.json`](results/genie_run1.json).

### What Genie run 1 found

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
question set (R8): met offline, not yet on Genie. The next step was to carry those rules in the agent's
examples as well as in its text (example SQL that sizes an incident window, and one that ranks issuers
inside a window, on dates outside the benchmark) and in the descriptions (`p95_latency_ms` is a daily
percentile and cannot be averaged into a monthly one), then run the 20 questions on Genie again (ADR-09).

### What changed between Genie runs 1 and 2

Each fix went into the most structured place that could carry it, as Databricks recommends for Genie
agents: example queries and functions first, and text instructions "only for context that should be applied
globally" ([tune quality](https://docs.databricks.com/aws/en/genie-agents/tune-quality); ADR-12). The
context of run 1 is kept in [`results/genie_run1_model_context.md`](results/genie_run1_model_context.md).

| Failure in run 1 | Change |
|---|---|
| Sized the calendar day (B1, B3) | `fn_changes_near` also returns the changes in the 72 hours after the window, with their position (before, during, after). In run 1 it stopped at the end of the window, so the rollback of 14 August was out of sight. `fn_drop_impact` says in its description to pass the window of the drop. Two example queries: hourly approval next to the changes of each hour, and a window sized between two changes. The first step of the method says where a drop usually ends, and that an incident named by its dates takes its window from the change log. |
| Claimed more than it had checked (C3) | An example that searches the whole period for the first flagged day, a note on the alert column to query from 2026-06-01, and a rule for early warnings: search from the start of the data, give the first day, and say what it caught and what caused it. |
| Wrong table or metric (A4, C4) | An example that recomputes p95 from payments and one that ranks a dimension inside a window with `fn_approval_drivers`; descriptions on `first_latency_ms`, the issuer table's `issuer_id` and `fn_approval_drivers`; a definition: rates and percentiles of a period are recomputed, never averaged. |
| Explained a recovery it had not checked (B6) | The longer `fn_changes_near`, and one sentence in step 6: check the changes after a drop before saying how it ended. |
| Shape of a comparison (C1) | A rule: a comparison returns one row per item, with its rate and its payments. |
| Flaws in passing answers (B5, D4) | Issuer-side causes cannot be routed around; deleting incident rows would hide an incident. |

The new examples use dates, segments and changes the benchmark does not ask about, and none copies a
benchmark query. All eight examples ran on the warehouse before they went into the agent, together with the
dataset queries, the benchmark answers, the functions and the tables (59 checks, no errors).

Of the 111 statements of run 6, re-run with the new functions, the 107 that do not call `fn_changes_near`
return identical results. The 4 that call it keep every row and gain the `position` column and the changes
after their window: a call on 13 August now also shows the rollback and the mapping hotfix. The ground
truth of B1 and B5 was refreshed for the same reason; no scoring rule changed. The offline evaluation was not
repeated: the deployed product is the Genie agent, and run 2 measures it directly.

Run 2 also checks that the fixes are not tied to the benchmark's wording. B1 and C3, the two questions of the
demo, were asked three times each, and six reworded variants of failed questions (V1–V6), which are not in
the benchmark or in the agent, were asked once and graded with the same kind of scoring rule.

### Databricks Genie, run 2

| Mode | Questions | Passed | Failed | Graded by |
|---|---|---|---|---|
| Chat | A1–A6, B3, C1, C2, C4 (10) | 6 | 4: A5, B3, C1, C4 | Genie's grader (6 good, 4 bad) |
| Agent | B1, B2, B4, B5, B6, C3, D1–D4 (10) | 8 | 2: B1, B6 | Claude Opus, with the offline scoring rules |
| **Total** | 20 | **14** | 6 | |

Repeated samples: B1 passed 0 of 3, C3 3 of 3. Reworded variants: 2 of 6 (V1 and V4). Answers, SQL and
verdicts: [`results/genie_run2.json`](results/genie_run2.json); context:
[`results/genie_run2_model_context.md`](results/genie_run2_model_context.md).

### What Genie run 2 found

The fixes worked on what they targeted. In agent mode every answer to the incident sized its window, from
14:00 to the rollback at 12:10 the next day: 110 lost approvals and USD 6,354, in the three B1 samples and in
the variant V1. B2 named the rollback as the end of the drop, every early-warning answer (C3 three times,
and V2) found the first alert on 6 August, and A4 recomputed the p95 from payments. What failed instead:

- **B1 failed in all three samples, on its explanation rather than its numbers.** Sample 1 called the hard
  mapping of code 05 "correct" and blamed code 51, which the map never retries, by policy. Sample 2 called
  the 05 declines issuer-side and unrecoverable: it applied the run-2 sentence about issuer-side causes to a
  code that grew on one acquirer only. `fn_cascade_health` showed whether the map classified a code as soft
  or hard, but not whether it allowed a retry. Sample 3 led with the calendar day's −7.3 pp and gave the
  window's −10.9 pp only after.
- **It stated numbers no query returned (B6, V3).** B6 compared each card type with a blended baseline and
  stated a "2×" difference; V3 put 30 hours between two changes that are 54 hours apart.
- **An early warning without its trigger (V2).** It traced the alert to map v2.3 but left out the routing
  change that turned it into the drop a week later.
- **Chat mode used `fn_approval_drivers` too widely (C1, C4, V5, V6).** The run-2 line that sent rankings
  inside a window to that function also drew Genie to it for whole months, and chat mode returned its 24 or
  25 rows across six dimensions without filtering them. C4 called it on the right window, but the right
  answer, CO-ISS-07, is one row among 24. A5 multiplied the share by 100 in SQL, which the grader read as a
  missing column, and B3 still sized the two calendar days.

### What changed between Genie runs 2 and 3

| Failure in run 2 | Change |
|---|---|
| The retry data misread (B1) | `fn_cascade_health` also returns whether the map allowed a retry in each period (`baseline_retryable`, `window_retryable`). Step 5 says how to read it: a code that is never retryable (51) is policy, and a code the acquirer used to retry and no longer does points to a map change, which the alert table shows against the acquirer's own history. Step 7 defines an issuer-side cause as one issuer failing on every acquirer, and a code that grows on one acquirer only as acquirer-side, even 05. |
| Two figures for one drop (B1) | Step 7: lead with the impact of the window sized; a calendar-day figure is labeled as context. |
| Numbers no query returned (B6, V3) | Rules: a duration, ratio or difference that no query returned is not stated, and each segment is compared with its own baseline. |
| An early warning without its trigger (V2) | The early-warning rule also asks what turned the alert into the drop. |
| `fn_approval_drivers` used too widely (C1, C4, V5, V6) | Whole days and months go to the daily tables, one row per item; the function returns every dimension, so it is filtered to the one asked about, as its description now says too. The example that compares acquirers is worded the way users ask: which one is higher, and by how much. |
| Shape of a result (A5) | Rates stay fractions in SQL results; percentages belong in the text. |
| Window in one query (B3) | Step 1: an answer in a single query reads the window from the change log inside that query. |

Of the 111 statements of run 6, 103 return identical results; the 4 that call `fn_changes_near` and the 4
that call `fn_cascade_health` keep every row and gain the new columns. The ground truth of B1 was refreshed
for the same reason. The context of run 3 is kept in
[`results/genie_run3_model_context.md`](results/genie_run3_model_context.md); it is also the current
[`model_context.md`](model_context.md).

### Databricks Genie, run 3

| Mode | Questions | Passed | Failed | Graded by |
|---|---|---|---|---|
| Chat | A1–A6, B3, C1, C2, C4 (10) | 7 | 3: B3, C1, C4 | Genie's grader (6 good, 3 bad); A5, sent to manual review, by hand |
| Agent | B1, B2, B4, B5, B6, C3, D1–D4 (10) | 8 | 2: B1, B4 | Claude Opus, with the offline scoring rules |
| **Total** | 20 | **15** | 5 | |

Repeated samples: B1 passed 1 of 3, C3 3 of 3. Reworded variants: 4 of 6 (V1, V3, V4 and V6). All 61 SQL
statements of the 20 questions returned results, and no verdict was overruled. Answers, SQL and verdicts:
[`results/genie_run3.json`](results/genie_run3.json); context:
[`results/genie_run3_model_context.md`](results/genie_run3_model_context.md).

### What Genie run 3 found

Run 3 passed 15 of 20 (13 in run 1, 14 in run 2), and the reworded variants 4 of 6 (2 in run 2). The
changes fixed what they targeted:

- **The retry data was read right.** No B1 sample took code 51, which is never retried by policy, for the
  fault, called the 05 declines issuer-side or led with the calendar day's figure.
- **B6 and V3 passed.** B6 compared each card type with its own baseline instead of a blended one, and V3
  gave the time of both changes instead of a wrong duration between them.
- **Chat-mode results had the right shape.** A5 kept its rate as a fraction (the grader sent it to manual
  review because of two `NOT NULL` filters that do not change the result), and V6, the reworded C1, compared
  the acquirers with one row each: C at 91.5% against A at 89.6%.

What still fails:

- **B1 passed one sample of three.** Sample 2 is complete and right. Sample 1 ended the window at midnight
  instead of at the rollback, so it reported −12.2 pp and 79 lost approvals for ten hours of the incident,
  and wrote that about 98 of 93 failures would have been retried. Sample 3 had the right window and numbers,
  but offered "0 baseline failures on A" for code 05 as evidence. `fn_decline_shift` compares each acquirer
  with its own baseline: B had none, because it carried no Colombian debit before the change, and A's 628
  baseline 05 declines fell outside the 15 rows the query kept. The same split makes B's code 05 look like
  growth from zero (+13.7 pp), when across acquirers it went from 4.1% to 13.7% of payments.
- **B4 failed for the first time.** Cause and fix were right, but it grouped 6 to 15 August as "v2.3",
  although that period also holds v2.2 traffic from the morning of 6 August and v2.3.1 traffic after the
  hotfix of 15 August. It put the 48 retries of the period on "residual v2.2 traffic" (44 were v2.3.1) and
  estimated 70–75 lost approvals that no query returned; the ground truth is about 220.
- **Chat mode is where the rest fails (B3, C1, C4, and V5, the reworded C4).** C1, C4 and V5 called
  `fn_approval_drivers` and returned all of its 24 or 25 rows, across six dimensions, without filtering them,
  although its description and the instructions say to filter by dimension. B3 sized 13 and 14 August as
  whole days again; its one query did not read the window from the change log.
- **Two rules held most of the time, not every time.** Two of the three C3 samples searched the alerts only
  from 1 August; they named the right first day because no alert fired earlier. V2 again left out the
  routing change that turned the alert into the drop.

**What it means.** On Genie, structure carries the method better than text. The changes in functions and
examples fixed their targets: since run 2, 7 of the 8 agent-mode answers that sized the incident used the
window from 14:00 to the rollback, and the retry flags ended the misreadings of run 2. Text rules about how
to answer (search from the start of the data, name the trigger, state no number that no query returned)
hold most of the time. The questions that still fail point to two functions, not to more text: `fn_approval_drivers` needs a
dimension parameter, so that the one call chat mode makes returns only the rows asked for, and
`fn_decline_shift` needs a baseline across acquirers, for drops that move traffic from one acquirer to
another. R8 asks for 90%: met offline, not on Genie (75%). Three rounds on the same 20 questions also start
to fit the benchmark, so the next round adds a held-out question set.

### What changed between Genie runs 3 and 4

| Failure in run 3 | Change |
|---|---|
| Chat mode used `fn_approval_drivers` unfiltered (C1, C4, V5) | A `p_dimension` parameter: the function returns only the rows of that dimension, ordered from the lowest window rate (all six dimensions when it is not passed). Its description says to pass it, and that whole days or months are compared with the daily tables. The example that ranks card brands inside a window passes it, and the line of the instructions that said to filter the output now says to pass the parameter. |
| B's empty baseline read as A's (B1) | `fn_decline_shift` also returns each code across acquirers (`all_acquirers_baseline_pct`, `all_acquirers_window_pct`, `all_acquirers_delta_pp`). Code 05 grew from 4.1% to 13.7% of payments across acquirers, not from zero, and code 51 fell (4.2% to 3.8%), although each looked like growth from zero on B. Step 4 of the method says to read these columns when traffic moved between acquirers. |

Nothing else changed. Of the 111 statements of run 6, re-run with the new functions, 107 return identical results, one
returns the same rows in another order, and the three that select every column of `fn_decline_shift` keep their rows
and gain the new columns. The ground truth of B1, B5 and V1 gains the same columns; no scoring rule changed. All eight
examples, the benchmark answers, the functions and the tables ran on the warehouse before the agent was updated (59
checks, no errors). Context of run 4:
[`results/genie_run4_model_context.md`](results/genie_run4_model_context.md).

### The held-out set

Four rounds on the same 20 questions start to fit the benchmark, so run 4 also answered a set of questions the
agent was never tuned on: [`heldout_set.json`](heldout_set.json), 14 questions in the same four groups, with
reference SQL and scoring rules of the same kind. They were written after the changes above and committed to this
repository (commit `62ecca2`) before run 4 started. They ask about other months, segments and changes than the 20
benchmark questions: approval, fees, installments and 3DS abandonment, rankings of card brands, acquirers and merchant
segments, a drop that only shows in retries, a check of cross-border traffic during the incident, two changes that had
no effect (a 3DS SDK upgrade and two new merchants), and three new guardrails (card numbers, a routing change, and
"yesterday" in data that ends on 29 August).

The set runs once. The seven KPI and comparison questions went to chat mode through the Conversation API, the seven
diagnoses and guardrails to agent mode, and the same judge graded all fourteen with their scoring rules. Its result is
published whatever it is, and the agent is not changed after it.

### Databricks Genie, run 4

| Mode | Questions | Passed | Failed | Graded by |
|---|---|---|---|---|
| Chat | A1–A6, B3, C1, C2, C4 (10) | 8 | 2: B3, C4 | Genie's grader (7 good, 2 bad); A5, sent to manual review, by hand |
| Agent | B1, B2, B4, B5, B6, C3, D1–D4 (10) | 10 | 0 | Claude Opus, with the offline scoring rules |
| **Total** | 20 | **18** | 2 | |

Repeated samples: B1 passed 2 of 3, C3 3 of 3. Reworded variants: 5 of 6 (all but V6, overruled below). All 55 SQL
statements of the 20 questions returned results. Answers, SQL and verdicts:
[`results/genie_run4.json`](results/genie_run4.json); context:
[`results/genie_run4_model_context.md`](results/genie_run4_model_context.md).

**Overruled verdict (Genie run 4, V6).** The judge passed it. I count it as a failure, for consistency with the
held-out question H6: both ask "by how much", and both chat-mode answers are a table with the two rates and no gap.
The only percentage-point columns in those tables are changes against a 14-day baseline, which a reader could take
for the gap. H6 was failed on that ground, and a verdict is never made more lenient, so V6 fails too.

### What Genie run 4 found

Run 4 passed 18 of 20, the 90% that R8 asks for, against 13 in run 1. Every agent-mode question passed, and the
two changes did what they were for:

- **The dimension parameter fixed C1.** Chat mode called `fn_approval_drivers` with `p_dimension => 'acquirer'`
  over July and got one row per acquirer, A at 89.3% and C at 88.6%, which Genie's grader accepted. V5, the reworded
  C4, passed the same way.
- **The codes were read across acquirers.** All three B1 samples and V1 compared code 05 across acquirers: it went
  from 4.1% of payments (13.7% in the incident window), not from zero, and none took a code that is never retried for
  the fault.
- **Rules that had held most of the time held.** B4 split its periods at the map changes and labeled its estimate
  of lost recoveries as one (about 202, from its own counts); V2 named the routing change that turned the alert into
  the drop, after failing twice; B6 said that approval of the payments that reached an acquirer stayed flat.

What still fails:

- **B1 sample 2 ended the window at midnight again** (−12.2 pp and 79 lost approvals for ten hours), the error of
  run 3's sample 1, and dated the first alert to 10 August from a search that started then. Two of the 12 agent-mode
  answers that sized the incident since run 2 made this error.
- **B3 still sizes calendar days in chat mode**: `fn_drop_impact` from 13 August 00:00 to 15 August 00:00, 106
  approvals and USD 6,134.
- **C4 has the right answer in the wrong shape.** `p_dimension => 'issuer'` returned the eight issuers from the
  lowest up, CO-ISS-07 at 65.3% first, but the reference keeps the three lowest with four columns, and Genie's grader
  marked the extra rows and columns as wrong. By the offline scoring rule (name the lowest issuer) it would pass;
  the stricter verdict stands.
- **Flaws the scoring rules do not grade.** B5 recommends "temporary routing adjustments" for an issuer-side
  outage that, as it says itself, routing cannot avoid; V1 says the map bug "went undetected", although the retry
  alert flagged it from 6 August.

### The held-out set: results

| Group | Questions | Mode | Passed | Failed |
|---|---|---|---|---|
| KPI lookups | H1–H4 | Chat | 4 / 4 | |
| Comparisons | H5–H7 | Chat | 1 / 3 | H6, H7 |
| Diagnoses | H8–H11 | Agent | 3 / 4 | H8 |
| Guardrails | H12–H14 | Agent | 3 / 3 | |
| **Total** | 14 | | **11 / 14** | 3 |

All 42 SQL statements returned results. H11's first request ended with a transport error before any answer
(`ChunkedEncodingError`: the response ended prematurely), so the question was asked once more with the same code;
that answer is the one graded. Answers, SQL and verdicts: [`results/heldout_run4.json`](results/heldout_run4.json).

What passed is the part the method was built for. Every KPI lookup was right, both changes with no effect were
called that way (the 3DS SDK upgrade, H11, and the new merchants, H10, whose approval was in line with the existing
ones), cross-border traffic was checked and found untouched by the routing change (H9), and the three new
guardrails held: no card numbers (H12), no routing change (H13), and no "yesterday" in data that ends on 29 August
(H14). The three failures:

- **H6, a question in two parts, half answered.** Chat mode returned the acquirers from the lowest up (B first,
  right) and no text, so "how far below the best" (2.1 pp) was never said.
- **H7, an empty result.** Genie passed the text `'NULL'` for the optional card type of `fn_approval_drivers`, so
  the function filtered on a card type called NULL and returned nothing. The functions should treat that text as
  NULL.
- **H8, the right diagnosis with the wrong cost.** It found the map change and the lost retries, and sized B's drop
  right (81.8% to 79.1%), but gave the whole segment's lost approvals (146, all acquirers) as the cost of B's drop
  (about 122).

**What it means.** On the benchmark the agent reached the 90% of R8 on its fourth run, and on questions it was never
tuned on it scored 11 of 14 (79%). The benchmark questions drove four rounds of fixes and the held-out questions none,
so the held-out score is the better estimate of how the agent does on a question it has not seen. Two of the three
held-out failures are chat-mode problems of the same family as the benchmark's: chat mode answers with a function's
table, so the answer holds only what the table holds. The next changes are structural again: functions that treat a
text "NULL" as NULL, and comparison results that carry the gap, so that a table answers both parts of a question.
Then the held-out set grows, with new questions, and runs again.

### Limits of this evaluation

- 20 benchmark questions. Offline, one sample per question and run; on Genie, B1 and C3 also ran three times in
  runs 2 to 4, with six reworded variants of failed questions (2, 4 and 5 of 6 passed). Every run after the first
  re-asks the questions that drove the fixes, so it shows the fixes work on those cases, not that they
  generalize. The held-out set is that check, and it is small: 14 questions, one sample each, run once. A larger
  held-out set, with several samples per question, is the next step. Offline runs 4 to 6 differ only in table and
  function descriptions, so together they are three samples of almost the same design.
- The judge is a model. Its verdicts and notes are published in `results/` so anyone can re-check them. On
  Genie it also graded the agent-mode answers, in place of Genie's own agent-mode grader.
- The offline model under test is not Genie, and the gap was large: 20 of 20 offline, 13 of 20 on Genie with
  the same context. It took three rounds of fixes on Genie to reach 18. The offline runs show whether the design can
  work; only Genie runs show whether it works in the product.
- Chat-mode questions are graded by Genie's grader, which compares result sets and fails a right answer in the
  wrong shape (C4); agent-mode and held-out questions are graded by the judge with the scoring rules. The two
  graders are not interchangeable, and each verdict says which one gave it.

## Files

| File | Purpose |
|---|---|
| `eval_set.json` | Questions, ground-truth SQL, scoring rules |
| `heldout_set.json` | 14 held-out questions, same format, committed before Genie run 4 and run once |
| `model_context.md` | What the model receives (generated by `tools/build_docs.py`) |
| `harness/run_queries.py` | Runs the model's SQL on the local pipeline output |
| `results/` | Context used in each run, answers and verdicts of each run, summary for the project page |
