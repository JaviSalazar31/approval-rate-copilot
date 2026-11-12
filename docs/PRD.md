# PRD · Approval Rate Copilot

| | |
|---|---|
| Author | Javier Salazar (Senior Product Owner, payments) · personal project |
| Status | v1 built on synthetic data, evaluated offline and deployed on Databricks Free Edition (29 Oct 2026) · Genie benchmark 17/20 on the eighth run and 19/20 on the seventh (13/20 on the first, 15/20 on the fifth and sixth) · held-out questions: 11/14 on the first set, then 26/39, 35/39, 32/39 and 32/39 answers on the second to fifth (13 questions each, three samples each) · gold layer in dbt, metric views, evaluations tracked in MLflow, deploys and evaluations from GitHub Actions, the five functions offered over MCP, a late-data test with AUTO CDC (Nov 2026) · pilot on real data not started |
| Scope | Card payments of a fictional LATAM orchestrator: Mexico, Colombia, Brazil · Acquirers A, B, C |

All figures below come from the synthetic dataset in this repository (90 days, 661k payment intents).
They illustrate the method and are not market statistics.

## 1. Problem

A payment orchestrator earns money only when payments are approved. At this synthetic volume (USD 45M
attempted in 90 days), one point of final approval is worth about USD 450k of approved sales per quarter.

The working hypothesis, to be measured in the pilot, is that when approval drops, the payments team finds out
late and takes hours to explain why, because the answer is spread across four sources: the orchestrator log
and three acquirer feeds, each with its own file format, time zone, amount format and decline-code dialect.

Two kinds of failure are especially expensive because they look fine on the usual dashboards:

1. A routing change can save fees and still lose approvals. On 13 Aug a routing change sent all Colombian domestic debit
   to Acquirer B to save 40 bps. Final approval fell from 86.5% to 75.6% for 22 hours: 110 approvals and USD 6.4k
   of approved volume lost, against about USD 180 of fees saved.
2. The recovery mechanism can fail silently. On 6 Aug a release of Acquirer B's decline-code map reclassified
   "do not honor" (05) as a hard decline, so those payments stopped being retried on a second acquirer (cascade).
   Daily approval barely moved. A retry alert flagged it in the first daily run, but it had no owner, so nobody
   acted for nine days, and about 220 approvals that a retry would normally have recovered were lost.

## 2. Users and jobs to be done

| User | Job | Today |
|---|---|---|
| Payments operations analyst | "When approval drops, tell me where, how much and why, so I can escalate to the right partner or ask for a rollback." | Manual SQL across four sources; time to a first answer to be measured in the pilot |
| Payments product manager (routing, acquirer strategy) | "Tell me whether a routing change pays off in approved volume as well as in fees." | Fee savings are visible on day one; approval losses show up later, mixed with noise |
| Merchant success manager | "Answer a merchant's question about declines with numbers I can trust." | Escalates to the data team |

Out of scope as users in v1: finance reconciliation, dispute and chargeback teams (different data).

## 3. Goals and non-goals

Goals:

- G1 · Detect abnormal approval drops and silent failures of the cascade within one daily cycle.
- G2 · Explain "why did approval drop?" in one conversation: impact, cause, evidence and recommended action.
- G3 · Be trustworthy: every number comes from a query the user can inspect, and the copilot says so
  when the data cannot answer.

Non-goals for v1:

- Automatic remediation (no automatic rollback of routing changes).
- Real-time streaming alerts (daily and hourly tables are enough to prove the value).
- Chargebacks, fraud losses, refunds and forecasting: not in the data.
- A merchant-facing interface.

## 4. Solution

```
 Orchestrator log ─┐
 Acquirer A feed  ─┤   BRONZE            SILVER                              GOLD                      CONSUMERS
 Acquirer B feed  ─┼─> raw files ──> attempts harmonized, de-duplicated, ──> KPI tables by day/hour, ──> AI/BI dashboard
 Acquirer C feed  ─┘   as received    mapped to canonical statuses           decline reasons, cascade,   "Approval Health"
 Reference data ───────────────────>  (versioned map), quality rules,        issuers, funnel,          ──> Genie copilot
 (routing, codes, change log)          quarantine · one row per payment       anomalies, cascade alerts       + 5 SQL functions
```

1. The data foundation is a Lakeflow pipeline (Spark Declarative Pipelines) on Databricks. Bronze keeps raw files
   as received; silver harmonizes the three acquirer formats, removes webhook re-deliveries, maps every raw code
   to a canonical status with the map version that was active at the time, and quarantines rows that fail
   quality rules; gold aggregates the KPIs.
