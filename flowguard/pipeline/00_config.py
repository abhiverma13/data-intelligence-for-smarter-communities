# Databricks notebook source
# MAGIC %md
# MAGIC # FlowGuard · 00 — Config
# MAGIC Loaded by every other notebook with `%run ./00_config`. You never need to run it on its own.
# MAGIC
# MAGIC - Thresholds and data rules live in **`fg_settings.py`** and every formula in **`fg_core.py`** (plain
# MAGIC   Python files next to this notebook, shared with the local scripts so both compute identical numbers).
# MAGIC - **Catalog fallback:** if the `flowguard` catalog exists we use `flowguard.bronze / silver / gold`,
# MAGIC   otherwise the Free Edition default catalog `workspace` with `flowguard_bronze / …` schemas.

# COMMAND ----------

import importlib
import os
import sys

if os.getcwd() not in sys.path:
    sys.path.insert(0, os.getcwd())

import fg_settings
import fg_core

importlib.reload(fg_settings)   # pick up edits after a Git pull without restarting the session
importlib.reload(fg_core)
from fg_settings import *        # noqa: F401,F403  (TS_IS_UTC, POI_*, thresholds, …)

# ---------- Unity Catalog ----------
PREFERRED_CATALOG = "flowguard"
FALLBACK_CATALOG = "workspace"          # Free Edition default catalog
FALLBACK_SCHEMA_PREFIX = "flowguard_"


def _catalog_usable(name: str) -> bool:
    try:
        spark.sql(f"USE CATALOG `{name}`")
        return True
    except Exception:
        return False


if _catalog_usable(PREFERRED_CATALOG):
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
GTFS_FILES = ["stops", "routes", "trips", "stop_times", "calendar", "calendar_dates"]
# Points of interest (Park Royal, UBC, Waterfront) come from app/server/pois.json via fg_settings.POIS;
# each has a raw CSV file name (poi["file"]) expected at the root of the raw Volume.
POI_KEYS = [p["key"] for p in POIS]

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

print(f"FlowGuard config → catalog={CATALOG}  bronze={BRONZE}  silver={SILVER}  gold={GOLD}")
print(f"                   raw files={RAW_PATH}  config csvs={CONFIG_DIR}")

# COMMAND ----------

# DBTITLE 1,Helpers shared by the notebooks
import pandas as pd


def load_ref(csv_name: str, table: str, columns: list, casts: dict, comment: str):
    """Load a repo config CSV into a small bronze.ref_* table."""
    pdf = pd.read_csv(os.path.join(CONFIG_DIR, csv_name), dtype=str, keep_default_na=False)
    missing = set(columns) - set(pdf.columns)
    assert not missing, f"{csv_name} is missing columns {missing}"
    rows = [tuple(r) for r in pdf[columns].itertuples(index=False)]
    df = spark.createDataFrame(rows, ", ".join(f"{c} STRING" for c in columns))
    df = df.selectExpr(*[casts.get(c, c) for c in columns])
    full = f"{BRONZE}.{table}"
    df.write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(full)
    spark.sql(f"COMMENT ON TABLE {full} IS '{comment}'")
    print(f"✅ {full}: {df.count()} rows")


def from_spark(df, ts_cols=(), date_cols=()):
    """Spark → pandas with timestamps passed as strings (avoids any session-timezone shifting)."""
    exprs = []
    for c in df.columns:
        if c in ts_cols:
            exprs.append(f"date_format(`{c}`, 'yyyy-MM-dd HH:mm:ss') AS `{c}`")
        elif c in date_cols:
            exprs.append(f"CAST(`{c}` AS STRING) AS `{c}`")
        else:
            exprs.append(f"`{c}`")
    pdf = df.selectExpr(*exprs).toPandas()
    for c in list(ts_cols) + list(date_cols):
        pdf[c] = pd.to_datetime(pdf[c])
    return pdf


def write_gold(pdf: pd.DataFrame, table: str, comment: str, column_comments: dict,
               ts_cols=(), date_cols=(), schema: str = None):
    """pandas → Delta table. Timestamps are written as TIMESTAMP_NTZ (local clock), dates as DATE."""
    pdf = pdf.copy()
    for c in ts_cols:
        pdf[c] = pd.to_datetime(pdf[c]).dt.strftime("%Y-%m-%d %H:%M:%S")
    for c in date_cols:
        pdf[c] = pd.to_datetime(pdf[c]).dt.strftime("%Y-%m-%d")
    for c in pdf.columns:
        if pdf[c].dtype == object and c not in ts_cols and c not in date_cols:
            pdf[c] = pdf[c].where(pdf[c].notna(), None)
    float_cols = {c for c in pdf.columns if pd.api.types.is_float_dtype(pdf[c])}
    sdf = spark.createDataFrame(pdf)
    sdf = sdf.selectExpr(*[
        f"CAST(`{c}` AS TIMESTAMP_NTZ) AS `{c}`" if c in ts_cols
        else f"CAST(`{c}` AS DATE) AS `{c}`" if c in date_cols
        else f"CASE WHEN isnan(`{c}`) THEN NULL ELSE `{c}` END AS `{c}`" if c in float_cols   # NaN → NULL for SQL/Genie
        else f"`{c}`"
        for c in pdf.columns
    ])
    full = f"{schema or GOLD}.{table}"
    sdf.write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(full)
    q = lambda s: s.replace("'", "\\'")  # noqa: E731
    spark.sql(f"COMMENT ON TABLE {full} IS '{q(comment)}'")
    for c, text in column_comments.items():
        spark.sql(f"ALTER TABLE {full} ALTER COLUMN `{c}` COMMENT '{q(text)}'")
    print(f"✅ {full}: {len(pdf):,} rows")
