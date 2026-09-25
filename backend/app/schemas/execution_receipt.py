"""ExecutionReceipt - the contract between the outcome skill and the router.

Like the fingerprint, this is UNTRUSTED INPUT. It is produced by an agent
running outside this application, pasted through a text box, and validated
strictly before it is allowed anywhere near the learning statistics.

Two rules matter more than the rest:

1. Token usage stays `null` unless the provider actually reported it. There is
   no "estimated" usage source. A guessed token count is worse than no token
   count, because it looks like a measurement forever.
2. The agent's own self-assessment is recorded but ranked below the objective
   signals (tests passed, build passed, debug cycles, escalation).
"""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .enums import (
    ActualComplexity,
    Effort,
    OutcomeStatus,
    RecommendationFit,
    UsageSource,
)

RECEIPT_SCHEMA_VERSION = "1.0"

RESULT_OPEN_MARKER = "[LLM-ROUTER-RESULT]"
RESULT_CLOSE_MARKER = "[/LLM-ROUTER-RESULT]"

_RESULT_BLOCK_RE = re.compile(
    re.escape(RESULT_OPEN_MARKER) + r"(?P<body>.*?)" + re.escape(RESULT_CLOSE_MARKER),
    re.DOTALL,
)

#: Task ids are issued by this application: RT- followed by six digits.
TASK_ID_RE = re.compile(r"^RT-\d{6,}$")


class ReceiptExecution(BaseModel):
    """What was actually run. May differ from what was recommended."""

    model_config = ConfigDict(extra="ignore")

    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    effort: Effort


class ReceiptOutcome(BaseModel):
    """Objective outcome signals."""

    model_config = ConfigDict(extra="ignore")

    status: OutcomeStatus
    implementation_complete: bool = False
    first_pass_success: bool = False
    tests_passed: bool | None = Field(
        default=None, description="null when no tests were run at all."
    )
    build_passed: bool | None = Field(
        default=None, description="null when nothing was built."
    )
    known_regression: bool = False


class ReceiptWork(BaseModel):
    """Countable work signals. Null when genuinely unknowable."""

    model_config = ConfigDict(extra="ignore")

    files_read: int | None = Field(default=None, ge=0)
    files_modified: int | None = Field(default=None, ge=0)
    debug_cycles: int = Field(default=0, ge=0)
    major_replans: int = Field(default=0, ge=0)
    user_corrections: int = Field(default=0, ge=0)


class ReceiptEscalation(BaseModel):
    """Whether the work had to move to a stronger configuration mid-task."""

    model_config = ConfigDict(extra="ignore")

    occurred: bool = False
    from_provider: str | None = None
    from_model: str | None = None
    from_effort: Effort | None = None
    to_provider: str | None = None
    to_model: str | None = None
    to_effort: Effort | None = None
    reason: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _consistent(self) -> ReceiptEscalation:
        if not self.occurred:
            return self
        if not (self.to_provider and self.to_model and self.to_effort):
            raise ValueError(
                "escalation.occurred is true but the target configuration is incomplete"
            )
        return self


class ReceiptUsage(BaseModel):
    """Token usage. Measured or absent, never estimated."""

    model_config = ConfigDict(extra="ignore")

    input_tokens: int | None = Field(default=None, ge=0)
    cached_input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    reasoning_tokens: int | None = Field(default=None, ge=0)
    provider_reported_cost: float | None = Field(default=None, ge=0.0)
    source: UsageSource = UsageSource.UNAVAILABLE

    @model_validator(mode="after")
    def _no_invented_usage(self) -> ReceiptUsage:
        numbers = (
            self.input_tokens,
            self.cached_input_tokens,
            self.output_tokens,
            self.reasoning_tokens,
            self.provider_reported_cost,
        )
        has_numbers = any(n is not None for n in numbers)
        if self.source is UsageSource.UNAVAILABLE and has_numbers:
            raise ValueError(
                "usage.source is 'unavailable' but usage numbers were supplied; "
                "numbers must be null unless the provider reported them"
            )
        return self


class ReceiptAgentAssessment(BaseModel):
    """The executing agent's subjective read. Lower trust by design."""

    model_config = ConfigDict(extra="ignore")

    actual_complexity: ActualComplexity | None = None
    recommendation_fit: RecommendationFit = RecommendationFit.INCONCLUSIVE
    notes: str = Field(default="", max_length=4000)


class ExecutionReceipt(BaseModel):
    """A full outcome report for one executed task."""

    model_config = ConfigDict(extra="ignore")

    receipt_version: str = Field(default=RECEIPT_SCHEMA_VERSION)
    task_id: str

    execution: ReceiptExecution
    outcome: ReceiptOutcome
    work: ReceiptWork = Field(default_factory=ReceiptWork)
    escalation: ReceiptEscalation = Field(default_factory=ReceiptEscalation)
    usage: ReceiptUsage = Field(default_factory=ReceiptUsage)
    agent_assessment: ReceiptAgentAssessment = Field(
        default_factory=ReceiptAgentAssessment
    )

    @model_validator(mode="after")
    def _validate(self) -> ExecutionReceipt:
        if self.receipt_version != RECEIPT_SCHEMA_VERSION:
            raise ValueError(
                f"unsupported receipt_version {self.receipt_version!r}; "
                f"this build understands {RECEIPT_SCHEMA_VERSION!r}"
            )
        if not TASK_ID_RE.match(self.task_id):
            raise ValueError(
                f"task_id {self.task_id!r} is not a router task id (expected RT-000000 form)"
            )
        if self.outcome.status is OutcomeStatus.FAILED and self.outcome.first_pass_success:
            raise ValueError("a failed task cannot also be a first-pass success")
        if self.outcome.first_pass_success and self.work.debug_cycles > 0:
            raise ValueError(
                "first_pass_success is true but debug_cycles is greater than zero"
            )
        if self.outcome.first_pass_success and self.escalation.occurred:
            raise ValueError("first_pass_success is true but an escalation occurred")
        return self


def extract_receipt_block(raw: str) -> str:
    """Pull the JSON body out of a pasted `[LLM-ROUTER-RESULT]` block.

    Accepts a bare JSON object too, so a user who copied only the JSON is not
    punished for it. Raises ValueError with a message meant for a human.
    """
    if not raw or not raw.strip():
        raise ValueError("Nothing was pasted.")

    match = _RESULT_BLOCK_RE.search(raw)
    body = match.group("body") if match else raw

    body = body.strip()
    # Tolerate a fenced code block inside the markers.
    if body.startswith("```"):
        body = re.sub(r"^```[a-zA-Z]*\n", "", body)
        body = re.sub(r"\n?```\s*$", "", body).strip()

    if not body:
        raise ValueError("The result block was empty.")
    if not body.startswith("{"):
        start = body.find("{")
        end = body.rfind("}")
        if start == -1 or end == -1 or end < start:
            raise ValueError(
                "No JSON object found. Paste the whole "
                f"{RESULT_OPEN_MARKER} ... {RESULT_CLOSE_MARKER} block."
            )
        body = body[start : end + 1]
    return body


def parse_receipt(raw: str) -> tuple[ExecutionReceipt, dict[str, Any]]:
    """Extract, decode and validate a pasted receipt.

    Returns the validated receipt and the raw decoded dict, which is stored
    verbatim so that nothing the agent reported is silently discarded.
    """
    body = extract_receipt_block(raw)
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise ValueError(f"The result block is not valid JSON: {exc.msg} (line {exc.lineno})") from exc
    if not isinstance(payload, dict):
        raise ValueError("The result block must be a JSON object.")
    return ExecutionReceipt.model_validate(payload), payload
