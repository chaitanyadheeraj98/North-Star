"""History: recommended against actual, task by task."""

from __future__ import annotations

from fastapi import APIRouter, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import ConfigBundle
from ..db.models import Execution, RoutingDecisionRow, Task, TaskFingerprintRow
from ..schemas.api import HistoryResponse, HistoryRow
from .deps import ConfigDep, SessionDep

router = APIRouter(prefix="/api/history", tags=["history"])


@router.get("", response_model=HistoryResponse)
def get_history(
    session: Session = SessionDep,
    config: ConfigBundle = ConfigDep,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    task_family: str | None = Query(default=None),
    only_executed: bool = Query(default=False),
) -> HistoryResponse:
    # "Latest row per task" via grouped max-id subqueries rather than
    # correlated scalar subqueries: SQLite plans these better, and correlated
    # subqueries inside an outer join auto-correlate away their FROM clause.
    latest_decision = (
        select(
            RoutingDecisionRow.task_id.label("task_id"),
            func.max(RoutingDecisionRow.id).label("decision_id"),
        )
        .group_by(RoutingDecisionRow.task_id)
        .subquery()
    )
    latest_execution = (
        select(
            Execution.task_id.label("task_id"),
            func.max(Execution.id).label("execution_id"),
        )
        .group_by(Execution.task_id)
        .subquery()
    )

    stmt = (
        select(Task, TaskFingerprintRow, RoutingDecisionRow, Execution)
        .outerjoin(TaskFingerprintRow, TaskFingerprintRow.task_id == Task.id)
        .outerjoin(latest_decision, latest_decision.c.task_id == Task.id)
        .outerjoin(
            RoutingDecisionRow, RoutingDecisionRow.id == latest_decision.c.decision_id
        )
        .outerjoin(latest_execution, latest_execution.c.task_id == Task.id)
        .outerjoin(Execution, Execution.id == latest_execution.c.execution_id)
    )
    count_stmt = select(func.count(Task.id))

    if task_family:
        stmt = stmt.where(TaskFingerprintRow.task_family == task_family)
        count_stmt = count_stmt.join(
            TaskFingerprintRow, TaskFingerprintRow.task_id == Task.id
        ).where(TaskFingerprintRow.task_family == task_family)
    if only_executed:
        stmt = stmt.where(Execution.id.is_not(None))
        count_stmt = count_stmt.join(Execution, Execution.task_id == Task.id)

    total = session.execute(count_stmt).scalar() or 0
    rows = session.execute(
        stmt.order_by(Task.id.desc()).limit(limit).offset(offset)
    ).all()

    out: list[HistoryRow] = []
    for task, fingerprint, decision, execution in rows:
        metrics = execution.metrics if execution else None

        recommended_display = None
        if decision is not None:
            p, m = config.registry.display(decision.provider, decision.model)
            recommended_display = f"{p} {m} ({decision.effort})"

        actual_display = None
        if execution is not None:
            p, m = config.registry.display(
                execution.actual_provider, execution.actual_model
            )
            actual_display = f"{p} {m} ({execution.actual_effort})"

        out.append(
            HistoryRow(
                public_task_id=task.public_task_id,
                title=task.title,
                task_family=fingerprint.task_family if fingerprint else None,
                created_at=task.created_at,
                status=task.status,
                recommended_provider=decision.provider if decision else None,
                recommended_model=decision.model if decision else None,
                recommended_effort=decision.effort if decision else None,
                recommended_display=recommended_display,
                actual_provider=execution.actual_provider if execution else None,
                actual_model=execution.actual_model if execution else None,
                actual_effort=execution.actual_effort if execution else None,
                actual_display=actual_display,
                outcome=execution.status if execution else None,
                recommendation_followed=(
                    execution.recommendation_followed if execution else None
                ),
                first_pass_success=metrics.first_pass_success if metrics else None,
                escalated=(execution.escalation is not None) if execution else None,
                debug_cycles=metrics.debug_cycles if metrics else None,
                predicted_burn=decision.predicted_burn if decision else None,
                estimated_effective_burn=(
                    metrics.estimated_effective_burn if metrics else None
                ),
                usage_source=metrics.usage_source if metrics else None,
                executed_at=execution.executed_at if execution else None,
            )
        )

    return HistoryResponse(rows=out, total=total, limit=limit, offset=offset)
