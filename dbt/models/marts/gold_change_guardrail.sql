-- Follows every production change for its first 24 hours, hour by hour, in each segment it can affect
-- (country, card type, domestic or cross-border). Each hour adds up the payments sent since the change and compares
-- their final approval with the segment's approval over the 14 days before it. The guardrail fires when the drop is
-- at least 2 points, unlikely at that volume (binomial z-score of -3.5 or lower, which keeps the chance of a false alarm
-- in 24 hourly looks under about 0.6%) and based on at least 30 payments.
-- Scope: ALL is every segment, country codes are their segments, and an acquirer (A, B or C) is the segments it
-- served in those 14 days. Partner notices (external_notice) are left out: they are not changes of the orchestrator.
-- analysis/change_guardrail.py runs the same rule on other datasets and on days with no change.
WITH changes AS (
  SELECT change_id, change_type, scope, changed_at_mexico_city, changed_at_bogota, changed_at_sao_paulo
  FROM {{ ref('gold_change_log') }}
  WHERE change_type <> 'external_notice'
),
segments AS (
  SELECT DISTINCT country, card_type, is_cross_border
  FROM {{ ref('int_payments_sent') }}
),
changes_local AS (
  SELECT
    c.change_id,
    c.change_type,
    c.scope,
    s.country,
    s.card_type,
    s.is_cross_border,
    CASE s.country
      WHEN 'MX' THEN c.changed_at_mexico_city
      WHEN 'CO' THEN c.changed_at_bogota
      WHEN 'BR' THEN c.changed_at_sao_paulo
    END AS changed_at_local
  FROM changes c
  CROSS JOIN segments s
),
baseline AS (
  SELECT
    cl.change_id,
    cl.change_type,
    cl.scope,
    cl.country,
    cl.card_type,
    cl.is_cross_border,
    cl.changed_at_local,
    count(*) AS baseline_payments,
    sum(CAST(p.final_approved AS INT)) / count(*) AS baseline_rate,
    sum(p.approved_usd) / nullif(sum(p.amount_usd), 0) AS baseline_volume_rate,
    sum(p.fee_usd) / nullif(sum(p.approved_usd), 0) AS baseline_fee_rate,
    sum(CASE WHEN p.first_acquirer_id = cl.scope THEN 1 ELSE 0 END) AS scope_acquirer_payments
  FROM changes_local cl
  JOIN {{ ref('int_payments_sent') }} p
    ON p.country = cl.country
   AND p.card_type = cl.card_type
   AND p.is_cross_border = cl.is_cross_border
   AND p.created_ts_local >= cl.changed_at_local - INTERVAL 14 DAYS
   AND p.created_ts_local < cl.changed_at_local
  GROUP BY cl.change_id, cl.change_type, cl.scope, cl.country, cl.card_type, cl.is_cross_border,
           cl.changed_at_local
),
monitored AS (
  SELECT *
  FROM baseline
  WHERE scope = 'ALL'
     OR array_contains(split(scope, ','), country)
     OR scope_acquirer_payments > 0
),
after_change AS (
  SELECT
    m.change_id,
    m.country,
    m.card_type,
    m.is_cross_border,
    CAST(floor((unix_timestamp(p.created_ts_local) - unix_timestamp(m.changed_at_local)) / 3600) AS INT) + 1
      AS hour_number,
    CAST(p.final_approved AS INT) AS approved,
    p.amount_usd,
    p.approved_usd,
    p.fee_usd
  FROM monitored m
  JOIN {{ ref('int_payments_sent') }} p
    ON p.country = m.country
   AND p.card_type = m.card_type
   AND p.is_cross_border = m.is_cross_border
   AND p.created_ts_local >= m.changed_at_local
   AND p.created_ts_local < m.changed_at_local + INTERVAL 24 HOURS
),
hourly AS (
  SELECT
    change_id,
    country,
    card_type,
    is_cross_border,
    hour_number,
    count(*) AS payments_sent,
    sum(approved) AS final_approved,
    sum(amount_usd) AS attempted_usd,
    sum(approved_usd) AS approved_usd,
    sum(fee_usd) AS fees_usd
  FROM after_change
  GROUP BY change_id, country, card_type, is_cross_border, hour_number
),
cumulative AS (
  SELECT
    m.change_id,
    m.change_type,
    m.scope,
    m.country,
    m.card_type,
    m.is_cross_border,
    m.changed_at_local,
    m.baseline_payments,
    m.baseline_rate,
    m.baseline_volume_rate,
    m.baseline_fee_rate,
    h.hours_since_change,
    coalesce(sum(x.payments_sent), 0) AS payments_sent,
    coalesce(sum(x.final_approved), 0) AS final_approved,
    coalesce(sum(x.attempted_usd), 0) AS attempted_usd,
    coalesce(sum(x.approved_usd), 0) AS approved_usd,
    coalesce(sum(x.fees_usd), 0) AS fees_usd
  FROM monitored m
  CROSS JOIN (SELECT explode(sequence(1, 24)) AS hours_since_change) h
  LEFT JOIN hourly x
    ON x.change_id = m.change_id
   AND x.country = m.country
   AND x.card_type = m.card_type
   AND x.is_cross_border = m.is_cross_border
   AND x.hour_number <= h.hours_since_change
  GROUP BY m.change_id, m.change_type, m.scope, m.country, m.card_type, m.is_cross_border, m.changed_at_local,
           m.baseline_payments, m.baseline_rate, m.baseline_volume_rate, m.baseline_fee_rate, h.hours_since_change
),
scored AS (
  SELECT
    *,
    final_approved / nullif(payments_sent, 0) AS final_approval_rate,
    (final_approved - payments_sent * baseline_rate)
      / nullif(sqrt(payments_sent * baseline_rate * (1 - baseline_rate)), 0) AS z
  FROM cumulative
),
flagged AS (
  SELECT
    *,
    coalesce(payments_sent >= 30
             AND final_approval_rate <= baseline_rate - 0.02
             AND z <= -3.5, false) AS fires
  FROM scored
)
SELECT
  change_id,
  change_type,
  scope,
  country,
  card_type,
  is_cross_border,
  changed_at_local,
  hours_since_change,
  baseline_payments,
  round(baseline_rate, 4) AS baseline_rate,
  payments_sent,
  final_approved,
  round(final_approval_rate, 4) AS final_approval_rate,
  round((final_approval_rate - baseline_rate) * 100, 2) AS rate_change_pp,
  round(z, 2) AS z_score,
  round(greatest(payments_sent * baseline_rate - final_approved, 0), 1) AS lost_approvals,
  round(greatest(attempted_usd * baseline_volume_rate - approved_usd, 0), 2) AS lost_approved_usd,
  round(approved_usd * baseline_fee_rate - fees_usd, 2) AS fees_saved_usd,
  fires,
  min(CASE WHEN fires THEN hours_since_change END)
    OVER (PARTITION BY change_id, country, card_type, is_cross_border) AS first_fire_hours
FROM flagged
