SELECT
  business_date,
  country,
  issuer_id,
  card_type,
  first_acquirer_id AS acquirer_id,
  count(*) AS payments_sent,
  sum(CAST(final_approved AS INT)) AS final_approved,
  round(sum(CAST(final_approved AS INT)) / count(*), 4) AS final_approval_rate
FROM silver_payments
WHERE sent_to_acquirer
GROUP BY business_date, country, issuer_id, card_type, first_acquirer_id
