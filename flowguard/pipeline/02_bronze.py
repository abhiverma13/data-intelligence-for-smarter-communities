# Databricks notebook source
# /// script
# [tool.databricks.environment]
# environment_version = "6"
# ///
# MAGIC %md
# MAGIC # FlowGuard · 02 — Bronze
# MAGIC Lands the uploaded files as-is (every column a string) plus lineage columns:
# MAGIC - mobility CSVs (Park Royal, UBC, Waterfront) → `bronze.mobility_raw`, tagged with `poi`
# MAGIC - TransLink GTFS `*.txt` → `bronze.gtfs_<file>`
# MAGIC
# MAGIC Prerequisite: `01_setup_uc` has run and the files are uploaded to the `raw` Volume.

# COMMAND ----------

# MAGIC %run ./00_config

# COMMAND ----------

from functools import reduce

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

# DBTITLE 1,Mobility CSVs → bronze.mobility_raw (one poi per file)
present = volume_files(RAW_PATH)
frames = []
for p in POIS:
    if p["file"] not in present:
        print(f"⚠️ {p['file']} ({p['name']}) not found in {RAW_PATH} — skipped. Upload it and re-run.")
        continue
    frames.append(
        spark.read.option("header", True).option("inferSchema", False)
        .csv(f"{RAW_PATH}/{p['file']}")
        .transform(clean_columns)
        .withColumn("poi", F.lit(p["key"]))
        .withColumn("_source_file", F.col("_metadata.file_path"))
        .withColumn("_ingested_at", F.current_timestamp())
    )
assert frames, f"No mobility CSVs found in {RAW_PATH}."
raw = reduce(lambda a, b: a.unionByName(b), frames)
raw.write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(f"{BRONZE}.mobility_raw")
spark.sql(f"COMMENT ON TABLE {BRONZE}.mobility_raw IS 'Synthetic cell-tower attachments for each point of interest, raw strings as uploaded. One row per device attachment.'")
display(spark.table(f"{BRONZE}.mobility_raw").groupBy("poi", "location_name").count().orderBy("poi"))

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