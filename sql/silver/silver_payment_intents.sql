-- One row per payment intent logged by the orchestrator, typed and cleaned.
-- Timestamps arrive as UTC strings without an offset; we make the offset explicit
-- so the result does not depend on the session time zone.
SELECT
  payment_id,
  nullif(merchant_id, '') AS merchant_id,
  CAST(concat(replace(created_at_utc, ' ', 'T'), 'Z') AS TIMESTAMP) AS created_ts_utc,
  country,
  currency,
  CAST(amount AS DECIMAL(18, 2)) AS amount,
  lower(card_brand) AS card_brand,
  card_type,
  issuer_id,
  CAST(is_cross_border AS BOOLEAN) AS is_cross_border,
  CAST(installments AS INT) AS installments,
  CAST(three_ds_requested AS BOOLEAN) AS three_ds_requested,
  nullif(three_ds_flow, '') AS three_ds_flow,
  orchestrator_status,
  nullif(routing_rule_version, '') AS routing_rule_version,
  nullif(first_acquirer, '') AS first_acquirer_id,
  channel
FROM bronze_orchestrator
