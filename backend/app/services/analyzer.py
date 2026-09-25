"""Task analyzer abstraction.

The backend talks to an interface, never to Pi directly. Two implementations
ship: the Pi bridge client, and a deterministic heuristic analyzer used by the
test suite and as a graceful degradation when the bridge is not running.

Keeping Pi behind this seam is what makes the rest of the application testable
without a subscription, a network, or a running agent.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass

from ..schemas.enums import Scope, TestIntensity
from ..schemas.task_fingerprint import (
    AnalyzerMetadata,
    FingerprintEnvelope,
    TaskFingerprint,
)


@dataclass(frozen=True)
class AnalyzerOverrides:
    """Which analyzer to classify with, chosen by the user in Settings.

    Threaded through to the Pi bridge on every request so a preference change
    takes effect on the next analysis without restarting anything. All three
    fields are optional; whatever is omitted falls back to the bridge's own
    boot configuration.
    """

    provider: str | None = None
    model: str | None = None
    thinking_level: str | None = None

    def as_payload(self) -> dict[str, str]:
        return {
            key: value
            for key, value in (
                ("provider", self.provider),
                ("model", self.model),
                ("thinking_level", self.thinking_level),
            )
            if value
        }

    @property
    def is_empty(self) -> bool:
        return not self.as_payload()


class AnalyzerError(RuntimeError):
    """The analyzer could not produce a valid fingerprint."""

    def __init__(self, message: str, *, detail: str | None = None) -> None:
        super().__init__(message)
        self.detail = detail


class TaskAnalyzer(ABC):
    """Turns an unstructured task into a validated TaskFingerprint."""

    name: str = "analyzer"

    @abstractmethod
    async def analyze(
        self, task: str, overrides: AnalyzerOverrides | None = None
    ) -> FingerprintEnvelope: ...

    @abstractmethod
    async def status(self) -> dict: ...


# --------------------------------------------------------------------------
# Heuristic fallback
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class _FamilyRule:
    family: str
    patterns: tuple[str, ...]
    complexity: float
    regression: float
    architecture: float
    database: float
    concurrency: float
    security: float
    repo: float
    scope: Scope
    files: int
    tests: TestIntensity
    frontend: bool = False
    backend: bool = False
    database_flag: bool = False
    infrastructure: bool = False


#: Ordered most-specific first. The first rule whose pattern matches wins.
_RULES: tuple[_FamilyRule, ...] = (
    _FamilyRule(
        "concurrency",
        (r"\brace condition\b", r"\bdeadlock\b", r"\bidempoten", r"\bconcurren",
         r"\bthread[- ]safe", r"\bmutex\b", r"\block contention\b", r"\bdouble[- ]charg"),
        0.88, 0.85, 0.68, 0.55, 0.92, 0.35, 0.78, Scope.MULTI_SERVICE, 6,
        TestIntensity.HIGH, backend=True,
    ),
    _FamilyRule(
        "security",
        (r"\bauthenticat", r"\bauthoriz", r"\bpermission", r"\bcredential", r"\bsecret\b",
         r"\btoken\b", r"\bxss\b", r"\bcsrf\b", r"\bsql injection\b", r"\bvulnerab"),
        0.70, 0.68, 0.52, 0.32, 0.20, 0.90, 0.58, Scope.MULTI_FILE, 4,
        TestIntensity.HIGH, backend=True,
    ),
    _FamilyRule(
        "database_migration",
        (r"\bmigration\b", r"\bbackfill\b", r"\balembic\b", r"\bdrop column\b"),
        0.62, 0.78, 0.42, 0.88, 0.15, 0.10, 0.52, Scope.MULTI_FILE, 3,
        TestIntensity.HIGH, backend=True, database_flag=True,
    ),
    _FamilyRule(
        "database_schema",
        (r"\bschema\b", r"\badd (a )?column\b", r"\bindex\b", r"\bforeign key\b",
         r"\bconstraint\b", r"\btable\b"),
        0.42, 0.55, 0.35, 0.82, 0.10, 0.10, 0.42, Scope.MULTI_FILE, 3,
        TestIntensity.MEDIUM, backend=True, database_flag=True,
    ),
    _FamilyRule(
        "backend_data_integrity",
        (r"\bdeduplicat", r"\bduplicate\b", r"\bprovenance\b", r"\bre-?key\b",
         r"\bdata integrity\b", r"\bidentity semantics\b", r"\buniqueness\b"),
        0.80, 0.85, 0.70, 0.86, 0.22, 0.12, 0.72, Scope.MULTI_FILE, 5,
        TestIntensity.HIGH, backend=True, database_flag=True,
    ),
    _FamilyRule(
        "root_cause_analysis",
        (r"\broot cause\b", r"\bintermittent\b", r"\bflaky\b", r"\bheisenbug\b",
         r"\bwhy (is|does|did)\b.*\b(fail|break|crash)"),
        0.80, 0.60, 0.62, 0.45, 0.48, 0.20, 0.82, Scope.MULTI_FILE, 6,
        TestIntensity.MEDIUM, backend=True,
    ),
    _FamilyRule(
        "performance",
        (r"\bperformance\b", r"\bslow\b", r"\blatency\b", r"\boptimi[sz]", r"\bn\+1\b",
         r"\bmemory leak\b", r"\bthroughput\b"),
        0.62, 0.50, 0.52, 0.55, 0.35, 0.10, 0.68, Scope.MULTI_FILE, 4,
        TestIntensity.MEDIUM, backend=True,
    ),
    _FamilyRule(
        "refactor_architectural",
        (r"\bextract .* service\b", r"\bsplit .* (service|module|package)\b",
         r"\bmove .* (service|layer|boundary)\b", r"\barchitectural refactor\b"),
        0.75, 0.72, 0.88, 0.40, 0.30, 0.20, 0.85, Scope.MULTI_SERVICE, 12,
        TestIntensity.HIGH, backend=True,
    ),
    _FamilyRule(
        "architecture_design",
        (r"\bdesign (a|an|the)\b", r"\barchitecture\b", r"\btrade[- ]?offs?\b",
         r"\bproposal\b", r"\brfc\b"),
        0.78, 0.30, 0.92, 0.45, 0.35, 0.30, 0.55, Scope.ARCHITECTURE, 0,
        TestIntensity.LOW,
    ),
    _FamilyRule(
        "devops",
        (r"\bci/?cd\b", r"\bgithub actions\b", r"\bpipeline\b", r"\bdockerfile\b",
         r"\bdocker compose\b", r"\bkubernetes\b", r"\bdeploy"),
        0.48, 0.45, 0.35, 0.10, 0.15, 0.30, 0.45, Scope.SINGLE_FILE, 2,
        TestIntensity.LOW, infrastructure=True,
    ),
    _FamilyRule(
        "testing",
        (r"\bwrite (unit |integration )?tests?\b", r"\btest coverage\b",
         r"\badd tests?\b", r"\bpytest\b", r"\bvitest\b"),
        0.32, 0.18, 0.18, 0.15, 0.10, 0.08, 0.48, Scope.SINGLE_FILE, 2,
        TestIntensity.HIGH, backend=True,
    ),
    _FamilyRule(
        "documentation",
        (r"\bdocument", r"\breadme\b", r"\bchangelog\b", r"\bdocstring", r"\brunbook\b"),
        0.18, 0.03, 0.10, 0.02, 0.02, 0.03, 0.32, Scope.SINGLE_FILE, 1,
        TestIntensity.LOW,
    ),
    _FamilyRule(
        "repository_analysis",
        (r"\bexplain how\b", r"\bhow does .* work\b", r"\btrace (the )?flow\b",
         r"\bwalk me through\b", r"\bmap (the|out)\b"),
        0.45, 0.02, 0.45, 0.25, 0.20, 0.15, 0.85, Scope.MULTI_FILE, 0,
        TestIntensity.LOW,
    ),
    _FamilyRule(
        "bug_fix_cross_system",
        (r"\bacross (services|modules|layers)\b", r"\bend[- ]to[- ]end\b.*\bbug\b"),
        0.70, 0.72, 0.58, 0.48, 0.40, 0.20, 0.78, Scope.MULTI_SERVICE, 6,
        TestIntensity.MEDIUM, backend=True,
    ),
    _FamilyRule(
        "debugging",
        (r"\bdebug\b", r"\bfix (the )?(bug|error|crash|exception)\b", r"\btraceback\b",
         r"\bstack ?trace\b", r"\bnot working\b"),
        0.55, 0.55, 0.35, 0.30, 0.25, 0.15, 0.62, Scope.MULTI_FILE, 3,
        TestIntensity.MEDIUM, backend=True,
    ),
    _FamilyRule(
        "api_implementation",
        (r"\bendpoint\b", r"\brest api\b", r"\bgraphql\b", r"\broute handler\b",
         r"\b(get|post|put|patch|delete) /"),
        0.38, 0.32, 0.28, 0.25, 0.12, 0.28, 0.38, Scope.SINGLE_FILE, 2,
        TestIntensity.MEDIUM, backend=True,
    ),
    _FamilyRule(
        "frontend_state",
        (r"\bredux\b", r"\bzustand\b", r"\bstate management\b", r"\bcache invalidat",
         r"\bstale (data|state)\b"),
        0.52, 0.48, 0.42, 0.10, 0.22, 0.08, 0.55, Scope.MULTI_FILE, 3,
        TestIntensity.MEDIUM, frontend=True,
    ),
    _FamilyRule(
        "refactor_local",
        (r"\brefactor\b", r"\brename\b", r"\bextract (a )?(function|method|helper)\b",
         r"\bclean ?up\b", r"\bdead code\b"),
        0.28, 0.30, 0.18, 0.10, 0.08, 0.08, 0.42, Scope.MULTI_FILE, 4,
        TestIntensity.LOW, backend=True,
    ),
    _FamilyRule(
        "ui_behavior",
        (r"\bon ?click\b", r"\bform validation\b", r"\bmodal\b", r"\bdropdown\b",
         r"\bkeyboard shortcut\b", r"\bdisable the button\b"),
        0.35, 0.28, 0.20, 0.05, 0.08, 0.10, 0.35, Scope.SINGLE_FILE, 2,
        TestIntensity.MEDIUM, frontend=True,
    ),
    _FamilyRule(
        "ui_cosmetic",
        (r"\bcolou?r\b", r"\bpadding\b", r"\bmargin\b", r"\bfont\b", r"\bspacing\b",
         r"\bbutton (text|label)\b", r"\bchange the (text|label|wording|copy)\b",
         r"\bcss\b", r"\bicon\b", r"\bstyling\b"),
        0.10, 0.06, 0.03, 0.01, 0.01, 0.02, 0.12, Scope.SINGLE_FILE, 1,
        TestIntensity.LOW, frontend=True,
    ),
    _FamilyRule(
        "bug_fix_local",
        (r"\bfix\b", r"\bbroken\b", r"\bincorrect\b", r"\bwrong (value|result|output)\b"),
        0.38, 0.40, 0.22, 0.20, 0.15, 0.12, 0.48, Scope.SINGLE_FILE, 2,
        TestIntensity.MEDIUM, backend=True,
    ),
)

#: Fallback when nothing matches. Middle of the road on every axis.
_DEFAULT_RULE = _FamilyRule(
    "backend_business_logic", (), 0.45, 0.42, 0.32, 0.28, 0.15, 0.18, 0.48,
    Scope.MULTI_FILE, 3, TestIntensity.MEDIUM, backend=True,
)

_AMBIGUITY_MARKERS = (
    r"\bsomehow\b", r"\bnot sure\b", r"\bfigure out\b", r"\bmaybe\b",
    r"\bsomething is wrong\b", r"\bor whatever\b", r"\bi think\b", r"\betc\.?\b",
)
_CLARITY_MARKERS = (
    r"\bmust\b", r"\bexactly\b", r"\bspecifically\b", r"\bwithout changing\b",
    r"\bpreserve\b", r"\bacceptance criteria\b", r"\bstep \d\b",
)


class HeuristicAnalyzer(TaskAnalyzer):
    """Keyword-driven analyzer. Deterministic, offline, and honest about it.

    This is NOT an attempt to replace semantic understanding. It exists so that
    the backend, the test suite and the end-to-end flow can run without Pi, and
    so the user is not left with a dead application when the bridge is down. It
    reports `provider: "heuristic"` and a deliberately modest confidence so the
    UI can say where the classification came from.
    """

    name = "heuristic"

    async def analyze(
        self, task: str, overrides: AnalyzerOverrides | None = None
    ) -> FingerprintEnvelope:
        # The offline analyzer has no model to choose, so overrides are
        # accepted and ignored rather than rejected: the caller should not have
        # to special-case which analyzer it is talking to.
        del overrides
        if not task or not task.strip():
            raise AnalyzerError("The task is empty.")
        return FingerprintEnvelope(
            fingerprint=self.classify(task),
            analyzer=AnalyzerMetadata(
                provider="heuristic",
                model="keyword-rules-v1",
                confidence=0.55,
                repaired=False,
                duration_ms=0,
            ),
        )

    async def status(self) -> dict:
        return {
            "status": "ok",
            "available": True,
            "analyzer": "heuristic",
            "provider": "heuristic",
            "model": "keyword-rules-v1",
            "note": "Offline keyword analyzer. Start the Pi bridge for semantic analysis.",
        }

    @staticmethod
    def classify(task: str) -> TaskFingerprint:
        text = task.lower()
        rule = next(
            (r for r in _RULES if any(re.search(p, text) for p in r.patterns)),
            _DEFAULT_RULE,
        )

        ambiguity_hits = sum(1 for p in _AMBIGUITY_MARKERS if re.search(p, text))
        clarity_hits = sum(1 for p in _CLARITY_MARKERS if re.search(p, text))
        ambiguity = _clamp(0.18 + 0.12 * ambiguity_hits - 0.05 * clarity_hits)
        clarity = _clamp(0.72 + 0.07 * clarity_hits - 0.14 * ambiguity_hits)

        # Length nudges scope a little, but never complexity. A long mechanical
        # task is still a simple task; that distinction is the whole point.
        words = len(text.split())
        scope = rule.scope
        files = rule.files
        if words > 260 and scope in (Scope.SINGLE_LINE, Scope.SINGLE_FUNCTION, Scope.SINGLE_FILE):
            scope = Scope.MULTI_FILE
            files = max(files, 3)

        return TaskFingerprint(
            task_family=rule.family,
            task_type="heuristic",
            complexity=rule.complexity,
            ambiguity=ambiguity,
            requirements_clarity=clarity,
            regression_risk=rule.regression,
            architecture_reasoning=rule.architecture,
            database_reasoning=rule.database,
            concurrency_risk=rule.concurrency,
            security_risk=rule.security,
            repository_understanding=rule.repo,
            scope=scope,
            estimated_files=files,
            frontend=rule.frontend,
            backend=rule.backend,
            database=rule.database_flag,
            infrastructure=rule.infrastructure,
            tests_required=rule.tests,
            confidence=0.55,
        )


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))
