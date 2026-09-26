"""FlowGuard - FastAPI backend + static single-page frontend.

Only the health endpoint exists so far; the data endpoints (spec §7.3) come next.
"""
from __future__ import annotations
import os

from fastapi import FastAPI

DATA_MODE = os.environ.get("DATA_MODE", "snapshot")

app = FastAPI(title="FlowGuard")


@app.get("/api/health")
def health():
    return {"status": "ok", "data_mode": DATA_MODE}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
