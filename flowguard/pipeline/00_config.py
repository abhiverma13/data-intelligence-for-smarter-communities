# Databricks notebook source
# MAGIC %md
# MAGIC # FlowGuard · 00 — Config
# MAGIC Single source of truth for names, paths and thresholds. Every other notebook starts with
# MAGIC `%run ./00_config`. You never need to run this notebook on its own.
# MAGIC
# MAGIC **Catalog fallback:** if the `flowguard` catalog exists we use `flowguard.bronze / silver / gold`.
# MAGIC If it doesn't (catalog creation not permitted), we use the Free Edition default catalog
# MAGIC `workspace` with schemas `flowguard_bronze / flowguard_silver / flowguard_gold`.

# COMMAND ----------

import os

# ---------- Unity Catalog ----------
PREFERRED_CATALOG = "flowguard"
FALLBACK_CATALOG = "workspace"          # Free Edition default catalog
FALLBACK_SCHEMA_PREFIX = "flowguard_"


def _catalog_exists(name: str) -> bool:
    return spark.sql(f"SHOW CATALOGS LIKE '{name}'").count() > 0


if _catalog_exists(PREFERRED_CATALOG):
    CATALOG, SCHEMA_PREFIX = PREFERRED_CATALOG, ""
else:
    CATALOG, SCHEMA_PREFIX = FALLBACK_CATALOG, FALLBACK_SCHEMA_PREFIX

BRONZE_SCHEMA = f"{SCHEMA_PREFIX}bronze"
SILVER_SCHEMA = f"{SCHEMA_PREFIX}silver"
GOLD_SCHEMA = f"{SCHEMA_PREFIX}gold"
BRONZE = f"{CATALOG}.{BRONZE_SCHEMA}"
SILVER = f"{CATALOG}.{SILVER_SCHEMA}"
GOLD = f"{CATALOG}.{GOLD_SCHEMA}"

# ---------- Raw files (uploaded by hand to the Volume) ----------
RAW_VOLUME = f"{BRONZE}.raw"
RAW_PATH = f"/Volumes/{CATALOG}/{BRONZE_SCHEMA}/raw"
GTFS_PATH = f"{RAW_PATH}/gtfs"
PR_FILE = "synthetic_park_royal_mall.csv"
GTFS_FILES = ["stops", "routes", "trips", "stop_times", "calendar", "calendar_dates"]

# ---------- Repo config CSVs (flowguard/config/) ----------
def _find_config_dir() -> str:
    candidates = [os.path.abspath(os.path.join(os.getcwd(), "..", "config"))]
    try:
        nb_path = dbutils.notebook.entry_point.getDbutils().notebook().getContext().notebookPath().get()
        candidates.append(os.path.abspath(os.path.join("/Workspace" + os.path.dirname(nb_path), "..", "config")))
    except Exception:
        pass
    for c in candidates:
        if os.path.isdir(c):
            return c
    return candidates[0]


CONFIG_DIR = _find_config_dir()

# ---------- Data rules (spec §4.2) ----------
TS_IS_UTC = False                      # timestamps are Vancouver local clock time despite the 'Z'
LOCAL_TZ = "America/Vancouver"         # only used if TS_IS_UTC is flipped to True
POI_NAME = "Park Royal Mall"
POI_LAT, POI_LON = 49.3265, -123.138
SLOT_MINUTES = 30
SLOTS_PER_DAY = 48
LONG_STAY_MINUTES = 24 * 60            # dwell above this is flagged long_stay (kept for egress)

# ---------- Model (spec §5.2.4) ----------
TRAIN_END = "2026-07-01"               # kernel trained before this date; Jul–Aug is the holdout
HORIZONS = [1, 2, 3, 4]                # +30 … +120 min
KERNEL_MAX_K = 48                      # half-hour slots
MLFLOW_EXPERIMENT = "/Shared/flowguard-egress"

# ---------- Thresholds ----------
SURGE_RATIO_THRESHOLD = 1.6
PRESSURE_LEVELS = [(1.3, "Normal"), (1.8, "Elevated"), (2.5, "High"), (float("inf"), "Severe")]
READINESS_LEVELS = [(1.25, "Prepared"), (2.0, "Watch"), (3.0, "Strained"), (float("inf"), "Critical")]
SIGNATURE_REGIONAL_PP = 0.05           # regional share ≥ baseline + 5 pp
SIGNATURE_STAY_LONG = 1.15             # median dwell ≥ 1.15× baseline
SIGNATURE_STAY_SHORT = 0.85            # median dwell ≤ 0.85× baseline
SIGNATURE_VISITOR_X = 1.5              # out-of-region share ≥ 1.5× baseline
TYPICAL_LOAD_HOURS = (10, 20)          # daytime window for typical_load
GTFS_STOP_RADIUS_M = 400

print(f"FlowGuard config → catalog={CATALOG}  bronze={BRONZE}  silver={SILVER}  gold={GOLD}")
print(f"                   raw files={RAW_PATH}  config csvs={CONFIG_DIR}")
