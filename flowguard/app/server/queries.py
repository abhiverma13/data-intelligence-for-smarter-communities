"""Live reads of the gold tables through the SQL warehouse. Every query is small and pre-aggregated,
and filtered to one point of interest (poi).

Each function returns raw rows (list of dicts) with timestamps as 'YYYY-MM-DD HH:MM:SS' strings and dates as
'YYYY-MM-DD', which is the same shape the snapshot JSON files store.
"""
from __future__ import annotations

import datetime
import decimal
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List

from .config import table
from .logic import CORRIDOR_COLS, SLOT_COLS, TIMELINE_COLS, is_future
from .sql import run_query

DAY_COLS = "date, day_type, service_day_type, arrivals, surge_ratio, is_surge, peak_pressure, label, night_ratio, night_unusual"


def _norm(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for r in rows:
        o = {}
        for k, v in r.items():
            if isinstance(v, datetime.datetime):
                v = v.strftime("%Y-%m-%d %H:%M:%S")
            elif isinstance(v, datetime.date):
                v = v.isoformat()
            elif isinstance(v, decimal.Decimal):
                v = float(v)
            o[k] = v
        out.append(o)
    return out


def days(poi: str) -> List[Dict[str, Any]]:
    return _norm(run_query(f"SELECT {DAY_COLS} FROM {table('days')} WHERE poi = :p ORDER BY date", {"p": poi}))


def outlook_days(poi: str) -> List[Dict[str, Any]]:
    return _norm(run_query(f"SELECT {DAY_COLS}, method FROM {table('outlook_days')} WHERE poi = :p ORDER BY date", {"p": poi}))


def model(poi: str) -> Dict[str, List[Dict[str, Any]]]:
    p = {"p": poi}
    return {
        "kernel": _norm(run_query(f"SELECT k, p, cum_p FROM {table('egress_kernel')} WHERE poi = :p ORDER BY k", p)),
        "backtest": _norm(run_query(f"SELECT horizon, model, target, r2, mae, n FROM {table('model_backtest')} WHERE poi = :p", p)),
    }


def day(poi: str, date: str) -> Dict[str, Any]:
    p = {"p": poi, "d": date}
    if is_future(date):
        sql = {
            "day": f"SELECT {DAY_COLS}, method, analog_dates FROM {table('outlook_days')} WHERE poi = :p AND date = :d",
            "slots": f"SELECT {', '.join(SLOT_COLS)} FROM {table('outlook_slots')} WHERE poi = :p AND date = :d",
            "timeline": f"SELECT {', '.join(TIMELINE_COLS)} FROM {table('outlook_timeline')} WHERE poi = :p AND date = :d",
            "corridors": f"SELECT {', '.join(CORRIDOR_COLS)} FROM {table('outlook_slot_corridor')} "
                         f"WHERE poi = :p AND CAST(slot_ts AS DATE) = :d",
        }
        return _run_day(sql, p, date, future=True)
    sql = {
        "day": f"SELECT {DAY_COLS} FROM {table('days')} WHERE poi = :p AND date = :d",
        "slots": f"SELECT {', '.join(SLOT_COLS)} FROM {table('slots')} WHERE poi = :p AND date = :d",
        "timeline": f"SELECT {', '.join(TIMELINE_COLS)} FROM {table('flowguard_timeline')} WHERE poi = :p AND date = :d",
        "forecast_all": f"SELECT origin_slot_ts, horizon, dep_hat FROM {table('egress_forecast')} "
                        f"WHERE poi = :p AND corridor = 'ALL' AND CAST(origin_slot_ts AS DATE) = :d",
        "corridors": f"SELECT {', '.join(CORRIDOR_COLS)} FROM {table('slot_corridor')} "
                     f"WHERE poi = :p AND CAST(slot_ts AS DATE) = :d",
    }
    return _run_day(sql, p, date, future=False)


def _run_day(sql: Dict[str, str], p: Dict[str, Any], date: str, future: bool) -> Dict[str, Any]:
    # the small queries run in parallel (each opens its own warehouse session)
    with ThreadPoolExecutor(max_workers=len(sql)) as pool:
        futures = {k: pool.submit(run_query, q, p) for k, q in sql.items()}
        out = {k: _norm(f.result()) for k, f in futures.items()}
    if not out["day"]:
        raise KeyError(date)
    out["day"] = out["day"][0]
    out.setdefault("forecast_all", [])
    out["future"] = future
    return out
