SELECT
  change_id,
  CAST(concat(replace(changed_at_utc, ' ', 'T'), 'Z') AS TIMESTAMP) AS changed_at_utc,
  change_type,
  scope,
  description,
  owner_team
FROM {src:change_log}
