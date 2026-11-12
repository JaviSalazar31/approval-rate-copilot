-- Explains a change in approval between a baseline period and a window.
-- For each dimension value, splits its contribution into:
--   rate_effect_pp: the segment approved more/less than before (weighted by its share in the window)
--   mix_effect_pp:  traffic moved into/out of a segment that was better/worse than average
-- rate_effect_pp + mix_effect_pp across the values of one dimension = the window's whole change in that rate
-- (for the final rate, fn_drop_impact's final_rate_change_pp). No row carries that total: each row is one segment.
-- p_dimension keeps one dimension (NULL keeps all six); rows come ordered from the lowest window rate.
-- pp_below_next: how far each value's window rate sits below the next higher value of the same dimension.
-- p_rate picks the rate: 'final' (the default: approved on the first attempt or on a retry) or 'first_attempt';
-- rate_type says which one the rows show. p_rank ('lowest' or 'highest') keeps the three values at that end of
-- each dimension, in ranking order, and gap_to_next_pp is how far each one sits from the next in that order.
-- A segment with no baseline traffic is valued at the whole baseline rate, so traffic that moved onto it shows
-- in its rate effect: baseline_share and window_share show the move.
WITH scope AS (
  SELECT *,
         CAST(CASE WHEN lower(trim(coalesce(${p_rate}, 'final'))) IN ('first_attempt', 'first attempt', 'first-attempt')
                   THEN first_attempt_approved ELSE final_approved END AS INT) AS ok
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
    ok,
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
    ok,
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
    (SELECT avg(ok) FROM win) AS rate_win,
    (SELECT avg(ok) FROM base) AS rate_base
),
seg AS (
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
               - round(w.rate, 4)), 2) AS pp_below_next,
  round(100 * (round(w.rate, 4) - lead(round(w.rate, 4)) OVER (PARTITION BY coalesce(w.dimension, b.dimension)
                                              ORDER BY w.rate DESC NULLS LAST, coalesce(w.segment_value, b.segment_value))),
        2) AS pp_above_next_lower,
  row_number() OVER (PARTITION BY coalesce(w.dimension, b.dimension)
                     ORDER BY w.rate ASC NULLS LAST, coalesce(w.segment_value, b.segment_value)) AS rank_lowest,
  row_number() OVER (PARTITION BY coalesce(w.dimension, b.dimension)
                     ORDER BY w.rate DESC NULLS LAST, coalesce(w.segment_value, b.segment_value)) AS rank_highest
FROM agg_win w
FULL OUTER JOIN agg_base b
  ON w.dimension = b.dimension AND w.segment_value = b.segment_value
CROSS JOIN totals t
WHERE ${p_dimension} IS NULL OR upper(trim(${p_dimension})) IN ('NULL', '')
   OR coalesce(w.dimension, b.dimension) = ${p_dimension}
)
SELECT
  dimension, segment_value, baseline_payments, window_payments, baseline_share, window_share, baseline_rate,
  window_rate, rate_effect_pp, mix_effect_pp, total_effect_pp, pp_below_next,
  CASE WHEN lower(trim(coalesce(${p_rate}, 'final'))) IN ('first_attempt', 'first attempt', 'first-attempt')
       THEN 'first_attempt' ELSE 'final' END AS rate_type,
  CASE WHEN lower(trim(coalesce(${p_rank}, ''))) = 'highest' THEN pp_above_next_lower ELSE pp_below_next END
    AS gap_to_next_pp
FROM seg
-- p_rank keeps the three values at one end of each dimension; any other value keeps every row
WHERE lower(trim(coalesce(${p_rank}, ''))) NOT IN ('lowest', 'highest')
   OR (lower(trim(${p_rank})) = 'lowest' AND rank_lowest <= 3)
   OR (lower(trim(${p_rank})) = 'highest' AND rank_highest <= 3)
ORDER BY dimension, CASE WHEN lower(trim(coalesce(${p_rank}, ''))) = 'highest' THEN rank_highest ELSE rank_lowest END
