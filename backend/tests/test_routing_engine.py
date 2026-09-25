"""Routing engine: determinism, thresholds, capability, burn, selection."""

from __future__ import annotations

import pytest

from app.core.router import burn as burn_mod
from app.core.router import reliability as rel_mod
from app.core.router import scoring, thresholds
from app.core.router.engine import RoutingEngine
from app.core.router.reliability import EMPTY_EVIDENCE, HistoricalEvidence
from app.schemas.enums import Effort, ReasonCode

from .fixtures import EXPECTATIONS, by_name, fp


# --------------------------------------------------------------------------
# Determinism
# --------------------------------------------------------------------------


def test_same_inputs_produce_the_same_decision(engine):
    fingerprint = by_name("deduplication identity semantics").fingerprint
    first = engine.route(fingerprint)
    second = engine.route(fingerprint)

    assert (first.provider, first.model, first.effort) == (
        second.provider,
        second.model,
        second.effort,
    )
    assert first.predicted_burn == pytest.approx(second.predicted_burn)
    assert first.confidence == pytest.approx(second.confidence)
    assert first.reason_codes == second.reason_codes


def test_a_fresh_engine_makes_the_same_decision(config):
    """Determinism must survive process boundaries, not just repeated calls."""
    fingerprint = by_name("duplicate payments race condition").fingerprint
    a = RoutingEngine(config.registry, config.policy, config.families).route(fingerprint)
    b = RoutingEngine(config.registry, config.policy, config.families).route(fingerprint)
    assert a.model_dump(mode="json") == b.model_dump(mode="json")


def test_evaluation_order_is_total(engine):
    """Equal-burn configurations must not reorder between runs."""
    decision = engine.route(fp())
    burns = [round(s.predicted_burn, 9) for s in decision.evaluated]
    assert burns == sorted(burns)


# --------------------------------------------------------------------------
# Required reliability
# --------------------------------------------------------------------------


def test_required_reliability_rises_with_risk(policy, families):
    low, _ = thresholds.compute_required_reliability(
        by_name("change button text").fingerprint, policy, families.adjustment("ui_cosmetic")
    )
    high, _ = thresholds.compute_required_reliability(
        by_name("duplicate payments race condition").fingerprint,
        policy,
        families.adjustment("concurrency"),
    )
    assert low < high
    assert low >= policy.required_reliability.min
    assert high <= policy.required_reliability.max


def test_required_reliability_is_clamped(policy, families):
    everything = fp(
        complexity=1.0, regression_risk=1.0, database_reasoning=1.0,
        security_risk=1.0, concurrency_risk=1.0, scope="architecture",
    )
    value, _ = thresholds.compute_required_reliability(everything, policy, 0.2)
    assert value == pytest.approx(policy.required_reliability.max)

    nothing = fp(
        complexity=0.0, regression_risk=0.0, database_reasoning=0.0,
        security_risk=0.0, concurrency_risk=0.0, scope="single_line",
    )
    value, _ = thresholds.compute_required_reliability(nothing, policy, -0.2)
    assert value == pytest.approx(policy.required_reliability.min)


def test_ambiguity_does_not_raise_the_reliability_bar(policy, families):
    """An ambiguous task is harder to verify, not more dangerous to get wrong.

    It should cost the router confidence, not force a stronger model.
    """
    clear = fp(ambiguity=0.05, requirements_clarity=0.95)
    vague = fp(ambiguity=0.9, requirements_clarity=0.2)
    a, _ = thresholds.compute_required_reliability(clear, policy, 0.0)
    b, _ = thresholds.compute_required_reliability(vague, policy, 0.0)
    assert a == pytest.approx(b)


def test_family_adjustment_is_applied(policy):
    fingerprint = fp()
    base, _ = thresholds.compute_required_reliability(fingerprint, policy, 0.0)
    raised, _ = thresholds.compute_required_reliability(fingerprint, policy, 0.02)
    assert raised == pytest.approx(base + 0.02)


# --------------------------------------------------------------------------
# Capability and effort
# --------------------------------------------------------------------------


