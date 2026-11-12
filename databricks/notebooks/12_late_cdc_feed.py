# Databricks notebook source
# MAGIC %md
# MAGIC # 12 · Late-data test: land a batch of acquirer status events
# MAGIC Writes one batch of a small, synthetic feed of acquirer status events to
# MAGIC `workspace.approval_copilot_cdc.payment_status_feed`, the source of the AUTO CDC pipeline in
# MAGIC `databricks/pipeline/late_cdc_test.sql`. Batch 1 arrives on time; batch 2 arrives a day later with events
# MAGIC that are older than some already applied, a newer status, a payment not seen before and a deletion. Batch 1
# MAGIC also records a checksum of the copilot's gold table, so notebook 13 can show that the test changed none of it.
# MAGIC The schema `approval_copilot_cdc` holds only this test.

# COMMAND ----------

sql = spark.sql  # noqa: F821  (spark and dbutils exist in a Databricks notebook)
widgets = dbutils.widgets  # noqa: F821
widgets.dropdown("batch", "1", ["1", "2"])
BATCH = int(widgets.get("batch"))
FEED = "workspace.approval_copilot_cdc.payment_status_feed"

# (payment_id, status, amount_usd, event_ts_utc, sequence_num, op, batch)
# PAY-CDC-01  on time: APPROVED then SETTLED                          -> SETTLED
# PAY-CDC-02  late older event: SETTLED (seq 2) arrives after REVERSED (seq 3) -> REVERSED, history in order
# PAY-CDC-03  late newer event: REVERSED (seq 3) arrives in batch 2   -> REVERSED
# PAY-CDC-04  a payment first seen in batch 2: APPROVED then SETTLED -> SETTLED
# PAY-CDC-05  deleted: the acquirer voids the record (seq 2, DELETE)  -> gone from the current table
# PAY-CDC-06  out of order inside batch 1: seq 2 listed before seq 1  -> SETTLED
EVENTS = [
    ("PAY-CDC-01", "APPROVED", 120.50, "2026-08-20 14:00:05", 1, "UPSERT", 1),
    ("PAY-CDC-01", "SETTLED", 120.50, "2026-08-21 03:10:00", 2, "UPSERT", 1),
    ("PAY-CDC-02", "APPROVED", 64.00, "2026-08-20 14:02:11", 1, "UPSERT", 1),
    ("PAY-CDC-02", "REVERSED", 64.00, "2026-08-21 09:30:00", 3, "UPSERT", 1),
    ("PAY-CDC-02", "SETTLED", 64.00, "2026-08-21 03:10:00", 2, "UPSERT", 2),
    ("PAY-CDC-03", "APPROVED", 230.00, "2026-08-20 14:05:40", 1, "UPSERT", 1),
    ("PAY-CDC-03", "SETTLED", 230.00, "2026-08-21 03:10:00", 2, "UPSERT", 1),
    ("PAY-CDC-03", "REVERSED", 230.00, "2026-08-21 16:45:00", 3, "UPSERT", 2),
    ("PAY-CDC-04", "APPROVED", 18.90, "2026-08-20 14:07:02", 1, "UPSERT", 2),
    ("PAY-CDC-04", "SETTLED", 18.90, "2026-08-21 03:10:00", 2, "UPSERT", 2),
    ("PAY-CDC-05", "APPROVED", 75.25, "2026-08-20 14:09:30", 1, "UPSERT", 1),
    ("PAY-CDC-05", "VOIDED", 75.25, "2026-08-21 11:00:00", 2, "DELETE", 2),
    ("PAY-CDC-06", "SETTLED", 310.00, "2026-08-21 03:10:00", 2, "UPSERT", 1),
    ("PAY-CDC-06", "APPROVED", 310.00, "2026-08-20 14:11:47", 1, "UPSERT", 1),
]
RECEIVED = {1: "2026-08-21 12:00:00", 2: "2026-08-22 12:00:00"}

# COMMAND ----------

if BATCH == 1:
    sql("CREATE SCHEMA IF NOT EXISTS workspace.approval_copilot_cdc "
              "COMMENT 'Late-data test for AUTO CDC; holds nothing the copilot reads'")
    sql(f"""CREATE OR REPLACE TABLE {FEED} (
        payment_id STRING, status STRING, amount_usd DOUBLE, event_ts_utc TIMESTAMP, sequence_num BIGINT,
        op STRING, batch INT, received_at_utc TIMESTAMP)
        COMMENT 'Synthetic acquirer status events for the AUTO CDC late-data test'""")
    sql("""CREATE OR REPLACE TABLE workspace.approval_copilot_cdc.gold_checksum AS
        SELECT current_timestamp() AS taken_at, count(*) AS rows, sum(final_approved) AS final_approved,
               round(sum(approved_usd), 2) AS approved_usd
        FROM workspace.approval_copilot.gold_approval_daily""")

rows = [(p, s, a, t, n, o, b, RECEIVED[b]) for p, s, a, t, n, o, b in EVENTS if b == BATCH]
values = ",\n".join(f"('{p}', '{s}', {a}, TIMESTAMP'{t}', {n}, '{o}', {b}, TIMESTAMP'{r}')" for p, s, a, t, n, o, b, r in rows)
sql(f"INSERT INTO {FEED} VALUES\n{values}")
print(f"batch {BATCH}: {len(rows)} events landed in {FEED}")
