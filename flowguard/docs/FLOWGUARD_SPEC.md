# FlowGuard — Build Spec & Implementation Brief

**Hackathon:** Data Intelligence for Smarter Communities (Rogers × Databricks × UBC), Sept 25–27 2026
**Theme:** Transit (primary) + Security (secondary)
**Location of focus:** Park Royal Shopping Centre, West Vancouver (one point of interest)
**Tagline:** *See the pressure before it hits.*
**One-line pitch:** *You can't predict when a crowd will show up — but the moment it does, FlowGuard knows when it will leave, which direction it's heading, and whether transit is ready for it.*

> **How to use this doc (for Claude Code):** This is the source of truth for what we are building. The local folder is a copy of the Databricks workshop repo `databricks-solutions/data-intelligence-for-smarter-community`. Read Section 7 (repo plan) and Section 8 (step-by-step) first, produce an implementation plan, then make the code changes. Steps tagged **🖐 MANUAL (Databricks UI)** must be done by a human in the browser — do not attempt them; instead tell the human exactly when they are needed. Steps tagged **💻 TERMINAL** are CLI commands the human runs (you may run them if you have the CLI and credentials). Steps tagged **🤖 CODE** are yours.

---

## 1. Context & constraints

- **Time:** ~21 on-site working hours total. Fri until 9 PM, Sat 9 AM–9 PM, Sun 9–11 AM. Judging Sun 11:30 AM. **Feature freeze Sat 6 PM.**
- **Team:** ~6 people, mixed skills.
- **Platform:** Databricks **Free Edition** (serverless only). Key limits:
  - One SQL warehouse (2X-Small). Keep app queries tiny and pre-aggregated.
  - Up to 3 Databricks Apps per account. **Apps auto-stop 24 h after start/deploy → restart the app Sunday morning before judging.**
  - One active Lakeflow pipeline per pipeline type.
  - Exceeding the compute quota shuts compute down **for the rest of the day** → precompute everything, cache in the app, keep an offline snapshot fallback.
  - Outbound internet from notebooks is restricted → download external files (GTFS) on a laptop and upload to a Volume.
  - Avoid "Run All" in shared notebooks (organizer guidance).
- **Judging (55 + 5 bonus):**
  | Criterion | Pts | How FlowGuard earns it |
  |---|---|---|
  | Data analysis in Databricks | 15 (+5 for well-structured pipeline / reproducibility) | Medallion pipeline, 30-min segmentation, surge detection, egress model with MLflow-logged backtest, GTFS join |
  | Actionable insights in a separate interactive tool | **25** | Databricks App: live replay, Transit Pressure Radar, Scenario Lab, action cards, tailored to TransLink ops |
  | Originality & impact | 10 | Uses dwell_time to forecast *exit waves*, not another crowd dashboard; gives operators lead time |
  | Presentation | 5 | 5-min pitch built around the real Boxing Day replay |
- **Presentation format:** 5-min presentation + 3-min Q&A. Judges will **not** inspect code — the demo, story and clarity matter most.

---

## 2. The problem

Transit service around major destinations is scheduled in advance, but demand spikes sharply and unpredictably. Today, operators learn about a crowd problem when it is already visible — queues at the bus exchange, packed buses, gridlock.

Knowing *"Park Royal is busy right now"* is not actionable. What an operator actually needs to know is:

1. **When will today's crowd re-enter the transportation network?** (the exit wave)
2. **In which direction** is that demand likely to go?
3. **Is scheduled service at that time and in that direction proportionate** to the expected demand?
4. **What should I do, and by when?**

### Why Park Royal
- It sits at the **north foot of the Lions Gate Bridge** and hosts the **Park Royal bus exchange** (TransLink / West Vancouver Blue Bus).
- ~48% of its visitors come from **off the North Shore** — they have to cross Burrard Inlet to get home.
- On weekdays after 9:30 AM the bridge's reversible centre lane generally runs northbound, leaving **one southbound lane**.
- TransLink launched the **R2 RapidBus extension (one-seat Park Royal ↔ Metrotown)** on **Sept 7, 2026** — 18 days before the hackathon. The east-bound catchment in our data is exactly its rider market.
- Its surges are tied to dates a judge instantly recognizes (Boxing Day, Christmas-season weekends, long weekends).

## 3. Who it's for

**Primary user: TransLink operations & service-planning staff** responsible for service around high-demand destinations and bus exchanges.

Secondary (mention only in "next steps"): Park Royal centre operations, West Vancouver transportation planning, the Province's Lions Gate Bridge operators, West Vancouver Police (after-hours).

The whole UI speaks to the primary user: "Stage supplemental eastbound service before 11:45", not "the city should consider…".

---

## 4. The data — validated facts and rules

### 4.1 Raw schema (per file, one row per device attachment)

| Column | Meaning |
|---|---|
| `location_name` | POI name (`Park Royal Mall`, `UBC`, `Waterfront Station`) |
| `longitude`, `latitude` | Single fixed point per POI (Park Royal: 49.3265, −123.138) |
| `timestamp` | When the device attached to the cell tower (ISO string ending in `Z`) |
| `origin` | Home location of the device (36 categories: Vancouver neighbourhoods, Metro municipalities, `British Columbia Other`, provinces, `International`) |
| `dwell_time` | Minutes the device stayed attached |

