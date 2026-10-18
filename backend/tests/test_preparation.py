"""Plan / spec-first advice, the automatic receipt import, and Pi reconnect."""

from __future__ import annotations

import asyncio
import json
import types

import pytest

from app.config import get_settings
from app.db.models import Execution
from app.schemas.task_fingerprint import AnalyzerMetadata, FingerprintEnvelope
from app.services import pi_service
from app.services.analyzer import HeuristicAnalyzer
from app.services.preparation import recommend_preparation

from .fixtures import fp, receipt, wrap


def test_small_clear_task_runs_directly():
    prep = recommend_preparation(fp(complexity=0.1, scope="single_line", estimated_files=1), "RT-000001")
    assert prep.mode == "direct"
    assert prep.plan_path is None and prep.spec_path is None


def test_clear_but_complex_task_gets_a_plan_first():
    prep = recommend_preparation(
        fp(complexity=0.85, requirements_clarity=0.9, ambiguity=0.1, scope="multi_service"),
        "RT-000002",
    )
    assert prep.mode == "plan"
    assert prep.plan_path == "docs/plans/RT-000002-plan.md"
    assert prep.spec_path is None


def test_underspecified_substantial_task_gets_spec_and_plan():
    prep = recommend_preparation(
        fp(complexity=0.6, requirements_clarity=0.3, ambiguity=0.7), "RT-000003"
    )
    assert prep.mode == "spec_and_plan"
    assert prep.spec_path and prep.plan_path
    assert any("clarity" in reason for reason in prep.reasons)


def test_vague_but_trivial_task_is_not_worth_a_spec():
    prep = recommend_preparation(
        fp(complexity=0.1, requirements_clarity=0.3, ambiguity=0.6, scope="single_line"),
        "RT-000004",
    )
    assert prep.mode == "direct"


# --- Preparation through the real API -------------------------------------


@pytest.fixture
def fixed_fingerprint(monkeypatch):
    """Make the offline analyzer return a chosen fingerprint, to drive the real API."""

    def use(**overrides):
        async def analyze(self, task, overrides_=None):  # noqa: ARG001
            return FingerprintEnvelope(
                fingerprint=fp(**overrides),
                analyzer=AnalyzerMetadata(provider="heuristic", model="test", confidence=0.9),
            )

        monkeypatch.setattr(HeuristicAnalyzer, "analyze", analyze)

    return use


@pytest.mark.parametrize(
    ("overrides", "mode", "has_spec"),
    [
        ({"complexity": 0.1, "scope": "single_line", "estimated_files": 1}, "direct", False),
        ({"complexity": 0.85, "scope": "multi_service", "requirements_clarity": 0.9}, "plan", False),
        ({"complexity": 0.6, "requirements_clarity": 0.3, "ambiguity": 0.7}, "spec_and_plan", True),
    ],
)
def test_every_handoff_carries_the_preparation(client, fixed_fingerprint, overrides, mode, has_spec):
    fixed_fingerprint(**overrides)
    created = client.post("/api/tasks", json={"task": "Some task", "analyzer": "heuristic"}).json()
    task_id = created["public_task_id"]
    assert created["preparation"]["mode"] == mode

    # The same handoff from all three places it is served.
    fetched = client.get(f"/api/tasks/{task_id}").json()
    single = client.get(f"/api/tasks/{task_id}/handoff", params={"provider": "codex"}).json()
    assert fetched["preparation"] == created["preparation"]
    assert single["handoff"] == created["handoffs"]["codex"]
    for provider in ("codex", "claude"):
        handoff = created["handoffs"][provider]
        assert handoff == fetched["handoffs"][provider]
        assert f"preparation={mode}" in handoff
        assert "submits it to the router itself" in handoff
        if mode == "direct":
            assert "PREPARATION" not in handoff
        else:
            assert handoff.index("PREPARATION") < handoff.index("TASK")
            assert f"docs/plans/{task_id}-plan.md" in handoff
        assert (f"docs/specs/{task_id}-spec.md" in handoff) is has_spec


def test_unanalyzed_task_has_no_preparation(client):
    created = client.post("/api/tasks", json={"task": "Later", "analyze": False}).json()
    assert created["preparation"] is None


# --- Receipt import --------------------------------------------------------


def _valid_receipt_body(client) -> tuple[str, str]:
    created = client.post(
        "/api/tasks", json={"task": "Rename a variable in utils.py", "analyzer": "heuristic"}
    ).json()
    decision = created["recommendation"]["codex"]
    body = wrap(
        receipt(
            created["public_task_id"],
            execution={"provider": "codex", "model": decision["model"], "effort": decision["effort"]},
        )
    )
    return created["public_task_id"], body


