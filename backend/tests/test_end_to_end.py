"""End-to-end: task in, receipt back, next recommendation reflects the outcome.

This is the test the whole product exists to pass:

    task -> fingerprint -> route -> handoff -> execute -> receipt -> import ->
    statistics -> a later similar task is routed with that evidence in hand

The Pi bridge is replaced by the offline analyzer so the loop runs without a
subscription, a network, or a running agent.
"""

from __future__ import annotations

import json

import pytest

from app.core.router.engine import RoutingEngine
from app.core.learning.statistics import build_evidence_index
from app.db.models import Execution, ExecutionMetrics, RoutingStatistic
from app.schemas.enums import Effort
from app.services import routing_service, task_service
from app.schemas.task_fingerprint import AnalyzerMetadata

from .fixtures import by_name, receipt, wrap

DATA_INTEGRITY_TASK = (
    "Re-key premium lead versions based only on recruiter identity and preserve "
    "source-email provenance across the persistence layer. Do not change the public "
    "API shape."
)
TRIVIAL_TASK = "Change the Save button label from 'Save' to 'Save changes'."


# --------------------------------------------------------------------------
# The full loop through the HTTP API
# --------------------------------------------------------------------------


def test_full_loop_through_the_api(client):
    # 1. Task in, analyzed and routed in one call.
    created = client.post(
        "/api/tasks", json={"task": DATA_INTEGRITY_TASK, "analyzer": "heuristic"}
    )
    assert created.status_code == 201, created.text
    body = created.json()

    task_id = body["public_task_id"]
    assert task_id.startswith("RT-")
    assert body["fingerprint"]["task_family"] == "backend_data_integrity"

    decision = body["recommendation"]
    assert decision["provider"] and decision["model"] and decision["effort"]
    assert decision["predicted_reliability"] >= decision["required_reliability"]
    assert decision["explanation"]["why"]
    assert decision["explanation"]["why_not_lighter"]
    assert decision["explanation"]["why_not_stronger"]
    assert decision["fallback"] is not None

    # 2. The handoff carries the task id, which is the whole basis of learning.
    handoff = client.get(f"/api/tasks/{task_id}/handoff").json()
    assert f"task_id={task_id}" in handoff["handoff"]
    assert f"recommended_provider={decision['provider']}" in handoff["handoff"]
    assert f"recommended_effort={decision['effort']}" in handoff["handoff"]
    assert handoff["model_id"], "the handoff must name the provider-facing model id"
    assert DATA_INTEGRITY_TASK in handoff["handoff"]

    # 3. The work happens elsewhere; a receipt comes back.
    imported = client.post(
        "/api/results/import",
        json={
            "receipt": wrap(
                receipt(
                    task_id,
                    execution={
                        "provider": decision["provider"],
                        "model": decision["model"],
                        "effort": decision["effort"],
                    },
                )
            )
        },
    )
    assert imported.status_code == 200, imported.text
    result = imported.json()
    assert result["recommendation_followed"] is True
    assert result["status"] == "success"
    assert result["estimated_effective_burn"] > 0
    assert result["usage_source"] == "unavailable"
    assert "First recorded execution" in result["learning_summary"]

    # 4. History shows recommended against actual.
    rows = client.get("/api/history").json()["rows"]
    assert len(rows) == 1
    assert rows[0]["public_task_id"] == task_id
    assert rows[0]["outcome"] == "success"
    assert rows[0]["recommendation_followed"] is True
    assert rows[0]["recommended_display"]
    assert rows[0]["actual_display"]

    # 5. Analytics reflects it.
    analytics = client.get("/api/analytics").json()
    assert analytics["totals"]["executions"] == 1
    assert analytics["totals"]["successes"] == 1
    assert analytics["by_configuration"][0]["attempts"] == 1

    # 6. A later similar task carries the evidence.
    second = client.post(
        "/api/tasks", json={"task": DATA_INTEGRITY_TASK, "analyzer": "heuristic"}
    ).json()
    winner = next(
        s
        for s in second["recommendation"]["evaluated"]
        if s["configuration"]["provider"] == decision["provider"]
        and s["configuration"]["model"] == decision["model"]
        and s["configuration"]["effort"] == decision["effort"]
    )
    assert winner["evidence_weight"] == pytest.approx(1.0)
    assert winner["predicted_reliability"] > winner["prior_reliability"], (
        "a recorded success must raise the estimate for that configuration"
    )


# --------------------------------------------------------------------------
# Learning in both directions
# --------------------------------------------------------------------------


def _record(client, task_text, provider, model, effort, **receipt_overrides):
    """Route a task, then import a receipt for a chosen configuration."""
    created = client.post(
        "/api/tasks", json={"task": task_text, "analyzer": "heuristic"}
    ).json()
    task_id = created["public_task_id"]
    payload = receipt(
        task_id,
        execution={"provider": provider, "model": model, "effort": effort},
        **receipt_overrides,
    )
    response = client.post("/api/results/import", json={"receipt": wrap(payload)})
    assert response.status_code == 200, response.text
    return created, response.json()


