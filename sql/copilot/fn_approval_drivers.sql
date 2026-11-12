-- Explains a change in final approval between a baseline period and a window.
-- For each dimension value, splits its contribution into:
--   rate_effect_pp: the segment approved more/less than before (weighted by its share in the window)
--   mix_effect_pp:  traffic moved into/out of a segment that was better/worse than average
-- rate_effect_pp + mix_effect_pp across the values of one dimension = the window's whole change, which
-- fn_drop_impact returns as final_rate_change_pp. No row carries that total: each row is one segment.
-- p_dimension keeps one dimension (NULL keeps all six); rows come ordered from the lowest window rate.
-- pp_below_next: how far each value's window rate sits below the next higher value of the same dimension.
WITH scope AS (
  SELECT *
  FROM {schema}silver_payments
  WHERE sent_to_acquirer
    AND NOT is_cross_border
    AND country = ${p_country}
    AND (${p_card_type} IS NULL OR upper(trim(${p_card_type})) IN ('NULL', '') OR card_type = ${p_card_type})
),
win AS (
  SELECT * FROM scope
  WHERE created_ts_local >= ${p_start_local} AND created_ts_local < ${p_end_local}
),
base AS (
  SELECT * FROM scope
  WHERE created_ts_local >= timestampadd(DAY, -${p_baseline_days}, ${p_start_local})
    AND created_ts_local < ${p_start_local}
),
long_win AS (
  SELECT
    CAST(final_approved AS INT) AS ok,
    explode(map('acquirer', first_acquirer_id,
                'issuer', issuer_id,
                'card_brand', card_brand,
                'three_ds_authenticated', CAST(coalesce(is_3ds_authenticated, false) AS STRING),
                'merchant_segment', merchant_segment,
                'routing_rule_version', routing_rule_version)) AS (dimension, segment_value)
  FROM win
),
long_base AS (
  SELECT
    CAST(final_approved AS INT) AS ok,
    explode(map('acquirer', first_acquirer_id,
                'issuer', issuer_id,
                'card_brand', card_brand,
                'three_ds_authenticated', CAST(coalesce(is_3ds_authenticated, false) AS STRING),
                'merchant_segment', merchant_segment,
                'routing_rule_version', routing_rule_version)) AS (dimension, segment_value)
  FROM base
),
agg_win AS (
  SELECT dimension, segment_value, count(*) AS n, avg(ok) AS rate
  FROM long_win GROUP BY dimension, segment_value
),
agg_base AS (
  SELECT dimension, segment_value, count(*) AS n, avg(ok) AS rate
  FROM long_base GROUP BY dimension, segment_value
),
totals AS (
  SELECT
    (SELECT count(*) FROM win) AS n_win,
    (SELECT count(*) FROM base) AS n_base,
    (SELECT avg(CAST(final_approved AS INT)) FROM win) AS rate_win,
    (SELECT avg(CAST(final_approved AS INT)) FROM base) AS rate_base
)
SELECT
  coalesce(w.dimension, b.dimension) AS dimension,
  coalesce(w.segment_value, b.segment_value) AS segment_value,
  coalesce(b.n, 0) AS baseline_payments,
  coalesce(w.n, 0) AS window_payments,
  round(coalesce(b.n, 0) / nullif(t.n_base, 0), 4) AS baseline_share,
  round(coalesce(w.n, 0) / nullif(t.n_win, 0), 4) AS window_share,
  round(b.rate, 4) AS baseline_rate,
  round(w.rate, 4) AS window_rate,
  round(100 * (coalesce(w.n, 0) / nullif(t.n_win, 0)) * (coalesce(w.rate, 0) - coalesce(b.rate, t.rate_base)), 2) AS rate_effect_pp,
  round(100 * (coalesce(w.n, 0) / nullif(t.n_win, 0) - coalesce(b.n, 0) / nullif(t.n_base, 0))
            * (coalesce(b.rate, t.rate_base) - t.rate_base), 2) AS mix_effect_pp,
  round(100 * ((coalesce(w.n, 0) / nullif(t.n_win, 0)) * (coalesce(w.rate, 0) - coalesce(b.rate, t.rate_base))
             + (coalesce(w.n, 0) / nullif(t.n_win, 0) - coalesce(b.n, 0) / nullif(t.n_base, 0))
               * (coalesce(b.rate, t.rate_base) - t.rate_base)), 2) AS total_effect_pp,
  round(100 * (lead(round(w.rate, 4)) OVER (PARTITION BY coalesce(w.dimension, b.dimension)
                                              ORDER BY w.rate ASC NULLS LAST, coalesce(w.segment_value, b.segment_value))
               - round(w.rate, 4)), 2) AS pp_below_next
FROM agg_win w
FULL OUTER JOIN agg_base b
  ON w.dimension = b.dimension AND w.segment_value = b.segment_value
CROSS JOIN totals t
WHERE ${p_dimension} IS NULL OR upper(trim(${p_dimension})) IN ('NULL', '')
   OR coalesce(w.dimension, b.dimension) = ${p_dimension}
ORDER BY dimension, window_rate ASC NULLS LAST, segment_value
