"""Request and response bodies for the HTTP API.

Kept apart from the domain contracts so that changing a wire format never
forces a change to the routing engine's inputs, and vice versa.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

import hashlib
import re

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..services.preparation import Preparation
from .enums import InputType
from .routing_decision import RoutingRecommendations
from .task_fingerprint import TaskFingerprint


#: A pasted task or prompt. Longer input is a spec or plan, which is condensed
#: for analysis instead (services/input_digest.py).
TASK_MAX_CHARS = 200_000
#: A spec or plan file. Generous: it is stored, condensed, and never sent whole.
DOCUMENT_MAX_CHARS = 5_000_000

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_UTF8_BOM = b"\xef\xbb\xbf"


def _content_hashes(text: str) -> set[str]:
    """Hashes a file with this text can have.

    A browser's TextDecoder drops a leading byte-order mark, so the text that
    arrives here is the file without it, while the hash is of the bytes on
    disk. Both forms are therefore legitimate. Line endings are not touched by
    decoding, so they need no such allowance.
    """
    data = text.encode("utf-8")
    return {hashlib.sha256(data).hexdigest(), hashlib.sha256(_UTF8_BOM + data).hexdigest()}


class CreateTaskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task: str = Field(min_length=1, max_length=DOCUMENT_MAX_CHARS)
    #: What the text is. Tells the analyzer how to judge it and decides which
    #: preparation advice makes sense (a plan is never told to write a plan).
    input_type: InputType = InputType.TASK
    #: For a spec or plan file: where it lives in the repository. The handoff
    #: then tells the agent to read that file instead of carrying the text.
    source_path: str | None = Field(default=None, max_length=500)
    #: SHA-256 of the file's bytes, computed by the browser when it was picked.
    source_sha256: str | None = None

    analyze: bool = Field(
        default=True,
        description="Run the analyzer and produce a recommendation in the same call. "
        "This is what the Router page does; the separate endpoints exist for scripting.",
    )
    analyzer: Literal["pi", "heuristic"] = "pi"
    #: Fall back to the offline analyzer instead of failing when Pi is down.
    #: Off by default: silently degrading the analysis would be dishonest.
    allow_fallback: bool = False

    @field_validator("task")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        """Reject whitespace-only tasks here rather than deeper in the service.

        Otherwise an empty box and a box containing three spaces would fail with
        two different status codes for the same user-visible mistake.
        """
        if not value.strip():
            raise ValueError("The task is empty.")
        return value

    @field_validator("source_path")
    @classmethod
    def _safe_path(cls, value: str | None) -> str | None:
        """A path, and nothing that could break out of the handoff it is written into.

        It lands on its own `source_path=` line in the [LLM-ROUTER] block and in
        backticks in the instructions, so a newline or backtick could forge
        metadata or instructions.
        """
        if value is None:
            return None
        value = value.strip()
        if not value:
            return None
        if "`" in value or any(ord(ch) < 32 for ch in value):
            raise ValueError("The file path cannot contain backticks, tabs or line breaks.")
        return value

    @field_validator("source_sha256")
    @classmethod
    def _hex_digest(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip().lower()
        if not _SHA256.match(value):
            raise ValueError("source_sha256 must be 64 hexadecimal characters.")
        return value

    @model_validator(mode="after")
    def _fits_input_type(self) -> CreateTaskRequest:
        if self.input_type == InputType.TASK:
            if len(self.task) > TASK_MAX_CHARS:
                raise ValueError(
                    f"A task can be at most {TASK_MAX_CHARS:,} characters (this one is "
                    f"{len(self.task):,}). For a long spec or implementation plan, choose "
                    "that input type: it has no practical limit and is condensed for analysis."
                )
            if self.source_path or self.source_sha256:
                raise ValueError("A file path only applies to a spec or implementation plan.")
        if self.source_sha256 and not self.source_path:
            raise ValueError("source_sha256 needs the source_path it describes.")
        if self.source_path:
            # The handoff promises the agent a changed-file check; it cannot
            # make that promise without the hash, or with a hash of other bytes.
            if not self.source_sha256:
                raise ValueError(
                    "A spec or plan file needs source_sha256, the SHA-256 of its bytes, so "
                    "the executing model can tell whether the file changed after analysis."
                )
            if self.source_sha256 not in _content_hashes(self.task):
                raise ValueError(
                    "source_sha256 does not match the text sent. It must be the SHA-256 of "
                    "the file's UTF-8 bytes, the same file whose text is being analyzed."
                )
        return self


class TaskInput(BaseModel):
    """What the analyzed text was, as shown on the Router page."""

    input_type: InputType = InputType.TASK
    source_path: str | None = None
    source_sha256: str | None = None
    content_chars: int = 0
    #: True when the analyzer classified a condensed digest of the text.
    condensed: bool = False


class AnalyzeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    analyzer: Literal["pi", "heuristic"] = "pi"
    allow_fallback: bool = False


class AnalyzerInfo(BaseModel):
    provider: str | None = None
    model: str | None = None
    confidence: float | None = None
    repaired: bool = False
    duration_ms: int | None = None
    source: str = "pi"
    fallback_used: bool = False
    note: str | None = None


class TaskSummary(BaseModel):
    public_task_id: str
    title: str
    status: str
    created_at: datetime
    task_family: str | None = None


class TaskResponse(BaseModel):
    public_task_id: str
    title: str
    original_task: str
    status: str
    created_at: datetime
    input: TaskInput = Field(default_factory=TaskInput)
    #: What "Copy task only" copies: the text, or for a spec or plan file the
    #: instructions pointing at it (the file may be too long to paste).
    task_only: str = ""

    fingerprint: TaskFingerprint | None = None
    analyzer: AnalyzerInfo | None = None
    recommendation: RoutingRecommendations | None = None
    #: Execute directly, plan first, or spec and plan first. See services/preparation.py.
    preparation: Preparation | None = None
    handoffs: dict[str, str] = Field(default_factory=dict)
    execution: dict[str, Any] | None = None


class HandoffResponse(BaseModel):
    public_task_id: str
    handoff: str
    task_only: str
    provider: str
    model: str
    model_id: str
    effort: str


class ImportResultRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    receipt: str = Field(min_length=1, max_length=200_000)


class ImportResultResponse(BaseModel):
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
    learning_summary: str
    warnings: list[str] = Field(default_factory=list)
    statistics: dict[str, Any] = Field(default_factory=dict)


class HistoryRow(BaseModel):
    public_task_id: str
    title: str
    task_family: str | None
    created_at: datetime
    status: str

    recommended_provider: str | None = None
    recommended_model: str | None = None
    recommended_effort: str | None = None
    recommended_display: str | None = None
    recommendations: dict[str, str] = Field(default_factory=dict)

    actual_provider: str | None = None
    actual_model: str | None = None
    actual_effort: str | None = None
    actual_display: str | None = None

    outcome: str | None = None
    recommendation_followed: bool | None = None
    first_pass_success: bool | None = None
    escalated: bool | None = None
    debug_cycles: int | None = None
    predicted_burn: float | None = None
    estimated_effective_burn: float | None = None
    usage_source: str | None = None
    executed_at: datetime | None = None


class HistoryResponse(BaseModel):
    rows: list[HistoryRow]
    total: int
    limit: int
    offset: int


class SettingsResponse(BaseModel):
    app_version: str
    router_version: str
    registry_version: str
    config_dir: str
    database_url: str
    pi_bridge_url: str

    pi: dict[str, Any]
    providers: list[dict[str, Any]]
    task_families: list[dict[str, Any]]
    policy: dict[str, Any]
    preferences: dict[str, Any]


class UpdateSettingsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    analyzer_source: Literal["pi", "heuristic"] | None = None
    allow_analyzer_fallback: bool | None = None
    analyzer_provider: str | None = None
    analyzer_model: str | None = None
    #: Pi thinking level for the CLASSIFIER: minimal | low | medium | high | xhigh.
    #: Separate vocabulary from the effort the router recommends for the model
    #: that does the actual work.
    analyzer_effort: Literal["minimal", "low", "medium", "high", "xhigh"] | None = None
