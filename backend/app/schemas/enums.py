"""Closed vocabularies shared by every contract in the system.

Anything with a genuinely fixed set of values lives here. Anything that is
configuration (providers, models, task families) does NOT live here - it is
validated against the YAML registry at runtime so that the registry stays the
single source of truth.
"""

from __future__ import annotations

from enum import Enum


class StrEnum(str, Enum):
    """`str` subclass enum so values serialise as plain strings everywhere."""

    def __str__(self) -> str:  # pragma: no cover - trivial
        return str(self.value)


class Effort(StrEnum):
    """Reasoning effort levels.

    This is the union of what both providers expose. Which of them are valid
    for a given model is declared by `supported_efforts` in models.yaml, and
    which ones this installation is willing to route to is declared by
    `effort_policy.disabled_efforts`.
    """

    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    XHIGH = "xhigh"
    MAX = "max"
    ULTRA = "ultra"


#: Canonical ordering, weakest first. Used for "next lighter / next stronger"
#: reasoning in explanations, never for burn arithmetic.
EFFORT_ORDER: tuple[Effort, ...] = (
    Effort.NONE,
    Effort.LOW,
    Effort.MEDIUM,
    Effort.HIGH,
    Effort.XHIGH,
    Effort.MAX,
    Effort.ULTRA,
)


class Scope(StrEnum):
    """How much surface area the task touches."""

    SINGLE_LINE = "single_line"
    SINGLE_FUNCTION = "single_function"
    SINGLE_FILE = "single_file"
    MULTI_FILE = "multi_file"
    SERVICE = "service"
    MULTI_SERVICE = "multi_service"
    ARCHITECTURE = "architecture"


class TestIntensity(StrEnum):
    """Expected test effort the task implies."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class TaskStatus(StrEnum):
    """Lifecycle of a task inside the router."""

    CREATED = "created"
    ANALYZED = "analyzed"
    ROUTED = "routed"
    EXECUTED = "executed"


class OutcomeStatus(StrEnum):
    """Top-level outcome reported by the outcome skill."""

    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"


class ActualComplexity(StrEnum):
    """The complexity the executing agent observed, after the fact."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    VERY_HIGH = "very_high"


class RecommendationFit(StrEnum):
    """The executing agent's own read on whether the route was right.

    Lower trust than the objective signals in the receipt. The learning engine
    weights it accordingly.
    """

    UNDERPOWERED = "underpowered"
    APPROPRIATE = "appropriate"
    POTENTIALLY_OVERPOWERED = "potentially_overpowered"
    INCONCLUSIVE = "inconclusive"


class UsageSource(StrEnum):
    """Where token usage numbers came from.

    There is no "estimated" member on purpose. If the provider did not report
    usage, the numbers stay null and the source stays `unavailable`. Inventing
    token counts would poison the learning data permanently.
    """

    UNAVAILABLE = "unavailable"
    PROVIDER_REPORTED = "provider_reported"


class EvidenceBand(StrEnum):
    """How much historical evidence backs a statistic."""

    NONE = "none"
    WEAK = "weak"
    MODERATE = "moderate"
    STRONG = "strong"


class ReasonCode(StrEnum):
    """Deterministic, machine-readable justifications for a routing decision.

    The human-readable explanation is rendered from these plus the fingerprint;
    no second LLM call is involved in explaining a recommendation.
    """

    # Risk drivers
    HIGH_DATA_INTEGRITY_RISK = "HIGH_DATA_INTEGRITY_RISK"
    HIGH_REGRESSION_RISK = "HIGH_REGRESSION_RISK"
    HIGH_SECURITY_RISK = "HIGH_SECURITY_RISK"
    HIGH_CONCURRENCY_RISK = "HIGH_CONCURRENCY_RISK"
    HIGH_ARCHITECTURE_REASONING = "HIGH_ARCHITECTURE_REASONING"
    HIGH_COMPLEXITY = "HIGH_COMPLEXITY"
    LOW_COMPLEXITY = "LOW_COMPLEXITY"
    MULTI_FILE_SCOPE = "MULTI_FILE_SCOPE"
    MULTI_SERVICE_SCOPE = "MULTI_SERVICE_SCOPE"
    NARROW_SCOPE = "NARROW_SCOPE"
    DEEP_REPOSITORY_UNDERSTANDING = "DEEP_REPOSITORY_UNDERSTANDING"

    # Requirement quality
    HIGH_REQUIREMENT_CLARITY = "HIGH_REQUIREMENT_CLARITY"
    AMBIGUOUS_REQUIREMENTS = "AMBIGUOUS_REQUIREMENTS"

    # Selection drivers
    MINIMUM_SUFFICIENT_CONFIGURATION = "MINIMUM_SUFFICIENT_CONFIGURATION"
    LIGHTER_BELOW_RELIABILITY_THRESHOLD = "LIGHTER_BELOW_RELIABILITY_THRESHOLD"
    STRONGER_NOT_WORTH_BURN = "STRONGER_NOT_WORTH_BURN"
    NO_CONFIGURATION_MEETS_THRESHOLD = "NO_CONFIGURATION_MEETS_THRESHOLD"
    BEST_AVAILABLE_FALLBACK = "BEST_AVAILABLE_FALLBACK"

    # Evidence
    HISTORICAL_SUCCESS = "HISTORICAL_SUCCESS"
    HISTORICAL_UNDERPERFORMANCE = "HISTORICAL_UNDERPERFORMANCE"
    HISTORICAL_OVERPOWERED_PATTERN = "HISTORICAL_OVERPOWERED_PATTERN"
    LIMITED_HISTORICAL_DATA = "LIMITED_HISTORICAL_DATA"

    # Analyzer quality
    LOW_ANALYZER_CONFIDENCE = "LOW_ANALYZER_CONFIDENCE"
