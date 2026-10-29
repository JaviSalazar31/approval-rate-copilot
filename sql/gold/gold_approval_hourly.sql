-- Hourly approval per country, card type and first acquirer, in local time.
-- Used to pinpoint when an incident started and ended.
SELECT
  date_trunc('HOUR', created_ts_local) AS hour_local_ts,
  business_date,
  country,
  card_type,
  first_acquirer_id AS acquirer_id,
  is_cross_border,
  count(*) AS payments_sent,
  sum(CAST(first_attempt_approved AS INT)) AS first_attempt_approved,
  sum(CAST(final_approved AS INT)) AS final_approved,
  round(sum(CAST(final_approved AS INT)) / count(*), 4) AS final_approval_rate
FROM silver_payments
WHERE sent_to_acquirer
GROUP BY date_trunc('HOUR', created_ts_local), business_date, country, card_type, first_acquirer_id, is_cross_border
