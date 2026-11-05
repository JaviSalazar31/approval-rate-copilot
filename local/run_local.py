"""
Runs the same SQL bodies used in Databricks on a local Spark session, to validate
the pipeline end to end before deploying it. Generates the reference data first if it is missing.

  python local/run_local.py --data data/full --out local/out
"""
from __future__ import annotations

import argparse
import glob
import os
import re
import sys
import time

# Everything runs in UTC, whatever the machine's time zone: local business dates are computed
# explicitly per country, and pandas conversions must not depend on the host TZ.
os.environ["TZ"] = "UTC"
time.tzset()

from pyspark.sql import SparkSession  # noqa: E402

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "monitoring"))
from pipeline_spec import SOURCES, TABLES  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SQL_DIR = os.path.join(ROOT, "sql")


def spark_session():
    return (SparkSession.builder.master("local[2]").appName("approval-rate-copilot")
            .config("spark.ui.enabled", "false")
            .config("spark.sql.session.timeZone", "UTC")
            # Databricks serverless compute and SQL warehouses run with ANSI mode on: division by
            # zero and invalid casts raise errors instead of returning NULL. Mirror that here.
            .config("spark.sql.ansi.enabled", "true")
            .config("spark.driver.extraJavaOptions", "-Duser.timezone=UTC")
            .config("spark.driver.memory", "3g")
            .config("spark.sql.shuffle.partitions", "8")
            .getOrCreate())


def load_sources(spark, data_root):
    for key, (kind, rel) in SOURCES.items():
        path = os.path.join(data_root, rel)
        df = spark.read.option("header", True).option("inferSchema", False).csv(path)
        if kind == "raw":
            # Databricks read_files exposes the hidden _metadata column; mirror it locally
            df = df.select("*", "_metadata")
        df.createOrReplaceTempView(f"src_{key}")


def render(body: str) -> str:
    return re.sub(r"\{src:([a-z_]+)\}", lambda m: f"src_{m.group(1)}", body)


def run(data_root, out_dir, export=True):
    spark = spark_session()
    spark.sparkContext.setLogLevel("ERROR")
    load_sources(spark, data_root)
    timings = {}
    for name, rel, kind, comment, expectations in TABLES:
        t0 = time.time()
        body = render(open(os.path.join(SQL_DIR, rel)).read())
        df = spark.sql(body)
        drops = [c for n, c, action in expectations if action == "drop"]
        if drops:
            df = df.where(" AND ".join(f"({c})" for c in drops))
        df = df.cache()
        df.createOrReplaceTempView(name)
        n = df.count()
        timings[name] = (n, round(time.time() - t0, 1))
        print(f"{name:32s} {n:>9,} rows  {timings[name][1]:>5}s")
    # After the pipeline, the Databricks job runs the approved-payments forecast alert (notebook 07).
    # Same module here, on the same input.
    from volume_forecast import SPARK_SCHEMA, daily_approved, for_spark, volume_alerts
    alerts = volume_alerts(daily_approved(spark.table("gold_approval_daily").toPandas()))
    spark.createDataFrame(for_spark(alerts), SPARK_SCHEMA).createOrReplaceTempView("gold_volume_alerts_daily")
    print(f"{'gold_volume_alerts_daily':32s} {len(alerts):>9,} rows  (job task, not the pipeline)")
    if export:
        os.makedirs(os.path.join(out_dir, "gold"), exist_ok=True)
        for name in [t[0] for t in TABLES] + ["gold_volume_alerts_daily"]:
            if name.startswith("gold_") or name in ("silver_payments", "silver_dq_quarantine"):
                pdf = spark.table(name).toPandas()
                pdf.to_parquet(os.path.join(out_dir, "gold", f"{name}.parquet"), index=False)
                del pdf
            if not name.startswith("bronze_") and name != "silver_auth_attempts_staged":
                # Spark-native copy (keeps types) so later steps can reload without re-running
                spark.table(name).write.mode("overwrite").parquet(os.path.join(out_dir, "spark", name))
    return spark


def load_outputs(out_dir=os.path.join(ROOT, "local", "out")):
    """Reload the tables written by run() into a fresh Spark session as temp views."""
    spark = spark_session()
    spark.sparkContext.setLogLevel("ERROR")
    base = os.path.join(out_dir, "spark")
    for name in sorted(os.listdir(base)):
        spark.read.parquet(os.path.join(base, name)).createOrReplaceTempView(name)
    return spark


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=os.path.join(ROOT, "data", "full"))
    ap.add_argument("--out", default=os.path.join(ROOT, "local", "out"))
    args = ap.parse_args()
    sample = sorted(glob.glob(os.path.join(args.data, "raw", "orchestrator", "*.csv")))
    stale = bool(sample) and "card_last4" not in open(sample[0]).readline()
    if not sample or stale:
        # First run, or data written by a generator older than the card fields: generate the reference dataset
        sys.path.insert(0, os.path.join(ROOT, "data_generator"))
        from generate_data import generate
        print(f"{'Data in ' + args.data + ' predates the card fields' if stale else 'No data in ' + args.data}: "
              "generating the reference dataset (seed 42), under a minute")
        generate(args.data, seed=42)
    run(args.data, args.out)
