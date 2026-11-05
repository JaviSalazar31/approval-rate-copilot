-- Attempt-level failures by canonical reason, including how the normalization map
-- classified each code at that time (decline_type, retryable, mapping_version).
SELECT
  business_date,
  country,
  card_type,
  acquirer_id,
  attempt_seq,
  canonical_status,
  coalesce(iso_code, canonical_status) AS reason_code,
  reason,
  decline_type,
  retryable,
  mapping_version,
  count(*) AS attempts
FROM {{ ref('int_failed_attempts') }}
GROUP BY 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11
