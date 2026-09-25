"""Analytics: what the recorded history actually says.

Two findings matter more than any chart:

* UNDERPOWERED - a configuration that keeps needing debugging, corrections or
  escalation on a family. Starting stronger next time is cheaper.
* OVERPOWERED - an expensive configuration whose measured reliability a much
  cheaper one already matches. Starting lighter next time is cheaper.

Both claims are gated on having enough evidence to make them. Saying "Sol is
overpowered here" off two executions would be worse than saying nothing.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from ..config import ConfigBundle
from ..core.learning.bayesian import posterior
from ..db.models import Escalation, Execution, ExecutionMetrics, RoutingStatistic
from ..schemas.enums import OutcomeStatus


@dataclass
class Totals:
    executions: int = 0
    successes: int = 0
    partials: int = 0
    failures: int = 0
    first_pass_successes: int = 0
    escalations: int = 0
    regressions: int = 0
    recommendation_followed: int = 0
    measured_usage_reports: int = 0

    @property
    def success_rate(self) -> float | None:
        return self.successes / self.executions if self.executions else None

    @property
    def first_pass_rate(self) -> float | None:
        return self.first_pass_successes / self.executions if self.executions else None

    @property
    def failure_rate(self) -> float | None:
        return self.failures / self.executions if self.executions else None

    @property
    def escalation_rate(self) -> float | None:
        return self.escalations / self.executions if self.executions else None

    @property
    def recommendation_followed_rate(self) -> float | None:
        return (
            self.recommendation_followed / self.executions if self.executions else None
        )


@dataclass
class ConfigurationPerformance:
    task_family: str
    provider: str
    model: str
    effort: str
    provider_display: str
    model_display: str
    attempts: int
    successes: int
    failures: int
    first_pass_successes: int
    escalations: int
    success_rate: float | None
    smoothed_success_rate: float
    avg_debug_cycles: float | None
    avg_effective_burn: float | None


@dataclass
class Finding:
    kind: str
    task_family: str
    configuration: str
    alternative: str | None
    detail: str
    evidence: int


@dataclass
class Analytics:
    totals: Totals
    by_configuration: list[ConfigurationPerformance] = field(default_factory=list)
    by_family: list[dict[str, Any]] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        totals = asdict(self.totals)
        totals.update(
            {
                "success_rate": self.totals.success_rate,
                "first_pass_rate": self.totals.first_pass_rate,
                "failure_rate": self.totals.failure_rate,
                "escalation_rate": self.totals.escalation_rate,
                "recommendation_followed_rate": self.totals.recommendation_followed_rate,
            }
        )
        return {
            "totals": totals,
            "by_configuration": [asdict(c) for c in self.by_configuration],
            "by_family": self.by_family,
            "findings": [asdict(f) for f in self.findings],
            "notes": self.notes,
        }


def compute_analytics(session: Session, config: ConfigBundle) -> Analytics:
    totals = _totals(session)
    performance = _performance(session, config)
    analytics = Analytics(
        totals=totals,
        by_configuration=performance,
        by_family=_by_family(session),
        findings=_findings(performance, config),
    )

    if totals.executions == 0:
        analytics.notes.append(
            "No executions recorded yet. Every recommendation is currently based on base "
            "routing policy alone."
        )
    elif totals.executions < 20:
        analytics.notes.append(
            f"{totals.executions} executions recorded. Patterns below are indicative, not "
            "conclusive; the specification suggests collecting 50 or more before retuning "
            "the registry by hand."
        )
    if totals.measured_usage_reports == 0 and totals.executions:
        analytics.notes.append(
            "No execution reported provider token usage. Effective burn figures are "
            "estimates reconstructed from work signals, not measurements."
        )
    return analytics


def _totals(session: Session) -> Totals:
    totals = Totals()
    rows = session.execute(
        select(Execution, ExecutionMetrics)
        .outerjoin(ExecutionMetrics, ExecutionMetrics.execution_id == Execution.id)
    ).all()

    for execution, metrics in rows:
        totals.executions += 1
        if execution.status == OutcomeStatus.SUCCESS.value:
            totals.successes += 1
        elif execution.status == OutcomeStatus.PARTIAL.value:
            totals.partials += 1
        else:
            totals.failures += 1
        if execution.recommendation_followed:
            totals.recommendation_followed += 1
        if metrics is not None:
            if metrics.first_pass_success:
                totals.first_pass_successes += 1
            if metrics.regression_found:
                totals.regressions += 1
            if metrics.usage_source == "provider_reported":
                totals.measured_usage_reports += 1

    totals.escalations = (
        session.execute(select(func.count(Escalation.id))).scalar() or 0
    )
    return totals


def _performance(session: Session, config: ConfigBundle) -> list[ConfigurationPerformance]:
    stats = session.execute(
        select(RoutingStatistic).where(RoutingStatistic.attempts > 0)
    ).scalars().all()

    out: list[ConfigurationPerformance] = []
    for stat in stats:
        provider_display, model_display = config.registry.display(stat.provider, stat.model)
        # A neutral 0.5 prior with modest strength: analytics should not inherit
        # the router's task-specific prior, because this is a retrospective view
        # of what happened rather than a prediction about a particular task.
        smoothed = posterior(
            prior_mean=0.5,
            prior_strength=4.0,
            successes=stat.successes + 0.5 * stat.partials,
            attempts=stat.attempts,
        ).mean
        out.append(
            ConfigurationPerformance(
                task_family=stat.task_family,
                provider=stat.provider,
                model=stat.model,
                effort=stat.effort,
                provider_display=provider_display,
                model_display=model_display,
                attempts=stat.attempts,
                successes=stat.successes,
                failures=stat.failures,
                first_pass_successes=stat.first_pass_successes,
                escalations=stat.escalations,
                success_rate=stat.success_rate,
                smoothed_success_rate=smoothed,
                avg_debug_cycles=stat.avg_debug_cycles,
                avg_effective_burn=stat.avg_effective_burn,
            )
        )
    out.sort(key=lambda c: (c.task_family, -c.attempts, c.provider, c.model, c.effort))
    return out


def _by_family(session: Session) -> list[dict[str, Any]]:
    rows = session.execute(
        select(
            Execution.task_family,
            func.count(Execution.id),
            func.sum(
                case((Execution.status == OutcomeStatus.SUCCESS.value, 1), else_=0)
            ),
        ).group_by(Execution.task_family)
    ).all()
    return [
        {
            "task_family": family,
            "executions": int(count or 0),
            "successes": int(successes or 0),
            "success_rate": (float(successes or 0) / count) if count else None,
        }
        for family, count, successes in rows
    ]


def _findings(
    performance: list[ConfigurationPerformance], config: ConfigBundle
) -> list[Finding]:
    policy = config.policy.overpowering
    findings: list[Finding] = []

    by_family: dict[str, list[ConfigurationPerformance]] = {}
    for entry in performance:
        by_family.setdefault(entry.task_family, []).append(entry)

    for family, entries in by_family.items():
        # --- Underpowered -----------------------------------------------------
        for entry in entries:
            if entry.attempts < policy.min_evidence:
                continue
            escalation_rate = entry.escalations / entry.attempts
            debug = entry.avg_debug_cycles or 0.0
            if escalation_rate >= 0.25 or (entry.success_rate or 1.0) < 0.70 or debug >= 2.0:
                reasons = []
                if escalation_rate >= 0.25:
                    reasons.append(f"escalated {escalation_rate:.0%} of the time")
                if (entry.success_rate or 1.0) < 0.70:
                    reasons.append(f"succeeded only {(entry.success_rate or 0):.0%} of the time")
                if debug >= 2.0:
                    reasons.append(f"averaged {debug:.1f} debug cycles")
                findings.append(
                    Finding(
                        kind="underpowered",
                        task_family=family,
                        configuration=f"{entry.provider_display} {entry.model_display} ({entry.effort})",
                        alternative=None,
                        detail=(
                            f"On {family} this configuration "
                            + " and ".join(reasons)
                            + " across "
                            f"{entry.attempts} executions. Starting stronger is likely cheaper overall."
                        ),
                        evidence=entry.attempts,
                    )
                )

        # --- Overpowered ------------------------------------------------------
        priced = [
            e
            for e in entries
            if e.attempts >= policy.min_evidence and e.avg_effective_burn
        ]
        for expensive in priced:
            for cheaper in priced:
                if cheaper is expensive:
                    continue
                if cheaper.avg_effective_burn >= expensive.avg_effective_burn * policy.burn_ratio_threshold:
                    continue
                gap = expensive.smoothed_success_rate - cheaper.smoothed_success_rate
                if gap > policy.reliability_tolerance:
                    continue
                findings.append(
                    Finding(
                        kind="overpowered",
                        task_family=family,
                        configuration=f"{expensive.provider_display} {expensive.model_display} ({expensive.effort})",
                        alternative=f"{cheaper.provider_display} {cheaper.model_display} ({cheaper.effort})",
                        detail=(
                            f"On {family}, the cheaper configuration matched it within "
                            f"{abs(gap):.1%} smoothed success "
                            f"({cheaper.smoothed_success_rate:.0%} against "
                            f"{expensive.smoothed_success_rate:.0%}) at "
                            f"{cheaper.avg_effective_burn:.2f} against "
                            f"{expensive.avg_effective_burn:.2f} effective burn."
                        ),
                        evidence=min(expensive.attempts, cheaper.attempts),
                    )
                )

    findings.sort(key=lambda f: (-f.evidence, f.kind, f.task_family))
    return findings[:20]
