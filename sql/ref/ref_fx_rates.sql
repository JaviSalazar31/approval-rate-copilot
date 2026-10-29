SELECT
  currency,
  CAST(usd_rate AS DOUBLE) AS usd_rate
FROM {src:fx_rates}
