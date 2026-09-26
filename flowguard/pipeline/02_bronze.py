# Databricks notebook source
# MAGIC %md
# MAGIC # FlowGuard · 02 — Bronze
# MAGIC Lands the uploaded files as-is (every column a string) plus lineage columns:
# MAGIC - Park Royal CSV → `bronze.pr_raw`
# MAGIC - TransLink GTFS `*.txt` → `bronze.gtfs_<file>`
# MAGIC
# MAGIC Prerequisite: `01_setup_uc` has run and the files are uploaded to the `raw` Volume.

# COMMAND ----------

# MAGIC %run ./00_config

# COMMAND ----------

from pyspark.sql import functions as F


def volume_files(path: str) -> set:
    try:
        return {f.name.rstrip("/") for f in dbutils.fs.ls(path)}
    except Exception:
        return set()


def clean_columns(df):
    # GTFS files often carry a UTF-8 BOM on the first header.
    return df.toDF(*[c.replace("﻿", "").strip() for c in df.columns])

# COMMAND ----------

# DBTITLE 1,Park Royal CSV → bronze.pr_raw
assert PR_FILE in volume_files(RAW_PATH), (
    f"{PR_FILE} not found in {RAW_PATH}. Upload it via Catalog → {CATALOG} → {BRONZE_SCHEMA} → Volumes → raw."
)

pr_raw = (
    spark.read.option("header", True).option("inferSchema", False)
    .csv(f"{RAW_PATH}/{PR_FILE}")
    .transform(clean_columns)
    .withColumn("_source_file", F.col("_metadata.file_path"))
    .withColumn("_ingested_at", F.current_timestamp())
)
pr_raw.write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(f"{BRONZE}.pr_raw")
spark.sql(f"COMMENT ON TABLE {BRONZE}.pr_raw IS 'Park Royal synthetic cell-tower attachments, raw strings as uploaded. One row per device attachment.'")

n = spark.table(f"{BRONZE}.pr_raw").count()
print(f"✅ {BRONZE}.pr_raw: {n:,} rows")
display(spark.table(f"{BRONZE}.pr_raw").groupBy("location_name").count())

# COMMAND ----------

# DBTITLE 1,TransLink GTFS → bronze.gtfs_*
present = volume_files(GTFS_PATH)
for name in GTFS_FILES:
    fname = f"{name}.txt"
    if fname not in present:
        print(f"⚠️ {GTFS_PATH}/{fname} not uploaded yet — skipped. Re-run this cell once it is.")
        continue
    df = (
        spark.read.option("header", True).option("inferSchema", False)
        .csv(f"{GTFS_PATH}/{fname}")
        .transform(clean_columns)
        .withColumn("_ingested_at", F.current_timestamp())
    )
    table = f"{BRONZE}.gtfs_{name}"
    df.write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(table)
    spark.sql(f"COMMENT ON TABLE {table} IS 'TransLink static GTFS {fname} (feed downloaded Sept 2026), raw strings'")
    print(f"✅ {table}: {spark.table(table).count():,} rows")
