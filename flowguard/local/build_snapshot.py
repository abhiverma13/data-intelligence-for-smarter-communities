"""Write the app's offline snapshot (flowguard/app/static/data/<poi>/*.json) from the local gold tables.

The JSON holds the same raw rows the live SQL queries return, so the app renders identical payloads
with the warehouse stopped. Run after run_local.py (which writes flowguard/local/data/gold/*.csv):

    python flowguard/local/run_local.py
    python flowguard/local/build_snapshot.py

Per POI it keeps the demo days (presets), holidays and surge days, plus a few ordinary days.
"""
from __future__ import annotations

import json
import math
import shutil
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from server.logic import CORRIDOR_COLS, POIS, SLOT_COLS, TIMELINE_COLS  # noqa: E402

GOLD = ROOT / "local" / "data" / "gold"
OUT = ROOT / "app" / "static" / "data"
DAY_COLS = ["date", "day_type", "service_day_type", "arrivals", "surge_ratio", "is_surge", "peak_pressure", "label"]


def rows(df: pd.DataFrame) -> list:
    out = []
    for r in df.to_dict(orient="records"):
        o = {}
        for k, v in r.items():
            if isinstance(v, pd.Timestamp):
                v = v.strftime("%Y-%m-%d %H:%M:%S")
            elif isinstance(v, float):
                v = None if math.isnan(v) else round(v, 4)
            elif hasattr(v, "item"):
                v = v.item()
            o[k] = v
        out.append(o)
    return out


def dump(obj, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, separators=(",", ":"), ensure_ascii=False)


def with_date(df: pd.DataFrame, ts: str) -> pd.DataFrame:
    return df.assign(_d=df[ts].dt.strftime("%Y-%m-%d"))


def main():
    days_all = pd.read_csv(GOLD / "days.csv")
    days_all["label"] = days_all["label"].fillna("")
    slots_all = with_date(pd.read_csv(GOLD / "slots.csv", parse_dates=["slot_ts"]), "slot_ts")
    tl_all = with_date(pd.read_csv(GOLD / "flowguard_timeline.csv", parse_dates=["slot_ts"]), "slot_ts")
    fc_all = pd.read_csv(GOLD / "egress_forecast.csv", parse_dates=["origin_slot_ts"])
    fc_all = with_date(fc_all[fc_all["corridor"] == "ALL"], "origin_slot_ts")
    sc_all = with_date(pd.read_csv(GOLD / "slot_corridor.csv", parse_dates=["slot_ts"]), "slot_ts")
    kernel_all = pd.read_csv(GOLD / "egress_kernel.csv")
    bt_all = pd.read_csv(GOLD / "model_backtest.csv")

    for legacy in ["days.json", "model.json"]:            # single-location layout from before
        (OUT / legacy).unlink(missing_ok=True)
    shutil.rmtree(OUT / "day", ignore_errors=True)

    total = 0
    for poi in POIS:
        k = poi["key"]
        days = days_all[days_all["poi"] == k]
        wanted = {d for d, _ in poi["presets"]} | set(days.loc[(days["label"] != "") | days["is_surge"], "date"])
        normal = days[days["surge_ratio"].between(0.95, 1.05) & (days["label"] == "")]
        for dt in ["Wed", "Fri", "Sat", "Sun"]:
            wanted |= set(normal[normal["day_type"] == dt]["date"].iloc[5:6])
        shutil.rmtree(OUT / k, ignore_errors=True)
        slots, tl = slots_all[slots_all["poi"] == k], tl_all[tl_all["poi"] == k]
        fc, sc = fc_all[fc_all["poi"] == k], sc_all[sc_all["poi"] == k]
        for d in sorted(wanted):
            day = days[days["date"] == d]
            if day.empty:
                print(f"  skip {k} {d}: not in data")
                continue
            dump({
                "day": rows(day[DAY_COLS])[0],
                "slots": rows(slots.loc[slots["_d"] == d, SLOT_COLS]),
                "timeline": rows(tl.loc[tl["_d"] == d, TIMELINE_COLS]),
                "forecast_all": rows(fc.loc[fc["_d"] == d, ["origin_slot_ts", "horizon", "dep_hat"]]),
                "corridors": rows(sc.loc[sc["_d"] == d, CORRIDOR_COLS]),
            }, OUT / k / "day" / f"{d}.json")
        dump(rows(days[DAY_COLS]), OUT / k / "days.json")
        dump({"kernel": rows(kernel_all[kernel_all["poi"] == k][["k", "p", "cum_p"]]),
              "backtest": rows(bt_all[bt_all["poi"] == k][["horizon", "model", "target", "r2", "mae", "n"]])},
             OUT / k / "model.json")
        size = sum(p.stat().st_size for p in (OUT / k).rglob("*.json"))
        total += size
        print(f"snapshot {k}: {len(list((OUT / k / 'day').glob('*.json')))} days, {size / 1e6:.1f} MB")
    print(f"total {total / 1e6:.1f} MB -> {OUT}")


if __name__ == "__main__":
    main()
