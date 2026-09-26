# Databricks notebook source
# /// script
# [tool.databricks.environment]
# environment_version = "6"
# ///
# MAGIC %md
# MAGIC # FlowGuard · 07 — Transit Pressure Gap, readiness and action cards
# MAGIC For every point of interest, slot and route group, for +30…+120 min (spec §5.2, metrics 7 and 9):
# MAGIC
# MAGIC - **Expected demand** = forecast departures summed over the corridors that load the group (pois.json).
# MAGIC - **Load** = demand ÷ scheduled capacity in bus-equivalents (averaged over the slot and the next one).
# MAGIC - **Gap** = load ÷ typical load for that group and service day type (daytime; seasonal window for seasonal POIs).
# MAGIC - **Readiness**: 🟢 Prepared < 1.2 ≤ 🟡 Watch < 1.7 ≤ 🟠 Strained < 2.3 ≤ 🔴 Critical — calibrated on Park Royal so
# MAGIC   Strained is worse than 95% of normal daytime slots, Critical worse than 99%. "No service" when nothing is scheduled;
# MAGIC   never escalated when demand is below half the usual daytime level.
# MAGIC - **Action card** (the POI's operator levers) when any horizon is Strained/Critical.
# MAGIC
# MAGIC Writes `gold.flowguard_timeline`, the one table the app reads for the radar and replay.

# COMMAND ----------

# MAGIC %run ./00_config

# COMMAND ----------

# DBTITLE 1,Build the timeline per POI
slots_all = from_spark(spark.table(f"{GOLD}.slots"), ts_cols=["slot_ts"], date_cols=["date"])
sc_all = from_spark(spark.table(f"{GOLD}.slot_corridor"), ts_cols=["slot_ts"])
fc_all = from_spark(spark.table(f"{GOLD}.egress_forecast"), ts_cols=["origin_slot_ts", "target_slot_ts"])
svc_all = spark.table(f"{GOLD}.transit_service_30min").toPandas()
days_all = from_spark(spark.table(f"{GOLD}.days"), ts_cols=["peak_pressure_slot"], date_cols=["date"])

parts = []
for poi in POIS:
    k = poi["key"]
    if k not in set(slots_all["poi"]):
        print(f"⚠️ no slots for {poi['name']} — skipped")
        continue
    tl, typical_load = fg_core.build_timeline(slots_all[slots_all["poi"] == k], sc_all[sc_all["poi"] == k],
                                              fc_all[fc_all["poi"] == k], svc_all[svc_all["poi"] == k],
                                              days_all[days_all["poi"] == k], poi)
    parts.append(tl)
    print(f"== {poi['name']}: typical load (forecast departures per bus-equivalent, daytime)")
    print(pd.Series(typical_load).unstack().round(1).to_string())
timeline = pd.concat(parts, ignore_index=True)

# COMMAND ----------

# DBTITLE 1,Write gold.flowguard_timeline
h_comments = {}
for h in HORIZONS:
    m = h * SLOT_MINUTES
    h_comments.update({
        f"dep_hat_h{h}": f"Forecast departures loading this route group, +{m} min (sample scale)",
        f"demand_idx_h{h}": f"Forecast demand for this group vs usual for that weekday and slot, +{m} min (x normal)",
        f"actual_h{h}": f"Actual departures for this group at +{m} min (for replay; not known at slot_ts)",
        f"svc_h{h}": f"Scheduled capacity in bus-equivalents per 30 min at +{m} min (mean of that slot and the next)",
        f"gap_h{h}": f"Transit Pressure Gap at +{m} min: demand per unit of scheduled capacity vs normal",
        f"readiness_h{h}": f"Prepared, Watch, Strained, Critical or No service at +{m} min",
    })
write_gold(
    timeline, "flowguard_timeline",
    "FlowGuard radar and replay table: one row per point of interest, 30-minute slot and route group with forecast exit demand, scheduled capacity, Transit Pressure Gap and readiness for +30 to +120 min, plus the recommended action.",
    {"poi": "Point of interest: park_royal, ubc or waterfront",
     "slot_ts": "Current (replay) slot, Vancouver local time",
     "route_group": "Route group for this POI (see pois.json)",
     "pressure": "Current occupancy vs normal for this weekday and slot",
     "signature": "Mobility signature of the current crowd",
     "typical_load": "Normal forecast departures per bus-equivalent for this group on this date's service day type",
     "typical_demand": "Normal daytime forecast departures for this group; below half of it the group is never escalated",
     "action_text": "Recommended operator action if any horizon is Strained or Critical, else null",
     "action_priority": "Peak gap within the flagged window (higher = more urgent)",
     **h_comments},
    ts_cols=["slot_ts"], date_cols=["date"],
)

# COMMAND ----------

# DBTITLE 1,Checkpoint — readiness on each POI's demo days (+60 min, 09:00–22:00) and first actions
for poi in POIS:
    t_poi = timeline[timeline["poi"] == poi["key"]]
    print(f"\n==================== {poi['name']}")
    for d, name in poi["presets"]:
        t = t_poi[(t_poi["date"] == d) & t_poi["slot_of_day"].between(18, 44)]
        mix = t["readiness_h2"].value_counts().to_dict()
        first = t[t["action_text"].notna()].sort_values("slot_ts").head(1)
        print(f"{d} {name:28s} {mix}")
        for r in first.itertuples():
            print(f"    first action [{r.slot_ts:%H:%M}] {r.action_text}")