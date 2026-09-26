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
| `00_config` | shared settings, loaded by every notebook via `%run ./00_config` |
| `01_setup_uc` | catalog, schemas, `raw` Volume, `bronze.ref_*` tables |
| `02_bronze` | `bronze.pr_raw`, `bronze.gtfs_*` |
| `03_silver` | `silver.pr_visits` + data quality checks |

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
