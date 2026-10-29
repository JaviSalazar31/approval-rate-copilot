-- Flags days where domestic final approval falls well below its trailing 28-day baseline.
-- Volume-aware: a drop must be large in percentage points AND unlikely given the day's
-- volume (binomial z-score), which avoids false alarms on low-volume days.
WITH daily AS (
  SELECT
    business_date,
    country,
    card_type,
    sum(payments_sent) AS payments_sent,
    sum(final_approved) AS final_approved,
    sum(final_approved) / sum(payments_sent) AS final_approval_rate
  FROM gold_approval_daily
  WHERE NOT is_cross_border
  GROUP BY business_date, country, card_type
),
windowed AS (
  SELECT
    d.*,
    avg(final_approval_rate) OVER w AS baseline_rate,
    stddev(final_approval_rate) OVER w AS baseline_std,
    count(*) OVER w AS baseline_days
  FROM daily d
  WINDOW w AS (PARTITION BY country, card_type ORDER BY business_date
               ROWS BETWEEN 28 PRECEDING AND 1 PRECEDING)
),
scored AS (
  SELECT
    *,
    (final_approval_rate - baseline_rate) / nullif(baseline_std, 0) AS z_history,
    (final_approval_rate - baseline_rate)
      / nullif(sqrt(baseline_rate * (1 - baseline_rate) / payments_sent), 0) AS z_binomial
  FROM windowed
)
SELECT
  business_date,
  country,
  card_type,
  payments_sent,
  final_approved,
  round(final_approval_rate, 4) AS final_approval_rate,
  round(baseline_rate, 4) AS baseline_rate,
  round((final_approval_rate - baseline_rate) * 100, 2) AS delta_pp,
  round(z_history, 2) AS z_history,
  round(z_binomial, 2) AS z_binomial,
  coalesce(baseline_days >= 14
           AND final_approval_rate - baseline_rate <= -0.03
           AND z_history <= -3
           AND z_binomial <= -4, false) AS is_anomaly
FROM scored
