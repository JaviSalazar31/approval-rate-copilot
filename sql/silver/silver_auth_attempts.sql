-- Attempts that pass the data-quality expectations defined for this table
-- (see the pipeline definition). Failing rows go to silver_dq_quarantine.
SELECT * FROM silver_auth_attempts_staged