Three files exist (Park Royal, UBC, Waterfront). **We use Park Royal only for the MVP.** The team will obtain the **original uncut files** (the copies we analysed were truncated at Excel's 1,048,575-row limit; rows are shuffled so the sample was representative).

### 4.2 Data rules (MUST follow)

1. **Timestamps are Vancouver local clock time**, despite the `Z` suffix. Evidence: read as UTC, 29–31% of all arrivals at every POI would fall between 1–6 AM and the mall would be busiest at 6–9 AM; the raw clock pattern also does *not* shift at the March 8 DST change. Parse as a naive local timestamp (`TIMESTAMP_NTZ`) after stripping `Z`. Keep a single config flag `TS_IS_UTC = False` so this can be flipped in one place.
2. **Relative numbers only in the UI.** The data is a synthetic subscriber sample; never display absolute headcounts as "people". Use "× normal", shares, indices.
3. **`dwell_time` is minutes.** `departure_ts = arrival_ts + dwell_time minutes`. ~0.7% of dwells exceed 24 h — keep them for egress but flag `long_stay`.
4. **Drop exact duplicate rows** (~0.01%).
5. **30-minute slots** are the base grain (organizer recommendation; data was synthesized at that granularity).
6. **Arrivals are bursty; departures and occupancy are smooth.** Within a day, arrivals come in large blocks per origin per slot followed by empty slots (e.g., Boxing Day: 1,086 → 1,004 → 1,315 → 1,520 → 0 → 0 → 1,699 …). Therefore:
   - "Current pressure" is computed on **occupancy** (people present), not raw arrivals.
   - Inbound pressure is shown as the **typical pattern for that kind of day** (smoothed), not a 30-min arrival forecast.
   - The forecastable quantity is **departures** (the egress model).

### 4.3 Key findings (from the truncated sample — re-verify on the uncut files and update the numbers in the pitch)

- **Visitor mix:** 52% North Shore locals; 14% "South" corridor (Vancouver neighbourhoods, Richmond, Delta); 12% "East" corridor (Burnaby, New West, Surrey, Tri-Cities, Fraser Valley, East Van); 6% BC other; 16% out-of-province/international.
- **Daily rhythm:** arrivals rise from ~8 AM, peak ~4 PM, fall after 6 PM. Weekday occupancy peaks ~6 PM; weekends ~4:30 PM. Fri/Sat busiest days.
- **Surge days** (≥1.6× the median for that weekday): 17 days. Boxing Day 2025-12-26 = **3.25×**; Christmas-season Fri/Sat ≈ 1.8–2×; May long weekend ≈ 1.9×. On surge days far-suburb origins (Maple Ridge, Langley, Pitt Meadows) jump 3–5×, while out-of-province share *falls* → surges are a **regional transit** problem.
- **Boxing Day timeline (sample counts, 30-min slots):**

  | Slot | Arrivals | Departures |
  |---|---|---|
  | 10:30 | 1,086 | 115 |
  | 11:00 | 1,004 | 423 |
  | 11:30 | 1,315 | 560 |
  | 12:00 | 1,520 | **788** |
  | 12:30 | 0 | **802 (peak)** |
  | 13:00 | 0 | 534 |
  | 13:30 | 1,699 | 560 |
  | 14:00 | 0 | 674 |
  | 15:00 | 1,658 | 501 |
  | 15:30 | 0 | 610 |
  | 17:00 | 0 | 245 |

  Story: *the exit crush starts before lunch and stays heavy for four hours.* Peak occupancy ≈ 5× a normal weekday peak; the off-North-Shore exit peak ≈ 3.7× a normal Friday's peak and arrives at 12:30 PM, not ~4 PM as usual.
- **Busy = regional:** in the busiest 10% of slots ~89% of the crowd is Metro Vancouver (incl. North Shore) vs ~2% in the quietest 10%. At Park Royal, busy periods also have **longer** stays (median 61 → 78 min).
- **Forecastability (holdout Jul–Aug 2026, trained on Nov–Jun):**

  | Forecast | R² |
  |---|---|
  | Arrivals 30–60 min ahead (gradient-boosted trees with lags/calendar) | **0.06** (no better than weekday×slot average) |
  | **Departures +30 min** (egress model) | **0.91** |
  | Departures +60 / +90 / +120 min | **0.66 / 0.50 / 0.41** |
  | Departures from weekday×slot typical pattern | 0.28 |

  → The model is the **egress model**, not an arrival forecaster.
- **Egress kernel** (share of visitors who have left by k half-hour slots after their arrival slot): k0 12%, k1 36%, k2 52%, k3 62%, k4 70%, k6 79%, k8 85%.
- **After hours (security, stretch):** ~125–155 devices arrive per night between midnight and 6 AM while stores are closed; out-of-province share doubles overnight (33% vs 15%); a few nights are ≈3σ above normal.

### 4.4 Corridor mapping (assumption — state it as such in the pitch)

Origin → corridor (proxy for direction of outbound demand, *not* a claim about where any individual goes):

| Corridor | Origins |
|---|---|
| `NS_WEST` North Shore – West Van local | West Vancouver |
| `NS_EAST` North Shore – North Van | North Vancouver |
| `EAST` East via R2 / Ironworkers | Burnaby, New Westminster, Surrey, Port Moody, Langley, Maple Ridge, Pitt Meadows, Hastings-Sunrise, Renfrew-Collingwood, Grandview-Woodland |
| `SOUTH` South via Lions Gate | Downtown, West End, Kitsilano, Fairview, Mount Pleasant, Strathcona, UBC, Arbutus Ridge, Dunbar-Southlands, Marpole, Oakridge, Sunset, Kensington-Cedar Cottage, Victoria-Fraserview, Killarney, Richmond, Delta |
| `BC_OTHER` | British Columbia Other |
| `OUT_OF_REGION` | Ontario, Alberta, Manitoba, Saskatchewan and Territories, Atlantic Canada, International |

Any origin not listed → `OTHER` (log it). Store this as a small reference table (`bronze.ref_origin_corridor`), not hard-coded SQL.

Corridor → GTFS route group used for readiness:

| Route group (from GTFS, Park Royal stops) | Demand corridors that load it |
|---|---|
| `EASTBOUND` (R2 and routes toward Lonsdale / Phibbs / Metrotown) | `EAST`, `NS_EAST` |
| `DOWNTOWN` (routes to downtown Vancouver via Lions Gate) | `SOUTH` |
| `WEST_VAN_LOCAL` (Dundarave / Horseshoe Bay / British Properties etc.) | `NS_WEST` |
| (none — assume mostly car / ferry) | `BC_OTHER`, `OUT_OF_REGION` |

Route group classification is done from GTFS `routes.txt` + `trips.trip_headsign` of trips serving the Park Royal stops (keyword rules + a manual override CSV).

---

## 5. The solution — what FlowGuard does

### 5.1 Pipeline of reasoning (this is also the pitch diagram)

```
Where are people? (occupancy vs normal)            → CURRENT PRESSURE
   ↓
What kind of crowd is it? (mix + stay length)      → MOBILITY SIGNATURE
   ↓
When will it leave? (arrivals × learned dwell)     → EGRESS FORECAST (+30…+120 min)
   ↓
Which direction? (origin → corridor)               → REGIONAL CATCHMENT
   ↓
Is scheduled service proportionate? (GTFS)         → TRANSIT PRESSURE GAP / READINESS
   ↓
What should operators do, by when?                 → ACTION CARD
```

### 5.2 Metric definitions

All per 30-min slot `t` at Park Royal. "Baseline" = **median** over history for the same `(day_type, slot_of_day)` where `day_type ∈ {Mon..Sun}` (medians so surge days don't distort normal).

1. **Occupancy** `occ(t)` = cumulative arrivals up to end of `t` − cumulative departures up to end of `t` (window sums; avoids exploding rows).
2. **Current pressure** `pressure(t) = occ(t) / baseline_occ(day_type, slot)`. Display as `2.8× normal`. Levels: <1.3 Normal, 1.3–1.8 Elevated, 1.8–2.5 High, ≥2.5 Severe.
3. **Daily surge ratio** `surge_ratio(d) = arrivals(d) / median arrivals for that weekday`. Used for the day picker and the "surge day" flag (≥1.6).
4. **Egress model (the ML component).**
   - Kernel `P(k)`, k = 0..48 half-hour slots: the empirical share of visitors whose departure slot is k slots after their arrival slot, trained on data before 2026-07-01. Optional refinement (only if time): condition `P` on `(hour_of_day, busy_flag)` — gave a small gain.
   - Forecast of departures at `t+h` made at time `t` (h = 1..4 → +30…+120 min):
     `dep_hat(t+h) = Σ_{i ≤ t} arrivals(i) · P(t+h−i)  +  Σ_{t < i ≤ t+h} typical_arrivals(day_type, slot(i)) · P(t+h−i)`
     (first term = crowd already inside, second = typical arrivals still to come.)
   - Compute per corridor as well (use corridor-specific arrivals; same kernel, or corridor kernel if time).
   - **Backtest** on Jul–Aug holdout for h=1..4 and compare with the weekday×slot baseline; log kernel (as a CSV artifact), params and R²/MAE metrics to **MLflow**. Store results in `gold.model_backtest`. The app shows these numbers in a small "Why trust this?" tooltip.
5. **Egress pressure** `egress_idx(t+h, corridor) = dep_hat / baseline_departures(day_type, slot, corridor)`.
6. **Scheduled service** `svc(slot, route_group, service_day_type)` = number of scheduled trip departures from Park Royal stops in the slot, from TransLink static GTFS. `service_day_type ∈ {WEEKDAY, SATURDAY, SUNDAY_HOLIDAY}` from `calendar.txt`/`calendar_dates.txt` of the current feed (the feed is Sept 2026; our mobility data is Nov 2025–Aug 2026, so we apply the *current* service pattern by day type — say this in Q&A). Holiday → day-type mapping lives in a config table (default: BC stat holidays → SUNDAY_HOLIDAY; **Dec 26 → configurable, verify against TransLink's holiday service notice**).
7. **Transit Pressure Gap / Readiness** per slot and route group:
   - `load(t, g) = expected outbound demand for g / max(svc(t, g), 0.5)` where demand for g = sum of `dep_hat` over the corridors mapped to g.
   - `gap(t, g) = load(t, g) / typical_load(g)` where `typical_load(g)` = median `load` over normal-day daytime slots (10:00–20:00).
   - Readiness: `gap < 1.25` 🟢 Prepared · `1.25–2.0` 🟡 Watch · `2.0–3.0` 🟠 Strained · `≥ 3.0` 🔴 Critical.
   - "Transit Pressure Gap" is the headline metric: the slots where expected demand per scheduled trip is most out of line with normal.
8. **Mobility signature** (label for the current slot, using the trailing 2 h of arrivals; thresholds in config):
   - `pressure < 1.3` → **Routine activity**
   - else a surge label built from qualifiers:
     - **Regional** if the share from `SOUTH+EAST+BC_OTHER` ≥ baseline share + 5 pp
     - **Extended-stay** if median dwell of trailing arrivals ≥ 1.15× baseline; **Quick-turnover** if ≤ 0.85×
     - **Visitor-heavy** if `OUT_OF_REGION` share ≥ 1.5× baseline
   - Display e.g. **"Regional + Extended-stay surge"**. Always phrase as a *mobility pattern*, never as who people are.
9. **Action cards** (rule-based templates; LLM phrasing is a stretch):
   - Trigger: first future slot within +120 min where any route group is 🟠/🔴.
   - Content: route group, window, lead time, suggested levers, the driver. Example:
     > **🔴 Eastbound strained 12:00–13:30 (in 45 min).** Expected eastbound demand 3.4× normal against a normal Saturday schedule. Stage 2–3 supplemental eastbound (R2-direction) trips at Park Royal exchange from 11:45; position passenger-management staff at the eastbound bays; push rider messaging "consider later departure / alternative routes".
   - Secondary cards (lower priority): DOWNTOWN corridor → "flag Lions Gate southbound pressure to bridge operations"; mall-side → "open overflow wayfinding / parking staff".

### 5.3 Scenario Lab (must-have — the judges' hands-on moment)

Controls (client-side recompute, instant):
- **Crowd surge** slider −30% … +100% → scales the arrival term (egress is linear in arrivals).
- **Scheduled service** slider −50% … +50% → scales `svc`.
- **Event ends at [time] with [size]** → injects extra departures: 60% in the chosen slot, 30% next, 10% the one after, allocated across corridors by the day's corridor mix.
- Presets: *Boxing Day*, *Event ends 10 PM*, *Service −30%*.

Everything downstream (egress bars, readiness colours, action card text) updates immediately.

### 5.4 After-Hours Watch (stretch, Security theme)
Nightly (00:00–06:00) arrivals vs baseline, out-of-region share vs baseline, flagged nights (z ≥ 2.5). Small panel/tab. Recommendation: "Flag for West Vancouver Police patrol / mall security".

### 5.5 Genie (stretch — first thing after the core works)
A Genie space over the gold tables so judges can ask "Which corridor leaves latest on Saturdays?". The repo already contains a working Genie client (`server/genie.py`).

---

## 6. Architecture

```
TransLink GTFS zip ─┐
Park Royal CSV ─────┼─► UC Volume flowguard.bronze.raw
                    │
             ┌──────▼──────────────── Databricks (Free Edition) ────────────────────┐
             │ BRONZE  pr_raw, gtfs_* (stops, routes, trips, stop_times, calendar*), │
             │         ref_origin_corridor, ref_route_group_override, ref_day_type  │
             │ SILVER  pr_visits (clean, local ts, departure_ts, corridor)          │
             │         gtfs_parkroyal_departures (trip departures at PR stops)      │
             │ GOLD    pr_slots, pr_slot_corridor, pr_baselines, pr_days,           │
             │         egress_kernel, egress_forecast, model_backtest,              │
             │         gtfs_service_30min, flowguard_timeline, after_hours_nightly  │
             │ MLflow  experiment /Shared/flowguard-egress                          │
             │ Genie   space over gold (stretch)                                    │
             └──────┬───────────────────────────────────────────────────────────────┘
                    │ SQL warehouse (2X-Small)
             ┌──────▼──────── Databricks App "flowguard" ─────────┐
             │ FastAPI (reuse workshop server/config.py, sql.py,  │
             │ genie.py) + new static single-page frontend        │
             │ (HTML + vanilla JS + Chart.js from CDN)            │
             │ In-memory cache; offline JSON snapshot fallback    │
             └────────────────────────────────────────────────────┘
```

### 6.1 Unity Catalog layout
- Catalog: `flowguard` (fallback: if catalog creation is not permitted, use the workspace default catalog and prefix schemas `flowguard_bronze` etc. — make catalog/schema names config values).
- Schemas: `bronze`, `silver`, `gold`.
- Volume: `flowguard.bronze.raw` (uploaded files).

### 6.2 Gold tables (contract between pipeline and app)

`gold.pr_slots` — one row per 30-min slot (whole history, no gaps):
`slot_ts TIMESTAMP_NTZ, date DATE, day_type STRING, slot_of_day INT (0–47), service_day_type STRING, arrivals INT, departures INT, occupancy INT, baseline_occ DOUBLE, pressure DOUBLE, pressure_level STRING, baseline_arrivals DOUBLE, baseline_departures DOUBLE, median_dwell DOUBLE, share_ns_west DOUBLE, share_ns_east DOUBLE, share_south DOUBLE, share_east DOUBLE, share_bc_other DOUBLE, share_out_of_region DOUBLE, signature STRING`

`gold.pr_slot_corridor` — slot × corridor: `slot_ts, corridor, arrivals, departures, baseline_departures`

`gold.pr_days` — one row per date: `date, day_type, arrivals, surge_ratio, is_surge, peak_pressure, peak_pressure_slot, label` (label e.g. "Boxing Day", used by the day picker).

`gold.egress_kernel` — `k INT, p DOUBLE, cum_p DOUBLE` (+ optional `hour_of_day, busy_flag`).

`gold.egress_forecast` — forecast made at each slot: `origin_slot_ts, horizon INT (1–4), target_slot_ts, corridor ('ALL' + each), dep_hat DOUBLE, dep_actual INT, egress_idx DOUBLE`.

`gold.model_backtest` — `horizon, model ('egress'|'typical_week'|'gbt_arrivals'), r2, mae, n`.

`gold.gtfs_service_30min` — `service_day_type, slot_of_day, route_group, scheduled_departures INT`.

`gold.flowguard_timeline` — **the one table the app reads for the radar/replay**: one row per slot per route group with everything denormalised: `slot_ts, date, slot_of_day, route_group, pressure, pressure_level, signature, dep_hat_h1..h4, expected_demand_h1..h4, svc_h1..h4, gap_h1..h4, readiness_h1..h4, action_text`.

`gold.after_hours_nightly` (stretch) — `night_date, arrivals, baseline, z, out_of_region_share, flagged`.

Add table and column **comments** to every gold table (helps Genie and judges browsing Catalog Explorer).

---

## 7. Repo plan (what to change in the local workshop folder)

The local folder is the workshop repo. **Do not modify `workshop/`** (keep it as reference). Create a new top-level `flowguard/` folder.

### 7.1 What to reuse from the workshop
From `workshop/Includes/reference_code/app/`:
- **Keep/copy:** `app.py` (as a starting point; strip the campus endpoints), `app.yaml`, `requirements.txt`, `server/config.py` (dual local/app auth — works with a CLI profile locally), `server/sql.py` (SQL warehouse connector), `server/genie.py` (complete Genie Conversations API client).
- **Drop:** `server/datasets.py`, `server/geometry.py`, `server/geojson/`, `frontend/dist/` (it is a compiled React bundle — **the React source is NOT in the repo**, so it cannot be edited; we write a new frontend).

From `workshop/06 - Hands On - Deploy the App.py`: the cells that **grant the app's service principal** `USE CATALOG`/`USE SCHEMA`/`SELECT`, `CAN_USE` on the warehouse, and `CAN_RUN` on the Genie space, and the `deploy_and_wait` cell. Recreate these in `flowguard/pipeline/90_deploy_app.py`.

From `workshop/05 - Hands On - Create a Genie Space.py`: the pattern for Genie instructions + sample questions (see Appendix C).

From `workshop/02`/`04`: the medallion conventions (dedupe, validate, gold = pre-aggregated, readable names).

### 7.2 Target layout

```
flowguard/
  README.md                      # how to run everything (generated from this spec)
  docs/
    FLOWGUARD_SPEC.md            # this file
    pitch_script.md
    genie_instructions.md
  config/
    origin_corridor.csv          # Section 4.4 table
    route_group_overrides.csv    # manual route_id → route_group fixes
    day_type_overrides.csv       # holiday → service_day_type
  pipeline/                      # Databricks notebooks in "# Databricks notebook source" .py format
    00_config.py                 # catalog/schema names, TS_IS_UTC=False, POI coords, thresholds, train/test split date
    01_setup_uc.py               # CREATE CATALOG/SCHEMA/VOLUME IF NOT EXISTS; load config CSVs to bronze.ref_*
    02_bronze.py                 # CSV (Volume) → bronze.pr_raw; GTFS txt → bronze.gtfs_*
    03_silver.py                 # clean, local timestamp, departure_ts, corridor join, dedupe; expectations/assertions
    04_gold_slots.py             # pr_slots, pr_slot_corridor, baselines, pr_days, signature
    05_egress_model.py           # kernel, forecasts, backtest (+ GBT arrival baseline for comparison), MLflow logging
    06_gtfs_service.py           # Park Royal stops within radius, route groups, gtfs_service_30min
    07_timeline.py               # gap/readiness/action → gold.flowguard_timeline
    08_after_hours.py            # stretch
    09_export_snapshot.py        # writes app/static/data/*.json snapshot (offline fallback)
    90_deploy_app.py             # grants + deploy (adapted from workshop Lab 06)
  local/
    build_snapshot.py            # pandas re-implementation that builds the same JSON from local CSVs (for app dev before Databricks is ready + fallback)
  app/
    app.py
    app.yaml
    requirements.txt
    server/
      config.py  sql.py  genie.py      # copied from workshop
      queries.py                        # all SQL against gold tables (parameterised)
      logic.py                          # scenario recompute helpers (mirror of client logic, for tests)
      cache.py                          # in-memory cache + snapshot fallback (DATA_MODE=live|snapshot)
    static/
      index.html  app.js  styles.css
      data/                             # JSON snapshot files
  databricks.yml                 # OPTIONAL Databricks Asset Bundle (job chaining pipeline notebooks + app). Only if it works quickly on Free Edition.
```

Also: add a root `.gitignore` entry for `.venv/`, `*.env`, `local/data/` (raw CSVs are large — do not commit them).

### 7.3 Backend API (FastAPI)

All responses cached in memory (data is static). `DATA_MODE=live` reads gold via SQL warehouse; `DATA_MODE=snapshot` reads `static/data/*.json`. If a live query fails, fall back to snapshot automatically and log it.

| Endpoint | Returns |
|---|---|
| `GET /api/health` | status + data mode |
| `GET /api/days` | selectable days: date, label, surge_ratio, is_surge (+ curated presets first: Boxing Day 2025-12-26, Pre-Christmas Sat 2025-12-20, Long weekend Sun 2026-05-24, Normal Sat 2026-04-25, Normal Fri 2026-03-20) |
| `GET /api/timeline?date=YYYY-MM-DD` | 48 slots × route groups from `gold.flowguard_timeline` + slot-level pressure/signature/shares |
| `GET /api/catchment?date=&slot=` | corridor shares (trailing 2 h) vs baseline |
| `GET /api/service?service_day_type=` | `gtfs_service_30min` |
| `GET /api/model` | kernel + backtest metrics |
| `GET /api/after-hours` | stretch |
| `POST /api/genie/ask` | reuse workshop implementation |
| `GET /` | serves `static/index.html` |

Scenario Lab math runs **client-side in `app.js`** (instant, no warehouse load) using the kernel + timeline payload.

### 7.4 Frontend (single page, no build step)

- Plain HTML/CSS/JS + **Chart.js** from `cdn.jsdelivr.net` (or cdnjs). No React/npm build.
- Look: dark "operations console", large type, readable from the back of a room / projector. Colour only for status (green/yellow/orange/red) plus one accent. Must also be legible on a light projector — test both.
- Layout (desktop 1440 px, must not break at 1280):
  1. **Header:** FlowGuard · Park Royal Mobility Intelligence · day picker (presets first) · replay controls (▶ / ❚❚, speed 1×/4×, slot scrubber) · clock showing replay time.
  2. **Current Pressure** tile: big `3.1× normal`, level chip, sparkline of pressure for the day so far.
  3. **Mobility Signature** tile: label + three mini indicators (regional share vs normal, stay length vs normal, visitor share vs normal).
  4. **Transit Pressure Radar** (hero, widest): x-axis NOW, +30, +60, +90, +120; bars = forecast outbound demand index; faint band = typical inbound pattern; line = actual departures revealed as the replay advances (shows the forecast was right); readiness strip coloured per slot beneath.
  5. **Regional Catchment**: horizontal bars by corridor, share now vs normal.
  6. **Transit Readiness** table: route group · expected demand (× normal) · scheduled trips · status chip, for the next 2 h.
  7. **Action card**: highest-priority recommendation with lead time ("in 45 min"), levers, and a one-line "why".
  8. **Scenario Lab** drawer (right side): sliders + presets; changes animate.
  9. **Ask FlowGuard** drawer (Genie) — stretch.
  10. Footer tooltip "Why trust this?": egress backtest R² (+30 min 0.91 … vs 0.28 typical-week) and data notes (local time, relative numbers, synthetic sample).
- Replay: stepping the slot updates all panels using only data "known" at that slot (forecast rows keyed by `origin_slot_ts`) — this is what makes the demo feel live.

---

## 8. Step-by-step build plan

Legend: **🖐 MANUAL (Databricks UI)** · **💻 TERMINAL** (human runs) · **🤖 CODE** (Claude Code) · ⏱ target time.

### Phase 0 — Setup (Fri, first hour)

1. **🖐 MANUAL (Databricks UI)** — One teammate creates/uses a **Databricks Free Edition** workspace and invites all teammates: *Settings → Identity and access → Users → Add user*, grant **admin**. Everyone logs in.
2. **🖐 MANUAL (Databricks UI)** — *SQL Warehouses*: confirm the serverless warehouse exists, start it, open *Connection details* and copy the **warehouse ID** (last segment of the HTTP path). Share it with the team.
3. **💻 TERMINAL** — Put the local folder under git and share it:
   ```bash
   git init && git add . && git commit -m "Workshop baseline"
   # create a private GitHub repo, then:
   git remote add origin <url> && git push -u origin main
   ```
4. **💻 TERMINAL** — Install and authenticate the Databricks CLI (v0.2xx+):
   ```bash
   # macOS: brew tap databricks/tap && brew install databricks   |  Windows: winget install Databricks.DatabricksCLI
   databricks auth login --host https://<your-workspace-host> --profile flowguard
   databricks current-user me --profile flowguard   # sanity check
   ```
5. **🤖 CODE** — Scaffold `flowguard/` per Section 7.2, copy the reusable app files, add `config/*.csv`, `.gitignore`, and a `flowguard/README.md` with run instructions.

### Phase 1 — Data in (Fri)

6. **🤖 CODE** — `pipeline/00_config.py`, `pipeline/01_setup_uc.py` (creates catalog `flowguard`, schemas, Volume `flowguard.bronze.raw`; falls back gracefully if catalog creation is denied).
7. **💻 TERMINAL** — Get code into the workspace (choose one):
   - **A (recommended):** **🖐 MANUAL (Databricks UI)** *Workspace → Create → Git folder* → paste the GitHub URL. Pull after each push.
   - **B:** `databricks sync ./flowguard /Workspace/Users/<you>/flowguard --profile flowguard` (add `--watch` while developing).
8. **🖐 MANUAL (Databricks UI)** — Open `pipeline/01_setup_uc.py`, attach **Serverless**, run it (run cells individually, not "Run All" if others are in the notebook).
9. **🖐 MANUAL (Databricks UI)** — Upload the **original uncut** Park Royal CSV to the Volume: *Catalog → flowguard → bronze → Volumes → raw → Upload to this volume*. (Optionally also the UBC/Waterfront files for later.)
10. **💻 TERMINAL (laptop)** — Download TransLink static GTFS: `curl -L -o google_transit.zip https://gtfs-static.translink.ca/gtfs/google_transit.zip` (or via browser), unzip, then **🖐 MANUAL** upload `stops.txt, routes.txt, trips.txt, stop_times.txt, calendar.txt, calendar_dates.txt` to the same Volume under `raw/gtfs/`.
11. **🤖 CODE** — `02_bronze.py`, `03_silver.py`. Silver requirements:
    - parse `timestamp` with `Z` stripped → `arrival_ts TIMESTAMP_NTZ` (local); if `TS_IS_UTC` were True, convert `from_utc_timestamp(..., 'America/Vancouver')` instead.
    - `departure_ts = arrival_ts + INTERVAL dwell_time MINUTES`; `arrival_slot`, `departure_slot` = floor to 30 min; `long_stay` flag.
    - join `ref_origin_corridor`; unmapped → `OTHER`.
    - drop exact duplicates; assert non-null ts/origin/dwell ≥ 1; assert single POI coordinate; print row counts before/after (these checks are the "data quality expectations").
12. **🖐 MANUAL (Databricks UI)** — Run `02` and `03`. ⏱ **Checkpoint Fri ~7 PM:** `silver.pr_visits` exists; row count matches the uncut file minus duplicates; hourly profile peaks mid-afternoon.

### Phase 2 — Analytics & model (Fri evening → Sat noon)

13. **🤖 CODE** — `04_gold_slots.py`: full slot grid (no gaps), arrivals/departures/occupancy via window sums, baselines (median by day_type × slot_of_day), pressure, pr_days + surge flags + labels, corridor shares, signature rules.
14. **🤖 CODE** — `05_egress_model.py`: kernel (train < 2026-07-01), forecasts for h=1..4 per corridor, backtest vs typical-week and vs a GBT arrival forecaster (sklearn `HistGradientBoostingRegressor` with lags/calendar features — included to *prove* arrivals aren't forecastable), log to MLflow experiment `/Shared/flowguard-egress`, write `gold.egress_kernel`, `gold.egress_forecast`, `gold.model_backtest`. Target: reproduce ≈ R² 0.91/0.66/0.50/0.41 on the holdout.
15. **🤖 CODE** — `06_gtfs_service.py`: find stops within ~400 m of (49.3265, −123.138) using haversine; list them (print stop names so a human can sanity-check they're Park Royal exchange/area stops); join stop_times→trips→routes; classify route groups by headsign/route rules + override CSV; count departures per `service_day_type × slot_of_day × route_group` (handle GTFS times ≥ 24:00:00). Print the route list per group for human review.
16. **🖐 MANUAL (human review)** — Check the printed Park Royal stops and route→group assignments; fix via `config/route_group_overrides.csv`; re-run 06.
17. **🤖 CODE** — `07_timeline.py`: demand per route group, gap, readiness, action text → `gold.flowguard_timeline`. Add table/column comments.
18. **🖐 MANUAL (Databricks UI)** — Run 04 → 07 in order. ⏱ **Checkpoint Sat ~12 PM:** Boxing Day rows in `flowguard_timeline` show eastbound/downtown 🟠/🔴 around 12:00–15:00; a normal Saturday stays mostly 🟢/🟡. If not, tune thresholds in `00_config.py` (document any change).
19. **🖐 MANUAL (Databricks UI)** — *Jobs & Pipelines → Create job* "flowguard-refresh" chaining notebooks 02→07 (+09) as tasks on serverless; run it once end-to-end. (This is the reproducibility bonus. If time allows, convert 02–04 to a Lakeflow Declarative Pipeline with expectations; otherwise the Job is enough.) Alternatively **🤖 CODE** a `databricks.yml` bundle defining the job and **💻 TERMINAL** `databricks bundle deploy --profile flowguard` — only if it works first try.

### Phase 3 — App (in parallel from Fri evening; frontend can start immediately on the snapshot)

20. **🤖 CODE** — `local/build_snapshot.py`: pandas implementation producing the same JSON as `09_export_snapshot.py` from a local CSV, so frontend work can start **before** Databricks tables exist. Keep formulas identical to Section 5.2.
21. **🤖 CODE** — Backend (`app.py`, `server/queries.py`, `server/cache.py`), endpoints per 7.3, snapshot fallback.
22. **🤖 CODE** — Frontend (`static/index.html`, `app.js`, `styles.css`) per 7.4, including replay and Scenario Lab.
23. **💻 TERMINAL** — Run locally:
    ```bash
    cd flowguard/app && python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
    export DATABRICKS_PROFILE=flowguard DATABRICKS_WAREHOUSE_ID=<id> CATALOG=flowguard SCHEMA=gold DATA_MODE=live   # or DATA_MODE=snapshot
    uvicorn app:app --reload --port 8000
    ```
24. **🖐 MANUAL (Databricks UI)** — *Compute → Apps → Create app → Custom* named `flowguard` (or **💻** `databricks apps create flowguard --profile flowguard`). Under the app's **Resources**, add the SQL warehouse with **Can use**. Note the app's **service principal** name.
25. **🤖 CODE** — `pipeline/90_deploy_app.py` (adapted from workshop Lab 06): grants `USE CATALOG`, `USE SCHEMA`, `SELECT` on `flowguard.gold` to the app SP; `CAN_USE` on the warehouse; `CAN_RUN` on Genie space if configured. **🖐 MANUAL** run it.
26. **💻 TERMINAL** — Deploy:
    ```bash
    databricks sync ./flowguard/app /Workspace/Users/<you>/flowguard-app --profile flowguard
    databricks apps deploy flowguard --source-code-path /Workspace/Users/<you>/flowguard-app --profile flowguard
    ```
    ⏱ **Checkpoint Fri 9 PM:** app deployed showing at least one real value from gold (even with a rough UI).
27. `app.yaml` env: `DATABRICKS_WAREHOUSE_ID`, `CATALOG=flowguard`, `SCHEMA=gold`, `GENIE_SPACE_ID` (blank until created), `DATA_MODE=live`. Bundle `static/data/*.json` snapshot with the app so it still demos if the warehouse/quota fails.

### Phase 4 — Polish, stretch, pitch (Sat)

28. **🤖 CODE** — Visual polish, projector legibility, loading states, error states (never show a stack trace in the demo).
29. **Stretch (in order, only after 1–28 work):**
    a. **Genie** — **🖐 MANUAL (Databricks UI)** *Genie → New* space "FlowGuard – Park Royal", add gold tables, paste instructions + sample questions from Appendix C, test, copy the space ID from the URL (`/genie/rooms/<id>`) into `app.yaml`; grant the app SP **Can run** via the space's Share dialog; **🤖 CODE** wire the drawer to `/api/genie/ask`.
    b. After-Hours Watch (`08_after_hours.py` + small tab).
    c. `ai_query` one-paragraph "ops briefing" per surge day, materialised once into a gold table (never per request).
    d. Status tiles for UBC and Waterfront ("same engine, different crowd DNA") — **only** if Park Royal is fully polished.
30. **Pitch lead** — write `docs/pitch_script.md` (Section 9), rehearse 3× with the live app. Record a **backup screen recording** of the full demo Saturday night.
31. ⏱ **Feature freeze Sat 6 PM.** After that: bugs, rehearsal, slides only.

### Phase 5 — Sunday morning

32. **🖐 MANUAL (Databricks UI)** — By 9:30 AM: **start/restart the app** (24 h auto-stop), start the SQL warehouse, open the app once to warm the cache, load the Boxing Day preset once.
33. Rehearse; keep the backup recording and `DATA_MODE=snapshot` ready.

---

## 9. Demo & pitch script (5 minutes)

| Time | Beat |
|---|---|
| 0:00–0:35 | **Hook.** "Boxing Day, Park Royal, 12:30 PM. Nearly four times the usual number of people are leaving to cross the water — three and a half hours earlier than a normal day. Knowing a crowd is *there* isn't enough. The real question is when it hits the transit network." |
| 0:35–1:10 | **Insight.** Half of Park Royal's visitors don't live on the North Shore. Arrivals are unpredictable bursts — but departures follow a stable pattern we learned from dwell time. Corridors: south over Lions Gate, east on the brand-new R2. |
| 1:10–3:30 | **Live demo.** Open on a normal Saturday (all green). Switch to Boxing Day, press ▶. Pressure climbs → signature flips to "Regional + Extended-stay surge" → radar shows the exit wave building 60–90 min ahead → eastbound turns 🔴 → action card: "stage eastbound trips from 11:45". Actual departures line tracks the forecast. Hand a judge the Scenario Lab: "service −30%" / "event ends 10 PM" → recommendation changes. (Optional: one Genie question.) |
| 3:30–4:20 | **Impact & trust.** Lead time instead of reaction; right service in the right direction on the ~17 surge days/year. "+30-min exit forecast explains 91% of variation vs 28% for the typical-week pattern." Built end-to-end on Databricks (pipeline → MLflow → App). |
| 4:20–5:00 | **Next steps.** Live Rogers feed, TransLink ridership validation, other destinations (same engine works on UBC/Waterfront), Lions Gate operations. Close: *"A normal dashboard tells you Park Royal is crowded. FlowGuard tells you what happens next."* |

### Q&A prep
- **Privacy:** aggregate only, minimum group sizes, no device-level data leaves the pipeline, no individual trajectories.
- **Origin ≠ destination:** origin is the device's home location; we use the aggregate mix as a proxy for direction of outbound demand.
- **Timestamps:** validated as local time (UTC reading puts 29% of mall visits at 1–6 AM; no DST shift).
- **Relative numbers:** synthetic subscriber sample → we report × normal, not headcounts.
- **GTFS:** current schedule pattern by day type applied to historical days; "scheduled service", not capacity.
- **Why not forecast arrivals?** We tried (GBT, R² 0.06). Honest result → focus on what *is* predictable: departures.

---

## 10. Definition of done (MVP)

- [ ] Uncut Park Royal data loaded; silver/gold rebuilt by one Job run.
- [ ] Egress backtest logged to MLflow and shown in the app.
- [ ] GTFS service by route group joined; readiness colours sensible on Boxing Day vs a normal Saturday.
- [ ] Deployed Databricks App: day picker with presets, replay, all 7 panels, Scenario Lab, action card.
- [ ] Snapshot fallback works with the warehouse stopped.
- [ ] No absolute headcounts anywhere in the UI.
- [ ] 5-minute script rehearsed; backup recording saved.

## 11. Out of scope (do not build unless everything above is done)
GTFS-Realtime, weather, H3/hex maps, deep learning, arrival forecasting as a product feature, multi-POI deep dives, authentication beyond Databricks Apps defaults.

---

## Appendix A — Free Edition gotchas checklist
- Restart the app within 24 h of judging; warm the warehouse.
- Don't loop LLM calls per row or per request.
- Keep gold tables small (the app reads ≤ a few thousand rows per request).
- If compute is shut down for quota, switch the app to `DATA_MODE=snapshot`.
- Download external files locally, upload to the Volume.

## Appendix B — Suggested team split
| Role | Owns |
|---|---|
| Data engineer | 01–04, Job |
| Analyst/ML | 05 + MLflow + backtest numbers for the pitch |
| Transit/GTFS | 06 + route-group review |
| Backend/deploy | app backend, 90_deploy, app config, snapshot |
| Frontend | static UI, replay, Scenario Lab |
| Pitch lead | story, slides, Genie space, rehearsal, backup recording |

## Appendix C — Genie space draft (stretch)
**Instructions:**
> This data describes a synthetic, aggregate sample of mobile devices at Park Royal Shopping Centre (West Vancouver), in 30-minute slots, Nov 2025–Aug 2026. Times are Vancouver local time. Numbers are a sample — express results as ratios to normal ("× normal") or shares, never as counts of people. "Pressure" = occupancy ÷ the median occupancy for the same weekday and time slot. "Surge day" = daily arrivals ≥ 1.6× the median for that weekday. Corridors: SOUTH (to Vancouver/Richmond/Delta via Lions Gate), EAST (to Burnaby/New West/Surrey/Tri-Cities via the R2 direction), NS_WEST (West Vancouver), NS_EAST (North Vancouver), BC_OTHER, OUT_OF_REGION. Readiness compares expected outbound demand per scheduled trip with normal. Prefer `gold.flowguard_timeline`, `gold.pr_days` and `gold.pr_slots`.

**Sample questions:**
- Which 5 days had the highest surge ratio?
- On Saturdays, when does eastbound exit pressure usually peak?
- Which route group is most often Strained or Critical, and at what times?
- How did the corridor mix on Boxing Day differ from a normal Friday?
- What share of Park Royal's crowd is from outside the North Shore on surge days vs normal days?

## Appendix D — Sources
- TransLink GTFS static data: https://www.translink.ca/about-us/doing-business-with-translink/app-developer-resources/gtfs/gtfs-data (zip: https://gtfs-static.translink.ca/gtfs/google_transit.zip)
- Lions Gate Bridge counterflow: https://www.tranbc.ca/2019/08/29/how-the-lions-gate-bridge-counterflow-works/
- R2 RapidBus extension to Metrotown (Sept 7, 2026): https://buzzer.translink.ca/2026/09/major-r2-rapidbus-extension-provides-all-day-service-from-park-royal-to-metrotown
- Databricks Free Edition limitations: https://docs.databricks.com/aws/en/getting-started/free-edition-limitations
- Workshop repo: https://github.com/databricks-solutions/data-intelligence-for-smarter-community
