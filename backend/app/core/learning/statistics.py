"""Recency-weighted historical statistics.

This is what the router reads. It aggregates the raw execution rows into one
`HistoricalEvidence` per (task family, provider, model, effort) cell, weighting
each execution by its age.

It recomputes from raw rows rather than reading the `routing_statistics`
rollup, because a rollup cannot be re-weighted as evidence ages: an execution
that counted fully last month must count for less next quarter, and only the
raw rows can express that.

At personal-use scale (hundreds, maybe low thousands of executions) this is a
single indexed query per routing call, which is cheaper than the JSON parsing
that surrounds it. The `EvidenceIndex` caches one full sweep per request so
that scoring forty configurations does not mean forty queries.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...db.models import Escalation, Execution, ExecutionMetrics
from ...schemas.enums import Effort, OutcomeStatus
from ...schemas.routing_policy import RoutingPolicy
from ..router.reliability import EMPTY_EVIDENCE, HistoricalEvidence
from .recency import weight_for

CellKey = tuple[str, str, str, str]


@dataclass
class _Accumulator:
    weighted_attempts: float = 0.0
    weighted_successes: float = 0.0
    weighted_first_pass: float = 0.0
    weighted_escalations: float = 0.0
    weighted_debug_cycles: float = 0.0
    weighted_burn: float = 0.0
    burn_samples: float = 0.0
    raw_attempts: int = 0

    def finish(self) -> HistoricalEvidence:
        return HistoricalEvidence(
            weighted_attempts=self.weighted_attempts,
            weighted_successes=self.weighted_successes,
            weighted_first_pass=self.weighted_first_pass,
            weighted_escalations=self.weighted_escalations,
            weighted_debug_cycles=self.weighted_debug_cycles,
            weighted_burn=self.weighted_burn,
            burn_samples=self.burn_samples,
            raw_attempts=self.raw_attempts,
        )


@dataclass
class EvidenceIndex:
    """One sweep of the execution history, keyed by cell."""

    cells: dict[CellKey, HistoricalEvidence] = field(default_factory=dict)

    def lookup(
        self, task_family: str, provider: str, model: str, effort: Effort | str
    ) -> HistoricalEvidence:
        effort_value = effort.value if isinstance(effort, Effort) else str(effort)
        return self.cells.get(
            (task_family, provider, model, effort_value), EMPTY_EVIDENCE
        )

    def as_lookup(self):
        """Adapter matching the engine's `EvidenceLookup` signature."""

        def _lookup(
            task_family: str, provider: str, model: str, effort: Effort
        ) -> HistoricalEvidence:
            return self.lookup(task_family, provider, model, effort)

        return _lookup

    def total_weight(self) -> float:
        return sum(cell.weighted_attempts for cell in self.cells.values())


def _outcome_credit(status: str) -> float:
    """How much of an attempt counts as a success.

    A partial outcome is genuinely partial information: treating it as a clean
    success would flatter a configuration that left work unfinished, and
    treating it as a failure would punish one that got most of the way there
    against a badly specified task.
    """
    if status == OutcomeStatus.SUCCESS.value:
        return 1.0
    if status == OutcomeStatus.PARTIAL.value:
        return 0.5
    return 0.0


def build_evidence_index(
    session: Session,
    policy: RoutingPolicy,
    *,
    task_family: str | None = None,
    now: datetime | None = None,
) -> EvidenceIndex:
    """Aggregate execution history into recency-weighted per-cell evidence.

    Pass `task_family` to restrict the sweep to the family being routed, which
    is the common case and keeps the query small.
    """
    stmt = (
        select(Execution, ExecutionMetrics, Escalation)
        .outerjoin(ExecutionMetrics, ExecutionMetrics.execution_id == Execution.id)
        .outerjoin(Escalation, Escalation.execution_id == Execution.id)
    )
    if task_family is not None:
        stmt = stmt.where(Execution.task_family == task_family)

    accumulators: dict[CellKey, _Accumulator] = defaultdict(_Accumulator)

    for execution, metrics, escalation in session.execute(stmt).all():
        weight = weight_for(execution.executed_at, policy, now)
        if weight <= 0.0:
            continue

        key: CellKey = (
            execution.task_family,
            execution.actual_provider,
            execution.actual_model,
            execution.actual_effort,
        )
        acc = accumulators[key]
        acc.raw_attempts += 1
        acc.weighted_attempts += weight
        acc.weighted_successes += weight * _outcome_credit(execution.status)

        if escalation is not None:
            acc.weighted_escalations += weight

        if metrics is not None:
            if metrics.first_pass_success:
                acc.weighted_first_pass += weight
            acc.weighted_debug_cycles += weight * float(metrics.debug_cycles or 0)
            if metrics.estimated_effective_burn is not None:
                acc.weighted_burn += weight * metrics.estimated_effective_burn
                acc.burn_samples += weight

    return EvidenceIndex(cells={key: acc.finish() for key, acc in accumulators.items()})


def evidence_for_cell(
    session: Session,
    policy: RoutingPolicy,
    task_family: str,
    provider: str,
    model: str,
    effort: Effort | str,
    *,
    now: datetime | None = None,
) -> HistoricalEvidence:
    """Evidence for one cell. Convenience wrapper; prefers the batched sweep."""
    index = build_evidence_index(session, policy, task_family=task_family, now=now)
    return index.lookup(task_family, provider, model, effort)
