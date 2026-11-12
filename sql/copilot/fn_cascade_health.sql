-- Did failed first attempts get retried on another acquirer, and did the retries work?
-- Shows how the normalization map classified each reason in each period, and whether it allowed a retry.
-- lost_recoveries: recoveries the window missed at the baseline recovery rate of the same acquirer and code.
-- map_stopped_retries: the map allowed a retry for this acquirer and code in the acquirer's own history (all its
-- traffic in the baseline period) and no longer does in the window, so a decline-map change stopped the retries.
-- The window is the one given, or the one p_incident_date reads from the change log (_incident_window.sql);
-- window_start_local and window_end_local say which.
WITH
{incident_window},
scope AS (
  SELECT *
  FROM {schema}silver_payments
  WHERE sent_to_acquirer
    AND NOT is_cross_border
    AND NOT first_attempt_approved
    AND country = ${p_country}
    AND (${p_card_type} IS NULL OR upper(trim(${p_card_type})) IN ('NULL', '') OR card_type = ${p_card_type})
    AND (${p_issuer} IS NULL OR upper(trim(${p_issuer})) IN ('NULL', '') OR issuer_id = ${p_issuer})
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
hist AS (
  SELECT first_acquirer_id AS acquirer_id, coalesce(first_iso_code, first_status) AS reason_code,
         concat_ws(',', sort_array(collect_set(first_mapping_version))) AS mapping_versions,
         concat_ws(',', sort_array(collect_set(CAST(first_retryable AS STRING)))) AS retryable
  FROM {schema}silver_payments CROSS JOIN bounds
  WHERE sent_to_acquirer AND NOT first_attempt_approved
    AND created_ts_local >= timestampadd(DAY, -${p_baseline_days}, bounds.ws)
    AND created_ts_local < bounds.ws
  GROUP BY first_acquirer_id, coalesce(first_iso_code, first_status)
),
cw AS (
  SELECT first_acquirer_id AS acquirer_id, coalesce(first_iso_code, first_status) AS reason_code,
         count(*) AS failures, sum(CAST(is_cascaded AS INT)) AS cascaded,
         sum(CAST(recovered_by_cascade AS INT)) AS recovered,
         concat_ws(',', sort_array(collect_set(first_mapping_version))) AS mapping_versions,
         concat_ws(',', sort_array(collect_set(first_decline_type))) AS mapped_as,
         concat_ws(',', sort_array(collect_set(CAST(first_retryable AS STRING)))) AS retryable
  FROM win GROUP BY first_acquirer_id, coalesce(first_iso_code, first_status)
),
cb AS (
  SELECT first_acquirer_id AS acquirer_id, coalesce(first_iso_code, first_status) AS reason_code,
         count(*) AS failures, sum(CAST(is_cascaded AS INT)) AS cascaded,
         sum(CAST(recovered_by_cascade AS INT)) AS recovered,
         concat_ws(',', sort_array(collect_set(first_mapping_version))) AS mapping_versions,
         concat_ws(',', sort_array(collect_set(first_decline_type))) AS mapped_as,
         concat_ws(',', sort_array(collect_set(CAST(first_retryable AS STRING)))) AS retryable
  FROM base GROUP BY first_acquirer_id, coalesce(first_iso_code, first_status)
)
SELECT
  coalesce(w.acquirer_id, b.acquirer_id) AS acquirer_id,
  coalesce(w.reason_code, b.reason_code) AS reason_code,
  coalesce(b.failures, 0) AS baseline_failures,
  round(b.cascaded / nullif(b.failures, 0), 4) AS baseline_cascade_rate,
  round(b.recovered / nullif(b.failures, 0), 4) AS baseline_recovery_rate,
  b.mapped_as AS baseline_mapped_as,
  coalesce(w.failures, 0) AS window_failures,
  round(w.cascaded / nullif(w.failures, 0), 4) AS window_cascade_rate,
  round(w.recovered / nullif(w.failures, 0), 4) AS window_recovery_rate,
  w.mapped_as AS window_mapped_as,
  w.mapping_versions AS window_mapping_versions,
  b.retryable AS baseline_retryable,
  w.retryable AS window_retryable,
  CASE WHEN b.failures > 0
       THEN round(coalesce(w.failures, 0) * b.recovered / b.failures - coalesce(w.recovered, 0), 1) END AS lost_recoveries,
  h.mapping_versions AS acquirer_history_mapping_versions,
  h.retryable AS acquirer_history_retryable,
  coalesce(w.failures, 0) > 0
    AND coalesce(array_contains(split(h.retryable, ','), 'true'), false)
    AND NOT coalesce(array_contains(split(w.retryable, ','), 'true'), false) AS map_stopped_retries,
  date_format(bounds.ws, 'yyyy-MM-dd HH:mm:ss') AS window_start_local,
  date_format(bounds.we, 'yyyy-MM-dd HH:mm:ss') AS window_end_local
FROM cw w
FULL OUTER JOIN cb b ON w.acquirer_id = b.acquirer_id AND w.reason_code = b.reason_code
LEFT JOIN hist h ON h.acquirer_id = coalesce(w.acquirer_id, b.acquirer_id)
                AND h.reason_code = coalesce(w.reason_code, b.reason_code)
CROSS JOIN bounds
