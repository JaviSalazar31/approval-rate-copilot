# Databricks notebook source
# MAGIC %md
# MAGIC # 13 · Late-data test: check what AUTO CDC kept
# MAGIC Compares the tables of the late-data pipeline with what the acquirer's sequence numbers say, after both
# MAGIC batches: the current status of each payment is its event with the highest sequence number (and a payment whose
# MAGIC last event is a DELETE is gone), whatever order the events arrived in; the history holds every status in
# MAGIC sequence order. It also shows what keeping the last event to arrive would have got wrong, and checks that the
# MAGIC copilot's gold table did not change. The job fails if any check fails.

# COMMAND ----------

sql = spark.sql  # noqa: F821  (spark and display exist in a Databricks notebook)
show = display  # noqa: F821
S = "workspace.approval_copilot_cdc"
failures = []


def check(name, ok, detail=""):
    print(("ok    " if ok else "FAIL  ") + name + (f": {detail}" if detail else ""))
    if not ok:
        failures.append(name)


expected = sql(f"""
    SELECT payment_id, status, sequence_num FROM (
      SELECT *, row_number() OVER (PARTITION BY payment_id ORDER BY sequence_num DESC) AS rn FROM {S}.payment_status_feed)
    WHERE rn = 1 AND op <> 'DELETE'""")
current = sql(f"SELECT payment_id, status, sequence_num FROM {S}.payment_status_current")
missing, extra = expected.exceptAll(current).count(), current.exceptAll(expected).count()
check("current status = highest sequence per payment", missing == 0 and extra == 0, f"missing {missing}, extra {extra}")
show(current.orderBy("payment_id"))

by_arrival = sql(f"""
    SELECT payment_id, status FROM (
      SELECT *, row_number() OVER (PARTITION BY payment_id ORDER BY received_at_utc DESC, sequence_num DESC) AS rn
      FROM {S}.payment_status_feed) WHERE rn = 1""")
wrong = by_arrival.exceptAll(expected.select("payment_id", "status")).collect()
check("keeping the last event to arrive would have been wrong", len(wrong) > 0,
      ", ".join(f"{r.payment_id} -> {r.status}" for r in wrong))

late = {r.status: (r.__START_AT, r.__END_AT) for r in sql(
    f"SELECT status, `__START_AT`, `__END_AT` FROM {S}.payment_status_history WHERE payment_id = 'PAY-CDC-02'").collect()}
check("history places the late SETTLED between APPROVED and REVERSED",
      late == {"APPROVED": (1, 2), "SETTLED": (2, 3), "REVERSED": (3, None)}, str(late))
deleted = sql(f"SELECT `__END_AT` FROM {S}.payment_status_history WHERE payment_id = 'PAY-CDC-05'").collect()
check("a deleted payment leaves the current table and closes its history",
      current.filter("payment_id = 'PAY-CDC-05'").count() == 0 and [r[0] for r in deleted] == [2], str(deleted))
show(sql(f"SELECT * FROM {S}.payment_status_history ORDER BY payment_id, `__START_AT`"))

before = sql(f"SELECT rows, final_approved, approved_usd FROM {S}.gold_checksum").first()
after = sql("""SELECT count(*) AS rows, sum(final_approved) AS final_approved, round(sum(approved_usd), 2) AS approved_usd
                     FROM workspace.approval_copilot.gold_approval_daily""").first()
check("the copilot's gold table did not change", tuple(before) == tuple(after), f"{tuple(before)} -> {tuple(after)}")

if failures:
    raise AssertionError(f"{len(failures)} check(s) failed: {', '.join(failures)}")
print("late-data test: all checks passed")
