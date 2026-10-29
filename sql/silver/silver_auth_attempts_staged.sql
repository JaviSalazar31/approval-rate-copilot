-- Harmonizes three acquirer formats into one schema, removes webhook re-deliveries,
-- and maps each raw (status, code) pair to a canonical status using the mapping
-- version that was active when the attempt happened.
WITH a AS (
  SELECT
    'A' AS acquirer_id,
    event_id AS source_event_id,
    payment_ref AS payment_id,
    merchant_ref AS merchant_id,
    CAST(created_at_utc AS TIMESTAMP) AS attempt_ts_utc,            -- ISO-8601 with Z
    CAST(received_at_utc AS TIMESTAMP) AS received_ts_utc,
    CAST(amount AS DECIMAL(18, 2)) AS amount,
    currency,
    country,
    lower(card_brand) AS card_brand,
    lower(card_type) AS card_type,
    issuer_ref AS issuer_id,
    CAST(is_3ds AS BOOLEAN) AS is_3ds_authenticated,
    CAST(installments AS INT) AS installments,
    status AS raw_status,
    coalesce(response_code, '') AS raw_code,
    CAST(latency_ms AS INT) AS latency_ms,
    CAST(attempt_seq AS INT) AS attempt_seq
  FROM bronze_acquirer_a
),
b AS (
  SELECT
    'B' AS acquirer_id,
    id AS source_event_id,
    order_id AS payment_id,
    merchant AS merchant_id,
    -- local "dd/MM/yyyy HH:mm:ss" + offset "-05:00" -> ISO-8601 with offset -> UTC instant
    CAST(concat(substr(local_datetime, 7, 4), '-', substr(local_datetime, 4, 2), '-', substr(local_datetime, 1, 2),
                'T', substr(local_datetime, 12, 8), tz_offset) AS TIMESTAMP) AS attempt_ts_utc,
    CAST(NULL AS TIMESTAMP) AS received_ts_utc,
    CAST(CAST(amount_minor AS DECIMAL(20, 0)) / 100 AS DECIMAL(18, 2)) AS amount,  -- minor units
    ccy AS currency,
    ctry AS country,
    CASE upper(brand) WHEN 'MC' THEN 'mastercard' ELSE lower(brand) END AS card_brand,
    lower(funding) AS card_type,
    issuer AS issuer_id,
    auth_3ds = 'Y' AS is_3ds_authenticated,
    CAST(cuotas AS INT) AS installments,
    result AS raw_status,
    coalesce(reason_code, '') AS raw_code,
    CAST(resp_time_ms AS INT) AS latency_ms,
    CAST(try_no AS INT) AS attempt_seq
  FROM bronze_acquirer_b
),
c AS (
  SELECT
    'C' AS acquirer_id,
    transacao_id AS source_event_id,
    pedido_id AS payment_id,
    loja_id AS merchant_id,
    timestamp_millis(CAST(timestamp_ms AS BIGINT)) AS attempt_ts_utc,  -- epoch millis
    CAST(NULL AS TIMESTAMP) AS received_ts_utc,
    CAST(replace(valor, ',', '.') AS DECIMAL(18, 2)) AS amount,          -- comma decimals
    coalesce(nullif(moeda, ''),
             CASE pais WHEN 'BR' THEN 'BRL' WHEN 'MX' THEN 'MXN' WHEN 'CO' THEN 'COP' END) AS currency,
    pais AS country,
    lower(bandeira) AS card_brand,
    CASE tipo_cartao WHEN 'CREDITO' THEN 'credit' WHEN 'DEBITO' THEN 'debit' END AS card_type,
    emissor AS issuer_id,
    autenticado_3ds = '1' AS is_3ds_authenticated,
    CAST(parcelas AS INT) AS installments,
    situacao AS raw_status,
    coalesce(codigo_retorno, '') AS raw_code,
    CAST(tempo_resposta_ms AS INT) AS latency_ms,
    CAST(tentativa AS INT) AS attempt_seq
  FROM bronze_acquirer_c
),
unioned AS (
  SELECT * FROM a
  UNION ALL SELECT * FROM b
  UNION ALL SELECT * FROM c
),
ranked AS (
  -- Acquirer A re-delivers some webhooks: keep the first copy of each event
  SELECT
    u.*,
    row_number() OVER (PARTITION BY acquirer_id, source_event_id
                       ORDER BY received_ts_utc NULLS LAST) AS delivery_rank
  FROM unioned u
),
deduplicated AS (
  SELECT
    acquirer_id, source_event_id, payment_id, merchant_id, attempt_ts_utc, received_ts_utc,
    amount, currency, country, card_brand, card_type, issuer_id, is_3ds_authenticated,
    installments, raw_status, raw_code, latency_ms, attempt_seq
  FROM ranked
  WHERE delivery_rank = 1
)
SELECT
  d.*,
  m.mapping_version,
  m.canonical_status,
  m.iso_code,
  m.reason,
  m.decline_type,
  m.retryable,
  m.canonical_status IN ('APPROVED', 'REVERSED') AS is_authorized
FROM deduplicated d
LEFT JOIN ref_decline_code_map m
  ON m.source_system = d.acquirer_id
 AND m.raw_status = d.raw_status
 AND m.raw_code = d.raw_code
 AND d.attempt_ts_utc >= m.valid_from_utc
 AND d.attempt_ts_utc < m.valid_to_utc
