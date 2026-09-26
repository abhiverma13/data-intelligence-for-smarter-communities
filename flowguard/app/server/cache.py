"""Data access with an in-memory cache and an offline snapshot fallback.

DATA_MODE=live      read gold tables through the SQL warehouse; on any failure fall back to the snapshot
DATA_MODE=snapshot  read only the bundled static/data/<poi>/*.json files (no warehouse needed)
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


def _read(poi: str, name: str) -> Any:
    with open(SNAPSHOT_DIR / poi / name, encoding="utf-8") as f:
        return json.load(f)


def snapshot_dates(poi: str) -> set:
    d = SNAPSHOT_DIR / poi / "day"
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


def days(poi: str) -> Dict[str, Any]:
    P = logic.POI_BY_KEY[poi]

    def live():
        from . import queries
        past = queries.days(poi)
        try:
            future = queries.outlook_days(poi)
        except Exception as e:  # noqa: BLE001 — outlook tables not built yet: past days only
            log.warning("outlook days for %s unavailable (%s)", poi, type(e).__name__)
            future = []
        return logic.days_payload(P, past, future)

    def snap():
        return logic.days_payload(P, _read(poi, "days.json"), _read(poi, "outlook_days.json"), available=snapshot_dates(poi))

    return _get(f"days:{poi}", live, snap)


def model(poi: str) -> Dict[str, Any]:
    def live():
        from . import queries
        m = queries.model(poi)
        return logic.model_payload(m["kernel"], m["backtest"])

    def snap():
        m = _read(poi, "model.json")
        return logic.model_payload(m["kernel"], m["backtest"])

    return _get(f"model:{poi}", live, snap)


def day(poi: str, date: str) -> Dict[str, Any]:
    P = logic.POI_BY_KEY[poi]

    def build(raw):
        return logic.day_payload(P, raw["day"], raw["slots"], raw["timeline"], raw.get("forecast_all", []), raw["corridors"],
                                 future=bool(raw.get("future")))

    def live():
        from . import queries
        return build(queries.day(poi, date))

    def snap():
        if date not in snapshot_dates(poi):
            raise KeyError(date)
        return build(_read(poi, f"day/{date}.json"))

    return _get(f"day:{poi}:{date}", live, snap)