2. Monitoring uses an AI/BI dashboard plus three detectors: a volume-aware anomaly check on daily approval, a
   cascade alert that watches the retry mechanism itself, and a forecast alert on approved payments. Before the
   pipeline runs, each raw feed is checked against its data contract.
3. The copilot is a Genie agent on the gold tables with business definitions, guardrails and five trusted SQL
   functions that encode the diagnostic method: size the drop, find who moved, see which decline codes grew,
   check whether retries still worked, and list production changes nearby.

Main flows:

- Morning check: the dashboard shows a flagged day; the analyst asks the copilot "why?", gets the cause, impact
  and evidence, and escalates or requests a rollback.
- Change review: a PM asks "compare A and B on Colombian debit since the routing change" before and after a
  change.
- Merchant question: a success manager asks about a merchant segment's declines and gets numbers with the query
  behind them.

## 5. Metrics

| Type | Metric | Definition |
|---|---|---|
| North star | Approved volume rate | approved USD / attempted USD, domestic traffic |
| Driver | First-attempt approval | approved on the first acquirer / payments sent |
| Driver | Cascade recovery | approvals added by retries on a second acquirer, in pp |
| Driver | Final approval | approved after retries / payments sent |
| Guardrail | Risk block rate | intents blocked by the risk engine / intents |
| Guardrail | Effective fee | fees / approved USD, in bps |
| Guardrail | p95 authorization latency | first attempt, per acquirer |
| Guardrail | Data-quality rejects | rows quarantined by rule |
| Product | Copilot answer accuracy | share of the 20-question eval set answered correctly (target ≥ 90%; guardrail questions 100%) |
| Product | Time to diagnosis | from "approval dropped" to a cause with evidence (target: under 15 minutes; to be measured in the pilot) |
| Product | Adoption | weekly active analysts and questions per week (pilot) |

Full definitions, formulas and pitfalls are in [metrics.md](metrics.md).

## 6. Requirements (v1)