def test_capability_weighting_follows_the_task(registry, policy):
    """A database-heavy task must weight the database dimension heavily."""
    sonnet = registry.model("claude", "sonnet_5")
    db_task = fp(database_reasoning=0.95, concurrency_risk=0.0, security_risk=0.0)
    cosmetic = fp(database_reasoning=0.0, concurrency_risk=0.0, security_risk=0.0,
                  complexity=0.05, repository_understanding=0.05, ambiguity=0.05,
                  requirements_clarity=0.98)

    db_match = scoring.capability_match(db_task, sonnet, policy)
    cosmetic_match = scoring.capability_match(cosmetic, sonnet, policy)

    # Sonnet's database prior (0.84) is below its coding prior (0.90), so a
    # database-dominated task must pull the blended match down.
    assert db_match < cosmetic_match


def test_effort_cannot_substitute_for_capability(registry, policy):
    """A weak model at max effort must not reach a strong model at medium.

    This is the property that stops the router recommending a cheap model with
    maximum reasoning as a stand-in for competence.
    """
    weak = registry.model("codex", "luna")
    strong = registry.model("codex", "sol")
    task = fp(complexity=0.8, database_reasoning=0.7, regression_risk=0.8)

    weak_maxed = scoring.apply_effort(
        scoring.capability_match(task, weak, policy), policy.effort_capability_gain[Effort.MAX]
    )
    strong_medium = scoring.apply_effort(
        scoring.capability_match(task, strong, policy),
        policy.effort_capability_gain[Effort.MEDIUM],
    )
    assert weak_maxed < strong_medium


def test_effort_is_monotonic(policy):
    from app.schemas.enums import EFFORT_ORDER

    values = [
        scoring.apply_effort(0.8, policy.effort_capability_gain[effort])
        for effort in EFFORT_ORDER
    ]
    assert values == sorted(values)


def test_capability_stays_in_range(policy):
    for capability in (0.0, 0.05, 0.5, 0.99, 1.0):
        for effort in policy.effort_capability_gain:
            value = scoring.apply_effort(capability, policy.effort_capability_gain[effort])
            assert 0.0 <= value <= 0.999


# --------------------------------------------------------------------------
# Reliability
# --------------------------------------------------------------------------


def test_reliability_never_reaches_certainty(policy):
    value = rel_mod.prior_reliability(0.999, 0.0, policy)
    assert value <= policy.reliability_model.max_reliability
    assert value < 1.0


def test_reliability_falls_as_difficulty_rises(policy):
    easy = rel_mod.prior_reliability(0.9, 0.05, policy)
    hard = rel_mod.prior_reliability(0.9, 0.95, policy)
    assert easy > hard


def test_reliability_rises_with_capability(policy):
    weak = rel_mod.prior_reliability(0.6, 0.5, policy)
    strong = rel_mod.prior_reliability(0.95, 0.5, policy)
    assert strong > weak


# --------------------------------------------------------------------------
# Effort filtering
# --------------------------------------------------------------------------


def test_only_supported_efforts_are_enumerated(registry):
    """Haiku is documented at low/medium only; nothing may route it to max."""
    configurations = registry.enabled_configurations()
    haiku = [c for c in configurations if c[1] == "haiku_4_5"]
    assert haiku, "haiku should be routable"
    assert {c[2] for c in haiku} <= {Effort.LOW, Effort.MEDIUM}


def test_disabled_efforts_are_never_offered(registry):
    disabled = set(registry.effort_policy.disabled_efforts)
    assert disabled, "the default policy disables at least `none` and `ultra`"
    offered = {c[2] for c in registry.enabled_configurations()}
    assert not (offered & disabled)


def test_astra_does_not_offer_none(registry):
    """CodexLLM.md records that Astra starts at low."""
    astra = registry.model("codex", "astra")
    assert Effort.NONE not in astra.supported_efforts


# --------------------------------------------------------------------------
# Burn
# --------------------------------------------------------------------------


def test_base_burn_is_the_product_of_its_three_factors(registry, policy):
    provider = registry.providers["codex"]
    model = provider.models["sol"]
    expected = (
        provider.burn_weight
        * model.relative_model_burn
        * policy.effort_burn_prior[Effort.HIGH]
    )
    assert burn_mod.base_burn(provider, model, Effort.HIGH, policy) == pytest.approx(expected)


