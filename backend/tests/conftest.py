"""Shared fixtures.

Every test gets its own SQLite file in a temp directory. Real config is used
rather than a fixture registry, because a change to models.yaml or routing.yaml
that breaks the routing sanity expectations SHOULD break the test suite - that
is the point of those tests.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session", autouse=True)
def _config_dir() -> None:
    os.environ.setdefault("CONFIG_DIR", str(BACKEND_ROOT / "config"))


@pytest.fixture
def db_path() -> Iterator[Path]:
    """A throwaway database directory.

    `tempfile.mkdtemp` rather than pytest's `tmp_path`: the shared
    `pytest-of-<user>` root can be left unwritable by an interrupted or
    differently-privileged run, and a permissions problem in a scratch
    directory should not be able to take down the whole suite.
    """
    directory = Path(tempfile.mkdtemp(prefix="adaptive-router-test-"))
    try:
        yield directory / "router.db"
    finally:
        shutil.rmtree(directory, ignore_errors=True)


@pytest.fixture
def app_env(db_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Point the application at a throwaway database and reset the caches."""
    from app import config as config_module
    from app.db import database

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setenv("CONFIG_DIR", str(BACKEND_ROOT / "config"))
    # Point at a port nothing listens on. The suite must behave identically
    # whether or not a real Pi bridge happens to be running on this machine;
    # tests that want a bridge stub it explicitly (see test_analyzer_config).
    monkeypatch.setenv("PI_BRIDGE_URL", "http://127.0.0.1:1")
    # Keep the "bridge is down" paths fast: the default is tuned for real
    # model calls, and these tests are asserting the connection failure itself.
    monkeypatch.setenv("PI_TIMEOUT_SECONDS", "2")

    config_module.get_settings.cache_clear()
    config_module.get_config.cache_clear()
    database.reset_engine()

    yield

    database.reset_engine()
    config_module.get_settings.cache_clear()
    config_module.get_config.cache_clear()


@pytest.fixture
def config(app_env: None):  # noqa: ARG001 - fixture ordering
    from app.config import get_config

    return get_config()


@pytest.fixture
def registry(config):
    return config.registry


@pytest.fixture
def policy(config):
    return config.policy


@pytest.fixture
def families(config):
    return config.families


@pytest.fixture
def engine(config):
    from app.core.router.engine import RoutingEngine

    return RoutingEngine(config.registry, config.policy, config.families)


@pytest.fixture
def session(app_env: None) -> Iterator:  # noqa: ARG001 - fixture ordering
    from app.db.database import get_session_factory, init_db

    init_db()
    factory = get_session_factory()
    db = factory()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def client(app_env: None) -> Iterator:  # noqa: ARG001 - fixture ordering
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def scratch_dir() -> Iterator[Path]:
    """A throwaway directory, independent of pytest's shared temp root.

    Same reason as `db_path`: the shared `pytest-of-<user>` root can end up
    unwritable, and that should not be able to fail tests that only need a
    scratch folder.
    """
    directory = Path(tempfile.mkdtemp(prefix="adaptive-router-scratch-"))
    try:
        yield directory
    finally:
        shutil.rmtree(directory, ignore_errors=True)
