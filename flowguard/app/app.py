"""FlowGuard - FastAPI backend + static single-page frontend (no build step)."""
from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from server import cache, logic
from server.genie import GenieSpaceNotConfigured, ask as genie_ask, space_info as genie_space_info

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("flowguard")

STATIC = Path(__file__).parent / "static"
app = FastAPI(title="FlowGuard")


@app.on_event("startup")
def warm_cache():
    """Pre-load the day list, model metrics and demo days in the background so the demo is instant."""
    def run():
        for name, fn in [("days", cache.days), ("model", cache.model)] + [
            (d, lambda d=d: cache.day(d)) for d, _ in logic.PRESETS
        ]:
            try:
                fn()
                log.info("warmed %s from %s", name, cache.last_source["source"])
            except Exception as e:  # noqa: BLE001
                log.warning("warm-up of %s failed: %s", name, e)
    threading.Thread(target=run, daemon=True).start()


def _error(status: int, message: str) -> JSONResponse:
    # Never surface a stack trace in the UI — a short, human message only.
    return JSONResponse(status_code=status, content={"error": message})


@app.get("/api/health")
def health():
    return {"status": "ok", "data_mode": cache.DATA_MODE, "last_source": cache.last_source["source"]}


@app.get("/api/days")
def days():
    try:
        return cache.days()
    except Exception as e:  # noqa: BLE001
        log.exception("days failed")
        return _error(503, f"Day list unavailable ({type(e).__name__}).")


@app.get("/api/day")
def day(date: str = Query(..., pattern=r"^\d{4}-\d{2}-\d{2}$")):
    try:
        payload = cache.day(date)
    except KeyError:
        return _error(404, f"No data for {date} in this mode.")
    except Exception as e:  # noqa: BLE001
        log.exception("day failed")
        return _error(503, f"Data for {date} unavailable ({type(e).__name__}).")
    return {**payload, "source": cache.last_source["source"]}


@app.get("/api/model")
def model():
    try:
        return cache.model()
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


# -------- Frontend --------
app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")


@app.get("/")
def root():
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
