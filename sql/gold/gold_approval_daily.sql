-- Daily approval KPIs per country, card type and FIRST acquirer chosen by routing.
-- final_* metrics include approvals recovered by cascading to a second acquirer.
SELECT
  business_date,
  country,
  card_type,
  first_acquirer_id AS acquirer_id,
  is_cross_border,
  count(*) AS payments_sent,
  sum(CAST(first_attempt_approved AS INT)) AS first_attempt_approved,
  sum(CAST(final_approved AS INT)) AS final_approved,
  sum(CAST(is_cascaded AS INT)) AS cascaded,
  sum(CAST(recovered_by_cascade AS INT)) AS recovered_by_cascade,
  sum(CAST(first_status = 'DECLINED_SOFT' AS INT)) AS first_soft_declines,
  sum(CAST(first_status = 'DECLINED_HARD' AS INT)) AS first_hard_declines,
  sum(CAST(first_status IN ('TECHNICAL_ERROR', 'TIMEOUT') AS INT)) AS first_technical_failures,
  round(sum(amount_usd), 2) AS attempted_usd,
  round(sum(approved_usd), 2) AS approved_usd,
  round(sum(fee_usd), 2) AS fees_usd,
  percentile_approx(first_latency_ms, 0.95) AS p95_latency_ms,
  round(sum(CAST(first_attempt_approved AS INT)) / count(*), 4) AS first_attempt_approval_rate,
  round(sum(CAST(final_approved AS INT)) / count(*), 4) AS final_approval_rate,
  round(sum(approved_usd) / sum(amount_usd), 4) AS approved_volume_rate
FROM silver_payments
WHERE sent_to_acquirer
GROUP BY business_date, country, card_type, first_acquirer_id, is_cross_border
