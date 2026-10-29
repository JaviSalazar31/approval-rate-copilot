# Decision log

Short records of the choices that shaped the product, in the order they were made. Each one says what was
decided, what else was on the table and what it costs.

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
"soft" under v2.2 and "hard" under v2.3, which is exactly how the August incident is explained. The cost is
owning the map as a product artifact, with releases and tests.

---

### ADR-02 · Quarantine bad rows instead of dropping them silently

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

### ADR-06 · A SQL-grounded copilot, not a chatbot over documents

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
| BI alerts only, no copilot | Cheapest; tools the team already has | Says that something moved, not why; the analyst still spends the hours |

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
needs hourly detection. A fair test needs data the thresholds were not tuned on. Known limit: the trailing
mean also learns a drop that lasts for weeks, so such a drop would stop flagging; the same healthy-days fix
as the cascade alert (ADR-08) is planned for this check.

---

### ADR-08 · Watch the safety net, not only the outcome

**Decision.** Add a cascade alert: for each acquirer and decline reason, compare today's retry rate with its
recent healthy days (days whose retry rate had already collapsed are left out), alert when it collapses, and
escalate to the payments lead after three days in a row.

**Why.** When retries stop working, daily approval moves by a fraction of a point and the outcome-based
detector stays quiet. The mapping bug of 6 Aug cost about 220 recoverable approvals over nine days. The
first daily run after the release flags it, six days before the routing change that turned it into a
visible incident; in the scenario it had no owner, so nobody acted.

**Constraint: retries must follow the card networks' rules.** A cascade is a reattempt, and the networks
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
every one of its days, and another pins where the alert stops. Two limits remain. A deliberate reclassification would alert every day for about
eight weeks, so the alert needs an acknowledgement keyed on the map version; and a collapse longer than that
empties the healthy part of the 56-day window, so the alert stops. Both are reasons to escalate after three
days.

---

### ADR-09 · Evaluate the copilot before anyone relies on it

**Decision.** A fixed set of 20 questions (KPI lookups, diagnoses, comparisons and guardrails), each with
a scoring rule, run before every change to instructions, functions or tables. Sixteen have ground-truth
SQL; the four guardrail questions have an expected behavior. The same questions are specified as Genie
benchmarks (see `genie_space_setup.md`, section 5).

**Why.** Instructions that look better can make answers worse. Without a fixed set there is no way to know.

---

### ADR-10 · Synthetic data with realistic mess

**Decision.** Generate 90 days of data with a seeded script: four source formats (the orchestrator log and
three acquirer feeds), webhook re-deliveries,
unmapped codes, zero amounts, an issuer outage, a risk-rule change, a routing change and a mapping bug.

**Why.** No real payment data can be published, and clean synthetic data would hide the hard parts. The
generator is documented, so anyone can see which effects were planted and check that the pipeline and the
copilot find them.

**Cost.** Parameters are illustrative, not market statistics. A pilot on real data is the next step.

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

### ADR-12 · Carry the method in functions and examples, not only in the instructions

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

**Result.** 13 of 20 before the changes, then 14, 15 and 18 after three rounds; the reworded variants passed 2,
4 and 5 of 6. The third round was two more function changes, not more text: a dimension parameter on
`fn_approval_drivers`, because chat mode used the function's output unfiltered, and each code across acquirers
in `fn_decline_shift`, because a drop that moves traffic to another acquirer makes that acquirer's codes look
like growth from zero. On 14 held-out questions, committed before the last run and run once, the agent scored
11, and two of its three failures were chat-mode gaps of the same kind, which also call for structure: functions
that treat a text "NULL" as NULL, and comparison results that carry the gap.
