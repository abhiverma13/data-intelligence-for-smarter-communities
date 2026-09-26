# Databricks notebook source
# MAGIC %md
# MAGIC # FlowGuard · 04 — Gold: slots, pressure, signature, days
# MAGIC `silver.pr_visits` → three gold tables (spec §5.2, metrics 1–3 and 8):
# MAGIC
# MAGIC | Table | Grain | Key metrics |
# MAGIC |---|---|---|
# MAGIC | `gold.pr_slots` | 30-min slot (no gaps) | occupancy, **pressure** = occupancy ÷ median for weekday × slot, corridor shares (trailing 2 h), **mobility signature** |
# MAGIC | `gold.pr_slot_corridor` | slot × corridor | arrivals, departures, usual departures |
# MAGIC | `gold.pr_days` | date | **surge ratio** = arrivals ÷ median for that weekday, peak pressure, label |
# MAGIC
# MAGIC Spark aggregates the 5.4M visits to slots; the formulas run in `fg_core.build_slots` (shared with the local scripts).

# COMMAND ----------

# MAGIC %run ./00_config

# COMMAND ----------

# DBTITLE 1,Aggregate visits to 30-min slots (Spark)
from pyspark.sql import functions as F

v = spark.table(f"{SILVER}.pr_visits")
not_long = (~F.col("long_stay")).cast("int")

arr = v.groupBy(F.col("arrival_slot").alias("slot_ts")).agg(
    F.count("*").alias("arrivals"),
    F.sum(not_long).alias("arrivals_occ"),
    F.percentile_approx("dwell_time", 0.5).alias("median_dwell"),
)
dep = v.groupBy(F.col("departure_slot").alias("slot_ts")).agg(
    F.count("*").alias("departures"),
    F.sum(not_long).alias("departures_occ"),
)
slot_agg = from_spark(arr.join(dep, "slot_ts", "full"), ts_cols=["slot_ts"]).set_index("slot_ts").sort_index()

ca = v.groupBy(F.col("arrival_slot").alias("slot_ts"), "corridor").agg(F.count("*").alias("arrivals"))
cd = v.groupBy(F.col("departure_slot").alias("slot_ts"), "corridor").agg(F.count("*").alias("departures"))
corr_agg = from_spark(ca.join(cd, ["slot_ts", "corridor"], "full").fillna(0), ts_cols=["slot_ts"])

ref_day = spark.table(f"{BRONZE}.ref_day_type").selectExpr("CAST(date AS STRING) AS date", "service_day_type", "label").toPandas()
print(f"slots with activity: {len(slot_agg):,}   slot × corridor rows: {len(corr_agg):,}")

# COMMAND ----------

# DBTITLE 1,Compute slots, baselines, pressure, signature, days (fg_core)
pr_slots, pr_slot_corridor, pr_days = fg_core.build_slots(slot_agg, corr_agg, ref_day)

share_cols = {f"share_{c.lower()}": f"Share of trailing-2 h arrivals from corridor {c}" for c in CORRIDORS}
base_share_cols = {f"baseline_share_{c.lower()}": f"Usual (median) trailing-2 h share from corridor {c} for this weekday and slot" for c in CORRIDORS}

write_gold(
    pr_slots, "pr_slots",
    "Park Royal, one row per 30-minute slot (Vancouver local time), Nov 2025 to Aug 2026. Pressure = occupancy vs the median for the same weekday and slot. Synthetic sample: use ratios, not headcounts.",
    {
        "slot_ts": "Start of the 30-minute slot, Vancouver local time",
        "day_type": "Day of week (Mon..Sun)",
        "slot_of_day": "30-minute slot index within the day, 0..47",
        "service_day_type": "TransLink service day type: WEEKDAY, SATURDAY or SUNDAY_HOLIDAY (holidays from ref_day_type)",
        "arrivals": "Devices arriving in the slot (sample count)",
        "departures": "Devices leaving in the slot (sample count)",
        "occupancy": "Devices present at the end of the slot, excluding stays over 24 h",
        "baseline_occ": "Median occupancy for this weekday and slot",
        "pressure": "Occupancy divided by baseline_occ (x normal)",
        "pressure_level": "Normal < 1.3 <= Elevated < 1.8 <= High < 2.5 <= Severe",
        "median_dwell": "Median dwell minutes of devices arriving in the slot",
        "regional_share": "Trailing-2 h arrival share from SOUTH + EAST + BC_OTHER corridors",
        "stay_ratio": "Trailing-2 h dwell vs usual for this weekday and slot",
        "visitor_ratio": "Trailing-2 h out-of-region share vs usual",
        "signature": "Mobility signature label, e.g. Regional + Extended-stay surge",
        **share_cols, **base_share_cols,
    },
    ts_cols=["slot_ts"], date_cols=["date"],
)
write_gold(
    pr_slot_corridor, "pr_slot_corridor",
    "Park Royal arrivals and departures per 30-minute slot and outbound corridor.",
    {"corridor": "Outbound demand corridor (proxy for direction, from device home origin)",
     "baseline_departures": "Median departures for this corridor, weekday and slot"},
    ts_cols=["slot_ts"],
)
write_gold(
    pr_days, "pr_days",
    "Park Royal, one row per day: surge ratio vs the median for that weekday, peak pressure and a label for the day picker.",
    {"surge_ratio": "Daily arrivals divided by the median for that weekday",
     "is_surge": f"True if surge_ratio >= {SURGE_RATIO_THRESHOLD}",
     "peak_pressure": "Highest pressure between 08:00 and 22:00",
     "peak_pressure_slot": "Slot of peak_pressure",
     "label": "Holiday name, or Surge day"},
    ts_cols=["peak_pressure_slot"], date_cols=["date"],
)

# COMMAND ----------

# DBTITLE 1,Checkpoint — surge days and Boxing Day pressure
print(pr_days[pr_days["is_surge"]][["date", "day_type", "surge_ratio", "peak_pressure", "label"]].round(2).to_string(index=False))
bd = pr_slots[(pr_slots["date"] == "2025-12-26") & pr_slots["slot_of_day"].between(18, 42)]
print(bd[["slot_ts", "occupancy", "pressure", "pressure_level", "signature"]].iloc[::2].round(2).to_string(index=False))
