-- Size of a drop: approvals and approved volume lost versus the baseline rate, acquirer fees paid in the window,
-- and the payment funnel of the same segment (risk blocks, 3DS abandonment, conversion).
-- lost_approvals counts the payments sent to an acquirer; lost_conversions counts all payment intents, so it also
-- holds the intents stopped before any acquirer, which extra_risk_blocks and extra_3ds_abandons count.
-- lost_* and gained_* are never negative: a window that approved less than its baseline predicts shows the
-- shortfall in lost_*, one that approved more shows the excess in gained_*.
-- The window is the one given, or the one p_incident_date reads from the change log (_incident_window.sql).
WITH
{incident_window},
chg_at AS (
  SELECT at_local, min(change_id) AS change_id FROM chg GROUP BY at_local
),
bounds_src AS (
  -- names the window the same way however it was passed: a window that runs from one change to another is the
  -- change log's. Joins, not scalar subqueries: inside a Unity Catalog function, a subquery expression cannot
  -- read the function's arguments, and chg depends on them.
  SELECT b.ws, b.we,
    CASE WHEN b.ws IS NULL OR b.we IS NULL THEN 'no window: pass a start and end, or p_incident_date'
         WHEN b.fallback IS NOT NULL THEN b.fallback
         WHEN cs.change_id IS NOT NULL AND ce.change_id IS NOT NULL
           THEN concat('change log: from ', cs.change_id, ' to ', ce.change_id)
         WHEN cs.change_id IS NOT NULL AND b.we = b.ws + INTERVAL 24 HOURS
           THEN concat('change log: from ', cs.change_id, ' to 24 hours later')
         ELSE 'as given' END AS source
  FROM bounds b
  LEFT JOIN chg_at cs ON cs.at_local = b.ws
  LEFT JOIN chg_at ce ON ce.at_local = b.we
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
  greatest(round(w.payments * b.final_rate) - w.approved, 0) AS lost_approvals,
  greatest(w.approved - round(w.payments * b.final_rate), 0) AS gained_approvals,
  round(w.attempted_usd, 2) AS window_attempted_usd,
  round(w.approved_usd, 2) AS window_approved_usd,
  greatest(round(w.attempted_usd * b.volume_rate - w.approved_usd, 2), 0) AS lost_approved_usd,
  greatest(round(w.approved_usd - w.attempted_usd * b.volume_rate, 2), 0) AS gained_approved_usd,
  round(w.fees_usd, 2) AS window_fees_usd,
  round(bf.approved / nullif(bf.intents, 0), 4) AS baseline_conversion_rate,
  round(wf.approved / nullif(wf.intents, 0), 4) AS window_conversion_rate,
  round(100 * (wf.approved / nullif(wf.intents, 0) - bf.approved / nullif(bf.intents, 0)), 2) AS conversion_change_pp,
  round(bf.blocked / nullif(bf.intents, 0), 4) AS baseline_risk_block_rate,
  round(wf.blocked / nullif(wf.intents, 0), 4) AS window_risk_block_rate,
  round(bf.abandoned / nullif(bf.intents, 0), 4) AS baseline_3ds_abandon_rate,
  round(wf.abandoned / nullif(wf.intents, 0), 4) AS window_3ds_abandon_rate,
  wf.intents AS window_intents,
  greatest(round(wf.intents * bf.approved / nullif(bf.intents, 0)) - wf.approved, 0) AS lost_conversions,
  greatest(wf.approved - round(wf.intents * bf.approved / nullif(bf.intents, 0)), 0) AS gained_conversions,
  wf.blocked - round(wf.intents * bf.blocked / nullif(bf.intents, 0)) AS extra_risk_blocks,
  wf.abandoned - round(wf.intents * bf.abandoned / nullif(bf.intents, 0)) AS extra_3ds_abandons
FROM bounds_src CROSS JOIN bounds CROSS JOIN w CROSS JOIN b CROSS JOIN wf CROSS JOIN bf
