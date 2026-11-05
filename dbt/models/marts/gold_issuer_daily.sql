-- Daily approval by issuer and first acquirer, cross-border payments included.
SELECT
  business_date,
  country,
  issuer_id,
  card_type,
  first_acquirer_id AS acquirer_id,
  count(*) AS payments_sent,
  sum(CAST(final_approved AS INT)) AS final_approved,
  round(sum(CAST(final_approved AS INT)) / count(*), 4) AS final_approval_rate
FROM {{ ref('int_payments_sent') }}
GROUP BY business_date, country, issuer_id, card_type, first_acquirer_id
