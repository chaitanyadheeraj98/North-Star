"""TaskFingerprint - the contract between Pi and the routing engine.

This is the most important schema in the system. Pi produces it; the router
consumes it. Pi output is UNTRUSTED INPUT and is validated strictly: every
score must be a real number in [0, 1], the scope and test intensity must be
known values, and the task family must exist in task_families.yaml.

Pi never sees provider names, model names or effort levels. It classifies the
task and stops there.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .enums import Scope, TestIntensity

FINGERPRINT_SCHEMA_VERSION = "1.0"

#: A normalised score. Everything the analyzer emits lives on one scale so that
#: no component has to guess whether it received 1-10, 0-100 or 0.0-1.0.
Score = Annotated[float, Field(ge=0.0, le=1.0)]


class TaskFingerprint(BaseModel):
    """Structured, normalised description of a software-development task."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = Field(
        default=FINGERPRINT_SCHEMA_VERSION,
        description="Version of this contract. Mandatory; mismatches are rejected upstream.",
    )

    task_family: str = Field(
        description="One of the canonical families defined in task_families.yaml.",
        min_length=1,
    )
    task_type: str = Field(
        default="",
        max_length=120,
        description="Free-text sub-label, e.g. 'bug_fix'. Never used for routing arithmetic.",
    )

    # --- Difficulty of thought -------------------------------------------------
    complexity: Score = Field(
        description="Reasoning difficulty, NOT prompt length. A long mechanical "
        "task is simple; a short race-condition task is not."
    )
    ambiguity: Score = Field(
        description="How much is left unsaid or open to interpretation."
    )
    requirements_clarity: Score = Field(
        description="How precisely the desired end state is specified."
    )

    # --- Risk dimensions -------------------------------------------------------
    regression_risk: Score = Field(
        description="Likelihood that a wrong change silently breaks something else."
    )
    architecture_reasoning: Score = Field(
        description="How much component-boundary and design reasoning is required."
    )
    database_reasoning: Score = Field(
        description="How much persistence, schema and data-semantics reasoning is required."
    )
    concurrency_risk: Score = Field(
        description="Exposure to races, ordering, idempotency and coordination."
    )
    security_risk: Score = Field(
        description="Exposure to auth, secrets, trust boundaries and injection surfaces."
    )
    repository_understanding: Score = Field(
        description="How much existing-codebase comprehension the task demands."
    )

    # --- Shape -----------------------------------------------------------------
    scope: Scope = Field(description="Blast radius of the change.")
    estimated_files: int = Field(
        ge=0, le=10_000, description="Rough count of files likely touched."
    )

    frontend: bool = False
    backend: bool = False
    database: bool = False
    infrastructure: bool = False

    tests_required: TestIntensity = Field(
        description="Expected test intensity implied by the task."
    )

    # --- Analyzer self-assessment ---------------------------------------------
    confidence: Score = Field(
        description="How confident the analyzer is in this classification. "
        "Feeds router confidence; it never changes the reliability bar."
    )

    @field_validator("schema_version")
    @classmethod
    def _known_schema_version(cls, value: str) -> str:
        if value != FINGERPRINT_SCHEMA_VERSION:
            raise ValueError(
                f"unsupported fingerprint schema_version {value!r}; "
                f"this build understands {FINGERPRINT_SCHEMA_VERSION!r}"
            )
        return value

    @field_validator("task_family")
    @classmethod
    def _normalise_family(cls, value: str) -> str:
        return value.strip().lower()

    @property
    def clarity_gap(self) -> float:
        """Inverse of requirements clarity, used by the difficulty blend."""
        return 1.0 - self.requirements_clarity

    @property
    def debugging_pressure(self) -> float:
        """Synthetic driver for the debugging capability dimension.

        There is no `debugging` score in the fingerprint because the analyzer
        would have to guess at it. It is derived instead from the signals that
        genuinely predict debugging load: an unclear problem in a codebase you
        must first understand.
        """
        return max(
            self.ambiguity,
            self.clarity_gap,
            self.repository_understanding * self.complexity,
        )


class AnalyzerMetadata(BaseModel):
    """Who produced a fingerprint, and how sure they were.

    Stored so that the task analyzer itself can be evaluated later: if a
    particular analyzer model consistently produces fingerprints that lead to
    bad routes, that is visible in the data.
    """

    model_config = ConfigDict(extra="ignore")

    provider: str | None = None
    model: str | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    repaired: bool = Field(
        default=False,
        description="True when the analyzer returned malformed JSON and a single "
        "controlled repair attempt was needed.",
    )
    duration_ms: int | None = Field(default=None, ge=0)


class FingerprintEnvelope(BaseModel):
    """Exactly what the Pi bridge returns from POST /analyze."""

    model_config = ConfigDict(extra="ignore")

    fingerprint: TaskFingerprint
    analyzer: AnalyzerMetadata = Field(default_factory=AnalyzerMetadata)
