# Databricks notebook source
# MAGIC %md
# MAGIC # FlowGuard · 06 — Scheduled transit service at each point of interest (TransLink GTFS)
# MAGIC For every POI in `pois.json`:
# MAGIC 1. **Stops** = GTFS stops within `stop_radius_m` (Park Royal 400 m, UBC Exchange 800 m, Waterfront 400 m).
# MAGIC 2. **Departing trips** = each trip counted once, at its first stop there; trips that *end* within
# MAGIC    `terminating_radius_m` are arrivals, not departures, and are dropped.
# MAGIC 3. **Route group** from the trip headsign (`route_group_rules` in pois.json; `config/route_group_overrides.csv` wins).
# MAGIC 4. **Scheduled service** per service day type × 30-min slot × route group, as departures and as **capacity in
# MAGIC    bus-equivalents** (SkyTrain / SeaBus ≈ 5 buses, West Coast Express ≈ 12), using one representative date per
# MAGIC    day type from the current feed (`SERVICE_REP_DATES`).
# MAGIC
# MAGIC Writes `silver.transit_stops`, `silver.transit_departures`, `gold.transit_service_30min`.
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
routes = gtfs("routes", ["route_id", "route_short_name", "route_long_name", "route_type"])
calendar = gtfs("calendar", ["service_id", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday", "start_date", "end_date"])
calendar_dates = gtfs("calendar_dates", ["service_id", "date", "exception_type"])
last = fg_core.last_stops(stop_times)

# re-load the overrides CSV so an edit only needs this notebook re-run
load_ref("route_group_overrides.csv", "ref_route_group_override", ["poi", "trip_headsign", "route_group", "note"], {},
         "Manual GTFS trip headsign → route group fixes per point of interest; beat the keyword rules in pois.json")
ov = spark.table(f"{BRONZE}.ref_route_group_override").toPandas()
print(f"stop_times rows: {len(stop_times):,}   overrides: {len(ov)}")
print(f"feed service window: {calendar['start_date'].min()} → {calendar['end_date'].max()}")

# COMMAND ----------

# DBTITLE 1,Stops, departing trips and route groups per POI (review these)
deps, nears, services = [], [], []
for poi in POIS:
    o = ov[ov["poi"] == poi["key"]]
    dep, near = fg_core.poi_departures(stops, stop_times, trips, routes, last, dict(zip(o["trip_headsign"], o["route_group"])), poi)
    deps.append(dep); nears.append(near)
    services.append(fg_core.service_30min(dep, calendar, calendar_dates, poi))
    print(f"\n==================== {poi['name']} — {len(near)} stops within {poi['stop_radius_m']} m")
    print(near.round(0).drop(columns="poi").head(12).to_string(index=False) + ("\n  …" if len(near) > 12 else ""))
    print(f"\n-- route groups ({len(dep):,} departing trips across all service days)")
    print(dep.groupby(["route_group", "route_name", "trip_headsign"]).size().rename("trips").reset_index().to_string(index=False))
departures = pd.concat(deps, ignore_index=True)
near_stops = pd.concat(nears, ignore_index=True)
service = pd.concat(services, ignore_index=True)

# COMMAND ----------

# DBTITLE 1,Scheduled service per POI × service day type × route group
print(service.groupby(["poi", "service_day_type", "route_group"])[["scheduled_departures", "scheduled_capacity"]].sum().to_string())

write_gold(near_stops, "transit_stops",
           "GTFS stops counted as each point of interest (within its stop radius).",
           {"dist_m": "Distance from the point of interest in metres"}, schema=SILVER)
write_gold(departures, "transit_departures",
           "TransLink trips departing each point of interest (first stop within its radius), with route group and capacity. Current GTFS feed (Sept 2026).",
           {"route_group": "Route group for this POI (see pois.json) or EXCLUDE",
            "capacity": "Approximate capacity in bus-equivalents (bus 1, SkyTrain/SeaBus 5, West Coast Express 12)",
            "departure_time": "Scheduled departure at the POI stop (GTFS time, may exceed 24:00:00)"},
           schema=SILVER)
write_gold(service, "transit_service_30min",
           "Scheduled TransLink departures and capacity from each point of interest per service day type, 30-minute slot and route group (current feed applied to all history).",
           {"service_day_type": "WEEKDAY, SATURDAY or SUNDAY_HOLIDAY",
            "slot_of_day": "30-minute slot index 0..47",
            "route_group": "Route group for this POI (see pois.json)",
            "scheduled_departures": "Scheduled trips leaving the POI in the slot",
            "scheduled_capacity": "Scheduled capacity in bus-equivalents leaving the POI in the slot"})
