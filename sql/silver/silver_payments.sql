-- One row per payment: the intent, its first authorization attempt, the cascade
-- attempt on a second acquirer (if any) and the final outcome.
WITH first_att AS (
  SELECT * FROM silver_auth_attempts WHERE attempt_seq = 1
),
cascade_att AS (
  SELECT * FROM silver_auth_attempts WHERE attempt_seq = 2
),
orchestrator_map AS (
  SELECT DISTINCT raw_status, canonical_status
  FROM ref_decline_code_map
  WHERE source_system = 'ORCHESTRATOR'
),
joined AS (
  SELECT
    i.payment_id,
    coalesce(i.merchant_id, 'UNKNOWN') AS merchant_id,
    coalesce(m.segment, 'unknown') AS merchant_segment,
    m.mcc,
    i.country,
    i.currency,
    i.amount,
    round(i.amount / fx.usd_rate, 2) AS amount_usd,
    i.card_brand,
    i.card_type,
    i.issuer_id,
    i.is_cross_border,
    i.installments,
    i.three_ds_requested,
    i.three_ds_flow,
    i.channel,
    i.created_ts_utc,
    from_utc_timestamp(i.created_ts_utc, tz.time_zone) AS created_ts_local,
    i.orchestrator_status,
    i.routing_rule_version,
    f.payment_id IS NOT NULL AS sent_to_acquirer,
    i.orchestrator_status = 'ROUTED' AND f.payment_id IS NULL AS missing_attempt_data,
    f.acquirer_id AS first_acquirer_id,
    f.canonical_status AS first_status,
    f.iso_code AS first_iso_code,
    f.reason AS first_reason,
    f.decline_type AS first_decline_type,
    f.retryable AS first_retryable,
    f.mapping_version AS first_mapping_version,
    f.latency_ms AS first_latency_ms,
    f.is_3ds_authenticated,
    s.payment_id IS NOT NULL AS is_cascaded,
    s.acquirer_id AS cascade_acquirer_id,
    s.canonical_status AS cascade_status,
    s.iso_code AS cascade_iso_code,
    coalesce(f.is_authorized, false) AS first_attempt_approved,
    coalesce(s.is_authorized, false) AS cascade_approved,
    CASE
      WHEN i.orchestrator_status <> 'ROUTED' THEN om.canonical_status
      ELSE coalesce(s.canonical_status, f.canonical_status)
    END AS final_status
  FROM silver_payment_intents i
  JOIN ref_country_tz tz ON tz.country = i.country
  LEFT JOIN first_att f ON f.payment_id = i.payment_id
  LEFT JOIN cascade_att s ON s.payment_id = i.payment_id
  LEFT JOIN ref_merchants m ON m.merchant_id = i.merchant_id
  LEFT JOIN ref_fx_rates fx ON fx.currency = i.currency
  LEFT JOIN orchestrator_map om ON om.raw_status = i.orchestrator_status
),
outcomes AS (
  SELECT
    j.*,
    to_date(j.created_ts_local) AS business_date,
    hour(j.created_ts_local) AS hour_local,
    j.first_attempt_approved OR j.cascade_approved AS final_approved,
    NOT j.first_attempt_approved AND j.cascade_approved AS recovered_by_cascade,
    CASE
      WHEN j.first_attempt_approved THEN j.first_acquirer_id
      WHEN j.cascade_approved THEN j.cascade_acquirer_id
    END AS approving_acquirer_id
  FROM joined j
)
SELECT
  o.*,
  CASE WHEN o.final_approved THEN o.amount_usd ELSE 0 END AS approved_usd,
  round(CASE WHEN o.final_approved THEN o.amount_usd * a.fee_bps / 10000 ELSE 0 END, 4) AS fee_usd
FROM outcomes o
LEFT JOIN ref_acquirers a ON a.acquirer_id = o.approving_acquirer_id
