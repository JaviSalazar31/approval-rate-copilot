-- Change log with local times, so it can be lined up with local-time metrics.
SELECT
  change_id,
  changed_at_utc,
  from_utc_timestamp(changed_at_utc, 'America/Mexico_City') AS changed_at_mexico_city,
  from_utc_timestamp(changed_at_utc, 'America/Bogota') AS changed_at_bogota,
  from_utc_timestamp(changed_at_utc, 'America/Sao_Paulo') AS changed_at_sao_paulo,
  change_type,
  scope,
  description,
  owner_team
FROM ref_change_log
