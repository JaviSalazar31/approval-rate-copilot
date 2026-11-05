-- One-to-one with ref_change_log from the pipeline: the only place a model reads that source.
select * from {{ source('pipeline', 'ref_change_log') }}
