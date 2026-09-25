"""Task lifecycle: creation, ids, and the handoff block.

Task ids are the join between this application and whatever ran the work. The
handoff carries the id into Claude or Codex, the outcome skill carries it back
out in the receipt, and that is the only reason learning can attribute an
outcome to a decision.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db.models import RoutingDecisionRow, Task, TaskFingerprintRow
from ..schemas.enums import TaskStatus

HANDOFF_OPEN = "[LLM-ROUTER]"
HANDOFF_CLOSE = "[/LLM-ROUTER]"

_TASK_ID_PREFIX = "RT-"
_TASK_ID_DIGITS = 6


def next_task_id(session: Session) -> str:
    """Allocate the next sequential public task id.

    Derived from the highest existing id rather than from a counter row, so a
    restored or hand-edited database cannot silently start reissuing ids that
    are already attached to receipts.
    """
    highest = session.execute(select(func.max(Task.id))).scalar() or 0
    return f"{_TASK_ID_PREFIX}{highest + 1:0{_TASK_ID_DIGITS}d}"


def derive_title(task_text: str, limit: int = 90) -> str:
    """First meaningful line, trimmed. Only ever used as a list label."""
    for line in task_text.splitlines():
        stripped = line.strip().lstrip("#*-> ").strip()
        if stripped:
            return stripped[:limit] + ("..." if len(stripped) > limit else "")
    return "(untitled task)"


def create_task(session: Session, task_text: str) -> Task:
    if not task_text or not task_text.strip():
        raise ValueError("The task text is empty.")

    task = Task(
        public_task_id="",  # replaced below, once the row has an id
        original_task=task_text,
        title=derive_title(task_text),
        status=TaskStatus.CREATED.value,
    )
    session.add(task)
    session.flush()
    task.public_task_id = f"{_TASK_ID_PREFIX}{task.id:0{_TASK_ID_DIGITS}d}"
    session.flush()
    return task


def get_task(session: Session, public_task_id: str) -> Task | None:
    return session.execute(
        select(Task).where(Task.public_task_id == public_task_id)
    ).scalar_one_or_none()


def latest_decision(session: Session, task: Task) -> RoutingDecisionRow | None:
    return session.execute(
        select(RoutingDecisionRow)
        .where(RoutingDecisionRow.task_id == task.id)
        .order_by(RoutingDecisionRow.id.desc())
        .limit(1)
    ).scalar_one_or_none()


def fingerprint_row(session: Session, task: Task) -> TaskFingerprintRow | None:
    return session.execute(
        select(TaskFingerprintRow).where(TaskFingerprintRow.task_id == task.id)
    ).scalar_one_or_none()


def build_handoff(
    task: Task, decision: RoutingDecisionRow, router_version: str, model_id: str = ""
) -> str:
    """The text the user pastes into Claude Code or the Codex CLI.

    The metadata block is what lets the outcome skill reconnect a receipt to
    this routing decision. `model_id` carries the provider-facing identifier so
    the user selects the right model rather than guessing from a display name.
    """
    lines = [
        HANDOFF_OPEN,
        f"task_id={task.public_task_id}",
        f"recommended_provider={decision.provider}",
        f"recommended_model={decision.model}",
        f"recommended_effort={decision.effort}",
    ]
    if model_id:
        lines.append(f"recommended_model_id={model_id}")
    lines.extend(
        [
            f"router_version={router_version}",
            HANDOFF_CLOSE,
            "",
            "TASK",
            "",
            task.original_task.rstrip(),
            "",
            "---",
            "When the work is finished, run the router-outcome skill "
            "(or ask: \"generate the router result receipt\") and paste the "
            "[LLM-ROUTER-RESULT] block back into the router's Result page.",
        ]
    )
    return "\n".join(lines)
