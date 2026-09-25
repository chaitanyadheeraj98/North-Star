"""HTTP surface: status codes, error messages, and the contracts the UI relies on."""

from __future__ import annotations

from .fixtures import receipt, wrap

TASK = "Add a POST /api/leads endpoint that validates the payload and returns 201."


def test_health(client):
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert body["configurations"] > 0


def test_create_without_analysis(client):
    body = client.post("/api/tasks", json={"task": TASK, "analyze": False}).json()
    assert body["public_task_id"] == "RT-000001"
    assert body["fingerprint"] is None
    assert body["recommendation"] is None
    assert body["status"] == "created"


def test_an_empty_task_is_rejected(client):
    assert client.post("/api/tasks", json={"task": "   "}).status_code == 422
    assert client.post("/api/tasks", json={"task": ""}).status_code == 422


def test_unknown_task_returns_404(client):
    response = client.get("/api/tasks/RT-999999")
    assert response.status_code == 404
    assert "RT-999999" in response.json()["detail"]


def test_recommendation_before_analysis_is_a_conflict(client):
    created = client.post("/api/tasks", json={"task": TASK, "analyze": False}).json()
    response = client.get(f"/api/tasks/{created['public_task_id']}/recommendation")
    assert response.status_code == 409
    assert "Analyze it first" in response.json()["detail"]


def test_handoff_before_analysis_is_a_conflict(client):
    created = client.post("/api/tasks", json={"task": TASK, "analyze": False}).json()
    assert client.get(f"/api/tasks/{created['public_task_id']}/handoff").status_code == 409


def test_pi_unavailable_returns_503_rather_than_a_silent_fallback(client):
    """A user who thinks Pi read their task deserves to know when it did not."""
    response = client.post(
        "/api/tasks", json={"task": TASK, "analyzer": "pi", "allow_fallback": False}
    )
    assert response.status_code == 503
    detail = response.json()["detail"]
    assert "Pi bridge" in detail["message"]


def test_fallback_is_used_only_when_asked_for_and_is_announced(client):
    response = client.post(
        "/api/tasks", json={"task": TASK, "analyzer": "pi", "allow_fallback": True}
    )
    assert response.status_code == 201
    body = response.json()
    assert body["analyzer"]["fallback_used"] is True
    assert body["analyzer"]["source"] == "heuristic"
    assert "unreachable" in body["analyzer"]["note"]
    assert body["recommendation"] is not None


def test_reanalysis_replaces_the_fingerprint(client):
    created = client.post("/api/tasks", json={"task": TASK, "analyzer": "heuristic"}).json()
    task_id = created["public_task_id"]

    again = client.post(
        f"/api/tasks/{task_id}/analyze", json={"analyzer": "heuristic"}
    )
    assert again.status_code == 200

    from app.db.database import get_session_factory
    from app.db.models import TaskFingerprintRow

    session = get_session_factory()()
    try:
        assert session.query(TaskFingerprintRow).count() == 1
    finally:
        session.close()


def test_recompute_uses_current_history(client):
    created = client.post("/api/tasks", json={"task": TASK, "analyzer": "heuristic"}).json()
    task_id = created["public_task_id"]
    recomputed = client.get(
        f"/api/tasks/{task_id}/recommendation", params={"recompute": True}
    )
    assert recomputed.status_code == 200
    assert recomputed.json()["provider"] == created["recommendation"]["provider"]


def test_models_endpoint_states_its_provenance(client):
    body = client.get("/api/models").json()
    assert body["providers"]
    assert body["configurations"] > 0
    assert "not provider guarantees" in body["provenance_note"]
    assert "adaptive reasoning" in body["provenance_note"]
    # Base burn per effort must be exposed so the UI never recomputes it.
    sol = next(
        m
        for p in body["providers"]
        if p["key"] == "codex"
        for m in p["models"]
        if m["key"] == "sol"
    )
    assert sol["base_burn_by_effort"]["high"] > sol["base_burn_by_effort"]["low"]


def test_task_families_endpoint(client):
    body = client.get("/api/models/task-families").json()
    keys = {f["key"] for f in body["families"]}
    assert {"ui_cosmetic", "concurrency", "backend_data_integrity"} <= keys
    assert all(f["definition"] for f in body["families"])


def test_config_reload(client):
    body = client.post("/api/models/reload").json()
    assert body["status"] == "reloaded"


