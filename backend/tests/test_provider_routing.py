from copy import deepcopy

import pytest

from app.core.router.engine import RoutingEngine
from app.core.router.reliability import EMPTY_EVIDENCE, HistoricalEvidence
from app.db.models import RoutingDecisionRow
from app.services.routing_service import load_decision
from .fixtures import EXPECTATIONS, fp, receipt, wrap


@pytest.mark.parametrize("provider", ["codex", "claude"])
def test_provider_routes_are_independent_and_minimum_sufficient(config, provider):
    engine = RoutingEngine(config.registry, config.policy, config.families)
    other = "claude" if provider == "codex" else "codex"
    isolated = deepcopy(config.registry)
    isolated.providers[other].enabled = False
    isolated_engine = RoutingEngine(isolated, config.policy, config.families)
    for case in EXPECTATIONS:
        decision = engine.route(case.fingerprint).for_provider(provider)
        assert decision == isolated_engine.route(case.fingerprint).for_provider(provider)
        assert {s.configuration.provider for s in decision.evaluated} == {provider}
        assert decision.fallback and decision.fallback.provider == provider
        assert decision.fallback != decision.configuration
        eligible = [s for s in decision.evaluated if s.eligible]
        if eligible:
            assert decision.configuration == eligible[0].configuration
        else:
            assert not decision.threshold_met
            assert decision.predicted_reliability == max(s.predicted_reliability for s in decision.evaluated)


def test_history_and_burn_changes_cannot_leak_across_providers(config):
    engine = RoutingEngine(config.registry, config.policy, config.families)
    before = engine.route(fp())
    changed = deepcopy(config.registry)
    changed.providers["codex"].burn_weight = 500
    def evidence(_family, provider, _model, _effort):
        return HistoricalEvidence(weighted_attempts=100, weighted_successes=0) if provider == "codex" else EMPTY_EVIDENCE
    after = RoutingEngine(changed, config.policy, config.families).route(fp(), evidence)
    assert before.claude == after.claude
    assert before.codex != after.codex


def test_disabled_provider_and_single_configuration_are_explicit(config):
    registry = deepcopy(config.registry)
    registry.providers["claude"].enabled = False
    for name, model in registry.providers["codex"].models.items():
        model.enabled = name == "luna"
    registry.providers["codex"].models["luna"].supported_efforts = registry.allowed_efforts("codex", "luna")[:1]
    result = RoutingEngine(registry, config.policy, config.families).route(fp())
    assert result.claude is None and result.unavailable["claude"]
    assert result.codex.fallback is None


@pytest.mark.parametrize("provider", ["codex", "claude"])
def test_both_providers_round_trip_handoff_and_receipt(client, provider):
    response = client.post("/api/tasks", json={"task": "Fix a database race condition", "analyzer": "heuristic"})
    assert response.status_code == 201
    task = response.json()
    task_id = task["public_task_id"]
    recommendation = task["recommendation"][provider]
    stored = client.get(f"/api/tasks/{task_id}").json()
    assert stored["recommendation"] == task["recommendation"]
    assert set(stored["handoffs"]) == {"codex", "claude"}
    assert client.get(f"/api/tasks/{task_id}/handoff").status_code == 422
    handoff = client.get(f"/api/tasks/{task_id}/handoff", params={"provider": provider}).json()
    assert handoff["handoff"] == stored["handoffs"][provider]
    assert f"recommended_provider={provider}" in handoff["handoff"]
    payload = receipt(task_id, execution={key: recommendation[key] for key in ("provider", "model", "effort")})
    imported = client.post("/api/results/import", json={"receipt": wrap(payload)})
    assert imported.status_code == 200, imported.text
    assert imported.json()["recommendation_followed"] is True
    history = client.get("/api/history").json()["rows"][0]
    assert set(history["recommendations"]) == {"codex", "claude"}
    assert history["recommended_provider"] == provider


def test_old_decisions_remain_readable_without_inventing_history(engine):
    old = engine.route(fp()).codex
    row = RoutingDecisionRow(explanation_json=old.model_dump(mode="json"))
    result = load_decision(row)
    assert result.legacy and result.codex == old
    assert result.claude is None
    assert "Not recorded" in result.unavailable["claude"]


def test_receipt_burn_estimate_ignores_other_provider_history(config, monkeypatch):
    from app.core.learning.statistics import EvidenceIndex
    from app.db.models import Execution
    from app.schemas.execution_receipt import ExecutionReceipt
    from app.services import receipt_service

    payload = ExecutionReceipt.model_validate(receipt(
        "RT-000001", execution={"provider": "claude", "model": "opus_5", "effort": "high"},
        outcome={"first_pass_success": False}, work={"debug_cycles": 2},
    ))
    execution = Execution(actual_provider="claude", actual_model="opus_5", actual_effort="high")
    index = EvidenceIndex()
    monkeypatch.setattr(receipt_service, "build_evidence_index", lambda *a, **kw: index)
    before = receipt_service._estimate_burn(None, config, execution, payload, "backend")
    index.cells[("backend", "codex", "luna", "low")] = HistoricalEvidence(weighted_attempts=100)
    after = receipt_service._estimate_burn(None, config, execution, payload, "backend")
    assert before == after
