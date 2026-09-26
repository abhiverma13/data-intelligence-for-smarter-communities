# AGENTS.md

Guidance for AI coding agents working in this repo. Read this before changing anything.

## What this is

**FlowGuard**: a hackathon project (Rogers × Databricks × UBC, Sept 25–27 2026) built on Databricks Free Edition.
It uses synthetic cell-tower dwell data at three points of interest (POIs): **Park Royal** (the headline story),
**UBC** and **Waterfront Station**. For each, it forecasts **when** the crowd will leave, **which direction** it heads,
and whether scheduled TransLink service can carry it. The primary user is TransLink operations staff.

Every POI is defined once in `flowguard/app/server/pois.json`: raw file, coordinates, stop radius, corridors, route
groups with headsign rules, operator levers, demo days, and an optional seasonal baseline (`baseline_weeks`, used by
UBC because summer break differs from term). The pipeline reads it through `fg_settings.POIS`; the app reads it
directly. Every table carries a `poi` column. To add a POI, edit `pois.json` and `config/origin_corridor.csv` only.

- Design spec: `flowguard/docs/FLOWGUARD_SPEC.md`. Its §4.3 numbers came from a truncated sample and are **superseded**
  by `flowguard/docs/data_findings.md`. Quote the findings doc, never spec §4.3.
- Everything lives under `flowguard/`. The original workshop code was removed; it is still in git history
  (commit `6c6d14d`) if you need a reference, e.g. `git show 6c6d14d:"workshop/06 - Hands On - Deploy the App.py"`.

## Layout

```
flowguard/
  pipeline/   Databricks notebooks 00–08, 90 (".py" files starting with "# Databricks notebook source")
              fg_settings.py  every threshold and constant (plain Python)
              fg_core.py      every formula (plain pandas/numpy)
  config/     CSVs loaded into bronze.ref_* (origin→corridor, route-group overrides, day-type overrides/holidays)
  app/        Databricks App: app.py + server/ (FastAPI) + static/ (vanilla JS, Chart.js + Leaflet vendored)
              static/map.js  Leaflet corridor map: real GTFS routes, end labels, flow dots + per-route exit-wave cards
              static/data/<poi>/map.json  committed map geometry per POI (route polylines, corridor ends, waypoints, land)
  local/      run_local.py (all gold tables locally), build_snapshot.py (app offline JSON)
              build_map_geometry.py (local GTFS shapes → app/static/data/<poi>/map.json for every POI)
              local/data/ is gitignored: raw GTFS, local gold CSVs
  deploy/     app.json (app + warehouse resource), job.json (refresh job, create), job_reset.json (Verma's job, reset)
  docs/       spec, verified findings
```

## Invariants: do not break these

1. **One implementation of every formula.** All metric logic lives in `fg_core.py`, and all thresholds live in
   `fg_settings.py` (per-POI settings in `pois.json`). Notebooks use Spark only to aggregate the ~21M visits to
   slots, then call `fg_core` once per POI.
   `local/run_local.py` calls the same functions. Never re-implement a formula inside a notebook.
2. **The browser mirrors `fg_core`.** `app/static/app.js` (`view`, `actionsAt`) rescales the server's gap for
   scenarios and re-implements the readiness/action logic so the Scenario Lab can recompute instantly;
   `app/static/map.js` consumes that same `view()` so the map can never disagree with the readiness table.
   `app/server/logic.py` holds copies of the
   thresholds. If you change readiness or action logic in `fg_core`/`fg_settings`, update both, then run the
   self-test (see Verification).
3. **Timestamps are Vancouver local clock time**, despite the `Z` suffix in the raw data. They are stored as
   `TIMESTAMP_NTZ`. Move them between Spark and pandas as strings (the `from_spark` / `write_gold` helpers in
   `00_config.py`); never rely on session time zones.
4. **Relative numbers only in the UI.** The data is a synthetic sample, so show "× normal", shares or an index
   (normal-day peak = 100), never headcounts or "people".
