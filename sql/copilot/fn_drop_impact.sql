-- Size of a drop: approvals and approved volume lost versus the baseline rate,
-- and acquirer fees paid in the window.
WITH scope AS (
  SELECT *
  FROM {schema}silver_payments
  WHERE sent_to_acquirer
    AND NOT is_cross_border
    AND country = ${p_country}
    AND (${p_card_type} IS NULL OR card_type = ${p_card_type})
),
win AS (
  SELECT * FROM scope
  WHERE created_ts_local >= ${p_start_local} AND created_ts_local < ${p_end_local}
),
base AS (
  SELECT * FROM scope
  WHERE created_ts_local >= timestampadd(DAY, -${p_baseline_days}, ${p_start_local})
    AND created_ts_local < ${p_start_local}
),
w AS (
  SELECT count(*) AS payments, sum(CAST(final_approved AS INT)) AS approved,
         sum(CAST(first_attempt_approved AS INT)) AS first_approved,
         sum(amount_usd) AS attempted_usd, sum(approved_usd) AS approved_usd, sum(fee_usd) AS fees_usd
  FROM win
),
b AS (
  SELECT count(*) AS payments, avg(CAST(final_approved AS INT)) AS final_rate,
         avg(CAST(first_attempt_approved AS INT)) AS first_rate,
         sum(approved_usd) / sum(amount_usd) AS volume_rate
  FROM base
)
SELECT
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
  round(w.fees_usd, 2) AS window_fees_usd
FROM w CROSS JOIN b
