import json
import shutil
import threading

import httpx
import pytest

from app.config import ConfigBundle
from app.schemas.enums import Effort
from app.schemas.model_registry import CAPABILITY_DIMENSIONS
from app.services import model_updater as updater


def _next_patch(version: str) -> str:
    major, minor, patch = version.split(".")
    return f"{major}.{minor}.{int(patch) + 1}"


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
    monkeypatch.setattr(
        updater, "fetch_official_models", lambda: (result, "raw claude docs text (fixture)")
    )
    return result


@pytest.fixture
def pi_research(monkeypatch):
    """Stub research_capabilities as if the Pi bridge were reachable.

    Scores every target it is given (whatever the real registry's current
    review_required entries plus the sources fixture's synthetic model happen
    to be) with a uniform, non-placeholder prior, and appends a marker to
    whichever reference docs it was handed - enough to prove the auto-enable
    and doc-rewrite paths without depending on a live Pi bridge.
    """

    def fake(settings, targets, candidate, sources, claude_docs_raw, current_docs):
        return updater.ResearchOutcome(
            capability_priors={
                f"{provider}/{key}": {dim: 0.6 for dim in CAPABILITY_DIMENSIONS}
                for provider, key in targets
            },
            claude_reference_md=(
                current_docs["claude"] + "\n<!-- researched -->\n" if "claude" in current_docs else None
            ),
            codex_reference_md=(
                current_docs["codex"] + "\n<!-- researched -->\n" if "codex" in current_docs else None
            ),
        )

    monkeypatch.setattr(updater, "research_capabilities", fake)
    return fake


def test_update_backs_up_reloads_is_idempotent_and_falls_back_to_placeholders_when_pi_is_unreachable(
    bundle, sources
):
    """Default test environment: PI_BRIDGE_URL points nowhere (see app_env), so
    every review_required target - the synthetic one this test adds, and any
    the real registry already carries - stays exactly at today's placeholder
    behaviour, and the rest of the update still succeeds."""
    path = bundle.directory / "models.yaml"
    original = path.read_bytes()
    before = bundle.registry.model_copy(deep=True)
    expected_version = _next_patch(before.registry_version)
    result = updater.update_models(bundle)
    assert result.status == "updated" and result.registry_version == expected_version
    assert (bundle.directory / result.backup).read_bytes() == original
    assert bundle.registry.registry_version == result.registry_version
    assert result.researched == []
    assert any("Pi bridge unreachable" in w for w in result.warnings)
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
    assert second.status == "unchanged" and second.registry_version == expected_version
    assert second.backup is None and path.read_bytes() == updated
    assert len(list(bundle.directory.glob("*.bak"))) == 1


def test_update_researches_and_auto_enables_when_pi_is_reachable(bundle, sources, pi_research):
    result = updater.update_models(bundle)
    assert result.status == "updated"
    assert "codex/gpt_99_test" in result.researched
    assert not any("Pi bridge unreachable" in w for w in result.warnings)

    new = bundle.registry.model("codex", "gpt_99_test")
    assert new.enabled and not new.review_required
    assert not all(v == 0.0 for v in new.capability_priors.values())
    assert any(m == "gpt_99_test" for _, m, _ in bundle.registry.enabled_configurations())

    # An already-reviewed model was never a research target and is untouched.
    stable = bundle.registry.model("claude", "sonnet_5")
    assert stable.capability_priors == {
        "coding": 0.9, "debugging": 0.87, "architecture": 0.82, "database": 0.84,
        "concurrency": 0.78, "security": 0.82, "repository_understanding": 0.86,
    }

    # codex/gpt_99_test guarantees a codex research target regardless of what
    # the live registry's own review_required set happens to be right now;
    # a claude doc rewrite is not asserted here for the same reason - whether
    # one occurs depends on the live registry's current unreviewed models,
    # which this test does not control.
    from app.config import Settings, get_settings

    get_settings.cache_clear()
    settings: Settings = get_settings()
    codex_md = (settings.reference_dir / "CodexLLM.md").read_text(encoding="utf-8")
    assert "<!-- researched -->" in codex_md


def test_update_leaves_a_target_disabled_when_effort_support_is_still_unconfirmed(
    bundle, sources, monkeypatch
):
    """Real priors are still worth keeping even when the model cannot be routed yet."""

    def fake(settings, targets, candidate, sources, claude_docs_raw, current_docs):
        return updater.ResearchOutcome(
            capability_priors={
                f"{p}/{k}": {dim: 0.6 for dim in CAPABILITY_DIMENSIONS} for p, k in targets
            },
        )

    monkeypatch.setattr(updater, "research_capabilities", fake)
    # An empty supported_efforts on the official source is a real signal
    # (see build_candidate: it means the provider did not confirm any effort
    # for this model), and build_candidate carries it straight through.
    sources["codex"][-1].supported_efforts = None
    result = updater.update_models(bundle)
    new = bundle.registry.model("codex", "gpt_99_test")
    # Still not routable, so still flagged for another look next cycle - but
    # the priors are real now, not the 0.0 placeholder build_candidate seeds.
    assert not new.enabled and new.review_required
    assert new.capability_priors["coding"] == 0.6
    assert "codex/gpt_99_test" not in result.researched
    assert any("effort support is still unconfirmed" in w for w in result.warnings)


@pytest.mark.parametrize("failure", ["connect", "status", "not_json", "schema"])
def test_research_capabilities_maps_every_bridge_failure_to_research_unavailable(
    bundle, monkeypatch, failure
):
    def handler(request):
        if failure == "status":
            return httpx.Response(503, json={"error": "not configured"})
        if failure == "not_json":
            return httpx.Response(200, text="not json")
        if failure == "schema":
            return httpx.Response(200, json={"capability_priors": "not a dict"})
        raise httpx.ConnectError("refused", request=request)

    # Capture the real class before patching: the lambda below is about to
    # become `updater.httpx.Client` itself, so calling `httpx.Client(...)`
    # from inside it would recurse into itself rather than build a real client.
    real_client = httpx.Client
    monkeypatch.setattr(
        updater.httpx, "Client",
        lambda *a, **k: real_client(transport=httpx.MockTransport(handler), **k),
    )

    from app.config import get_settings

    provider_name, provider = next(iter(bundle.registry.providers.items()))
    model_key = next(iter(provider.models))

    with pytest.raises(updater.ResearchUnavailable):
        updater.research_capabilities(
            get_settings(), [(provider_name, model_key)], bundle.registry,
            {"codex": [], "claude": []}, "raw docs", {},
        )


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
    before_version = bundle.registry.registry_version
    expected_version = _next_patch(before_version)
    app.dependency_overrides[config_bundle] = lambda: bundle
    try:
        response = client.post("/api/models/update")
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "updated"
        # No Pi bridge is running against the test environment (see app_env),
        # so nothing is auto-enabled this cycle - the endpoint still succeeds.
        assert response.json()["researched"] == []
        assert client.get("/api/models").json()["registry_version"] == expected_version
        sources["claude"] = []
        response = client.post("/api/models/update")
        assert response.status_code == 422
        assert bundle.registry.registry_version == expected_version
    finally:
        app.dependency_overrides.clear()


def test_update_endpoint_reports_researched_models_when_pi_is_reachable(
    client, bundle, sources, pi_research
):
    from app.main import app
    from app.api.deps import config_bundle

    app.dependency_overrides[config_bundle] = lambda: bundle
    try:
        response = client.post("/api/models/update")
        assert response.status_code == 200, response.text
        assert "codex/gpt_99_test" in response.json()["researched"]
    finally:
        app.dependency_overrides.clear()