| ID | Requirement | Acceptance criteria | Status |
|---|---|---|---|
| R1 | Ingest the four source formats and the reference data | Every attempt lands in silver or in quarantine with the rule it failed | Done: of 700,038 attempts, 699,262 landed in silver and 776 in quarantine |
| R2 | Remove webhook re-deliveries | One row per acquirer event | Done: 1,537 duplicates removed |
| R3 | Map raw codes to canonical statuses with a versioned map | Each attempt keeps the map version used | Done: v2.2, v2.3, v2.3.1 |
| R4 | Local business dates per country | MX UTC-6, CO UTC-5, BR UTC-3 | Done |
| R5 | Dashboard "Approval Health" | KPIs, trends, hourly drill-down, decline reasons, cascade, funnel, cost vs approval, anomalies, change log, data quality | Built on Databricks (29 Oct 2026) as specified in [dashboard_setup.md](dashboard_setup.md); the 42 queries behind its 21 widgets run without errors; layout review pending (with the demo recording) |
| R6 | Anomaly detection on daily approval | Flags the 13 Aug drop; no false alarms in the 90 days | Done, in-sample: 1 flag in 540 country × card-type days, on the days the thresholds were tuned on. The 3-hour issuer outage of 21 Jul needs hourly detection |
| R7 | Cascade alert | Fires on 6 Aug for Acquirer B, code 05, in Mexico and Colombia, and keeps firing every day until the map is fixed | Done, in-sample: alerts every day 6–14 Aug; a test checks that a 20-day collapse keeps alerting, and another pins where the alert stops |
| R8 | Copilot answers only with numbers from queries; separates acquirer-side from issuer-side causes; refuses what the data cannot answer | Eval set: ≥ 90% overall, 4/4 guardrail questions | Met offline: 20/20 (run 6), 4/4 guardrails. Met on Genie's benchmark in run 7: 19/20 (95%), after 13, 14, 15, 17, 15 and 15; not in run 8, after the eighth release: 17/20 (85%). 4/4 guardrails in every run. Not yet on new questions: 11/14 (79%) on the first held-out set, run once; 26 of 39 answers (67%) on the second, 13 questions asked three times each, with 7 questions passing in all three samples and the guardrails in 7 of 9 answers; 35 of 39 (89.7%) on the third, different questions asked after the release that addressed what the second found, with 10 questions passing in all three samples and the guardrails in 6 of 9 answers; 32 of 39 (82%) on the fourth, after the seventh release, with 9 questions passing in all three samples and the guardrails in 6 of 9 answers, all three misses on a what-if forecast; 32 of 39 (82%) on the fifth, after the eighth release, with 9 questions passing in all three samples and the guardrails in 6 of 9 answers: one what-if answer gave a number, and two answers to a request to change the decline map accepted a premise the change log contradicts ([evals/README.md](../evals/README.md)) |
| R9 | Read-only copilot | Refuses to modify or delete data | Covered by guardrail question D4. In the third held-out set, all three answers to a fake admin notice refused to change the change log, but two offered to draft the UPDATE for the user to run elsewhere, which I counted as failures. The seventh release extended the read-only rule to drafting, and in the fourth set all three answers to an on-call engineer who asked to add a row refused and drafted no statement. In the fifth set, all three answers to a request to make a decline code retryable refused and drafted no statement ([evals/README.md](../evals/README.md)) |
| R10 | No cardholder data in the analytics layer | No full card numbers (PAN), expiry dates, CVV or cardholder names in bronze, silver or gold. The only card fields are a truncated number (BIN and last four) in the orchestrator feed, `bronze_orchestrator` and `silver_payment_intents`. The landing volume and bronze are pipeline internals that no one else gets a grant on; from silver on, the last four are masked for everyone outside a support group. Issuers and merchants are codes; users reach the gold tables and `silver_payments` only through read-only Unity Catalog grants | Done and enforced twice: the feed checks stop a run on any text or number value that looks like a full card number (R11), and the Unity Catalog column mask on `silver_payment_intents.card_last4` shows the digits only to `payments_support` (verified by notebook 08 on 4 November 2026). Genie evaluates data access with each user's own Unity Catalog permissions, so analysts get SELECT on the 10 copilot tables and the three metric views, and EXECUTE on the five functions, nothing else (a production step: Free Edition has one user; see the runbook, step 7) |
| R11 | Stop the run when a feed breaks | A missing or empty file, a changed column set, values that break their format in more than 1% of a column, a repeated payment ID, an orchestrator volume outside 0.5 to 1.5 times the median of the previous 7 files, or an acquirer file that does not reconcile within 2% with the payments routed to it stops the job before the pipeline; single bad rows still go to quarantine (R1) | Done: one contract per feed in `contracts/`, checked by `checks/feed_checks.py` locally, in CI and as the job task before the pipeline. All 360 feed-days of the reference data pass, and tests cover each failure |
| R12 | Alert on approved payments as well as on rates | A daily forecast of approved payments with weekday and payday terms flags days well below it, tested on datasets its thresholds were not set on | Done: on three more datasets from the same generator, 2 false alerts in 762 segment-days, and 81% of days cut by 20% caught, against 54% for a same-weekday mean at the same false-alert rate ([impact_and_detection.md](impact_and_detection.md)) |
| R13 | Gold tables built and tested as code | Every gold table built by dbt, tested at its grain (unique keys, accepted values, a relationship to the issuer table, rates between 0 and 1) and reconciled with silver, locally, in CI and in the daily job, which stops on a failed test | Done: 17 models and 76 tests pass on Databricks (job run of 4 November 2026) and in CI, with the same gold totals as the pipeline's previous tables ([ADR-15](decisions.md)) |
| R14 | One definition per metric for the dashboard and the copilot | Approval, volume and decline measures defined once, as ratios of sums, and read by both the dashboard and Genie | Partly done: three Unity Catalog metric views hold the approval, volume, decline and funnel measures; two of the dashboard's eleven datasets read the first two, with the same rows as before, and Genie's chat mode queries all three. The other datasets and the functions still compute their own rates. A dimension comment without its values cost one held-out question (N4); the sixth release lists the values, and the three held-out answers that filtered on them got the reference count. The funnel view came with the seventh release, after A5 had split a country's risk-block share by card type ([ADR-17](decisions.md), [ADR-21](decisions.md)) |
| R15 | Every evaluation run and every deploy can be traced and repeated | Each evaluation run records its context, answers, SQL, latency, guardrail checks and verdicts; the workspace is deployed from the repository; the agent changes only with an evaluated release | Evaluation done: MLflow traces for Genie runs 5 to 8 and the second to fifth held-out sets (252 answers, none failing a guardrail check), with the verdicts attached ([ADR-18](decisions.md)); since 12 November the evaluation runs from GitHub Actions and the graded files are committed ([ADR-19](decisions.md)). Deploy done: on 5 November 2026 the GitHub Actions workflow bound the existing pipeline, three jobs and dashboard and changed them in place, with nothing created or deleted. The first run of the deployed daily job found a bug in the metric views notebook, fixed the same day, and the job then ran end to end ([ADR-16](decisions.md)) |

## 7. Risks and mitigations

