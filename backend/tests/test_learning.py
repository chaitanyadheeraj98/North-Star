"""Learning engine: Bayesian smoothing, recency weighting, rollups."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.core.learning.bayesian import posterior, shrink
from app.core.learning.recency import age_in_days, weight_for
from app.core.learning.statistics import build_evidence_index
from app.core.learning.updater import apply_execution, rebuild_all, retract_execution
from app.core.router.reliability import HistoricalEvidence, evidence_band, posterior_reliability
from app.db.models import (
    Escalation,
    Execution,
    ExecutionMetrics,
    RoutingStatistic,
    Task,
)
from app.schemas.enums import Effort, EvidenceBand


# --------------------------------------------------------------------------
# Bayesian smoothing
# --------------------------------------------------------------------------


def test_one_success_is_not_a_hundred_percent():
    """The single most seductive mistake in a system like this."""
    result = posterior(prior_mean=0.8, prior_strength=20.0, successes=1, attempts=1)
    assert result.mean < 0.85
    assert result.mean > 0.8


def test_evidence_pulls_the_estimate_toward_what_was_observed():
    low = posterior(0.9, 20.0, successes=10, attempts=100).mean
    high = posterior(0.9, 20.0, successes=95, attempts=100).mean
    assert low < 0.5
    assert high > 0.85


def test_more_evidence_narrows_the_interval():
    few = posterior(0.8, 20.0, successes=4, attempts=5).credible_interval()
    many = posterior(0.8, 20.0, successes=400, attempts=500).credible_interval()
    assert (few[1] - few[0]) > (many[1] - many[0])


def test_no_evidence_returns_the_prior():
    assert posterior(0.77, 20.0, successes=0, attempts=0).mean == pytest.approx(0.77)


def test_shrink_ignores_absent_observations():
    assert shrink(2.0, None, 0, 8.0) == 2.0
    assert shrink(2.0, 5.0, 0, 8.0) == 2.0


def test_shrink_moves_toward_the_observation_as_samples_accumulate():
    light = shrink(2.0, 6.0, samples=1, strength=8.0)
    heavy = shrink(2.0, 6.0, samples=80, strength=8.0)
    assert 2.0 < light < heavy < 6.0


# --------------------------------------------------------------------------
# Posterior reliability, as the router uses it
# --------------------------------------------------------------------------


def test_posterior_respects_the_shift_cap(policy):
    evidence = HistoricalEvidence(weighted_attempts=500.0, weighted_successes=0.0)
    value = posterior_reliability(0.95, evidence, policy)
    assert value >= 0.95 - policy.learning.max_history_shift - 1e-9


def test_posterior_is_the_prior_without_evidence(policy):
    assert posterior_reliability(0.91, HistoricalEvidence(), policy) == 0.91


def test_evidence_bands_follow_the_spec(policy):
    assert evidence_band(0.0, policy) is EvidenceBand.NONE
    assert evidence_band(4.0, policy) is EvidenceBand.NONE
    assert evidence_band(5.0, policy) is EvidenceBand.WEAK
    assert evidence_band(19.0, policy) is EvidenceBand.WEAK
    assert evidence_band(20.0, policy) is EvidenceBand.MODERATE
    assert evidence_band(49.0, policy) is EvidenceBand.MODERATE
    assert evidence_band(50.0, policy) is EvidenceBand.STRONG


# --------------------------------------------------------------------------
# Recency
# --------------------------------------------------------------------------


def test_recency_bands_decay(policy):
    now = datetime(2026, 9, 17, tzinfo=timezone.utc)
    weights = [
        weight_for(now - timedelta(days=days), policy, now)
        for days in (1, 60, 120, 300, 800)
    ]
    assert weights == [1.00, 0.85, 0.65, 0.40, 0.20]
    assert weights == sorted(weights, reverse=True)


def test_naive_timestamps_are_treated_as_utc(policy):
    """SQLite hands back naive datetimes even for timezone-aware columns."""
    now = datetime(2026, 9, 17, tzinfo=timezone.utc)
    naive = datetime(2026, 9, 16)
    assert age_in_days(naive, now) == pytest.approx(1.0, abs=0.01)
    assert weight_for(naive, policy, now) == 1.0


def test_future_timestamps_do_not_produce_negative_ages(policy):
    now = datetime(2026, 9, 17, tzinfo=timezone.utc)
    assert age_in_days(now + timedelta(days=5), now) == 0.0


# --------------------------------------------------------------------------
# Statistics aggregation
# --------------------------------------------------------------------------


def _next_task_number(session) -> int:
    from sqlalchemy import func, select

    return (session.execute(select(func.count(Task.id))).scalar() or 0) + 1


def _add_execution(
    session,
    *,
    family="backend_data_integrity",
    provider="codex",
    model="sol",
    effort="high",
    status="success",
    first_pass=True,
    debug_cycles=0,
    burn=3.0,
    age_days=1,
    escalated=False,
):
    # Executions are foreign-keyed to a task, so the helper creates one. That
    # constraint is deliberate: an outcome with no task cannot be attributed.
    task = Task(
        public_task_id=f"RT-{_next_task_number(session):06d}",
        original_task="fixture task",
        title="fixture task",
        status="executed",
    )
    session.add(task)
    session.flush()

    execution = Execution(
        task_id=task.id,
        task_family=family,
        actual_provider=provider,
        actual_model=model,
        actual_effort=effort,
        recommendation_followed=True,
        status=status,
        receipt_json={},
        executed_at=datetime.now(timezone.utc) - timedelta(days=age_days),
    )
    session.add(execution)
    session.flush()

    metrics = ExecutionMetrics(
        execution_id=execution.id,
        implementation_complete=status == "success",
        first_pass_success=first_pass,
        debug_cycles=debug_cycles,
        estimated_effective_burn=burn,
    )
    session.add(metrics)

    escalation = None
    if escalated:
        escalation = Escalation(
            execution_id=execution.id,
            task_family=family,
            to_provider="claude",
            to_model="opus_5",
            to_effort="high",
        )
        session.add(escalation)

    session.flush()
    return execution, metrics, escalation


def test_recent_evidence_outweighs_old_evidence(session, policy):
    _add_execution(session, status="success", age_days=5)
    _add_execution(session, status="failed", first_pass=False, age_days=800)
    session.commit()

    index = build_evidence_index(session, policy, task_family="backend_data_integrity")
    evidence = index.lookup("backend_data_integrity", "codex", "sol", Effort.HIGH)

    # 1.00 for the recent success, 0.20 for the ancient failure.
    assert evidence.weighted_attempts == pytest.approx(1.20)
    assert evidence.weighted_successes == pytest.approx(1.00)
    assert evidence.observed_success_rate == pytest.approx(1.0 / 1.2)


def test_a_partial_outcome_counts_as_half(session, policy):
    _add_execution(session, status="partial", first_pass=False, age_days=1)
    session.commit()
    evidence = build_evidence_index(
        session, policy, task_family="backend_data_integrity"
    ).lookup("backend_data_integrity", "codex", "sol", Effort.HIGH)
    assert evidence.weighted_successes == pytest.approx(0.5)
    assert evidence.weighted_attempts == pytest.approx(1.0)


def test_escalations_are_counted(session, policy):
    _add_execution(session, status="success", first_pass=False, escalated=True)
    session.commit()
    evidence = build_evidence_index(
        session, policy, task_family="backend_data_integrity"
    ).lookup("backend_data_integrity", "codex", "sol", Effort.HIGH)
    assert evidence.weighted_escalations == pytest.approx(1.0)


def test_cells_are_kept_separate(session, policy):
    _add_execution(session, model="sol", effort="high")
    _add_execution(session, model="terra", effort="medium", status="failed", first_pass=False)
    session.commit()

    index = build_evidence_index(session, policy, task_family="backend_data_integrity")
    assert index.lookup("backend_data_integrity", "codex", "sol", Effort.HIGH).weighted_successes == 1.0
    assert index.lookup("backend_data_integrity", "codex", "terra", Effort.MEDIUM).weighted_successes == 0.0
    assert index.lookup("backend_data_integrity", "codex", "luna", Effort.LOW).weighted_attempts == 0.0


def test_an_unrelated_family_is_not_mixed_in(session, policy):
    _add_execution(session, family="ui_cosmetic")
    _add_execution(session, family="security", status="failed", first_pass=False)
    session.commit()

    index = build_evidence_index(session, policy, task_family="ui_cosmetic")
    assert index.lookup("ui_cosmetic", "codex", "sol", Effort.HIGH).weighted_attempts == 1.0
    assert index.lookup("security", "codex", "sol", Effort.HIGH).weighted_attempts == 0.0


# --------------------------------------------------------------------------
# Rollup
# --------------------------------------------------------------------------


def test_rollup_accumulates(session):
    for _ in range(3):
        execution, metrics, escalation = _add_execution(session)
        apply_execution(session, execution, metrics, escalation)
    session.commit()

    stat = session.query(RoutingStatistic).one()
    assert stat.attempts == 3
    assert stat.successes == 3
    assert stat.first_pass_successes == 3
    assert stat.avg_effective_burn == pytest.approx(3.0)


def test_retract_undoes_exactly_what_apply_added(session):
    execution, metrics, escalation = _add_execution(session)
    apply_execution(session, execution, metrics, escalation)
    session.commit()

    before = session.query(RoutingStatistic).one()
    assert before.attempts == 1

    retract_execution(session, execution, metrics, escalation)
    session.commit()

    after = session.query(RoutingStatistic).one()
    assert after.attempts == 0
    assert after.successes == 0
    assert after.burn_samples == 0
    assert after.total_estimated_burn == pytest.approx(0.0)


def test_rebuild_reproduces_the_rollup(session):
    for status in ("success", "failed", "partial"):
        execution, metrics, escalation = _add_execution(
            session, status=status, first_pass=status == "success"
        )
        apply_execution(session, execution, metrics, escalation)
    session.commit()

    incremental = {
        (s.task_family, s.provider, s.model, s.effort): (s.attempts, s.successes, s.failures)
        for s in session.query(RoutingStatistic).all()
    }

    rebuild_all(session)
    session.commit()

    rebuilt = {
        (s.task_family, s.provider, s.model, s.effort): (s.attempts, s.successes, s.failures)
        for s in session.query(RoutingStatistic).all()
    }
    assert incremental == rebuilt


def test_the_rollup_never_goes_negative(session):
    """Retracting something that was never applied must not corrupt the counts."""
    execution, metrics, escalation = _add_execution(session)
    retract_execution(session, execution, metrics, escalation)
    session.commit()
    assert session.query(RoutingStatistic).count() == 0
