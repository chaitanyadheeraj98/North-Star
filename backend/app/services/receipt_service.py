"""Importing an execution receipt.

The pipeline, in order:

    extract marker block -> parse JSON -> validate schema -> find the task ->
    compare recommended against actual -> store execution + metrics +
    escalation -> estimate effective burn -> update the rollup ->
    report what was learned

Two things this deliberately does NOT do:

* It does not reject a receipt because the user ignored the recommendation.
  That case is some of the most valuable evidence the system can get, and
  throwing it away to protect the router's feelings would be absurd.
* It does not write to any config file. Learned statistics are data. A system
  that rewrites its own routing weights from single observations drifts, and
  nobody can tell drift from improvement after the fact.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import ConfigBundle
from ..core.learning.statistics import build_evidence_index
from ..core.learning.updater import StatisticsDelta, apply_execution, retract_execution
from ..core.router import burn as burn_mod
from ..db.models import (
    Escalation,
    Execution,
    ExecutionMetrics,
    RoutingDecisionRow,
    Task,
)
from ..schemas.enums import OutcomeStatus, TaskStatus
from ..schemas.execution_receipt import ExecutionReceipt, parse_receipt
from .task_service import get_task


class ReceiptImportError(ValueError):
    """A receipt could not be imported. The message is shown to the user."""


@dataclass(frozen=True)
class ImportResult:
    task_id: str
    execution_id: int
    task_family: str
    recommended: str | None
    actual: str
    recommendation_followed: bool
    status: str
    first_pass_success: bool
    escalated: bool
    debug_cycles: int
    estimated_effective_burn: float | None
    usage_source: str
    replaced_previous: bool
    delta: StatisticsDelta
    learning_summary: str
    warnings: list[str]


def import_receipt(
    session: Session, config: ConfigBundle, raw_text: str
) -> ImportResult:
    receipt, raw_payload = _parse(raw_text)

    task = get_task(session, receipt.task_id)
    if task is None:
        raise ReceiptImportError(
            f"No task named {receipt.task_id} exists in this router. "
            "Receipts can only be imported for tasks this instance created."
        )

    warnings: list[str] = []
    decision = _latest_decision(session, task)

    provider = receipt.execution.provider.strip().lower()
    model = receipt.execution.model.strip().lower()
    effort = receipt.execution.effort

    if not config.registry.supports(provider, model, effort):
        # Recorded anyway: a receipt naming a model that is disabled locally,
        # or an effort this installation will not route to, is still a real
        # observation about real work.
        warnings.append(
            f"{provider}/{model} at {effort.value} effort is not a configuration this "
            "registry declares. The outcome was recorded, but it will not influence "
            "routing until the registry includes it."
        )

    recommendation_followed = bool(
        decision
        and decision.provider == provider
        and decision.model == model
        and decision.effort == effort.value
    )
    if decision and not recommendation_followed:
        warnings.append(
            f"Recommendation was {decision.provider}/{decision.model}/{decision.effort}; "
            f"you ran {provider}/{model}/{effort.value}. Recorded as a deviation, which is "
            "exactly the kind of evidence the router learns from."
        )
    if decision is None:
        warnings.append(
            "This task has no stored recommendation, so the outcome is recorded without a "
            "recommended-versus-actual comparison."
        )

    task_family = _resolve_family(session, task)

    replaced_previous = _retract_existing(session, task)
    if replaced_previous:
        warnings.append(
            "A receipt had already been imported for this task. The previous outcome was "
            "withdrawn and replaced rather than counted twice."
        )

    execution = Execution(
        task_id=task.id,
        routing_decision_id=decision.id if decision else None,
        task_family=task_family,
        actual_provider=provider,
        actual_model=model,
        actual_effort=effort.value,
        recommendation_followed=recommendation_followed,
        status=receipt.outcome.status.value,
        receipt_json=raw_payload,
        router_version=decision.router_version if decision else None,
        registry_version=decision.registry_version if decision else None,
        executed_at=datetime.now(timezone.utc),
    )
    session.add(execution)
    session.flush()

    base_burn, estimated_burn = _estimate_burn(
        session, config, execution, receipt, task_family
    )

    metrics = ExecutionMetrics(
        execution_id=execution.id,
        implementation_complete=receipt.outcome.implementation_complete,
        first_pass_success=receipt.outcome.first_pass_success,
        tests_passed=receipt.outcome.tests_passed,
        build_passed=receipt.outcome.build_passed,
        regression_found=receipt.outcome.known_regression,
        files_read=receipt.work.files_read,
        files_modified=receipt.work.files_modified,
        debug_cycles=receipt.work.debug_cycles,
        major_replans=receipt.work.major_replans,
        user_corrections=receipt.work.user_corrections,
        measured_input_tokens=receipt.usage.input_tokens,
        measured_cached_input_tokens=receipt.usage.cached_input_tokens,
        measured_output_tokens=receipt.usage.output_tokens,
        measured_reasoning_tokens=receipt.usage.reasoning_tokens,
        measured_cost=receipt.usage.provider_reported_cost,
        usage_source=receipt.usage.source.value,
        estimated_effective_burn=estimated_burn,
        base_burn=base_burn,
        actual_complexity=(
            receipt.agent_assessment.actual_complexity.value
            if receipt.agent_assessment.actual_complexity
            else None
        ),
        recommendation_fit=receipt.agent_assessment.recommendation_fit.value,
        notes=receipt.agent_assessment.notes,
    )
    session.add(metrics)

    escalation: Escalation | None = None
    if receipt.escalation.occurred:
        escalation = Escalation(
            execution_id=execution.id,
            task_family=task_family,
            from_provider=receipt.escalation.from_provider or provider,
            from_model=receipt.escalation.from_model or model,
            from_effort=(
                receipt.escalation.from_effort.value
                if receipt.escalation.from_effort
                else effort.value
            ),
            to_provider=receipt.escalation.to_provider,
            to_model=receipt.escalation.to_model,
            to_effort=(
                receipt.escalation.to_effort.value if receipt.escalation.to_effort else None
            ),
            reason=receipt.escalation.reason,
        )
        session.add(escalation)

    session.flush()

    delta = apply_execution(session, execution, metrics, escalation)
    task.status = TaskStatus.EXECUTED.value
    session.flush()

    return ImportResult(
        task_id=task.public_task_id,
        execution_id=execution.id,
        task_family=task_family,
        recommended=(
            f"{decision.provider}/{decision.model}/{decision.effort}" if decision else None
        ),
        actual=f"{provider}/{model}/{effort.value}",
        recommendation_followed=recommendation_followed,
        status=receipt.outcome.status.value,
        first_pass_success=receipt.outcome.first_pass_success,
        escalated=receipt.escalation.occurred,
        debug_cycles=receipt.work.debug_cycles,
        estimated_effective_burn=estimated_burn,
        usage_source=receipt.usage.source.value,
        replaced_previous=replaced_previous,
        delta=delta,
        learning_summary=_summarise(delta, receipt),
        warnings=warnings,
    )


# --------------------------------------------------------------------------


def _parse(raw_text: str) -> tuple[ExecutionReceipt, dict]:
    try:
        return parse_receipt(raw_text)
    except ValueError as exc:
        raise ReceiptImportError(str(exc)) from exc
    except Exception as exc:  # pydantic ValidationError and friends
        raise ReceiptImportError(f"The receipt failed validation: {exc}") from exc


def _latest_decision(session: Session, task: Task) -> RoutingDecisionRow | None:
    return session.execute(
        select(RoutingDecisionRow)
        .where(RoutingDecisionRow.task_id == task.id)
        .order_by(RoutingDecisionRow.id.desc())
        .limit(1)
    ).scalar_one_or_none()


def _resolve_family(session: Session, task: Task) -> str:
    """Family comes from the stored fingerprint, not from the receipt.

    The executing agent has no business deciding which statistics bucket its
    own outcome lands in.
    """
    row = task.fingerprint
    if row is not None:
        return row.task_family
    return "backend_business_logic"


def _retract_existing(session: Session, task: Task) -> bool:
    """Withdraw any prior execution for this task, so re-imports correct."""
    existing = session.execute(
        select(Execution).where(Execution.task_id == task.id)
    ).scalars().all()
    if not existing:
        return False
    for execution in existing:
        retract_execution(session, execution, execution.metrics, execution.escalation)
        session.delete(execution)
    session.flush()
    return True


def _estimate_burn(
    session: Session,
    config: ConfigBundle,
    execution: Execution,
    receipt: ExecutionReceipt,
    task_family: str,
) -> tuple[float | None, float | None]:
    """Reconstruct what this execution cost, in the router's own burn units.

    This is an ESTIMATE from observed work signals, never a token measurement.
    It is stored in `estimated_effective_burn` and surfaced under that name.
    Measured token usage, when the provider reported it, lives in separate
    columns and is not folded into this number.

    Returns (base_burn, estimated_effective_burn). Both are None when the
    configuration is not in the registry, because there is no honest way to
    price a model we know nothing about.
    """
    provider = config.registry.provider(execution.actual_provider)
    model = config.registry.model(execution.actual_provider, execution.actual_model)
    if provider is None or model is None:
        return None, None

    base = burn_mod.base_burn(provider, model, receipt.execution.effort, config.policy)

    # The escalation reference is what redoing the work properly would cost.
    # Use the cheapest base burn seen in this family's history, falling back to
    # this configuration's own base when there is no history yet.
    index = build_evidence_index(session, config.policy, task_family=task_family)
    reference = base
    for (family, prov, mdl, eff), evidence in index.cells.items():
        if family != task_family or evidence.weighted_attempts <= 0:
            continue
        candidate_provider = config.registry.provider(prov)
        candidate_model = config.registry.model(prov, mdl)
        if candidate_provider is None or candidate_model is None:
            continue
        try:
            from ..schemas.enums import Effort

            candidate_base = burn_mod.base_burn(
                candidate_provider, candidate_model, Effort(eff), config.policy
            )
        except ValueError:
            continue
        reference = min(reference, candidate_base)

    estimated = burn_mod.observed_effective_burn(
        base,
        succeeded=receipt.outcome.status is OutcomeStatus.SUCCESS,
        debug_cycles=receipt.work.debug_cycles,
        escalated=receipt.escalation.occurred,
        escalation_reference_burn=reference,
        user_corrections=receipt.work.user_corrections,
        major_replans=receipt.work.major_replans,
        policy=config.policy,
    )
    return round(base, 4), round(estimated, 4)


def _summarise(delta: StatisticsDelta, receipt: ExecutionReceipt) -> str:
    """One honest sentence about what the router now knows."""
    cell = f"{delta.provider}/{delta.model}/{delta.effort} on {delta.task_family}"
    if delta.attempts_after <= 1:
        return (
            f"First recorded execution for {cell}. One data point does not move routing on "
            "its own; the Bayesian prior still dominates until several more arrive."
        )

    before = delta.success_rate_before
    after = delta.success_rate_after
    trend = ""
    if before is not None and after is not None:
        if after > before + 1e-9:
            trend = f" Observed success rate rose from {before:.0%} to {after:.0%}."
        elif after < before - 1e-9:
            trend = f" Observed success rate fell from {before:.0%} to {after:.0%}."
        else:
            trend = f" Observed success rate held at {after:.0%}."

    escalated = " An escalation was recorded, which raises the expected cost of starting here." if receipt.escalation.occurred else ""
    return (
        f"{cell} now has {delta.attempts_after} recorded executions.{trend}{escalated}"
    )
