# Databricks notebook source
# MAGIC %md
# MAGIC # FlowGuard · 04 — Gold: slots, pressure, signature, days
# MAGIC `silver.visits` → three gold tables for every point of interest (spec §5.2, metrics 1–3 and 8):
# MAGIC
# MAGIC | Table | Grain | Key metrics |
# MAGIC |---|---|---|
# MAGIC | `gold.slots` | poi × 30-min slot (no gaps) | occupancy, **pressure** = occupancy ÷ normal for weekday × slot, **mobility signature** |
# MAGIC | `gold.slot_corridor` | poi × slot × corridor | arrivals, departures, usual departures, trailing-2 h share vs normal |
# MAGIC | `gold.days` | poi × date | **surge ratio** = arrivals ÷ normal for that weekday, peak pressure, label |
# MAGIC
# MAGIC "Normal" is the median over the whole history, or, for seasonal POIs (`baseline_weeks` in pois.json,
# MAGIC e.g. UBC term vs summer), over the surrounding weeks only. Spark aggregates the visits to slots; the
# MAGIC formulas run in `fg_core.build_slots` (shared with the local scripts).

# COMMAND ----------

# MAGIC %run ./00_config

# COMMAND ----------

# DBTITLE 1,Aggregate visits to 30-min slots (Spark)
from pyspark.sql import functions as F

v = spark.table(f"{SILVER}.visits")
not_long = (~F.col("long_stay")).cast("int")

arr = v.groupBy("poi", F.col("arrival_slot").alias("slot_ts")).agg(
    F.count("*").alias("arrivals"),
    F.sum(not_long).alias("arrivals_occ"),
    F.percentile_approx("dwell_time", 0.5).alias("median_dwell"),
)
dep = v.groupBy("poi", F.col("departure_slot").alias("slot_ts")).agg(
    F.count("*").alias("departures"),
    F.sum(not_long).alias("departures_occ"),
)
slot_agg_all = from_spark(arr.join(dep, ["poi", "slot_ts"], "full"), ts_cols=["slot_ts"])

ca = v.groupBy("poi", F.col("arrival_slot").alias("slot_ts"), "corridor").agg(F.count("*").alias("arrivals"))
cd = v.groupBy("poi", F.col("departure_slot").alias("slot_ts"), "corridor").agg(F.count("*").alias("departures"))
corr_agg_all = from_spark(ca.join(cd, ["poi", "slot_ts", "corridor"], "full").fillna(0), ts_cols=["slot_ts"])

ref_day = spark.table(f"{BRONZE}.ref_day_type").selectExpr("CAST(date AS STRING) AS date", "service_day_type", "label").toPandas()
print(slot_agg_all.groupby("poi").size().rename("slots with activity").to_string())

# COMMAND ----------

# DBTITLE 1,Compute slots, baselines, pressure, signature, days per POI (fg_core)
parts = {"slots": [], "slot_corridor": [], "days": []}
for key in sorted(slot_agg_all["poi"].unique()):
    poi = POI_BY_KEY[key]
    slot_agg = slot_agg_all[slot_agg_all["poi"] == key].drop(columns="poi").set_index("slot_ts").sort_index()
    corr_agg = corr_agg_all[corr_agg_all["poi"] == key].drop(columns="poi")
    s, sc, d = fg_core.build_slots(slot_agg, corr_agg, ref_day, poi)
    parts["slots"].append(s); parts["slot_corridor"].append(sc); parts["days"].append(d)
    print(f"{poi['name']}: {len(s):,} slots, {int(d['is_surge'].sum())} surge days")
slots = pd.concat(parts["slots"], ignore_index=True)
slot_corridor = pd.concat(parts["slot_corridor"], ignore_index=True)
days = pd.concat(parts["days"], ignore_index=True)

write_gold(
    slots, "slots",
    "One row per point of interest and 30-minute slot (Vancouver local time), Nov 2025 to Aug 2026. Pressure = occupancy vs normal for the same weekday and slot. Synthetic sample: use ratios, not headcounts.",
    {
        "poi": "Point of interest: park_royal, ubc or waterfront",
        "slot_ts": "Start of the 30-minute slot, Vancouver local time",
        "day_type": "Day of week (Mon..Sun)",
        "slot_of_day": "30-minute slot index within the day, 0..47",
        "service_day_type": "TransLink service day type: WEEKDAY, SATURDAY or SUNDAY_HOLIDAY (holidays from ref_day_type)",
        "arrivals": "Devices arriving in the slot (sample count)",
        "departures": "Devices leaving in the slot (sample count)",
        "occupancy": "Devices present at the end of the slot, excluding stays over 24 h",
        "baseline_occ": "Normal occupancy for this weekday and slot (median; seasonal window for seasonal POIs)",
        "pressure": "Occupancy divided by baseline_occ (x normal)",
        "pressure_level": "Normal < 1.3 <= Elevated < 1.8 <= High < 2.5 <= Severe",
        "median_dwell": "Median dwell minutes of devices arriving in the slot",
        "local_share": "Trailing-2 h arrival share from the POI local corridors (e.g. North Shore at Park Royal)",
        "regional_share": "Trailing-2 h arrival share from the POI regional corridors",
        "stay_ratio": "Trailing-2 h dwell vs normal for this weekday and slot",
        "visitor_ratio": "Trailing-2 h out-of-region share vs normal",
        "signature": "Mobility signature label, e.g. Regional + Extended-stay surge",
    },
    ts_cols=["slot_ts"], date_cols=["date"],
)
write_gold(
    slot_corridor, "slot_corridor",
    "Arrivals and departures per point of interest, 30-minute slot and outbound corridor, with the trailing-2 h arrival share (catchment) vs normal.",
    {"corridor": "Outbound demand corridor for this POI (proxy for direction, from device home origin)",
     "baseline_departures": "Normal departures for this corridor, weekday and slot",
     "share": "Share of trailing-2 h arrivals from this corridor",
     "baseline_share": "Normal trailing-2 h share from this corridor for this weekday and slot"},
    ts_cols=["slot_ts"],
)
write_gold(
    days, "days",
    "One row per point of interest and day: surge ratio vs normal for that weekday, peak pressure and a label for the day picker.",
    {"surge_ratio": "Daily arrivals divided by normal for that weekday (seasonal window for seasonal POIs)",
     "is_surge": f"True if surge_ratio >= {SURGE_RATIO_THRESHOLD}",
     "peak_pressure": "Highest pressure between 08:00 and 22:00",
     "peak_pressure_slot": "Slot of peak_pressure",
     "label": "Holiday name, or Surge day"},
    ts_cols=["peak_pressure_slot"], date_cols=["date"],
)

# COMMAND ----------

# DBTITLE 1,Checkpoint — top surge days per POI
for key, d in days.groupby("poi"):
    print(f"== {POI_BY_KEY[key]['name']}")
    print(d.sort_values("surge_ratio", ascending=False).head(6)[["date", "day_type", "surge_ratio", "peak_pressure", "label"]].round(2).to_string(index=False))
