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
-- p_segment_values keeps only the values it names (for example 'A,B' for two acquirers), and the gaps and ranks are
-- computed among them. Text arguments are read without quotes or case ('''final''' is final, 'NULL' is none).
-- The window is the one given, or the one p_incident_date reads from the change log (_incident_window.sql);
-- window_start_local and window_end_local say which.
-- A segment with no baseline traffic is valued at the whole baseline rate, so traffic that moved onto it shows
-- in its rate effect: baseline_share and window_share show the move.
WITH
{incident_window},
prm AS (
  SELECT lower(trim(translate(coalesce(${p_rate}, ''), concat(char(39), char(34)), ''))) AS rate,
         lower(trim(translate(coalesce(${p_rank}, ''), concat(char(39), char(34)), ''))) AS rnk,
         lower(trim(translate(coalesce(${p_dimension}, ''), concat(char(39), char(34)), ''))) AS dim,
         lower(trim(translate(coalesce(${p_segment_values}, ''), concat(char(39), char(34)), ''))) AS segs
),
arg AS (
  SELECT rate IN ('first_attempt', 'first attempt', 'first-attempt') AS first_attempt,
         rnk,
         CASE WHEN dim IN ('', 'null') THEN NULL ELSE dim END AS dim,
         CASE WHEN segs IN ('', 'null') THEN NULL
              ELSE transform(split(segs, ','), x -> regexp_replace(trim(x), '^acquirer[ _]+', '')) END AS seg_list
  FROM prm
),
scope AS (
  SELECT p.*,
         CAST(CASE WHEN arg.first_attempt THEN p.first_attempt_approved ELSE p.final_approved END AS INT) AS ok
  FROM {schema}silver_payments p CROSS JOIN arg
  WHERE sent_to_acquirer
    AND NOT is_cross_border
    AND country = ${p_country}
    AND (${p_card_type} IS NULL OR upper(trim(${p_card_type})) IN ('NULL', '') OR card_type = ${p_card_type})
),
win AS (
  SELECT s.* FROM scope s CROSS JOIN bounds
  WHERE s.created_ts_local >= bounds.ws AND s.created_ts_local < bounds.we
),
base AS (
  SELECT s.* FROM scope s CROSS JOIN bounds
  WHERE s.created_ts_local >= timestampadd(DAY, -${p_baseline_days}, bounds.ws)
    AND s.created_ts_local < bounds.ws
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
  -- joins, not scalar subqueries: inside a Unity Catalog function, a subquery expression cannot read the
  -- function's arguments, and win and base depend on them
  SELECT tw.n_win, tb.n_base, tw.rate_win, tb.rate_base
  FROM (SELECT count(*) AS n_win, avg(ok) AS rate_win FROM win) tw
  CROSS JOIN (SELECT count(*) AS n_base, avg(ok) AS rate_base FROM base) tb
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
CROSS JOIN arg
WHERE (arg.dim IS NULL OR coalesce(w.dimension, b.dimension) = arg.dim)
  AND (arg.seg_list IS NULL OR array_contains(arg.seg_list, lower(coalesce(w.segment_value, b.segment_value))))
)
SELECT
  dimension, segment_value, baseline_payments, window_payments, baseline_share, window_share, baseline_rate,
  window_rate, rate_effect_pp, mix_effect_pp, total_effect_pp, pp_below_next,
  CASE WHEN arg.first_attempt THEN 'first_attempt' ELSE 'final' END AS rate_type,
  CASE WHEN arg.rnk = 'highest' THEN pp_above_next_lower ELSE pp_below_next END AS gap_to_next_pp,
  date_format(bounds.ws, 'yyyy-MM-dd HH:mm:ss') AS window_start_local,
  date_format(bounds.we, 'yyyy-MM-dd HH:mm:ss') AS window_end_local
FROM seg CROSS JOIN arg CROSS JOIN bounds
-- p_rank keeps the three values at one end of each dimension; any other value keeps every row
WHERE arg.rnk NOT IN ('lowest', 'highest')
   OR (arg.rnk = 'lowest' AND rank_lowest <= 3)
   OR (arg.rnk = 'highest' AND rank_highest <= 3)
ORDER BY dimension, CASE WHEN arg.rnk = 'highest' THEN rank_highest ELSE rank_lowest END
