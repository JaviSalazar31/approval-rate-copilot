SELECT
  merchant_id,
  country,
  segment,
  mcc,
  CAST(avg_ticket_usd AS DOUBLE) AS avg_ticket_usd,
  CAST(concat(replace(onboarded_utc, ' ', 'T'), ':00Z') AS TIMESTAMP) AS onboarded_ts_utc
FROM {src:merchants}
