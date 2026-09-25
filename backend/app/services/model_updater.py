"""Official-source registry refresh. No task text, credentials, Pi, or LLM calls."""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

import httpx
import yaml
from pydantic import BaseModel, ConfigDict, Field

from ..config import ConfigBundle
from ..schemas.enums import EFFORT_ORDER, Effort
from ..schemas.model_registry import CAPABILITY_DIMENSIONS, ModelEntry, ModelRegistry

OPENAI_MODELS = "https://raw.githubusercontent.com/openai/codex/main/codex-rs/models-manager/models.json"
CLAUDE_MODELS = "https://platform.claude.com/docs/en/models/overview.md"
CLAUDE_EFFORT = "https://platform.claude.com/docs/en/build-with-claude/effort.md"
SOURCES = (OPENAI_MODELS, CLAUDE_MODELS, CLAUDE_EFFORT)
MAX_SOURCE_BYTES = 4_000_000


class ModelUpdateError(RuntimeError):
    pass


class OfficialModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    model_id: str = Field(pattern=r"^[a-z0-9][a-z0-9.-]{1,127}$")
    display_name: str = Field(min_length=1, max_length=160)
    aliases: list[str] = Field(default_factory=list)
    supported_efforts: list[Effort] | None = None
    source_pricing: dict[str, float] = Field(default_factory=dict)
    source_urls: list[str]


class ModelUpdateResult(BaseModel):
    status: str
    registry_version: str
    added: list[str] = Field(default_factory=list)
    changed: list[str] = Field(default_factory=list)
    unconfirmed: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=lambda: list(SOURCES))
    backup: str | None = None


def _fetch(client: httpx.Client, url: str) -> str:
    # Only constant official URLs are accepted; redirects cannot widen access.
    if url not in SOURCES:
        raise ModelUpdateError("The updater only accepts its official source URLs.")
    with client.stream("GET", url, follow_redirects=False) as response:
        response.raise_for_status()
        chunks = bytearray()
        for chunk in response.iter_bytes():
            chunks.extend(chunk)
            if len(chunks) > MAX_SOURCE_BYTES:
                raise ModelUpdateError("Official source exceeded the size limit.")
        return chunks.decode("utf-8")


