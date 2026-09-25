"""Routing sanity across thirty reference tasks.

These assert BANDS, not exact models. "A cosmetic tweak must not reach for a
premium model at max effort" is a property of a correct router; "this task must
select Sol High" is a property of one particular tuning, and pinning it would
make every legitimate registry adjustment look like a regression.

The one thing these tests are strict about is the product philosophy:
underpowering high-risk work and overpowering trivial work must both fail.
"""

from __future__ import annotations

import pytest

from app.schemas.enums import Effort

from .fixtures import EXPECTATIONS, LIGHT_MODELS, PREMIUM_MODELS, by_name


@pytest.mark.parametrize("expectation", EXPECTATIONS, ids=lambda e: e.name)
def test_reference_task_lands_in_its_band(engine, expectation):
    decision = engine.route(expectation.fingerprint)
    where = (
        f"{expectation.name}: got {decision.provider}/{decision.model}/"
        f"{decision.effort.value} at burn {decision.predicted_burn:.2f}"
    )

    if expectation.forbidden_models:
        assert decision.model not in expectation.forbidden_models, where
    if expectation.forbidden_efforts:
        assert decision.effort.value not in expectation.forbidden_efforts, where
    if expectation.allowed_models:
        assert decision.model in expectation.allowed_models, where
    if expectation.max_burn is not None:
        assert decision.predicted_burn <= expectation.max_burn, where
    if expectation.min_burn is not None:
        assert decision.predicted_burn >= expectation.min_burn, where


def test_harder_tasks_cost_more_than_easier_ones(engine):
    """The central ordering property of the whole product."""
    trivial = engine.route(by_name("change button text").fingerprint)
    routine = engine.route(by_name("straightforward REST endpoint").fingerprint)
    hard = engine.route(by_name("deduplication identity semantics").fingerprint)
    nasty = engine.route(by_name("duplicate payments race condition").fingerprint)

    burns = [
        trivial.predicted_burn,
        routine.predicted_burn,
        hard.predicted_burn,
        nasty.predicted_burn,
    ]
    assert burns == sorted(burns), (
        "expected monotonically increasing burn across the difficulty ladder, got "
        f"{[round(b, 2) for b in burns]}"
    )


def test_required_reliability_rises_across_the_ladder(engine):
    trivial = engine.route(by_name("change button text").fingerprint)
    routine = engine.route(by_name("straightforward REST endpoint").fingerprint)
    hard = engine.route(by_name("deduplication identity semantics").fingerprint)
    nasty = engine.route(by_name("duplicate payments race condition").fingerprint)

    bars = [
        trivial.required_reliability,
        routine.required_reliability,
        hard.required_reliability,
        nasty.required_reliability,
    ]
    assert bars == sorted(bars)


def test_prompt_length_does_not_drive_cost(engine):
    """A 120-file mechanical rename must stay cheaper than a 5-file hard bug.

    This is the property the analyzer prompt works hardest to get right, and
    the one that would quietly waste the most quota if the router got it wrong.
    """
    huge_but_simple = engine.route(by_name("very long but very simple task").fingerprint)
    small_but_hard = engine.route(by_name("deduplication identity semantics").fingerprint)
    assert huge_but_simple.predicted_burn < small_but_hard.predicted_burn


def test_clarity_plus_difficulty_does_not_force_max(engine):
    """High complexity with perfect requirements is demanding, not open-ended.

    Maximum reasoning is for tasks where the model must search, not for tasks
    that are merely large and precisely specified.
    """
    decision = engine.route(by_name("high complexity with perfect clarity").fingerprint)
    assert decision.effort not in (Effort.MAX, Effort.ULTRA)


def test_no_trivial_task_reaches_a_premium_model(engine):
    for name in (
        "change button text",
        "change CSS padding",
        "documentation update",
        "mechanical rename across 40 files",
    ):
        decision = engine.route(by_name(name).fingerprint)
        assert decision.model not in PREMIUM_MODELS, f"{name} -> {decision.model}"


def test_narrow_trivial_tasks_do_not_buy_heavy_reasoning(engine):
    """A one-line change has nothing for extra reasoning to chew on."""
    for name in ("change button text", "change CSS padding", "documentation update"):
        decision = engine.route(by_name(name).fingerprint)
        assert decision.effort not in (Effort.XHIGH, Effort.MAX, Effort.ULTRA), (
            f"{name} -> {decision.effort.value}"
        )


def test_a_wide_mechanical_task_buys_care_rather_than_capability(engine):
    """Forty files of mechanical edits need thoroughness, not intelligence.

    Spending effort on the cheapest model is the right trade here and is what
    the burn arithmetic says: Luna at xHigh costs 0.25 base against 1.00 for
    the next model tier at its cheapest effort. What must NOT happen is paying
    for a stronger model, so the assertion is on cost and tier, not on effort.
    """
    decision = engine.route(by_name("mechanical rename across 40 files").fingerprint)
    hard = engine.route(by_name("deduplication identity semantics").fingerprint)

    assert decision.model not in PREMIUM_MODELS
    assert decision.predicted_burn < hard.predicted_burn / 3, (
        f"a mechanical rename should cost far less than a data-integrity fix, got "
        f"{decision.predicted_burn:.2f} against {hard.predicted_burn:.2f}"
    )


def test_no_high_risk_task_is_left_on_a_light_model(engine):
    for name in (
        "duplicate payments race condition",
        "authentication change",
        "cross-system data integrity under concurrency",
        "deduplication identity semantics",
    ):
        decision = engine.route(by_name(name).fingerprint)
        assert decision.model not in LIGHT_MODELS, f"{name} -> {decision.model}"
        assert decision.effort not in (Effort.NONE, Effort.LOW), name


def test_read_only_work_is_not_charged_for_regression_risk(engine):
    """Analysis and design produce no code, so the bar should stay moderate."""
    analysis = engine.route(by_name("repository analysis").fingerprint)
    integrity = engine.route(by_name("deduplication identity semantics").fingerprint)
    assert analysis.required_reliability < integrity.required_reliability


def test_every_reference_task_produces_a_usable_decision(engine):
    for expectation in EXPECTATIONS:
        decision = engine.route(expectation.fingerprint)
        assert decision.provider and decision.model, expectation.name
        assert 0.0 <= decision.predicted_reliability <= 1.0
        assert decision.predicted_burn > 0.0
        assert decision.evaluated, expectation.name
        assert decision.explanation.summary


def test_the_registry_offers_a_real_spread(registry):
    """If every configuration cost the same, the router would be pointless."""
    configurations = registry.enabled_configurations()
    assert len(configurations) >= 20
    assert len({c[0] for c in configurations}) >= 2, "need at least two providers"
