"""Predicted reliability, and the Bayesian update that lets history override it.

The prior is a closed-form model of how often a configuration finishes a task
of a given difficulty correctly. The posterior blends that prior with recorded
outcomes for the same task family and configuration.

Deliberate properties:

* Reliability never reaches 1.0. Even a trivial task carries irreducible risk.
* A weak model cannot reach a strong model's reliability by spending effort.
* One recorded execution can never swing a recommendation (`max_history_shift`).
* Evidence weight rises smoothly rather than switching bands abruptly.
"""

from __future__ import annotations

from dataclasses import dataclass

from ...schemas.enums import EvidenceBand
from ...schemas.routing_policy import RoutingPolicy


@dataclass(frozen=True)
class HistoricalEvidence:
    """Recency-weighted outcomes for one (family, provider, model, effort) cell.

    Weighted rather than counted: a success from last week says more about the
    models available today than a success from last year.
    """

    weighted_attempts: float = 0.0
    weighted_successes: float = 0.0
    weighted_first_pass: float = 0.0
    weighted_escalations: float = 0.0
    weighted_debug_cycles: float = 0.0
    weighted_burn: float = 0.0
    burn_samples: float = 0.0
    raw_attempts: int = 0

    @property
    def observed_success_rate(self) -> float | None:
        if self.weighted_attempts <= 0.0:
            return None
        return self.weighted_successes / self.weighted_attempts

    @property
    def observed_debug_cycles(self) -> float | None:
        if self.weighted_attempts <= 0.0:
            return None
        return self.weighted_debug_cycles / self.weighted_attempts

    @property
    def observed_burn(self) -> float | None:
        if self.burn_samples <= 0.0:
            return None
        return self.weighted_burn / self.burn_samples


EMPTY_EVIDENCE = HistoricalEvidence()


def prior_reliability(
    capability_effective: float, difficulty: float, policy: RoutingPolicy
) -> float:
    """Closed-form reliability from the gap between capability and difficulty.

        failure = scale * deficit^a * difficulty_eff^b
        deficit = 1 - capability_effective

    Squaring the deficit (a = 2 by default) is what produces the separation the
    router needs: halving a model's shortfall quarters its predicted failure
    rate, so the difference between a competent and a very competent model
    grows rather than shrinks as tasks get harder.
    """
    cfg = policy.reliability_model
    deficit = max(0.0, 1.0 - capability_effective)
    difficulty_eff = cfg.difficulty_floor + (1.0 - cfg.difficulty_floor) * _clamp01(difficulty)

    failure = (
        cfg.scale
        * (deficit**cfg.deficit_exponent)
        * (difficulty_eff**cfg.difficulty_exponent)
    )
    return _clamp(1.0 - failure, cfg.min_reliability, cfg.max_reliability)


def posterior_reliability(
    prior: float, evidence: HistoricalEvidence, policy: RoutingPolicy
) -> float:
    """Blend the prior with recorded outcomes via a Beta-Binomial posterior.

    The prior is task-specific (it already accounts for this fingerprint's
    difficulty) while the evidence is aggregated per task family, so the
    posterior asks: given that this configuration usually succeeds at this
    family's work, how should the estimate for THIS task move?

        alpha = prior_strength * prior + weighted_successes
        beta  = prior_strength * (1 - prior) + weighted_failures
        posterior = alpha / (alpha + beta)

    `prior_strength` is the number of recency-weighted executions at which
    history carries the same weight as policy. The result is then clamped so
    that no amount of evidence can move a single recommendation further than
    `max_history_shift` in one go.
    """
    cfg = policy.learning
    if evidence.weighted_attempts <= 0.0:
        return prior

    alpha = cfg.prior_strength * prior + evidence.weighted_successes
    beta = cfg.prior_strength * (1.0 - prior) + max(
        0.0, evidence.weighted_attempts - evidence.weighted_successes
    )
    denominator = alpha + beta
    if denominator <= 0.0:  # pragma: no cover - prior_strength is validated positive
        return prior

    posterior = alpha / denominator
    shift = _clamp(posterior - prior, -cfg.max_history_shift, cfg.max_history_shift)

    model = policy.reliability_model
    return _clamp(prior + shift, model.min_reliability, model.max_reliability)


def expected_debug_cycles(
    predicted_reliability: float, evidence: HistoricalEvidence, policy: RoutingPolicy
) -> float:
    """How much back-and-forth to expect, in cycles.

    The prior is proportional to predicted failure probability. Measured debug
    cycles shrink toward that prior with the usual evidence weighting, so a
    configuration that has repeatedly needed three rounds of debugging on this
    family starts being costed as if it will.
    """
    p_fail = max(0.0, 1.0 - predicted_reliability)
    prior = p_fail * policy.effective_burn.debug_cycles_scale

    observed = evidence.observed_debug_cycles
    if observed is None:
        return prior

    strength = policy.learning.debug_cycles_prior_strength
    weight = evidence.weighted_attempts / (evidence.weighted_attempts + strength)
    return max(0.0, (1.0 - weight) * prior + weight * observed)


def evidence_band(weighted_attempts: float, policy: RoutingPolicy) -> EvidenceBand:
    """Label the amount of evidence, for the user interface only.

    Nothing in the arithmetic branches on this; the posterior already weights
    evidence smoothly. It exists so the UI can be honest about what a number
    is resting on.
    """
    bands = policy.learning.evidence_bands
    if weighted_attempts >= bands.strong:
        return EvidenceBand.STRONG
    if weighted_attempts >= bands.moderate:
        return EvidenceBand.MODERATE
    if weighted_attempts >= bands.weak:
        return EvidenceBand.WEAK
    return EvidenceBand.NONE


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _clamp01(value: float) -> float:
    return _clamp(value, 0.0, 1.0)
