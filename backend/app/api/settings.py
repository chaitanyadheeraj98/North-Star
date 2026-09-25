"""Settings: what the app is running with, and the few knobs worth exposing.

Routing policy is NOT editable here. It lives in routing.yaml with the comments
that say which numbers are sourced facts and which are our assumptions; a UI
form would strip that context and quietly create a second source of truth.
What is editable here is genuine user preference: which analyzer to use, and
whether to fall back when it is unavailable.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from sqlalchemy.orm import Session

from ..config import ConfigBundle, Settings
from ..db.models import AppSetting
from ..schemas.api import SettingsResponse, UpdateSettingsRequest
from ..services.pi_service import PiBridgeAnalyzer
from .deps import ConfigDep, PiDep, SessionDep, SettingsDep

router = APIRouter(prefix="/api/settings", tags=["settings"])

_PREFERENCES_KEY = "preferences"
#: `analyzer_provider` / `analyzer_model` / `analyzer_effort` pin which model
#: classifies tasks. They are sent to the bridge on every analysis; None means
#: "use the bridge's own boot configuration from .env". Credentials never
#: appear here - authentication lives entirely in Pi's own store.
_DEFAULT_PREFERENCES: dict[str, Any] = {
    "analyzer_source": "pi",
    "allow_analyzer_fallback": False,
    "analyzer_provider": None,
    "analyzer_model": None,
    "analyzer_effort": None,
}


def load_preferences(session: Session) -> dict[str, Any]:
    row = session.get(AppSetting, _PREFERENCES_KEY)
    merged = dict(_DEFAULT_PREFERENCES)
    if row and isinstance(row.value, dict):
        merged.update(row.value)
    return merged


@router.get("", response_model=SettingsResponse)
async def get_settings_view(
    session: Session = SessionDep,
    config: ConfigBundle = ConfigDep,
    settings: Settings = SettingsDep,
    pi: PiBridgeAnalyzer = PiDep,
) -> SettingsResponse:
    registry = config.registry
    policy = config.policy

    providers = [
        {
            "key": name,
            "display_name": provider.display_name,
            "enabled": provider.enabled,
            "burn_weight": provider.burn_weight,
            "burn_reference": provider.burn_reference,
            "models": [
                {
                    "key": model_key,
                    "display_name": model.display_name,
                    "model_id": model.model_id,
                    "enabled": model.enabled,
                    "review_required": model.review_required,
                    "supported_efforts": [e.value for e in model.supported_efforts],
                    "routable_efforts": [
                        e.value for e in registry.allowed_efforts(name, model_key)
                    ],
                    "relative_model_burn": model.relative_model_burn,
                }
                for model_key, model in sorted(provider.models.items())
            ],
        }
        for name, provider in sorted(registry.providers.items())
    ]

    return SettingsResponse(
        app_version=settings.app_version,
        router_version=policy.router_version,
        registry_version=registry.registry_version,
        config_dir=str(config.directory),
        database_url=_redact(settings.database_url),
        pi_bridge_url=settings.pi_bridge_url,
        pi=await pi.status(),
        providers=providers,
        task_families=[
            {
                "key": key,
                "label": entry.label,
                "required_reliability_adjustment": entry.required_reliability_adjustment,
            }
            for key, entry in sorted(config.families.families.items())
        ],
        policy={
            "required_reliability": policy.required_reliability.model_dump(),
            "effort_burn_prior": {
                k.value: v for k, v in policy.effort_burn_prior.items()
            },
            "effort_capability_gain": {
                k.value: v for k, v in policy.effort_capability_gain.items()
            },
            "difficulty_weights": policy.difficulty_weights,
            "reliability_model": policy.reliability_model.model_dump(),
            "effective_burn": policy.effective_burn.model_dump(),
            "learning": policy.learning.model_dump(),
            "recency_weights": [b.model_dump() for b in policy.recency_weights],
            "disabled_efforts": [
                e.value for e in registry.effort_policy.disabled_efforts
            ],
            "editable_in": "backend/config/routing.yaml and backend/config/models.yaml",
        },
        preferences=load_preferences(session),
    )


@router.put("", response_model=dict)
def update_settings(
    body: UpdateSettingsRequest, session: Session = SessionDep
) -> dict[str, Any]:
    preferences = load_preferences(session)
    for key, value in body.model_dump(exclude_unset=True).items():
        # The three analyzer pins are explicitly nullable: clearing one means
        # "fall back to the bridge's boot configuration", which is a real choice
        # and must not be mistaken for "field omitted".
        if value is not None or key in (
            "analyzer_provider",
            "analyzer_model",
            "analyzer_effort",
        ):
            preferences[key] = value

    row = session.get(AppSetting, _PREFERENCES_KEY)
    if row is None:
        row = AppSetting(key=_PREFERENCES_KEY, value=preferences)
        session.add(row)
    else:
        row.value = preferences
    session.commit()
    return {"status": "saved", "preferences": preferences}


def _redact(database_url: str) -> str:
    """Strip credentials from a URL before showing it.

    SQLite has none today, but the settings screen is exactly the place where a
    future Postgres URL would leak a password into a screenshot.
    """
    if "@" not in database_url:
        return database_url
    scheme, _, rest = database_url.partition("://")
    _, _, host = rest.rpartition("@")
    return f"{scheme}://***@{host}"
