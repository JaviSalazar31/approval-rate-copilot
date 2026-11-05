-- One-to-one with ref_country_tz from the pipeline: the only place a model reads that source.
select * from {{ source('pipeline', 'ref_country_tz') }}
