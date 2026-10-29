-- Production changes that could explain a window: same country, an acquirer used in
-- that country, or global changes, from N hours before the window until its end.
SELECT
  c.change_id,
  c.changed_at_utc,
  from_utc_timestamp(c.changed_at_utc, tz.time_zone) AS changed_at_local,
  c.change_type,
  c.scope,
  c.description,
  round((unix_timestamp(from_utc_timestamp(c.changed_at_utc, tz.time_zone))
         - unix_timestamp(${p_start_local})) / 3600, 1) AS hours_from_window_start
FROM {schema}ref_change_log c
CROSS JOIN (SELECT time_zone FROM {schema}ref_country_tz WHERE country = ${p_country}) tz
WHERE (c.scope = 'ALL'
       OR array_contains(split(c.scope, ','), ${p_country})
       OR c.scope IN (SELECT acquirer_id FROM {schema}ref_acquirers
                      WHERE array_contains(split(countries, ','), ${p_country})))
  AND from_utc_timestamp(c.changed_at_utc, tz.time_zone)
        >= timestampadd(HOUR, -${p_hours_before}, ${p_start_local})
  AND from_utc_timestamp(c.changed_at_utc, tz.time_zone) < ${p_end_local}
