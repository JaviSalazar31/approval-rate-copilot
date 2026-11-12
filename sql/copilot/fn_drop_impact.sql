-- Size of a drop: approvals and approved volume lost versus the baseline rate, acquirer fees paid in the window,
-- and the payment funnel of the same segment (risk blocks, 3DS abandonment, conversion).
-- lost_approvals counts the payments sent to an acquirer; lost_conversions counts all payment intents, so it also
-- holds the intents stopped before any acquirer, which extra_risk_blocks and extra_3ds_abandons count.
-- The window is the one given. With p_incident_date and no start or end, it runs from the first production change
-- of that date in the country (gold_change_log, local time) to the next change in the country, usually a rollback,
-- a fix or a tuning; with no change that date, it is the calendar day.
WITH chg AS (
  SELECT change_id, change_type,
         CASE ${p_country} WHEN 'MX' THEN changed_at_mexico_city
                           WHEN 'CO' THEN changed_at_bogota
                           WHEN 'BR' THEN changed_at_sao_paulo END AS at_local
  FROM {schema}gold_change_log
  WHERE scope = 'ALL' OR array_contains(split(scope, ','), ${p_country})
),
day AS (
  SELECT try_cast(${p_incident_date} AS DATE) AS d
),
started AS (
  SELECT c.change_id, c.at_local
  FROM chg c CROSS JOIN day
  WHERE c.change_type NOT IN ('rollback', 'external_notice') AND to_date(c.at_local) = day.d
  ORDER BY c.at_local, c.change_id
  LIMIT 1
),
ended AS (
  SELECT c.change_id, c.at_local
  FROM chg c CROSS JOIN started s
  WHERE c.at_local > s.at_local
  ORDER BY c.at_local, c.change_id
  LIMIT 1
),
bounds AS (
  SELECT
    CASE WHEN ${p_start_local} IS NOT NULL THEN ${p_start_local}
         WHEN s.at_local IS NOT NULL THEN s.at_local
         ELSE CAST(day.d AS TIMESTAMP) END AS ws,
    CASE WHEN ${p_start_local} IS NOT NULL THEN ${p_end_local}
         WHEN s.at_local IS NOT NULL THEN coalesce(e.at_local, s.at_local + INTERVAL 24 HOURS)
         ELSE CAST(date_add(day.d, 1) AS TIMESTAMP) END AS we,
    CASE WHEN ${p_start_local} IS NULL AND s.at_local IS NULL AND day.d IS NOT NULL
         THEN 'calendar day: no change in the country that date' END AS fallback
  FROM day
  LEFT JOIN started s ON true
  LEFT JOIN ended e ON true
),
bounds_src AS (
  -- names the window the same way however it was passed: a window that runs from one change to another is the
  -- change log's
  SELECT ws, we,
    CASE WHEN ws IS NULL OR we IS NULL THEN 'no window: pass a start and end, or p_incident_date'
         WHEN fallback IS NOT NULL THEN fallback
         WHEN (SELECT min(change_id) FROM chg WHERE at_local = ws) IS NOT NULL
              AND (SELECT min(change_id) FROM chg WHERE at_local = we) IS NOT NULL
           THEN concat('change log: from ', (SELECT min(change_id) FROM chg WHERE at_local = ws),
                       ' to ', (SELECT min(change_id) FROM chg WHERE at_local = we))
         WHEN (SELECT min(change_id) FROM chg WHERE at_local = ws) IS NOT NULL AND we = ws + INTERVAL 24 HOURS
           THEN concat('change log: from ', (SELECT min(change_id) FROM chg WHERE at_local = ws), ' to 24 hours later')
         ELSE 'as given' END AS source
  FROM bounds
),
intents AS (
  SELECT p.*
  FROM {schema}silver_payments p
  WHERE NOT p.is_cross_border
    AND p.country = ${p_country}
    AND (${p_card_type} IS NULL OR upper(trim(${p_card_type})) IN ('NULL', '') OR p.card_type = ${p_card_type})
),
win_all AS (
  SELECT i.* FROM intents i CROSS JOIN bounds
  WHERE i.created_ts_local >= bounds.ws AND i.created_ts_local < bounds.we
),
base_all AS (
  SELECT i.* FROM intents i CROSS JOIN bounds
  WHERE i.created_ts_local >= timestampadd(DAY, -${p_baseline_days}, bounds.ws)
    AND i.created_ts_local < bounds.ws
),
w AS (
  SELECT count(*) AS payments, sum(CAST(final_approved AS INT)) AS approved,
         sum(CAST(first_attempt_approved AS INT)) AS first_approved,
         sum(amount_usd) AS attempted_usd, sum(approved_usd) AS approved_usd, sum(fee_usd) AS fees_usd
  FROM win_all WHERE sent_to_acquirer
),
b AS (
  SELECT count(*) AS payments, avg(CAST(final_approved AS INT)) AS final_rate,
         avg(CAST(first_attempt_approved AS INT)) AS first_rate,
         sum(approved_usd) / sum(amount_usd) AS volume_rate
  FROM base_all WHERE sent_to_acquirer
),
wf AS (
  SELECT count(*) AS intents, sum(CAST(final_approved AS INT)) AS approved,
         sum(CAST(final_status = 'BLOCKED_BY_RISK' AS INT)) AS blocked,
         sum(CAST(final_status = 'ABANDONED' AS INT)) AS abandoned
  FROM win_all
),
bf AS (
  SELECT count(*) AS intents, sum(CAST(final_approved AS INT)) AS approved,
         sum(CAST(final_status = 'BLOCKED_BY_RISK' AS INT)) AS blocked,
         sum(CAST(final_status = 'ABANDONED' AS INT)) AS abandoned
  FROM base_all
)
SELECT
  date_format(bounds.ws, 'yyyy-MM-dd HH:mm:ss') AS window_start_local,
  date_format(bounds.we, 'yyyy-MM-dd HH:mm:ss') AS window_end_local,
  bounds_src.source AS window_source,
  w.payments AS window_payments,
  b.payments AS baseline_payments,
  round(b.first_rate, 4) AS baseline_first_attempt_rate,
  round(w.first_approved / nullif(w.payments, 0), 4) AS window_first_attempt_rate,
  round(b.final_rate, 4) AS baseline_final_rate,
  round(w.approved / nullif(w.payments, 0), 4) AS window_final_rate,
  round(100 * (w.approved / nullif(w.payments, 0) - b.final_rate), 2) AS final_rate_change_pp,
  w.approved AS window_approvals,
  round(w.payments * b.final_rate) AS expected_approvals_at_baseline,
  round(w.payments * b.final_rate) - w.approved AS lost_approvals,
  round(w.attempted_usd, 2) AS window_attempted_usd,
  round(w.approved_usd, 2) AS window_approved_usd,
  round(w.attempted_usd * b.volume_rate - w.approved_usd, 2) AS lost_approved_usd,
  round(w.fees_usd, 2) AS window_fees_usd,
  round(bf.approved / nullif(bf.intents, 0), 4) AS baseline_conversion_rate,
  round(wf.approved / nullif(wf.intents, 0), 4) AS window_conversion_rate,
  round(100 * (wf.approved / nullif(wf.intents, 0) - bf.approved / nullif(bf.intents, 0)), 2) AS conversion_change_pp,
  round(bf.blocked / nullif(bf.intents, 0), 4) AS baseline_risk_block_rate,
  round(wf.blocked / nullif(wf.intents, 0), 4) AS window_risk_block_rate,
  round(bf.abandoned / nullif(bf.intents, 0), 4) AS baseline_3ds_abandon_rate,
  round(wf.abandoned / nullif(wf.intents, 0), 4) AS window_3ds_abandon_rate,
  wf.intents AS window_intents,
  round(wf.intents * bf.approved / nullif(bf.intents, 0)) - wf.approved AS lost_conversions,
  wf.blocked - round(wf.intents * bf.blocked / nullif(bf.intents, 0)) AS extra_risk_blocks,
  wf.abandoned - round(wf.intents * bf.abandoned / nullif(bf.intents, 0)) AS extra_3ds_abandons
FROM bounds_src CROSS JOIN bounds CROSS JOIN w CROSS JOIN b CROSS JOIN wf CROSS JOIN bf
