"""Build every FlowGuard gold table locally with pandas, using the same fg_core formulas as the
Databricks notebooks. Useful for checking numbers fast and as an offline fallback.

Usage (from the repo root):
    set FLOWGUARD_PR_CSV=C:\\path\\to\\synthetic_park_royal_mall.csv   (PowerShell: $env:FLOWGUARD_PR_CSV=...)
    python flowguard/local/run_local.py

Reads GTFS from flowguard/local/data/gtfs/ and writes gold tables to flowguard/local/data/gold/*.csv.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipeline"))
import fg_core as C          # noqa: E402
import fg_settings as S      # noqa: E402

DATA = ROOT / "local" / "data"
GOLD = DATA / "gold"
CONFIG = ROOT / "config"
PR_CSV = os.environ.get("FLOWGUARD_PR_CSV", str(Path.home() / "Downloads" / "OneDrive_1_2026-09-25" / "synthetic_park_royal_mall.csv"))


def load_visits() -> pd.DataFrame:
    """pandas mirror of 02_bronze + 03_silver."""
    raw = pd.read_csv(PR_CSV, dtype=str).drop_duplicates()
    ts = pd.to_datetime(raw["timestamp"].str.replace("Z", "", regex=False))
    if S.TS_IS_UTC:
        ts = ts.dt.tz_localize("UTC").dt.tz_convert(S.LOCAL_TZ).dt.tz_localize(None)
    dwell = raw["dwell_time"].astype(int)
    v = pd.DataFrame({"arrival_ts": ts, "dwell_time": dwell, "origin": raw["origin"].str.strip()})
    v["departure_ts"] = v["arrival_ts"] + pd.to_timedelta(v["dwell_time"], unit="min")
    v["arrival_slot"] = v["arrival_ts"].dt.floor(f"{S.SLOT_MINUTES}min")
    v["departure_slot"] = v["departure_ts"].dt.floor(f"{S.SLOT_MINUTES}min")
    v["egress_k"] = ((v["departure_slot"] - v["arrival_slot"]) / C.SLOT).astype(int)
    v["long_stay"] = v["dwell_time"] > S.LONG_STAY_MINUTES
    corr = pd.read_csv(CONFIG / "origin_corridor.csv")
    v = v.merge(corr, on="origin", how="left")
    v["corridor"] = v["corridor"].fillna("OTHER")
    return v


def aggregate(v: pd.DataFrame):
    """pandas mirror of the Spark aggregation in 04_gold_slots."""
    arr = v.groupby("arrival_slot").agg(arrivals=("dwell_time", "size"),
                                        arrivals_occ=("long_stay", lambda s: int((~s).sum())),
                                        median_dwell=("dwell_time", "median"))
    dep = v.groupby("departure_slot").agg(departures=("dwell_time", "size"),
                                          departures_occ=("long_stay", lambda s: int((~s).sum())))
    slot_agg = arr.join(dep, how="outer")
    ca = v.groupby(["arrival_slot", "corridor"]).size().rename("arrivals")
    cd = v.groupby(["departure_slot", "corridor"]).size().rename("departures")
    ca.index.names = cd.index.names = ["slot_ts", "corridor"]
    corr_agg = pd.concat([ca, cd], axis=1).fillna(0).reset_index()
    return slot_agg, corr_agg


def main():
    t0 = time.time()
    GOLD.mkdir(parents=True, exist_ok=True)
    v = load_visits()
    print(f"silver rows: {len(v):,}  ({time.time() - t0:.0f}s)")
    slot_agg, corr_agg = aggregate(v)
    ref_day = pd.read_csv(CONFIG / "day_type_overrides.csv", dtype=str)

    pr_slots, pr_slot_corridor, pr_days = C.build_slots(slot_agg, corr_agg, ref_day)
    k_counts = v[v["arrival_slot"] < pd.Timestamp(S.TRAIN_END)].groupby("egress_k").size()
    kernel = C.estimate_kernel(k_counts)
    forecast, backtest = C.run_forecasts(pr_slots, pr_slot_corridor, kernel)
    backtest = pd.concat([backtest, C.gbt_arrival_backtest(pr_slots)], ignore_index=True)

    g = DATA / "gtfs"
    read = lambda n: pd.read_csv(g / f"{n}.txt", dtype=str)  # noqa: E731
    ov = pd.read_csv(CONFIG / "route_group_overrides.csv", dtype=str)
    overrides = dict(zip(ov["trip_headsign"], ov["route_group"]))
    departures, near_stops = C.parkroyal_departures(read("stops"), read("stop_times"), read("trips"), read("routes"), overrides)
    service = C.service_30min(departures, read("calendar"), read("calendar_dates"))
    timeline, typical_load = C.build_timeline(pr_slots, pr_slot_corridor, forecast, service, pr_days)

    for name, df in {"pr_slots": pr_slots, "pr_slot_corridor": pr_slot_corridor, "pr_days": pr_days,
                     "egress_kernel": kernel, "egress_forecast": forecast, "model_backtest": backtest,
                     "gtfs_parkroyal_departures": departures, "gtfs_service_30min": service,
                     "flowguard_timeline": timeline}.items():
        df.to_csv(GOLD / f"{name}.csv", index=False)
    print(f"wrote {GOLD}  ({time.time() - t0:.0f}s)\n")

    print("== backtest"); print(backtest.round(3).to_string(index=False))
    print("\n== surge days"); print(pr_days[pr_days["is_surge"]][["date", "day_type", "surge_ratio", "peak_pressure", "label"]].round(2).to_string(index=False))
    print("\n== labelled days"); print(pr_days[pr_days["label"] != ""][["date", "day_type", "surge_ratio", "label"]].round(2).to_string(index=False))
    print("\n== Park Royal stops"); print(near_stops.round(0).to_string(index=False))
    print("\n== routes per group"); print(departures.groupby(["route_group", "route_short_name", "trip_headsign"]).size().to_string())
    print("\n== typical load (trips-normalised demand)", {k: round(v_, 1) for k, v_ in typical_load.items()})
    for d in ["2025-12-26", "2025-12-20", "2026-04-25", "2026-03-20"]:
        t = timeline[(timeline["date"] == d) & timeline["slot_of_day"].between(18, 44)]
        counts = t.groupby("route_group")["readiness_h2"].value_counts().unstack(fill_value=0)
        print(f"\n== {d} readiness (+60 min), slots 09:00–22:00"); print(counts.to_string())
    bd = timeline[(timeline["date"] == "2025-12-26") & timeline["action_text"].notna()].sort_values("slot_ts")
    print("\n== first Boxing Day action cards")
    for r in bd.head(3).itertuples():
        print(f"[{r.slot_ts:%H:%M}] {r.action_text}")


if __name__ == "__main__":
    main()