def test_repeated_failure_pushes_the_estimate_down(client, config):
    """Underpowering must become visible in the numbers."""
    for _ in range(8):
        _record(
            client,
            DATA_INTEGRITY_TASK,
            "codex",
            "terra",
            "medium",
            outcome={
                "status": "failed",
                "implementation_complete": False,
                "first_pass_success": False,
                "tests_passed": False,
                "build_passed": True,
            },
            work={"debug_cycles": 4, "major_replans": 1, "user_corrections": 2},
            agent_assessment={"recommendation_fit": "underpowered", "actual_complexity": "high"},
        )

    from app.db.database import get_session_factory

    session = get_session_factory()()
    try:
        index = build_evidence_index(
            session, config.policy, task_family="backend_data_integrity"
        )
        evidence = index.lookup("backend_data_integrity", "codex", "terra", Effort.MEDIUM)
        assert evidence.weighted_attempts == pytest.approx(8.0)
        assert evidence.weighted_successes == pytest.approx(0.0)

        engine = RoutingEngine(config.registry, config.policy, config.families)
        decision = engine.route(
            by_name("deduplication identity semantics").fingerprint, index.as_lookup()
        )
        punished = next(
            s
            for s in decision.evaluated
            if s.configuration.model == "terra" and s.configuration.effort is Effort.MEDIUM
        )
        assert punished.history_shift < -0.01
        assert punished.predicted_reliability < punished.prior_reliability
    finally:
        session.close()


def test_repeated_success_on_a_cheaper_option_pushes_it_up(client, config):
    """Overpowering must be discoverable too, not only underpowering."""
    for _ in range(10):
        _record(client, DATA_INTEGRITY_TASK, "codex", "terra", "high")

    from app.db.database import get_session_factory

    session = get_session_factory()()
    try:
        index = build_evidence_index(
            session, config.policy, task_family="backend_data_integrity"
        )
        engine = RoutingEngine(config.registry, config.policy, config.families)
        decision = engine.route(
            by_name("deduplication identity semantics").fingerprint, index.as_lookup()
        )
        rewarded = next(
            s
            for s in decision.evaluated
            if s.configuration.model == "terra" and s.configuration.effort is Effort.HIGH
        )
        assert rewarded.history_shift > 0.01
        assert rewarded.evidence_weight == pytest.approx(10.0)
    finally:
        session.close()


def test_analytics_surfaces_an_underpowered_pattern(client):
    for _ in range(6):
        _record(
            client,
            DATA_INTEGRITY_TASK,
            "codex",
            "luna",
            "medium",
            outcome={
                "status": "failed",
                "implementation_complete": False,
                "first_pass_success": False,
                "tests_passed": False,
                "build_passed": False,
            },
            work={"debug_cycles": 3},
        )

    findings = client.get("/api/analytics").json()["findings"]
    underpowered = [f for f in findings if f["kind"] == "underpowered"]
    assert underpowered, f"expected an underpowered finding, got {findings}"
    assert "luna" in underpowered[0]["configuration"].lower()


# --------------------------------------------------------------------------
# Deviation and re-import
# --------------------------------------------------------------------------


def test_a_receipt_for_a_different_configuration_is_kept(client):
    """Ignoring the recommendation is evidence, not an error."""
    created = client.post(
        "/api/tasks", json={"task": DATA_INTEGRITY_TASK, "analyzer": "heuristic"}
    ).json()
    task_id = created["public_task_id"]
    recommended = created["recommendation"]

    # Deliberately run something else.
    other = ("codex", "luna", "low")
    assert (recommended["provider"], recommended["model"], recommended["effort"]) != other

    imported = client.post(
        "/api/results/import",
        json={
            "receipt": wrap(
                receipt(
                    task_id,
                    execution={"provider": other[0], "model": other[1], "effort": other[2]},
                )
            )
        },
    ).json()

    assert imported["recommendation_followed"] is False
    assert any("deviation" in warning for warning in imported["warnings"])
    assert imported["actual"] == "codex/luna/low"


def test_reimporting_replaces_rather_than_double_counts(client):
    created = client.post(
        "/api/tasks", json={"task": TRIVIAL_TASK, "analyzer": "heuristic"}
    ).json()
    task_id = created["public_task_id"]
    body = wrap(receipt(task_id, execution={"provider": "codex", "model": "luna", "effort": "medium"}))

    first = client.post("/api/results/import", json={"receipt": body}).json()
    assert first["replaced_previous"] is False

    second = client.post("/api/results/import", json={"receipt": body}).json()
    assert second["replaced_previous"] is True

    analytics = client.get("/api/analytics").json()
    assert analytics["totals"]["executions"] == 1, "a re-import must not be counted twice"


def test_a_receipt_for_an_unknown_task_is_refused(client):
    response = client.post(
        "/api/results/import", json={"receipt": wrap(receipt("RT-999999"))}
    )
    assert response.status_code == 422
    assert "RT-999999" in response.json()["detail"]