def test_receipt_can_be_posted_as_plain_text(client):
    task_id, body = _valid_receipt_body(client)
    response = client.post(
        "/api/results/import-text",
        content=body.encode("utf-8"),
        headers={"Content-Type": "text/plain; charset=utf-8", "X-NorthStar-Receipt": "1"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["task_id"] == task_id


def test_plain_text_receipt_without_the_header_is_refused(client, session):
    """A cross-origin page can send text/plain with no preflight; it cannot add the header."""
    _, body = _valid_receipt_body(client)
    response = client.post(
        "/api/results/import-text",
        content=body.encode("utf-8"),
        headers={"Content-Type": "text/plain", "Origin": "http://evil.example"},
    )
    assert response.status_code == 403
    assert session.query(Execution).count() == 0, "a refused receipt must not be recorded"


def test_a_foreign_origin_cannot_preflight_the_header(client):
    response = client.options(
        "/api/results/import-text",
        headers={
            "Origin": "http://evil.example",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "x-northstar-receipt",
        },
    )
    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers


def test_the_json_import_still_refuses_a_text_plain_body(client):
    _, body = _valid_receipt_body(client)
    response = client.post(
        "/api/results/import",
        content=json.dumps({"receipt": body}).encode(),
        headers={"Content-Type": "text/plain"},
    )
    assert response.status_code == 422


def test_empty_plain_text_receipt_is_rejected(client):
    response = client.post(
        "/api/results/import-text", content=b"", headers={"X-NorthStar-Receipt": "1"}
    )
    assert response.status_code == 422


# --- Reconnect -------------------------------------------------------------


@pytest.fixture
def pi(app_env, monkeypatch):
    """A bridge client with fake status/reload, a fake clock, and fresh lock state."""
    monkeypatch.setattr(pi_service, "_reconnect_lock", asyncio.Lock())
    monkeypatch.setattr(pi_service, "_last_reconnect", None)
    clock = {"now": 0.0}
    real_sleep = asyncio.sleep

    async def fake_sleep(seconds):
        clock["now"] += seconds
        await real_sleep(0)  # still yield, so concurrent callers interleave

    monkeypatch.setattr(
        pi_service, "time", types.SimpleNamespace(monotonic=lambda: clock["now"], time=lambda: 0.0)
    )
    monkeypatch.setattr(pi_service, "asyncio", types.SimpleNamespace(sleep=fake_sleep))

    analyzer = pi_service.PiBridgeAnalyzer(get_settings())
    analyzer.clock = clock
    analyzer.flag = get_settings().data_dir / pi_service.RESTART_REQUEST
    analyzer.reloads = 0

    async def reload():
        analyzer.reloads += 1
        return {"status": "ok", "available": True, "started_at": "old"}

    monkeypatch.setattr(analyzer, "_reload", reload)
    yield analyzer
    analyzer.flag.unlink(missing_ok=True)


def _watchdog(pi, monkeypatch):
    """Sleeps advance the clock and, like the real watchdog, consume the request."""

    async def sleep(seconds):
        pi.clock["now"] += seconds
        pi.flag.unlink(missing_ok=True)

    monkeypatch.setattr(pi_service, "asyncio", types.SimpleNamespace(sleep=sleep))


def _statuses(pi, monkeypatch, *responses):
    """status() returns each response in turn, then repeats the last."""
    queue = list(responses)

    async def status():
        return queue.pop(0) if len(queue) > 1 else queue[0]

    monkeypatch.setattr(pi, "status", status)


def test_reconnect_without_a_watchdog_falls_back_after_the_pickup_window(pi, monkeypatch):
    _statuses(pi, monkeypatch, {"status": "ok", "available": True, "started_at": "old"})
    result = asyncio.run(pi.reconnect(wait_seconds=60))
    assert result["reconnect"] == "reloaded" and pi.reloads == 1
    # Regression: this used to sit out the whole 60s restart timeout first.
    assert pi.clock["now"] <= pi_service.PICKUP_SECONDS + 1
    assert not pi.flag.exists()


def test_reconnect_reports_a_restart_once_the_watchdog_acts(pi, monkeypatch):
    _watchdog(pi, monkeypatch)
    _statuses(
        pi,
        monkeypatch,
        {"status": "ok", "available": True, "started_at": "old"},  # before the restart
        {"status": "ok", "available": True, "started_at": "new"},
    )
    result = asyncio.run(pi.reconnect())
    assert result["reconnect"] == "restarted" and result["started_at"] == "new"
    assert pi.reloads == 0


def test_a_restarted_bridge_that_reports_a_problem_returns_at_once(pi, monkeypatch):
    """Not logged in after a restart is an answer, not a reason to wait 60s."""
    _watchdog(pi, monkeypatch)
    _statuses(
        pi,
        monkeypatch,
        {"status": "unavailable", "available": False, "started_at": None},
        {"status": "degraded", "available": False, "started_at": "new", "note": "log in"},
    )
    result = asyncio.run(pi.reconnect(wait_seconds=60))
    assert result["reconnect"] == "restarted" and result["note"] == "log in"
    assert pi.clock["now"] < 5


def test_a_restart_that_never_comes_back_times_out(pi, monkeypatch):
    _watchdog(pi, monkeypatch)
    _statuses(pi, monkeypatch, {"status": "unavailable", "available": False, "started_at": None})
    result = asyncio.run(pi.reconnect(wait_seconds=20))
    assert result["reconnect"] == "failed" and "bridge.err.log" in result["note"]
    assert 20 <= pi.clock["now"] <= 23


def test_concurrent_reconnects_share_one_restart(pi, monkeypatch):
    """A second request landing after the watchdog restarted would kill the new bridge."""
    writes = {"n": 0}
    path_type = type(pi.flag)
    real_write = path_type.write_text

    def counting_write(self, *args, **kwargs):
        writes["n"] += 1
        return real_write(self, *args, **kwargs)

    monkeypatch.setattr(path_type, "write_text", counting_write)
    _statuses(pi, monkeypatch, {"status": "ok", "available": True, "started_at": "old"})

    async def both():
        return await asyncio.gather(pi.reconnect(), pi.reconnect())

    first, second = asyncio.run(both())
    assert writes["n"] == 1 and pi.reloads == 1
    assert first == second


def test_reconnect_endpoint_requires_its_header(client, monkeypatch):
    async def fake(self, *args, **kwargs):  # noqa: ARG001
        return {"status": "ok", "available": True, "reconnect": "restarted"}

    monkeypatch.setattr(pi_service.PiBridgeAnalyzer, "reconnect", fake)
    assert client.post("/api/pi/reconnect").status_code == 403
    ok = client.post("/api/pi/reconnect", headers={"X-NorthStar-Reconnect": "1"})
    assert ok.status_code == 200 and ok.json()["reconnect"] == "restarted"
