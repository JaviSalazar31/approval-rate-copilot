-- One-to-one with silver_auth_attempts from the pipeline: the only place a model reads that source.
select * from {{ source('pipeline', 'silver_auth_attempts') }}
