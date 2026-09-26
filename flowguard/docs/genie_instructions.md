# Ask FlowGuard — Genie space setup

Everything to paste into the Databricks Genie space that powers the app's **Ask FlowGuard** drawer.

## 1. Space settings

- **Title:** `FlowGuard – Park Royal, UBC, Waterfront`
- **Description:** `Crowd pressure, exit forecasts, transit readiness and after-hours activity for three Vancouver locations, Nov 2025 – Aug 2026 (observed) plus an outlook to Jan 2027 (expected).`
- **Warehouse:** Serverless Starter Warehouse
- **Tables** (catalog `flowguard`, schema `gold`):
  - `days`
  - `slots`
  - `flowguard_timeline`
  - `model_backtest`
  - `outlook_days`
  - `outlook_timeline`
  - `slot_corridor`
  - `transit_service_30min`

## 2. General instructions (paste into Instructions → Text)

```
You answer questions for TransLink operations staff about three locations (column poi):
park_royal = Park Royal Shopping Centre (West Vancouver), ubc = UBC campus, waterfront = Waterfront Station.
Map the place names in the question to these poi values.

Data: a synthetic, aggregate sample of mobile devices in 30-minute slots, Vancouver local time.
Observed data runs 2025-11-01 to 2026-08-31 (tables days, slots, flowguard_timeline, slot_corridor).
Future dates 2026-09-07 to 2027-01-03 are an OUTLOOK (tables outlook_days, outlook_timeline): expected
conditions built from comparable past days, not observations. Always say "expected" for outlook dates.

Numbers are a sample: never report counts of people or devices. Report ratios to normal ("2.1x normal"),
shares (%), or the readiness levels. Round ratios to one decimal place.

Definitions:
- pressure = people on site vs normal for the same weekday and time (x normal). 1.3+ Elevated, 1.8+ High, 2.5+ Severe.
- surge_ratio = daily arrivals vs a usual day of that weekday; is_surge = surge day.
- flowguard_timeline has one row per poi, 30-minute slot (slot_ts) and route_group. readiness_h1..h4 are the
  transit readiness 30/60/90/120 minutes after slot_ts: Prepared, Watch, Strained, Critical, No service.
  "Strained or Critical" = transit is likely overloaded. gap_hN = expected exit demand per unit of scheduled
  service vs normal. For "what time" questions about readiness_h1, report the time as slot_ts + 30 minutes.
- "Times to avoid" = times of day with the highest share of Strained/Critical readiness and highest pressure.
  Use daytime slots (slot_of_day 16 to 44 = 08:00 to 22:00) unless asked about night.
- Route groups: park_royal EASTBOUND (R2, 255), DOWNTOWN (250/253/254/257, 44), WEST_VAN_LOCAL;
  ubc BROADWAY_EAST (99, 84, 25, 33, 9), DOWNTOWN (4, 14, 44), SOUTH (R4, 49);
  waterfront NORTH_SHORE (SeaBus), EAST (Expo Line, West Coast Express, R5, 14, 4), SOUTH (Canada Line, buses), WEST (4, 7, 14, 44, 50, 5).
- After-hours watch: days.night_ratio = presence 00:00-06:00 vs a normal night; night_unusual = 1.5x or more.
  It is based on activity volume only. Never relate it to visitor origin.
- model_backtest: forecast accuracy on a July-August 2026 holdout. model = 'egress' is FlowGuard's exit forecast;
  r2 = share of variation explained; horizon 1..4 = 30..120 minutes ahead.
- Times: use date_format(slot_ts, 'HH:mm'). Weekdays are in day_type as Mon..Sun.
```

## 3. Example SQL queries (Instructions → SQL queries)

Each has been checked against the warehouse.

**What times should I avoid at Park Royal on Saturdays?**
```sql
SELECT date_format(slot_ts + INTERVAL 30 MINUTES, 'HH:mm') AS time_of_day,
       round(avg(pressure), 2) AS avg_pressure_x_normal,
       round(avg(CASE WHEN readiness_h1 IN ('Strained', 'Critical') THEN 1 ELSE 0 END) * 100, 1) AS pct_strained
FROM flowguard.gold.flowguard_timeline
WHERE poi = 'park_royal' AND day_type = 'Sat' AND slot_of_day BETWEEN 16 AND 44
GROUP BY 1
ORDER BY pct_strained DESC, avg_pressure_x_normal DESC
LIMIT 6
```

**Which route group is most often Strained or Critical?**
```sql
SELECT poi, route_group,
       round(avg(CASE WHEN readiness_h1 IN ('Strained', 'Critical') THEN 1 ELSE 0 END) * 100, 1) AS pct_daytime_slots_strained
FROM flowguard.gold.flowguard_timeline
WHERE slot_of_day BETWEEN 18 AND 44
GROUP BY poi, route_group
ORDER BY pct_daytime_slots_strained DESC
```

**Which days in December 2026 are expected surge days?**
```sql
SELECT poi, date, day_type, round(surge_ratio, 2) AS expected_x_usual, label, method
FROM flowguard.gold.outlook_days
WHERE date BETWEEN '2026-12-01' AND '2026-12-31' AND is_surge
ORDER BY poi, date
```

**Which nights had unusual overnight presence?**
```sql
SELECT poi, date, day_type, round(night_ratio, 2) AS overnight_x_normal
FROM flowguard.gold.days
WHERE night_unusual
ORDER BY night_ratio DESC
```

**How accurate is the exit forecast at each location?**
```sql
SELECT poi, horizon * 30 AS minutes_ahead, round(r2, 3) AS r2
FROM flowguard.gold.model_backtest
WHERE model = 'egress' AND target = 'departures'
ORDER BY poi, horizon
```

**Compare peak pressure across locations on Saturdays**
```sql
SELECT poi, round(percentile(peak_pressure, 0.5), 2) AS median_peak_pressure,
       round(max(peak_pressure), 2) AS max_peak_pressure
FROM flowguard.gold.days
WHERE day_type = 'Sat'
GROUP BY poi
ORDER BY poi
```

## 4. Sample questions (Settings → Sample questions)

- What times should I avoid at Park Royal on Saturdays?
- Which days in December 2026 are expected surge days at each location?
- Which route group at Waterfront is most often Strained or Critical, and at what times?
- Which nights had unusual overnight presence?
- How accurate is the exit forecast at each location?
- When is eastbound demand highest at Park Royal on Boxing Day 2026?
