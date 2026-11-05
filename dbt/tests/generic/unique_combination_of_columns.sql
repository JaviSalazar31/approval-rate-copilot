{% test unique_combination_of_columns(model, combination_of_columns) %}
-- Fails with every combination of the columns that appears more than once (NULL counts as a value).
select {{ combination_of_columns | join(', ') }}, count(*) as rows_with_this_key
from {{ model }}
group by {{ combination_of_columns | join(', ') }}
having count(*) > 1
{% endtest %}
