-- Payment funnel: intents -> risk -> 3DS -> sent to acquirer -> approved.
SELECT
  business_date,
  country,
  card_type,
  count(*) AS payment_intents,
  sum(CAST(final_status = 'BLOCKED_BY_RISK' AS INT)) AS blocked_by_risk,
  sum(CAST(final_status = 'ABANDONED' AS INT)) AS abandoned_3ds,
  sum(CAST(sent_to_acquirer AS INT)) AS sent_to_acquirer,
  sum(CAST(missing_attempt_data AS INT)) AS routed_without_valid_attempt,
  sum(CAST(final_approved AS INT)) AS approved,
  round(sum(CAST(final_approved AS INT)) / count(*), 4) AS conversion_rate,
  round(sum(CAST(final_status = 'BLOCKED_BY_RISK' AS INT)) / count(*), 4) AS risk_block_rate
FROM silver_payments
GROUP BY business_date, country, card_type
