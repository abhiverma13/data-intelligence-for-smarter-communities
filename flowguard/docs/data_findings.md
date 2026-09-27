# FlowGuard — verified findings

Computed on the full files (Park Royal: 5,435,118 visits after removing 700 exact duplicates; all three
locations: about 21.5M visits, Nov 1 2025 – Aug 31 2026) with `local/run_local.py`, which uses the same `fg_core`
formulas as the Databricks notebooks. These **replace spec §4.3**, whose numbers came from an Excel-truncated
sample. Use these in the pitch.

## Still true
- **Timestamps are local time:** arrivals peak 15:00–16:00, 1–6 AM holds ~3% of arrivals.
- **Home-area mix (Park Royal):** 52% of visits are from North Shore home areas (28% West Van, 24% North Van),
  48% from elsewhere (South 15%, East 12%, rest of BC 6%, out of province 16%). Home area is only a proxy for
  which routes a crowd loads, not a record of where anyone goes; don't present it as destinations.
- **Egress kernel** (share gone within k half-hour slots of arrival): 13% · 37% · 53% · 63% · 70% (k=0..4), 85% by k=8.
- **Overnight activity** exists at every location while it is closed (1–6 AM holds ~3% of arrivals), which is
  what the after-hours watch measures. It uses volume only; visitor origin is never used as a security signal.

## Different from the spec
| Spec (truncated sample) | Uncut data |
|---|---|
| Arrivals bursty, many empty slots | **Smooth** — no empty slots at all |
| Arrivals unforecastable (GBT R² 0.06) | **Forecastable**: GBT R² 0.95 at +30 min (typical week 0.70) |
| 17 surge days ≥ 1.6× | **3 days ≥ 1.6×**; **8 days ≥ 1.4×** (threshold now 1.4) — all Dec 5–Jan 6 |
| Boxing Day 3.25× | Boxing Day **2.03×** daily arrivals, peak pressure **2.3×** occupancy |
| Off-North-Shore exit peak 3.7× a normal Friday, at 12:30 | **2.1×** a normal Friday's peak, at **16:30 vs 18:30** (2 h earlier) |
| Surges are more regional | Corridor mix barely moves on surge days |
| Busy periods → longer stays (61 → 78 min) | Busy daytime slots → slightly **shorter** stays (73 vs 80 min) |

