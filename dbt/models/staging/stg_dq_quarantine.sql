-- One-to-one with silver_dq_quarantine from the pipeline: the only place a model reads that source.
select * from {{ source('pipeline', 'silver_dq_quarantine') }}
