# Databricks notebook source
# /// script
# [tool.databricks.environment]
# environment_version = "6"
# ///
# MAGIC %md
# MAGIC # FlowGuard · 01 — Unity Catalog setup
# MAGIC Creates the catalog, the `bronze / silver / gold` schemas and the `raw` Volume, then loads the
# MAGIC repo's `config/*.csv` files into small `bronze.ref_*` reference tables.
# MAGIC
# MAGIC Attach **Serverless** and run it (safe to re-run).

# COMMAND ----------

# DBTITLE 1,Try to create the flowguard catalog (falls back to `workspace` if not permitted)
try:
    spark.sql("CREATE CATALOG IF NOT EXISTS flowguard COMMENT 'FlowGuard — mobility intelligence for Park Royal, UBC and Waterfront'")
    print("✅ Catalog `flowguard` is ready.")
except Exception as e:
    print(f"⚠️ Could not create catalog `flowguard` ({type(e).__name__}). "
          "Falling back to the `workspace` catalog with `flowguard_*` schemas.")

# COMMAND ----------

# MAGIC %run ./00_config

# COMMAND ----------

# DBTITLE 1,Schemas + raw Volume
spark.sql(f"CREATE SCHEMA IF NOT EXISTS {BRONZE} COMMENT 'Raw ingested files (mobility CSVs, TransLink GTFS) and reference tables'")
spark.sql(f"CREATE SCHEMA IF NOT EXISTS {SILVER} COMMENT 'Cleaned, typed, de-duplicated visit records and transit departures'")
spark.sql(f"CREATE SCHEMA IF NOT EXISTS {GOLD} COMMENT 'Pre-aggregated tables read by the FlowGuard app and Genie'")
spark.sql(f"CREATE VOLUME IF NOT EXISTS {RAW_VOLUME} COMMENT 'Uploaded raw files: mobility CSVs at the root, GTFS txt files under gtfs/'")
print(f"✅ Schemas {BRONZE}, {SILVER}, {GOLD} and volume {RAW_VOLUME} are ready.")

# COMMAND ----------

# DBTITLE 1,Load config CSVs → bronze.ref_* tables (load_ref is defined in 00_config)
load_ref("origin_corridor.csv", "ref_origin_corridor", ["poi", "origin", "corridor"], {},
         "Per point of interest: device home origin → outbound demand corridor (proxy for direction, not individual destination)")
load_ref("route_group_overrides.csv", "ref_route_group_override", ["poi", "trip_headsign", "route_group", "note"], {},
         "Manual GTFS trip headsign → route group fixes per point of interest; beat the keyword rules in pois.json")
load_ref("day_type_overrides.csv", "ref_day_type", ["date", "service_day_type", "label", "note"],
         {"date": "CAST(date AS DATE) AS date"},
         "Holiday dates → TransLink service day type, plus day labels for the day picker")

display(spark.table(f"{BRONZE}.ref_origin_corridor").groupBy("poi", "corridor").count().orderBy("poi", "corridor"))

# COMMAND ----------

# DBTITLE 1,Drop tables from the single-location (Park Royal only) version — all rebuilt with a poi column
for t in [f"{BRONZE}.pr_raw", f"{SILVER}.pr_visits", f"{SILVER}.gtfs_parkroyal_departures", f"{GOLD}.pr_slots",
          f"{GOLD}.pr_slot_corridor", f"{GOLD}.pr_days", f"{GOLD}.gtfs_service_30min"]:
    spark.sql(f"DROP TABLE IF EXISTS {t}")
print("✅ legacy Park-Royal-only tables removed (if present)")

# COMMAND ----------

# DBTITLE 1,Where to upload the raw files
print(f"""
Upload the raw files in Catalog Explorer: Catalog → {CATALOG} → {BRONZE_SCHEMA} → Volumes → raw

  to the volume root (one per point of interest):
""" + "\n".join(f"    • {p['file']:36s} ({p['name']})" for p in POIS) + f"""
  into the directory gtfs/:
    • {', '.join(f + '.txt' for f in GTFS_FILES)}
""")