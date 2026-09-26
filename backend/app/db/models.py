"""SQLAlchemy models.

Design notes:

* The full JSON of every fingerprint, decision and receipt is stored verbatim
  alongside the extracted columns. The columns exist for querying; the JSON
  exists so that nothing an analyzer or an agent reported is ever lost because
  this version of the schema had no field for it.
* Provider / model / effort are stored as plain strings, never as enums bound
  to the current registry. A receipt naming a model the user has since removed
  from models.yaml must still load, and history must still render.
* `routing_statistics` is a rollup for the analytics page. The router does NOT
  read it: routing uses recency-weighted aggregation computed from the
  execution rows, because a rollup cannot be re-weighted as evidence ages.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSON}


class Task(Base):
    """A task the user pasted in."""

    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    public_task_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    original_task: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(String(200), default="")
    status: Mapped[str] = mapped_column(String(32), default="created", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    fingerprint: Mapped[TaskFingerprintRow | None] = relationship(
        back_populates="task", uselist=False, cascade="all, delete-orphan"
    )
    decisions: Mapped[list[RoutingDecisionRow]] = relationship(
        back_populates="task", cascade="all, delete-orphan", order_by="RoutingDecisionRow.id"
    )
    executions: Mapped[list[Execution]] = relationship(
        back_populates="task", cascade="all, delete-orphan", order_by="Execution.id"
    )


class TaskFingerprintRow(Base):
    """The structured classification Pi produced for a task.

    Stored with analyzer provenance so the ANALYZER can itself be evaluated
    later: if one analyzer model reliably produces fingerprints that lead to
    bad routes, that shows up here rather than being blamed on the router.
    """

    __tablename__ = "task_fingerprints"

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), unique=True, index=True
    )
    schema_version: Mapped[str] = mapped_column(String(16))
    task_family: Mapped[str] = mapped_column(String(64), index=True)
    task_type: Mapped[str] = mapped_column(String(120), default="")
    fingerprint_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    analyzer_provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    analyzer_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    analyzer_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    analyzer_repaired: Mapped[bool] = mapped_column(Boolean, default=False)
    analyzer_duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source: Mapped[str] = mapped_column(
        String(32), default="pi", doc="pi | mock | manual"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    task: Mapped[Task] = relationship(back_populates="fingerprint")


class RoutingDecisionRow(Base):
    """A recommendation produced by the deterministic engine."""

    __tablename__ = "routing_decisions"

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), index=True
    )

    provider: Mapped[str] = mapped_column(String(64), index=True)
    model: Mapped[str] = mapped_column(String(64), index=True)
    effort: Mapped[str] = mapped_column(String(16), index=True)

    confidence: Mapped[float] = mapped_column(Float)
    required_reliability: Mapped[float] = mapped_column(Float)
    predicted_reliability: Mapped[float] = mapped_column(Float)
    predicted_burn: Mapped[float] = mapped_column(Float)
    threshold_met: Mapped[bool] = mapped_column(Boolean, default=True)

    #: Complete RoutingRecommendations (legacy rows contain RoutingDecision), so a
    #: past decision can be audited against a later version of the policy.
    explanation_json: Mapped[dict[str, Any]] = mapped_column(JSON)

    router_version: Mapped[str] = mapped_column(String(32))
    registry_version: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    task: Mapped[Task] = relationship(back_populates="decisions")
    executions: Mapped[list[Execution]] = relationship(back_populates="decision")


class Execution(Base):
    """One imported outcome receipt."""

    __tablename__ = "executions"
    __table_args__ = (Index("ix_executions_family_config", "task_family", "actual_provider", "actual_model", "actual_effort"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), index=True
    )
    routing_decision_id: Mapped[int | None] = mapped_column(
        ForeignKey("routing_decisions.id", ondelete="SET NULL"), nullable=True, index=True
    )

    #: Denormalised onto the execution so statistics queries never need a join
    #: and so history survives a task family being renamed in config.
    task_family: Mapped[str] = mapped_column(String(64), index=True)

    actual_provider: Mapped[str] = mapped_column(String(64), index=True)
    actual_model: Mapped[str] = mapped_column(String(64), index=True)
    actual_effort: Mapped[str] = mapped_column(String(16), index=True)

    recommendation_followed: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    status: Mapped[str] = mapped_column(String(16), index=True)

    receipt_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    router_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    registry_version: Mapped[str | None] = mapped_column(String(32), nullable=True)

    #: When the work happened, which is what recency weighting uses.
    executed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    task: Mapped[Task] = relationship(back_populates="executions")
    decision: Mapped[RoutingDecisionRow | None] = relationship(back_populates="executions")
    metrics: Mapped[ExecutionMetrics | None] = relationship(
        back_populates="execution", uselist=False, cascade="all, delete-orphan"
    )
    escalation: Mapped[Escalation | None] = relationship(
        back_populates="execution", uselist=False, cascade="all, delete-orphan"
    )


class ExecutionMetrics(Base):
    """Extracted, queryable signals from a receipt.

    The split between measured and estimated is load-bearing. `*_tokens` are
    null unless the provider reported them. `estimated_effective_burn` is
    reconstructed by us from observed work signals and is never presented as a
    measurement.
    """

    __tablename__ = "execution_metrics"

    id: Mapped[int] = mapped_column(primary_key=True)
    execution_id: Mapped[int] = mapped_column(
        ForeignKey("executions.id", ondelete="CASCADE"), unique=True, index=True
    )

    implementation_complete: Mapped[bool] = mapped_column(Boolean, default=False)
    first_pass_success: Mapped[bool] = mapped_column(Boolean, default=False)
    tests_passed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    build_passed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    regression_found: Mapped[bool] = mapped_column(Boolean, default=False)

    files_read: Mapped[int | None] = mapped_column(Integer, nullable=True)
    files_modified: Mapped[int | None] = mapped_column(Integer, nullable=True)
    debug_cycles: Mapped[int] = mapped_column(Integer, default=0)
    major_replans: Mapped[int] = mapped_column(Integer, default=0)
    user_corrections: Mapped[int] = mapped_column(Integer, default=0)

    # --- MEASURED (provider-reported only; null otherwise) ---
    measured_input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    measured_cached_input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    measured_output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    measured_reasoning_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    measured_cost: Mapped[float | None] = mapped_column(Float, nullable=True)
    usage_source: Mapped[str] = mapped_column(String(32), default="unavailable")

    # --- ESTIMATED (ours, from work signals) ---
    estimated_effective_burn: Mapped[float | None] = mapped_column(Float, nullable=True)
    base_burn: Mapped[float | None] = mapped_column(Float, nullable=True)

    # --- AGENT SELF-ASSESSMENT (lower trust than the above) ---
    actual_complexity: Mapped[str | None] = mapped_column(String(16), nullable=True)
    recommendation_fit: Mapped[str | None] = mapped_column(String(32), nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="")

    execution: Mapped[Execution] = relationship(back_populates="metrics")


class Escalation(Base):
    """A mid-task move to a different configuration."""

    __tablename__ = "escalations"

    id: Mapped[int] = mapped_column(primary_key=True)
    execution_id: Mapped[int] = mapped_column(
        ForeignKey("executions.id", ondelete="CASCADE"), unique=True, index=True
    )
    task_family: Mapped[str] = mapped_column(String(64), index=True)

    from_provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    from_model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    from_effort: Mapped[str | None] = mapped_column(String(16), nullable=True)
    to_provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    to_model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    to_effort: Mapped[str | None] = mapped_column(String(16), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    execution: Mapped[Execution] = relationship(back_populates="escalation")


class RoutingStatistic(Base):
    """Unweighted rollup per (family, provider, model, effort).

    Maintained on import for the analytics page. The router deliberately does
    not read this table: recency weighting cannot be applied to a rollup after
    the fact, so routing recomputes from the execution rows instead.
    """

    __tablename__ = "routing_statistics"
    __table_args__ = (
        UniqueConstraint(
            "task_family", "provider", "model", "effort", name="uq_routing_statistics_cell"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    task_family: Mapped[str] = mapped_column(String(64), index=True)
    provider: Mapped[str] = mapped_column(String(64))
    model: Mapped[str] = mapped_column(String(64))
    effort: Mapped[str] = mapped_column(String(16))

    attempts: Mapped[int] = mapped_column(Integer, default=0)
    successes: Mapped[int] = mapped_column(Integer, default=0)
    partials: Mapped[int] = mapped_column(Integer, default=0)
    failures: Mapped[int] = mapped_column(Integer, default=0)
    first_pass_successes: Mapped[int] = mapped_column(Integer, default=0)
    escalations: Mapped[int] = mapped_column(Integer, default=0)
    regressions: Mapped[int] = mapped_column(Integer, default=0)

    total_debug_cycles: Mapped[int] = mapped_column(Integer, default=0)
    total_user_corrections: Mapped[int] = mapped_column(Integer, default=0)
    total_estimated_burn: Mapped[float] = mapped_column(Float, default=0.0)
    burn_samples: Mapped[int] = mapped_column(Integer, default=0)

    last_updated: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    @property
    def success_rate(self) -> float | None:
        return self.successes / self.attempts if self.attempts else None

    @property
    def avg_debug_cycles(self) -> float | None:
        return self.total_debug_cycles / self.attempts if self.attempts else None

    @property
    def avg_effective_burn(self) -> float | None:
        return self.total_estimated_burn / self.burn_samples if self.burn_samples else None


class AppSetting(Base):
    """Small key/value store for user-editable runtime settings.

    Deliberately not a mirror of the YAML config: YAML stays the source of
    truth for routing policy. This holds things like the preferred analyzer
    model, which is a user preference rather than a routing parameter.
    """

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class ModelRegistrySnapshot(Base):
    """Point-in-time copy of models.yaml, keyed by registry_version.

    Recorded the first time a version is seen so that a decision made months
    ago can still be explained even after the registry has been retuned.
    """

    __tablename__ = "model_registry_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    registry_version: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
