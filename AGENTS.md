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
  pipeline/   Databricks notebooks 00–07, 90 (".py" files starting with "# Databricks notebook source")
              fg_settings.py  every threshold and constant (plain Python)
              fg_core.py      every formula (plain pandas/numpy)
  config/     CSVs loaded into bronze.ref_* (origin→corridor, route-group overrides, holidays)
  app/        Databricks App: app.py + server/ (FastAPI) + static/ (vanilla JS, Chart.js vendored)
  local/      run_local.py (all gold tables locally), build_snapshot.py (app offline JSON)
              local/data/ is gitignored: raw GTFS, local gold CSVs
  deploy/     app.json (app + warehouse resource), job.json (flowguard-refresh job)
  docs/       spec, verified findings
```

## Invariants: do not break these

1. **One implementation of every formula.** All metric logic lives in `fg_core.py`, and all thresholds live in
   `fg_settings.py` (per-POI settings in `pois.json`). Notebooks use Spark only to aggregate the ~21M visits to
   slots, then call `fg_core` once per POI.
   `local/run_local.py` calls the same functions. Never re-implement a formula inside a notebook.
2. **The browser mirrors `fg_core`.** `app/static/app.js` (`view`, `actionsAt`) rescales the server's gap for
   scenarios and re-implements the readiness/action logic so the Scenario Lab can recompute instantly. `app/server/logic.py` holds copies of the
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
- **After-hours watch (security):** `days.night_ratio` = mean occupancy 00:00–06:00 vs a normal night for that
  weekday (seasonal for UBC); `night_unusual` at ≥ 1.5×. Volume only — never use visitor origin as a security signal.
  The radar spans 00:00–24:00 with the after-hours band (grey, amber when unusual).
- **Operator briefing** (`briefingHtml` in app.js): printable page built client-side from the current day (scenario
  included); alert windows are Strained/Critical runs at +30 min with first-flagged lead time.
- **Genie:** space setup text and verified example SQL in `flowguard/docs/genie_instructions.md`; the app shows the
  Ask button only when `GENIE_SPACE_ID` is set in `app/app.yaml`.
- Occupancy excludes stays over 24 h. "No service" is shown when nothing is scheduled. Route groups are never
  escalated below half the usual daytime demand.

## Workflow

- The human runs `git push`. Databricks pulls through a **Git folder**, where the human clicks Pull. Code is
  never edited in the Databricks UI.
- Notebooks run on **serverless**. "Run all" is fine because this is a solo project. Order: 01 → 02 → 03 → 04 → 05,
  and 06 (after 02) → 07. The job in `deploy/job.json` encodes this graph.
- Databricks CLI profile: `flowguard`. The SQL warehouse ID is in `deploy/app.json`. The Git folder path is in
  `deploy/job.json`.
- Free Edition limits:
  - one small SQL warehouse
  - compute quota: if exceeded, compute stops for the day, and the app's snapshot fallback exists for this reason
  - apps auto-stop 24 h after start/deploy, so restart before judging
  - notebooks have no outbound internet: download external files locally and upload them to the Volume
- Shell gotchas on this Windows machine:
  - In Git Bash, prefix CLI calls that take workspace paths with `MSYS_NO_PATHCONV=1`.
  - In PowerShell, quote `--json "@file.json"`, because a bare `@` is PowerShell splatting.
  - Keep script output ASCII-safe or set `PYTHONIOENCODING=utf-8`. The console is cp1252.

## Verification

```
# formulas and all gold tables locally (~20 s); prints backtest, surge days, route groups, readiness on demo days
python flowguard/local/run_local.py
# refresh the app's offline snapshot from those tables
python flowguard/local/build_snapshot.py
# run the app offline (venv lives in flowguard/app/.venv, gitignored)
cd flowguard/app && DATA_MODE=snapshot .venv/Scripts/python -m uvicorn app:app --port 8765
```

- **Client/server consistency:** open `/?date=2025-12-26&selftest=1` and read `body[data-selftest]`, which should
  show `readiness_mismatch=0/576` (a single 23:30 cross-midnight mismatch on Saturdays is a known edge).
  Headless: `chrome --headless=new --virtual-time-budget=8000 --dump-dom "<url>"`.
- **Visual check:** after UI changes, take screenshots with
  `chrome --headless=new --window-size=1440,1250 --screenshot=out.png "<url>&theme=dark"`, and repeat with
  `theme=light` and a 1280 width. The URL params are `poi`, `date`, `t=HH:MM`, `theme`, and `lab=1` (opens
  the Scenario Lab). Run the self-test for every POI (`?poi=ubc&date=…&selftest=1`).
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
- The frontend has no build step. Colours are CSS tokens in `styles.css`, with light and dark each defined
  separately. Status colours (good / warning / serious / critical) are only for readiness and pressure levels,
  always paired with an icon and a label.
- Commit messages end with a `Co-Authored-By` line when an agent writes the commit. Never push, deploy, or run
  Databricks jobs without the human asking.

## Status and next steps

Done: pipeline 01–07, egress model with MLflow, GTFS service, readiness timeline, app (live + snapshot),
deploy and job configs.
Pipeline and app are multi-POI (Park Royal, UBC, Waterfront): the app has a location switcher, every endpoint takes
`?poi=`, and the snapshot lives in `app/static/data/<poi>/`.
Next:
- redeploy the app after each app change (`databricks apps deploy …`, see README)
- Genie space ("Ask FlowGuard" drawer is wired; set `GENIE_SPACE_ID` in `app/app.yaml` and in `90_app_grants`)
- pitch script (`docs/pitch_script.md`) built from `data_findings.md`
- stretch: after-hours watch (security theme)
