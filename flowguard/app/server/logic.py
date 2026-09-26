"""Shape gold-table rows into the payloads the frontend renders.

Live (SQL warehouse) and snapshot (bundled JSON) modes hand the same raw rows to these functions,
so both produce identical payloads. Thresholds mirror flowguard/pipeline/fg_settings.py.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List

SLOT_MINUTES = 30
HORIZONS = [1, 2, 3, 4]
CORRIDORS = ["NS_WEST", "NS_EAST", "EAST", "SOUTH", "BC_OTHER", "OUT_OF_REGION"]
CORRIDOR_LABELS = {
    "NS_WEST": "West Vancouver", "NS_EAST": "North Vancouver", "EAST": "East via R2",
    "SOUTH": "South via Lions Gate", "BC_OTHER": "Rest of BC", "OUT_OF_REGION": "Out of province",
}
GROUPS = ["EASTBOUND", "DOWNTOWN", "WEST_VAN_LOCAL"]
GROUP_LABELS = {"EASTBOUND": "Eastbound", "DOWNTOWN": "Downtown", "WEST_VAN_LOCAL": "West Van local"}
GROUP_CORRIDORS = {"EASTBOUND": ["EAST", "NS_EAST"], "DOWNTOWN": ["SOUTH"], "WEST_VAN_LOCAL": ["NS_WEST"]}
GROUP_ROUTES = {"EASTBOUND": "R2 · 255", "DOWNTOWN": "250 · 253 · 254 · 257 · 44", "WEST_VAN_LOCAL": "250–257 local"}
PRESSURE_LEVELS = [[1.3, "Normal"], [1.8, "Elevated"], [2.5, "High"], [None, "Severe"]]
READINESS_LEVELS = [[1.2, "Prepared"], [1.7, "Watch"], [2.3, "Strained"], [None, "Critical"]]
MIN_DEMAND_SHARE = 0.5
SERVICE_LABELS = {"WEEKDAY": "weekday", "SATURDAY": "Saturday", "SUNDAY_HOLIDAY": "Sunday/holiday"}

# Curated demo days shown first in the day picker (date, label).
PRESETS = [
    ("2025-12-26", "Boxing Day"),
    ("2025-12-20", "Pre-Christmas Saturday"),
    ("2026-04-25", "Normal Saturday"),
    ("2026-03-20", "Normal Friday"),
    ("2026-08-15", "August Saturday (holdout)"),
]

SLOT_COLS = [
    "slot_ts", "slot_of_day", "pressure", "pressure_level", "signature", "departures", "baseline_departures",
    "regional_share", "baseline_regional_share", "stay_ratio", "visitor_ratio",
] + [f"share_{c.lower()}" for c in CORRIDORS] + [f"baseline_share_{c.lower()}" for c in CORRIDORS]

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


def days_payload(day_rows: List[Dict[str, Any]], available: set | None = None) -> Dict[str, Any]:
    by_date = {str(r["date"])[:10]: r for r in day_rows}
    labels = dict(PRESETS)

    def item(d, r):
        return {
            "date": d, "label": labels.get(d) or r.get("label") or "", "day_type": r["day_type"],
            "surge_ratio": clean(float(r["surge_ratio"])), "is_surge": bool(r["is_surge"]),
            "peak_pressure": clean(float(r["peak_pressure"])) if r.get("peak_pressure") is not None else None,
        }

    keep = (lambda d: d in available) if available is not None else (lambda d: True)
    presets = [item(d, by_date[d]) for d, _ in PRESETS if d in by_date and keep(d)]
    rest = [item(d, r) for d, r in sorted(by_date.items()) if keep(d)]
    return {"presets": presets, "days": rest}


def model_payload(kernel: List[Dict[str, Any]], backtest: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "kernel": [{"k": int(r["k"]), "cum_p": clean(float(r["cum_p"]))} for r in sorted(kernel, key=lambda r: int(r["k"]))],
        "backtest": [{k: clean(v) if not isinstance(v, str) else v for k, v in r.items()} for r in backtest],
    }


def day_payload(day: Dict[str, Any], slots: List[Dict[str, Any]], timeline: List[Dict[str, Any]],
                forecast_all: List[Dict[str, Any]], corridor_mix: List[Dict[str, Any]]) -> Dict[str, Any]:
    slots = sorted(slots, key=lambda r: int(r["slot_of_day"]))
    n = len(slots)

    slot_out = []
    for r in slots:
        slot_out.append({
            "t": hhmm(r["slot_ts"]),
            "pressure": clean(r["pressure"]), "level": r["pressure_level"], "signature": r["signature"],
            "regional": clean(r["regional_share"]), "regional_base": clean(r["baseline_regional_share"]),
            "stay": clean(r["stay_ratio"]), "visitor": clean(r["visitor_ratio"]),
            "shares": {c: clean(r[f"share_{c.lower()}"]) for c in CORRIDORS},
            "base_shares": {c: clean(r[f"baseline_share_{c.lower()}"]) for c in CORRIDORS},
        })

    usual = [clean(float(r["baseline_departures"])) for r in slots]
    fc = [[None] * len(HORIZONS) for _ in range(n)]
    for r in forecast_all:
        i = int(hhmm(r["origin_slot_ts"])[:2]) * 2 + int(hhmm(r["origin_slot_ts"])[3:]) // SLOT_MINUTES
        fc[i][int(r["horizon"]) - 1] = clean(float(r["dep_hat"]))
    exit_wave = {
        "normal_peak": max(u for u in usual if u is not None),
        "actual": [int(r["departures"]) for r in slots],
        "usual": usual,
        "forecast": fc,
    }

    groups = {}
    for g in GROUPS:
        rows = sorted((r for r in timeline if r["route_group"] == g), key=lambda r: int(r["slot_of_day"]))
        first = rows[0] if rows else {}
        groups[g] = {
            "label": GROUP_LABELS[g], "routes": GROUP_ROUTES[g], "corridors": GROUP_CORRIDORS[g],
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

    mix = {r["corridor"]: float(r["departures"]) for r in corridor_mix}
    total = sum(mix.values()) or 1.0
    group_mix = {g: sum(mix.get(c, 0.0) for c in cs) / total for g, cs in GROUP_CORRIDORS.items()}

    d = str(day["date"])[:10]
    return {
        "date": d,
        "label": dict(PRESETS).get(d) or day.get("label") or "",
        "day_type": day["day_type"],
        "service_day_type": day["service_day_type"],
        "service_label": SERVICE_LABELS.get(day["service_day_type"], day["service_day_type"]),
        "surge_ratio": clean(float(day["surge_ratio"])),
        "is_surge": bool(day["is_surge"]),
        "thresholds": {"pressure": PRESSURE_LEVELS, "readiness": READINESS_LEVELS,
                       "min_demand_share": MIN_DEMAND_SHARE, "slot_minutes": SLOT_MINUTES},
        "corridors": [{"key": c, "label": CORRIDOR_LABELS[c]} for c in CORRIDORS],
        "slots": slot_out,
        "exit": exit_wave,
        "groups": groups,
        "group_order": GROUPS,
        "group_mix": {g: clean(v) for g, v in group_mix.items()},
    }
