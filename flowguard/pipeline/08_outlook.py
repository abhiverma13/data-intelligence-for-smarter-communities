# Databricks notebook source
# MAGIC %md
# MAGIC # FlowGuard · 08 — Outlook for future dates
# MAGIC Expected conditions for **Sept 7 2026 – Jan 3 2027** (the dates TransLink's published GTFS feed covers, after the
# MAGIC mobility data ends), for every point of interest:
# MAGIC
# MAGIC - **Crowd**: the median of analog past days: a holiday → last year's same holiday (Boxing Day 2026 ← Boxing Day 2025);
# MAGIC   otherwise the same weekday 52 weeks earlier ±1 week; where there is no year-ago data (Sept–Oct), the typical same
# MAGIC   weekday of the training period.
# MAGIC - **Service**: the actual published schedule of that exact date (including holiday service), in bus-equivalents.
# MAGIC - **Readiness & actions**: the same gap / readiness / action logic as the past days.
# MAGIC
# MAGIC Writes `gold.outlook_days`, `gold.outlook_slots`, `gold.outlook_slot_corridor`, `gold.outlook_timeline`
# MAGIC (same columns as the past tables; `outlook_days` adds `method` and `analog_dates`).

# COMMAND ----------

# MAGIC %run ./00_config

# COMMAND ----------

# DBTITLE 1,Load past gold tables, transit departures and the GTFS calendar
slots_all = from_spark(spark.table(f"{GOLD}.slots"), ts_cols=["slot_ts"], date_cols=["date"])
sc_all = from_spark(spark.table(f"{GOLD}.slot_corridor"), ts_cols=["slot_ts"])
tl_all = spark.table(f"{GOLD}.flowguard_timeline").select("poi", "route_group", "service_day_type", "typical_load", "typical_demand").toPandas()
days_all = from_spark(spark.table(f"{GOLD}.days"), ts_cols=["peak_pressure_slot"], date_cols=["date"])
deps_all = spark.table(f"{SILVER}.transit_departures").toPandas()
calendar = spark.table(f"{BRONZE}.gtfs_calendar").toPandas()
calendar_dates = spark.table(f"{BRONZE}.gtfs_calendar_dates").toPandas()
ref_day = spark.table(f"{BRONZE}.ref_day_type").selectExpr("CAST(date AS STRING) AS date", "service_day_type", "label").toPandas()

# COMMAND ----------

# DBTITLE 1,Build the outlook per POI
parts = {"outlook_days": [], "outlook_slots": [], "outlook_slot_corridor": [], "outlook_timeline": []}
for poi in POIS:
    k = poi["key"]
    if k not in set(slots_all["poi"]):
        print(f"⚠️ no past data for {poi['name']} — skipped")
        continue
    out = fg_core.build_outlook(slots_all[slots_all["poi"] == k], sc_all[sc_all["poi"] == k], tl_all[tl_all["poi"] == k],
                                days_all[days_all["poi"] == k], deps_all[deps_all["poi"] == k], calendar, calendar_dates, ref_day, poi)
    for name, df in zip(parts, out):
        parts[name].append(df)
    o_days = out[0]
    print(f"== {poi['name']}: {len(o_days)} outlook days, {int(o_days['is_surge'].sum())} expected surge days")
    print(o_days.sort_values("surge_ratio", ascending=False).head(5)[["date", "day_type", "surge_ratio", "peak_pressure", "label", "method"]]
          .round(2).to_string(index=False))
outlook = {name: pd.concat(frames, ignore_index=True) for name, frames in parts.items()}

# COMMAND ----------

# DBTITLE 1,Write gold outlook tables
OUTLOOK_NOTE = "Outlook (expected, not observed): crowd from analog past days, service from the published schedule of each date."
write_gold(outlook["outlook_days"], "outlook_days",
           f"One row per point of interest and future day (Sept 7 2026 to Jan 3 2027). {OUTLOOK_NOTE}",
           {"surge_ratio": "Expected daily arrivals vs normal for that weekday (median of the analog days)",
            "method": "How the day was estimated (holiday match, same week last year, or typical weekday)",
            "night_ratio": "Expected overnight presence 00:00-06:00 vs a normal night (median of the analog days)",
            "night_unusual": "True if unusual overnight presence is expected (night_ratio >= 1.5)",
            "analog_dates": "Past days used as analogs"},
           ts_cols=["peak_pressure_slot"], date_cols=["date"])
write_gold(outlook["outlook_slots"], "outlook_slots",
           f"Expected conditions per point of interest and 30-minute slot for future days. {OUTLOOK_NOTE}",
           {"pressure": "Expected people on site vs normal for this weekday and slot (x normal)",
            "signature": "Expected mobility signature"},
           ts_cols=["slot_ts"], date_cols=["date"])
write_gold(outlook["outlook_slot_corridor"], "outlook_slot_corridor",
           f"Expected exits and catchment shares per corridor and 30-minute slot for future days. {OUTLOOK_NOTE}", {},
           ts_cols=["slot_ts"])
write_gold(outlook["outlook_timeline"], "outlook_timeline",
           f"Expected transit readiness and actions per slot and route group for future days. {OUTLOOK_NOTE}",
           {**{f"readiness_h{h}": f"Expected Prepared, Watch, Strained, Critical or No service at +{h * SLOT_MINUTES} min" for h in HORIZONS},
            **{f"gap_h{h}": f"Expected exit demand per unit of scheduled service vs normal at +{h * SLOT_MINUTES} min" for h in HORIZONS},
            "action_text": "Recommended operator action if any horizon is expected Strained or Critical"},
           ts_cols=["slot_ts"], date_cols=["date"])
