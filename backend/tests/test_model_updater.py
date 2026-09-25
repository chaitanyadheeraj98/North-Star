import json
import shutil
import threading

import httpx
import pytest

from app.config import ConfigBundle
from app.schemas.enums import Effort
from app.services import model_updater as updater


@pytest.fixture
def bundle(config, scratch_dir):
    for file in config.directory.glob("*.yaml"):
        shutil.copyfile(file, scratch_dir / file.name)
    result = ConfigBundle(scratch_dir)
    result.load()
    return result


@pytest.fixture
def sources(bundle, monkeypatch):
    result = {}
    for provider, entries in bundle.registry.providers.items():
        result[provider] = [updater.OfficialModel(
            model_id=entry.model_id, display_name=entry.display_name,
            supported_efforts=entry.supported_efforts,
            source_pricing=entry.source_pricing,
            source_urls=[updater.OPENAI_MODELS if provider == "codex" else updater.CLAUDE_MODELS],
        ) for entry in entries.models.values()]
    result["codex"].append(updater.OfficialModel(
        model_id="gpt-99-test", display_name="Test model", supported_efforts=[Effort.LOW],
        source_urls=[updater.OPENAI_MODELS],
    ))
    monkeypatch.setattr(updater, "fetch_official_models", lambda: result)
    return result


def test_update_backs_up_reloads_preserves_priors_and_is_idempotent(bundle, sources):
    path = bundle.directory / "models.yaml"
    original = path.read_bytes()
    before = bundle.registry.model_copy(deep=True)
    result = updater.update_models(bundle)
    assert result.status == "updated" and result.registry_version == "1.0.1"
    assert (bundle.directory / result.backup).read_bytes() == original
    assert bundle.registry.registry_version == result.registry_version
    new = bundle.registry.model("codex", "gpt_99_test")
    assert not new.enabled and new.review_required
    assert all(v == 0 for v in new.capability_priors.values())
    assert not any(m == "gpt_99_test" for _, m, _ in bundle.registry.enabled_configurations())
    for provider, entries in before.providers.items():
        for key, old in entries.models.items():
            now = bundle.registry.model(provider, key)
            assert now.capability_priors == old.capability_priors
            assert now.relative_model_burn == old.relative_model_burn
            assert now.model_id == old.model_id
    updated = path.read_bytes()
    second = updater.update_models(bundle)
    assert second.status == "unchanged" and second.registry_version == "1.0.1"
    assert second.backup is None and path.read_bytes() == updated
    assert len(list(bundle.directory.glob("*.bak"))) == 1


@pytest.mark.parametrize("failure", ["network", "empty", "duplicate", "invalid", "cross_validation", "backup", "replace", "reload"])
def test_failure_preserves_disk_and_live_config(bundle, sources, monkeypatch, failure):
    path = bundle.directory / "models.yaml"
    original, live = path.read_bytes(), bundle.registry
    def fail(*args, **kwargs):
        raise RuntimeError("injected failure")
    if failure == "network":
        monkeypatch.setattr(updater, "fetch_official_models", fail)
    elif failure == "empty":
        sources["claude"] = []
    elif failure == "duplicate":
        sources["codex"].append(sources["codex"][0])
    elif failure == "invalid":
        sources["codex"][0].supported_efforts = ["future-effort"]
    elif failure == "cross_validation":
        monkeypatch.setattr(bundle, "validate_registry", fail)
    elif failure == "backup":
        monkeypatch.setattr(updater.tempfile, "mkstemp", fail)
    elif failure == "replace":
        replace = updater.os.replace
        calls = []
        def fail_once(*args):
            calls.append(args)
            if len(calls) == 1:
                raise OSError("injected replace failure")
            return replace(*args)
        monkeypatch.setattr(updater.os, "replace", fail_once)
    else:
        monkeypatch.setattr(bundle, "load", fail)
    with pytest.raises(updater.ModelUpdateError):
        updater.update_models(bundle)
    assert path.read_bytes() == original
    assert bundle.registry is live
    assert not list(bundle.directory.glob("*.tmp"))


def test_missing_models_preserved_and_alias_priors_reused(bundle, sources):
    sources["claude"] = [sources["claude"][0]]
    item = sources["claude"][0]
    old_id = item.model_id
    item.model_id += "-20260925"
    item.aliases = [old_id]
    result = updater.update_models(bundle)
    assert "claude/sonnet_5" in result.unconfirmed
    assert bundle.registry.model("claude", "haiku_4_5").model_id == old_id
    assert not any(key.startswith("claude/") for key in result.added)


def test_concurrent_update_is_rejected(bundle, sources):
    errors = []
    def run():
        try:
            updater.update_models(bundle)
        except updater.ModelUpdateError as exc:
            errors.append(str(exc))
    with bundle.reload_lock:
        thread = threading.Thread(target=run)
        thread.start()
        thread.join(timeout=5)
    assert errors and "already running" in errors[0]


def test_fetch_rejects_redirects_unofficial_urls_and_oversized_sources():
    for response in (httpx.Response(302, headers={"location": "https://evil.example"}), httpx.Response(200, text="x" * (updater.MAX_SOURCE_BYTES + 1))):
        with httpx.Client(transport=httpx.MockTransport(lambda _: response)) as client:
            with pytest.raises((updater.ModelUpdateError, httpx.HTTPStatusError)):
                updater._fetch(client, updater.OPENAI_MODELS)
    with httpx.Client() as client, pytest.raises(updater.ModelUpdateError):
        updater._fetch(client, "https://evil.example")


def test_official_parsers_use_explicit_metadata_only():
    codex = updater.parse_openai(json.dumps({"models": [{
        "slug": "gpt-99-test", "display_name": "Test", "visibility": "list",
        "supported_reasoning_levels": [{"effort": "low"}, {"effort": "high"}],
        "description": "Best model ever, 100% reliability!",
    }]}))
    assert codex[0].supported_efforts == [Effort.LOW, Effort.HIGH]
    overview = '''| Feature | Claude Test 9 |
| Claude API ID | `claude-test-9` |
| Pricing | $2 / input MTok, $10 / output MTok |
| Default effort | `high` |
'''
    effort = '''---
featureMetadata:
  supportedModels: [claude-test-9]
---
| Level | Description |
| `low` | Minimal |
| `medium` | Moderate |
| `high` | Deep |
| `xhigh` | Available on Claude Test 9. |
| `max` | Available on Claude Other 9. |
'''
    claude = updater.parse_claude(overview, effort)
    assert claude[0].supported_efforts == [Effort.LOW, Effort.MEDIUM, Effort.HIGH, Effort.XHIGH]
    assert claude[0].source_pricing["output_per_1m_usd"] == 10
    with pytest.raises((KeyError, updater.ModelUpdateError)):
        updater.parse_claude("<html>Changed docs</html>", effort)


def test_update_endpoint_reports_failure_and_success(client, bundle, sources, monkeypatch):
    from app.main import app
    from app.api.deps import config_bundle
    app.dependency_overrides[config_bundle] = lambda: bundle
    try:
        response = client.post("/api/models/update")
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "updated"
        assert client.get("/api/models").json()["registry_version"] == "1.0.1"
        sources["claude"] = []
        response = client.post("/api/models/update")
        assert response.status_code == 422
        assert bundle.registry.registry_version == "1.0.1"
    finally:
        app.dependency_overrides.clear()
