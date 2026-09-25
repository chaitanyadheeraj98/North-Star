"""Model registry and task family endpoints (read-only).

The registry is edited in models.yaml, not through the API. Letting the UI
write routing priors would put two sources of truth in the system, and the
YAML comments explaining which numbers are sourced and which are assumptions
would be the first casualty.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from ..config import ConfigBundle, ConfigError, get_config
from .deps import ConfigDep

router = APIRouter(prefix="/api/models", tags=["models"])


@router.get("")
def list_models(config: ConfigBundle = ConfigDep) -> dict[str, Any]:
    registry = config.registry
    policy = config.policy

    providers: list[dict[str, Any]] = []
    for provider_name, provider in sorted(registry.providers.items()):
        models: list[dict[str, Any]] = []
        for model_name, model in sorted(provider.models.items()):
            routable = registry.allowed_efforts(provider_name, model_name)
            models.append(
                {
                    "key": model_name,
                    "display_name": model.display_name,
                    "model_id": model.model_id,
                    "enabled": model.enabled,
                    "supported_efforts": [e.value for e in model.supported_efforts],
                    "routable_efforts": [e.value for e in routable],
                    "relative_model_burn": model.relative_model_burn,
                    "source_pricing": model.source_pricing,
                    "capability_priors": model.capability_priors,
                    "notes": model.notes,
                    "base_burn_by_effort": {
                        effort.value: round(
                            provider.burn_weight
                            * model.relative_model_burn
                            * policy.effort_burn_prior[effort],
                            4,
                        )
                        for effort in routable
                    },
                }
            )
        providers.append(
            {
                "key": provider_name,
                "display_name": provider.display_name,
                "enabled": provider.enabled,
                "burn_weight": provider.burn_weight,
                "burn_reference": provider.burn_reference,
                "handoff_hint": provider.handoff_hint,
                "models": models,
            }
        )

    return {
        "registry_version": registry.registry_version,
        "schema_version": registry.schema_version,
        "providers": providers,
        "disabled_efforts": [e.value for e in registry.effort_policy.disabled_efforts],
        "effort_burn_prior": {k.value: v for k, v in policy.effort_burn_prior.items()},
        "effort_capability_gain": {
            k.value: v for k, v in policy.effort_capability_gain.items()
        },
        "family_affinity": registry.family_affinity,
        "provenance_note": (
            "Model names, supported effort levels and published pricing/credit rates come "
            "from the provider reference documents. Capability priors, burn weights and "
            "effort priors are this application's own routing assumptions, not provider "
            "guarantees. Neither provider publishes a fixed token multiplier per effort "
            "level; both use adaptive reasoning."
        ),
        "configurations": len(registry.enabled_configurations()),
    }


@router.get("/task-families")
def list_task_families(config: ConfigBundle = ConfigDep) -> dict[str, Any]:
    return {
        "schema_version": config.families.schema_version,
        "families": [
            {
                "key": key,
                "label": entry.label,
                "definition": entry.definition.strip(),
                "required_reliability_adjustment": entry.required_reliability_adjustment,
            }
            for key, entry in sorted(config.families.families.items())
        ],
    }


@router.post("/reload")
def reload_config() -> dict[str, str]:
    """Re-read the YAML files after a hand edit, without restarting the container."""
    bundle = get_config()
    try:
        bundle.load()
    except ConfigError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "status": "reloaded",
        "registry_version": bundle.registry.registry_version,
        "router_version": bundle.policy.router_version,
    }
