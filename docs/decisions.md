# Decision log

Short records of the choices that shaped the product, in the order they were made. Each one states the
decision, the alternatives considered and the cost.

---

### ADR-01 · One canonical status model, with a versioned decline-code map

**Context.** Acquirer A sends ISO 8583 codes, B sends its own `R`-prefixed codes and C sends Portuguese
labels. The same issuer answer ("insufficient funds") arrives in three dialects, and partners change their
codes over time.

**Decision.** Map every raw `(source, status, code)` to eight canonical statuses (APPROVED, REVERSED,
DECLINED_SOFT, DECLINED_HARD, TECHNICAL_ERROR, TIMEOUT, BLOCKED_BY_RISK, ABANDONED), an ISO reason, a
decline type and a retryable flag. The map is a versioned reference table with validity dates, and every
attempt keeps the version that classified it.

**Alternatives.** A hard-coded `CASE` in the pipeline (no history, no audit), or keeping raw codes and
normalizing in each report (every analyst reinvents it).

**Consequences.** Point-in-time correctness and an audit trail: the copilot can show that code 05 from B was
"soft" under v2.2 and "hard" under v2.3, which is how the August incident is explained. The cost is
owning the map as a product artifact, with releases and tests.

---

### ADR-02 · Quarantine bad rows and keep them visible

**Decision.** Silver applies four quality rules (payment id present, valid timestamp, positive amount,
code found in the map). Failing rows are dropped from silver and kept in `silver_dq_quarantine` with the
rule they failed; `gold_dq_summary` and the dashboard show them.

**Why.** A silent drop hides partner problems. In this dataset the quarantine surfaced 722 Acquirer B
responses with an unmapped code (R65): the visible gap that mapping v2.3 was released to fix.

**Cost.** One more table, and a funnel metric (`routed_without_valid_attempt`) to keep the counts reconciled.

---

### ADR-03 · Attribute outcomes to the first acquirer chosen by routing

**Decision.** Approval, cascades and recoveries are attributed to the first acquirer (`acquirer_id` in gold).
The acquirer that finally approved is kept as `approving_acquirer_id`.

**Why.** Routing is the decision the team controls and the one a PM needs to evaluate. If a payment declined
by B is recovered by A, the routing choice "B first" still produced a worse first attempt.

**Cost.** Partner scorecards need the other column. Open question in the PRD.

---

### ADR-04 · Count reversals as authorized

**Decision.** `REVERSED` (authorized, then voided) counts as an approval in approval rates.

**Why.** The issuer approved; the void is a later merchant or system action. Excluding it would mix
authorization performance with post-authorization behavior. Reversal rate can be tracked on its own.

---

### ADR-05 · Local business dates per country

**Decision.** Dates and hours are computed in each country's time zone; raw timestamps are kept in UTC.

**Why.** Operations teams, merchants and partners talk in local days ("Tuesday afternoon in Bogotá").
A UTC day splits a Colombian evening across two dates. All three countries have no daylight saving time
in 2026, which keeps the offsets fixed.

**Cost.** Every consumer must keep the session in UTC. It is the Databricks serverless default, and
notebook 02 also sets it explicitly.

---

### ADR-06 · A SQL-grounded copilot built on Genie

**Decision.** The copilot is a Genie agent over the gold tables. The diagnostic method lives in five Unity
Catalog SQL functions (`fn_drop_impact`, `fn_approval_drivers`, `fn_decline_shift`, `fn_cascade_health`,
`fn_changes_near`); the model chooses which to call, with which window, adds its own SQL when it needs to,
and explains the result.

**Alternatives.**

| Option | Gains | Costs |
|---|---|---|
| Retrieval over runbooks and incident notes (RAG) | Fast to build | Cannot compute; numbers would be quoted, not measured |
| The model writes all the SQL from scratch | No functions to maintain | Each answer reinvents the method; more room for wrong joins and baselines |
| A custom agent (own orchestration) over the same five functions | Full control of the model, prompts, cost per question and logs | Hosting, access control and a chat UI to build and run; slower to pilot |
| BI alerts only, no copilot | Cheapest; tools the team already has | Says that something moved but not why; the analyst still spends the hours |

**Why.** The questions are quantitative ("how much did we lose?"), so answers must be exact and auditable.
Encoding the method in tested SQL makes the model's job smaller and safer: pick the right tool and window,
then explain. Genie gives the chat UI, Unity Catalog permissions and visible SQL out of the box, which is
what a pilot needs. The functions are plain Unity Catalog SQL, so a custom agent could reuse them later if
cost or control requires it.

**Cost.** Questions outside the tables and functions get a polite "the data doesn't have that". That is
intended (see the guardrail questions in the eval set). Genie is a Databricks feature, so the copilot
depends on that platform; the functions and the evaluation do not.

---

### ADR-07 · Volume-aware anomaly detection

**Decision.** A day is anomalous only if final approval is at least 3 pp below the trailing 28-day mean,
at least 3 standard deviations below that history, and at least 4 binomial standard errors below it for
that day's volume.

**Alternatives.** A fixed threshold (fires on every quiet Sunday) or z-score on history only (fires on
low-volume segments).

