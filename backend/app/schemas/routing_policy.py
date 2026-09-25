"""Typed view over config/routing.yaml.

Every field here is an application policy prior. None of it is a provider fact.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .enums import Effort


class ReliabilityModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scale: float = Field(gt=0.0)
    deficit_exponent: float = Field(gt=0.0)
    difficulty_exponent: float = Field(gt=0.0)
    difficulty_floor: float = Field(ge=0.0, lt=1.0)
    min_reliability: float = Field(ge=0.0, lt=1.0)
    max_reliability: float = Field(gt=0.0, le=1.0)

    @model_validator(mode="after")
    def _ordered(self) -> ReliabilityModel:
        if self.min_reliability >= self.max_reliability:
            raise ValueError("min_reliability must be below max_reliability")
        return self


class RequiredReliabilityPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base: float = Field(ge=0.0, le=1.0)
    risk_coefficients: dict[str, float]
    min: float = Field(ge=0.0, le=1.0)
    max: float = Field(gt=0.0, le=1.0)

    @model_validator(mode="after")
    def _ordered(self) -> RequiredReliabilityPolicy:
        if self.min >= self.max:
            raise ValueError("required_reliability.min must be below .max")
        return self


class EffectiveBurnPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    retry_cost_multiplier: float = Field(ge=0.0)
    debug_overhead_per_cycle: float = Field(ge=0.0)
    debug_cycles_scale: float = Field(ge=0.0)
    escalation_probability: float = Field(ge=0.0, le=1.0)
    failure_penalty_weight: float = Field(ge=0.0)


class EvidenceBands(BaseModel):
    model_config = ConfigDict(extra="forbid")

    none: float = 0.0
    weak: float = 5.0
    moderate: float = 20.0
    strong: float = 50.0


class LearningPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prior_strength: float = Field(gt=0.0)
    max_history_shift: float = Field(ge=0.0, le=1.0)
    burn_prior_strength: float = Field(gt=0.0)
    debug_cycles_prior_strength: float = Field(gt=0.0)
    evidence_bands: EvidenceBands = Field(default_factory=EvidenceBands)


class RecencyBand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_age_days: int | None = Field(default=None, ge=0)
    weight: float = Field(ge=0.0, le=1.0)


class ConfidencePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base: float = Field(ge=0.0, le=1.0)
    analyzer_confidence_weight: float = Field(ge=0.0, le=1.0)
    ambiguity_penalty: float = Field(ge=0.0, le=1.0)
    evidence_weight: float = Field(ge=0.0, le=1.0)
    separation_weight: float = Field(ge=0.0, le=1.0)
    margin_weight: float = Field(ge=0.0, le=1.0)
    min: float = Field(ge=0.0, le=1.0)
    max: float = Field(gt=0.0, le=1.0)
    low_evidence_threshold: float = Field(ge=0.0)


class OverpoweringPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reliability_tolerance: float = Field(ge=0.0, le=1.0)
    burn_ratio_threshold: float = Field(gt=0.0, le=1.0)
    min_evidence: float = Field(ge=0.0)


class ExplanationPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    meaningful_reliability_gain: float = Field(ge=0.0, le=1.0)


class CapabilityWeight(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_weight: float = Field(ge=0.0)
    driver: str | None = None


class RoutingPolicy(BaseModel):
    """The whole of routing.yaml."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str
    router_version: str

    effort_burn_prior: dict[Effort, float]
    effort_capability_gain: dict[Effort, float]

    difficulty_weights: dict[str, float]
    scope_scores: dict[str, float]

    capability_weights: dict[str, CapabilityWeight]
    capability_driver_scale: float = Field(ge=0.0)

    reliability_model: ReliabilityModel
    required_reliability: RequiredReliabilityPolicy
    effective_burn: EffectiveBurnPolicy
    learning: LearningPolicy
    recency_weights: list[RecencyBand]
    confidence: ConfidencePolicy
    overpowering: OverpoweringPolicy
    explanation: ExplanationPolicy

    @model_validator(mode="after")
    def _check(self) -> RoutingPolicy:
        for effort in Effort:
            if effort not in self.effort_burn_prior:
                raise ValueError(f"effort_burn_prior is missing {effort.value!r}")
            if effort not in self.effort_capability_gain:
                raise ValueError(f"effort_capability_gain is missing {effort.value!r}")
            if self.effort_burn_prior[effort] <= 0:
                raise ValueError(f"effort_burn_prior[{effort.value}] must be positive")

        total = sum(self.difficulty_weights.values())
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"difficulty_weights must sum to 1.0, got {total:.6f}")

        if not self.recency_weights:
            raise ValueError("recency_weights must not be empty")
        if self.recency_weights[-1].max_age_days is not None:
            raise ValueError(
                "the last recency band must have max_age_days: null so every age is covered"
            )
        return self

    def recency_weight(self, age_days: float) -> float:
        """Weight for evidence of a given age. Bands are ordered youngest first."""
        for band in self.recency_weights:
            if band.max_age_days is None or age_days <= band.max_age_days:
                return band.weight
        return self.recency_weights[-1].weight

    def scope_score(self, scope: str) -> float:
        if scope not in self.scope_scores:
            raise ValueError(f"routing.yaml has no scope_score for {scope!r}")
        return self.scope_scores[scope]
