"""Effective burn: what a configuration is really expected to cost.

The point of the whole application is that the cheapest configuration is
frequently not the cheapest outcome. A model that is slightly too weak gets
retried, debugged, corrected and finally escalated, and the sum of that is
often several times what starting one tier higher would have cost.

Units are normalised quota units, not currency. This is a subscription tool:
optimising dollars would be optimising the wrong thing.

Measured usage is recorded separately and never mixed into these estimates. If
a provider did not report tokens, this module does not invent them.
"""

from __future__ import annotations

from dataclasses import dataclass

from ...schemas.enums import Effort
from ...schemas.model_registry import ModelEntry, ProviderEntry
from ...schemas.routing_policy import RoutingPolicy
from .reliability import HistoricalEvidence


@dataclass(frozen=True)
class BurnEstimate:
    total: float
    breakdown: dict[str, float]


def base_burn(
    provider: ProviderEntry, model: ModelEntry, effort: Effort, policy: RoutingPolicy
) -> float:
    """Expected first-attempt cost, before anything goes wrong.

        provider exchange rate x model intensity x effort intensity

    The effort term is a routing prior. Neither provider publishes a fixed
    token multiplier per effort level, and this code must never imply one.
    """
    return (
        provider.burn_weight
        * model.relative_model_burn
        * policy.effort_burn_prior[effort]
    )


def effective_burn(
    base: float,
    predicted_reliability: float,
    expected_debug_cycles: float,
    escalation_reference_burn: float,
    policy: RoutingPolicy,
    evidence: HistoricalEvidence | None = None,
) -> BurnEstimate:
    """Total expected cost of choosing this configuration.

        initial   the first attempt
      + retry     a second attempt at the same configuration
      + debug     iteration cycles on top of the attempt
      + escalate  the chance of having to move to something stronger
      + penalty   the residual cost of a failure that is not cleanly recovered

    `escalation_reference_burn` is the cheapest base burn among configurations
    that actually clear the reliability bar, i.e. what redoing the work
    properly costs. Pricing both the escalation term and the failure penalty
    against that reference is what keeps the router honest at both ends: being
    wrong about a cosmetic change is genuinely cheap, so cheap configurations
    are not punished into oblivion, while being wrong about a migration is
    expensive, so the router will happily pay up front to avoid it.
    """
    cfg = policy.effective_burn
    p_fail = max(0.0, 1.0 - predicted_reliability)

    initial = base
    retry = cfg.retry_cost_multiplier * p_fail * base
    debug = cfg.debug_overhead_per_cycle * expected_debug_cycles * base

    escalation_rate = cfg.escalation_probability
    if evidence is not None and evidence.weighted_attempts > 0:
        # Observed escalation frequency, shrunk toward the policy rate so that
        # a single escalation does not rewrite the cost model.
        observed = evidence.weighted_escalations / evidence.weighted_attempts
        strength = policy.learning.prior_strength
        weight = evidence.weighted_attempts / (evidence.weighted_attempts + strength)
        escalation_rate = (1.0 - weight) * escalation_rate + weight * observed

    escalation = escalation_rate * p_fail * escalation_reference_burn
    penalty = cfg.failure_penalty_weight * p_fail * escalation_reference_burn

    total = initial + retry + debug + escalation + penalty
    return BurnEstimate(
        total=total,
        breakdown={
            "initial": round(initial, 4),
            "retry": round(retry, 4),
            "debug": round(debug, 4),
            "escalation": round(escalation, 4),
            "failure_penalty": round(penalty, 4),
        },
    )


def blend_measured_burn(
    estimated: float, evidence: HistoricalEvidence, policy: RoutingPolicy
) -> float:
    """Pull the estimate toward what this configuration has historically cost.

    Only recorded effective burn is used here, and only where it exists. A cell
    with no burn samples is left entirely on the estimate.
    """
    observed = evidence.observed_burn
    if observed is None or observed <= 0.0:
        return estimated
    strength = policy.learning.burn_prior_strength
    weight = evidence.burn_samples / (evidence.burn_samples + strength)
    return (1.0 - weight) * estimated + weight * observed


def observed_effective_burn(
    base: float,
    *,
    succeeded: bool,
    debug_cycles: int,
    escalated: bool,
    escalation_reference_burn: float,
    user_corrections: int,
    major_replans: int,
    policy: RoutingPolicy,
) -> float:
    """Reconstruct what an execution actually cost, from the receipt.

    This is an ESTIMATE built from observed work signals, not a measurement of
    tokens. It is stored as `estimated_effective_burn` and is labelled as such
    everywhere it surfaces. When a provider reports real token usage, that is
    stored separately and is never overwritten by this number.
    """
    cfg = policy.effective_burn
    total = base
    total += cfg.debug_overhead_per_cycle * debug_cycles * base
    # A replan or a user correction is a redo of part of the work.
    total += cfg.retry_cost_multiplier * base * (0.5 * major_replans + 0.25 * user_corrections)
    if escalated:
        total += escalation_reference_burn
    if not succeeded:
        total += cfg.failure_penalty_weight * escalation_reference_burn
    return total
