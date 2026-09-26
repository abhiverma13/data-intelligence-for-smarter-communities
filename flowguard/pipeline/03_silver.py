# Databricks notebook source
# MAGIC %md
# MAGIC # FlowGuard · 03 — Silver
# MAGIC `bronze.pr_raw` → `silver.pr_visits`: one clean, typed row per device visit.
# MAGIC
# MAGIC | Rule (spec §4.2) | Implementation |
# MAGIC |---|---|
# MAGIC | Timestamps are Vancouver **local** time despite the `Z` | strip `Z`, parse as `TIMESTAMP_NTZ` (flip `TS_IS_UTC` in 00_config to convert instead) |
# MAGIC | `dwell_time` is minutes | `departure_ts = arrival_ts + dwell_time` minutes; `long_stay` if > 24 h |
# MAGIC | Drop exact duplicates | `dropDuplicates` on the six raw columns |
# MAGIC | 30-min slots | `arrival_slot`, `departure_slot`, `slot_of_day`, `egress_k` |
# MAGIC | Origin → corridor | join `bronze.ref_origin_corridor`, unmapped → `OTHER` |
# MAGIC
# MAGIC Data quality checks at the end fail the notebook on hard errors.

# COMMAND ----------

# MAGIC %run ./00_config

# COMMAND ----------

from pyspark.sql import functions as F

RAW_COLS = ["location_name", "longitude", "latitude", "timestamp", "origin", "dwell_time"]

raw = spark.table(f"{BRONZE}.pr_raw")
deduped = raw.dropDuplicates(RAW_COLS)

ts_expr = "try_cast(regexp_replace(timestamp, 'Z$', '') AS TIMESTAMP_NTZ)"
if TS_IS_UTC:
    ts_expr = f"convert_timezone('UTC', '{LOCAL_TZ}', {ts_expr})"


def slot_floor(col: str) -> str:
    """Floor a TIMESTAMP_NTZ to its 30-minute slot, staying in NTZ."""
    return (f"make_timestamp_ntz(year({col}), month({col}), day({col}), hour({col}), "
            f"CAST(floor(minute({col}) / {SLOT_MINUTES}) * {SLOT_MINUTES} AS INT), 0)")


typed = deduped.selectExpr(
    "location_name",
    "try_cast(latitude AS DOUBLE) AS latitude",
    "try_cast(longitude AS DOUBLE) AS longitude",
    f"{ts_expr} AS arrival_ts",
    "trim(origin) AS origin",
    "try_cast(dwell_time AS INT) AS dwell_time",
)

visits = (
    typed
    .withColumn("departure_ts", F.expr("timestampadd(MINUTE, dwell_time, arrival_ts)"))
    .withColumn("arrival_slot", F.expr(slot_floor("arrival_ts")))
    .withColumn("departure_slot", F.expr(slot_floor("departure_ts")))
    .withColumn("date", F.expr("CAST(arrival_ts AS DATE)"))
    .withColumn("day_type", F.expr("date_format(arrival_ts, 'EEE')"))
    .withColumn("slot_of_day", F.expr(f"hour(arrival_ts) * 2 + CAST(floor(minute(arrival_ts) / {SLOT_MINUTES}) AS INT)"))
    .withColumn("egress_k", F.expr(f"CAST(timestampdiff(MINUTE, arrival_slot, departure_slot) / {SLOT_MINUTES} AS INT)"))
    .withColumn("long_stay", F.col("dwell_time") > LONG_STAY_MINUTES)
)

corridors = spark.table(f"{BRONZE}.ref_origin_corridor")
visits = (
    visits.join(F.broadcast(corridors), "origin", "left")
    .withColumn("corridor", F.coalesce("corridor", F.lit("OTHER")))
    .select(
        "arrival_ts", "departure_ts", "arrival_slot", "departure_slot", "date", "day_type",
        "slot_of_day", "dwell_time", "egress_k", "long_stay", "origin", "corridor",
        "location_name", "latitude", "longitude",
    )
)

# COMMAND ----------

# DBTITLE 1,Write silver.pr_visits
target = f"{SILVER}.pr_visits"
visits.write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(target)

