-- Databricks notebook source
-- MAGIC %md
-- MAGIC # 00 · Setup
-- MAGIC Creates the schema and the landing volume used by the project. Run once.
-- MAGIC
-- MAGIC Works on Databricks Free Edition (catalog `workspace`).

-- COMMAND ----------

USE CATALOG workspace;
CREATE SCHEMA IF NOT EXISTS approval_copilot
  COMMENT 'Approval Rate Copilot: synthetic payments case study (fictional orchestrator, acquirers, issuers and merchants)';
CREATE VOLUME IF NOT EXISTS approval_copilot.landing
  COMMENT 'Raw acquirer files and reference CSVs produced by 01_generate_data';
