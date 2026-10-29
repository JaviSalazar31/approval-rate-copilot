"""
Runs the same SQL bodies used in Databricks on a local Spark session, to validate
the pipeline end to end before deploying it.

  python local/run_local.py --data data/full --out local/out
"""
from __future__ import annotations

import argparse
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
    if export:
        os.makedirs(os.path.join(out_dir, "gold"), exist_ok=True)
        for name, *_ in TABLES:
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
    run(args.data, args.out)