spark.sql(f"COMMENT ON TABLE {target} IS 'Park Royal device visits, cleaned: local-time arrival/departure, 30-min slots, outbound corridor. Synthetic sample — use ratios, not headcounts.'")
column_comments = {
    "arrival_ts": "Arrival time, Vancouver local clock (TIMESTAMP_NTZ)",
    "departure_ts": "arrival_ts + dwell_time minutes",
    "arrival_slot": "arrival_ts floored to its 30-minute slot",
    "departure_slot": "departure_ts floored to its 30-minute slot",
    "date": "Local calendar date of arrival",
    "day_type": "Day of week of arrival (Mon..Sun)",
    "slot_of_day": "30-minute slot index of arrival within the day, 0..47",
    "dwell_time": "Minutes the device stayed attached",
    "egress_k": "Number of 30-minute slots between arrival slot and departure slot",
    "long_stay": "True if dwell_time exceeds 24 hours",
    "origin": "Home location category of the device",
    "corridor": "Outbound demand corridor mapped from origin (NS_WEST, NS_EAST, EAST, SOUTH, BC_OTHER, OUT_OF_REGION, OTHER)",
}
for col, text in column_comments.items():
    spark.sql(f"ALTER TABLE {target} ALTER COLUMN {col} COMMENT '{text}'")
print(f"✅ wrote {target}")

# COMMAND ----------

# DBTITLE 1,Data quality checks
silver = spark.table(target)
n_raw = raw.count()
n_silver = silver.count()

q = silver.agg(
    F.sum(F.col("arrival_ts").isNull().cast("int")).alias("null_ts"),
    F.sum((F.col("origin").isNull() | (F.col("origin") == "")).cast("int")).alias("null_origin"),
    F.sum((F.col("dwell_time").isNull() | (F.col("dwell_time") < 1)).cast("int")).alias("bad_dwell"),
    F.countDistinct("latitude", "longitude").alias("n_coords"),
    F.countDistinct("location_name").alias("n_pois"),
    F.sum(F.col("long_stay").cast("int")).alias("n_long_stay"),
    F.min("arrival_ts").alias("min_ts"),
    F.max("arrival_ts").alias("max_ts"),
    F.countDistinct("date").alias("n_days"),
).first()
unmapped = [r.origin for r in silver.filter("corridor = 'OTHER'").select("origin").distinct().collect()]

checks = [
    ("rows: bronze → silver", True, f"{n_raw:,} → {n_silver:,} ({n_raw - n_silver:,} exact duplicates removed)"),
    ("all timestamps parsed", q.null_ts == 0, f"{q.null_ts:,} unparsed"),
    ("origin present", q.null_origin == 0, f"{q.null_origin:,} missing"),
    ("dwell_time ≥ 1 minute", q.bad_dwell == 0, f"{q.bad_dwell:,} bad"),
    ("single POI coordinate", q.n_coords == 1 and q.n_pois == 1, f"{q.n_pois} POI name(s), {q.n_coords} coordinate(s)"),
    ("all origins mapped to a corridor", not unmapped, f"unmapped: {unmapped}" if unmapped else "0 unmapped"),
    ("long stays (> 24 h) flagged", True, f"{q.n_long_stay:,} ({q.n_long_stay / n_silver:.2%})"),
    ("date range", True, f"{q.min_ts} → {q.max_ts} ({q.n_days} days)"),
]
for name, ok, detail in checks:
    print(f"{'✅' if ok else '❌'} {name}: {detail}")

hard_fail = [name for name, ok, _ in checks[1:5] if not ok]
assert not hard_fail, f"Data quality checks failed: {hard_fail}"

# COMMAND ----------

# DBTITLE 1,Checkpoint — hourly arrival profile (should peak mid-afternoon, not 1–6 AM)
hourly = (
    silver.groupBy(F.hour("arrival_ts").alias("hour")).count()
    .withColumn("share_pct", F.round(F.col("count") / F.lit(n_silver) * 100, 1))
    .orderBy("hour").toPandas()
)
print(hourly[["hour", "share_pct"]].to_string(index=False))

# COMMAND ----------

# DBTITLE 1,Checkpoint — Boxing Day 2025-12-26, 30-min arrivals vs departures
bd = "2025-12-26"
arr = silver.filter(F.col("arrival_ts").cast("date") == bd).groupBy(F.col("arrival_slot").alias("slot")).agg(F.count("*").alias("arrivals"))
dep = silver.filter(F.col("departure_ts").cast("date") == bd).groupBy(F.col("departure_slot").alias("slot")).agg(F.count("*").alias("departures"))
boxing = (
    arr.join(dep, "slot", "full").fillna(0)
    .filter(F.hour("slot").between(9, 19))
    .orderBy("slot")
    .select(F.date_format("slot", "HH:mm").alias("slot"), "arrivals", "departures")
    .toPandas()
)
print(boxing.to_string(index=False))