def test_effective_burn_exceeds_base_when_failure_is_possible(policy):
    estimate = burn_mod.effective_burn(
        base=2.0, predicted_reliability=0.80, expected_debug_cycles=0.6,
        escalation_reference_burn=2.0, policy=policy,
    )
    assert estimate.total > 2.0
    assert set(estimate.breakdown) == {
        "initial", "retry", "debug", "escalation", "failure_penalty"
    }


def test_a_less_reliable_configuration_costs_more_at_the_same_base(policy):
    reliable = burn_mod.effective_burn(2.0, 0.98, 0.06, 2.0, policy).total
    shaky = burn_mod.effective_burn(2.0, 0.70, 0.90, 2.0, policy).total
    assert shaky > reliable


def test_failure_cost_scales_with_what_redoing_the_work_costs(policy):
    """Being wrong about a cosmetic tweak is cheap; about a migration it is not.

    Pricing the failure penalty against the escalation reference is what stops
    the router over-powering trivial tasks out of misplaced caution.
    """
    cheap_context = burn_mod.effective_burn(0.5, 0.85, 0.45, 0.5, policy).total
    expensive_context = burn_mod.effective_burn(0.5, 0.85, 0.45, 6.0, policy).total
    assert expensive_context > cheap_context * 2


# --------------------------------------------------------------------------
# Selection
# --------------------------------------------------------------------------


def test_the_cheapest_eligible_configuration_wins(engine):
    decision = engine.route(by_name("deduplication identity semantics").fingerprint)
    eligible = [s for s in decision.evaluated if s.eligible]
    assert eligible
    cheapest = min(eligible, key=lambda s: s.predicted_burn)
    assert decision.configuration == cheapest.configuration


def test_the_winner_clears_the_bar(engine):
    for expectation in EXPECTATIONS:
        decision = engine.route(expectation.fingerprint)
        if decision.threshold_met:
            assert decision.predicted_reliability >= decision.required_reliability, (
                expectation.name
            )


def test_stronger_eligible_options_are_left_on_the_table(engine):
    """Minimum sufficient, not maximum available."""
    decision = engine.route(by_name("deduplication identity semantics").fingerprint)
    stronger_and_eligible = [
        s
        for s in decision.evaluated
        if s.eligible
        and s.predicted_reliability > decision.predicted_reliability
        and s.predicted_burn > decision.predicted_burn
    ]
    assert stronger_and_eligible, "the test is meaningless if nothing stronger existed"
    assert ReasonCode.MINIMUM_SUFFICIENT_CONFIGURATION in decision.reason_codes


def test_fallback_prefers_a_different_provider(engine):
    """Most real stalls are provider-shaped: rate limits, outages, exhausted quota."""
    decision = engine.route(by_name("deduplication identity semantics").fingerprint)
    assert decision.fallback is not None
    assert decision.fallback != decision.configuration
    providers = {s.configuration.provider for s in decision.evaluated if s.eligible}
    if len(providers) > 1:
        assert decision.fallback.provider != decision.provider


def test_no_eligible_configuration_falls_back_to_the_strongest(config):
    """When nothing clears the bar, say so rather than pretending."""
    import copy

    registry = copy.deepcopy(config.registry)
    # Leave only the weakest model routable.
    registry.providers["claude"].enabled = False
    for name, model in registry.providers["codex"].models.items():
        model.enabled = name == "luna"

    engine = RoutingEngine(registry, config.policy, config.families)
    decision = engine.route(by_name("cross-system data integrity under concurrency").fingerprint)

    assert decision.threshold_met is False
    assert ReasonCode.NO_CONFIGURATION_MEETS_THRESHOLD in decision.reason_codes
    assert decision.predicted_reliability == max(
        s.predicted_reliability for s in decision.evaluated
    )
    assert "No available configuration" in decision.explanation.summary


def test_an_unknown_task_family_is_rejected(engine):
    with pytest.raises(ValueError, match="unknown task_family"):
        engine.route(fp(task_family="vibes_based_engineering"))


# --------------------------------------------------------------------------
# History influence
# --------------------------------------------------------------------------


