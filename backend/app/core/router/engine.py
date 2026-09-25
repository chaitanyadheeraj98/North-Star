"""The routing engine.

Minimum sufficient intelligence, in eight steps:

1.  Enumerate every routable provider x model x effort configuration.
2.  Score each one's predicted reliability for this fingerprint.
3.  Derive the reliability this task actually requires.
4.  Discard configurations below that requirement.
5.  Estimate the effective burn of what remains.
6.  Take the lowest expected effective burn.
7.  If nothing clears the bar, take the most reliable option and say so.
8.  Explain the choice deterministically.

The engine is a pure function of (fingerprint, registry, policy, families,
evidence). It performs no I/O; history is passed in as an already-resolved
lookup so that the same inputs always produce the same decision and the whole
thing is trivially testable.
"""

from __future__ import annotations

from collections.abc import Callable

from ...schemas.enums import EFFORT_ORDER, Effort, EvidenceBand
from ...schemas.model_registry import ModelRegistry, TaskFamilyRegistry
from ...schemas.routing_decision import (
    Configuration,
    ConfigurationScore,
    RiskProfile,
    RoutingDecision,
)
from ...schemas.routing_policy import RoutingPolicy
from ...schemas.task_fingerprint import TaskFingerprint
from . import burn as burn_mod
from . import explain as explain_mod
from . import reliability as rel_mod
from . import scoring, thresholds
from .reliability import EMPTY_EVIDENCE, HistoricalEvidence

#: Resolves recorded evidence for one cell. Returning EMPTY_EVIDENCE is always
#: valid and means "no history", which is the state a fresh install is in.
EvidenceLookup = Callable[[str, str, str, Effort], HistoricalEvidence]


def no_evidence(_family: str, _provider: str, _model: str, _effort: Effort) -> HistoricalEvidence:
    return EMPTY_EVIDENCE


