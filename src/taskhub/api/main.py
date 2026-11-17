"""FastAPI application (health endpoint for now; REST routes arrive in M8)."""

from __future__ import annotations

from fastapi import FastAPI

app = FastAPI(title="TaskHub API", version="0.1.0")


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    """Liveness probe."""

    return {"status": "ok", "service": "taskhub"}
