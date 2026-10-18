"""Shared FastAPI dependencies."""

from __future__ import annotations

from collections.abc import Iterator

from fastapi import Depends, Header, HTTPException
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


def require_header(name: str):
    """Reject a request unless it carries `name: 1`.

    For state-changing endpoints that do not take a JSON body. A browser can
    send a cross-origin text/plain or bodyless POST without asking first (a
    "simple request"); a custom header forces a CORS preflight, which the
    loopback-only CORS policy refuses for any other origin. curl and the
    frontend send the header; a hostile web page cannot.
    """

    def check(value: str | None = Header(default=None, alias=name)) -> None:
        if value != "1":
            raise HTTPException(status_code=403, detail=f"Missing the {name}: 1 header.")

    return Depends(check)


SessionDep = Depends(db_session)
ConfigDep = Depends(config_bundle)
SettingsDep = Depends(settings)
PiDep = Depends(pi_analyzer)