5. **Never commit raw data.** The three mobility CSVs (0.4–0.6 GB each) live outside the repo and in the
   Databricks Volume `flowguard.bronze.raw`. The `static/data/` JSON snapshot is intentionally committed.
6. **Gold tables are the contract with the app.** If you add or rename a gold column, update `write_gold` comments
   in the notebook, `SLOT_COLS`/`TIMELINE_COLS` in `app/server/logic.py`, and rebuild the snapshot.
   In live mode, a missing column makes the app silently fall back to the snapshot. Check `/api/health`
   → `last_source`.
7. **The map geometry is a committed static asset.** `app/static/data/<poi>/map.json` is generated for every POI in
   `pois.json` by `local/build_map_geometry.py` from GTFS `shapes.txt`. Departing trips and route groups come from
   `fg_core.poi_departures` (the same call as `06_gtfs_service`, including `route_group_overrides.csv`), so the map's
   lines match the readiness groups. Per-POI draw radius and waypoint labels live in the script's `MAP` dict.
   Each group's `end` point carries a permanent label (name + readiness chip + next-30-min index) facing away from the
   hub; `fit()` projects those labels at the target zoom and pads only the sides they would spill past. Groups also
   have a `corner` field that `map.js` no longer uses (the exit-wave cards now sit in a strip under the map).
   `map.js` rebuilds the layers when the location changes. Re-run the script and commit the `map.json` files if a POI,
   its rules or the GTFS feed change.
   `build_snapshot.py` only clears `<poi>/day/`, so it leaves `map.json` alone. The map is Leaflet
   (vendored in `static/vendor/`) over keyless Esri canvas basemap tiles (Light Gray light, Dark Gray dark; quiet so the routes lead), with
   the attribution kept visible. Route overlays, labels and cards come only from `map.json`, so if the tiles
   fail to load (offline) it falls back to the committed land silhouette and still works in snapshot mode.
   No API keys in the frontend. (CARTO basemaps now watermark tiles without a key, so don't switch to them.)

## Deliberate deviations from the spec (keep unless the data changes)

- Surge threshold is 1.4 (the spec's 1.6), because only 3 Park Royal days reach 1.6 on the uncut data.
- Readiness thresholds are 1.2 / 1.7 / 2.3, calibrated to the p75 / p95 / p99 of Park Royal normal-day daytime gaps.
  They give 3–9% Strained across the three POIs.
- Typical load is computed per route group **and service day type**, so normal Saturdays aren't flagged. It uses all
  training days (no "normal day" cap), because a ratio cap mislabels UBC term days. Seasonal POIs use a ±`baseline_weeks`
  window for pressure, surge ratio and typical load.
- Scheduled service is capacity in bus-equivalents (SkyTrain/SeaBus ≈ 5, West Coast Express ≈ 12); for bus-only
  Park Royal this equals the trip count.
- The forecast scales not-yet-arrived typical arrivals by today's trailing-2 h busyness (`TODAY_SCALING`).
  The spec formula is kept as `egress_static` in the backtest.
- **Outlook days** (future, `08_outlook`, `fg_core.build_outlook`): Sep 7 2026 – Jan 3 2027 = the window the GTFS
  feed covers. Crowd = median of analog past days (same holiday last year → same week last year ±1 → typical weekday);
  service = the published schedule of that exact date (`service_for_date`). The UI marks them with a violet
  OUTLOOK badge, a dashed explainer bar and dashed card borders; never present them as observed data.
- Occupancy excludes stays over 24 h. "No service" is shown when nothing is scheduled. Route groups are never
  escalated below half the usual daytime demand.

## Workflow

- The human runs `git push`. Databricks pulls through a **Git folder**, where the human clicks Pull. Code is
  never edited in the Databricks UI.
- Notebooks run on **serverless**. "Run all" is fine because this is a solo project. Order: 01 → 02 → 03 → 04 → 05,
  and 06 (after 02) → 07 (after 05 + 06) → 08. The job in `deploy/job.json` encodes this graph.
- Databricks CLI profile: `flowguard`. The SQL warehouse ID is in `deploy/app.json`. The Git folder path is in
  `deploy/job.json`.
- **This deployment** (Harsha) is a separate app on the same workspace as Verma's. Keep the two apart:

  | | Harsha (this branch) | Verma (leave alone) |
  |---|---|---|
  | Branch | `deploy/harsha-workspace-b` | `main` |
  | App | `flowguard-harsha` (own service principal) | `flowguard` |
  | App reads | `flowguard.gold` (shared, built by Verma's pipeline) | `flowguard.gold` |
  | Job / notebooks write | `flowguard-refresh-harsha` → `workspace.flowguard_*` (private copy) | `flowguard-refresh` → `flowguard.*` |
  | Code path | `/Workspace/Users/aminharsh317@gmail.com/data-intelligence-for-smarter-communities` | `/Workspace/Users/a.verma1304@gmail.com/…` |

  - Workspace `dbc-4c89dd88-f18b`, CLI profile `flowguard-b`; the one SQL warehouse is shared.
  - `app.yaml` pins the app to `flowguard.gold`. `90_app_grants` gives the app's SP `USE CATALOG` on `flowguard` and
    `USE SCHEMA` + `SELECT` on `flowguard.gold`. Harsha has `ALL_PRIVILEGES` on `flowguard`, granted by Verma.
  - `00_config.py` on this branch is pinned to `workspace.flowguard_*`, so these notebooks can never overwrite the
    shared tables. Don't run `flowguard-refresh-harsha` unless you mean to rebuild that private copy. Keep this pin
    when merging `main`, which prefers the `flowguard` catalog.
  - Pick up Verma's work with `git merge origin/main` into this branch. Keep Harsha's `app.yaml`, `00_config.py`,
    `90_app_grants.py` and the job name/paths in `job.json`. Never push this branch's config to `main`.
  - Deploy: `databricks sync ./flowguard <code path>/flowguard -p flowguard-b --exclude ".venv/**" --exclude
    "local/data/**" --exclude "**/__pycache__/**"`, then `databricks apps deploy flowguard-harsha --source-code-path
    <code path>/flowguard/app -p flowguard-b`. Large pushes to GitHub may need `git -c http.postBuffer=524288000 push`.
  - On Harsha's Mac the shell exports `DATABRICKS_HOST`/`DATABRICKS_TOKEN` for another workspace. Those override
    `-p`, so prefix every CLI call with `env -u DATABRICKS_HOST -u DATABRICKS_TOKEN`.
  - Verifying live: the PAT can't open the app (401) or read `apps logs` (needs OAuth), and the local Python SQL
    connector hits a self-signed-cert error. Run SQL through `databricks api post /api/2.0/sql/statements`, and ask
    the human to open `/api/health` in a browser to confirm `last_source=live`.
  - The Git-folder flow above is the original workflow and still applies to `main`.
- Free Edition limits:
  - one small SQL warehouse
  - compute quota: if exceeded, compute stops for the day, and the app's snapshot fallback exists for this reason
  - apps auto-stop 24 h after start/deploy, so restart before judging
  - notebooks have no outbound internet: download external files locally and upload them to the Volume
- Shell gotchas on Windows:
  - In Git Bash, prefix CLI calls that take workspace paths with `MSYS_NO_PATHCONV=1`.
  - In PowerShell, quote `--json "@file.json"`, because a bare `@` is PowerShell splatting.
  - Keep script output ASCII-safe or set `PYTHONIOENCODING=utf-8`. The console is cp1252.
- On macOS: the venv python is `.venv/bin/python`, and headless Chrome is
  `"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"`.

## Verification

```
# formulas and all gold tables locally (~20 s); prints backtest, surge days, route groups, readiness on demo days
python flowguard/local/run_local.py
# refresh the app's offline snapshot from those tables
python flowguard/local/build_snapshot.py
# regenerate the map geometry from local GTFS (flowguard/local/data/gtfs)
python flowguard/local/build_map_geometry.py
# run the app offline (venv lives in flowguard/app/.venv, gitignored)
cd flowguard/app && DATA_MODE=snapshot .venv/Scripts/python -m uvicorn app:app --port 8765   # macOS: .venv/bin/python
```

- **Client/server consistency:** open `/?date=2025-12-26&selftest=1` and read `body[data-selftest]`, which should
  show `readiness_mismatch=0/576`, or `0/768` at Waterfront with its 4 route groups (a single 23:30 cross-midnight
  mismatch on Saturdays is a known edge).
  Headless: `chrome --headless=new --virtual-time-budget=8000 --dump-dom "<url>"`.
- **Map view:** the hero card toggles `Radar | Map` (default map; `?view=radar` for the chart). The map reads the
  same `view()` maths, so the self-test also reports `map_mismatch=0`. Check every POI, one outlook date, and a
  location switch in the page (layers, end labels, cards and the KPI card must all change). Geometry is static, so only re-run
  `build_map_geometry.py` when route-group rules or the GTFS feed change.
- **Visual check:** after UI changes, take screenshots with
  `chrome --headless=new --window-size=1440,1250 --screenshot=out.png "<url>&theme=dark"`, and repeat with
  `theme=light` and a 1280 width. The URL params are `poi`, `date`, `t=HH:MM`, `theme`, `view=map|radar`, and
  `lab=1` (opens the Scenario Lab). Run the self-test for every POI (`?poi=ubc&date=…&selftest=1`).
- **Expected numbers** (`run_local.py` prints all of these):
  - Park Royal backtest R²: egress 0.97 / 0.96 / 0.95 / 0.94 vs typical week 0.64. Boxing Day: first eastbound
    alert at 11:00 for 13:00. Normal Saturday 2026-04-25: nearly all Prepared.
  - UBC: egress 0.99 → 0.93, typical week −1.09 (summer holdout). Waterfront: egress 0.99 → 0.97, typical week 0.68.
  - Daytime readiness mix: Strained ≈ 5% (Park Royal), 7% (UBC), 3% (Waterfront).

  If a change moves these, say so explicitly.

## Conventions

- Notebooks start with `%run ./00_config`, which puts `fg_settings`, `fg_core`, `CATALOG`/`BRONZE`/`SILVER`/`GOLD`
  and the helpers `load_ref`, `from_spark` and `write_gold` in scope. Write gold tables with `write_gold` so they
  get table and column comments (Genie and judges read these).
- Match the existing style: concise docstrings stating the formula, comments only for non-obvious decisions.
- The frontend has no build step (only `map.json` is generated, by `local/build_map_geometry.py`). Colours are
  CSS tokens in `styles.css`, with light and dark each defined separately. Status colours (good / warning /
  serious / critical) are only for readiness and pressure levels, always paired with an icon and a label; the
  map's route colour, end-label chips and card dots follow the same rule. Map encoding: colour = worst readiness in
  the next 2 h, width = next-30-min exit demand, moving dots = direction of travel (speed ∝ demand), hub ring =
  on-site pressure; animations stop under `prefers-reduced-motion`. Tile fade is off (`fadeAnimation: false`) because
  the flow animation can starve Leaflet's rAF fade in headless captures; `--disable-gpu` captures can also show a faint
  rectangular seam that a GPU render doesn't, so drop that flag when judging the map. `.mapbox` uses `isolation: isolate` so Leaflet's
  z-indexes (400–1000) stay under the sticky header (5) and drawers (10).
- Commit messages end with a `Co-Authored-By` line when an agent writes the commit. Never push, deploy, or run
  Databricks jobs without the human asking.

## Status and next steps

Done: pipeline 01–08, egress model with MLflow, GTFS service, readiness timeline, future-date outlook, app (live +
snapshot), deploy and job configs, Leaflet map view for every POI (real GTFS routes, labelled corridor ends, flow dots + per-route exit-wave cards,
Radar | Map toggle). Harsha's `flowguard-harsha` is deployed from `deploy/harsha-workspace-b` and reads the shared
`flowguard.gold`.
Pipeline and app are multi-POI (Park Royal, UBC, Waterfront): the app has a location switcher, every endpoint takes
`?poi=`, and the snapshot lives in `app/static/data/<poi>/`.
## Judging rubric (from the organisers' onboarding guide) and where we stand

Presentations are Sunday Sept 27, 11:30 AM: 5 min pitch + 3 min Q&A, round robin then a 4-team final. Scores below
are a self-assessment as of Sept 25 evening; use them to decide what to work on, and prefer work that moves points.

| Criterion | Pts | Est. | Evidence we have | Gap |
|---|---|---|---|---|
| Data analysis in Databricks (patterns across POIs; segmentation, visualization, modelling, prediction) | 15 | 11–13 | 3 POIs; segmentation (corridors, day types, mobility signature); exit forecast R² 0.94–0.99 vs typical week 0.64 (UBC −1.09); MLflow run per POI; findings in `data_findings.md` | Analysis isn't *visible* in Databricks: notebooks 00–08 mostly `print`, only 2 `display()` calls, no charts or dashboard |
| Extra credit: well-structured pipelines / reproducible analysis | +5 | +4–5 | Medallion tables in UC, job DAG, one `fg_core`/`fg_settings` shared by notebooks and `run_local.py` | — |
| Actionable insights in a separate tool (interactive, visually clear, tailored user, grounded in data, plus what other data would add) | 25 | 21–24 | Live Databricks App on gold; action card with levers and lead time; map; Scenario Lab; outlook; "Why trust this?" backtest | No "what other data would improve this" story; Genie drawer unused (`GENIE_SPACE_ID` empty); app auto-stops 24 h after start |
| Originality (5) & impact clearly communicated (5) | 10 | 7–9 | Dwell → directional exit wave vs scheduled capacity, well beyond a dashboard | Impact must be stated in the pitch (e.g. Boxing Day: eastbound strain flagged 2 h ahead, on a holiday schedule) |
| Presentation (problem, solution, impact, next steps) | 5 | 2–4 | — | No pitch script yet (`docs/pitch_script.md` is referenced but doesn't exist) |

Total estimate: 44–52 / 55, +3–5 extra credit.

Next, in order of points per hour:
1. **Pitch script + demo run order** in `docs/pitch_script.md`, built from `data_findings.md` (quote it, never spec
   §4.3): problem ~45 s → insight ~60 s → live demo ~2 min → impact ~45 s → next steps ~30 s. Rehearse against a timer.
2. **Make the analysis visible in Databricks**: an AI/BI dashboard or a short `09_insights` notebook with `display()`
   charts (egress kernel, backtest R² vs typical week per POI, surge-day calendar, corridor mix). Read-only over gold;
   no new formulas outside `fg_core`. Keep the MLflow experiment ready to show.
3. **Genie space** over the gold tables: set `GENIE_SPACE_ID` in `app/app.yaml` and `90_app_grants`, re-run the
   grants notebook, redeploy.
4. **"With more data" slide**: automatic passenger counts, GTFS-RT, event calendars, weather; plus the security
   extension (overnight arrivals are 33% out-of-region vs 15% in daytime).
5. **Sunday 9 AM**: restart `flowguard-harsha`, have the human confirm `/api/health` → `last_source=live`, keep a
   `?date=2025-12-26` link ready; the snapshot is the fallback.
6. Small: both READMEs still say "Park Royal Mobility Intelligence"; FlowGuard now covers three POIs.

Ongoing:
- redeploy the app after each app change (`databricks apps deploy …`, see README)
- stretch: after-hours watch (security theme)
