-- One-to-one with silver_payments from the pipeline: the only place a model reads that source.
select * from {{ source('pipeline', 'silver_payments') }}