**Result (in-sample).** Over the same 90 days used to set the thresholds and six country × card-type
segments: one flag, on the planted incident (CO debit, 13 Aug). An earlier version with history-only
z-scores raised three false alarms. The three-hour issuer outage of 21 July does not trip a daily rule; it
needs hourly detection. A fair test needs data the thresholds were not tuned on. A known limit is that the
trailing mean also learns a drop that lasts for weeks, so such a drop would stop flagging; the same
healthy-days fix as the cascade alert (ADR-08) is planned for this check.

---

### ADR-08 · Alert on the safety net too

**Decision.** Add a cascade alert: for each acquirer and decline reason, compare today's retry rate with its
recent healthy days (days whose retry rate had already collapsed are left out), alert when it collapses, and
escalate to the payments lead after three days in a row.

**Why.** When retries stop working, daily approval moves by a fraction of a point and the outcome-based
detector stays quiet. The mapping bug of 6 Aug cost about 220 recoverable approvals over nine days. The
first daily run after the release flags it, six days before the routing change that turned it into a
visible incident; in the scenario it had no owner, so nobody acted.

**Constraint.** Retries must follow the card networks' rules. A cascade is a reattempt, and the networks
limit reattempts. Visa groups decline codes into four categories. After a Category 1 code ("issuer will
never approve", for example 04, 07, 41, 43 or 57) the transaction may not be reattempted. Categories 2 to 4
allow a limited number of reattempts of the same card in 30 days, with a fee beyond that: 15 under the rules
in force since April 2021, while one of the processor pages cited below now gives 20, so the limit must be
confirmed for each country. "Do not honor" (05) is not in Categories 1 to 3 as processor documentation
reproduces Visa's table, so it falls in Category 4, "generic response codes", which covers every other code.
Mastercard can charge for retrying the same card and amount after Merchant Advice Code 03 ("do not try
again") or 21 (recurring payment cancelled), and sets regional limits on attempts with the same card at the
same merchant (for example 10 a day or 35 a month). One retry on another acquirer stays within these limits
unless the decline carries MAC 03 or 21. The retry policy therefore allows a single cascade, only for codes
the map marks retryable, and never after a "do not try again" advice; the synthetic feeds carry no advice
code, so that check is not modeled. A release gate compares each code's retryable flag with that policy, so
a change like v2.3 is either caught or documented as intended. Rules and fees differ by region and change
over time, so the policy must be checked against the networks' current rules for Mexico, Colombia and
Brazil, including the other brands in the data (American Express and Elo). Sources: Visa's rule update for
Canada, Europe, Latin America and the U.S.
([declined transaction resubmission, 2020](https://usa.visa.com/content/dam/VCOM/global/support-legal/documents/updates-to-rules-for-declined-transaction-resubmission-and-use-of-authorization-response-codes.pdf))
and processor documentation ([Visa decline categories, Qualpay](https://help.qualpay.com/help/visas-decline-code-grouping);
[Merchant Advice Codes and reattempt limits, Yuno](https://docs.y.uno/reference/merchant-advice-codes-mac)).

**Cost.** One more alert to own. Thresholds need enough history (≥ 50 failures) to avoid noise. A first
version used a plain rolling 28-day baseline, which learns the outage: in Colombia it fell from 100% to about
60% by 14 Aug and would have dropped below the 50% the rule needs within days, so an unowned alert would also
have gone quiet. The baseline now uses healthy days only; one test checks that a 20-day collapse alerts on
every one of its days, and another pins where the alert stops. Two limits remain. A deliberate
reclassification would alert every day for about eight weeks, so the alert needs an acknowledgement keyed on
the map version; and a collapse longer than that empties the healthy part of the 56-day window, so the alert
stops. Both are reasons to escalate after three days.

---

### ADR-09 · Evaluate the copilot before anyone relies on it

**Decision.** A fixed set of 20 questions (KPI lookups, diagnoses, comparisons and guardrails), each with
a scoring rule, run before every change to instructions, functions or tables. Sixteen have ground-truth
SQL; the four guardrail questions have an expected behavior. The same questions are specified as Genie
benchmarks (see `genie_space_setup.md`, section 5).

**Why.** Instructions that look better can make answers worse, and without a fixed set there is no way to know.

---

### ADR-10 · Synthetic data with realistic mess

**Decision.** Generate 90 days of data with a seeded script: four source formats (the orchestrator log and
three acquirer feeds), webhook re-deliveries, unmapped codes, zero amounts, an issuer outage, a risk-rule
change, a routing change and a mapping bug.

**Why.** No real payment data can be published, and clean synthetic data would hide the hard parts. The
generator is documented, so anyone can see which effects were planted and check that the pipeline and the
copilot find them.

**Cost.** The parameters are illustrative assumptions, not market statistics. A pilot on real data is the next step.

---

### ADR-11 · Window bounds as strings in the copilot functions

**Decision.** The five copilot functions take the window start and end as strings in the local time of the
country (`'2026-08-13 14:00:00'`) and convert them to timestamps inside the function.

**Why.** Genie rejects attached functions with timestamp arguments. On the first deployment (29 Oct 2026)
the agent failed every question with "certified answer argument type is not supported: timestamp", and an
agent without its functions loses the diagnostic method (ADR-06).

**Alternative.** String wrappers over the timestamp functions: rejected, because it doubles the functions to keep in step.

**Cost.** The six lines of the context that show the signatures and one example query changed after run 6.
All 111 SQL statements of run 6, re-run against the new signatures, return identical results, and the
20 questions then ran on the deployed version as a Genie benchmark (ADR-09): 13 of 20, see
`evals/README.md`.

---

### ADR-12 · Carry the method in functions and examples as well as in the instructions

**Decision.** After the first Genie benchmark (13 of 20), each fix went into the most structured place that
could carry it. `fn_changes_near` also returns the changes after a window (`p_hours_after`, 72 hours by
default), with their position. After the second run, `fn_cascade_health` also returns whether the map
allowed a retry (`baseline_retryable`, `window_retryable`), because Genie had read a code that the map never
retries as a fault. The descriptions of three functions and six columns say how to use them, and five
example queries show the query shapes Genie got wrong. The text instructions gained short rules only where
nothing structured fits: early warnings, the shape of results, issuer-side causes, numbers that no query
returned, and deletes.

**Why.** Genie followed the text instructions less closely than the offline model did: two rules that fixed
the offline runs did not hold. And in the first run the function itself hid what Genie needed:
`fn_changes_near` stopped at the end of the window, so the rollback of 14 August was out of sight when
Genie sized 13 August. Databricks' guidance puts example queries and functions first and keeps text
instructions "only for context that should be applied globally"
([tune quality](https://docs.databricks.com/aws/en/genie-agents/tune-quality)).

**Alternative.** Longer instructions: rejected, because Genie had already ignored the short ones. A sixth
function that finds the window of a drop by itself: rejected for now, because in this data the change log
already marks where each incident starts and ends, and a detector tuned on 90 days would be fitted to them.

**Cost.** Example queries can leak answers, so they use dates, segments and changes the benchmark does not
ask about, and none copies a benchmark query. The two functions return more: of the 111 statements of
run 6, the 8 that call them keep their rows and gain the new columns (and, for `fn_changes_near`, the changes
after the window), and the other 103 return identical results. Each round ran the 20 questions on Genie
again, with reworded variants of the failed ones to check that the fixes are not tied to the benchmark's
wording (`evals/README.md`).

**Result.** 13 of 20 before the changes, then 14, 15 and 17 after three rounds; the reworded variants passed 2,
4 and 5 of 6. The third round changed two functions and the parts of the context that point to them (their
descriptions, one example, one line of the instructions and step 4 of the method): a dimension parameter on
`fn_approval_drivers`, because chat mode used the function's output unfiltered, and each code across acquirers
in `fn_decline_shift`, because a drop that moves traffic to another acquirer makes that acquirer's codes look
like growth from zero. The second did what it was for. The first returned only the rows asked for, but it also
drew chat mode to the function for whole months, where the answer is the table alone: C1 and its reworded
variant gave both rates and never the gap, and count as failures. On 14 held-out questions, committed before the
fourth run and run once, the agent scored 11, and two of its three failures were chat-mode gaps of the same kind,
which also call for structure: functions that treat a text "NULL" as NULL, and comparison results that carry the
gap. A fifth run, after the metric views were added, passed 15; the two questions it lost were agent-mode answers
that used no metric view. A second held-out set of 13 questions, asked three times each, passed 26 of 39 answers
and again lost most of its points in chat mode: a function's table with no text (N7), a ranking that dropped the
runner-up (N6), an instruction applied outside its scope (N3), and a metric view whose description did not list a
dimension's values (N4, ADR-17). Two of the four, N7 and N4, call for structure again.

The sixth release put each fix where the model reads it: the values of every short dimension in the metric view and
column descriptions (N4), monthly rankings and comparisons sent to the daily tables with two example queries that
compute the gap before any filter (N6, N7, C1), a `pp_below_next` column on `fn_approval_drivers`, the recoveries
lost at the baseline rate on `fn_cascade_health` (B4), and a text "NULL" read as NULL in the functions (H7). Six
passages of the instructions changed with them, on the scope of the domestic default (N3), where whole-month
rankings go and how a comparison computes its gap (N6, N7, C1), how to size stopped retries (B4), how a diagnosis
opens (B1) and the data's lack of customer names (N13). On a third held-out set, written after the release and
asked three times each, chat mode passed all 21 answers and the set passed 35 of 39. The benchmark stayed at 15 of
20: C1 and B4 now pass, A5 and B6 failed, and B1 failed all three samples, each on a different misreading of its
own query results; those need structural fixes as well.

---

### ADR-13 · Stop the run on a broken feed, quarantine bad rows

**Decision.** Each raw feed has a data contract (`contracts/*.yml`): its columns, their formats and allowed
values, how much of a column may break before the feed counts as changed (1%), and a key, volume or
reconciliation rule. A job task checks the day's files against them before the pipeline reads anything, and
stops the run on a missing or empty file, a changed column set, a column whose values stop matching their
format, a repeated payment ID, an orchestrator volume outside 0.5 to 1.5 times the median of the previous 7
files, an acquirer file whose first attempts do not reconcile within 2% with the payments the orchestrator
routed to it, or any value that looks like a full card number. Single bad rows inside a valid file still go
to the quarantine (ADR-02).

**Why.** The quarantine is built for a few bad rows. A feed that changes shape at once, for example an
acquirer that switches its timestamp format, would send most of a day to quarantine and leave a hole in the
gold tables that reads like a drop in approval. Stopping before the pipeline keeps the previous tables intact
and sends the failure email instead. The card-number rule enforces R10 before any table reads the file.

**Alternatives.** Pipeline expectations that fail the update: they check one row at a time, so a share of bad
values, a volume ratio or a reconciliation between two feeds would each need its own aggregate table inside
the pipeline, and the rules would run only on Databricks. The contract check runs before ingestion, and the
same code runs locally and in CI. A row-count check per acquirer against its own history: a routing change
moves volume between acquirers on purpose (on 13 August, B's file was 1.65 times the median of its previous
seven files), so each acquirer reconciles against what the orchestrator routed to it.

**Cost.** A format change announced by an acquirer now needs a contract change in the same release as the
parser change. A file that fails stays in the landing volume, and the next run checks only its own date, so
the on-call replaces or removes the bad file before rerunning (runbook, troubleshooting). The check reads every
value of the day's files, which is cheap at these volumes; at real volumes it would sample. The contracts are
strict on structure and loose on rows: up to 1% of bad values per column passes.

**Result.** All 360 feed-days of the reference data pass, locally, in CI and in the job run of 4 November
2026. Tests cover a renamed column, a new timestamp format, truncated orchestrator and acquirer files, a file
delivered twice, a missing file (including an orchestrator file missing on a day the acquirers already sent
theirs), five bad amounts (which pass) and one full card number (which stops the run).

---

### ADR-14 · Forecast approved payments, with weekday and payday terms

**Decision.** A third daily alert watches approved payments per country and card type against a forecast
fitted on the previous 56 days, with a trend, weekday terms and a payday term. A day alerts when it falls
below the 99.9% lower prediction bound and at least 8% under the forecast. A job task writes the result to
`gold_volume_alerts_daily` after the pipeline.

**Why.** The two existing alerts watch ratios of payments that reached an acquirer, so a loss before the
acquirer leaves them flat: the risk rules of 18 June cut conversion in Brazil while authorization approval
did not move. Approved payments see every kind of loss, but they move with the calendar: in this data a
Sunday runs about 30% below a Friday, and paydays 12 to 18% above a normal day.

**Alternatives.** A trailing 28-day mean of counts, which needs a band wide enough to absorb those swings, and
the mean of the previous four same weekdays, which handles weekends but not paydays. With thresholds set so
that each raises as many false alerts as the forecast on the reference data, they caught 23% and 54% of the
days cut by 20% in the backtest. A forecasting library (Prophet, exponential smoothing): more machinery for
the same two seasonal terms, and one more dependency in the job. Hourly forecasts come with the hourly
approval alerts on the roadmap.

**Cost.** About one false alert every six weeks across the six segments (4 in 1,016 segment-days without a
planted incident, over the four datasets), and a lower bound about 15% under the forecast: smaller drops pass unseen at daily
grain, which includes every planted incident in this data. The 18 June risk rules cost Brazil 2 to 3% of its
approved payments on their two worst days and fell in the 42-day warm-up, so this alert would have missed
them too. The payday calendar is an input someone has to keep current.

**Result.** On three more datasets from the same generator, the alert caught 81% of the days cut by 20% and
all of those cut by 30%, against 54% and 94% for the same-weekday mean and 23% and 66% for the trailing mean.
The generator draws daily volume from the same weekday, payday and trend terms the forecast fits, so this
tests new noise rather than new behavior ([impact and detection](impact_and_detection.md)).

---

### ADR-15 · Build the gold layer with dbt, as a task of the job

**Decision.** The Lakeflow pipeline keeps ingestion, harmonization and the quality rules: bronze, reference and
silver. The dbt project in `dbt/` builds the ten gold tables, with the same names, in a job task between the pipeline
and everything that reads gold. Staging models wrap the pipeline's silver and reference tables one to one, two intermediate models hold the
logic several gold tables share (payments that reached an acquirer, and failed attempts with their local date), and
the marts are the gold tables.

**Why.** Gold holds the business logic: rate definitions, baselines, and the anomaly and retry alerts. dbt tests each
gold table at its grain (unique keys, accepted values, a relationship to the issuer reference table, rates between 0 and 1,
gold reconciling with silver), generates documentation from the same descriptions Unity Catalog and Genie read, and
draws the dependency graph. The same project runs in three places: locally, in the Spark session of
`local/run_local.py`; in CI, which fails on a broken test; and on Databricks, where the job's dbt task runs it on the
SQL warehouse.

**Alternatives.** Keeping gold as materialized views in the pipeline: one tool less, but pipeline expectations check
rows, so a table's grain or its reconciliation with silver would each need an extra aggregate table. dbt for every
layer, ingestion included: Auto Loader and the quality rules with a quarantine are what the pipeline does well, and
dbt would need a separate ingestion step.

**Cost.** Two tools in the job instead of one. dbt rebuilds each gold table in full on every run, which takes under two
minutes at these volumes (105 seconds in the job run of 4 November); at real volumes the daily tables would become incremental models. When gold moved, the
pipeline left the old materialized views inactive, as Lakeflow does with datasets removed from its code, and they
were dropped by hand before the first dbt run.

**Result.** Locally, the gold tables dbt builds match the previous ones row for row, except `fees_usd` on 4 of
1,501 rows of `gold_approval_daily`, by one cent: the order in which floating-point sums add up changed. On
Databricks, the job ran with the dbt task on 4 November 2026: 17 models and 76 tests, all passing, the same gold
totals as the local run, and the functions' smoke test in notebook 04 unchanged (1,004 payments, 110 lost approvals, −10.95
points, USD 6,354).

---

### ADR-16 · Deploy from the repository with an Asset Bundle, and change the agent only with an evaluated release

**Decision.** `databricks.yml`, generated from the same sources as the job definitions, declares the pipeline, the
three jobs (the daily job, the evaluation job and the job that updates the agent) and the dashboard. A GitHub
Actions workflow validates the bundle on every push to main that touches it, and deploys it when someone runs the
workflow by hand. The first deploy binds the pipeline, jobs and dashboard that already exist, found by name, so the
bundle takes them over instead of creating copies. The Genie agent is not a bundle resource: the update job applies
the space file in the repository (`databricks/genie/approval_copilot_agent.json`) only when a deploy asks for it,
and first runs every example and benchmark query and checks that each function and table exists, stopping on any
failure.

**Why.** What runs in the workspace should be what the repository holds, reviewed and tested by CI, and a deploy
should be repeatable. The agent is held to a stricter rule. Its instructions, examples and descriptions are what
the evaluation measured (ADR-09), so a change to them ships only with a new evaluation, and a deploy never changes
them as a side effect.

**Alternatives.** Deploying on every push: simpler, but a change to the agent would reach users before its
evaluation, and the first push would have created copies of every resource before the bind. Terraform: Databricks
maintains a provider that covers the same resources, but a bundle keeps jobs, pipelines and dashboards in YAML next
to the code, with one CLI to validate, deploy and run them. One-off API scripts and edits in the workspace: how this
project was deployed until 4 November, with nothing to check that the workspace still matched the repository.

**Cost.** The workflow signs in with a personal access token kept as a repository secret; in production it would
be a service principal, not a person's token. Free Edition has one user, so there is a single production target
and no staging workspace.

**Result.** On 5 November 2026 the first deploy bound the existing pipeline, three jobs and dashboard, then stopped
before recreating the dashboard: the bundle had placed it in a folder of its own, and a new parent path would have
given it a new ID and URL. With the dashboard's folder named in the bundle, the next deploy changed the three jobs
and the pipeline in place and created and deleted nothing. The deployed daily job then found a bug that no earlier
run had reached: notebook 09, added after the last full run, was a SQL notebook, and its cell split a metric view's
definition at a semicolon inside a comment. Notebook 09 now creates the views from Python, with the same
definitions, and the daily job ran end to end from the deployed files in under seven minutes, with the same gold totals.

---

### ADR-17 · Define each rate once, in Unity Catalog metric views

**Decision.** Two metric views, `approval_metrics` over `gold_approval_daily` and `decline_metrics` over
`gold_decline_reasons_daily`, define the measures: rates as ratios of sums (final and first-attempt approval,
approved share of the volume) and plain sums (payments, approvals, volume, fees, failed attempts), with the
dimensions people group by. The dashboard's daily-segment and decline-reason datasets read them with `MEASURE()`,
and the Genie agent has both as data sources, with four lines of instructions on how to query them.

**Why.** A rate stored per row is wrong as soon as someone averages it over days or segments; a ratio of sums is
right at any grouping. With the definition in one place, every dashboard dataset and agent query that reads it computes the
same number the same way, and a new cut needs no new table or view.

**Alternatives.** Rate columns in the gold tables: they stay, for row-level use, but every reader has to know not
to average them. A view per grouping: one more view for every new cut. dbt's semantic layer: its query interfaces
for other tools come with dbt Cloud, and the dashboard and Genie read Unity Catalog.

**Cost.** Queries go through `MEASURE()`, and a filter on a dimension needs its exact values. The second held-out
set showed what a vague description costs. The `attempt` dimension of `decline_metrics` takes the values
'first attempt' and 'retry', and its comment reads "First attempt, or the retry on a second acquirer". The values
appear in the dimension's CASE expression, not in its comment. Chat mode filtered on 'First attempt' or on
'first' in all three samples of N4: two got no rows and reported no declines, and the third got a NULL and
reported no value, where the answer was 2,036. The dimensions with a handful of values (country, card type,
acquirer, decline type, map version) list them in their comments; this one did not. Listing them changes what the agent sees, so the fix
waits for the next evaluated release.

**Result.** The two dashboard datasets return exactly the rows of the queries they replaced (540 and 15,235 rows,
no differences). In Genie run 5 and the second held-out set, chat mode chose a metric view for 9 of its 31
answers, and agent mode for none: five of the nine passed, N4's three failed on the dimension values, and one
failed on its own ranking query.

---

### ADR-18 · Track each evaluation run in MLflow, with guardrail checks on every answer

**Decision.** The evaluation job (notebook 11, built from `evals/genie_eval.py`) asks the agent the questions:
the chat-mode benchmark questions through Genie's benchmark runner, the others through the Conversation API
(chat mode) or the Agent mode API (agent mode), each in a new conversation. Each run is an MLflow run in the
experiment `approval-copilot-evals`, and each answer is a trace: an agent span with the question and the answer, a
tool span for each SQL statement, the latency (for the answers asked through the Conversation and Agent mode APIs;
Genie's benchmark runner does not report it), and the result of the deterministic checks in
`evals/guardrails.py` (no full card number, no card listed by its last four digits, no SQL that writes). The run
records a hash of the agent's context, the latency percentiles and the guardrail failures. After grading, the
verdicts go back onto the same traces as feedback, each with who gave it: the judge, Genie's grader, or my
stricter override.

**Why.** Until run 4, each run was a set of JSON files put together by hand. With traces, each verdict points to
the exact answer, queries and context it graded, two runs can be compared in the workspace, and latency is
measured. The guardrail checks run on every answer to every question, not only on the guardrail questions,
because a card number could leak from any answer.

**Alternatives.** The JSON files alone: they stay in the repository as the published record, but they cannot be
queried or compared in the workspace. MLflow's built-in LLM judges: the judge guide and the scoring rules would
have to be ported and checked again, and the verdicts would stop being comparable with runs 1 to 4.

**Cost.** The Agent mode API is in beta. A run waits on the agent: agent-mode answers took a median of 60 seconds
in run 5 (95th percentile 86 seconds) and 24 seconds in the held-out run (95th percentile 45 seconds), measured from
the request to the final answer with three questions in flight at a time, and the 39 held-out answers took six
minutes. Each trace is written once its answer has arrived, so the trace timeline does not show the wait; the latency is an attribute of the trace. The judge runs outside the workspace, so the verdicts reach MLflow in a second step. The checks
are a floor: they cannot tell a real bank's name from an issuer code, which only the judge can.

**Result.** Run 5 (24 answers) and the held-out run (39 answers) are MLflow runs with one trace per answer and
the verdicts attached. None of the 63 answers failed a guardrail check.

---

### ADR-19 · Run the evaluation from the repository, like the deploy

**Decision.** A workflow started by hand (`.github/workflows/evaluate.yml`) runs the evaluation job with the
repository's two Databricks secrets. `ask` runs a question set and keeps the answers file as a workflow artifact and,
compressed, in the run log; `log_verdicts` uploads a graded file committed to the repository
(`evals/results/verdicts_*.json`) and attaches its verdicts to the run's MLflow traces; `fetch` reads back the answers
of an earlier run. The deploy workflow can also run the daily job after a deploy (`run_daily_job`), so the
functions, column descriptions and metric views in the workspace match the release before the agent is updated.

**Why.** The agent changes only with an evaluated release (ADR-16), but until run 5 I started the evaluation from a
local session on my computer. On 12 November (UTC) that session's credentials had expired, and the sixth release
still had to be deployed and evaluated. Running both from the repository removes the dependency on one machine and
one login, and the graded file that MLflow received becomes part of the published record.

**Alternatives.** Logging in again on the local machine: quicker that night, but every later run would depend on
it again. An evaluation on every deploy: each run asks Genie dozens of questions and needs a judge outside the
workspace, so it stays a deliberate step.

**Cost.** The workflow holds a GitHub runner while the job runs, up to two hours. The answers are kept three
times: in the volume, as an artifact and in the log.

**Result.** The sixth release was deployed, with the daily job and then the agent update, and evaluated from GitHub
Actions on 12 November (UTC): Genie run 6 (24 answers) and the third held-out set (39 answers). The verdicts
reached MLflow from the committed files, 24 and 39 graded, and both runs logged the same hash of the agent's
context (`context_sha256` in the results files), different from run 5's.

---

### ADR-20 · Expose the five functions to other assistants through a small MCP server

**Decision.** A Model Context Protocol server (`mcp_server/`, Python, stdio) offers the five diagnostic
functions as read-only tools to any MCP client, such as Claude Desktop, Claude Code or another agent. Each tool
runs one `SELECT` on one function. Its arguments are checked against the values the data holds and bound as
parameters of the Statement Execution API, so nothing a client sends becomes SQL text. A local backend runs the
same SQL bodies on the local pipeline output.

**Why.** The method lives in the functions (ADR-06, ADR-12), not in Genie. An analyst who works in another
assistant, or an agent that triages incidents, should get the same numbers without reimplementing the method or
having to work in the Databricks workspace. A server that can only call the five functions keeps that access as
narrow as the Genie agent's.

**Alternatives.** A tool that takes free SQL: more flexible, and it would turn every client into a writer of SQL
against the workspace, which the guardrails exist to prevent. A Databricks-managed MCP server for the schema's
functions: no code to keep, but arguments would reach the functions unchecked, every call would need the workspace,
so CI could not test the tools on the local output, and Free Edition may not offer it. A REST API: any client could
call it, but each assistant would need its own integration.

**Cost.** One more component to keep in step with the functions: a new function or parameter needs a new tool or
argument here. The server uses the caller's Databricks credentials, so read-only access depends on the token too.
It adds the MCP and Databricks SDKs to CI.

**Result.** On 12 November, through the MCP protocol over stdio, the server returned on the deployed functions the
copilot's numbers for the Colombia debit incident: 1,004 payments, −10.95 points, 110 lost approvals and USD 6,354
of lost approved volume; CO-ISS-07 as the lowest issuer, 7.1 points below the next; and the routing change during
the window, with the rollback and the map hotfix after it. CI makes the impact and issuer calls on the local
backend.

---

### ADR-21 · Have the functions return what the agent misread (agent v7)

**Decision.** The seventh release moves into the functions what the agent had to work out from their results.
`fn_drop_impact` takes the date of the change that started an incident (`p_incident_date`), reads the window from
the change log, from that change to the next change in the country, and returns the window it sized, where it came
from, and the payment funnel of the same segment (conversion, risk blocks, 3DS abandonment). `fn_cascade_health`
flags a retry that the decline map stopped (`map_stopped_retries`), judged against the acquirer's own history
because the acquirer may have carried none of the segment before, and can filter one issuer. `fn_approval_drivers`
no longer repeats the total change on every row. A third metric view, `funnel_metrics`, defines conversion, risk
blocks and 3DS abandonment as ratios of sums, so a country's rate covers both card types. The read-only rule now
covers drafting a change for someone else to run, a ranking returns the item and the next two, and new example
queries show the incident date, the issuer filter, failures summed from `decline_metrics` and the funnel view. The
copilot still has five functions.

**Why.** After the sixth release, most failures came from reading results the agent had to interpret
([evals/README.md](../evals/README.md)). In run 6 the main diagnosis (B1) failed all three samples: one took the
decline-map change for a correct classification, one read the total change, which `fn_approval_drivers` repeated
on every row, as each segment's own effect, and one counted the rows of a daily table where it had to sum failures.
B3 had sized the calendar days in all six runs, B6 never said that approval held while conversion fell, C4
returned all eight issuers, and A5 returned one row per card type in run 6. In the third held-out set, one answer
gave the country's recovery rate for an issuer's maintenance (T10), one offered a count that no query returned
(T12), and two offered to draft a change they had refused (T11). The instructions already asked for the incident
window and for summed counts.

**Alternatives.** A new function for each kind of question: more for the agent to choose from, and the README, the
MCP server and the video describe five. More instruction lines: the rules for B3 and for counting were already
there. Changing the benchmark's references so the answers match: the evaluation rules allow only stricter verdicts.

**Cost.** The functions return more columns for the agent to read. Agent-mode answers took longer than in run 6
(a median of 72 seconds against 55), which one run cannot pin on the release. One change came after the fourth
held-out set was committed, and it bears on B3's grade: `fn_drop_impact` names its window the same way however it
is passed (`window_source`, commit c78f5ce). The benchmark's reference for B3 passes the window explicitly and
selects every column, and Genie's grader compares every column, so without the change an answer that called the
function with the incident date would have failed on that label alone, with the same figures. It was deployed with
the agent before both runs, and no question of the fourth set depends on that column.

**Result.** Genie run 7, on 12 November (UTC), passed 19 of 20 (9 of 10 in chat mode and 10 of 10 in agent mode,
the four guardrails included), against 15 in runs 5 and 6: the first Genie run above the PRD's 90% bar. B3 passed
for the first time, with the incident date. B6 passed: it said that conversion fell while acquirer approval did
not. B1 passed two of three samples; I failed the second although the judge passed it, because it said the traffic
composition had not changed. C4 still returned all eight issuers. On a fourth held-out set, 13 new questions asked
three times each, the agent got 32 of 39 answers right, and 9 questions passed in all three samples. The three
answers on what the stopped retries cost before the incident (U10) found the reference's 57 lost recoveries, and
none of the three answers to a request to add a row to the change log drafted a statement (U11). Two kinds of
failure are new: asked a what-if about next month, all three samples scaled the 22-hour incident up to a month of
losses (U13), and two of three answers to a first-attempt ranking used the drivers function, whose rate is final
approval (U5). The other two answers it lost miscounted a total (U2) and measured a drop against a three-day
baseline (U8).

---

### ADR-22 · Apply late status events by the acquirer's sequence, in a test of their own

**Decision.** A separate job (`approval_copilot_late_cdc`) tests how a live feed of acquirer status events would
be applied. Notebook 12 lands the events of six test payments in two batches, the second a day late, in a
schema that holds only this test (`workspace.approval_copilot_cdc`). A Lakeflow pipeline
(`databricks/pipeline/late_cdc_test.sql`) applies them with AUTO CDC, keyed by payment and ordered by the
acquirer's sequence number: the current status as SCD type 1 and every status as SCD type 2, and a DELETE event
removes the payment. Notebook 13 checks the tables after the second batch and fails the job if a check fails.

**Why.** The copilot's pipeline rebuilds a closed 90-day period from files that are all there when it runs, so the
order in which events arrive does not change its result. A live feed can deliver an event after a newer one for
the same payment, and a table that keeps the last event to arrive would then show an old status. The test covers
the cases that cause this, on data the copilot never reads: a settlement that arrives a day after the reversal that
followed it, a reversal that arrives late, a payment first seen in the late batch, a payment the acquirer voids,
and two events listed out of order in the same batch.

**Alternatives.** A MERGE written by hand with a sequence check: it can keep the current status, but the history
and two events for one payment in the same batch need more code to get right. The test on the copilot's own
tables: it would add test payments to the numbers the copilot reports.

**Cost.** One more pipeline and job to keep. Six payments cover the cases written for them; a real feed brings more
volume and variety. The copilot's tables do not use this yet; moving them onto a live feed is work for a pilot with
a real source.

**Result.** On 12 November (UTC) the job's first run completed both pipeline updates, and a query of the tables
afterwards showed them right, but its check task failed: it read the history's `__START_AT` column as a Python
attribute, which PySpark does not allow. With an alias in the query, and a test that keeps such reads out of the
notebook, the second run passed all five checks. Each payment's current status is its event with the highest
sequence number; keeping the last event to arrive would have shown PAY-CDC-02 as settled, although the acquirer
reversed it after the settlement, and kept PAY-CDC-05, which the acquirer voided. The history places PAY-CDC-02's
late settlement between its approval and its reversal, the voided payment left the current table with its history
closed, and the copilot's gold table did not change (1,501 rows, 558,150 final approvals and USD 39,055,555.01 of
approved volume before and after).

---

### ADR-23 · Answer the fourth set's misreadings with rules and examples (agent v8)

**Decision.** The eighth release answers what Genie run 7 and the fourth held-out set found with text and example
queries; no function returns anything new. The rule against forecasts covers a question about what would happen if
something were done or repeated, even when it asks only for a number: the agent says that the data covers 1 June to
29 August 2026, scales no measured figure to another period and gives what was measured as history. The definitions
and the description of `fn_approval_drivers` say that it measures final approval, so a first-attempt ranking reads
`first_attempt_approved`. A ranking orders its rows and keeps three in the query itself, also when they come from a
function. A total comes from the query itself, and a baseline is the 14 days before the window, never a shorter
average. Before it says whether traffic moved between segments, the agent compares the shares, because traffic that
moves onto a segment with no baseline shows in that segment's rate effect, and its mix effect stays near zero. Two
new example queries rank acquirers on first-attempt approval and issuers inside a window, and neither answers an
evaluation question.

**Why.** In the fourth held-out set, all three answers to a what-if about next month scaled the 22-hour incident up
to a month (U13), two of three answers to a first-attempt ranking used the drivers function's final rate (U5), one
gave a total that its own rows did not add up to (U2), and one measured a drop against a three-day average (U8). In
run 7, one B1 sample read a zero mix effect as unchanged traffic, and C4 returned all eight issuers. None of them
needed data the agent did not have.

**Alternatives.** A rate parameter and a row limit in `fn_approval_drivers`: a change to a function that the MCP
server, its tests and the docs describe, made for two questions, so I tried the instructions first. Keeping chat
mode away from the function: it answers questions about a window in local time, which the daily tables cannot.

**Cost.** More text for the agent to read, and on Genie text has held less well than a function's output (ADR-12).

**Result.** Genie run 8, on 12 November (UTC), passed 17 of 20 (9 of 10 in chat mode and 8 of 10 in agent mode, the
four guardrails included), against 19 in run 7. Two agent-mode answers failed in new ways. B4 recommended restoring
the retry of code 05 for Acquirer B, although its own results showed hotfix v2.3.1 cascading those declines from 15
August; the judge passed it, and I count it as a failure. B6 gave the impact function's 16 lost approvals, which
count the acquirer stage, as the cost of risk blocks that stopped about 120 intents before they got there. On a
fifth held-out set, 13 new questions asked three times each, the agent got 32 of 39 answers right, as on the
fourth, and 9 questions passed in all three samples. Answers followed the new rules where the agent writes its own
query and text: all three answers on whether the traffic moved compared the shares (V8), and two of three answers
to a what-if gave no number (V11), against none of three in the fourth set; the third turned a past gap into about
800 approvals. Chat mode's direct function calls did not follow them: C4 ran the same SQL as in runs 4 to 7,
`fn_approval_drivers` with named parameters and no ORDER BY or LIMIT, and all three first-attempt rankings returned
its final rate again (V5). Two answers to a request to make code 05 retryable for Acquirer B also treated it as
still not retryable (V13), the premise B4 accepted. The next release starts there: the rate and the row limit as
parameters of the function, and a check of the change log before the agent accepts a premise or recommends a fix.
