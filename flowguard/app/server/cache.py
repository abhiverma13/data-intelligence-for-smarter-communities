"""Data access with an in-memory cache and an offline snapshot fallback.

DATA_MODE=live      read gold tables through the SQL warehouse; on any failure fall back to the snapshot
DATA_MODE=snapshot  read only the bundled static/data/*.json files (no warehouse needed)
"""
from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any, Callable, Dict

from . import logic

log = logging.getLogger("flowguard")

DATA_MODE = os.environ.get("DATA_MODE", "live").lower()
SNAPSHOT_DIR = Path(__file__).resolve().parents[1] / "static" / "data"

_cache: Dict[str, Any] = {}
_lock = threading.Lock()
last_source = {"source": DATA_MODE}


def _read(name: str) -> Any:
    with open(SNAPSHOT_DIR / name, encoding="utf-8") as f:
        return json.load(f)


def snapshot_dates() -> set:
    d = SNAPSHOT_DIR / "day"
    return {p.stem for p in d.glob("*.json")} if d.exists() else set()


def _get(key: str, live: Callable[[], Any], snapshot: Callable[[], Any]) -> Any:
    with _lock:
        if key in _cache:
            return _cache[key]
    value = None
    if DATA_MODE == "live":
        try:
            value = live()
            last_source["source"] = "live"
        except KeyError:
            raise
        except Exception as e:  # noqa: BLE001 — any warehouse/auth/quota problem → snapshot
            log.warning("live query for %s failed (%s: %s) — using snapshot", key, type(e).__name__, e)
    if value is None:
        value = snapshot()
        last_source["source"] = "snapshot"
    with _lock:
        _cache[key] = value
    return value


def days() -> Dict[str, Any]:
    def live():
        from . import queries
        return logic.days_payload(queries.days())

    def snap():
        return logic.days_payload(_read("days.json"), available=snapshot_dates())

    return _get("days", live, snap)


def model() -> Dict[str, Any]:
    def live():
        from . import queries
        m = queries.model()
        return logic.model_payload(m["kernel"], m["backtest"])

    def snap():
        m = _read("model.json")
        return logic.model_payload(m["kernel"], m["backtest"])

    return _get("model", live, snap)


def day(date: str) -> Dict[str, Any]:
    def build(raw):
        return logic.day_payload(raw["day"], raw["slots"], raw["timeline"], raw["forecast_all"], raw["corridor_mix"])

    def live():
        from . import queries
        return build(queries.day(date))

    def snap():
        if date not in snapshot_dates():
            raise KeyError(date)
        return build(_read(f"day/{date}.json"))

    return _get(f"day:{date}", live, snap)