def test_an_escalation_is_recorded_and_costed(client):
    created = client.post(
        "/api/tasks", json={"task": DATA_INTEGRITY_TASK, "analyzer": "heuristic"}
    ).json()
    task_id = created["public_task_id"]

    imported = client.post(
        "/api/results/import",
        json={
            "receipt": wrap(
                receipt(
                    task_id,
                    execution={"provider": "codex", "model": "terra", "effort": "medium"},
                    outcome={"status": "success", "first_pass_success": False},
                    work={"debug_cycles": 3},
                    escalation={
                        "occurred": True,
                        "from_provider": "codex",
                        "from_model": "terra",
                        "from_effort": "medium",
                        "to_provider": "codex",
                        "to_model": "sol",
                        "to_effort": "high",
                        "reason": "terra kept reintroducing the same defect",
                    },
                )
            )
        },
    ).json()

    assert imported["escalated"] is True
    assert imported["debug_cycles"] == 3

    escalated_burn = imported["estimated_effective_burn"]
    clean = client.post(
        "/api/tasks", json={"task": DATA_INTEGRITY_TASK, "analyzer": "heuristic"}
    ).json()
    clean_import = client.post(
        "/api/results/import",
        json={
            "receipt": wrap(
                receipt(
                    clean["public_task_id"],
                    execution={"provider": "codex", "model": "terra", "effort": "medium"},
                )
            )
        },
    ).json()
    assert escalated_burn > clean_import["estimated_effective_burn"], (
        "an execution that needed three debug rounds and an escalation must cost "
        "more than a clean one on the same configuration"
    )


def test_measured_usage_is_stored_separately_from_the_estimate(client):
    created = client.post(
        "/api/tasks", json={"task": TRIVIAL_TASK, "analyzer": "heuristic"}
    ).json()
    client.post(
        "/api/results/import",
        json={
            "receipt": wrap(
                receipt(
                    created["public_task_id"],
                    execution={"provider": "codex", "model": "luna", "effort": "medium"},
                    usage={
                        "source": "provider_reported",
                        "input_tokens": 100_000,
                        "output_tokens": 20_000,
                        "reasoning_tokens": 15_000,
                    },
                )
            )
        },
    )

    from app.db.database import get_session_factory

    session = get_session_factory()()
    try:
        metrics = session.query(ExecutionMetrics).one()
        assert metrics.measured_output_tokens == 20_000
        assert metrics.usage_source == "provider_reported"
        # The estimate is ours and is kept apart from the measurement.
        assert metrics.estimated_effective_burn is not None
        assert metrics.estimated_effective_burn != 20_000
    finally:
        session.close()


# --------------------------------------------------------------------------
# Service-level loop, without HTTP
# --------------------------------------------------------------------------


def test_service_layer_loop(session, config):
    """The same loop through the services, to prove the seam is not HTTP-shaped."""
    task = task_service.create_task(session, DATA_INTEGRITY_TASK)
    fingerprint = by_name("deduplication identity semantics").fingerprint

    routing_service.store_fingerprint(
        session,
        task,
        fingerprint,
        AnalyzerMetadata(provider="test", model="fixture", confidence=0.9),
        "mock",
    )
    decision = routing_service.route_fingerprint(session, config, fingerprint)
    row = routing_service.store_decision(session, config, task, decision)
    session.commit()

    assert task.public_task_id == "RT-000001"
    assert row.provider == decision.provider

    from app.services.receipt_service import import_receipt

    payload = receipt(
        task.public_task_id,
        execution={
            "provider": decision.provider,
            "model": decision.model,
            "effort": decision.effort.value,
        },
    )
    result = import_receipt(session, config, wrap(payload))
    session.commit()

    assert result.recommendation_followed is True
    assert result.task_family == "backend_data_integrity"
    assert session.query(Execution).count() == 1
    assert session.query(RoutingStatistic).one().attempts == 1

    reloaded = routing_service.load_decision(row)
    assert reloaded.provider == decision.provider
    assert reloaded.explanation.why == decision.explanation.why


def test_task_ids_are_sequential_and_unique(client):
    ids = [
        client.post("/api/tasks", json={"task": f"Task number {n}", "analyze": False}).json()[
            "public_task_id"
        ]
        for n in range(5)
    ]
    assert ids == ["RT-000001", "RT-000002", "RT-000003", "RT-000004", "RT-000005"]
    assert len(set(ids)) == 5


def test_a_stored_decision_survives_a_round_trip(client):
    created = client.post(
        "/api/tasks", json={"task": DATA_INTEGRITY_TASK, "analyzer": "heuristic"}
    ).json()
    task_id = created["public_task_id"]

    stored = client.get(f"/api/tasks/{task_id}/recommendation").json()
    assert stored["provider"] == created["recommendation"]["provider"]
    assert stored["explanation"]["why"] == created["recommendation"]["explanation"]["why"]
    assert len(stored["evaluated"]) == len(created["recommendation"]["evaluated"])

    fetched = client.get(f"/api/tasks/{task_id}").json()
    assert fetched["handoff"] and json.dumps(fetched["fingerprint"])
