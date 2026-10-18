"""Whether a task should be planned, or specified and planned, before execution.

Derived from the fingerprint with fixed rules rather than asked of the
analyzer: the analyzer already scores complexity, clarity, ambiguity and scope,
and keeping the judgement here makes it deterministic, testable, and identical
for the Pi and offline analyzers.

Two independent axes, mirroring the analyzer prompt:

- Is the task hard?           -> write an implementation plan first.
- Is the request underspecified? -> write a spec first, then the plan.

A vague but trivial task ("make the button nicer") still runs directly: a spec
for a one-line change costs more than getting it slightly wrong.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from ..schemas.enums import InputType, Scope
from ..schemas.task_fingerprint import TaskFingerprint

PreparationMode = Literal["direct", "plan", "spec_and_plan"]

#: The task is hard enough that an executor should commit to an approach first.
PLAN_COMPLEXITY = 0.55
PLAN_ARCHITECTURE = 0.6
PLAN_FILES = 6
_WIDE_SCOPES = {Scope.SERVICE, Scope.MULTI_SERVICE, Scope.ARCHITECTURE}

#: The request leaves too much open to plan against directly.
SPEC_CLARITY = 0.55
SPEC_AMBIGUITY = 0.5
#: ...but only once the task is substantial enough for a spec to pay off.
SPEC_MIN_COMPLEXITY = 0.3

PLAN_PATH = "docs/plans/{task_id}-plan.md"
SPEC_PATH = "docs/specs/{task_id}-spec.md"


class Preparation(BaseModel):
    mode: PreparationMode
    headline: str
    reasons: list[str]
    #: Repository-relative files the executing agent writes before coding.
    spec_path: str | None = None
    plan_path: str | None = None


def recommend_preparation(
    fingerprint: TaskFingerprint, task_id: str, input_type: InputType = InputType.TASK
) -> Preparation:
    """What to do before executing. Never asks for a stage the input already is:
    a plan is executed as it stands, and a spec is never told to write a spec."""
    fp = fingerprint
    if input_type == InputType.PLAN:
        return Preparation(
            mode="direct",
            headline="This is an implementation plan: hand it to the recommended model to execute.",
            reasons=[],
        )
    hard: list[str] = []
    if fp.complexity >= PLAN_COMPLEXITY:
        hard.append(f"complexity {fp.complexity:.2f}")
    if fp.architecture_reasoning >= PLAN_ARCHITECTURE:
        hard.append(f"architecture reasoning {fp.architecture_reasoning:.2f}")
    if fp.scope in _WIDE_SCOPES:
        hard.append(f"{fp.scope.value.replace('_', ' ')} scope")
    if fp.estimated_files >= PLAN_FILES:
        hard.append(f"about {fp.estimated_files} files")

    vague: list[str] = []
    if fp.requirements_clarity < SPEC_CLARITY:
        vague.append(f"requirements clarity only {fp.requirements_clarity:.2f}")
    if fp.ambiguity >= SPEC_AMBIGUITY:
        vague.append(f"ambiguity {fp.ambiguity:.2f}")
    substantial = bool(hard) or fp.complexity >= SPEC_MIN_COMPLEXITY

    if input_type == InputType.SPEC:
        gaps = (
            " The spec also leaves decisions open; the model will make the most reasonable "
            "choice for each and record it, so tighten the spec first if they matter."
            if vague
            else ""
        )
        if hard:
            return Preparation(
                mode="plan",
                headline=(
                    "The spec is complex. Write an implementation plan from it first, then "
                    "execute the plan." + gaps
                ),
                reasons=hard + vague,
                plan_path=PLAN_PATH.format(task_id=task_id),
            )
        return Preparation(
            mode="direct",
            headline="The spec is small enough to implement directly." + gaps,
            reasons=vague,
        )

    if vague and substantial:
        return Preparation(
            mode="spec_and_plan",
            headline=(
                "The prompt does not pin down enough to build against. Write a detailed spec, "
                "then an implementation plan, then execute the plan."
            ),
            reasons=vague + hard,
            spec_path=SPEC_PATH.format(task_id=task_id),
            plan_path=PLAN_PATH.format(task_id=task_id),
        )
    if hard:
        return Preparation(
            mode="plan",
            headline=(
                "The prompt is clear, but the task is complex. Write an implementation plan "
                "first, then execute it."
            ),
            reasons=hard,
            plan_path=PLAN_PATH.format(task_id=task_id),
        )
    return Preparation(
        mode="direct",
        headline="The prompt is clear enough and the task small enough to execute directly.",
        reasons=[],
    )


def handoff_steps(preparation: Preparation, spec_source: str | None = None) -> list[str]:
    """Instructions for the executing agent, in the order it should follow them.

    `spec_source` names the spec the plan is written from, when the input
    itself is a spec: its repository path, or "below" when it is inline.
    """
    if preparation.mode == "direct":
        return []
    steps = ["PREPARATION (do this before writing any code)", ""]
    n = 1
    if preparation.spec_path:
        steps.append(
            f"{n}. Write a spec to {preparation.spec_path}: the goal, the users and their "
            "workflows, functional requirements, non-goals, edge cases and error states, and "
            "acceptance criteria. Read the relevant code first. Wherever the task leaves a "
            "decision open, make the most reasonable choice and record it under 'Assumptions'."
        )
        n += 1
    if preparation.spec_path:
        source = " from that spec"
    elif spec_source == "below":
        source = " from the spec below"
    elif spec_source:
        source = f" from the spec in {spec_source}"
    else:
        source = ""
    steps.append(
        f"{n}. Write an implementation plan to {preparation.plan_path}"
        + source
        + ": the files to change, ordered steps that each leave the build working, the "
        "tests that prove each step, and the risks with how to check for them."
    )
    n += 1
    steps.append(
        f"{n}. Then implement the plan step by step. If the code shows the plan is wrong, "
        "update the plan file before diverging from it."
    )
    steps.append("")
    return steps
