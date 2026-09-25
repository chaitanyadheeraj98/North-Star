"""Wires the pure routing engine to persistence.

The engine itself never touches the database. This module resolves the
historical evidence, calls the engine, and records what it decided.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import ConfigBundle
from ..core.learning.statistics import build_evidence_index
from ..core.router.engine import RoutingEngine
from ..db.models import (
    ModelRegistrySnapshot,
    RoutingDecisionRow,
    Task,
    TaskFingerprintRow,
)
from ..schemas.enums import TaskStatus
from ..schemas.routing_decision import RoutingDecision
from ..schemas.task_fingerprint import AnalyzerMetadata, TaskFingerprint


def route_fingerprint(
    session: Session, config: ConfigBundle, fingerprint: TaskFingerprint
) -> RoutingDecision:
    """Produce a recommendation, informed by everything recorded so far."""
    index = build_evidence_index(
        session, config.policy, task_family=fingerprint.task_family
    )
    engine = RoutingEngine(config.registry, config.policy, config.families)
    return engine.route(fingerprint, index.as_lookup())


def store_fingerprint(
    session: Session,
    task: Task,
    fingerprint: TaskFingerprint,
    analyzer: AnalyzerMetadata,
    source: str,
) -> TaskFingerprintRow:
    """Persist a fingerprint, replacing any earlier one for the task.

    Re-analysis is allowed and overwrites, because an analyzer upgrade or a
    reworded task should not leave two competing classifications attached to
    one piece of work.
    """
    existing = session.execute(
        select(TaskFingerprintRow).where(TaskFingerprintRow.task_id == task.id)
    ).scalar_one_or_none()
    if existing is not None:
        session.delete(existing)
        session.flush()

    row = TaskFingerprintRow(
        task_id=task.id,
        schema_version=fingerprint.schema_version,
        task_family=fingerprint.task_family,
        task_type=fingerprint.task_type,
        fingerprint_json=fingerprint.model_dump(mode="json"),
        analyzer_provider=analyzer.provider,
        analyzer_model=analyzer.model,
        analyzer_confidence=analyzer.confidence,
        analyzer_repaired=analyzer.repaired,
        analyzer_duration_ms=analyzer.duration_ms,
        source=source,
    )
    session.add(row)
    task.status = TaskStatus.ANALYZED.value
    session.flush()
    return row


def store_decision(
    session: Session, config: ConfigBundle, task: Task, decision: RoutingDecision
) -> RoutingDecisionRow:
    """Persist a recommendation in full.

    The entire decision, including every configuration that was considered and
    rejected, is stored. Disk is free and being able to answer "why did it say
    that in March" is not.
    """
    _ensure_registry_snapshot(session, config)

    row = RoutingDecisionRow(
        task_id=task.id,
        provider=decision.provider,
        model=decision.model,
        effort=decision.effort.value,
        confidence=decision.confidence,
        required_reliability=decision.required_reliability,
        predicted_reliability=decision.predicted_reliability,
        predicted_burn=decision.predicted_burn,
        threshold_met=decision.threshold_met,
        explanation_json=decision.model_dump(mode="json"),
        router_version=decision.router_version,
        registry_version=decision.registry_version,
    )
    session.add(row)
    task.status = TaskStatus.ROUTED.value
    session.flush()
    return row


def load_decision(row: RoutingDecisionRow) -> RoutingDecision:
    """Rehydrate a stored decision.

    Falls back to a reduced view if the stored JSON predates a schema change,
    so history never becomes unreadable after an upgrade.
    """
    try:
        return RoutingDecision.model_validate(row.explanation_json)
    except Exception:
        return RoutingDecision.model_validate(
            {
                "router_version": row.router_version,
                "registry_version": row.registry_version,
                "provider": row.provider,
                "model": row.model,
                "effort": row.effort,
                "provider_display": row.provider,
                "model_display": row.model,
                "confidence": row.confidence,
                "required_reliability": row.required_reliability,
                "predicted_reliability": row.predicted_reliability,
                "predicted_burn": row.predicted_burn,
                "risk": {
                    "difficulty": 0.0,
                    "required_reliability": row.required_reliability,
                },
                "explanation": {
                    "summary": "Stored in an older format; details unavailable.",
                    "why": "",
                    "why_not_lighter": "",
                    "why_not_stronger": "",
                    "evidence_note": "",
                },
                "threshold_met": row.threshold_met,
            }
        )


def _ensure_registry_snapshot(session: Session, config: ConfigBundle) -> None:
    """Record models.yaml the first time a registry version is used.

    Without this, retuning the registry would quietly invalidate the
    explanation attached to every past decision.
    """
    version = config.registry.registry_version
    exists = session.execute(
        select(ModelRegistrySnapshot.id).where(
            ModelRegistrySnapshot.registry_version == version
        )
    ).scalar_one_or_none()
    if exists is not None:
        return
    snapshot: dict[str, Any] = json.loads(config.registry.model_dump_json())
    session.add(
        ModelRegistrySnapshot(registry_version=version, snapshot_json=snapshot)
    )
    session.flush()
