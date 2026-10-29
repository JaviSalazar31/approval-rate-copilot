-- Raw acquirer_a files as received (all columns kept as strings), plus lineage columns.
SELECT
  *,
  _metadata.file_name AS _source_file,
  current_timestamp() AS _ingested_at
FROM {src:acquirer_a}
