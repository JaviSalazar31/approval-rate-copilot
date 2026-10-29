# PRD · Approval Rate Copilot

| | |
|---|---|
| Author | Javier Salazar (Senior Product Owner, payments) · personal project |
| Status | v1 built on synthetic data, evaluated offline and deployed on Databricks Free Edition (29 Oct 2026) · Genie benchmark 17/20 after three tuning rounds (13/20 on the first run), 11/14 on held-out questions · pilot on real data not started |
| Scope | Card payments of a fictional LATAM orchestrator: Mexico, Colombia, Brazil · Acquirers A, B, C |

All figures below come from the synthetic dataset in this repository (90 days, 661k payment intents).
They illustrate the method; they are not market statistics.

## 1. Problem

A payment orchestrator earns money only when payments are approved. At this synthetic volume (USD 45M
attempted in 90 days), one point of final approval is worth about USD 450k of approved sales per quarter.

Working hypothesis, to be measured in the pilot: when approval drops, the payments team finds out late
and takes hours to explain why, because the answer is spread across four sources: the orchestrator log
and three acquirer feeds, each with its own file format, time zone, amount format and decline-code
dialect.

Two kinds of failure are especially expensive because they look fine on the usual dashboards:

1. **Routing changes that save fees but approve less.** On 13 Aug a routing change sent all Colombian
   domestic debit to Acquirer B to save 40 bps. Final approval fell from 86.5% to 75.6% for 22 hours:
   110 approvals and USD 6.4k of approved volume lost, against about USD 180 of fees saved.
2. **Silent failures of the recovery mechanism.** On 6 Aug a release of Acquirer B's decline-code map
   reclassified "do not honor" (05) as a hard decline, so those payments stopped being retried on a second
   acquirer (cascade). Daily approval barely moved. A retry alert flagged it in the first daily run, but it had no owner, so
   nobody acted for nine days, and about 220 approvals that a retry would normally have recovered were
   lost.

## 2. Users and jobs to be done

| User | Job | Today |
|---|---|---|
| Payments operations analyst | "When approval drops, tell me where, how much and why, so I can escalate to the right partner or ask for a rollback." | Manual SQL across four sources; time to a first answer to be measured in the pilot |
| Payments product manager (routing, acquirer strategy) | "Tell me whether a routing change pays off in approved volume, not only in fees." | Fee savings are visible on day one; approval losses show up later, mixed with noise |
| Merchant success manager | "Answer a merchant's question about declines with numbers I can trust." | Escalates to the data team |

Out of scope as users in v1: finance reconciliation, dispute and chargeback teams (different data).

## 3. Goals and non-goals

**Goals**

- **G1 · Detect** abnormal approval drops and silent failures of the cascade within one daily cycle.
- **G2 · Explain** "why did approval drop?" in one conversation: impact, cause, evidence and recommended action.
- **G3 · Be trustworthy**: every number comes from a query the user can inspect, and the copilot says so
  when the data cannot answer.

**Non-goals for v1**

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

1. **Data foundation.** A Lakeflow pipeline (Spark Declarative Pipelines) on Databricks: bronze keeps raw files as received;
   silver harmonizes the three acquirer formats, removes webhook re-deliveries, maps every raw code to a
   canonical status with the map version that was active at the time, and quarantines rows that fail
   quality rules; gold aggregates the KPIs.
2. **Monitoring.** An AI/BI dashboard plus two detectors: a volume-aware anomaly check on daily approval,
   and a cascade alert that watches the retry mechanism itself.
3. **Copilot.** A Genie agent on the gold tables with business definitions, guardrails and five trusted
   SQL functions that encode the diagnostic method: size the drop, find who moved, see which decline codes
   grew, check whether retries still worked, and list production changes nearby.

Main flows:

- **Morning check:** dashboard shows a flagged day → analyst asks the copilot "why?" → gets cause, impact and
  evidence → escalates or requests a rollback.
- **Change review:** PM asks "compare A and B on Colombian debit since the routing change" before and after
  a change.
- **Merchant question:** success manager asks about a merchant segment's declines and gets numbers with the
  query behind them.

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

Full definitions, formulas and pitfalls: [metrics.md](metrics.md).

## 6. Requirements (v1)

