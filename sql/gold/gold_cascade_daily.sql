-- Cascade health: of the payments whose first attempt failed, how many were retried
-- on another acquirer and how many of those retries were approved.
SELECT
  business_date,
  country,
  card_type,
  first_acquirer_id AS acquirer_id,
  coalesce(first_iso_code, first_status) AS first_reason_code,
  first_reason,
  first_decline_type,
  first_retryable,
  first_mapping_version,
  count(*) AS first_attempt_failures,
  sum(CAST(is_cascaded AS INT)) AS cascaded,
  sum(CAST(recovered_by_cascade AS INT)) AS recovered
FROM silver_payments
WHERE sent_to_acquirer AND NOT first_attempt_approved
GROUP BY 1, 2, 3, 4, 5, 6, 7, 8, 9
