-- Databricks notebook source
-- MAGIC %md
-- MAGIC # 00 · Setup
-- MAGIC Creates the schema, the landing volume and the column mask used by the project. Idempotent: it is
-- MAGIC also the first task of the job.
-- MAGIC
-- MAGIC Works on Databricks Free Edition (catalog `workspace`).

-- COMMAND ----------

USE CATALOG workspace;
CREATE SCHEMA IF NOT EXISTS approval_copilot
  COMMENT 'Approval Rate Copilot: synthetic payments case study (fictional orchestrator, acquirers, issuers and merchants)';
CREATE VOLUME IF NOT EXISTS approval_copilot.landing
  COMMENT 'Raw acquirer files and reference CSVs produced by 01_generate_data';

-- COMMAND ----------

-- Column mask for the last four card digits (PRD requirement R10). Members of payments_support see them;
-- everyone else reads ****. The pipeline attaches it to silver_payment_intents.card_last4, so it must
-- exist before the pipeline runs.
CREATE FUNCTION IF NOT EXISTS approval_copilot.mask_card_last4(card_last4 STRING)
  RETURNS STRING
  COMMENT 'Column mask: shows the last four card digits only to members of payments_support.'
  RETURN CASE WHEN is_account_group_member('payments_support') THEN card_last4 ELSE '****' END;
