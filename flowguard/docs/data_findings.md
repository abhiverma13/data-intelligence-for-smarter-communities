# FlowGuard — verified findings (uncut Park Royal data)

Computed on the full file (5,435,118 visits after removing 700 exact duplicates, Nov 1 2025 – Aug 31 2026)
with `local/run_local.py`, which uses the same `fg_core` formulas as the Databricks notebooks. These
**replace spec §4.3**, whose numbers came from an Excel-truncated sample. Use these in the pitch.

## Still true
- **Timestamps are local time:** arrivals peak 15:00–16:00, 1–6 AM holds ~3% of arrivals.
- **Visitor mix:** 52% North Shore (28% West Van, 24% North Van) → **48% must leave the North Shore**:
  South 15%, East 12%, BC other 6%, out-of-province/international 16%.
- **Egress kernel** (share gone within k half-hour slots of arrival): 13% · 37% · 53% · 63% · 70% (k=0..4), 85% by k=8.
- **Overnight (security):** out-of-region share of midnight–6 AM arrivals is 33% vs 15% in the daytime.

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
| Boxing Day (Fri, holiday service) | 8 🔴 · 8 🟠 · 6 🟡 · 5 🟢 | 1 🔴 · 19 🟠 · 4 🟡 · 3 🟢 | 8 🔴 · 14 🟠 · 3 🟡 · 2 🟢 |
| Normal Saturday 2026-04-25 | 25 🟢 · 2 🟡 | 21 🟢 · 6 🟡 | 17 🟢 · 8 🟡 · 2 🟠 |

First Boxing Day alert: **11:00 → eastbound strained at 13:00 (2 h lead time)**.

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

## Pitch framing that the data supports
- *Arrivals are predictable, but arrivals aren't the operator's problem — the exit wave is.* FlowGuard turns
  arrivals + learned dwell into **departures by direction**, 2 h ahead, R² ≥ 0.94, and compares them with the
  **scheduled trips** leaving Park Royal.
- Boxing Day: the off-North-Shore exit wave is 2.1× a normal Friday and peaks 2 h earlier — on a
  holiday schedule. FlowGuard flags eastbound strain 2 h before it happens.
