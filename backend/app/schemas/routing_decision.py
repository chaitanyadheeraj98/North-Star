"""RoutingDecision - what the deterministic engine emits.

Nothing in here is produced by an LLM. Every field is computed from the
fingerprint, the model registry, the routing policy and the recorded history.
Same inputs, same output, always.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from .enums import Effort, EvidenceBand, ReasonCode


class Configuration(BaseModel):
    """A provider / model / effort triple. The unit the router selects."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: str
    model: str
    effort: Effort

    def key(self) -> str:
        return f"{self.provider}/{self.model}/{self.effort.value}"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.key()


class ConfigurationScore(BaseModel):
    """Full evaluation of one configuration against one fingerprint.

    Every intermediate quantity is retained so the explanation generator and
    the UI can justify the decision without recomputing anything.
    """

    model_config = ConfigDict(extra="forbid")

    configuration: Configuration
    provider_display: str
    model_display: str

    capability_match: float = Field(
        ge=0.0, le=1.0, description="Fingerprint-weighted blend of the capability priors."
    )
    capability_effective: float = Field(
        ge=0.0, le=1.0, description="Capability after the effort adjustment."
    )

    prior_reliability: float = Field(
        ge=0.0, le=1.0, description="Reliability from base policy alone, before history."
    )
    predicted_reliability: float = Field(
        ge=0.0, le=1.0, description="Reliability after the Beta posterior update."
    )
    history_shift: float = Field(
        description="predicted_reliability - prior_reliability. Clamped by policy."
    )

    base_burn: float = Field(
        ge=0.0, description="provider weight x model burn x effort burn prior."
    )
    predicted_burn: float = Field(
        ge=0.0, description="Expected effective burn including retry/debug/escalation/failure."
    )
    burn_breakdown: dict[str, float] = Field(default_factory=dict)

    expected_debug_cycles: float = Field(ge=0.0)

    eligible: bool = Field(description="Meets or exceeds the required reliability.")

    evidence_weight: float = Field(
        ge=0.0, description="Recency-weighted execution count backing this configuration."
    )
    evidence_band: EvidenceBand = EvidenceBand.NONE
    observed_success_rate: float | None = Field(
        default=None, ge=0.0, le=1.0, description="Raw recency-weighted success rate, if any."
    )


class RiskProfile(BaseModel):
    """Derived risk view of the task, shown to the user instead of raw scores."""

    model_config = ConfigDict(extra="forbid")

    difficulty: float = Field(ge=0.0, le=1.0)
    difficulty_contributions: dict[str, float] = Field(default_factory=dict)
    required_reliability: float = Field(ge=0.0, le=1.0)
    required_reliability_contributions: dict[str, float] = Field(default_factory=dict)
    dominant_risks: list[str] = Field(default_factory=list)


class Explanation(BaseModel):
    """Deterministically generated prose. No LLM call."""

    model_config = ConfigDict(extra="forbid")

    summary: str
    why: str
    why_not_lighter: str
    why_not_stronger: str
    evidence_note: str
    reason_codes: list[ReasonCode] = Field(default_factory=list)


class RoutingDecision(BaseModel):
    """The recommendation, plus everything needed to defend it."""

    model_config = ConfigDict(extra="forbid")

    router_version: str
    registry_version: str

    provider: str
    model: str
    effort: Effort

    provider_display: str
    model_display: str

    confidence: float = Field(ge=0.0, le=1.0)

    required_reliability: float = Field(ge=0.0, le=1.0)
    predicted_reliability: float = Field(ge=0.0, le=1.0)
    predicted_burn: float = Field(ge=0.0)

    risk: RiskProfile
    explanation: Explanation
    reason_codes: list[ReasonCode] = Field(default_factory=list)

    fallback: Configuration | None = Field(
        default=None,
        description="Next best option if the primary route is unavailable or stalls.",
    )
    fallback_display: str | None = None

    #: Nearby configurations that were rejected, for the UI and for auditing.
    rejected_lighter: ConfigurationScore | None = None
    rejected_stronger: ConfigurationScore | None = None

    #: Full enumeration, ordered by effective burn ascending. Kept so the
    #: decision is fully reproducible and inspectable after the fact.
    evaluated: list[ConfigurationScore] = Field(default_factory=list)

    threshold_met: bool = Field(
        default=True,
        description="False when nothing cleared the bar and the strongest option was taken.",
    )
    evidence_band: EvidenceBand = EvidenceBand.NONE
    evidence_weight: float = 0.0

    @property
    def configuration(self) -> Configuration:
        return Configuration(provider=self.provider, model=self.model, effort=self.effort)
