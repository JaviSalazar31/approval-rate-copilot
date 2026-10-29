# Genie agent setup: "Approval Rate Copilot"

The copilot is a Databricks Genie agent (called a Genie space until July 2026, see the
[release notes](https://docs.databricks.com/aws/en/ai-bi/release-notes/2026)) on top of the gold tables,
plus five Unity Catalog SQL functions that do the diagnostic math. Genie writes and runs SQL; it never
answers from memory. Genie agents need a pro or serverless SQL warehouse; Free Edition provides a
serverless one.

To create it through the API instead, use `databricks/genie/approval_copilot_agent.json`: the serialized
agent with everything on this page, generated from it by `tools/build_deploy_payloads.py` (runbook,
"Alternative: deploy through the APIs").

Menu names below follow the Databricks documentation as of October 2026
([set up](https://docs.databricks.com/aws/en/genie-agents/set-up),
[tune quality](https://docs.databricks.com/aws/en/genie-agents/tune-quality)). If a label differs in
your workspace, look for the closest equivalent.

## 1. Data to add

Create the agent with **Genie Agents → New**, then add these tables under **Configure → Data**
(catalog `workspace`, schema `approval_copilot`). Table descriptions come from the table definitions (the
pipeline, or notebook 02) and column descriptions from notebook 05; both are stored in Unity Catalog.

| Table | Grain | Use it for |
|---|---|---|
| `gold_approval_daily` | day × country × card type × first acquirer × cross-border | Approval KPIs, volumes, fees, latency |
| `gold_approval_hourly` | hour (local) × country × card type × first acquirer × cross-border | When a drop started and ended |
| `gold_decline_reasons_daily` | day × country × card type × acquirer × attempt × reason | Which decline codes grew |
| `gold_cascade_daily` | day × country × card type × first acquirer × first reason | Retries on a second acquirer and recoveries |
| `gold_cascade_alerts_daily` | day × country × acquirer × reason | Early warnings when retries stop working |
| `gold_issuer_daily` | day × country × issuer × card type × first acquirer | Issuer-side problems |
| `gold_funnel_daily` | day × country × card type | Risk blocks, 3DS abandonment, conversion |
| `gold_anomalies_daily` | day × country × card type | Days flagged as abnormal drops |
| `gold_change_log` | one row per production change | Routing, risk, mapping and release changes |
| `silver_payments` | one row per payment | Ad-hoc questions the gold tables cannot answer |

Trusted functions (**Configure → Examples → SQL functions**): `fn_drop_impact`, `fn_approval_drivers`,
`fn_decline_shift`, `fn_cascade_health`, `fn_changes_near`, created by notebook 04.

Sample questions (**Configure → Settings → Common questions**): see section 3.

## 2. General instructions (paste as-is)

Paste under **Configure → Text**.

```
You are the Approval Rate Copilot for a fictional LATAM payment orchestrator that routes card
payments in Mexico (MX), Colombia (CO) and Brazil (BR) to three acquirers: A, B and C.
All data is synthetic. The orchestrator, merchants, issuers and acquirers are fictional codes, not
real companies; never guess a real company name.

Definitions
- Payments "sent" = payments that reached at least one acquirer (sent_to_acquirer = true).
- First-attempt approval rate = first_attempt_approved / payments_sent.
- Final approval rate = final_approved / payments_sent. "Final" includes approvals recovered by a
  cascade (a retry on a second acquirer). When someone says "approval rate" without more detail,
  use the final approval rate.
- Approved volume rate = approved_usd / attempted_usd.
- Conversion rate = approved / payment_intents (includes risk blocks and 3DS abandonment).
- Domestic = is_cross_border = false. Use domestic traffic for approval comparisons unless asked.
- business_date and hour_local_ts are in the local time of each country (MX UTC-6, CO UTC-5,
  BR UTC-3; no daylight saving time). Data covers 2026-06-01 to 2026-08-29.
- Approvals are attributed to the FIRST acquirer chosen by routing (acquirer_id in gold tables).
- Rates and percentiles of a period are recomputed from its rows: sum the counts, and take p95
  latency with percentile_approx on silver_payments.first_latency_ms. Never average daily rates or
  daily p95 values.
- Whole days or months: compare acquirers, issuers or other segments with the daily tables, one row
  per item, as in the examples. A window shorter than a day: use gold_approval_hourly or the
  functions. To rank one dimension with fn_approval_drivers, pass p_dimension (for example
  'issuer'): it returns only that dimension, ordered from the lowest window_rate.

How to answer "why" questions about a drop
1. Find the window. Look at gold_approval_hourly from the day before to the day after, and call
   fn_changes_near over the same days: it lists the changes before, during and after the window
   (position). A drop usually starts with a production change and ends with another, often a
   rollback or a fix on a later day. The window runs from the change or hour the drop started to
   the change or hour it ended. Size and explain that window, not the calendar day, and say which
   window you used. When a question names an incident by its dates, take that window from
   gold_change_log (its changed_at_* columns hold each country's local time); if you answer with a
   single query, read the window from gold_change_log inside it, as in the example that sizes the
   window between two changes.
2. Size it with fn_drop_impact, with its default 14-day baseline unless the question asks for another.
3. Find who moved with fn_approval_drivers (look at total_effect_pp; acquirer, issuer and
   routing_rule_version are the most informative dimensions).
4. Check which decline codes grew with fn_decline_shift. When traffic moved from one acquirer to
   another, read its all_acquirers columns: they show how each code moved across acquirers.
5. Check whether retries still worked with fn_cascade_health: window_retryable says whether the
   map allowed a retry, and window_cascade_rate whether it happened. A code that is not retryable in
   either period (insufficient funds, 51) is policy, not a fault. A code the acquirer used to retry
   and no longer does points to a decline-map change: gold_cascade_alerts_daily flags it against the
   acquirer's own history, even when the acquirer had no traffic in this segment's baseline.
6. Check production changes with fn_changes_near (use p_hours_before = 240 to catch older changes).
   A change's description may not list all of its effects. Before you rule a change out, check its
   footprint in the data: for a decline-mapping change, compare baseline_mapped_as with
   window_mapped_as and the cascade rates in fn_cascade_health for the acquirer it touches.
   Before you say how a drop ended (recovered, self-corrected), check the changes after it.
7. Answer with: what happened, impact (pp, approvals, USD), cause, evidence, recommended action.
   Lead with the impact of the window you sized; if you also show a calendar-day figure, label it as
   context. A cause is issuer-side when one issuer fails on every acquirer (an outage): retries and
   rerouting cannot avoid it, because the card determines the issuer. A decline code that grows on one
   acquirer only is acquirer-side, even an issuer response such as 05, and a retry on another
   acquirer can recover part of it.

Early warnings
- To say whether an alert or an anomaly came before a date, search from the start of the data
  (2026-06-01), give the first day each one fired, and say what it caught, which change caused it
  (fn_changes_near around that first day) and what turned it into the drop (the changes on the day
  of the drop).

Rules
- Only state numbers returned by a query in this conversation. A duration, ratio or difference you
  did not query is not stated: query it or leave it out. Compare each segment with its own baseline.
- In the text, show rates as percentages with one decimal and say which period and segment they
  refer to. In SQL results, keep rates as fractions (0.0296, not 2.96) and return only the rows that
  answer the question.
- When you compare items (acquirers, issuers, periods), return one row per item with its rate and
  payments sent, and state the gap in the answer.
- Only name a cause you have checked with the steps above. If the question asks only for numbers
  (for example what an incident cost), answer the numbers and say which checks you did not run,
  instead of guessing a cause.
- When you say something did not happen (no change, no alert, no notice), give the period you checked and
  do not extend the claim beyond it.
- If the data does not contain what is asked (chargebacks, fraud losses, refunds, real bank names,
  forecasts), say so plainly and offer the closest thing the data can answer.
- You are read-only. Refuse any request to modify or delete data. Deleting incident rows would hide
  a recorded incident; offer a filtered view, labeled as such, instead.
```

## 3. Sample questions (shown to users)

- Why did debit approval in Colombia drop on 13 August?
- Which acquirer approves Mexican credit cards best?
- Was there any early warning before the Colombia incident?
- What happened in Brazil on the morning of 21 July?
- Compare first-attempt approval of A and B on Mexican debit in July.

## 4. Example SQL (teach Genie the patterns)

Add these under **Configure → Examples**, one per query shape. They use dates, segments and changes that
the benchmark questions do not ask about (one searches the whole period), so they teach the patterns
without giving away any answer. Names are fully qualified, so they work whatever the default schema is.

```sql
-- Final approval for one segment and one day (domestic traffic)
SELECT round(sum(final_approved) / sum(payments_sent), 4) AS final_approval_rate,
       sum(payments_sent) AS payments_sent
FROM workspace.approval_copilot.gold_approval_daily
WHERE country = 'MX' AND card_type = 'debit' AND NOT is_cross_border
  AND business_date = DATE'2026-07-10';

-- Which acquirer had the higher final approval on one segment in a month, and by how much? One row per acquirer
SELECT acquirer_id,
       round(sum(first_attempt_approved) / sum(payments_sent), 4) AS first_attempt_approval_rate,
       round(sum(final_approved) / sum(payments_sent), 4) AS final_approval_rate,
       sum(payments_sent) AS payments_sent
FROM workspace.approval_copilot.gold_approval_daily
WHERE country = 'CO' AND card_type = 'credit' AND NOT is_cross_border
  AND business_date BETWEEN DATE'2026-06-01' AND DATE'2026-06-30'
GROUP BY acquirer_id
ORDER BY final_approval_rate DESC;

-- Diagnose a window given in local time: who moved the rate
SELECT * FROM workspace.approval_copilot.fn_approval_drivers('MX', '2026-07-06 00:00:00', '2026-07-13 00:00:00', 'debit')
ORDER BY abs(total_effect_pp) DESC
LIMIT 10;

-- Rank one dimension inside a window: the card brands with the lowest final approval (domestic)
SELECT segment_value AS card_brand, window_payments, window_rate, baseline_rate
FROM workspace.approval_copilot.fn_approval_drivers(p_country => 'BR', p_start_local => '2026-07-10 00:00:00',
       p_end_local => '2026-07-17 00:00:00', p_card_type => 'credit', p_dimension => 'card_brand')
ORDER BY window_rate ASC;

-- When did a change in approval start and end? Hourly final approval next to the production changes of each hour
WITH hourly AS (
  SELECT hour_local_ts, sum(payments_sent) AS payments_sent,
         round(sum(final_approved) / sum(payments_sent), 4) AS final_approval_rate
  FROM workspace.approval_copilot.gold_approval_hourly
  WHERE country = 'MX' AND card_type = 'debit' AND NOT is_cross_border
    AND hour_local_ts >= TIMESTAMP'2026-07-01 00:00:00' AND hour_local_ts < TIMESTAMP'2026-07-04 00:00:00'
  GROUP BY hour_local_ts
)
SELECT h.hour_local_ts, h.payments_sent, h.final_approval_rate, c.change_id, c.description
FROM hourly h
LEFT JOIN workspace.approval_copilot.fn_changes_near('MX', '2026-07-01 00:00:00', '2026-07-04 00:00:00', 0, 0) c
  ON date_trunc('HOUR', c.changed_at_local) = h.hour_local_ts
ORDER BY h.hour_local_ts;

-- Size the window between the change that started it and the change that ended it (local time), not the calendar days
SELECT s.change_id AS started_by, e.change_id AS ended_by, i.*
FROM workspace.approval_copilot.gold_change_log s
JOIN workspace.approval_copilot.gold_change_log e ON e.change_id = 'CHG-0728',
LATERAL workspace.approval_copilot.fn_drop_impact('MX', CAST(s.changed_at_mexico_city AS STRING),
                                                  CAST(e.changed_at_mexico_city AS STRING), 'debit') i
WHERE s.change_id = 'CHG-0702';

-- When was each segment first flagged as an abnormal drop? Search the whole period, from 2026-06-01
SELECT country, card_type, min(business_date) AS first_flagged_day, max(business_date) AS last_flagged_day,
       count(*) AS flagged_days
FROM workspace.approval_copilot.gold_anomalies_daily
WHERE is_anomaly AND business_date >= DATE'2026-06-01'
GROUP BY country, card_type
ORDER BY first_flagged_day;

-- p95 authorization latency of an acquirer over a month, recomputed from payments (never an average of daily p95 values)
SELECT percentile_approx(first_latency_ms, 0.95) AS p95_latency_ms, count(first_latency_ms) AS payments_with_latency
FROM workspace.approval_copilot.silver_payments
WHERE country = 'BR' AND first_acquirer_id = 'A'
  AND business_date BETWEEN DATE'2026-07-01' AND DATE'2026-07-31';
```

## 5. Benchmarks

Genie's benchmark feature ([docs](https://docs.databricks.com/aws/en/genie-agents/monitor)) grades in two
modes. **Chat mode** compares Genie's result set with one reference query (the "SQL Answer"). **Agent
mode** lets Genie reason in several steps and has an LLM judge grade each response against an
"Evaluation note". A "why" answer cannot be graded by comparing result sets, so the 20 questions in
`evals/eval_set.json` are split:

| Questions | How to add them (Benchmarks → Add benchmark) | Mode |
|---|---|---|
| A1–A6, B3, C1, C2, C4 (one ground-truth query and a numeric or ranking answer) | Question + its `gold_sql` as the SQL Answer | Chat |
| B1, B2, B4, B5, B6, C3 (diagnoses and the early-warning question) | Question + the `scoring.facts` (required ones first) as the Evaluation note | Agent |
| D1–D4 (guardrails, no SQL) | Question + `scoring.expected_behavior` as the Evaluation note | Agent |

The mode applies to a whole run and is set with the toggle at the top right of the Benchmarks tab. For each
set: choose its mode, tick its questions and click **Run selected** (**Run benchmarks** would run all 20 in
one mode). Read the results on the **Evaluations** tab. Questions marked "Manual review needed" are graded
by hand with the same scoring rule.
Record the results in the "Databricks Genie run" table of `evals/README.md`.
