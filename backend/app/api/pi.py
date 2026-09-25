"""Pi bridge status passthrough.

Read-only. The backend never proxies credentials, never asks the bridge for
secrets, and never exposes anything beyond which provider and model the
analyzer is currently using.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from ..services.pi_service import PiBridgeAnalyzer
from .deps import PiDep

router = APIRouter(prefix="/api/pi", tags=["pi"])


@router.get("/status")
async def pi_status(pi: PiBridgeAnalyzer = PiDep) -> dict[str, Any]:
    return await pi.status()


@router.get("/providers")
async def pi_providers(pi: PiBridgeAnalyzer = PiDep) -> dict[str, Any]:
    return {"providers": await pi.providers()}


@router.get("/models")
async def pi_models(pi: PiBridgeAnalyzer = PiDep) -> dict[str, Any]:
    return {"models": await pi.models()}
