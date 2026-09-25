"""Analytics endpoint."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from sqlalchemy.orm import Session

from ..config import ConfigBundle
from ..services.analytics_service import compute_analytics
from .deps import ConfigDep, SessionDep

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


@router.get("")
def get_analytics(
    session: Session = SessionDep, config: ConfigBundle = ConfigDep
) -> dict[str, Any]:
    return compute_analytics(session, config).to_dict()
