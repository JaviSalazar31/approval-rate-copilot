SELECT
  issuer_id,
  issuer_country,
  CAST(market_share AS DOUBLE) AS market_share,
  issuer_type
FROM {src:issuers}
