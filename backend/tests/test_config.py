"""Configuration loading and cross-file validation.

A router running on a half-parsed policy would answer every request, just with
silently wrong recommendations. That is the worst failure mode this application
has, so config problems are fatal at startup and these tests check that they
actually are.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
import yaml

from app.config import ConfigBundle, ConfigError
from app.schemas.enums import Effort, Scope
from app.schemas.model_registry import CAPABILITY_DIMENSIONS

BACKEND_ROOT = Path(__file__).resolve().parent.parent
REAL_CONFIG = BACKEND_ROOT / "config"


# --------------------------------------------------------------------------
# The shipped configuration
# --------------------------------------------------------------------------


def test_the_shipped_configuration_loads(config):
    assert config.registry.registry_version
    assert config.policy.router_version
    assert len(config.families.families) >= 20


def test_difficulty_weights_sum_to_one(policy):
    assert sum(policy.difficulty_weights.values()) == pytest.approx(1.0)


def test_every_scope_has_a_difficulty_score(policy):
    for scope in Scope:
        assert scope.value in policy.scope_scores


def test_scope_scores_increase_with_blast_radius(policy):
    ordered = [
        policy.scope_scores[scope.value]
        for scope in (
            Scope.SINGLE_LINE,
            Scope.SINGLE_FUNCTION,
            Scope.SINGLE_FILE,
            Scope.MULTI_FILE,
            Scope.SERVICE,
            Scope.MULTI_SERVICE,
            Scope.ARCHITECTURE,
        )
    ]
    assert ordered == sorted(ordered)


def test_effort_burn_priors_increase_with_effort(policy):
    from app.schemas.enums import EFFORT_ORDER

    values = [policy.effort_burn_prior[effort] for effort in EFFORT_ORDER]
    assert values == sorted(values)


def test_effort_capability_gains_increase_with_effort(policy):
    from app.schemas.enums import EFFORT_ORDER

    values = [policy.effort_capability_gain[effort] for effort in EFFORT_ORDER]
    assert values == sorted(values)


def test_medium_is_the_capability_reference_point(policy):
    """Capability priors are documented as calibrated at medium effort."""
    assert policy.effort_capability_gain[Effort.MEDIUM] == 0.0


def test_every_model_declares_every_capability_dimension(registry):
    for provider in registry.providers.values():
        for name, model in provider.models.items():
            missing = set(CAPABILITY_DIMENSIONS) - set(model.capability_priors)
            assert not missing, f"{name} is missing {missing}"


def test_registry_matches_the_reference_documents(registry):
    """Spot-check the facts seeded from ClaudeLLM.md and CodexLLM.md.

    Only SOURCED values are asserted: published price ratios and published
    effort support. The capability priors are our own assumptions and are
    deliberately not pinned here.
    """
    claude = registry.providers["claude"]
    # 1/5, 2/10, 5/25, 10/50 USD per 1M => 0.5x, 1x, 2.5x, 5x on output price.
    assert claude.models["haiku_4_5"].relative_model_burn == pytest.approx(0.5)
    assert claude.models["sonnet_5"].relative_model_burn == pytest.approx(1.0)
    assert claude.models["opus_5"].relative_model_burn == pytest.approx(2.5)
    assert claude.models["fable_5_1"].relative_model_burn == pytest.approx(5.0)
    # Haiku is only listed at low/medium in the reference matrix.
    assert set(claude.models["haiku_4_5"].supported_efforts) == {Effort.LOW, Effort.MEDIUM}

    codex = registry.providers["codex"]
    # 30/300/500/1250 credits per 1M output => 0.1x, 1x, 1.667x, 4.167x vs Terra.
    assert codex.models["luna"].relative_model_burn == pytest.approx(0.1)
    assert codex.models["terra"].relative_model_burn == pytest.approx(1.0)
    assert codex.models["sol"].relative_model_burn == pytest.approx(1.667, abs=1e-3)
    assert codex.models["astra"].relative_model_burn == pytest.approx(4.167, abs=1e-3)
    # Astra starts at low; the GPT-5.6 models also expose `none`.
    assert Effort.NONE not in codex.models["astra"].supported_efforts
    assert Effort.NONE in codex.models["sol"].supported_efforts


def test_capability_ordering_is_internally_consistent(registry):
    """Within a provider, a more expensive model should not be less capable.

    A registry edit that breaks this would make the router pay more for less,
    which no amount of downstream cleverness can fix.
    """
    for provider_name, provider in registry.providers.items():
        models = sorted(provider.models.values(), key=lambda m: m.relative_model_burn)
        coding = [m.capability_priors["coding"] for m in models]
        assert coding == sorted(coding), f"{provider_name} coding priors are not monotonic in cost"


def test_task_family_adjustments_are_small(families):
    """The family is a nudge. Risk comes from the fingerprint."""
    for key, entry in families.families.items():
        assert abs(entry.required_reliability_adjustment) <= 0.05, key


def test_cosmetic_and_documentation_lower_the_bar(families):
    assert families.adjustment("ui_cosmetic") < 0
    assert families.adjustment("documentation") < 0
    assert families.adjustment("concurrency") > 0
    assert families.adjustment("security") > 0


def test_affinity_starts_empty(registry):
    """Inventing affinities would be indistinguishable from making up facts."""
    assert registry.family_affinity == {}


# --------------------------------------------------------------------------
# Rejecting bad configuration
# --------------------------------------------------------------------------


def _bundle(tmp: Path, **overrides: dict) -> ConfigBundle:
    """Copy the real config into a temp dir with targeted mutations."""
    tmp.mkdir(parents=True, exist_ok=True)
    for name in ("models.yaml", "routing.yaml", "task_families.yaml"):
        data = yaml.safe_load((REAL_CONFIG / name).read_text(encoding="utf-8"))
        if name in overrides:
            overrides[name](data)
        (tmp / name).write_text(yaml.safe_dump(data), encoding="utf-8")
    return ConfigBundle(tmp)


def test_a_missing_file_is_fatal(scratch_dir):
    directory = scratch_dir
    with pytest.raises(ConfigError, match="not found"):
        ConfigBundle(directory).load()


def test_difficulty_weights_that_do_not_sum_to_one_are_rejected(scratch_dir):
    directory = scratch_dir

    def mutate(data: dict) -> None:
        data["difficulty_weights"]["complexity"] = 0.9

    with pytest.raises(ConfigError, match="must sum to 1.0"):
        _bundle(directory, **{"routing.yaml": mutate}).load()


def test_an_out_of_range_capability_prior_is_rejected(scratch_dir):
    directory = scratch_dir

    def mutate(data: dict) -> None:
        data["providers"]["codex"]["models"]["sol"]["capability_priors"]["coding"] = 1.4

    with pytest.raises(ConfigError, match="models.yaml is invalid"):
        _bundle(directory, **{"models.yaml": mutate}).load()


def test_an_enabled_model_with_all_zero_capability_priors_is_rejected(scratch_dir):
    """All-zero is the placeholder update_models() seeds a brand-new model
    with, never a real judgement - an enabled model claiming it is exactly
    that placeholder is rejected the same way a missing review is."""
    directory = scratch_dir

    def mutate(data: dict) -> None:
        model = data["providers"]["codex"]["models"]["sol"]
        model["capability_priors"] = {dim: 0.0 for dim in model["capability_priors"]}

    with pytest.raises(ConfigError, match="models.yaml is invalid"):
        _bundle(directory, **{"models.yaml": mutate}).load()


def test_an_affinity_pointing_at_an_unknown_model_is_rejected(scratch_dir):
    directory = scratch_dir

    def mutate(data: dict) -> None:
        data["family_affinity"] = {"codex.nonexistent.security": 0.05}

    with pytest.raises(ConfigError, match="unknown model"):
        _bundle(directory, **{"models.yaml": mutate}).load()


def test_an_affinity_pointing_at_an_unknown_family_is_rejected(scratch_dir):
    directory = scratch_dir

    def mutate(data: dict) -> None:
        data["family_affinity"] = {"codex.sol.made_up_family": 0.05}

    with pytest.raises(ConfigError, match="unknown task family"):
        _bundle(directory, **{"models.yaml": mutate}).load()


def test_a_registry_with_nothing_routable_is_rejected(scratch_dir):
    directory = scratch_dir

    def mutate(data: dict) -> None:
        for provider in data["providers"].values():
            provider["enabled"] = False

    with pytest.raises(ConfigError, match="no routable configurations"):
        _bundle(directory, **{"models.yaml": mutate}).load()


def test_an_unknown_difficulty_dimension_is_rejected(scratch_dir):
    directory = scratch_dir

    def mutate(data: dict) -> None:
        data["difficulty_weights"] = {"vibes": 1.0}

    with pytest.raises(ConfigError, match="unknown dimensions"):
        _bundle(directory, **{"routing.yaml": mutate}).load()


def test_malformed_yaml_is_reported_as_such(scratch_dir):
    directory = scratch_dir
    directory.mkdir(parents=True, exist_ok=True)
    for name in ("models.yaml", "routing.yaml", "task_families.yaml"):
        (directory / name).write_text(
            (REAL_CONFIG / name).read_text(encoding="utf-8"), encoding="utf-8"
        )
    (directory / "routing.yaml").write_text(
        textwrap.dedent("""
        schema_version: "1.0"
          bad_indent: [unclosed
        """),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="not valid YAML"):
        ConfigBundle(directory).load()
