# FlowGuard

*See the pressure before it hits.* FlowGuard uses anonymous cell-tower data (how many people are at a place and
how long they stay) to forecast when crowds will leave, up to two hours ahead, and whether scheduled TransLink
service is ready for them. It runs for three points of interest (POIs): Park Royal, UBC and Waterfront Station.

Verified numbers: [docs/data_findings.md](docs/data_findings.md). The original design brief is
[docs/FLOWGUARD_SPEC.md](docs/FLOWGUARD_SPEC.md); some of its details and numbers were superseded during the build.

## Layout

```
flowguard/
  config/     reference CSVs → bronze.ref_* (home area → corridor per POI, route-group overrides, holidays)
  pipeline/   Databricks notebooks 00–08 and 90 (".py" notebook-source format)
              fg_settings.py (all thresholds) and fg_core.py (all formulas): plain Python, shared with local scripts
  app/        Databricks App: FastAPI backend (app.py, server/) + static frontend (static/, Chart.js and Leaflet vendored)
              server/pois.json defines every POI (file, stops, route groups, operator actions, demo days)
  local/      run_local.py, build_snapshot.py, build_map_geometry.py; local/data/ is gitignored
  deploy/     app.json (app + SQL warehouse resource), job.json (pipeline Job), job_reset.json (update the Job)
  docs/       verified findings, Genie space setup, original design brief
```

## Pipeline

Notebooks run on **serverless**. The normal way to rebuild everything is the Job `flowguard-refresh`
([deploy/job.json](deploy/job.json)): setup → bronze → silver → gold → model → timeline → outlook, with the transit
schedule step running in parallel after bronze.

```
databricks jobs run-now 744392775638066 --profile flowguard
```

| Notebook | Produces |
|---|---|
| `00_config` | shared settings and helpers, loaded by every notebook via `%run ./00_config` |
| `01_setup_uc` | catalog, schemas, `raw` Volume, `bronze.ref_*` tables |
| `02_bronze` | `bronze.mobility_raw` (all POIs), `bronze.gtfs_*` |
| `03_silver` | `silver.visits` and data-quality checks per POI |
| `04_gold_slots` | `gold.slots`, `gold.slot_corridor`, `gold.days` (incl. the after-hours watch) |
| `05_egress_model` | `gold.egress_kernel`, `gold.egress_forecast`, `gold.model_backtest`, and one MLflow run per POI |
| `06_gtfs_service` | `silver.transit_stops`, `silver.transit_departures`, `gold.transit_service_30min` |
| `07_timeline` | `gold.flowguard_timeline` (readiness and actions; the main table the app reads) |
| `08_outlook` | `gold.outlook_days`, `gold.outlook_slots`, `gold.outlook_slot_corridor`, `gold.outlook_timeline` (future dates, Sept 7 2026 – Jan 3 2027) |
| `90_app_grants` | one-off: grants the app's service principal read access to `gold` and run access to the Genie space |

Every table has a `poi` column (`park_royal`, `ubc`, `waterfront`). Raw data is never committed: the three
mobility CSVs sit at the root of the Unity Catalog Volume `flowguard.bronze.raw`, and the TransLink GTFS text
files under `raw/gtfs/`. If `CREATE CATALOG` isn't permitted, everything falls back automatically to the
`workspace` catalog with `flowguard_bronze / flowguard_silver / flowguard_gold` schemas.

## App

A Park Royal / UBC / Waterfront switcher, day picker (history, and outlook days marked **Outlook**) and replay
clock, with:
- current pressure, mobility signature and catchment
- a **Map | Radar** view of the exit wave and each route group's readiness, with the after-hours band (radar) and
  badge (map)
- the readiness table and action card
- **Scenario Lab**, a printable **Briefing**, and **Ask FlowGuard** (a Genie chat that streams its progress)

`DATA_MODE=live` reads the gold tables through the SQL warehouse and falls back automatically to the bundled
snapshot in `app/static/data/<poi>/`; `DATA_MODE=snapshot` never touches the warehouse. The Genie space is set by
`GENIE_SPACE_ID` in [app/app.yaml](app/app.yaml); its setup is in [docs/genie_instructions.md](docs/genie_instructions.md).

## Workflow: laptop → GitHub → Databricks

1. Edit code locally, then `git push`.
2. In Databricks, open the Git folder → branch button → **Pull**.
3. Redeploy the app if anything under `app/` changed:
   ```
   databricks apps deploy flowguard --source-code-path /Workspace/Users/a.verma1304@gmail.com/data-intelligence-for-smarter-communities/flowguard/app --profile flowguard
   ```
4. Re-run the Job if anything in `pipeline/` or `config/` changed.

Don't edit notebooks in the Databricks UI; the two copies would conflict. Apps stop 24 h after they are started
or deployed: `databricks apps start flowguard --profile flowguard`.

## Local run

```
python flowguard/local/run_local.py        # every gold table, all POIs → flowguard/local/data/gold/*.csv
python flowguard/local/build_snapshot.py   # refresh the app's offline snapshot from those tables
python flowguard/local/build_map_geometry.py   # only when route groups or the GTFS feed change
cd flowguard/app && DATA_MODE=snapshot .venv/Scripts/python -m uvicorn app:app --port 8000
```

`run_local.py` reads the raw CSVs from `FLOWGUARD_DATA_DIR` (default `~/Downloads/OneDrive_1_2026-09-25/`) and
the GTFS files from `local/data/gtfs/` (source: https://gtfs-static.translink.ca/gtfs/google_transit.zip).

## Data rules

- Timestamps are **Vancouver local time** despite the `Z` suffix (`TS_IS_UTC = False` in `pipeline/fg_settings.py`).
- `dwell_time` is minutes; departure = arrival + dwell time.
- 30-minute slots are the base grain.
- The data is a synthetic sample, so the app shows ratios ("× normal") and shares, never headcounts.
