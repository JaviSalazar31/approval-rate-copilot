-- Rates are fractions between 0 and 1, recomputed from counts in each table.
select 'gold_approval_daily' as table_name, business_date, country
from {{ ref('gold_approval_daily') }}
where final_approval_rate not between 0 and 1 or first_attempt_approval_rate not between 0 and 1
   or final_approved < first_attempt_approved
union all
select 'gold_funnel_daily', business_date, country
from {{ ref('gold_funnel_daily') }}
where conversion_rate not between 0 and 1 or risk_block_rate not between 0 and 1
