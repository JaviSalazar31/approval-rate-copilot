-- Versioned normalization map: (source system, raw status, raw code) -> canonical status.
-- 18 raw statuses across 4 source systems collapse into 8 canonical statuses.
SELECT
  mapping_version,
  CAST(concat(replace(valid_from_utc, ' ', 'T'), 'Z') AS TIMESTAMP) AS valid_from_utc,
  CAST(concat(replace(valid_to_utc, ' ', 'T'), 'Z') AS TIMESTAMP) AS valid_to_utc,
  source_system,
  raw_status,
  coalesce(raw_code, '') AS raw_code,
  nullif(iso_code, '') AS iso_code,
  reason,
  canonical_status,
  decline_type,
  CAST(retryable AS BOOLEAN) AS retryable
FROM {src:decline_code_map}
