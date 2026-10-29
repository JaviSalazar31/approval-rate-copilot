-- Data-quality summary per source and rule, to show what the pipeline rejected and why.
SELECT
  acquirer_id AS source_system,
  failed_rule,
  raw_status,
  raw_code,
  count(*) AS rejected_rows,
  min(attempt_ts_utc) AS first_seen_utc,
  max(attempt_ts_utc) AS last_seen_utc
FROM silver_dq_quarantine
GROUP BY acquirer_id, failed_rule, raw_status, raw_code
