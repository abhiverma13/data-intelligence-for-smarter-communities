"""FlowGuard settings shared by the Databricks notebooks (via 00_config) and local scripts.

Plain Python (not a notebook) so both environments import the exact same values.
"""

# ---------- Data rules (spec §4.2) ----------
TS_IS_UTC = False                      # timestamps are Vancouver local clock time despite the 'Z'
LOCAL_TZ = "America/Vancouver"         # only used if TS_IS_UTC is flipped to True
POI_NAME = "Park Royal Mall"
POI_LAT, POI_LON = 49.3265, -123.138
SLOT_MINUTES = 30
SLOTS_PER_DAY = 48
LONG_STAY_MINUTES = 24 * 60            # dwell above this is flagged long_stay (kept for egress)
OCC_EXCLUDE_LONG_STAY = True           # multi-day devices (residents/workers) excluded from occupancy
DATA_START = "2025-11-01"
DATA_END = "2026-08-31"                # last date with arrivals (inclusive)

CORRIDORS = ["NS_WEST", "NS_EAST", "EAST", "SOUTH", "BC_OTHER", "OUT_OF_REGION"]
REGIONAL_CORRIDORS = ["SOUTH", "EAST", "BC_OTHER"]
DAY_TYPES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

# ---------- Model (spec §5.2.4) ----------
TRAIN_END = "2026-07-01"               # kernel trained before this date; Jul–Aug is the holdout
HORIZONS = [1, 2, 3, 4]                # +30 … +120 min
KERNEL_MAX_K = 48                      # half-hour slots
MLFLOW_EXPERIMENT = "/Shared/flowguard-egress"
TODAY_SCALING = True                   # scale not-yet-arrived typical arrivals by today's trailing-2 h busyness
TODAY_FACTOR_CLIP = (0.5, 3.0)

# ---------- Thresholds ----------
# 1.6 in the spec came from the truncated sample; on the uncut data only 3 days reach it.
SURGE_RATIO_THRESHOLD = 1.4
NORMAL_DAY_MAX_RATIO = 1.3             # days below this count as "normal" for typical_load
PRESSURE_LEVELS = [(1.3, "Normal"), (1.8, "Elevated"), (2.5, "High"), (float("inf"), "Severe")]
# Calibrated on normal-day daytime gaps (uncut data): Watch ≥ p75, Strained ≥ p95, Critical ≥ p99.
READINESS_LEVELS = [(1.2, "Prepared"), (1.7, "Watch"), (2.3, "Strained"), (float("inf"), "Critical")]
BASELINE_OCC_FLOOR_SHARE = 0.10        # baseline occupancy floored at 10% of its daily max (night noise)
PEAK_WINDOW_SLOTS = (16, 44)           # 08:00–22:00: where day-level peak pressure is searched
TRAILING_SLOTS = 4                     # mobility signature looks at the trailing 2 h of arrivals
SIGNATURE_REGIONAL_PP = 0.05           # regional share ≥ baseline + 5 pp
SIGNATURE_STAY_LONG = 1.15             # median dwell ≥ 1.15× baseline
SIGNATURE_STAY_SHORT = 0.85            # median dwell ≤ 0.85× baseline
SIGNATURE_VISITOR_X = 1.5              # out-of-region share ≥ 1.5× baseline
TYPICAL_LOAD_SLOTS = (20, 40)          # 10:00–20:00 daytime window for typical_load

# ---------- GTFS / transit readiness ----------
GTFS_STOP_RADIUS_M = 400               # stops counted as "Park Royal"
GTFS_TERMINATING_RADIUS_M = 600        # trips ending this close to the mall arrive, not depart
# One representative service date per day type, inside the current feed (Sept 7 2026 → Jan 3 2027).
SERVICE_REP_DATES = {"WEEKDAY": "2026-10-14", "SATURDAY": "2026-10-17", "SUNDAY_HOLIDAY": "2026-10-18"}
MIN_DEMAND_SHARE = 0.5                 # below half the usual daytime demand a route group is never escalated
SERVICE_SMOOTH_SLOTS = 2              # svc = mean trips over the slot and the next one (60-min window)
ROUTE_GROUPS = ["EASTBOUND", "DOWNTOWN", "WEST_VAN_LOCAL"]
# Keyword rules on trip_headsign, first match wins (case-insensitive). Overrides CSV beats these.
ROUTE_GROUP_RULES = [
    ("school special", "EXCLUDE"),
    ("metrotown", "EASTBOUND"),
    ("phibbs", "EASTBOUND"),
    ("lonsdale", "EASTBOUND"),
    ("capilano university", "EASTBOUND"),
    ("vancouver", "DOWNTOWN"),
    ("ubc", "DOWNTOWN"),
    ("downtown", "DOWNTOWN"),
]
ROUTE_GROUP_DEFAULT = "WEST_VAN_LOCAL"
GROUP_CORRIDORS = {
    "EASTBOUND": ["EAST", "NS_EAST"],
    "DOWNTOWN": ["SOUTH"],
    "WEST_VAN_LOCAL": ["NS_WEST"],
}
GROUP_LABELS = {"EASTBOUND": "Eastbound", "DOWNTOWN": "Downtown", "WEST_VAN_LOCAL": "West Van local"}
