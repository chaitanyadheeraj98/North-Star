"""Maintenance of the `routing_statistics` rollup.

The rollup is unweighted lifetime counts, used by the analytics page and by
the "what did this teach the router" summary shown after an import. Routing
itself does not read it - see `statistics.py` for why.

Nothing here writes to a config file. The specification is explicit and it is
the right call: learned statistics are DATA, not source-code mutations. An
agent that could rewrite its own routing weights would drift, and there would
be no way to tell a genuine improvement from slow corruption.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...db.models import Escalation, Execution, ExecutionMetrics, RoutingStatistic
from ...schemas.enums import OutcomeStatus


@dataclass(frozen=True)
class StatisticsDelta:
    """What one import changed, for the confirmation shown to the user."""

    task_family: str
    provider: str
    model: str
    effort: str
    attempts_before: int
    attempts_after: int
    success_rate_before: float | None
    success_rate_after: float | None
    avg_burn_before: float | None
    avg_burn_after: float | None


def apply_execution(
    session: Session,
    execution: Execution,
    metrics: ExecutionMetrics | None,
    escalation: Escalation | None,
) -> StatisticsDelta:
    """Fold one execution into the rollup for its cell.

    Called once, inside the import transaction. Re-importing the same receipt
    is prevented upstream by the caller, which replaces any prior execution for
    the task rather than double-counting it.
    """
    stat = session.execute(
        select(RoutingStatistic).where(
            RoutingStatistic.task_family == execution.task_family,
            RoutingStatistic.provider == execution.actual_provider,
            RoutingStatistic.model == execution.actual_model,
            RoutingStatistic.effort == execution.actual_effort,
        )
    ).scalar_one_or_none()

    if stat is None:
        stat = RoutingStatistic(
            task_family=execution.task_family,
            provider=execution.actual_provider,
            model=execution.actual_model,
            effort=execution.actual_effort,
        )
        session.add(stat)
        session.flush()

    before = StatisticsDelta(
        task_family=stat.task_family,
        provider=stat.provider,
        model=stat.model,
        effort=stat.effort,
        attempts_before=stat.attempts,
        attempts_after=stat.attempts,
        success_rate_before=stat.success_rate,
        success_rate_after=stat.success_rate,
        avg_burn_before=stat.avg_effective_burn,
        avg_burn_after=stat.avg_effective_burn,
    )

    stat.attempts += 1
    if execution.status == OutcomeStatus.SUCCESS.value:
        stat.successes += 1
    elif execution.status == OutcomeStatus.PARTIAL.value:
        stat.partials += 1
    else:
        stat.failures += 1

    if escalation is not None:
        stat.escalations += 1

    if metrics is not None:
        if metrics.first_pass_success:
            stat.first_pass_successes += 1
        if metrics.regression_found:
            stat.regressions += 1
        stat.total_debug_cycles += int(metrics.debug_cycles or 0)
        stat.total_user_corrections += int(metrics.user_corrections or 0)
        if metrics.estimated_effective_burn is not None:
            stat.total_estimated_burn += float(metrics.estimated_effective_burn)
            stat.burn_samples += 1

    session.flush()

    return StatisticsDelta(
        task_family=before.task_family,
        provider=before.provider,
        model=before.model,
        effort=before.effort,
        attempts_before=before.attempts_before,
        attempts_after=stat.attempts,
        success_rate_before=before.success_rate_before,
        success_rate_after=stat.success_rate,
        avg_burn_before=before.avg_burn_before,
        avg_burn_after=stat.avg_effective_burn,
    )


def retract_execution(
    session: Session,
    execution: Execution,
    metrics: ExecutionMetrics | None,
    escalation: Escalation | None,
) -> None:
    """Undo a previously applied execution.

    Needed when a receipt for a task is re-imported: the old execution is
    withdrawn from the rollup before the new one is folded in, so correcting a
    mistyped receipt does not permanently inflate the counts.
    """
    stat = session.execute(
        select(RoutingStatistic).where(
            RoutingStatistic.task_family == execution.task_family,
            RoutingStatistic.provider == execution.actual_provider,
            RoutingStatistic.model == execution.actual_model,
            RoutingStatistic.effort == execution.actual_effort,
        )
    ).scalar_one_or_none()
    if stat is None:
        return

    stat.attempts = max(0, stat.attempts - 1)
    if execution.status == OutcomeStatus.SUCCESS.value:
        stat.successes = max(0, stat.successes - 1)
    elif execution.status == OutcomeStatus.PARTIAL.value:
        stat.partials = max(0, stat.partials - 1)
    else:
        stat.failures = max(0, stat.failures - 1)

    if escalation is not None:
        stat.escalations = max(0, stat.escalations - 1)

    if metrics is not None:
        if metrics.first_pass_success:
            stat.first_pass_successes = max(0, stat.first_pass_successes - 1)
        if metrics.regression_found:
            stat.regressions = max(0, stat.regressions - 1)
        stat.total_debug_cycles = max(0, stat.total_debug_cycles - int(metrics.debug_cycles or 0))
        stat.total_user_corrections = max(
            0, stat.total_user_corrections - int(metrics.user_corrections or 0)
        )
        if metrics.estimated_effective_burn is not None:
            stat.total_estimated_burn = max(
                0.0, stat.total_estimated_burn - float(metrics.estimated_effective_burn)
            )
            stat.burn_samples = max(0, stat.burn_samples - 1)

    session.flush()


def rebuild_all(session: Session) -> int:
    """Recompute every rollup row from the execution table.

    A repair tool. The incremental path is authoritative in normal operation;
    this exists for when a database is edited by hand or restored from a
    partial backup. Returns the number of cells rebuilt.
    """
    session.query(RoutingStatistic).delete()
    session.flush()

    rows = session.execute(
        select(Execution, ExecutionMetrics, Escalation)
        .outerjoin(ExecutionMetrics, ExecutionMetrics.execution_id == Execution.id)
        .outerjoin(Escalation, Escalation.execution_id == Execution.id)
    ).all()
    for execution, metrics, escalation in rows:
        apply_execution(session, execution, metrics, escalation)

    return session.query(RoutingStatistic).count()
