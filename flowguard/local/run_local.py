"""Build every FlowGuard gold table locally with pandas, for all points of interest, using the same
fg_core formulas as the Databricks notebooks. Useful for checking numbers fast and for the app snapshot.

Usage (from the repo root):
    python flowguard/local/run_local.py                 # all POIs
    python flowguard/local/run_local.py ubc waterfront  # a subset

Raw CSVs are read from FLOWGUARD_DATA_DIR (default ~/Downloads/OneDrive_1_2026-09-25); GTFS from
flowguard/local/data/gtfs/. Gold tables are written to flowguard/local/data/gold/*.csv (all POIs stacked).
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
RAW_DIR = Path(os.environ.get("FLOWGUARD_DATA_DIR", str(Path.home() / "Downloads" / "OneDrive_1_2026-09-25")))


def load_visits(poi: dict) -> pd.DataFrame:
    """pandas mirror of 02_bronze + 03_silver for one POI."""
    raw = pd.read_csv(RAW_DIR / poi["file"], dtype=str).drop_duplicates()
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
    corr = corr[corr["poi"] == poi["key"]][["origin", "corridor"]]
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


def run_poi(poi: dict, gtfs: dict, last, ref_day, overrides_all) -> dict:
    t0 = time.time()
    v = load_visits(poi)
    unmapped = sorted(v.loc[v["corridor"] == "OTHER", "origin"].unique())
    print(f"\n######## {poi['name']}: {len(v):,} visits ({time.time() - t0:.0f}s)  unmapped origins: {unmapped or 'none'}")
    slot_agg, corr_agg = aggregate(v)
    slots, slot_corridor, days = C.build_slots(slot_agg, corr_agg, ref_day, poi)
    k_counts = v[v["arrival_slot"] < pd.Timestamp(S.TRAIN_END)].groupby("egress_k").size()
    kernel = C.estimate_kernel(k_counts, poi)
    forecast, backtest = C.run_forecasts(slots, slot_corridor, kernel, poi)
    backtest = pd.concat([backtest, C.gbt_arrival_backtest(slots, poi)], ignore_index=True)
    ov = overrides_all[overrides_all["poi"] == poi["key"]]
    departures, near = C.poi_departures(gtfs["stops"], gtfs["stop_times"], gtfs["trips"], gtfs["routes"], last,
                                        dict(zip(ov["trip_headsign"], ov["route_group"])), poi)
    service = C.service_30min(departures, gtfs["calendar"], gtfs["calendar_dates"], poi)
    timeline, typical_load = C.build_timeline(slots, slot_corridor, forecast, service, days, poi)
    o_days, o_slots, o_corr, o_tl = C.build_outlook(slots, slot_corridor, timeline, days, departures,
                                                   gtfs["calendar"], gtfs["calendar_dates"], ref_day, poi)

    bt = backtest.pivot_table(index="model", columns="horizon", values="r2").round(3)
    print("== backtest R2\n" + bt.to_string())
    print("== top days\n" + days.sort_values("surge_ratio", ascending=False).head(8)[
        ["date", "day_type", "surge_ratio", "peak_pressure", "label"]].round(2).to_string(index=False))
    print(f"== stops within {poi['stop_radius_m']} m: {len(near)}; departing trips by group (weekday feed, all days):")
    print(departures.groupby(["route_group", "route_name", "trip_headsign"]).size().rename("trips").to_string())
    print("== scheduled capacity per day type\n" + service.groupby(["service_day_type", "route_group"])[
        "scheduled_capacity"].sum().unstack().to_string())
    day = timeline[timeline["slot_of_day"].between(18, 44)]
    ready = pd.concat([day[f"readiness_h{h}"] for h in S.HORIZONS]).value_counts(normalize=True).round(3)
    print("== readiness mix, daytime, all days: " + ", ".join(f"{k} {v:.1%}" for k, v in ready.items()))
    for d in days.sort_values("surge_ratio", ascending=False)["date"].head(1).tolist() + [
            days.loc[(days["surge_ratio"].between(0.95, 1.05)) & (days["day_type"] == "Sat"), "date"].iloc[0]]:
        t = timeline[(timeline["date"] == d) & timeline["slot_of_day"].between(18, 44)]
        print(f"== {pd.Timestamp(d):%a %Y-%m-%d} readiness +60 min, 09:00–22:00")
        print(t.groupby("route_group")["readiness_h2"].value_counts().unstack(fill_value=0).to_string())
    return {"slots": slots, "slot_corridor": slot_corridor, "days": days, "egress_kernel": kernel,
            "egress_forecast": forecast, "model_backtest": backtest, "transit_departures": departures,
            "transit_stops": near, "transit_service_30min": service, "flowguard_timeline": timeline,
            "outlook_days": o_days, "outlook_slots": o_slots, "outlook_slot_corridor": o_corr, "outlook_timeline": o_tl}


def main():
    wanted = sys.argv[1:] or [p["key"] for p in S.POIS]
    GOLD.mkdir(parents=True, exist_ok=True)
    g = DATA / "gtfs"
    gtfs = {n: pd.read_csv(g / f"{n}.txt", dtype=str)
            for n in ["stops", "stop_times", "trips", "routes", "calendar", "calendar_dates"]}
    last = C.last_stops(gtfs["stop_times"])
    ref_day = pd.read_csv(CONFIG / "day_type_overrides.csv", dtype=str)
    overrides = pd.read_csv(CONFIG / "route_group_overrides.csv", dtype=str)
    results = [run_poi(S.POI_BY_KEY[k], gtfs, last, ref_day, overrides) for k in wanted]
    for name in results[0]:
        pd.concat([r[name] for r in results], ignore_index=True).to_csv(GOLD / f"{name}.csv", index=False)
    print(f"\nwrote {GOLD}")


if __name__ == "__main__":
    main()