## Model results (holdout Jul–Aug 2026, trained Nov–Jun) — departures, all corridors
| Horizon | **Egress (scaled by today's busyness)** | Egress, spec formula | Typical week |
|---|---|---|---|
| +30 min | **0.97** | 0.96 | 0.64 |
| +60 min | **0.96** | 0.89 | 0.64 |
| +90 min | **0.95** | 0.83 | 0.64 |
| +120 min | **0.94** | 0.78 | 0.64 |

"Scaled by today's busyness": arrivals still to come = typical pattern × (last 2 h arrivals ÷ typical last 2 h).

## Transit readiness (+60 min, 09:00–22:00, slots per route group)
| Day | Eastbound | Downtown | West Van local |
|---|---|---|---|
| Boxing Day (Fri, holiday service) | 8 🔴 · 8 🟠 · 6 🟡 · 5 🟢 | 1 🔴 · 18 🟠 · 5 🟡 · 3 🟢 | 8 🔴 · 14 🟠 · 3 🟡 · 2 🟢 |
| Normal Saturday 2026-04-25 | 25 🟢 · 2 🟡 | 22 🟢 · 5 🟡 | 18 🟢 · 7 🟡 · 2 🟠 |

First Boxing Day alerts: **10:30 → West Van local strained at 12:30**, and **11:00 → eastbound strained at 13:00**
(2 h lead time). Readiness thresholds (1.2 / 1.7 / 2.3) are the p75 / p95 / p99 of Park Royal's normal-day daytime
gaps. Across all days, daytime slots reach Strained / Critical 5.3% / 1.4% of the time at Park Royal, 7.0% / 0.7% at
UBC and 2.9% / 0.3% at Waterfront.

## Same engine, three crowds (UBC and Waterfront added)

| | Park Royal | UBC | Waterfront |
|---|---|---|---|
| Visits (after dedupe) | 5.4M | 8.0M | 8.1M |
| Gone within 1 h of arriving (kernel) | 37% | 48% | 43% |
| Gone within 2.5 h | 70% | 80% | 85% |
| Surge days (≥ 1.4× normal) | 8 (Christmas shopping, Dec 5–Jan 6) | 5 (exam season, late Nov–early Dec) | 12 (June/July Saturdays, early Dec) |
| Exit forecast R² +30 / +120 min | 0.97 / 0.94 | 0.99 / 0.93 | 0.99 / 0.97 |
| Typical-week pattern R² | 0.64 | **−1.09** | 0.68 |
| Spec formula (no today scaling) +120 min | 0.78 | **−0.13** | 0.79 |

- **UBC is seasonal.** The Jul–Aug holdout is summer break, so the term-trained typical week is badly wrong
  (R² −1.09). The egress model still scores 0.93–0.99 because it starts from the crowd actually on campus and
  scales future arrivals by today's busyness. UBC's "normal" is therefore seasonal (same weekday ±4 weeks),
  so an ordinary term Wednesday reads ~0.9× normal, not a surge.
- **Waterfront is the most predictable** (R² ≥ 0.97 at every horizon): commuter-driven, short stays.
  Service includes SkyTrain, SeaBus and West Coast Express, weighted by capacity (bus-equivalents).
- Demo days: UBC exam-season Saturday 2025-12-06 (1.66×, first alert 10:30); Waterfront June event Saturday
  2026-06-06 (1.86×, peak pressure 2.65×, first alert 09:00).

## After-hours watch (security)
Overnight presence (mean devices on site 00:00–06:00, stays over 24 h excluded) vs a normal night for that weekday;
UBC uses its seasonal (±4 weeks) normal. Flagged at ≥ 1.5×, which happens only a few nights a year:

| | Park Royal | UBC | Waterfront |
|---|---|---|---|
| Unusual nights | 3 | 4 | 5 |
| Highest | 2025-12-27, the night after Boxing Day (1.78×) | 2025-12-07, exam season (1.69×) | 2026-06-06, June event Saturday (1.65×) |

At 03:00 on Dec 27 Park Royal shows 1.8× pressure while no buses are scheduled ("No service"). Volume only, never
visitor origin.

## Outlook (future dates)
Sept 7 2026 – Jan 3 2027 (the window the published GTFS feed covers), 119 days per location. The crowd is the
median of analog past days: the same holiday last year, otherwise the same week last year (±1 week), otherwise a
typical weekday (Sept–Oct, which has no year-ago data). Service is the published timetable of each exact date,
including holiday schedules.
- Park Royal: 6 expected surge days, all in December 2026. Boxing Day 2026 is expected at 2.03× (based on Boxing Day
  2025), with the first eastbound alert at 10:30 for 12:30.
- Expected unusual nights: Park Royal Dec 12 (1.57×) and Dec 19 (1.65×), 2026.

## Pitch framing that the data supports
- *Arrivals are predictable, but arrivals aren't the operator's problem; the exit wave is.* FlowGuard turns the
  crowd on site plus the learned stay-length pattern into expected departures up to 2 h ahead (R² ≥ 0.93 at every
  location and horizon) and compares them with the **scheduled service** for each group of routes.
- Boxing Day at Park Royal: 2.03× the usual crowd; the exit wave peaks at 2.1× a normal Friday's peak and 2 h earlier
  (16:30 vs 18:30), on a holiday schedule. FlowGuard flags eastbound strain 2 h before it happens.
- Say "explains 97% of the variation", not "97% accurate". Route groups use the crowd's home-area mix as an
  estimate of which routes feel the pressure; TransLink tap-on data would confirm it.