def test_settings_reports_state_without_leaking_secrets(client):
    body = client.get("/api/settings").json()
    assert body["router_version"]
    assert body["pi"]["available"] is False  # no bridge in tests
    assert body["preferences"]["analyzer_source"] == "pi"
    assert "routing.yaml" in body["policy"]["editable_in"]
    # No credential should ever appear in this payload. The patterns are
    # credential-shaped rather than merely topical: "output tokens" is a unit
    # of burn and is expected to appear.
    text = str(body).lower()
    for secret in (
        "api_key",
        "apikey",
        "sk-ant",
        "authorization",
        "password",
        "access_token",
        "refresh_token",
        "client_secret",
    ):
        assert secret not in text, f"{secret!r} leaked into the settings payload"


def test_settings_can_be_updated(client):
    saved = client.put(
        "/api/settings", json={"analyzer_source": "heuristic", "allow_analyzer_fallback": True}
    ).json()
    assert saved["preferences"]["analyzer_source"] == "heuristic"
    assert client.get("/api/settings").json()["preferences"]["allow_analyzer_fallback"] is True


def test_pi_status_never_raises_when_the_bridge_is_down(client):
    body = client.get("/api/pi/status").json()
    assert body["available"] is False
    assert "Pi bridge" in body["note"]


def test_pi_lists_are_empty_rather_than_failing_when_down(client):
    assert client.get("/api/pi/providers").json() == {"providers": []}
    assert client.get("/api/pi/models").json() == {"models": []}


def test_history_paginates(client):
    for n in range(7):
        client.post("/api/tasks", json={"task": f"Task {n}", "analyze": False})

    page = client.get("/api/history", params={"limit": 3, "offset": 0}).json()
    assert page["total"] == 7
    assert len(page["rows"]) == 3

    second = client.get("/api/history", params={"limit": 3, "offset": 3}).json()
    assert {r["public_task_id"] for r in page["rows"]} & {
        r["public_task_id"] for r in second["rows"]
    } == set()


def test_history_filters_by_family(client):
    client.post("/api/tasks", json={"task": TASK, "analyzer": "heuristic"})
    client.post(
        "/api/tasks",
        json={"task": "Change the button colour to grey.", "analyzer": "heuristic"},
    )

    cosmetic = client.get("/api/history", params={"task_family": "ui_cosmetic"}).json()
    assert cosmetic["total"] == 1
    assert cosmetic["rows"][0]["task_family"] == "ui_cosmetic"


def test_analytics_is_honest_about_an_empty_database(client):
    body = client.get("/api/analytics").json()
    assert body["totals"]["executions"] == 0
    assert any("No executions recorded" in note for note in body["notes"])


def test_deleting_a_task_removes_it(client):
    created = client.post("/api/tasks", json={"task": TASK, "analyze": False}).json()
    task_id = created["public_task_id"]
    assert client.delete(f"/api/tasks/{task_id}").status_code == 204
    assert client.get(f"/api/tasks/{task_id}").status_code == 404


def test_malformed_receipt_returns_422_with_a_readable_message(client):
    response = client.post("/api/results/import", json={"receipt": "not a receipt"})
    assert response.status_code == 422
    assert "No JSON object found" in response.json()["detail"]


def test_a_receipt_naming_an_unknown_model_is_kept_but_flagged(client):
    created = client.post("/api/tasks", json={"task": TASK, "analyzer": "heuristic"}).json()
    body = client.post(
        "/api/results/import",
        json={
            "receipt": wrap(
                receipt(
                    created["public_task_id"],
                    execution={"provider": "codex", "model": "nebula", "effort": "high"},
                )
            )
        },
    )
    assert body.status_code == 200
    payload = body.json()
    assert payload["actual"] == "codex/nebula/high"
    assert any("not a configuration this registry declares" in w for w in payload["warnings"])


def test_rebuild_statistics_endpoint(client):
    created = client.post("/api/tasks", json={"task": TASK, "analyzer": "heuristic"}).json()
    client.post(
        "/api/results/import",
        json={
            "receipt": wrap(
                receipt(
                    created["public_task_id"],
                    execution={"provider": "codex", "model": "terra", "effort": "medium"},
                )
            )
        },
    )
    body = client.post("/api/results/rebuild-statistics").json()
    assert body["status"] == "ok"
    assert body["rebuilt_cells"] == 1


def test_openapi_is_served(client):
    """The API doc page is the fastest way to debug a bad request by hand."""
    assert client.get("/openapi.json").status_code == 200
