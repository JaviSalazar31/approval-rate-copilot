-- gold_approval_daily must count every payment that reached an acquirer exactly once, with the same approvals.
with gold as (
  select sum(payments_sent) as payments, sum(final_approved) as approved
  from {{ ref('gold_approval_daily') }}
),
silver as (
  select count(*) as payments, sum(cast(final_approved as int)) as approved
  from {{ ref('int_payments_sent') }}
)
select gold.payments as gold_payments, silver.payments as silver_payments,
       gold.approved as gold_approved, silver.approved as silver_approved
from gold cross join silver
where gold.payments <> silver.payments or gold.approved <> silver.approved