| ID | Requirement | Acceptance criteria | Status |
|---|---|---|---|
| R1 | Ingest the four source formats and the reference data | Every attempt lands in silver or in quarantine with the rule it failed | Done: 700,038 attempts → 699,262 in silver + 776 quarantined |
| R2 | Remove webhook re-deliveries | One row per acquirer event | Done: 1,537 duplicates removed |
| R3 | Map raw codes to canonical statuses with a versioned map | Each attempt keeps the map version used | Done: v2.2, v2.3, v2.3.1 |
| R4 | Local business dates per country | MX UTC-6, CO UTC-5, BR UTC-3 | Done |
| R5 | Dashboard "Approval Health" | KPIs, trends, hourly drill-down, decline reasons, cascade, funnel, cost vs approval, anomalies, change log, data quality | Built on Databricks (29 Oct 2026) as specified in [dashboard_setup.md](dashboard_setup.md); the 42 queries behind its 21 widgets run without errors; layout review pending (with the demo recording) |
| R6 | Anomaly detection on daily approval | Flags the 13 Aug drop; no false alarms in the 90 days | Done, in-sample: 1 flag in 540 country × card-type days, on the days the thresholds were tuned on. The 3-hour issuer outage of 21 Jul needs hourly detection |
| R7 | Cascade alert | Fires on 6 Aug for Acquirer B, code 05, in Mexico and Colombia, and keeps firing every day until the map is fixed | Done, in-sample: alerts every day 6–14 Aug; a test checks that a 20-day collapse keeps alerting, and another pins where the alert stops |
| R8 | Copilot answers only with numbers from queries; separates acquirer-side from issuer-side causes; refuses what the data cannot answer | Eval set: ≥ 90% overall, 4/4 guardrail questions | Met offline: 20/20 (run 6), 4/4 guardrails. Not yet on Genie: 17/20 (85%) on the fourth run, after 13, 14 and 15, with 4/4 guardrails in every run; on 14 held-out questions, run once, 11/14 (79%) and 3/3 guardrails ([evals/README.md](../evals/README.md)) |
| R9 | Read-only copilot | Refuses to modify or delete data | Covered by guardrail question D4 |
| R10 | No cardholder data in the analytics layer | No card numbers (PAN), expiry dates, CVV or cardholder names in bronze, silver or gold; issuers and merchants are codes; users reach the gold tables and `silver_payments` only through read-only Unity Catalog grants | Done in the data model. Genie evaluates data access with each user's own Unity Catalog permissions, so analysts get SELECT on the 10 copilot tables and EXECUTE on the five functions, nothing else (a production step: Free Edition has one user; see the runbook, step 7) |

## 7. Risks and mitigations

| Risk | Mitigation |
|---|---|
| The copilot states a wrong number with confidence | Trusted SQL functions for the diagnostic math; "only numbers returned by a query" rule; SQL visible to the user; eval set run before every change to instructions |
| Metric definitions drift between teams | One set of definitions in the Genie instructions, the data dictionary and Unity Catalog column comments |
| Alert fatigue | Volume-aware thresholds (pp drop + z-score vs history + binomial z-score); backtest before enabling |
| A fix in the decline-code map breaks something else (the latent cause of this incident) | Map versioning, cascade alerts after every map release, and a regression test on retryable codes as a release gate |
| Retries break card-network rules (reattempt limits and fees) | One cascade per payment, only for codes the map marks retryable, never after a "do not try again" advice (Mastercard MAC 03 or 21; needs the advice code in the acquirer feeds), and the release gate checks the map against the retry policy (ADR-08) |
| Card data enters the analytics workspace and pulls it into PCI DSS scope | R10: the pipeline carries no cardholder data; any new source is reviewed for card fields before ingestion |
| Synthetic data hides real-world mess | Pilot on real data before any rollout decision; parameters of the generator are documented |

## 8. Rollout

1. **Phase 0, done:** synthetic data, pipeline validated on local Spark, dashboard and Genie agent
   specified, offline evaluation.
2. **Phase 0b, done:** deployed on Databricks Free Edition (29 Oct 2026) and ran the same questions as a
   Genie benchmark: 13/20 on the first run.
3. **Phase 0c, done:** tuned the Genie agent in three rounds, with functions, examples and descriptions as
   well as instructions ([ADR-12](decisions.md)), each followed by the full benchmark: 14/20, 15/20, then 17/20.
   The last round also ran 14 held-out questions once: 11/14.
4. **Phase 0d, next:** fix the chat-mode gaps the benchmark and the held-out set found (comparisons without the
   gap, a text "NULL" for an optional parameter), grow the held-out set and run it on every change. The pilot
   starts when R8 is met on Genie, with the held-out score as the number to watch.
5. **Phase 1, pilot (4 weeks):** first, 20-minute interviews with three analysts to confirm the jobs and
   today's time to diagnosis. Then three analysts on real data; measure time to diagnosis against the
   current process and keep a log of wrong or missing answers.
   - Success: median time to a cause with evidence under 15 minutes; at least 8 of 10 answers judged
     correct by the analysts; all three use it every week.
   - Stop or rethink: fewer than 6 of 10 answers correct, or the analysts redo the SQL by hand anyway.
6. **Phase 2:** hourly detection and alerts to the team channel; benchmark questions grow with every incident.
7. **Phase 3:** routing-change guardrail: compare approved volume, not fees, in the first hours after a change,
   and propose a rollback when the drop is significant.

## 9. Open questions

- Acquirer scorecards: attribute cascade approvals to the first acquirer (routing decision) or to the
  approving acquirer (partner performance)? v1 keeps both columns and uses the first acquirer.
- Who owns the decline-code map, and which tests gate a release?
- Do thresholds need to differ by segment (for example, low-volume cross-border traffic)?
