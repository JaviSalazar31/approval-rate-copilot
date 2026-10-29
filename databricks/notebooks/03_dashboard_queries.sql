-- Databricks notebook source
-- MAGIC %md
-- MAGIC # 03 · Dashboard datasets
-- MAGIC One query per dataset of the AI/BI dashboard "Approval Health". Paste each one as a dataset
-- MAGIC (Dashboards → Create dashboard → Data → Create from SQL). Widget setup is in docs/dashboard_setup.md.
-- MAGIC
-- MAGIC Names are fully qualified, so each query works on its own in the dashboard editor. Datasets keep
-- MAGIC `business_date`, `country` and `card_type` wherever the grain allows, so the page filters apply.

-- COMMAND ----------

-- ds_kpis: KPI counters, last 7 days vs the 7 days before (domestic traffic).
-- Counts, not rates, so the counters can be filtered by country and card type: each counter is a
-- custom calculation with a filtered aggregation, for example
-- SUM(final_approved) FILTER (WHERE period = 'last_7_days') / SUM(payments_sent) FILTER (WHERE period = 'last_7_days').
WITH last_day AS (SELECT max(business_date) AS d FROM workspace.approval_copilot.gold_approval_daily)
SELECT
  CASE WHEN business_date > (SELECT d FROM last_day) - INTERVAL 7 DAYS THEN 'last_7_days' ELSE 'previous_7_days' END AS period,
  country, card_type,
  sum(payments_sent) AS payments_sent,
  sum(first_attempt_approved) AS first_attempt_approved,
  sum(final_approved) AS final_approved,
  sum(payments_sent) - sum(first_attempt_approved) AS first_attempt_failures,
  sum(recovered_by_cascade) AS recovered_by_cascade,
  sum(attempted_usd) AS attempted_usd,
  sum(approved_usd) AS approved_usd,
  sum(fees_usd) AS fees_usd
FROM workspace.approval_copilot.gold_approval_daily
WHERE NOT is_cross_border
  AND business_date > (SELECT d FROM last_day) - INTERVAL 14 DAYS
GROUP BY 1, country, card_type;

-- COMMAND ----------

-- ds_daily_by_segment: final approval per day, country and card type (line chart)
SELECT business_date, country, card_type, concat(country, ' ', card_type) AS segment,
       sum(payments_sent) AS payments_sent,
       round(sum(final_approved) / sum(payments_sent), 4) AS final_approval_rate
FROM workspace.approval_copilot.gold_approval_daily
WHERE NOT is_cross_border
GROUP BY business_date, country, card_type;

-- COMMAND ----------

-- ds_hourly_by_acquirer: hourly drill-down by first acquirer. business_date is the local date of the
-- hour, so the page's date filter applies.
SELECT CAST(hour_local_ts AS DATE) AS business_date, hour_local_ts, country, card_type, acquirer_id,
       sum(payments_sent) AS payments_sent,
       round(sum(final_approved) / sum(payments_sent), 4) AS final_approval_rate
FROM workspace.approval_copilot.gold_approval_hourly
WHERE NOT is_cross_border
GROUP BY hour_local_ts, country, card_type, acquirer_id;

-- COMMAND ----------

-- ds_decline_reasons: first-attempt failure reasons by acquirer (bar chart)
SELECT business_date, country, card_type, acquirer_id, reason_code, reason, decline_type, retryable,
       sum(attempts) AS failed_attempts
FROM workspace.approval_copilot.gold_decline_reasons_daily
WHERE attempt_seq = 1
GROUP BY ALL;

-- COMMAND ----------

-- ds_cascade_daily: failed first attempts, retries on a second acquirer (cascade) and recoveries, per day
-- and first acquirer. Counts, not rates: the charts group the days by week and compute the cascade rate
-- and the retry recovery rate as custom calculations, so every filter keeps the rates correct.
SELECT business_date, country, card_type, acquirer_id,
       sum(first_attempt_failures) AS failures,
       sum(cascaded) AS cascaded,
       sum(recovered) AS recovered
FROM workspace.approval_copilot.gold_cascade_daily
GROUP BY business_date, country, card_type, acquirer_id;

-- COMMAND ----------

-- ds_funnel: intents -> risk -> 3DS -> sent -> approved
SELECT business_date, country, card_type,
       sum(payment_intents) AS intents, sum(blocked_by_risk) AS blocked_by_risk,
       sum(abandoned_3ds) AS abandoned_3ds, sum(sent_to_acquirer) AS sent_to_acquirer, sum(approved) AS approved,
       round(sum(blocked_by_risk) / sum(payment_intents), 4) AS risk_block_rate,
       round(sum(approved) / sum(payment_intents), 4) AS conversion_rate
FROM workspace.approval_copilot.gold_funnel_daily
GROUP BY business_date, country, card_type;

-- COMMAND ----------

-- ds_cost_vs_approval: acquirer economics per country and card type over the whole period
-- (scatter: fee vs approval; the date filter does not apply)
SELECT country, card_type, acquirer_id,
       sum(payments_sent) AS payments_sent,
       round(sum(final_approved) / sum(payments_sent), 4) AS final_approval_rate,
       round(10000 * sum(fees_usd) / nullif(sum(approved_usd), 0), 1) AS effective_fee_bps
FROM workspace.approval_copilot.gold_approval_daily
WHERE NOT is_cross_border
GROUP BY country, card_type, acquirer_id;

-- COMMAND ----------

-- ds_anomalies: flagged days
SELECT * FROM workspace.approval_copilot.gold_anomalies_daily WHERE is_anomaly ORDER BY business_date DESC;

-- COMMAND ----------

-- ds_cascade_alerts: days when the retry rate of an acquirer/reason pair collapsed (the early warning)
SELECT business_date, country, acquirer_id, first_reason_code, failures, cascaded,
       cascade_rate, baseline_cascade_rate, mapping_versions
FROM workspace.approval_copilot.gold_cascade_alerts_daily
WHERE is_alert;

-- COMMAND ----------

-- ds_change_log: production changes (not filtered by country: scope says what each change touches)
SELECT change_id, changed_at_utc, change_type, scope, description
FROM workspace.approval_copilot.gold_change_log
ORDER BY changed_at_utc DESC;

-- COMMAND ----------

-- ds_data_quality: what the pipeline rejected and why
SELECT * FROM workspace.approval_copilot.gold_dq_summary ORDER BY rejected_rows DESC;
