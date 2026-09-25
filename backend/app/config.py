"""Application settings and configuration loading.

Configuration lives in YAML under `config/` so that tuning the router never
requires touching Python. The loaded objects are cached, because the router is
called on every request and reparsing YAML each time would be silly; a reload
endpoint exists for when a file is edited by hand.
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

from .schemas.model_registry import ModelRegistry, TaskFamilyRegistry
from .schemas.routing_policy import RoutingPolicy

#: backend/app/config.py -> backend/
_BACKEND_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    """Runtime settings, all overridable by environment variable."""

    database_url: str
    config_dir: Path
    pi_bridge_url: str
    pi_timeout_seconds: float
    cors_origins: tuple[str, ...]
    app_version: str

    @staticmethod
    def from_env() -> Settings:
        config_dir = Path(os.getenv("CONFIG_DIR", str(_BACKEND_ROOT / "config"))).resolve()

        default_db = (_BACKEND_ROOT.parent / "data" / "router.db").as_posix()
        database_url = os.getenv("DATABASE_URL", f"sqlite:///{default_db}")

        origins = os.getenv(
            "CORS_ORIGINS",
            "http://localhost:3000,http://127.0.0.1:3000,http://localhost:5173,http://127.0.0.1:5173",
        )
        return Settings(
            database_url=database_url,
            config_dir=config_dir,
            pi_bridge_url=os.getenv("PI_BRIDGE_URL", "http://127.0.0.1:31415").rstrip("/"),
            pi_timeout_seconds=float(os.getenv("PI_TIMEOUT_SECONDS", "180")),
            cors_origins=tuple(o.strip() for o in origins.split(",") if o.strip()),
            app_version=os.getenv("APP_VERSION", "1.0.0"),
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings.from_env()


class ConfigError(RuntimeError):
    """Raised when a YAML config file is missing or invalid.

    Deliberately fatal at startup: a router running on a half-parsed policy is
    worse than a router that refuses to start.
    """


def _read_yaml(path: Path) -> dict:
    if not path.exists():
        raise ConfigError(f"configuration file not found: {path}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path.name} is not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{path.name} must contain a YAML mapping at the top level")
    return data


class ConfigBundle:
    """Loaded, validated configuration. Reloadable at runtime."""

    def __init__(self, config_dir: Path) -> None:
        self._dir = config_dir
        # The application runs one backend worker; reload and registry updates
        # share this lock so a reload cannot observe an uncommitted replacement.
        self.reload_lock = threading.RLock()
        self._registry: ModelRegistry | None = None
        self._policy: RoutingPolicy | None = None
        self._families: TaskFamilyRegistry | None = None

    @property
    def directory(self) -> Path:
        return self._dir

    def load(self) -> None:
        """Parse and validate every config file. Raises ConfigError on any problem."""
        with self.reload_lock:
            self._load()

    def _load(self) -> None:
        registry_raw = _read_yaml(self._dir / "models.yaml")
        policy_raw = _read_yaml(self._dir / "routing.yaml")
        families_raw = _read_yaml(self._dir / "task_families.yaml")

        try:
            registry = ModelRegistry.model_validate(registry_raw)
        except Exception as exc:
            raise ConfigError(f"models.yaml is invalid: {exc}") from exc
        try:
            policy = RoutingPolicy.model_validate(policy_raw)
        except Exception as exc:
            raise ConfigError(f"routing.yaml is invalid: {exc}") from exc
        try:
            families = TaskFamilyRegistry.model_validate(families_raw)
        except Exception as exc:
            raise ConfigError(f"task_families.yaml is invalid: {exc}") from exc

        _cross_validate(registry, policy, families)

        with self.reload_lock:
            self._registry = registry
            self._policy = policy
            self._families = families

    def validate_registry(self, registry: ModelRegistry) -> None:
        policy = RoutingPolicy.model_validate(_read_yaml(self._dir / "routing.yaml"))
        families = TaskFamilyRegistry.model_validate(_read_yaml(self._dir / "task_families.yaml"))
        _cross_validate(registry, policy, families)

    def _ensure(self) -> None:
        if self._registry is None:
            self.load()

    @property
    def registry(self) -> ModelRegistry:
        self._ensure()
        assert self._registry is not None
        return self._registry

    @property
    def policy(self) -> RoutingPolicy:
        self._ensure()
        assert self._policy is not None
        return self._policy

    @property
    def families(self) -> TaskFamilyRegistry:
        self._ensure()
        assert self._families is not None
        return self._families


def _cross_validate(
    registry: ModelRegistry, policy: RoutingPolicy, families: TaskFamilyRegistry
) -> None:
    """Catch mismatches that are only visible when the files are read together."""
    # Every scope the fingerprint schema allows needs a difficulty score.
    from .schemas.enums import Scope

    for scope in Scope:
        if scope.value not in policy.scope_scores:
            raise ConfigError(f"routing.yaml scope_scores is missing {scope.value!r}")

    # Difficulty weight keys must be things we can actually read off a fingerprint.
    known_drivers = {
        "complexity",
        "scope",
        "regression_risk",
        "ambiguity",
        "clarity_gap",
        "architecture_reasoning",
        "database_reasoning",
        "concurrency_risk",
        "security_risk",
        "repository_understanding",
    }
    unknown = set(policy.difficulty_weights) - known_drivers
    if unknown:
        raise ConfigError(
            f"routing.yaml difficulty_weights references unknown dimensions: {sorted(unknown)}"
        )
    unknown = set(policy.required_reliability.risk_coefficients) - known_drivers
    if unknown:
        raise ConfigError(
            "routing.yaml required_reliability.risk_coefficients references unknown "
            f"dimensions: {sorted(unknown)}"
        )

    # Capability weights must name dimensions the registry actually declares.
    from .schemas.model_registry import CAPABILITY_DIMENSIONS

    unknown = set(policy.capability_weights) - set(CAPABILITY_DIMENSIONS)
    if unknown:
        raise ConfigError(
            f"routing.yaml capability_weights references unknown dimensions: {sorted(unknown)}"
        )
    missing = set(CAPABILITY_DIMENSIONS) - set(policy.capability_weights)
    if missing:
        raise ConfigError(
            f"routing.yaml capability_weights is missing dimensions: {sorted(missing)}"
        )
    allowed_driver_names = known_drivers | {"debugging_pressure"}
    for dim, weight in policy.capability_weights.items():
        if weight.driver is not None and weight.driver not in allowed_driver_names:
            raise ConfigError(
                f"routing.yaml capability_weights[{dim}].driver={weight.driver!r} is not a "
                "readable fingerprint dimension"
            )

    # Affinity keys must point at real families and real models.
    for key in registry.family_affinity:
        provider, model, family = key.split(".")
        if registry.model(provider, model) is None:
            raise ConfigError(f"family_affinity key {key!r} names an unknown model")
        if not families.has(family):
            raise ConfigError(f"family_affinity key {key!r} names an unknown task family")

    # At least one configuration must be routable, or the router can do nothing.
    if not registry.enabled_configurations():
        raise ConfigError(
            "no routable configurations: every provider/model is disabled, or "
            "effort_policy.disabled_efforts excludes every supported effort"
        )


@lru_cache(maxsize=1)
def get_config() -> ConfigBundle:
    bundle = ConfigBundle(get_settings().config_dir)
    bundle.load()
    return bundle
