"""Shape gold-table rows into the payloads the frontend renders.

Live (SQL warehouse) and snapshot (bundled JSON) modes hand the same raw rows to these functions,
so both produce identical payloads. Per-location settings come from pois.json (shared with the
pipeline); readiness thresholds mirror flowguard/pipeline/fg_settings.py.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Dict, List

SLOT_MINUTES = 30
HORIZONS = [1, 2, 3, 4]
PRESSURE_LEVELS = [[1.3, "Normal"], [1.8, "Elevated"], [2.5, "High"], [None, "Severe"]]
READINESS_LEVELS = [[1.2, "Prepared"], [1.7, "Watch"], [2.3, "Strained"], [None, "Critical"]]
MIN_DEMAND_SHARE = 0.5
SERVICE_LABELS = {"WEEKDAY": "weekday", "SATURDAY": "Saturday", "SUNDAY_HOLIDAY": "Sunday/holiday"}

with open(Path(__file__).with_name("pois.json"), encoding="utf-8") as _f:
    POIS: List[Dict[str, Any]] = json.load(_f)["pois"]
POI_BY_KEY = {p["key"]: p for p in POIS}
DEFAULT_POI = POIS[0]["key"]

SLOT_COLS = [
    "slot_ts", "slot_of_day", "pressure", "pressure_level", "signature", "departures", "baseline_departures",
    "local_share", "baseline_local_share", "regional_share", "baseline_regional_share", "stay_ratio", "visitor_ratio",
]
CORRIDOR_COLS = ["slot_ts", "corridor", "departures", "share", "baseline_share"]
TIMELINE_COLS = ["slot_ts", "slot_of_day", "route_group", "typical_load", "typical_demand", "action_text", "action_priority"] + [
    f"{m}_h{h}" for h in HORIZONS for m in ("dep_hat", "demand_idx", "actual", "svc", "gap", "readiness")
]


def clean(v: Any) -> Any:
    """JSON-safe scalar: NaN/inf → None, floats rounded."""
    if v is None:
        return None
    if isinstance(v, float):
        if math.isnan(v) or math.isinf(v):
            return None
        return round(v, 4)
    return v


def hhmm(ts: str) -> str:
    return str(ts)[11:16]


def slot_index(ts: str) -> int:
    t = hhmm(ts)
    return int(t[:2]) * 2 + int(t[3:]) // SLOT_MINUTES


def pois_payload() -> List[Dict[str, Any]]:
    return [{"key": p["key"], "name": p["name"], "full_name": p["full_name"]} for p in POIS]


def days_payload(poi: Dict[str, Any], day_rows: List[Dict[str, Any]], available: set | None = None) -> Dict[str, Any]:
    by_date = {str(r["date"])[:10]: r for r in day_rows}
    labels = dict(poi["presets"])

    def item(d, r):
        return {
            "date": d, "label": labels.get(d) or r.get("label") or "", "day_type": r["day_type"],
            "surge_ratio": clean(float(r["surge_ratio"])), "is_surge": bool(r["is_surge"]),
            "peak_pressure": clean(float(r["peak_pressure"])) if r.get("peak_pressure") is not None else None,
        }

    keep = (lambda d: d in available) if available is not None else (lambda d: True)
    presets = [item(d, by_date[d]) for d, _ in poi["presets"] if d in by_date and keep(d)]
    rest = [item(d, r) for d, r in sorted(by_date.items()) if keep(d)]
    return {"poi": poi["key"], "presets": presets, "days": rest, "default_date": poi.get("default_date") or ""}


def model_payload(kernel: List[Dict[str, Any]], backtest: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "kernel": [{"k": int(r["k"]), "cum_p": clean(float(r["cum_p"]))} for r in sorted(kernel, key=lambda r: int(r["k"]))],
        "backtest": [{k: clean(v) if not isinstance(v, str) else v for k, v in r.items()} for r in backtest],
    }


def day_payload(poi: Dict[str, Any], day: Dict[str, Any], slots: List[Dict[str, Any]], timeline: List[Dict[str, Any]],
                forecast_all: List[Dict[str, Any]], corridors: List[Dict[str, Any]]) -> Dict[str, Any]:
    slots = sorted(slots, key=lambda r: int(r["slot_of_day"]))
    n = len(slots)
    ckeys = [c["key"] for c in poi["corridors"]]

    shares = [{c: None for c in ckeys} for _ in range(n)]
    base_shares = [{c: None for c in ckeys} for _ in range(n)]
    mix = {c: 0.0 for c in ckeys}
    for r in corridors:
        i, c = slot_index(r["slot_ts"]), r["corridor"]
        if c in mix and i < n:
            shares[i][c] = clean(r["share"])
            base_shares[i][c] = clean(r["baseline_share"])
            mix[c] += float(r["departures"] or 0)

    slot_out = []
    for i, r in enumerate(slots):
        slot_out.append({
            "t": hhmm(r["slot_ts"]),
            "pressure": clean(r["pressure"]), "level": r["pressure_level"], "signature": r["signature"],
            "local": clean(r["local_share"]), "local_base": clean(r["baseline_local_share"]),
            "regional": clean(r["regional_share"]), "regional_base": clean(r["baseline_regional_share"]),
            "stay": clean(r["stay_ratio"]), "visitor": clean(r["visitor_ratio"]),
            "shares": shares[i], "base_shares": base_shares[i],
        })

    usual = [clean(float(r["baseline_departures"])) for r in slots]
    fc = [[None] * len(HORIZONS) for _ in range(n)]
    for r in forecast_all:
        fc[slot_index(r["origin_slot_ts"])][int(r["horizon"]) - 1] = clean(float(r["dep_hat"]))
    exit_wave = {
        "normal_peak": max(u for u in usual if u is not None),
        "actual": [int(r["departures"]) for r in slots],
        "usual": usual,
        "forecast": fc,
    }

    groups = {}
    for g in poi["groups"]:
        rows = sorted((r for r in timeline if r["route_group"] == g["key"]), key=lambda r: int(r["slot_of_day"]))
        first = rows[0] if rows else {}
        groups[g["key"]] = {
            "label": g["label"], "routes": g["routes"], "corridors": g["corridors"], "levers": g["levers"],
            "typical_load": clean(first.get("typical_load")), "typical_demand": clean(first.get("typical_demand")),
            "rows": [{
                "dep": [clean(r[f"dep_hat_h{h}"]) for h in HORIZONS],
                "idx": [clean(r[f"demand_idx_h{h}"]) for h in HORIZONS],
                "act": [clean(r[f"actual_h{h}"]) for h in HORIZONS],
                "svc": [clean(r[f"svc_h{h}"]) for h in HORIZONS],
                "gap": [clean(r[f"gap_h{h}"]) for h in HORIZONS],
                "ready": [r[f"readiness_h{h}"] for h in HORIZONS],
                "action": r.get("action_text"), "priority": clean(r.get("action_priority")),
            } for r in rows],
        }

    total = sum(mix.values()) or 1.0
    group_mix = {g["key"]: sum(mix.get(c, 0.0) for c in g["corridors"]) / total for g in poi["groups"]}

    d = str(day["date"])[:10]
    return {
        "poi": {"key": poi["key"], "name": poi["name"], "full_name": poi["full_name"], "hub": poi["hub"],
                "catchment_kpi": poi["catchment_kpi"], "replay_start": poi.get("replay_start", "10:00")},
        "date": d,
        "label": dict(poi["presets"]).get(d) or day.get("label") or "",
        "day_type": day["day_type"],
        "service_day_type": day["service_day_type"],
        "service_label": SERVICE_LABELS.get(day["service_day_type"], day["service_day_type"]),
        "surge_ratio": clean(float(day["surge_ratio"])),
        "is_surge": bool(day["is_surge"]),
        "thresholds": {"pressure": PRESSURE_LEVELS, "readiness": READINESS_LEVELS,
                       "min_demand_share": MIN_DEMAND_SHARE, "slot_minutes": SLOT_MINUTES},
        "corridors": [{"key": c["key"], "label": c["label"]} for c in poi["corridors"]],
        "slots": slot_out,
        "exit": exit_wave,
        "groups": groups,
        "group_order": [g["key"] for g in poi["groups"]],
        "group_mix": {g: clean(v) for g, v in group_mix.items()},
    }
