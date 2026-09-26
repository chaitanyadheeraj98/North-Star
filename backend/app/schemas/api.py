"""Request and response bodies for the HTTP API.

Kept apart from the domain contracts so that changing a wire format never
forces a change to the routing engine's inputs, and vice versa.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .routing_decision import RoutingRecommendations
from .task_fingerprint import TaskFingerprint


class CreateTaskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task: str = Field(min_length=1, max_length=200_000)

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

    fingerprint: TaskFingerprint | None = None
    analyzer: AnalyzerInfo | None = None
    recommendation: RoutingRecommendations | None = None
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
