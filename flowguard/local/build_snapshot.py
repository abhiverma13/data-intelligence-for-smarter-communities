"""Write the app's offline snapshot (flowguard/app/static/data/*.json) from the local gold tables.

The JSON holds the same raw rows the live SQL queries return, so the app renders identical payloads
with the warehouse stopped. Run after run_local.py (which writes flowguard/local/data/gold/*.csv):

    python flowguard/local/run_local.py
    python flowguard/local/build_snapshot.py
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from server.logic import PRESETS, SLOT_COLS, TIMELINE_COLS  # noqa: E402

GOLD = ROOT / "local" / "data" / "gold"
OUT = ROOT / "app" / "static" / "data"
EXTRA_NORMAL_DAYS = 6          # a few ordinary days of each kind besides presets/labelled/surge days


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


def main():
    days = pd.read_csv(GOLD / "pr_days.csv", parse_dates=["peak_pressure_slot"])
    days["label"] = days["label"].fillna("")
    slots = pd.read_csv(GOLD / "pr_slots.csv", parse_dates=["slot_ts"])
    slots["date"] = slots["slot_ts"].dt.strftime("%Y-%m-%d")
    tl = pd.read_csv(GOLD / "flowguard_timeline.csv", parse_dates=["slot_ts"])
    tl["date"] = tl["slot_ts"].dt.strftime("%Y-%m-%d")
    fc = pd.read_csv(GOLD / "egress_forecast.csv", parse_dates=["origin_slot_ts"])
    fc = fc[fc["corridor"] == "ALL"]
    fc["date"] = fc["origin_slot_ts"].dt.strftime("%Y-%m-%d")
    psc = pd.read_csv(GOLD / "pr_slot_corridor.csv", parse_dates=["slot_ts"])
    psc["date"] = psc["slot_ts"].dt.strftime("%Y-%m-%d")

    # which days go into the snapshot: presets, labelled/surge days, and a few ordinary days
    wanted = {d for d, _ in PRESETS} | set(days.loc[days["label"] != "", "date"])
    normal = days[(days["surge_ratio"].between(0.9, 1.1)) & (days["label"] == "")]
    for dt in ["Fri", "Sat", "Sun"]:
        wanted |= set(normal[normal["day_type"] == dt]["date"].iloc[:: max(1, len(normal) // EXTRA_NORMAL_DAYS)].head(2))

    for f in (OUT / "day").glob("*.json") if (OUT / "day").exists() else []:
        f.unlink()
    for d in sorted(wanted):
        day = days[days["date"] == d]
        if day.empty:
            print(f"skip {d}: not in data")
            continue
        dump({
            "day": rows(day.drop(columns=["peak_pressure_slot"]))[0],
            "slots": rows(slots.loc[slots["date"] == d, SLOT_COLS]),
            "timeline": rows(tl.loc[tl["date"] == d, TIMELINE_COLS]),
            "forecast_all": rows(fc.loc[fc["date"] == d, ["origin_slot_ts", "horizon", "dep_hat"]]),
            "corridor_mix": rows(psc[psc["date"] == d].groupby("corridor", as_index=False)["departures"].sum()),
        }, OUT / "day" / f"{d}.json")

    dump(rows(days.drop(columns=["peak_pressure_slot"])), OUT / "days.json")
    dump({"kernel": rows(pd.read_csv(GOLD / "egress_kernel.csv")),
          "backtest": rows(pd.read_csv(GOLD / "model_backtest.csv"))}, OUT / "model.json")
    size = sum(p.stat().st_size for p in OUT.rglob("*.json"))
    print(f"snapshot: {len(list((OUT / 'day').glob('*.json')))} days, {size / 1e6:.1f} MB -> {OUT}")


if __name__ == "__main__":
    main()
