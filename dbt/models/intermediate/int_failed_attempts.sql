-- Authorization attempts the issuer did not approve, with the local business date of the attempt.
select
  to_date(from_utc_timestamp(a.attempt_ts_utc, tz.time_zone)) as business_date,
  a.*
from {{ ref('stg_auth_attempts') }} a
join {{ ref('stg_country_tz') }} tz on tz.country = a.country
where not a.is_authorized
