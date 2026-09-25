"""FastAPI application entry point."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .api import analytics, history, models, pi, results, settings, tasks
from .config import ConfigError, get_config, get_settings
from .db.database import init_db

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)-8s %(name)s: %(message)s"
)
log = logging.getLogger("adaptive-router")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Validate configuration and prepare the database before serving.

    Configuration problems are fatal here on purpose. A router running on a
    half-parsed policy would still answer every request, just with silently
    wrong recommendations, which is the worst possible failure mode for this
    particular application.
    """
    app_settings = get_settings()
    config = get_config()
    init_db()
    log.info(
        "Adaptive LLM Router %s ready | router=%s registry=%s | %d routable configurations",
        app_settings.app_version,
        config.policy.router_version,
        config.registry.registry_version,
        len(config.registry.enabled_configurations()),
    )
    log.info("Pi bridge expected at %s", app_settings.pi_bridge_url)
    yield


app = FastAPI(
    title="Adaptive LLM Router",
    version=get_settings().app_version,
    summary="Use the right amount of AI for the task.",
    description=(
        "Local-first router that independently recommends a model and reasoning effort per provider for a "
        "coding task, then learns from what actually happened. Pi classifies; this "
        "backend decides and learns."
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=list(get_settings().cors_origins),
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(tasks.router)
app.include_router(results.router)
app.include_router(history.router)
app.include_router(analytics.router)
app.include_router(models.router)
app.include_router(pi.router)
app.include_router(settings.router)


@app.exception_handler(ConfigError)
async def _config_error_handler(_request: Request, exc: ConfigError) -> JSONResponse:
    return JSONResponse(status_code=500, content={"detail": f"Configuration error: {exc}"})


@app.get("/api/health", tags=["health"])
def health() -> dict[str, Any]:
    config = get_config()
    return {
        "status": "ok",
        "version": get_settings().app_version,
        "router_version": config.policy.router_version,
        "registry_version": config.registry.registry_version,
        "configurations": len(config.registry.enabled_configurations()),
    }
