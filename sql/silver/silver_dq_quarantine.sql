-- Rows rejected by the data-quality rules, with the first rule they failed.
-- Kept for investigation instead of being silently dropped.
SELECT
  s.*,
  CASE
    WHEN s.payment_id IS NULL THEN 'has_payment_id'
    WHEN s.attempt_ts_utc IS NULL THEN 'valid_timestamp'
    WHEN NOT coalesce(s.amount > 0, false) THEN 'valid_amount'
    WHEN s.canonical_status IS NULL THEN 'mapped_code'
  END AS failed_rule
FROM silver_auth_attempts_staged s
WHERE NOT (
  s.payment_id IS NOT NULL
  AND s.attempt_ts_utc IS NOT NULL
  AND coalesce(s.amount > 0, false)
  AND s.canonical_status IS NOT NULL
)
