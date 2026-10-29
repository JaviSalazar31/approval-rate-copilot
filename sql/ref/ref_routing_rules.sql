SELECT
  routing_version,
  CAST(concat(replace(valid_from_utc, ' ', 'T'), 'Z') AS TIMESTAMP) AS valid_from_utc,
  CAST(concat(replace(valid_to_utc, ' ', 'T'), 'Z') AS TIMESTAMP) AS valid_to_utc,
  country,
  card_type,
  scope,
  acquirer_id,
  CAST(traffic_share AS DOUBLE) AS traffic_share
FROM {src:routing_rules}