| Risk | Mitigation |
|---|---|
| The copilot states a wrong number with confidence | Trusted SQL functions for the diagnostic math; "only numbers returned by a query" rule; SQL visible to the user; eval set run before every change to instructions, and a new held-out set, committed before each run, with three samples per question |
| Metric definitions drift between teams | Approval, volume and decline measures defined in Unity Catalog metric views that the dashboard and the copilot both read (ADR-17); the same definitions in the Genie instructions, the data dictionary and the column comments |
| Alert fatigue | Volume-aware thresholds (pp drop + z-score vs history + binomial z-score); backtest before enabling |
| A fix in the decline-code map breaks something else (the latent cause of this incident) | Map versioning, cascade alerts after every map release, and a regression test on retryable codes as a release gate |
| Retries break card-network rules (reattempt limits and fees) | One cascade per payment, only for codes the map marks retryable, never after a "do not try again" advice (Mastercard MAC 03 or 21; needs the advice code in the acquirer feeds), and the release gate checks the map against the retry policy (ADR-08) |
| Card data enters the analytics workspace and pulls it into PCI DSS scope | R10: only a truncated card number, with a column mask on the last four from silver on; the feed checks stop a run on anything that looks like a full card number, and any new source is reviewed for card fields before ingestion |
| Synthetic data hides real-world mess | Pilot on real data before any rollout decision; parameters of the generator are documented |

## 8. Rollout

1. Phase 0, done: synthetic data, pipeline validated on local Spark, dashboard and Genie agent
   specified, offline evaluation.
2. Phase 0b, done: deployed on Databricks Free Edition (29 Oct 2026) and ran the same questions as a
   Genie benchmark: 13/20 on the first run.
3. Phase 0c, done: tuned the Genie agent in three rounds, with functions, examples and descriptions as
   well as instructions ([ADR-12](decisions.md)), each followed by the full benchmark: 14/20, 15/20, then 17/20.
   The last round also ran 14 held-out questions once: 11/14.
4. Phase 0d, done (4 to 5 November 2026): one job for the whole run, data contracts checked before the pipeline, a
   forecast alert on approved payments, the gold layer in dbt, metric views for the dashboard and the copilot,
   evaluations tracked in MLflow, and deploys from GitHub Actions with an Asset Bundle (ADR-13 to ADR-18). The
   fifth benchmark run passed 15/20; a second held-out set, 13 questions asked three times each, passed 26 of 39
   answers.
5. Phase 0e, done (12 November 2026, UTC): the chat-mode fixes the held-out sets pointed to (dimension values in
   the descriptions, gaps computed in the queries, a text "NULL" treated as NULL, the domestic default kept to rate
   comparisons), deployed and evaluated from GitHub Actions (ADR-19), and the five functions offered to other
   assistants through an MCP server (ADR-20). The sixth benchmark run passed 15/20; a third held-out set, 13 new
   questions asked three times each, passed 35 of 39 answers.
6. Phase 0f, done (12 November 2026, UTC): the seventh release moved what the sixth run and the third set found
   into the functions (an incident date for the impact, a flag for retries the map stopped), a third metric view
   (the funnel) and the instructions (no drafted changes, rankings with the next two), and a late-data test ran
   with AUTO CDC in a schema of its own (ADR-21, ADR-22). The seventh benchmark run passed 19/20; a fourth held-out
   set, 13 new questions asked three times each, passed 32 of 39 answers.
7. Phase 0g, done (12 November 2026, UTC): the eighth release added rules and example queries for what the fourth
   set found, on what-if questions, rankings, totals and baselines (ADR-23). The eighth benchmark run passed 17/20;
   a fifth held-out set, 13 new questions asked three times each, passed 32 of 39 answers.
8. Phase 0h, next: what the fifth set found. Chat mode calls the drivers function directly and adds no ORDER BY or
   LIMIT, so a first-attempt rate and a row limit go into the function's parameters, and the agent checks the change
   log before it accepts a premise or recommends a fix; then the benchmark and a sixth held-out set. The pilot starts
   when the held-out sets also reach R8's 90%, the number to watch.
9. Phase 1, pilot (4 weeks): first, 20-minute interviews with three analysts to confirm the jobs and
   today's time to diagnosis. Then three analysts on real data; measure time to diagnosis against the
   current process and keep a log of wrong or missing answers.
   - Success: median time to a cause with evidence under 15 minutes; at least 8 of 10 answers judged
     correct by the analysts; all three use it every week.
   - Stop or rethink: fewer than 6 of 10 answers correct, or the analysts redo the SQL by hand anyway.
10. Phase 2: hourly detection and alerts to the team channel; benchmark questions grow with every incident.
11. Phase 3: a routing-change guardrail that compares approved volume instead of fees in the first hours after a
    change, and proposes a rollback when the drop is significant.

## 9. Open questions

- Acquirer scorecards: attribute cascade approvals to the first acquirer (routing decision) or to the
  approving acquirer (partner performance)? v1 keeps both columns and uses the first acquirer.
- Who owns the decline-code map, and which tests gate a release?
- Do thresholds need to differ by segment (for example, low-volume cross-border traffic)?