def test_history_moves_the_estimate(engine, policy):
    fingerprint = by_name("straightforward REST endpoint").fingerprint
    baseline = engine.route(fingerprint)
    target = baseline.configuration

    def failing(_family, provider, model, effort):
        if (provider, model, effort) == (target.provider, target.model, target.effort):
            return HistoricalEvidence(weighted_attempts=30.0, weighted_successes=9.0)
        return EMPTY_EVIDENCE

    punished = engine.route(fingerprint, failing)
    scored = next(
        s for s in punished.evaluated if s.configuration == target
    )
    assert scored.predicted_reliability < scored.prior_reliability
    assert scored.history_shift < 0


def test_one_result_cannot_swing_a_recommendation(engine, policy):
    """Rule 11: no single import may wildly alter routing."""
    fingerprint = by_name("straightforward REST endpoint").fingerprint
    baseline = engine.route(fingerprint)
    target = baseline.configuration

    def one_failure(_family, provider, model, effort):
        if (provider, model, effort) == (target.provider, target.model, target.effort):
            return HistoricalEvidence(weighted_attempts=1.0, weighted_successes=0.0)
        return EMPTY_EVIDENCE

    after = engine.route(fingerprint, one_failure)
    scored = next(s for s in after.evaluated if s.configuration == target)
    assert abs(scored.history_shift) <= policy.learning.max_history_shift + 1e-9
    assert abs(scored.history_shift) < 0.06


def test_history_shift_is_hard_capped(engine, policy):
    fingerprint = by_name("straightforward REST endpoint").fingerprint
    target = engine.route(fingerprint).configuration

    def catastrophic(_family, provider, model, effort):
        if (provider, model, effort) == (target.provider, target.model, target.effort):
            return HistoricalEvidence(weighted_attempts=5000.0, weighted_successes=0.0)
        return EMPTY_EVIDENCE

    scored = next(
        s for s in engine.route(fingerprint, catastrophic).evaluated
        if s.configuration == target
    )
    assert scored.history_shift >= -policy.learning.max_history_shift - 1e-9


def test_evidence_is_reported_on_the_decision(engine):
    fingerprint = by_name("straightforward REST endpoint").fingerprint
    target = engine.route(fingerprint).configuration

    def evidence(_family, provider, model, effort):
        if (provider, model, effort) == (target.provider, target.model, target.effort):
            return HistoricalEvidence(weighted_attempts=25.0, weighted_successes=24.0)
        return EMPTY_EVIDENCE

    decision = engine.route(fingerprint, evidence)
    winner = next(s for s in decision.evaluated if s.configuration == target)
    if decision.configuration == target:
        assert decision.evidence_weight == pytest.approx(25.0)
        assert winner.observed_success_rate == pytest.approx(0.96)


# --------------------------------------------------------------------------
# Confidence
# --------------------------------------------------------------------------


def test_ambiguity_lowers_confidence(engine):
    clear = engine.route(fp(ambiguity=0.05, confidence=0.95))
    vague = engine.route(fp(ambiguity=0.85, confidence=0.5))
    assert vague.confidence < clear.confidence


def test_confidence_stays_in_range(engine):
    for expectation in EXPECTATIONS:
        decision = engine.route(expectation.fingerprint)
        assert 0.0 <= decision.confidence <= 1.0, expectation.name


# --------------------------------------------------------------------------
# Explanation
# --------------------------------------------------------------------------


def test_every_explanation_block_is_populated(engine):
    for expectation in EXPECTATIONS:
        decision = engine.route(expectation.fingerprint)
        explanation = decision.explanation
        for field in ("summary", "why", "why_not_lighter", "why_not_stronger", "evidence_note"):
            assert getattr(explanation, field).strip(), f"{expectation.name}: {field} is empty"


def test_explanation_admits_when_there_is_no_history(engine):
    decision = engine.route(by_name("change button text").fingerprint)
    assert "Limited historical data" in decision.explanation.evidence_note
    assert ReasonCode.LIMITED_HISTORICAL_DATA in decision.reason_codes


def test_reason_codes_name_the_actual_risks(engine):
    decision = engine.route(by_name("duplicate payments race condition").fingerprint)
    assert ReasonCode.HIGH_CONCURRENCY_RISK in decision.reason_codes
    assert ReasonCode.HIGH_COMPLEXITY in decision.reason_codes
    assert ReasonCode.MULTI_SERVICE_SCOPE in decision.reason_codes
