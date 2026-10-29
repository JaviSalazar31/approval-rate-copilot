-- Watches the recovery mechanism itself: flags acquirer/reason pairs whose cascade rate
-- collapses versus its recent healthy history. A silent failure here barely moves daily approval,
-- so the outcome-based anomaly check would miss it.
--
-- The baseline uses only healthy days (cascade rate of at least 50%) among the previous 56 days with
-- failures. A plain rolling 28-day baseline (the first version) would learn the outage: after a week or two
-- of collapsed days it falls below the 50% the rule needs, and the alert switches itself off while the bug
-- is still live. Limit: it stops once the healthy days left in that window add up to fewer than 50
-- failures, about eight weeks into a collapse at 20 to 30 failures a day (ADR-08).
WITH daily AS (
  SELECT
    business_date,
    country,
    acquirer_id,
    first_reason_code,
    sum(first_attempt_failures) AS failures,
    sum(cascaded) AS cascaded,
    sum(cascaded) / sum(first_attempt_failures) AS cascade_rate,
    concat_ws(',', sort_array(collect_set(first_mapping_version))) AS mapping_versions
  FROM gold_cascade_daily
  GROUP BY business_date, country, acquirer_id, first_reason_code
),
windowed AS (
  SELECT
    d.*,
    sum(CASE WHEN cascade_rate >= 0.5 THEN cascaded END) OVER w
      / sum(CASE WHEN cascade_rate >= 0.5 THEN failures END) OVER w AS baseline_cascade_rate,
    sum(CASE WHEN cascade_rate >= 0.5 THEN failures END) OVER w AS baseline_failures
  FROM daily d
  WINDOW w AS (PARTITION BY country, acquirer_id, first_reason_code ORDER BY business_date
               ROWS BETWEEN 56 PRECEDING AND 1 PRECEDING)
)
SELECT
  business_date,
  country,
  acquirer_id,
  first_reason_code,
  failures,
  cascaded,
  round(cascade_rate, 4) AS cascade_rate,
  round(baseline_cascade_rate, 4) AS baseline_cascade_rate,
  mapping_versions,
  coalesce(baseline_failures >= 50
           AND failures >= 5
           AND baseline_cascade_rate >= 0.5
           AND cascade_rate <= baseline_cascade_rate - 0.5, false) AS is_alert
FROM windowed
