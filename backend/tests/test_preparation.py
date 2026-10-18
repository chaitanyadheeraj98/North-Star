"""Plan / spec-first advice, the automatic receipt import, and Pi reconnect."""

from __future__ import annotations

import asyncio

from app.config import get_settings
from app.services import pi_service
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


def test_handoff_carries_the_preparation_steps(client):
    task = (
        "Redesign the whole billing system so it supports multiple currencies, refunds, "
        "invoices and proration across every service, with a migration for existing data."
    )
    created = client.post("/api/tasks", json={"task": task, "analyzer": "heuristic"}).json()
    prep = created["preparation"]
    assert prep["mode"] in {"direct", "plan", "spec_and_plan"}

    handoff = created["handoffs"]["codex"]
    assert f"preparation={prep['mode']}" in handoff
    if prep["mode"] != "direct":
        assert "PREPARATION" in handoff and prep["plan_path"] in handoff
        assert handoff.index("PREPARATION") < handoff.index("TASK")
    assert "submits it to the router itself" in handoff


def test_receipt_can_be_posted_as_plain_text(client):
    created = client.post(
        "/api/tasks", json={"task": "Rename a variable in utils.py", "analyzer": "heuristic"}
    ).json()
    decision = created["recommendation"]["codex"]
    body = wrap(
        receipt(
            created["public_task_id"],
            execution={
                "provider": "codex",
                "model": decision["model"],
                "effort": decision["effort"],
            },
        )
    )
    response = client.post(
        "/api/results/import-text",
        content=body.encode("utf-8"),
        headers={"Content-Type": "text/plain; charset=utf-8"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["task_id"] == created["public_task_id"]


def test_empty_plain_text_receipt_is_rejected(client):
    response = client.post("/api/results/import-text", content=b"")
    assert response.status_code == 422


def test_reconnect_falls_back_to_reload_without_a_watchdog(app_env, monkeypatch):
    analyzer = pi_service.PiBridgeAnalyzer(get_settings())

    async def status():
        return {"available": True, "started_at": "same"}

    reloaded = []

    async def reload():
        reloaded.append(True)
        return {"available": True, "started_at": "same"}

    monkeypatch.setattr(analyzer, "status", status)
    monkeypatch.setattr(analyzer, "_reload", reload)

    result = asyncio.run(analyzer.reconnect(wait_seconds=0))
    assert result["reconnect"] == "reloaded" and reloaded
    assert not (get_settings().data_dir / pi_service.RESTART_REQUEST).exists()


def test_reconnect_reports_a_restart_once_the_watchdog_acts(app_env, monkeypatch):
    analyzer = pi_service.PiBridgeAnalyzer(get_settings())
    flag = get_settings().data_dir / pi_service.RESTART_REQUEST
    calls = {"n": 0}

    async def status():
        calls["n"] += 1
        # The first call is the "before" snapshot, taken by the old bridge.
        return {"available": True, "started_at": "old" if calls["n"] == 1 else "new"}

    async def watchdog_acts(_):
        flag.unlink(missing_ok=True)

    monkeypatch.setattr(analyzer, "status", status)
    monkeypatch.setattr(pi_service.asyncio, "sleep", watchdog_acts)

    result = asyncio.run(analyzer.reconnect(wait_seconds=5))
    assert result["reconnect"] == "restarted"
