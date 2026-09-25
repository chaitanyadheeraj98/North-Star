"""How reliable a configuration has to be before it may be recommended.

Kept separate from `scoring` because this is the policy question ("how sure do
we need to be?") rather than the measurement question ("how good is this
model?"). They move for different reasons and are tuned by different numbers.
"""

from __future__ import annotations

from ...schemas.routing_policy import RoutingPolicy
from ...schemas.task_fingerprint import TaskFingerprint
from .scoring import fingerprint_dimension


def compute_required_reliability(
    fp: TaskFingerprint, policy: RoutingPolicy, family_adjustment: float
) -> tuple[float, dict[str, float]]:
    """Derive the reliability bar from the task's own risk profile.

    Blended from the fingerprint dimensions rather than looked up by task
    family, because two tasks in the same family can carry wildly different
    consequences: a cosmetic tweak and a rename that breaks a public contract
    are both `ui_cosmetic` right up until one of them is not. The family only
    contributes a small additive nudge from task_families.yaml.

    Ambiguity deliberately does NOT raise the bar. An ambiguous task is not
    more dangerous to get wrong, it is harder to know whether you got it right,
    which is a confidence problem rather than a reliability-requirement problem.
    """
    cfg = policy.required_reliability
    contributions: dict[str, float] = {"base": cfg.base}
    total = cfg.base

    for name, coefficient in cfg.risk_coefficients.items():
        contribution = fingerprint_dimension(fp, name, policy) * coefficient
        contributions[name] = contribution
        total += contribution

    contributions["task_family"] = family_adjustment
    total += family_adjustment

    clamped = max(cfg.min, min(cfg.max, total))
    contributions["clamped_to"] = clamped
    return clamped, contributions
