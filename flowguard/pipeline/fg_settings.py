"""FlowGuard settings shared by the Databricks notebooks (via 00_config) and local scripts.

Plain Python (not a notebook) so both environments import the exact same values.
Per-location settings (corridors, stops, route groups, operator levers, demo days) live in
flowguard/app/server/pois.json, the single source of truth also read by the app.
"""
import json
from pathlib import Path

# ---------- Points of interest ----------
_POI_FILE = Path(__file__).resolve().parents[1] / "app" / "server" / "pois.json"
with open(_POI_FILE, encoding="utf-8") as _f:
    _CFG = json.load(_f)
POIS = _CFG["pois"]                                  # ordered list of POI dicts
POI_BY_KEY = {p["key"]: p for p in POIS}
CAPACITY = {k: v for k, v in _CFG["capacity_bus_equivalents"].items() if not k.startswith("_")}

# ---------- Data rules (spec §4.2) ----------
TS_IS_UTC = False                      # timestamps are Vancouver local clock time despite the 'Z'
LOCAL_TZ = "America/Vancouver"         # only used if TS_IS_UTC is flipped to True
SLOT_MINUTES = 30
SLOTS_PER_DAY = 48
LONG_STAY_MINUTES = 24 * 60            # dwell above this is flagged long_stay (kept for egress)
OCC_EXCLUDE_LONG_STAY = True           # multi-day devices (residents/workers) excluded from occupancy
DATA_START = "2025-11-01"
DATA_END = "2026-08-31"                # last date with arrivals (inclusive)
DAY_TYPES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

# ---------- Model (spec §5.2.4) ----------
TRAIN_END = "2026-07-01"               # kernel trained before this date; Jul–Aug is the holdout
HORIZONS = [1, 2, 3, 4]                # +30 … +120 min
KERNEL_MAX_K = 48                      # half-hour slots
MLFLOW_EXPERIMENT = "/Shared/flowguard-egress"
TODAY_SCALING = True                   # scale not-yet-arrived typical arrivals by today's trailing-2 h busyness
TODAY_FACTOR_CLIP = (0.5, 3.0)

# ---------- Thresholds ----------
# 1.6 in the spec came from the truncated sample; on the uncut data only 3 Park Royal days reach it.
SURGE_RATIO_THRESHOLD = 1.4
# Days below this surge ratio feed typical_load. No cap: at UBC the weekday medians include the quiet
# summer, so a 1.3 cap would call most term days abnormal and make every term day look strained.
NORMAL_DAY_MAX_RATIO = float("inf")
PRESSURE_LEVELS = [(1.3, "Normal"), (1.8, "Elevated"), (2.5, "High"), (float("inf"), "Severe")]
# Calibrated on Park Royal normal-day daytime gaps: Watch ≥ p75, Strained ≥ p95, Critical ≥ p99.
READINESS_LEVELS = [(1.2, "Prepared"), (1.7, "Watch"), (2.3, "Strained"), (float("inf"), "Critical")]
BASELINE_OCC_FLOOR_SHARE = 0.10        # baseline occupancy floored at 10% of its daily max (night noise)
PEAK_WINDOW_SLOTS = (16, 44)           # 08:00–22:00: where day-level peak pressure is searched
TRAILING_SLOTS = 4                     # mobility signature looks at the trailing 2 h of arrivals
SIGNATURE_REGIONAL_PP = 0.05           # regional share ≥ baseline + 5 pp
SIGNATURE_STAY_LONG = 1.15             # median dwell ≥ 1.15× baseline
SIGNATURE_STAY_SHORT = 0.85            # median dwell ≤ 0.85× baseline
SIGNATURE_VISITOR_X = 1.5              # out-of-region share ≥ 1.5× baseline
TYPICAL_LOAD_SLOTS = (20, 40)          # 10:00–20:00 daytime window for typical_load
# After-hours watch (security): overnight presence = mean occupancy 00:00–06:00 vs a normal night for that
# weekday (seasonal window for seasonal POIs). Volume only, never visitor origin.
NIGHT_SLOTS = 12                       # 00:00–06:00
NIGHT_UNUSUAL_RATIO = 1.5              # ≈ the top 1–2% of nights at Park Royal / Waterfront

# ---------- GTFS / transit readiness ----------
# One representative service date per day type, inside the current feed (Sept 7 2026 → Jan 3 2027).
SERVICE_REP_DATES = {"WEEKDAY": "2026-10-14", "SATURDAY": "2026-10-17", "SUNDAY_HOLIDAY": "2026-10-18"}
# Outlook (future days): the dates the published GTFS feed covers, after the mobility data ends.
OUTLOOK_START = "2026-09-07"
OUTLOOK_END = "2027-01-03"
OUTLOOK_ANALOG_WEEKS = 1               # same weekday last year ±1 week (3 analog days)
MIN_DEMAND_SHARE = 0.5                # below half the usual daytime demand a route group is never escalated
SERVICE_SMOOTH_SLOTS = 2               # svc = mean capacity over the slot and the next one (60-min window)


def corridors(poi: dict) -> list:
    return [c["key"] for c in poi["corridors"]]


def groups(poi: dict) -> list:
    return [g["key"] for g in poi["groups"]]


def group_corridors(poi: dict) -> dict:
    return {g["key"]: g["corridors"] for g in poi["groups"]}
