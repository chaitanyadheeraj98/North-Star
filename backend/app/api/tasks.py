"""Task endpoints: create, analyze, recommend, hand off."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import ConfigBundle
from ..db.models import Execution, Task
from ..schemas.api import (
    AnalyzeRequest,
    AnalyzerInfo,
    CreateTaskRequest,
    HandoffResponse,
    TaskResponse,
    TaskSummary,
)
from ..schemas.routing_decision import RoutingDecision
from ..schemas.task_fingerprint import (
    AnalyzerMetadata,
    FingerprintEnvelope,
    TaskFingerprint,
)
from ..services import routing_service, task_service
from ..services.analyzer import AnalyzerError, AnalyzerOverrides, HeuristicAnalyzer
from .deps import ConfigDep, SessionDep, analyzer_for

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


@router.post("", response_model=TaskResponse, status_code=201)
async def create_task(
    body: CreateTaskRequest,
    session: Session = SessionDep,
    config: ConfigBundle = ConfigDep,
) -> TaskResponse:
    """Create a task and, by default, analyze and route it in one call.

    The Router page uses the combined form because that is the user-facing
    action. The internal steps stay separate services so each can be tested and
    re-run on its own.
    """
    try:
        task = task_service.create_task(session, body.task)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    analyzer_info: AnalyzerInfo | None = None
    fingerprint: TaskFingerprint | None = None
    decision: RoutingDecision | None = None
    handoff: str | None = None

    if body.analyze:
        envelope, analyzer_info = await _run_analyzer(
            body.task, body.analyzer, body.allow_fallback, _analyzer_overrides(session)
        )
        fingerprint = envelope.fingerprint
        decision, handoff = _route_and_store(
            session, config, task, envelope, analyzer_info.source
        )

    session.commit()
    session.refresh(task)

    return TaskResponse(
        public_task_id=task.public_task_id,
        title=task.title,
        original_task=task.original_task,
        status=task.status,
        created_at=task.created_at,
        fingerprint=fingerprint,
        analyzer=analyzer_info,
        recommendation=decision,
        handoff=handoff,
    )


@router.post("/{public_task_id}/analyze", response_model=TaskResponse)
async def analyze_task(
    public_task_id: str,
    body: AnalyzeRequest | None = None,
    session: Session = SessionDep,
    config: ConfigBundle = ConfigDep,
) -> TaskResponse:
    """(Re-)analyze a task and produce a fresh recommendation."""
    task = _require_task(session, public_task_id)
    request = body or AnalyzeRequest()

    envelope, analyzer_info = await _run_analyzer(
        task.original_task,
        request.analyzer,
        request.allow_fallback,
        _analyzer_overrides(session),
    )
    decision, handoff = _route_and_store(
        session, config, task, envelope, analyzer_info.source
    )
    session.commit()
    session.refresh(task)

    return TaskResponse(
        public_task_id=task.public_task_id,
        title=task.title,
        original_task=task.original_task,
        status=task.status,
        created_at=task.created_at,
        fingerprint=envelope.fingerprint,
        analyzer=analyzer_info,
        recommendation=decision,
        handoff=handoff,
    )


@router.get("", response_model=list[TaskSummary])
def list_tasks(
    session: Session = SessionDep,
    limit: int = Query(default=50, ge=1, le=500),
) -> list[TaskSummary]:
    tasks = (
        session.execute(select(Task).order_by(Task.id.desc()).limit(limit))
        .scalars()
        .all()
    )
    return [
        TaskSummary(
            public_task_id=t.public_task_id,
            title=t.title,
            status=t.status,
            created_at=t.created_at,
            task_family=t.fingerprint.task_family if t.fingerprint else None,
        )
        for t in tasks
    ]


@router.get("/{public_task_id}", response_model=TaskResponse)
def get_task(
    public_task_id: str,
    session: Session = SessionDep,
    config: ConfigBundle = ConfigDep,
) -> TaskResponse:
    task = _require_task(session, public_task_id)
    return _task_response(session, config, task)


@router.get("/{public_task_id}/recommendation", response_model=RoutingDecision)
def get_recommendation(
    public_task_id: str,
    recompute: bool = Query(
        default=False,
        description="Re-run the router against the stored fingerprint using current "
        "history, instead of returning the decision as it was made.",
    ),
    session: Session = SessionDep,
    config: ConfigBundle = ConfigDep,
) -> RoutingDecision:
    task = _require_task(session, public_task_id)

    if recompute:
        row = task_service.fingerprint_row(session, task)
        if row is None:
            raise HTTPException(
                status_code=409, detail=f"{public_task_id} has not been analyzed yet."
            )
        fingerprint = TaskFingerprint.model_validate(row.fingerprint_json)
        return routing_service.route_fingerprint(session, config, fingerprint)

    decision_row = task_service.latest_decision(session, task)
    if decision_row is None:
        raise HTTPException(
            status_code=409,
            detail=f"{public_task_id} has no recommendation. Analyze it first.",
        )
    return routing_service.load_decision(decision_row)


@router.get("/{public_task_id}/handoff", response_model=HandoffResponse)
def get_handoff(
    public_task_id: str,
    session: Session = SessionDep,
    config: ConfigBundle = ConfigDep,
) -> HandoffResponse:
    task = _require_task(session, public_task_id)
    decision_row = task_service.latest_decision(session, task)
    if decision_row is None:
        raise HTTPException(
            status_code=409,
            detail=f"{public_task_id} has no recommendation. Analyze it first.",
        )

    entry = config.registry.model(decision_row.provider, decision_row.model)
    model_id = entry.model_id if entry else decision_row.model

    return HandoffResponse(
        public_task_id=task.public_task_id,
        handoff=task_service.build_handoff(
            task, decision_row, config.policy.router_version, model_id
        ),
        task_only=task.original_task,
        provider=decision_row.provider,
        model=decision_row.model,
        model_id=model_id,
        effort=decision_row.effort,
    )


@router.delete("/{public_task_id}", status_code=204)
def delete_task(public_task_id: str, session: Session = SessionDep) -> None:
    """Remove a task and everything attached to it.

    The rollup is left alone on purpose: withdrawing an outcome from the
    learned statistics is the receipt importer's job, and deleting a task is
    about tidying the list, not about retracting what was learned. Use the
    rebuild endpoint if the two ever need reconciling.
    """
    task = _require_task(session, public_task_id)
    session.delete(task)
    session.commit()


# --------------------------------------------------------------------------


def _require_task(session: Session, public_task_id: str) -> Task:
    task = task_service.get_task(session, public_task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"No task named {public_task_id}.")
    return task


def _analyzer_overrides(session: Session) -> AnalyzerOverrides:
    """The analyzer the user pinned in Settings, if any.

    Read per request rather than cached, so changing the analyzer in Settings
    takes effect on the very next analysis without restarting anything.
    """
    from .settings import load_preferences

    preferences = load_preferences(session)
    return AnalyzerOverrides(
        provider=preferences.get("analyzer_provider"),
        model=preferences.get("analyzer_model"),
        thinking_level=preferences.get("analyzer_effort"),
    )


async def _run_analyzer(
    task_text: str,
    source: str,
    allow_fallback: bool,
    overrides: AnalyzerOverrides | None = None,
) -> tuple[FingerprintEnvelope, AnalyzerInfo]:
    """Run the requested analyzer, optionally degrading to the offline one.

    When a fallback happens the response says so explicitly. A user who thinks
    Pi read their task deserves to know when a keyword matcher did instead.
    """
    analyzer = analyzer_for(source)
    try:
        envelope = await analyzer.analyze(task_text, overrides)
        return envelope, AnalyzerInfo(
            provider=envelope.analyzer.provider,
            model=envelope.analyzer.model,
            confidence=envelope.analyzer.confidence,
            repaired=envelope.analyzer.repaired,
            duration_ms=envelope.analyzer.duration_ms,
            source=source,
            fallback_used=False,
        )
    except AnalyzerError as exc:
        if not (allow_fallback and source == "pi"):
            raise HTTPException(
                status_code=503,
                detail={"message": str(exc), "detail": exc.detail},
            ) from exc

    envelope = await HeuristicAnalyzer().analyze(task_text)
    return envelope, AnalyzerInfo(
        provider=envelope.analyzer.provider,
        model=envelope.analyzer.model,
        confidence=envelope.analyzer.confidence,
        repaired=False,
        duration_ms=envelope.analyzer.duration_ms,
        source="heuristic",
        fallback_used=True,
        note=(
            "The Pi bridge was unreachable, so an offline keyword analyzer classified this "
            "task. Treat the fingerprint as approximate."
        ),
    )


def _route_and_store(
    session: Session,
    config: ConfigBundle,
    task: Task,
    envelope: FingerprintEnvelope,
    source: str,
) -> tuple[RoutingDecision, str]:
    if not config.families.has(envelope.fingerprint.task_family):
        raise HTTPException(
            status_code=422,
            detail=(
                f"The analyzer returned task_family "
                f"{envelope.fingerprint.task_family!r}, which is not in task_families.yaml."
            ),
        )

    analyzer_meta: AnalyzerMetadata = envelope.analyzer
    routing_service.store_fingerprint(
        session, task, envelope.fingerprint, analyzer_meta, source
    )
    decision = routing_service.route_fingerprint(session, config, envelope.fingerprint)
    decision_row = routing_service.store_decision(session, config, task, decision)

    entry = config.registry.model(decision.provider, decision.model)
    handoff = task_service.build_handoff(
        task,
        decision_row,
        config.policy.router_version,
        entry.model_id if entry else decision.model,
    )
    return decision, handoff


def _task_response(
    session: Session, config: ConfigBundle, task: Task
) -> TaskResponse:
    fingerprint: TaskFingerprint | None = None
    analyzer: AnalyzerInfo | None = None
    row = task_service.fingerprint_row(session, task)
    if row is not None:
        fingerprint = TaskFingerprint.model_validate(row.fingerprint_json)
        analyzer = AnalyzerInfo(
            provider=row.analyzer_provider,
            model=row.analyzer_model,
            confidence=row.analyzer_confidence,
            repaired=row.analyzer_repaired,
            duration_ms=row.analyzer_duration_ms,
            source=row.source,
            fallback_used=row.source == "heuristic",
        )

    decision: RoutingDecision | None = None
    handoff: str | None = None
    decision_row = task_service.latest_decision(session, task)
    if decision_row is not None:
        decision = routing_service.load_decision(decision_row)
        entry = config.registry.model(decision_row.provider, decision_row.model)
        handoff = task_service.build_handoff(
            task,
            decision_row,
            config.policy.router_version,
            entry.model_id if entry else decision_row.model,
        )

    execution_summary: dict[str, Any] | None = None
    execution = session.execute(
        select(Execution).where(Execution.task_id == task.id).order_by(Execution.id.desc())
    ).scalars().first()
    if execution is not None:
        metrics = execution.metrics
        execution_summary = {
            "actual_provider": execution.actual_provider,
            "actual_model": execution.actual_model,
            "actual_effort": execution.actual_effort,
            "status": execution.status,
            "recommendation_followed": execution.recommendation_followed,
            "executed_at": execution.executed_at,
            "first_pass_success": metrics.first_pass_success if metrics else None,
            "debug_cycles": metrics.debug_cycles if metrics else None,
            "estimated_effective_burn": (
                metrics.estimated_effective_burn if metrics else None
            ),
            "usage_source": metrics.usage_source if metrics else None,
            "escalated": execution.escalation is not None,
        }

    return TaskResponse(
        public_task_id=task.public_task_id,
        title=task.title,
        original_task=task.original_task,
        status=task.status,
        created_at=task.created_at,
        fingerprint=fingerprint,
        analyzer=analyzer,
        recommendation=decision,
        handoff=handoff,
        execution=execution_summary,
    )
