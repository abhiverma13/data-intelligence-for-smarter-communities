# Databricks notebook source
# MAGIC %md
# MAGIC # FlowGuard · 01 — Unity Catalog setup
# MAGIC Creates the catalog, the `bronze / silver / gold` schemas and the `raw` Volume, then loads the
# MAGIC repo's `config/*.csv` files into small `bronze.ref_*` reference tables.
# MAGIC
# MAGIC Attach **Serverless** and run the cells **one at a time** (Shift+Enter). Safe to re-run.

# COMMAND ----------

# DBTITLE 1,Try to create the flowguard catalog (falls back to `workspace` if not permitted)
try:
    spark.sql("CREATE CATALOG IF NOT EXISTS flowguard COMMENT 'FlowGuard — Park Royal mobility intelligence'")
    print("✅ Catalog `flowguard` is ready.")
except Exception as e:
    print(f"⚠️ Could not create catalog `flowguard` ({type(e).__name__}). "
          "Falling back to the `workspace` catalog with `flowguard_*` schemas.")

# COMMAND ----------

# MAGIC %run ./00_config

# COMMAND ----------

# DBTITLE 1,Schemas + raw Volume
spark.sql(f"CREATE SCHEMA IF NOT EXISTS {BRONZE} COMMENT 'Raw ingested files (Park Royal mobility CSV, TransLink GTFS) and reference tables'")
spark.sql(f"CREATE SCHEMA IF NOT EXISTS {SILVER} COMMENT 'Cleaned, typed, de-duplicated visit records'")
spark.sql(f"CREATE SCHEMA IF NOT EXISTS {GOLD} COMMENT 'Pre-aggregated tables read by the FlowGuard app and Genie'")
spark.sql(f"CREATE VOLUME IF NOT EXISTS {RAW_VOLUME} COMMENT 'Uploaded raw files: Park Royal CSV at the root, GTFS txt files under gtfs/'")
print(f"✅ Schemas {BRONZE}, {SILVER}, {GOLD} and volume {RAW_VOLUME} are ready.")

# COMMAND ----------

# DBTITLE 1,Load config CSVs → bronze.ref_* tables
import pandas as pd


def load_ref(csv_name: str, table: str, columns: list, casts: dict, comment: str):
    pdf = pd.read_csv(os.path.join(CONFIG_DIR, csv_name), dtype=str, keep_default_na=False)
    missing = set(columns) - set(pdf.columns)
    assert not missing, f"{csv_name} is missing columns {missing}"
    rows = [tuple(r) for r in pdf[columns].itertuples(index=False)]
    df = spark.createDataFrame(rows, ", ".join(f"{c} STRING" for c in columns))
    df = df.selectExpr(*[casts.get(c, c) for c in columns])
    full = f"{BRONZE}.{table}"
    df.write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(full)
    spark.sql(f"COMMENT ON TABLE {full} IS '{comment}'")
    print(f"✅ {full}: {df.count()} rows")


load_ref("origin_corridor.csv", "ref_origin_corridor", ["origin", "corridor"], {},
         "Device home origin → outbound demand corridor (proxy for direction, not individual destination)")
load_ref("route_group_overrides.csv", "ref_route_group_override", ["route_short_name", "route_group", "note"], {},
         "Manual GTFS route → route group fixes applied after the keyword rules")
load_ref("day_type_overrides.csv", "ref_day_type", ["date", "service_day_type", "label", "note"],
         {"date": "CAST(date AS DATE) AS date"},
         "Holiday dates → TransLink service day type, plus day labels for the day picker")

display(spark.table(f"{BRONZE}.ref_origin_corridor").groupBy("corridor").count().orderBy("corridor"))

# COMMAND ----------

# DBTITLE 1,Where to upload the raw files
print(f"""
NEXT: upload the raw files in Catalog Explorer

  Catalog → {CATALOG} → {BRONZE_SCHEMA} → Volumes → raw
    • {PR_FILE}                       → upload to the volume root
    • create directory 'gtfs', then upload into it:
        {', '.join(f + '.txt' for f in GTFS_FILES)}

  Final paths:
    {RAW_PATH}/{PR_FILE}
    {GTFS_PATH}/<file>.txt
""")
