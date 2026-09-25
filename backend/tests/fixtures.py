"""Reference task fingerprints.

Thirty representative tasks spanning the families the router has to get right,
each with the routing band it is expected to land in. These test ROUTING
SANITY, not exact model names: the assertion is "a cosmetic tweak must not
reach for a premium model at max effort", never "this task must select Sol".
Pinning exact models would make every registry retune look like a regression.

`max_burn` and `min_burn` are in the router's normalised quota units, and are
set loosely enough that reasonable retuning passes while an inverted
recommendation does not.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.schemas.task_fingerprint import TaskFingerprint


@dataclass(frozen=True)
class Expectation:
    """A band, not a point. See the module docstring."""

    name: str
    fingerprint: TaskFingerprint
    #: Efforts that would be absurd for this task.
    forbidden_efforts: tuple[str, ...] = ()
    #: Models that would be absurd for this task.
    forbidden_models: tuple[str, ...] = ()
    #: If set, the selected configuration must be at least one of these models.
    allowed_models: tuple[str, ...] = ()
    max_burn: float | None = None
    min_burn: float | None = None
    notes: str = ""


def fp(**overrides) -> TaskFingerprint:
    """A neutral fingerprint with the given dimensions overridden."""
    base: dict = {
        "task_family": "backend_business_logic",
        "task_type": "feature",
        "complexity": 0.4,
        "ambiguity": 0.2,
        "requirements_clarity": 0.8,
        "regression_risk": 0.3,
        "architecture_reasoning": 0.2,
        "database_reasoning": 0.2,
        "concurrency_risk": 0.1,
        "security_risk": 0.1,
        "repository_understanding": 0.4,
        "scope": "single_file",
        "estimated_files": 2,
        "frontend": False,
        "backend": True,
        "database": False,
        "infrastructure": False,
        "tests_required": "medium",
        "confidence": 0.85,
    }
    base.update(overrides)
    return TaskFingerprint.model_validate(base)


#: The premium tier. Routing any of these to a trivial task is a bug.
PREMIUM_MODELS = ("fable_5_1", "astra")
#: The light tier. Routing any of these to a payments race condition is a bug.
LIGHT_MODELS = ("haiku_4_5", "luna")
HEAVY_EFFORTS = ("xhigh", "max", "ultra")


EXPECTATIONS: tuple[Expectation, ...] = (
    # --- 1-5: trivial and near-trivial -------------------------------------
    Expectation(
        "change button text",
        fp(task_family="ui_cosmetic", task_type="copy_change", complexity=0.05,
           ambiguity=0.05, requirements_clarity=0.98, regression_risk=0.03,
           architecture_reasoning=0.02, database_reasoning=0.0, concurrency_risk=0.0,
           security_risk=0.0, repository_understanding=0.05, scope="single_line",
           estimated_files=1, frontend=True, backend=False, tests_required="low",
           confidence=0.96),
        forbidden_models=PREMIUM_MODELS,
        forbidden_efforts=HEAVY_EFFORTS,
        max_burn=1.5,
        notes="A one-word copy change must never reach for a premium model.",
    ),
    Expectation(
        "change CSS padding",
        fp(task_family="ui_cosmetic", task_type="styling", complexity=0.06,
           ambiguity=0.08, requirements_clarity=0.94, regression_risk=0.05,
           architecture_reasoning=0.02, database_reasoning=0.0, concurrency_risk=0.0,
           security_risk=0.0, repository_understanding=0.08, scope="single_file",
           estimated_files=1, frontend=True, backend=False, tests_required="low",
           confidence=0.94),
        forbidden_models=PREMIUM_MODELS,
        forbidden_efforts=HEAVY_EFFORTS,
        max_burn=1.5,
    ),
    Expectation(
        "documentation update",
        fp(task_family="documentation", task_type="readme", complexity=0.15,
           ambiguity=0.2, requirements_clarity=0.85, regression_risk=0.02,
           architecture_reasoning=0.1, database_reasoning=0.0, concurrency_risk=0.0,
           security_risk=0.0, repository_understanding=0.3, scope="single_file",
           estimated_files=1, backend=False, tests_required="low", confidence=0.9),
        forbidden_models=PREMIUM_MODELS,
        forbidden_efforts=HEAVY_EFFORTS,
        max_burn=1.5,
    ),
    Expectation(
        "mechanical rename across 40 files",
        fp(task_family="refactor_local", task_type="rename", complexity=0.10,
           ambiguity=0.05, requirements_clarity=0.97, regression_risk=0.15,
           architecture_reasoning=0.05, database_reasoning=0.02, concurrency_risk=0.0,
           security_risk=0.0, repository_understanding=0.2, scope="multi_file",
           estimated_files=40, tests_required="low", confidence=0.93),
        forbidden_models=PREMIUM_MODELS,
        max_burn=2.5,
        notes="Volume is not difficulty. Forty files of mechanical edits stay cheap.",
    ),
    Expectation(
        "add form validation",
        fp(task_family="ui_behavior", task_type="feature", complexity=0.3,
           ambiguity=0.2, requirements_clarity=0.85, regression_risk=0.25,
           architecture_reasoning=0.15, database_reasoning=0.05, concurrency_risk=0.05,
           security_risk=0.2, repository_understanding=0.3, scope="single_file",
           estimated_files=2, frontend=True, tests_required="medium", confidence=0.88),
        forbidden_models=PREMIUM_MODELS,
        max_burn=3.5,
    ),
    # --- 6-12: routine implementation ---------------------------------------
    Expectation(
        "straightforward REST endpoint",
        fp(task_family="api_implementation", complexity=0.35, ambiguity=0.2,
           requirements_clarity=0.85, regression_risk=0.3, architecture_reasoning=0.25,
           database_reasoning=0.2, concurrency_risk=0.1, security_risk=0.25,
           repository_understanding=0.35, scope="single_file", estimated_files=2,
           tests_required="medium", confidence=0.88),
        forbidden_models=PREMIUM_MODELS,
        max_burn=4.0,
    ),
    Expectation(
        "add database field with simple migration",
        fp(task_family="database_migration", complexity=0.4, ambiguity=0.15,
           requirements_clarity=0.9, regression_risk=0.45, architecture_reasoning=0.2,
           database_reasoning=0.7, concurrency_risk=0.05, security_risk=0.05,
           repository_understanding=0.4, scope="multi_file", estimated_files=3,
           database=True, tests_required="medium", confidence=0.9),
        forbidden_models=("fable_5_1",),
        min_burn=1.0,
    ),
    Expectation(
        "unit test generation",
        fp(task_family="testing", complexity=0.32, ambiguity=0.18,
           requirements_clarity=0.88, regression_risk=0.15, architecture_reasoning=0.15,
           database_reasoning=0.1, concurrency_risk=0.05, security_risk=0.05,
           repository_understanding=0.5, scope="single_file", estimated_files=2,
           tests_required="high", confidence=0.9),
        forbidden_models=PREMIUM_MODELS,
        max_burn=4.0,
    ),
    Expectation(
        "CI/CD pipeline fix",
        fp(task_family="devops", complexity=0.45, ambiguity=0.35,
           requirements_clarity=0.7, regression_risk=0.4, architecture_reasoning=0.3,
           database_reasoning=0.05, concurrency_risk=0.1, security_risk=0.3,
           repository_understanding=0.45, scope="single_file", estimated_files=2,
           backend=False, infrastructure=True, tests_required="low", confidence=0.78),
        forbidden_models=("fable_5_1",),
    ),
    Expectation(
        "frontend state synchronisation",
        fp(task_family="frontend_state", complexity=0.52, ambiguity=0.3,
           requirements_clarity=0.75, regression_risk=0.48, architecture_reasoning=0.42,
           database_reasoning=0.05, concurrency_risk=0.25, security_risk=0.05,
           repository_understanding=0.55, scope="multi_file", estimated_files=4,
           frontend=True, backend=False, tests_required="medium", confidence=0.82),
        forbidden_models=("fable_5_1",),
    ),
    Expectation(
        "repository analysis",
        fp(task_family="repository_analysis", complexity=0.45, ambiguity=0.25,
           requirements_clarity=0.8, regression_risk=0.02, architecture_reasoning=0.5,
           database_reasoning=0.25, concurrency_risk=0.2, security_risk=0.15,
           repository_understanding=0.88, scope="multi_file", estimated_files=0,
           tests_required="low", confidence=0.85),
        forbidden_efforts=("max", "ultra"),
        notes="Read-only work carries no regression risk, so the bar stays low.",
    ),
    Expectation(
        "local bug fix with known reproduction",
        fp(task_family="bug_fix_local", complexity=0.35, ambiguity=0.2,
           requirements_clarity=0.85, regression_risk=0.35, architecture_reasoning=0.15,
           database_reasoning=0.15, concurrency_risk=0.05, security_risk=0.05,
           repository_understanding=0.45, scope="single_function", estimated_files=1,
           tests_required="medium", confidence=0.9),
        forbidden_models=PREMIUM_MODELS,
        max_burn=4.0,
    ),
    # --- 13-22: hard -------------------------------------------------------
    Expectation(
        "multi-file backend logic fix",
        fp(task_family="backend_business_logic", complexity=0.62, ambiguity=0.28,
           requirements_clarity=0.78, regression_risk=0.6, architecture_reasoning=0.45,
           database_reasoning=0.4, concurrency_risk=0.15, security_risk=0.1,
           repository_understanding=0.65, scope="multi_file", estimated_files=5,
           tests_required="high", confidence=0.84),
        forbidden_models=LIGHT_MODELS,
    ),
    Expectation(
        "deduplication identity semantics",
        fp(task_family="backend_data_integrity", task_type="bug_fix", complexity=0.84,
           ambiguity=0.16, requirements_clarity=0.93, regression_risk=0.88,
           architecture_reasoning=0.78, database_reasoning=0.91, concurrency_risk=0.10,
           security_risk=0.05, repository_understanding=0.72, scope="multi_file",
           estimated_files=5, database=True, tests_required="high", confidence=0.91),
        forbidden_models=LIGHT_MODELS,
        min_burn=2.0,
        notes="The specification's worked example. High clarity must not force Max.",
    ),
    Expectation(
        "preserve provenance across persistence change",
        fp(task_family="backend_data_integrity", complexity=0.78, ambiguity=0.22,
           requirements_clarity=0.88, regression_risk=0.85, architecture_reasoning=0.7,
           database_reasoning=0.88, concurrency_risk=0.12, security_risk=0.08,
           repository_understanding=0.75, scope="multi_file", estimated_files=6,
           database=True, tests_required="high", confidence=0.87),
        forbidden_models=LIGHT_MODELS,
        min_burn=2.0,
    ),
    Expectation(
        "cross-system bug fix",
        fp(task_family="bug_fix_cross_system", complexity=0.7, ambiguity=0.35,
           requirements_clarity=0.7, regression_risk=0.72, architecture_reasoning=0.58,
           database_reasoning=0.45, concurrency_risk=0.4, security_risk=0.2,
           repository_understanding=0.78, scope="multi_service", estimated_files=6,
           tests_required="high", confidence=0.78),
        forbidden_models=LIGHT_MODELS,
    ),
    Expectation(
        "idempotency bug",
        fp(task_family="concurrency", complexity=0.8, ambiguity=0.3,
           requirements_clarity=0.8, regression_risk=0.8, architecture_reasoning=0.6,
           database_reasoning=0.65, concurrency_risk=0.88, security_risk=0.3,
           repository_understanding=0.72, scope="service", estimated_files=4,
           database=True, tests_required="high", confidence=0.82),
        forbidden_models=LIGHT_MODELS,
        forbidden_efforts=("low", "none"),
    ),
    Expectation(
        "duplicate payments race condition",
        fp(task_family="concurrency", task_type="bug_fix", complexity=0.9,
           ambiguity=0.3, requirements_clarity=0.8, regression_risk=0.85,
           architecture_reasoning=0.7, database_reasoning=0.6, concurrency_risk=0.95,
           security_risk=0.5, repository_understanding=0.8, scope="multi_service",
           estimated_files=8, database=True, tests_required="high", confidence=0.85),
        forbidden_models=LIGHT_MODELS,
        forbidden_efforts=("low", "none"),
        min_burn=4.0,
        notes="The nastiest category. Must buy real capability.",
    ),
    Expectation(
        "authentication change",
        fp(task_family="security", complexity=0.7, ambiguity=0.25,
           requirements_clarity=0.82, regression_risk=0.7, architecture_reasoning=0.55,
           database_reasoning=0.35, concurrency_risk=0.2, security_risk=0.95,
           repository_understanding=0.6, scope="multi_file", estimated_files=6,
           tests_required="high", confidence=0.87),
        forbidden_models=LIGHT_MODELS,
        forbidden_efforts=("low", "none"),
        min_burn=3.0,
    ),
    Expectation(
        "authorization bug",
        fp(task_family="security", task_type="bug_fix", complexity=0.6,
           ambiguity=0.3, requirements_clarity=0.78, regression_risk=0.65,
           architecture_reasoning=0.45, database_reasoning=0.3, concurrency_risk=0.15,
           security_risk=0.9, repository_understanding=0.62, scope="multi_file",
           estimated_files=4, tests_required="high", confidence=0.84),
        forbidden_models=LIGHT_MODELS,
        forbidden_efforts=("low", "none"),
    ),
    Expectation(
        "multi-service refactor",
        fp(task_family="refactor_architectural", complexity=0.76, ambiguity=0.28,
           requirements_clarity=0.82, regression_risk=0.78, architecture_reasoning=0.9,
           database_reasoning=0.4, concurrency_risk=0.3, security_risk=0.2,
           repository_understanding=0.88, scope="multi_service", estimated_files=14,
           tests_required="high", confidence=0.8),
        forbidden_models=LIGHT_MODELS,
        min_burn=2.5,
    ),
    Expectation(
        "architecture design",
        fp(task_family="architecture_design", complexity=0.82, ambiguity=0.45,
           requirements_clarity=0.6, regression_risk=0.25, architecture_reasoning=0.95,
           database_reasoning=0.5, concurrency_risk=0.35, security_risk=0.35,
           repository_understanding=0.6, scope="architecture", estimated_files=0,
           tests_required="low", confidence=0.72),
        forbidden_models=LIGHT_MODELS,
        notes="No code is written, so regression risk is low even though it is hard.",
    ),
    # --- 23-30: investigation and edges -------------------------------------
    Expectation(
        "complex root cause analysis",
        fp(task_family="root_cause_analysis", complexity=0.85, ambiguity=0.6,
           requirements_clarity=0.5, regression_risk=0.5, architecture_reasoning=0.65,
           database_reasoning=0.5, concurrency_risk=0.55, security_risk=0.2,
           repository_understanding=0.9, scope="multi_service", estimated_files=3,
           tests_required="medium", confidence=0.62),
        forbidden_models=LIGHT_MODELS,
    ),
    Expectation(
        "performance investigation",
        fp(task_family="performance", complexity=0.65, ambiguity=0.45,
           requirements_clarity=0.6, regression_risk=0.45, architecture_reasoning=0.55,
           database_reasoning=0.6, concurrency_risk=0.35, security_risk=0.05,
           repository_understanding=0.75, scope="multi_file", estimated_files=4,
           tests_required="medium", confidence=0.72),
        forbidden_models=("haiku_4_5",),
    ),
    Expectation(
        "debugging with unknown cause",
        fp(task_family="debugging", complexity=0.6, ambiguity=0.55,
           requirements_clarity=0.55, regression_risk=0.5, architecture_reasoning=0.35,
           database_reasoning=0.3, concurrency_risk=0.25, security_risk=0.1,
           repository_understanding=0.7, scope="multi_file", estimated_files=3,
           tests_required="medium", confidence=0.65),
    ),
    Expectation(
        "database schema redesign",
        fp(task_family="database_schema", complexity=0.72, ambiguity=0.3,
           requirements_clarity=0.8, regression_risk=0.75, architecture_reasoning=0.65,
           database_reasoning=0.92, concurrency_risk=0.2, security_risk=0.15,
           repository_understanding=0.6, scope="multi_file", estimated_files=6,
           database=True, tests_required="high", confidence=0.84),
        forbidden_models=LIGHT_MODELS,
        min_burn=2.0,
    ),
    Expectation(
        "very long but very simple task",
        fp(task_family="refactor_local", complexity=0.12, ambiguity=0.06,
           requirements_clarity=0.96, regression_risk=0.18, architecture_reasoning=0.06,
           database_reasoning=0.03, concurrency_risk=0.0, security_risk=0.0,
           repository_understanding=0.25, scope="multi_file", estimated_files=120,
           tests_required="low", confidence=0.92),
        forbidden_models=PREMIUM_MODELS,
        max_burn=3.0,
        notes="120 files, still trivial per file. Prompt length must not drive cost.",
    ),
    Expectation(
        "high complexity with perfect clarity",
        fp(task_family="backend_business_logic", complexity=0.88, ambiguity=0.05,
           requirements_clarity=0.98, regression_risk=0.55, architecture_reasoning=0.6,
           database_reasoning=0.4, concurrency_risk=0.1, security_risk=0.1,
           repository_understanding=0.5, scope="multi_file", estimated_files=5,
           tests_required="high", confidence=0.94),
        forbidden_efforts=("max", "ultra"),
        notes="Clarity plus difficulty must not automatically escalate to Max.",
    ),
    Expectation(
        "ambiguous low-risk request",
        fp(task_family="ui_behavior", complexity=0.3, ambiguity=0.75,
           requirements_clarity=0.3, regression_risk=0.2, architecture_reasoning=0.2,
           database_reasoning=0.05, concurrency_risk=0.05, security_risk=0.05,
           repository_understanding=0.4, scope="single_file", estimated_files=2,
           frontend=True, backend=False, tests_required="low", confidence=0.5),
        forbidden_models=PREMIUM_MODELS,
        notes="Ambiguity lowers router confidence; it does not raise the reliability bar.",
    ),
    Expectation(
        "cross-system data integrity under concurrency",
        fp(task_family="backend_data_integrity", complexity=0.92, ambiguity=0.35,
           requirements_clarity=0.75, regression_risk=0.92, architecture_reasoning=0.85,
           database_reasoning=0.95, concurrency_risk=0.8, security_risk=0.4,
           repository_understanding=0.9, scope="multi_service", estimated_files=12,
           database=True, tests_required="high", confidence=0.76),
        forbidden_models=LIGHT_MODELS,
        forbidden_efforts=("low", "none"),
        min_burn=4.0,
        notes="The hardest fixture. Should approach the top of the registry.",
    ),
)


def by_name(name: str) -> Expectation:
    for expectation in EXPECTATIONS:
        if expectation.name == name:
            return expectation
    raise KeyError(name)


VALID_RECEIPT_TEMPLATE: dict = {
    "receipt_version": "1.0",
    "task_id": "RT-000001",
    "execution": {"provider": "codex", "model": "sol", "effort": "high"},
    "outcome": {
        "status": "success",
        "implementation_complete": True,
        "first_pass_success": True,
        "tests_passed": True,
        "build_passed": True,
        "known_regression": False,
    },
    "work": {
        "files_read": 9,
        "files_modified": 4,
        "debug_cycles": 0,
        "major_replans": 0,
        "user_corrections": 0,
    },
    "escalation": {"occurred": False},
    "usage": {
        "input_tokens": None,
        "output_tokens": None,
        "reasoning_tokens": None,
        "source": "unavailable",
    },
    "agent_assessment": {
        "actual_complexity": "high",
        "recommendation_fit": "appropriate",
        "notes": "",
    },
}


def receipt(task_id: str, **overrides) -> dict:
    """A valid receipt, deep-merged with the given overrides."""
    import copy

    data = copy.deepcopy(VALID_RECEIPT_TEMPLATE)
    data["task_id"] = task_id
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(data.get(key), dict):
            data[key].update(value)
        else:
            data[key] = value
    return data


def wrap(payload: dict) -> str:
    import json

    return f"[LLM-ROUTER-RESULT]\n{json.dumps(payload, indent=2)}\n[/LLM-ROUTER-RESULT]"
