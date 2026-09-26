"""FlowGuard - FastAPI backend + static single-page frontend (no build step)."""
from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
import json

from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from server import cache, logic
from server.genie import GenieSpaceNotConfigured, ask as genie_ask, ask_stream as genie_ask_stream, space_info as genie_space_info

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("flowguard")

STATIC = Path(__file__).parent / "static"
app = FastAPI(title="FlowGuard")


POI_PATTERN = "^(" + "|".join(logic.POI_BY_KEY) + ")$"


@app.on_event("startup")
def warm_cache():
    """Pre-load day lists, model metrics and every POI's demo days in the background so the demo is instant."""
    def run():
        for poi in logic.POIS:
            k = poi["key"]
            jobs = [("days", lambda k=k: cache.days(k)), ("model", lambda k=k: cache.model(k))] + [
                (d, lambda k=k, d=d: cache.day(k, d)) for d, _ in poi["presets"] + poi.get("outlook_presets", [])]
            for name, fn in jobs:
                try:
                    fn()
                    log.info("warmed %s %s from %s", k, name, cache.last_source["source"])
                except Exception as e:  # noqa: BLE001
                    log.warning("warm-up of %s %s failed: %s", k, name, e)
    threading.Thread(target=run, daemon=True).start()


def _error(status: int, message: str) -> JSONResponse:
    # Never surface a stack trace in the UI — a short, human message only.
    return JSONResponse(status_code=status, content={"error": message})


@app.get("/api/health")
def health():
    return {"status": "ok", "data_mode": cache.DATA_MODE, "last_source": cache.last_source["source"]}


@app.get("/api/pois")
def pois():
    return logic.pois_payload()


@app.get("/api/days")
def days(poi: str = Query(logic.DEFAULT_POI, pattern=POI_PATTERN)):
    try:
        return cache.days(poi)
    except Exception as e:  # noqa: BLE001
        log.exception("days failed")
        return _error(503, f"Day list unavailable ({type(e).__name__}).")


@app.get("/api/day")
def day(date: str = Query(..., pattern=r"^\d{4}-\d{2}-\d{2}$"), poi: str = Query(logic.DEFAULT_POI, pattern=POI_PATTERN)):
    try:
        payload = cache.day(poi, date)
    except KeyError:
        return _error(404, f"No data for {date} in this mode.")
    except Exception as e:  # noqa: BLE001
        log.exception("day failed")
        return _error(503, f"Data for {date} unavailable ({type(e).__name__}).")
    return {**payload, "source": cache.last_source["source"]}


@app.get("/api/model")
def model(poi: str = Query(logic.DEFAULT_POI, pattern=POI_PATTERN)):
    try:
        return cache.model(poi)
    except Exception as e:  # noqa: BLE001
        log.exception("model failed")
        return _error(503, f"Model metrics unavailable ({type(e).__name__}).")


# -------- Genie (stretch) --------
class GenieAsk(BaseModel):
    question: str
    conversation_id: Optional[str] = None


@app.get("/api/genie/info")
def genie_info():
    try:
        return genie_space_info()
    except Exception:  # noqa: BLE001
        return {"configured": False}


@app.post("/api/genie/ask")
def genie_ask_endpoint(body: GenieAsk):
    try:
        return genie_ask(body.question, conversation_id=body.conversation_id)
    except GenieSpaceNotConfigured:
        return {"status": "FAILED", "error": "Ask FlowGuard isn't configured yet.", "text": None,
                "sql": None, "columns": None, "rows": None, "conversation_id": None}
    except Exception as e:  # noqa: BLE001
        log.exception("genie failed")
        return {"status": "FAILED", "error": f"Genie request failed ({type(e).__name__}).", "text": None,
                "sql": None, "columns": None, "rows": None, "conversation_id": body.conversation_id}


@app.post("/api/genie/stream")
def genie_stream_endpoint(body: GenieAsk):
    """Server-sent events: Genie's progress states as they change, then the final answer."""
    def events():
        try:
            for ev in genie_ask_stream(body.question, conversation_id=body.conversation_id):
                yield f"data: {json.dumps(ev, default=str)}\n\n"
        except GenieSpaceNotConfigured:
            yield f"data: {json.dumps({'type': 'result', 'status': 'FAILED', 'error': 'Ask FlowGuard is not configured yet.'})}\n\n"
        except Exception as e:  # noqa: BLE001
            log.exception("genie stream failed")
            err = {"type": "result", "status": "FAILED", "error": f"Genie request failed ({type(e).__name__}).",
                   "conversation_id": body.conversation_id}
            yield f"data: {json.dumps(err)}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# -------- Frontend --------
app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")


@app.get("/")
def root():
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
