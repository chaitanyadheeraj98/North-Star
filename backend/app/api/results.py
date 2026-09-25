"""Result import: the other half of the loop."""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, HTTPException
from sqlalchemy.orm import Session

from ..config import ConfigBundle
from ..core.learning.updater import rebuild_all
from ..schemas.api import ImportResultRequest, ImportResultResponse
from ..services.receipt_service import ReceiptImportError, import_receipt
from .deps import ConfigDep, SessionDep

router = APIRouter(prefix="/api/results", tags=["results"])


@router.post("/import", response_model=ImportResultResponse)
def import_result(
    body: ImportResultRequest,
    session: Session = SessionDep,
    config: ConfigBundle = ConfigDep,
) -> ImportResultResponse:
    try:
        result = import_receipt(session, config, body.receipt)
    except ReceiptImportError as exc:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception:
        session.rollback()
        raise

    session.commit()

    return ImportResultResponse(
        task_id=result.task_id,
        execution_id=result.execution_id,
        task_family=result.task_family,
        recommended=result.recommended,
        actual=result.actual,
        recommendation_followed=result.recommendation_followed,
        status=result.status,
        first_pass_success=result.first_pass_success,
        escalated=result.escalated,
        debug_cycles=result.debug_cycles,
        estimated_effective_burn=result.estimated_effective_burn,
        usage_source=result.usage_source,
        replaced_previous=result.replaced_previous,
        learning_summary=result.learning_summary,
        warnings=result.warnings,
        statistics=asdict(result.delta),
    )


@router.post("/rebuild-statistics")
def rebuild_statistics(session: Session = SessionDep) -> dict[str, int | str]:
    """Recompute the analytics rollup from the execution rows.

    A repair tool for a database that was restored from a partial backup or
    edited by hand. It changes no outcomes, only the derived counts.
    """
    cells = rebuild_all(session)
    session.commit()
    return {"rebuilt_cells": cells, "status": "ok"}
