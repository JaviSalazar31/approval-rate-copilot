-- Payments that reached an acquirer: the denominator of every approval rate (sent_to_acquirer).
-- Risk blocks, 3DS abandonment and intents without a valid attempt are counted in gold_funnel_daily.
select *
from {{ ref('stg_payments') }}
where sent_to_acquirer
