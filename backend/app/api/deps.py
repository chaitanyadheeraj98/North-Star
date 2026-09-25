"""Shared FastAPI dependencies."""

from __future__ import annotations

from collections.abc import Iterator

from fastapi import Depends
from sqlalchemy.orm import Session

from ..config import ConfigBundle, Settings, get_config, get_settings
from ..db.database import get_db
from ..services.analyzer import HeuristicAnalyzer, TaskAnalyzer
from ..services.pi_service import PiBridgeAnalyzer


def db_session() -> Iterator[Session]:
    yield from get_db()


def config_bundle() -> ConfigBundle:
    return get_config()


def settings() -> Settings:
    return get_settings()


def pi_analyzer() -> PiBridgeAnalyzer:
    return PiBridgeAnalyzer(get_settings())


def heuristic_analyzer() -> HeuristicAnalyzer:
    return HeuristicAnalyzer()


def analyzer_for(source: str) -> TaskAnalyzer:
    """Pick an analyzer by name.

    `pi` is the real thing. `heuristic` is the offline fallback, chosen
    explicitly by the caller rather than silently substituted: a user who
    thinks Pi classified their task deserves to know when it did not.
    """
    if source == "heuristic":
        return HeuristicAnalyzer()
    return PiBridgeAnalyzer(get_settings())


SessionDep = Depends(db_session)
ConfigDep = Depends(config_bundle)
SettingsDep = Depends(settings)
PiDep = Depends(pi_analyzer)
