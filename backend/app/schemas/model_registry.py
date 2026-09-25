"""Typed views over config/models.yaml and config/task_families.yaml.

The YAML files are authoritative. These models exist so that a typo in the
registry fails loudly at startup rather than quietly producing a nonsense
recommendation three weeks later.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .enums import Effort

#: The capability dimensions every model must declare priors for.
CAPABILITY_DIMENSIONS: tuple[str, ...] = (
    "coding",
    "debugging",
    "architecture",
    "database",
    "concurrency",
    "security",
    "repository_understanding",
)


class ModelEntry(BaseModel):
    """One routable model."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    display_name: str
    model_id: str = Field(
        description="The provider-facing identifier, shown in the handoff so the "
        "user selects the right thing in Claude Code or the Codex CLI."
    )
    enabled: bool = True
    supported_efforts: list[Effort]
    relative_model_burn: float = Field(gt=0.0)
    source_pricing: dict[str, float] = Field(default_factory=dict)
    capability_priors: dict[str, float]
    notes: str = ""
    source_urls: list[str] = Field(default_factory=list)
    review_required: bool = False

    @model_validator(mode="after")
    def _check_capabilities(self) -> ModelEntry:
        if self.enabled and (not self.supported_efforts or self.review_required):
            raise ValueError("enabled models require reviewed priors and supported efforts")
        missing = [d for d in CAPABILITY_DIMENSIONS if d not in self.capability_priors]
        if missing:
            raise ValueError(f"missing capability priors: {', '.join(missing)}")
        for dim, value in self.capability_priors.items():
            if dim not in CAPABILITY_DIMENSIONS:
                raise ValueError(f"unknown capability dimension {dim!r}")
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"capability prior {dim}={value} is outside [0, 1]")
        if len(set(self.supported_efforts)) != len(self.supported_efforts):
            raise ValueError("supported_efforts contains duplicates")
        return self


class ProviderEntry(BaseModel):
    """One provider and the models it exposes."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    display_name: str
    burn_weight: float = Field(
        gt=0.0,
        description="Local burn scale for this provider. Providers are routed independently.",
    )
    burn_reference: str = ""
    handoff_hint: str = ""
    models: dict[str, ModelEntry]


class EffortPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    disabled_efforts: list[Effort] = Field(default_factory=list)


class ModelRegistry(BaseModel):
    """The whole of models.yaml."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str
    registry_version: str
    providers: dict[str, ProviderEntry]
    effort_policy: EffortPolicy = Field(default_factory=EffortPolicy)
    family_affinity: dict[str, float] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check(self) -> ModelRegistry:
        if not self.providers:
            raise ValueError("models.yaml declares no providers")
        for key, nudge in self.family_affinity.items():
            if not -0.10 <= nudge <= 0.10:
                raise ValueError(
                    f"family_affinity[{key!r}] = {nudge} is outside the allowed [-0.10, 0.10]"
                )
            if key.count(".") != 2:
                raise ValueError(
                    f"family_affinity key {key!r} must be '<provider>.<model>.<task_family>'"
                )
        return self

    # --- lookups ---------------------------------------------------------------

    def provider(self, name: str) -> ProviderEntry | None:
        return self.providers.get(name)

    def model(self, provider: str, model: str) -> ModelEntry | None:
        entry = self.providers.get(provider)
        return entry.models.get(model) if entry else None

    def display(self, provider: str, model: str) -> tuple[str, str]:
        """Human-facing names, falling back to the raw keys for unknown pairs.

        Unknown pairs are reachable: a receipt may report a model the user has
        since removed from the registry, and history must still render.
        """
        p = self.providers.get(provider)
        if not p:
            return provider, model
        m = p.models.get(model)
        return p.display_name, (m.display_name if m else model)

    def allowed_efforts(self, provider: str, model: str) -> list[Effort]:
        """Efforts this installation will actually route to for a model."""
        entry = self.model(provider, model)
        if entry is None:
            return []
        disabled = set(self.effort_policy.disabled_efforts)
        return [e for e in entry.supported_efforts if e not in disabled]

    def supports(self, provider: str, model: str, effort: Effort) -> bool:
        """Whether a configuration is one the provider actually offers.

        Note this checks `supported_efforts`, not the local disabled list: a
        user may legitimately have executed a task on an effort level this
        installation chooses not to recommend, and that receipt is still valid.
        """
        entry = self.model(provider, model)
        return entry is not None and effort in entry.supported_efforts

    def affinity(self, provider: str, model: str, task_family: str) -> float:
        return self.family_affinity.get(f"{provider}.{model}.{task_family}", 0.0)

    def enabled_configurations(self) -> list[tuple[str, str, Effort]]:
        """Every provider/model/effort triple the router may consider."""
        out: list[tuple[str, str, Effort]] = []
        for provider_name, provider in sorted(self.providers.items()):
            if not provider.enabled:
                continue
            for model_name, model in sorted(provider.models.items()):
                if not model.enabled:
                    continue
                for effort in self.allowed_efforts(provider_name, model_name):
                    out.append((provider_name, model_name, effort))
        return out


class TaskFamilyEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str
    definition: str
    required_reliability_adjustment: float = Field(ge=-0.20, le=0.20, default=0.0)


class TaskFamilyRegistry(BaseModel):
    """The whole of task_families.yaml."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str
    families: dict[str, TaskFamilyEntry]

    def has(self, name: str) -> bool:
        return name in self.families

    def adjustment(self, name: str) -> float:
        entry = self.families.get(name)
        return entry.required_reliability_adjustment if entry else 0.0

    def label(self, name: str) -> str:
        entry = self.families.get(name)
        return entry.label if entry else name.replace("_", " ").title()

    def names(self) -> list[str]:
        return sorted(self.families)
