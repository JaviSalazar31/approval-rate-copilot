-- Which first-attempt failure reasons grew or shrank, per acquirer, as % of payments.
-- all_acquirers_*: the same code summed over acquirers. When traffic moved from one acquirer to another, the
-- acquirer that carried none of the segment in the baseline shows its codes growing from zero; these columns
-- show how the code itself moved.
-- The window is the one given, or the one p_incident_date reads from the change log (_incident_window.sql);
-- window_start_local and window_end_local say which.
WITH
{incident_window},
scope AS (
  SELECT *
  FROM {schema}silver_payments
  WHERE sent_to_acquirer
    AND NOT is_cross_border
    AND country = ${p_country}
    AND (${p_card_type} IS NULL OR upper(trim(${p_card_type})) IN ('NULL', '') OR card_type = ${p_card_type})
),
win AS (
  SELECT s.* FROM scope s CROSS JOIN bounds
  WHERE s.created_ts_local >= bounds.ws AND s.created_ts_local < bounds.we
),
base AS (
  SELECT s.* FROM scope s CROSS JOIN bounds
  WHERE s.created_ts_local >= timestampadd(DAY, -${p_baseline_days}, bounds.ws)
    AND s.created_ts_local < bounds.ws
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
  -- joins, not scalar subqueries: inside a Unity Catalog function, a subquery expression cannot read the
  -- function's arguments, and win and base depend on them
  SELECT tw.n_win, tb.n_base
  FROM (SELECT count(*) AS n_win FROM win) tw CROSS JOIN (SELECT count(*) AS n_base FROM base) tb
)
SELECT
  coalesce(w.acquirer_id, b.acquirer_id) AS acquirer_id,
  coalesce(w.reason_code, b.reason_code) AS reason_code,
  coalesce(w.reason, b.reason) AS reason,
  coalesce(b.n, 0) AS baseline_failures,
  coalesce(w.n, 0) AS window_failures,
  round(100 * coalesce(b.n, 0) / nullif(t.n_base, 0), 2) AS baseline_pct_of_payments,
  round(100 * coalesce(w.n, 0) / nullif(t.n_win, 0), 2) AS window_pct_of_payments,
  round(100 * (coalesce(w.n, 0) / nullif(t.n_win, 0) - coalesce(b.n, 0) / nullif(t.n_base, 0)), 2) AS delta_pp,
  round(100 * sum(coalesce(b.n, 0)) OVER (PARTITION BY coalesce(w.reason_code, b.reason_code))
            / nullif(t.n_base, 0), 2) AS all_acquirers_baseline_pct,
  round(100 * sum(coalesce(w.n, 0)) OVER (PARTITION BY coalesce(w.reason_code, b.reason_code))
            / nullif(t.n_win, 0), 2) AS all_acquirers_window_pct,
  round(100 * (sum(coalesce(w.n, 0)) OVER (PARTITION BY coalesce(w.reason_code, b.reason_code)) / nullif(t.n_win, 0)
             - sum(coalesce(b.n, 0)) OVER (PARTITION BY coalesce(w.reason_code, b.reason_code)) / nullif(t.n_base, 0)),
        2) AS all_acquirers_delta_pp,
  date_format(bounds.ws, 'yyyy-MM-dd HH:mm:ss') AS window_start_local,
  date_format(bounds.we, 'yyyy-MM-dd HH:mm:ss') AS window_end_local
FROM fw w
FULL OUTER JOIN fb b ON w.acquirer_id = b.acquirer_id AND w.reason_code = b.reason_code
CROSS JOIN totals t
CROSS JOIN bounds
