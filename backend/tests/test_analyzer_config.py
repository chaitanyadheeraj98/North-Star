"""Analyzer selection, from the Settings page through to the bridge request.

The bridge decides WHICH model classifies a task; these tests prove the
backend sends the user's choice rather than leaving the bridge to guess, and
that a bridge refusal is surfaced instead of being retried against something
else.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from app.services.analyzer import AnalyzerError, AnalyzerOverrides, HeuristicAnalyzer
from app.services.pi_service import PiBridgeAnalyzer

FINGERPRINT: dict[str, Any] = {
    "schema_version": "1.0",
    "task_family": "concurrency",
    "task_type": "bug_fix",
    "complexity": 0.91,
    "ambiguity": 0.34,
    "requirements_clarity": 0.78,
    "regression_risk": 0.86,
    "architecture_reasoning": 0.7,
    "database_reasoning": 0.62,
    "concurrency_risk": 0.95,
    "security_risk": 0.3,
    "repository_understanding": 0.82,
    "scope": "multi_service",
    "estimated_files": 7,
    "frontend": False,
    "backend": True,
    "database": True,
    "infrastructure": False,
    "tests_required": "high",
    "confidence": 0.84,
}


class _FakeResponse:
    def __init__(self, status_code: int, payload: Any) -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self) -> Any:
        return self._payload


def _ok_response() -> _FakeResponse:
    return _FakeResponse(
        200,
        {
            "fingerprint": FINGERPRINT,
            "analyzer": {
                "provider": "openai-codex",
                "model": "gpt-5.5",
                "thinking_level": "low",
                "confidence": 0.84,
                "repaired": False,
                "duration_ms": 4000,
            },
        },
    )


class _RecordingClient:
    """Stands in for httpx.AsyncClient and records what was posted."""

    sent: list[dict[str, Any]] = []
    response: _FakeResponse = _ok_response()

    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        pass

    async def __aenter__(self) -> _RecordingClient:
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        return None

    async def post(self, _url: str, json: dict[str, Any]) -> _FakeResponse:  # noqa: A002
        type(self).sent.append(json)
        return type(self).response


@pytest.fixture
def recording(monkeypatch: pytest.MonkeyPatch) -> type[_RecordingClient]:
    import app.services.pi_service as module

    # Reset BOTH class attributes: a test that installs a failure response
    # would otherwise leak it into every test that ran after it.
    _RecordingClient.sent = []
    _RecordingClient.response = _ok_response()
    monkeypatch.setattr(module.httpx, "AsyncClient", _RecordingClient)
    return _RecordingClient


# --------------------------------------------------------------------------
# The override contract
# --------------------------------------------------------------------------


def test_empty_overrides_send_nothing():
    """No pin means "use the bridge's own boot configuration"."""
    assert AnalyzerOverrides().as_payload() == {}
    assert AnalyzerOverrides().is_empty


def test_blank_strings_are_not_treated_as_a_pin():
    assert AnalyzerOverrides(provider="", model=None).as_payload() == {}


def test_a_partial_pin_sends_only_what_was_pinned():
    overrides = AnalyzerOverrides(thinking_level="high")
    assert overrides.as_payload() == {"thinking_level": "high"}


# --------------------------------------------------------------------------
# What actually reaches the bridge
# --------------------------------------------------------------------------


def test_the_configured_analyzer_is_sent_on_every_request(recording):
    asyncio.run(
        PiBridgeAnalyzer().analyze(
            "fix the race",
            AnalyzerOverrides(provider="openai-codex", model="gpt-5.5", thinking_level="low"),
        )
    )
    payload = recording.sent[-1]
    assert payload["provider"] == "openai-codex"
    assert payload["model"] == "gpt-5.5"
    assert payload["thinking_level"] == "low"
    assert payload["task"] == "fix the race"


def test_no_analyzer_fields_are_sent_when_nothing_is_pinned(recording):
    asyncio.run(PiBridgeAnalyzer().analyze("fix the race", AnalyzerOverrides()))
    payload = recording.sent[-1]
    assert "provider" not in payload
    assert "model" not in payload
    assert "thinking_level" not in payload


def test_a_bridge_refusal_is_surfaced_not_retried_elsewhere(recording):
    """The bridge refuses to substitute a model; the backend must not either."""
    recording.response = _FakeResponse(
        503,
        {
            "error": 'Pi is not authenticated with "openai-codex".',
            "detail": 'Run "pi" and use /login.',
            "code": "not_authenticated",
            "alternatives": [{"provider": "kimi-coding", "models": ["kimi-for-coding"]}],
        },
    )
    with pytest.raises(AnalyzerError) as caught:
        asyncio.run(PiBridgeAnalyzer().analyze("fix the race", AnalyzerOverrides()))

    assert "openai-codex" in str(caught.value)
    assert len(recording.sent) == 1, "the backend must not retry with another analyzer"


def test_an_invalid_analyzer_configuration_is_reported_as_such(recording):
    recording.response = _FakeResponse(
        400, {"error": '"max" is not a Pi thinking level.', "detail": "Valid levels: ..."}
    )
    with pytest.raises(AnalyzerError, match="configuration is invalid"):
        asyncio.run(PiBridgeAnalyzer().analyze("x", AnalyzerOverrides(thinking_level="max")))


def test_the_offline_analyzer_accepts_overrides_and_ignores_them():
    """It has no model to choose, so callers need not special-case it."""
    envelope = asyncio.run(
        HeuristicAnalyzer().analyze(
            "Change the button colour to grey.",
            AnalyzerOverrides(provider="openai-codex", model="gpt-5.5"),
        )
    )
    assert envelope.fingerprint.task_family == "ui_cosmetic"
    assert envelope.analyzer.provider == "heuristic"


# --------------------------------------------------------------------------
# The Settings page
# --------------------------------------------------------------------------


def test_settings_exposes_the_analyzer_pins(client):
    preferences = client.get("/api/settings").json()["preferences"]
    assert preferences["analyzer_provider"] is None
    assert preferences["analyzer_model"] is None
    assert preferences["analyzer_effort"] is None


def test_settings_persists_the_analyzer_pins(client):
    saved = client.put(
        "/api/settings",
        json={
            "analyzer_provider": "openai-codex",
            "analyzer_model": "gpt-5.5",
            "analyzer_effort": "low",
        },
    ).json()
    assert saved["preferences"]["analyzer_model"] == "gpt-5.5"

    reloaded = client.get("/api/settings").json()["preferences"]
    assert reloaded["analyzer_provider"] == "openai-codex"
    assert reloaded["analyzer_effort"] == "low"


def test_an_analyzer_pin_can_be_cleared(client):
    client.put("/api/settings", json={"analyzer_model": "gpt-5.5"})
    client.put("/api/settings", json={"analyzer_model": None})
    assert client.get("/api/settings").json()["preferences"]["analyzer_model"] is None


def test_an_invalid_analyzer_effort_is_rejected(client):
    """`max` is a router effort, not a Pi thinking level."""
    assert client.put("/api/settings", json={"analyzer_effort": "max"}).status_code == 422
    assert client.put("/api/settings", json={"analyzer_effort": "ultra"}).status_code == 422


def test_the_saved_pin_reaches_the_bridge_on_the_next_analysis(client, recording):
    """The point of the whole wiring: Settings changes affect the next analysis."""
    client.put(
        "/api/settings",
        json={
            "analyzer_provider": "openai-codex",
            "analyzer_model": "gpt-5.5",
            "analyzer_effort": "medium",
        },
    )
    response = client.post("/api/tasks", json={"task": "Fix the race condition."})
    assert response.status_code == 201, response.text

    payload = recording.sent[-1]
    assert payload["provider"] == "openai-codex"
    assert payload["model"] == "gpt-5.5"
    assert payload["thinking_level"] == "medium"


def test_changing_the_pin_changes_the_next_request(client, recording):
    client.put("/api/settings", json={"analyzer_model": "gpt-5.5", "analyzer_provider": "openai-codex"})
    client.post("/api/tasks", json={"task": "First task."})
    assert recording.sent[-1]["model"] == "gpt-5.5"

    client.put("/api/settings", json={"analyzer_model": "gpt-5.4-mini"})
    client.post("/api/tasks", json={"task": "Second task."})
    assert recording.sent[-1]["model"] == "gpt-5.4-mini"


def test_pi_status_reports_configured_and_actual(client, monkeypatch):
    """Both halves matter; they differ exactly when something is wrong."""
    import app.services.pi_service as module

    class _HealthClient(_RecordingClient):
        async def get(self, _url: str) -> _FakeResponse:
            return _FakeResponse(
                200,
                {
                    "status": "ok",
                    "pi_available": True,
                    "authenticated": True,
                    "version": "1.1.0",
                    "configured_provider": "openai-codex",
                    "configured_model": "gpt-5.5",
                    "configured_effort": "low",
                    "actual_provider": "openai-codex",
                    "actual_model": "gpt-5.5",
                    "authenticated_providers": ["github-copilot", "kimi-coding", "openai-codex"],
                },
            )

    monkeypatch.setattr(module.httpx, "AsyncClient", _HealthClient)

    body = client.get("/api/pi/status").json()
    assert body["configured_provider"] == "openai-codex"
    assert body["configured_model"] == "gpt-5.5"
    assert body["configured_effort"] == "low"
    assert body["actual_provider"] == "openai-codex"
    assert body["actual_model"] == "gpt-5.5"
    assert body["authenticated"] is True
    # kimi being authenticated must not make it the analyzer.
    assert "kimi-coding" in body["authenticated_providers"]
    assert body["actual_provider"] != "kimi-coding"
