SELECT
  acquirer_id,
  acquirer_name,
  countries,
  CAST(fee_bps AS INT) AS fee_bps,
  raw_format
FROM {src:acquirers}
