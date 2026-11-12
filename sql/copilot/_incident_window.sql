-- Shared window of the copilot functions, inserted where a function body says {incident_window}.
-- A start and end, when passed, are the window. With p_incident_date and no start, the window runs from the
-- first production change of that date that affects the country (gold_change_log, local time: the country's own
-- changes, global ones and those of an acquirer that serves it; rollbacks and partner notices do not start one)
-- to the next such change, usually a rollback, a fix or a tuning, or 24 hours if none follows. With no change
-- that date, it is the calendar day.
chg AS (
  SELECT c.change_id, c.change_type,
         CASE ${p_country} WHEN 'MX' THEN c.changed_at_mexico_city
                           WHEN 'CO' THEN c.changed_at_bogota
                           WHEN 'BR' THEN c.changed_at_sao_paulo END AS at_local
  FROM {schema}gold_change_log c
  LEFT JOIN (SELECT acquirer_id FROM {schema}ref_acquirers
             WHERE array_contains(split(countries, ','), ${p_country})) a
    ON c.scope = a.acquirer_id
  WHERE c.scope = 'ALL' OR array_contains(split(c.scope, ','), ${p_country}) OR a.acquirer_id IS NOT NULL
),
day AS (
  SELECT try_cast(${p_incident_date} AS DATE) AS d
),
started AS (
  SELECT c.change_id, c.at_local
  FROM chg c CROSS JOIN day
  WHERE c.change_type NOT IN ('rollback', 'external_notice') AND to_date(c.at_local) = day.d
  ORDER BY c.at_local, c.change_id
  LIMIT 1
),
ended AS (
  SELECT c.change_id, c.at_local
  FROM chg c CROSS JOIN started s
  WHERE c.at_local > s.at_local
  ORDER BY c.at_local, c.change_id
  LIMIT 1
),
bounds AS (
  SELECT
    CASE WHEN ${p_start_local} IS NOT NULL THEN ${p_start_local}
         WHEN s.at_local IS NOT NULL THEN s.at_local
         ELSE CAST(day.d AS TIMESTAMP) END AS ws,
    CASE WHEN ${p_start_local} IS NOT NULL THEN ${p_end_local}
         WHEN s.at_local IS NOT NULL THEN coalesce(e.at_local, s.at_local + INTERVAL 24 HOURS)
         ELSE CAST(date_add(day.d, 1) AS TIMESTAMP) END AS we,
    CASE WHEN ${p_start_local} IS NULL AND s.at_local IS NULL AND day.d IS NOT NULL
         THEN 'calendar day: no change in the country that date' END AS fallback
  FROM day
  LEFT JOIN started s ON true
  LEFT JOIN ended e ON true
)
