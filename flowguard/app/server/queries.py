"""Live reads of the gold tables through the SQL warehouse. Every query is small and pre-aggregated.

Each function returns raw rows (list of dicts) with timestamps as 'YYYY-MM-DD HH:MM:SS' strings and dates as
'YYYY-MM-DD', which is the same shape the snapshot JSON files store.
"""
from __future__ import annotations

import datetime
import decimal
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List

from .config import table
from .logic import SLOT_COLS, TIMELINE_COLS
from .sql import run_query


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


def days() -> List[Dict[str, Any]]:
    return _norm(run_query(
        f"SELECT date, day_type, service_day_type, arrivals, surge_ratio, is_surge, peak_pressure, label "
        f"FROM {table('pr_days')} ORDER BY date"
    ))


def model() -> Dict[str, List[Dict[str, Any]]]:
    return {
        "kernel": _norm(run_query(f"SELECT k, p, cum_p FROM {table('egress_kernel')} ORDER BY k")),
        "backtest": _norm(run_query(f"SELECT horizon, model, target, r2, mae, n FROM {table('model_backtest')}")),
    }


def day(date: str) -> Dict[str, Any]:
    p = {"d": date}
    sql = {
        "day": f"SELECT date, day_type, service_day_type, arrivals, surge_ratio, is_surge, peak_pressure, label "
               f"FROM {table('pr_days')} WHERE date = :d",
        "slots": f"SELECT {', '.join(SLOT_COLS)} FROM {table('pr_slots')} WHERE date = :d",
        "timeline": f"SELECT {', '.join(TIMELINE_COLS)} FROM {table('flowguard_timeline')} WHERE date = :d",
        "forecast_all": f"SELECT origin_slot_ts, horizon, dep_hat FROM {table('egress_forecast')} "
                        f"WHERE corridor = 'ALL' AND CAST(origin_slot_ts AS DATE) = :d",
        "corridor_mix": f"SELECT corridor, SUM(departures) AS departures FROM {table('pr_slot_corridor')} "
                        f"WHERE CAST(slot_ts AS DATE) = :d GROUP BY corridor",
    }
    # the five small queries run in parallel (each opens its own warehouse session)
    with ThreadPoolExecutor(max_workers=len(sql)) as pool:
        futures = {k: pool.submit(run_query, q, p) for k, q in sql.items()}
        out = {k: _norm(f.result()) for k, f in futures.items()}
    if not out["day"]:
        raise KeyError(date)
    out["day"] = out["day"][0]
    return out
