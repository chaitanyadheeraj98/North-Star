"""Deterministic explanation of a routing decision.

No second LLM call. Everything the user reads is rendered from the reason
codes, the fingerprint and the scores that were already computed. Two reasons:
an explanation produced by a model could disagree with the arithmetic that
actually made the decision, and spending quota to explain a decision about
saving quota would be absurd.
"""

from __future__ import annotations

from ...schemas.enums import EvidenceBand, ReasonCode, Scope
from ...schemas.model_registry import TaskFamilyRegistry
from ...schemas.routing_decision import ConfigurationScore, Explanation
from ...schemas.routing_policy import RoutingPolicy
from ...schemas.task_fingerprint import TaskFingerprint

#: Fingerprint score above which a risk dimension is called out by name.
_HIGH = 0.70
_LOW = 0.25

_RISK_LABELS: dict[str, str] = {
    "regression_risk": "regression risk",
    "database_reasoning": "data-model reasoning",
    "concurrency_risk": "concurrency risk",
    "security_risk": "security exposure",
    "architecture_reasoning": "architectural reasoning",
    "repository_understanding": "existing-codebase understanding",
    "complexity": "reasoning complexity",
}


def collect_reason_codes(
    fp: TaskFingerprint,
    winner: ConfigurationScore,
    lighter: ConfigurationScore | None,
    stronger: ConfigurationScore | None,
    threshold_met: bool,
    policy: RoutingPolicy,
) -> list[ReasonCode]:
    """Machine-readable justifications, in a stable order."""
    codes: list[ReasonCode] = []

    if fp.database_reasoning >= _HIGH:
        codes.append(ReasonCode.HIGH_DATA_INTEGRITY_RISK)
    if fp.regression_risk >= _HIGH:
        codes.append(ReasonCode.HIGH_REGRESSION_RISK)
    if fp.security_risk >= _HIGH:
        codes.append(ReasonCode.HIGH_SECURITY_RISK)
    if fp.concurrency_risk >= _HIGH:
        codes.append(ReasonCode.HIGH_CONCURRENCY_RISK)
    if fp.architecture_reasoning >= _HIGH:
        codes.append(ReasonCode.HIGH_ARCHITECTURE_REASONING)
    if fp.repository_understanding >= _HIGH:
        codes.append(ReasonCode.DEEP_REPOSITORY_UNDERSTANDING)

    if fp.complexity >= _HIGH:
        codes.append(ReasonCode.HIGH_COMPLEXITY)
    elif fp.complexity <= _LOW:
        codes.append(ReasonCode.LOW_COMPLEXITY)

    if fp.scope in (Scope.MULTI_SERVICE, Scope.ARCHITECTURE):
        codes.append(ReasonCode.MULTI_SERVICE_SCOPE)
    elif fp.scope in (Scope.MULTI_FILE, Scope.SERVICE):
        codes.append(ReasonCode.MULTI_FILE_SCOPE)
    elif fp.scope in (Scope.SINGLE_LINE, Scope.SINGLE_FUNCTION):
        codes.append(ReasonCode.NARROW_SCOPE)

    if fp.requirements_clarity >= 0.80 and fp.ambiguity <= 0.30:
        codes.append(ReasonCode.HIGH_REQUIREMENT_CLARITY)
    if fp.ambiguity >= 0.55 or fp.requirements_clarity <= 0.45:
        codes.append(ReasonCode.AMBIGUOUS_REQUIREMENTS)
    if fp.confidence < 0.60:
        codes.append(ReasonCode.LOW_ANALYZER_CONFIDENCE)

    if threshold_met:
        codes.append(ReasonCode.MINIMUM_SUFFICIENT_CONFIGURATION)
        if lighter is not None:
            codes.append(ReasonCode.LIGHTER_BELOW_RELIABILITY_THRESHOLD)
        if stronger is not None:
            gain = stronger.predicted_reliability - winner.predicted_reliability
            if gain < policy.explanation.meaningful_reliability_gain:
                codes.append(ReasonCode.STRONGER_NOT_WORTH_BURN)
    else:
        codes.append(ReasonCode.NO_CONFIGURATION_MEETS_THRESHOLD)
        codes.append(ReasonCode.BEST_AVAILABLE_FALLBACK)

    if winner.evidence_band is EvidenceBand.NONE:
        codes.append(ReasonCode.LIMITED_HISTORICAL_DATA)
    elif winner.history_shift > 0.005:
        codes.append(ReasonCode.HISTORICAL_SUCCESS)
    elif winner.history_shift < -0.005:
        codes.append(ReasonCode.HISTORICAL_UNDERPERFORMANCE)

    return codes


def dominant_risks(fp: TaskFingerprint, contributions: dict[str, float]) -> list[str]:
    """The dimensions that actually drove the difficulty score, largest first."""
    ranked = sorted(contributions.items(), key=lambda kv: kv[1], reverse=True)
    out: list[str] = []
    for name, contribution in ranked:
        if contribution < 0.03:
            break
        label = _RISK_LABELS.get(name)
        if label:
            out.append(label)
        if len(out) == 3:
            break
    return out


