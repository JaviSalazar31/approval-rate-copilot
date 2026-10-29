-- Did failed first attempts get retried on another acquirer, and did the retries work?
-- Shows how the normalization map classified each reason in each period.
WITH scope AS (
  SELECT *
  FROM {schema}silver_payments
  WHERE sent_to_acquirer
    AND NOT is_cross_border
    AND NOT first_attempt_approved
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
cw AS (
  SELECT first_acquirer_id AS acquirer_id, coalesce(first_iso_code, first_status) AS reason_code,
         count(*) AS failures, sum(CAST(is_cascaded AS INT)) AS cascaded,
         sum(CAST(recovered_by_cascade AS INT)) AS recovered,
         concat_ws(',', sort_array(collect_set(first_mapping_version))) AS mapping_versions,
         concat_ws(',', sort_array(collect_set(first_decline_type))) AS mapped_as
  FROM win GROUP BY first_acquirer_id, coalesce(first_iso_code, first_status)
),
cb AS (
  SELECT first_acquirer_id AS acquirer_id, coalesce(first_iso_code, first_status) AS reason_code,
         count(*) AS failures, sum(CAST(is_cascaded AS INT)) AS cascaded,
         sum(CAST(recovered_by_cascade AS INT)) AS recovered,
         concat_ws(',', sort_array(collect_set(first_mapping_version))) AS mapping_versions,
         concat_ws(',', sort_array(collect_set(first_decline_type))) AS mapped_as
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
  w.mapping_versions AS window_mapping_versions
FROM cw w
FULL OUTER JOIN cb b ON w.acquirer_id = b.acquirer_id AND w.reason_code = b.reason_code