class RoutingEngine:
    """Deterministic selector. Construct once per request; it holds no state."""

    def __init__(
        self,
        registry: ModelRegistry,
        policy: RoutingPolicy,
        families: TaskFamilyRegistry,
    ) -> None:
        self.registry = registry
        self.policy = policy
        self.families = families

    # -- public API ------------------------------------------------------------

    def route(
        self,
        fingerprint: TaskFingerprint,
        evidence_lookup: EvidenceLookup = no_evidence,
    ) -> RoutingDecision:
        if not self.families.has(fingerprint.task_family):
            raise ValueError(
                f"unknown task_family {fingerprint.task_family!r}; "
                f"known families: {', '.join(self.families.names())}"
            )

        difficulty, difficulty_contributions = scoring.compute_difficulty(
            fingerprint, self.policy
        )
        required, required_contributions = thresholds.compute_required_reliability(
            fingerprint,
            self.policy,
            self.families.adjustment(fingerprint.task_family),
        )

        # Pass 1: reliability and base burn for every candidate. Effective burn
        # needs the escalation reference, which is only known once the eligible
        # set exists, so it is computed in pass 2.
        partials = self._score_all(fingerprint, difficulty, required, evidence_lookup)
        if not partials:  # pragma: no cover - config validation forbids this
            raise RuntimeError("no routable configurations; check models.yaml")

        eligible = [p for p in partials if p.score.eligible]
        threshold_met = bool(eligible)
        reference_pool = eligible if eligible else partials
        escalation_reference = min(p.score.base_burn for p in reference_pool)

        # Pass 2: effective burn.
        for partial in partials:
            estimate = burn_mod.effective_burn(
                base=partial.score.base_burn,
                predicted_reliability=partial.score.predicted_reliability,
                expected_debug_cycles=partial.score.expected_debug_cycles,
                escalation_reference_burn=escalation_reference,
                policy=self.policy,
                evidence=partial.evidence,
            )
            total = burn_mod.blend_measured_burn(
                estimate.total, partial.evidence, self.policy
            )
            partial.score.predicted_burn = total
            partial.score.burn_breakdown = estimate.breakdown

        scores = [p.score for p in partials]
        scores.sort(key=_burn_then_stable_key)

        if threshold_met:
            eligible_scores = [s for s in scores if s.eligible]
            winner = eligible_scores[0]
            fallback = self._pick_fallback(winner, eligible_scores)
        else:
            # Nothing clears the bar: take the most reliable thing available and
            # be explicit about it rather than pretending the choice was fine.
            winner = max(
                scores,
                key=lambda s: (s.predicted_reliability, -s.predicted_burn, _stable_key(s)),
            )
            remaining = [s for s in scores if s.configuration != winner.configuration]
            fallback = (
                max(
                    remaining,
                    key=lambda s: (s.predicted_reliability, -s.predicted_burn, _stable_key(s)),
                )
                if remaining
                else None
            )

        lighter = self._next_lighter(winner, scores)
        stronger = self._next_stronger(winner, scores)

        reason_codes = explain_mod.collect_reason_codes(
            fingerprint, winner, lighter, stronger, threshold_met, self.policy
        )
        explanation = explain_mod.build_explanation(
            fp=fingerprint,
            families=self.families,
            winner=winner,
            lighter=lighter,
            stronger=stronger,
            required_reliability=required,
            difficulty_contributions=difficulty_contributions,
            threshold_met=threshold_met,
            reason_codes=reason_codes,
            policy=self.policy,
        )

        confidence = self._confidence(
            fingerprint, winner, scores, required, threshold_met
        )

        fallback_display = None
        if fallback is not None:
            fallback_display = (
                f"{fallback.provider_display} {fallback.model_display} "
                f"({fallback.configuration.effort.value})"
            )

        return RoutingDecision(
            router_version=self.policy.router_version,
            registry_version=self.registry.registry_version,
            provider=winner.configuration.provider,
            model=winner.configuration.model,
            effort=winner.configuration.effort,
            provider_display=winner.provider_display,
            model_display=winner.model_display,
            confidence=confidence,
            required_reliability=required,
            predicted_reliability=winner.predicted_reliability,
            predicted_burn=winner.predicted_burn,
            risk=RiskProfile(
                difficulty=difficulty,
                difficulty_contributions={
                    k: round(v, 5) for k, v in difficulty_contributions.items()
                },
                required_reliability=required,
                required_reliability_contributions={
                    k: round(v, 5) for k, v in required_contributions.items()
                },
                dominant_risks=explain_mod.dominant_risks(
                    fingerprint, difficulty_contributions
                ),
            ),
            explanation=explanation,
            reason_codes=reason_codes,
            fallback=fallback.configuration if fallback else None,
            fallback_display=fallback_display,
            rejected_lighter=lighter,
            rejected_stronger=stronger,
            evaluated=scores,
            threshold_met=threshold_met,
            evidence_band=winner.evidence_band,
            evidence_weight=winner.evidence_weight,
        )

    # -- scoring ---------------------------------------------------------------

    def _score_all(
        self,
        fingerprint: TaskFingerprint,
        difficulty: float,
        required: float,
        evidence_lookup: EvidenceLookup,
    ) -> list[_Partial]:
        out: list[_Partial] = []
        for provider_name, model_name, effort in self.registry.enabled_configurations():
            provider = self.registry.providers[provider_name]
            model = provider.models[model_name]

            match = scoring.capability_match(fingerprint, model, self.policy)
            effective = scoring.apply_effort(
                match,
                self.policy.effort_capability_gain[effort],
                self.registry.affinity(provider_name, model_name, fingerprint.task_family),
            )

            prior = rel_mod.prior_reliability(effective, difficulty, self.policy)
            evidence = evidence_lookup(
                fingerprint.task_family, provider_name, model_name, effort
            )
            predicted = rel_mod.posterior_reliability(prior, evidence, self.policy)
            debug_cycles = rel_mod.expected_debug_cycles(predicted, evidence, self.policy)

            score = ConfigurationScore(
                configuration=Configuration(
                    provider=provider_name, model=model_name, effort=effort
                ),
                provider_display=provider.display_name,
                model_display=model.display_name,
                capability_match=match,
                capability_effective=effective,
                prior_reliability=prior,
                predicted_reliability=predicted,
                history_shift=predicted - prior,
                base_burn=burn_mod.base_burn(provider, model, effort, self.policy),
                predicted_burn=0.0,  # filled in pass 2
                expected_debug_cycles=debug_cycles,
                eligible=predicted >= required,
                evidence_weight=evidence.weighted_attempts,
                evidence_band=rel_mod.evidence_band(evidence.weighted_attempts, self.policy),
                observed_success_rate=evidence.observed_success_rate,
            )
            out.append(_Partial(score=score, evidence=evidence))
        return out

    # -- neighbours ------------------------------------------------------------

    def _next_lighter(
        self, winner: ConfigurationScore, scores: list[ConfigurationScore]
    ) -> ConfigurationScore | None:
        """The cheapest thing that was rejected for being too cheap.

        Defined as the most expensive configuration strictly cheaper than the
        winner. That is the one a sceptical user would actually have reached
        for, so it is the one worth defending against.
        """
        cheaper = [s for s in scores if s.predicted_burn < winner.predicted_burn - 1e-9]
        if not cheaper:
            return None
        return max(cheaper, key=lambda s: (s.predicted_burn, _stable_key(s)))

    def _next_stronger(
        self, winner: ConfigurationScore, scores: list[ConfigurationScore]
    ) -> ConfigurationScore | None:
        """The cheapest configuration that is genuinely more reliable.

        Only options that actually beat the winner on reliability count. A more
        expensive configuration that is not more reliable is not an upgrade, it
        is just waste, and quoting it in the explanation would be misleading.
        """
        better = [
            s
            for s in scores
            if s.predicted_reliability > winner.predicted_reliability + 1e-9
            and s.predicted_burn > winner.predicted_burn + 1e-9
        ]
        if not better:
            return None
        return min(better, key=lambda s: (s.predicted_burn, _stable_key(s)))

    def _pick_fallback(
        self, winner: ConfigurationScore, eligible: list[ConfigurationScore]
    ) -> ConfigurationScore | None:
        """A second route for when the first one stalls.

        Preference order: a different provider first, because most real stalls
        are provider-shaped (rate limit, outage, quota exhausted). Failing
        that, the next eligible option on cost.
        """
        others = [s for s in eligible if s.configuration != winner.configuration]
        if not others:
            return None
        cross_provider = [
            s for s in others if s.configuration.provider != winner.configuration.provider
        ]
        pool = cross_provider or others
        return min(pool, key=lambda s: (s.predicted_burn, _stable_key(s)))

    # -- confidence ------------------------------------------------------------

    def _confidence(
        self,
        fingerprint: TaskFingerprint,
        winner: ConfigurationScore,
        scores: list[ConfigurationScore],
        required: float,
        threshold_met: bool,
    ) -> float:
        """How much to trust the recommendation itself.

        Distinct from predicted reliability: the router can be very confident
        that a task is hard and needs an expensive model, and it can be very
        unsure about a task whose description is vague even though the chosen
        model is excellent.
        """
        cfg = self.policy.confidence
        value = cfg.base

        value += cfg.analyzer_confidence_weight * (fingerprint.confidence - 0.5) * 2.0
        value -= cfg.ambiguity_penalty * fingerprint.ambiguity

        evidence_factor = min(
            1.0,
            winner.evidence_weight / max(cfg.low_evidence_threshold * 4.0, 1e-9),
        )
        value += cfg.evidence_weight * evidence_factor

        if threshold_met:
            margin = winner.predicted_reliability - required
            value += cfg.margin_weight * min(1.0, max(0.0, margin / 0.05))

            runner_up = next(
                (
                    s
                    for s in scores
                    if s.eligible and s.configuration != winner.configuration
                ),
                None,
            )
            if runner_up is not None and runner_up.predicted_burn > 0:
                separation = (
                    runner_up.predicted_burn - winner.predicted_burn
                ) / runner_up.predicted_burn
                value += cfg.separation_weight * min(1.0, max(0.0, separation / 0.30))
            else:
                value += cfg.separation_weight * 0.5
        else:
            # The router is explicitly out of its depth here.
            value -= 0.15

        if winner.evidence_band is EvidenceBand.NONE:
            value -= 0.02

        return max(cfg.min, min(cfg.max, value))


class _Partial:
    """Mutable pairing of a score with its evidence while burn is computed."""

    __slots__ = ("score", "evidence")

    def __init__(self, score: ConfigurationScore, evidence: HistoricalEvidence) -> None:
        self.score = score
        self.evidence = evidence


def _stable_key(score: ConfigurationScore) -> tuple[str, str, int]:
    """Total ordering tiebreaker so equal-cost options never flip between runs."""
    cfg = score.configuration
    return (cfg.provider, cfg.model, EFFORT_ORDER.index(cfg.effort))


def _burn_then_stable_key(score: ConfigurationScore) -> tuple[float, float, tuple[str, str, int]]:
    # Round the burn before comparing so that floating-point noise far below
    # any meaningful difference cannot reorder two configurations.
    return (round(score.predicted_burn, 9), -round(score.predicted_reliability, 9), _stable_key(score))
