-- Attempt-level failures by canonical reason, including how the normalization map
-- classified each code at that time (decline_type, retryable, mapping_version).
SELECT
  to_date(from_utc_timestamp(a.attempt_ts_utc, tz.time_zone)) AS business_date,
  a.country,
  a.card_type,
  a.acquirer_id,
  a.attempt_seq,
  a.canonical_status,
  coalesce(a.iso_code, a.canonical_status) AS reason_code,
  a.reason,
  a.decline_type,
  a.retryable,
  a.mapping_version,
  count(*) AS attempts
FROM silver_auth_attempts a
JOIN ref_country_tz tz ON tz.country = a.country
WHERE NOT a.is_authorized
GROUP BY 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11