def _rows(text: str) -> dict[str, list[str]]:
    rows = {}
    for line in text.splitlines():
        if line.startswith("|"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            label = re.sub(r"\[([^]]+)\]\([^)]+\)", r"\1", cells[0]).strip("` ")
            if label not in rows:
                rows[label] = cells[1:]
    return rows


def parse_openai(text: str) -> list[OfficialModel]:
    models = []
    for item in json.loads(text)["models"]:
        if item.get("visibility") != "list":
            continue
        models.append(OfficialModel(
            model_id=item["slug"], display_name=item["display_name"],
            supported_efforts=[Effort(level["effort"]) for level in item["supported_reasoning_levels"]],
            source_urls=[OPENAI_MODELS],
        ))
    return models


def parse_claude(overview: str, effort: str) -> list[OfficialModel]:
    rows = _rows(overview)
    ids = rows["Claude API ID"]
    names, prices = rows["Feature"], rows["Pricing"]
    aliases = rows.get("Claude API alias", ids)
    defaults = rows["Default effort"]
    if not ids or any(len(cells) != len(ids) for cells in (names, prices, aliases, defaults)):
        raise ModelUpdateError("Anthropic model table is incomplete or changed format.")
    frontmatter = yaml.safe_load(effort.split("---", 2)[1])
    supported = frontmatter["featureMetadata"]["supportedModels"]
    levels = _rows(effort)
    if not all(level in levels for level in ("low", "medium", "high", "xhigh", "max")):
        raise ModelUpdateError("Anthropic effort table changed format.")
    models = []
    for model_id, name, price, alias, default in zip(ids, names, prices, aliases, defaults):
        model_id, alias = model_id.strip("`"), alias.strip("`")
        pricing = re.fullmatch(r"\$(\d+(?:\.\d+)?) / input MTok, \$(\d+(?:\.\d+)?) / output MTok", price)
        if not pricing:
            raise ModelUpdateError("Anthropic pricing table changed format.")
        efforts = None
        if model_id in supported or alias in supported:
            efforts = [Effort.LOW, Effort.MEDIUM, Effort.HIGH]
            for level in (Effort.XHIGH, Effort.MAX):
                # Parse explicit model lists only, never capability claims.
                if re.search(re.escape(name) + r"(?=,| and |\.?\s|\.?$)", levels[level.value][0]):
                    efforts.append(level)
        elif default == "Not supported":
            efforts = [Effort.NONE]
        models.append(OfficialModel(
            model_id=model_id, display_name=name, aliases=[alias],
            supported_efforts=efforts,
            source_pricing={"input_per_1m_usd": float(pricing[1]), "output_per_1m_usd": float(pricing[2])},
            source_urls=[CLAUDE_MODELS, CLAUDE_EFFORT],
        ))
    return models


def fetch_official_models() -> dict[str, list[OfficialModel]]:
    with httpx.Client(timeout=15, headers={"User-Agent": "North-Star-Model-Updater/1.0"}) as client:
        return {
            "codex": parse_openai(_fetch(client, OPENAI_MODELS)),
            "claude": parse_claude(_fetch(client, CLAUDE_MODELS), _fetch(client, CLAUDE_EFFORT)),
        }


def build_candidate(current: ModelRegistry, sources: dict[str, list[OfficialModel]]) -> tuple[ModelRegistry, ModelUpdateResult]:
    candidate = current.model_copy(deep=True)
    result = ModelUpdateResult(status="unchanged", registry_version=current.registry_version)
    if set(sources) != {"codex", "claude"}:
        raise ModelUpdateError("Both official provider catalogs are required.")
    for provider, models in sources.items():
        if not models or len({m.model_id for m in models}) != len(models):
            raise ModelUpdateError(f"{provider} catalog is empty or contains duplicate IDs.")
        entries = candidate.providers[provider].models
        matched = set()
        for official in models:
            if not official.model_id.startswith("gpt-" if provider == "codex" else "claude-"):
                raise ModelUpdateError(f"Unexpected model ID for {provider}.")
            if any(url not in SOURCES for url in official.source_urls):
                raise ModelUpdateError("Unofficial source in candidate metadata.")
            keys = [key for key, entry in entries.items() if entry.model_id in [official.model_id, *official.aliases]]
            if len(keys) > 1:
                raise ModelUpdateError(f"Ambiguous registry identity for {official.model_id}.")
            if keys:
                key = keys[0]
                if key in matched:
                    raise ModelUpdateError(f"Conflicting source entries for {key}.")
                entry = entries[key]
                before = entry.model_dump(mode="json")
            else:
                key = official.model_id.replace("-", "_").replace(".", "_")
                if key in entries:
                    raise ModelUpdateError(f"Registry key collision for {key}.")
                entry = ModelEntry(
                    display_name=official.display_name, model_id=official.model_id,
                    enabled=False, review_required=True, supported_efforts=[],
                    capability_priors={dimension: 0.0 for dimension in CAPABILITY_DIMENSIONS},
                    relative_model_burn=max(e.relative_model_burn for e in entries.values()),
                    notes="Discovered from official sources. Disabled pending review of capability and burn priors; zero capability values are placeholders, not measurements.",
                )
                entries[key] = entry
                result.added.append(f"{provider}/{key}")
                before = None
            matched.add(key)
            # Keep registry keys and provider IDs stable for historical evidence
            # and aliases. Never transfer priors to a different model generation.
            entry.display_name = official.display_name
            entry.source_urls = official.source_urls
            if official.supported_efforts is not None:
                entry.supported_efforts = sorted(set(official.supported_efforts), key=EFFORT_ORDER.index)
            else:
                result.warnings.append(f"{provider}/{key}: effort support unconfirmed; existing values preserved.")
            entry.source_pricing.update(official.source_pricing)
            if before is not None and before != entry.model_dump(mode="json"):
                result.changed.append(f"{provider}/{key}")
        result.unconfirmed.extend(f"{provider}/{key}" for key in entries if key not in matched)
    if result.added or result.changed:
        match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", current.registry_version)
        if not match:
            raise ModelUpdateError("registry_version must be major.minor.patch to increment safely.")
        candidate.registry_version = f"{match[1]}.{match[2]}.{int(match[3]) + 1}"
        result.registry_version = candidate.registry_version
        result.status = "updated"
    return ModelRegistry.model_validate(candidate.model_dump(mode="json")), result


def _atomic_write(path: Path, content: bytes) -> None:
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def update_models(config: ConfigBundle) -> ModelUpdateResult:
    # ponytail: one backend worker; use a process lock and cache invalidation
    # before deploying multiple workers against the same registry directory.
    if not config.reload_lock.acquire(blocking=False):
        raise ModelUpdateError("A model update or configuration reload is already running.")
    try:
        path = config.directory / "models.yaml"
        original = path.read_bytes()
        current = ModelRegistry.model_validate(yaml.safe_load(original))
        candidate, result = build_candidate(current, fetch_official_models())
        config.validate_registry(candidate)
        for provider in ("codex", "claude"):
            if any(p == provider for p, _, _ in current.enabled_configurations()) and not any(
                p == provider for p, _, _ in candidate.enabled_configurations()
            ):
                raise ModelUpdateError(f"Update would leave {provider} without a routable model.")
        if result.status == "unchanged":
            return result
        content = (
            "# Official metadata refreshed by Update Models. Capability and burn priors are local assumptions.\n"
            "# New models remain disabled until their priors are reviewed. See source_urls for provenance.\n"
            + yaml.safe_dump(candidate.model_dump(mode="json"), sort_keys=False, allow_unicode=True)
        ).encode("utf-8")
        # Validate the exact serialized bytes before creating any backup or replacement.
        ModelRegistry.model_validate(yaml.safe_load(content))
        if path.read_bytes() != original:
            raise ModelUpdateError("models.yaml changed during the fetch; retry the update.")
        fd, backup = tempfile.mkstemp(prefix=f"models.{current.registry_version}.", suffix=".yaml.bak", dir=path.parent)
        with os.fdopen(fd, "wb") as handle:
            handle.write(original)
            handle.flush()
            os.fsync(handle.fileno())
        result.backup = Path(backup).name
        try:
            _atomic_write(path, content)
            config.load()
        except Exception as exc:
            try:
                _atomic_write(path, original)
            except Exception as restore_error:
                raise ModelUpdateError(f"Update and restore failed; recover models.yaml from {result.backup}: {restore_error}") from exc
            raise ModelUpdateError(f"Update failed; original registry restored. Backup: {result.backup}. {exc}") from exc
        return result
    except ModelUpdateError:
        raise
    except Exception as exc:
        raise ModelUpdateError(f"Model update failed; registry was not replaced. {exc}") from exc
    finally:
        config.reload_lock.release()
