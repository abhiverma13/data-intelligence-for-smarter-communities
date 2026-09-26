# FlowGuard — Park Royal Mobility Intelligence

*See the pressure before it hits.* FlowGuard uses dwell time in Park Royal's cell-tower data to
forecast **when** a crowd will leave, **which direction** it heads, and whether scheduled
TransLink service is ready for it. Full design: [docs/FLOWGUARD_SPEC.md](docs/FLOWGUARD_SPEC.md).

## Layout

```
flowguard/
  config/     reference CSVs → bronze.ref_* (origin→corridor, route-group overrides, holidays)
  pipeline/   Databricks notebooks (.py "notebook source" format), run in numeric order
  app/        Databricks App (FastAPI backend + static frontend)
  local/      local-only helpers; local/data/ holds raw downloads and is gitignored
  docs/       spec, pitch script, Genie instructions
```

## Workflow: laptop → GitHub → Databricks

1. Edit code locally, then `git push`.
2. In Databricks, open the Git folder → branch button → **Pull**.
3. Don't edit notebooks in the Databricks UI (the two copies will conflict).

Raw data is never committed. The Park Royal CSV and the TransLink GTFS files are uploaded by
hand to the Unity Catalog Volume `flowguard.bronze.raw` (GTFS under `raw/gtfs/`).

## Pipeline run order

Attach **Serverless**, run cells one at a time (no "Run all").

| Notebook | Produces |
|---|---|
| `00_config` | shared settings + helpers, loaded by every notebook via `%run ./00_config` |
| `01_setup_uc` | catalog, schemas, `raw` Volume, `bronze.ref_*` tables |
| `02_bronze` | `bronze.mobility_raw` (all POIs), `bronze.gtfs_*` |
| `03_silver` | `silver.visits` + data quality checks per POI |
| `04_gold_slots` | `gold.slots`, `gold.slot_corridor`, `gold.days` |
| `05_egress_model` | `gold.egress_kernel`, `gold.egress_forecast`, `gold.model_backtest` + one MLflow run per POI |
| `06_gtfs_service` | `silver.transit_stops`, `silver.transit_departures`, `gold.transit_service_30min` |
| `07_timeline` | `gold.flowguard_timeline` (the table the app reads) |

Every table has a `poi` column: `park_royal`, `ubc`, `waterfront`. Each point of interest (raw file,
coordinates, stop radius, corridors, route groups and their headsign rules, operator levers, demo days,
optional seasonal baseline) is defined once in [app/server/pois.json](app/server/pois.json), read by both the
pipeline and the app. Origin → corridor mappings per POI are in `config/origin_corridor.csv`.

All thresholds live in `pipeline/fg_settings.py` and all formulas in `pipeline/fg_core.py` (plain Python,
imported by the notebooks and by `local/run_local.py`, so Databricks and local runs give identical numbers).
Verified numbers for the pitch: [docs/data_findings.md](docs/data_findings.md).

| `90_app_grants` | grants the app's service principal read access to `gold` |

The Job `flowguard-refresh` ([deploy/job.json](deploy/job.json)) chains 01 → 07 on serverless.

## App

`app/` is a Databricks App: FastAPI (`app.py`, `server/`) + a static page (`static/index.html`, `app.js`,
`styles.css`, Chart.js bundled in `static/vendor/`). `DATA_MODE=live` reads the gold tables through the SQL
warehouse and falls back automatically to the bundled snapshot `static/data/*.json`; `DATA_MODE=snapshot`
never touches the warehouse. Scenario Lab maths runs in the browser (`?selftest=1` checks it matches the server).

```
python flowguard/local/run_local.py && python flowguard/local/build_snapshot.py    # refresh the snapshot
cd flowguard/app && .venv/Scripts/python -m uvicorn app:app --port 8000            # DATA_MODE=snapshot to run offline
```

## Local run

```
python flowguard/local/run_local.py      # builds every gold table to flowguard/local/data/gold/*.csv
```
Set `FLOWGUARD_PR_CSV` if the Park Royal CSV isn't in `~/Downloads/OneDrive_1_2026-09-25/`.

If `CREATE CATALOG` isn't permitted, everything falls back to the `workspace` catalog with
`flowguard_bronze / flowguard_silver / flowguard_gold` schemas automatically.

## Data rules

- Timestamps are **Vancouver local time** despite the `Z` suffix (`TS_IS_UTC = False` in `00_config`).
- `dwell_time` is minutes; `departure_ts = arrival_ts + dwell_time`.
- 30-minute slots are the base grain.
- Synthetic sample → the UI shows ratios ("× normal") and shares, never headcounts.

## Local data (gitignored)

- Park Royal CSV: stays where it was downloaded (`synthetic_park_royal_mall.csv`).
- GTFS: `local/data/google_transit.zip`, unzipped to `local/data/gtfs/`
  (source: https://gtfs-static.translink.ca/gtfs/google_transit.zip).
