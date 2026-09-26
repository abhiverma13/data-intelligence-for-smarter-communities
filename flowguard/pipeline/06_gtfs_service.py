# Databricks notebook source
# MAGIC %md
# MAGIC # FlowGuard · 06 — Scheduled transit service at Park Royal (TransLink GTFS)
# MAGIC 1. **Park Royal stops** = GTFS stops within `GTFS_STOP_RADIUS_M` (400 m) of the mall.
# MAGIC 2. **Departing trips** = each trip counted once, at its first Park Royal stop; trips that *end* within
# MAGIC    `GTFS_TERMINATING_RADIUS_M` (600 m) are arrivals, not departures, and are dropped.
# MAGIC 3. **Route group** from the trip headsign (keyword rules in `fg_settings.ROUTE_GROUP_RULES`;
# MAGIC    `config/route_group_overrides.csv` wins): EASTBOUND, DOWNTOWN, WEST_VAN_LOCAL.
# MAGIC 4. **Scheduled departures** per service day type × 30-min slot × route group, using one representative
# MAGIC    date per day type from the current feed (`SERVICE_REP_DATES`).
# MAGIC
# MAGIC Writes `silver.gtfs_parkroyal_departures` and `gold.gtfs_service_30min`.
# MAGIC **Review the printed stops and route groups**; fix any wrong group in `config/route_group_overrides.csv`, push, pull, re-run.

# COMMAND ----------

# MAGIC %run ./00_config

# COMMAND ----------

# DBTITLE 1,Load GTFS + overrides
def gtfs(name, cols):
    return spark.table(f"{BRONZE}.gtfs_{name}").select(*cols).toPandas()


stops = gtfs("stops", ["stop_id", "stop_code", "stop_name", "stop_lat", "stop_lon"])
stop_times = gtfs("stop_times", ["trip_id", "stop_id", "stop_sequence", "departure_time"])
trips = gtfs("trips", ["trip_id", "route_id", "service_id", "trip_headsign"])
routes = gtfs("routes", ["route_id", "route_short_name", "route_long_name"])
calendar = gtfs("calendar", ["service_id", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday", "start_date", "end_date"])
calendar_dates = gtfs("calendar_dates", ["service_id", "date", "exception_type"])

# re-load the overrides CSV so an edit only needs this notebook re-run
load_ref("route_group_overrides.csv", "ref_route_group_override", ["trip_headsign", "route_group", "note"], {},
         "Manual GTFS trip headsign → route group fixes; beat the keyword rules in fg_settings.ROUTE_GROUP_RULES")
ov = spark.table(f"{BRONZE}.ref_route_group_override").toPandas()
overrides = dict(zip(ov["trip_headsign"], ov["route_group"]))
print(f"stop_times rows: {len(stop_times):,}   overrides: {len(overrides)}")
print(f"feed service window: {calendar['start_date'].min()} → {calendar['end_date'].max()}")

# COMMAND ----------

# DBTITLE 1,Park Royal stops and departing trips
departures, near_stops = fg_core.parkroyal_departures(stops, stop_times, trips, routes, overrides)

print("== Park Royal stops (check these are the exchange bays and adjacent Marine Dr / Taylor Way stops)")
print(near_stops.round(0).to_string(index=False))
print("\n== Route groups — trips per headsign (check each group)")
print(departures.groupby(["route_group", "route_short_name", "trip_headsign"]).size().rename("trips").to_string())

# COMMAND ----------

# DBTITLE 1,Scheduled departures per service day type × slot × route group
service = fg_core.service_30min(departures, calendar, calendar_dates)
print(service.groupby(["service_day_type", "route_group"])["scheduled_departures"].sum().unstack().to_string())

write_gold(departures, "gtfs_parkroyal_departures",
           "TransLink trips departing Park Royal (first stop within 400 m of the mall), with route group. Current GTFS feed (Sept 2026).",
           {"route_group": "EASTBOUND, DOWNTOWN, WEST_VAN_LOCAL or EXCLUDE",
            "departure_time": "Scheduled departure at the Park Royal stop (GTFS time, may exceed 24:00:00)"},
           schema=SILVER)
write_gold(service, "gtfs_service_30min",
           "Scheduled TransLink departures from Park Royal per service day type, 30-minute slot and route group (current feed applied to all history).",
           {"service_day_type": "WEEKDAY, SATURDAY or SUNDAY_HOLIDAY",
            "slot_of_day": "30-minute slot index 0..47",
            "route_group": "EASTBOUND (R2, 255), DOWNTOWN (250/253/254/257 Vancouver, 44), WEST_VAN_LOCAL",
            "scheduled_departures": "Scheduled trips leaving Park Royal in the slot"})
