"""Difficulty and capability scoring.

Pure functions. No database, no I/O, no randomness. Given the same fingerprint
and policy they return the same numbers forever, which is what makes the whole
router reproducible.
"""

from __future__ import annotations

from ...schemas.model_registry import CAPABILITY_DIMENSIONS, ModelEntry
from ...schemas.routing_policy import RoutingPolicy
from ...schemas.task_fingerprint import TaskFingerprint


def fingerprint_dimension(fp: TaskFingerprint, name: str, policy: RoutingPolicy) -> float:
    """Read one named dimension off a fingerprint as a 0..1 number.

    `scope` and `clarity_gap` are derived rather than stored, and
    `debugging_pressure` is synthesised, so they are resolved here rather than
    forcing every caller to special-case them.
    """
    if name == "scope":
        return policy.scope_score(fp.scope.value)
    if name == "clarity_gap":
        return fp.clarity_gap
    if name == "debugging_pressure":
        return fp.debugging_pressure
    value = getattr(fp, name, None)
    if value is None:
        raise KeyError(f"fingerprint has no dimension named {name!r}")
    return float(value)


def compute_difficulty(
    fp: TaskFingerprint, policy: RoutingPolicy
) -> tuple[float, dict[str, float]]:
    """Weighted blend of the fingerprint into a single 0..1 difficulty.

    Returns the difficulty and the per-dimension contributions, so the UI can
    say *why* a task is considered hard rather than only *that* it is.
    """
    contributions: dict[str, float] = {}
    total = 0.0
    for name, weight in policy.difficulty_weights.items():
        contribution = fingerprint_dimension(fp, name, policy) * weight
        contributions[name] = contribution
        total += contribution
    # difficulty_weights is validated to sum to 1.0, so total is already 0..1.
    return _clamp(total, 0.0, 1.0), contributions


def capability_match(
    fp: TaskFingerprint, model: ModelEntry, policy: RoutingPolicy
) -> float:
    """Blend a model's capability priors using weights the task itself implies.

    A database-heavy task makes the database dimension dominate; a cosmetic
    task makes it almost irrelevant. Raw coding ability always carries a fixed
    base weight, because no coding task is unaffected by it.
    """
    numerator = 0.0
    denominator = 0.0
    for dimension in CAPABILITY_DIMENSIONS:
        spec = policy.capability_weights[dimension]
        weight = spec.base_weight
        if spec.driver is not None:
            driver_value = fingerprint_dimension(fp, spec.driver, policy)
            weight += driver_value * policy.capability_driver_scale
        if weight <= 0.0:
            continue
        numerator += model.capability_priors[dimension] * weight
        denominator += weight

    if denominator <= 0.0:
        # Unreachable while `coding` has a positive base_weight, but a config
        # edit could make it so; fall back to raw coding ability.
        return model.capability_priors["coding"]
    return _clamp(numerator / denominator, 0.0, 1.0)


def apply_effort(capability: float, effort_gain: float, affinity: float = 0.0) -> float:
    """Move capability toward (or away from) its ceiling.

    `capability + gain * (1 - capability)` is deliberately sub-linear: effort
    buys a fraction of the remaining headroom, so a model with a low base
    capability can never buy its way up to a strong model's level. That is the
    property that stops the router recommending a cheap model at max effort as
    a substitute for competence.

    The same formula handles below-medium effort, where the gain is negative:
    capability moves away from the ceiling in proportion to the headroom it had.
    That is the right shape, because reduced reasoning costs most when the model
    was relying on reasoning to cover a gap, and costs a near-perfect model very
    little. Using one expression for both signs also keeps the transform
    monotonic in effort across the whole range.
    """
    gain = effort_gain + affinity
    adjusted = capability + gain * (1.0 - capability)
    return _clamp(adjusted, 0.0, 0.999)


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))
