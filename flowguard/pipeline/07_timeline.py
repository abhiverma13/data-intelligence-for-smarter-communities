# Databricks notebook source
# MAGIC %md
# MAGIC # FlowGuard · 07 — Transit Pressure Gap, readiness and action cards
# MAGIC For every slot and route group, for +30…+120 min (spec §5.2, metrics 7 and 9):
# MAGIC
# MAGIC - **Expected demand** = forecast departures summed over the corridors that load the group
# MAGIC   (EASTBOUND ← EAST + NS_EAST, DOWNTOWN ← SOUTH, WEST_VAN_LOCAL ← NS_WEST).
# MAGIC - **Load** = demand ÷ scheduled trips (averaged over the slot and the next one).
# MAGIC - **Gap** = load ÷ the typical load for that group on a normal day of the same service type (daytime, training period).
# MAGIC - **Readiness**: 🟢 Prepared < 1.2 ≤ 🟡 Watch < 1.7 ≤ 🟠 Strained < 2.3 ≤ 🔴 Critical — calibrated so Strained is worse
# MAGIC   than 95% of normal-day daytime slots, Critical worse than 99%. "No service" when nothing is scheduled;
# MAGIC   never escalated when demand is below half the usual daytime level.
# MAGIC - **Action card** when any horizon is Strained/Critical.
# MAGIC
# MAGIC Writes `gold.flowguard_timeline`, the one table the app reads for the radar and replay.

# COMMAND ----------

# MAGIC %run ./00_config

# COMMAND ----------

# DBTITLE 1,Build the timeline
pr_slots = from_spark(spark.table(f"{GOLD}.pr_slots"), ts_cols=["slot_ts"], date_cols=["date"])
pr_slot_corridor = from_spark(spark.table(f"{GOLD}.pr_slot_corridor"), ts_cols=["slot_ts"])
forecast = from_spark(spark.table(f"{GOLD}.egress_forecast"), ts_cols=["origin_slot_ts", "target_slot_ts"])
service = spark.table(f"{GOLD}.gtfs_service_30min").toPandas()
pr_days = from_spark(spark.table(f"{GOLD}.pr_days"), ts_cols=["peak_pressure_slot"], date_cols=["date"])

timeline, typical_load = fg_core.build_timeline(pr_slots, pr_slot_corridor, forecast, service, pr_days)
print("typical load (forecast departures per scheduled trip, normal daytime):")
print(pd.Series(typical_load).unstack().round(1).to_string())

# COMMAND ----------

# DBTITLE 1,Write gold.flowguard_timeline
h_comments = {}
for h in HORIZONS:
    m = h * SLOT_MINUTES
    h_comments.update({
        f"dep_hat_h{h}": f"Forecast departures loading this route group, +{m} min (sample scale)",
        f"demand_idx_h{h}": f"Forecast demand for this group vs usual for that weekday and slot, +{m} min (x normal)",
        f"actual_h{h}": f"Actual departures for this group at +{m} min (for replay; not known at slot_ts)",
        f"svc_h{h}": f"Scheduled trips per 30 min at +{m} min (mean of that slot and the next)",
        f"gap_h{h}": f"Transit Pressure Gap at +{m} min: demand per scheduled trip vs normal",
        f"readiness_h{h}": f"Prepared, Watch, Strained, Critical or No service at +{m} min",
    })
write_gold(
    timeline, "flowguard_timeline",
    "FlowGuard radar and replay table: one row per 30-minute slot and route group with forecast exit demand, scheduled trips, Transit Pressure Gap and readiness for +30 to +120 min, plus the recommended action.",
    {"slot_ts": "Current (replay) slot, Vancouver local time",
     "route_group": "EASTBOUND, DOWNTOWN or WEST_VAN_LOCAL",
     "pressure": "Current occupancy vs normal for this weekday and slot",
     "signature": "Mobility signature of the current crowd",
     "typical_load": "Normal forecast departures per scheduled trip for this group and service day type",
     "action_text": "Recommended operator action if any horizon is Strained or Critical, else null",
     "action_priority": "Peak gap within the flagged window (higher = more urgent)",
     **h_comments},
    ts_cols=["slot_ts"], date_cols=["date"],
)

# COMMAND ----------

# DBTITLE 1,Checkpoint — readiness on demo days (+60 min, 09:00–22:00) and Boxing Day actions
for d, name in [("2025-12-26", "Boxing Day"), ("2025-12-20", "Pre-Christmas Sat"), ("2026-04-25", "Normal Sat"), ("2026-03-20", "Normal Fri")]:
    t = timeline[(timeline["date"] == d) & timeline["slot_of_day"].between(18, 44)]
    print(f"\n== {d} {name}")
    print(t.groupby("route_group")["readiness_h2"].value_counts().unstack(fill_value=0).to_string())

bd = timeline[(timeline["date"] == "2025-12-26") & timeline["action_text"].notna()].sort_values("slot_ts")
print("\n== First Boxing Day action cards")
for r in bd.head(3).itertuples():
    print(f"[{r.slot_ts:%H:%M}] {r.action_text}")