def build_explanation(
    fp: TaskFingerprint,
    families: TaskFamilyRegistry,
    winner: ConfigurationScore,
    lighter: ConfigurationScore | None,
    stronger: ConfigurationScore | None,
    required_reliability: float,
    difficulty_contributions: dict[str, float],
    threshold_met: bool,
    reason_codes: list[ReasonCode],
    policy: RoutingPolicy,
) -> Explanation:
    """Render the four blocks of prose the Router page shows."""
    cfg = winner.configuration
    family_label = families.label(fp.task_family)
    config_label = f"{winner.provider_display} {winner.model_display} at {cfg.effort.value} effort"

    summary = (
        f"{config_label} is the least demanding configuration that still clears the "
        f"{_pct(required_reliability)} reliability this task requires."
    )
    if not threshold_met:
        summary = (
            f"No available configuration reaches the {_pct(required_reliability)} reliability "
            f"this task requires. {config_label} is the strongest option available and is "
            f"predicted at {_pct(winner.predicted_reliability)}."
        )

    why = _build_why(fp, family_label, required_reliability, difficulty_contributions)
    why_not_lighter = _build_why_not_lighter(lighter, required_reliability, threshold_met)
    why_not_stronger = _build_why_not_stronger(winner, stronger, policy, threshold_met)
    evidence_note = _build_evidence_note(winner, policy)

    return Explanation(
        summary=summary,
        why=why,
        why_not_lighter=why_not_lighter,
        why_not_stronger=why_not_stronger,
        evidence_note=evidence_note,
        reason_codes=reason_codes,
    )


def _build_why(
    fp: TaskFingerprint,
    family_label: str,
    required_reliability: float,
    contributions: dict[str, float],
) -> str:
    risks = dominant_risks(fp, contributions)
    parts = [f"This is a {family_label.lower()} task with {_scope_phrase(fp.scope)}"]
    if fp.estimated_files:
        parts[0] += f" across roughly {fp.estimated_files} file{'s' if fp.estimated_files != 1 else ''}"
    parts[0] += "."

    if risks:
        parts.append(
            "The reliability bar is driven mainly by " + _join(risks) + "."
        )
    else:
        parts.append("None of the risk dimensions scored highly, so the bar stays near the floor.")

    if fp.requirements_clarity >= 0.80:
        parts.append(
            "Requirements are stated precisely, so the work is demanding rather than open-ended."
        )
    elif fp.ambiguity >= 0.55:
        parts.append(
            "The request leaves significant room for interpretation, which raises the chance of "
            "solving the wrong problem correctly."
        )

    parts.append(f"That puts the required reliability at {_pct(required_reliability)}.")
    return " ".join(parts)


def _build_why_not_lighter(
    lighter: ConfigurationScore | None, required_reliability: float, threshold_met: bool
) -> str:
    if not threshold_met:
        return (
            "Every lighter configuration falls further below the bar than the one selected."
        )
    if lighter is None:
        return (
            "Nothing cheaper is available: this is already the least expensive routable "
            "configuration."
        )
    cfg = lighter.configuration
    return (
        f"{lighter.provider_display} {lighter.model_display} at {cfg.effort.value} effort is the "
        f"next cheaper option, but it is predicted at {_pct(lighter.predicted_reliability)} "
        f"against a {_pct(required_reliability)} requirement. On this task the expected cost of "
        f"retries, debugging and escalation exceeds what the stronger route costs outright."
    )


def _build_why_not_stronger(
    winner: ConfigurationScore,
    stronger: ConfigurationScore | None,
    policy: RoutingPolicy,
    threshold_met: bool,
) -> str:
    if not threshold_met:
        return "Nothing stronger is available; this is the most capable routable configuration."
    if stronger is None:
        return (
            "Nothing stronger is available, though the selected configuration already clears "
            "the requirement."
        )

    cfg = stronger.configuration
    gain = stronger.predicted_reliability - winner.predicted_reliability
    extra = stronger.predicted_burn - winner.predicted_burn
    label = f"{stronger.provider_display} {stronger.model_display} at {cfg.effort.value} effort"

    if gain < policy.explanation.meaningful_reliability_gain:
        return (
            f"{label} costs an estimated {extra:+.2f} more in effective burn and buys "
            f"{gain * 100:.1f} reliability points. The selected configuration already exceeds the "
            f"requirement, so the extra reasoning would be spent on certainty the task does not need."
        )
    return (
        f"{label} would reach {_pct(stronger.predicted_reliability)} rather than "
        f"{_pct(winner.predicted_reliability)}, but costs an estimated {extra:+.2f} more in "
        f"effective burn. The selected configuration is already above the requirement, and the "
        f"router buys the minimum sufficient reliability rather than the maximum available."
    )


def _build_evidence_note(winner: ConfigurationScore, policy: RoutingPolicy) -> str:
    if winner.evidence_weight < policy.confidence.low_evidence_threshold:
        return (
            "Limited historical data - this recommendation rests primarily on base routing "
            "policy rather than on your recorded outcomes. Import execution receipts to change that."
        )

    rate = winner.observed_success_rate
    band = winner.evidence_band.value
    rate_text = f", succeeding {_pct(rate)} of the time" if rate is not None else ""
    direction = ""
    if winner.history_shift > 0.005:
        direction = " This pushed the estimate up."
    elif winner.history_shift < -0.005:
        direction = " This pulled the estimate down."
    return (
        f"Backed by {winner.evidence_weight:.1f} recency-weighted executions on this task family "
        f"({band} evidence){rate_text}.{direction}"
    )


def _scope_phrase(scope: Scope) -> str:
    return {
        Scope.SINGLE_LINE: "a single-line change",
        Scope.SINGLE_FUNCTION: "a single-function change",
        Scope.SINGLE_FILE: "a single-file change",
        Scope.MULTI_FILE: "a multi-file change",
        Scope.SERVICE: "a service-wide change",
        Scope.MULTI_SERVICE: "a change spanning several services",
        Scope.ARCHITECTURE: "an architecture-level change",
    }[scope]


def _join(items: list[str]) -> str:
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return ", ".join(items[:-1]) + f" and {items[-1]}"


def _pct(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value * 100:.1f}%"
