-- Which first-attempt failure reasons grew or shrank, per acquirer, as % of payments.
WITH scope AS (
  SELECT *
  FROM {schema}silver_payments
  WHERE sent_to_acquirer
    AND NOT is_cross_border
    AND country = ${p_country}
    AND (${p_card_type} IS NULL OR card_type = ${p_card_type})
),
win AS (
  SELECT * FROM scope
  WHERE created_ts_local >= ${p_start_local} AND created_ts_local < ${p_end_local}
),
base AS (
  SELECT * FROM scope
  WHERE created_ts_local >= timestampadd(DAY, -${p_baseline_days}, ${p_start_local})
    AND created_ts_local < ${p_start_local}
),
fw AS (
  SELECT first_acquirer_id AS acquirer_id, coalesce(first_iso_code, first_status) AS reason_code,
         max(first_reason) AS reason, count(*) AS n
  FROM win WHERE NOT first_attempt_approved
  GROUP BY first_acquirer_id, coalesce(first_iso_code, first_status)
),
fb AS (
  SELECT first_acquirer_id AS acquirer_id, coalesce(first_iso_code, first_status) AS reason_code,
         max(first_reason) AS reason, count(*) AS n
  FROM base WHERE NOT first_attempt_approved
  GROUP BY first_acquirer_id, coalesce(first_iso_code, first_status)
),
totals AS (
  SELECT (SELECT count(*) FROM win) AS n_win, (SELECT count(*) FROM base) AS n_base
)
SELECT
  coalesce(w.acquirer_id, b.acquirer_id) AS acquirer_id,
  coalesce(w.reason_code, b.reason_code) AS reason_code,
  coalesce(w.reason, b.reason) AS reason,
  coalesce(b.n, 0) AS baseline_failures,
  coalesce(w.n, 0) AS window_failures,
  round(100 * coalesce(b.n, 0) / nullif(t.n_base, 0), 2) AS baseline_pct_of_payments,
  round(100 * coalesce(w.n, 0) / nullif(t.n_win, 0), 2) AS window_pct_of_payments,
  round(100 * (coalesce(w.n, 0) / nullif(t.n_win, 0) - coalesce(b.n, 0) / nullif(t.n_base, 0)), 2) AS delta_pp
FROM fw w
FULL OUTER JOIN fb b ON w.acquirer_id = b.acquirer_id AND w.reason_code = b.reason_code
CROSS JOIN totals t
