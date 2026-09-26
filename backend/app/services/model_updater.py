"""Official-source registry refresh, plus an optional local research pass.

Fetches structured facts (model IDs, pricing, supported efforts) from the same
three official URLs via regex/JSON parsing - no task text, no user
credentials, ever. For models that have never been reviewed, it additionally
asks the local Pi bridge to derive capability priors from those same fetched
documents and the existing registry, using the user's own authenticated
subscription - the same mechanism task classification already uses, on
loopback only. No coding task or repository content is ever sent. If Pi is
unreachable, those specific models fall back to the disabled-placeholder
behaviour this module has always had, and the rest of the update still
succeeds.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

import httpx
import yaml
from pydantic import BaseModel, ConfigDict, Field

from ..config import ConfigBundle, Settings, get_settings
from ..schemas.enums import EFFORT_ORDER, Effort
from ..schemas.model_registry import CAPABILITY_DIMENSIONS, ModelEntry, ModelRegistry

OPENAI_MODELS = "https://raw.githubusercontent.com/openai/codex/main/codex-rs/models-manager/models.json"
CLAUDE_MODELS = "https://platform.claude.com/docs/en/models/overview.md"
CLAUDE_EFFORT = "https://platform.claude.com/docs/en/build-with-claude/effort.md"
SOURCES = (OPENAI_MODELS, CLAUDE_MODELS, CLAUDE_EFFORT)
MAX_SOURCE_BYTES = 4_000_000


class ModelUpdateError(RuntimeError):
    pass


class ResearchUnavailable(RuntimeError):
    """The Pi bridge could not score models this cycle.

    Covers being unreachable, timing out, returning a non-2xx status, and
    returning a response that fails schema validation - every one of these is
    handled identically by the caller (fall back to placeholders and warn),
    so they share one exception type rather than a dict of failure codes.
    """


class ResearchOutcome(BaseModel):
    """A successful response from the Pi bridge's /research endpoint."""

    model_config = ConfigDict(extra="ignore")
    capability_priors: dict[str, dict[str, float]] = Field(default_factory=dict)
    claude_reference_md: str | None = None
    codex_reference_md: str | None = None


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
    #: Never-reviewed models auto-enabled this cycle by the Pi research pass.
    researched: list[str] = Field(default_factory=list)
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


def fetch_official_models() -> tuple[dict[str, list[OfficialModel]], str]:
    """Fetch and parse the official sources.

    Also returns the raw Claude overview text alongside the parsed models: it
    already has to be fetched to build the pricing/effort table, and the
    research pass wants the same qualitative prose that parsing discards, with
    no second network round-trip.
    """
    with httpx.Client(timeout=15, headers={"User-Agent": "North-Star-Model-Updater/1.0"}) as client:
        openai_text = _fetch(client, OPENAI_MODELS)
        claude_overview_text = _fetch(client, CLAUDE_MODELS)
        claude_effort_text = _fetch(client, CLAUDE_EFFORT)
        sources = {
            "codex": parse_openai(openai_text),
            "claude": parse_claude(claude_overview_text, claude_effort_text),
        }
        return sources, claude_overview_text


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
        _bump_version(current.registry_version, candidate, result)
    return ModelRegistry.model_validate(candidate.model_dump(mode="json")), result


def _bump_version(current_version: str, candidate: ModelRegistry, result: ModelUpdateResult) -> None:
    """Increment the patch version and mark the result as changed.

    A separate function so the research step (which can change the registry
    even when official metadata alone did not) can reuse the exact same
    increment logic rather than duplicating the regex.
    """
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", current_version)
    if not match:
        raise ModelUpdateError("registry_version must be major.minor.patch to increment safely.")
    candidate.registry_version = f"{match[1]}.{match[2]}.{int(match[3]) + 1}"
    result.registry_version = candidate.registry_version
    result.status = "updated"


def _research_targets(candidate: ModelRegistry) -> list[tuple[str, str]]:
    """Every model never yet researched.

    `review_required` is set only at creation time (see build_candidate above)
    and cleared only by a successful research pass (see _apply_research
    below), so it already means exactly "never researched" - covering both
    this cycle's brand-new entries and any leftover from a prior cycle where
    Pi was unreachable, with no separate bookkeeping needed.
    """
    return [
        (provider_name, model_key)
        for provider_name, provider in candidate.providers.items()
        for model_key, entry in provider.models.items()
        if entry.review_required
    ]


def _reference_paths(settings: Settings) -> dict[str, Path]:
    return {
        "claude": settings.reference_dir / "ClaudeLLM.md",
        "codex": settings.reference_dir / "CodexLLM.md",
    }


def research_capabilities(
    settings: Settings,
    targets: list[tuple[str, str]],
    candidate: ModelRegistry,
    sources: dict[str, list[OfficialModel]],
    claude_docs_raw: str,
    current_docs: dict[str, str],
) -> ResearchOutcome:
    """Ask the local Pi bridge to score never-reviewed models.

    Grounds the request in exactly what's already in hand: the raw fetched
    Claude docs (for qualitative claims plain pricing tables lose), the
    structured Codex catalog, the full current registry as calibration
    anchors, and the current reference/*.md prose so the rewrite continues the
    user's own research voice. No second fetch, no task text, no credentials.

    Every failure mode - unreachable, timeout, non-2xx, non-JSON, or a
    response that fails schema validation - becomes ResearchUnavailable. The
    caller treats all of them identically: leave these targets as placeholders
    for this cycle, warn, and let the rest of the update proceed.
    """
    provider_keys = {provider for provider, _ in targets}
    candidates = []
    for provider, model_key in targets:
        entry = candidate.providers[provider].models[model_key]
        candidates.append(
            {
                "key": f"{provider}/{model_key}",
                "provider": provider,
                "display_name": entry.display_name,
                "model_id": entry.model_id,
                "source_pricing": entry.source_pricing,
                "supported_efforts": [e.value for e in entry.supported_efforts],
            }
        )
    anchors = []
    for provider_name, provider in candidate.providers.items():
        for model_key, entry in provider.models.items():
            if entry.review_required:
                continue
            anchors.append(
                {
                    "key": f"{provider_name}/{model_key}",
                    "provider": provider_name,
                    "display_name": entry.display_name,
                    "relative_model_burn": entry.relative_model_burn,
                    "source_pricing": entry.source_pricing,
                    "capability_priors": entry.capability_priors,
                }
            )
    payload = {
        "candidates": candidates,
        "registry_anchors": anchors,
        "claude_docs_text": claude_docs_raw if "claude" in provider_keys else None,
        "codex_catalog": (
            [
                {
                    "model_id": m.model_id,
                    "display_name": m.display_name,
                    "supported_efforts": [e.value for e in m.supported_efforts]
                    if m.supported_efforts
                    else [],
                }
                for m in sources.get("codex", [])
            ]
            if "codex" in provider_keys
            else None
        ),
        "claude_reference_md": current_docs.get("claude"),
        "codex_reference_md": current_docs.get("codex"),
    }
    try:
        with httpx.Client(timeout=settings.pi_timeout_seconds) as client:
            response = client.post(f"{settings.pi_bridge_url}/research", json=payload)
    except httpx.ConnectError as exc:
        raise ResearchUnavailable(f"cannot reach the Pi bridge at {settings.pi_bridge_url}: {exc}") from exc
    except httpx.TimeoutException as exc:
        raise ResearchUnavailable(
            f"the Pi bridge did not answer within {settings.pi_timeout_seconds:.0f}s: {exc}"
        ) from exc
    if response.status_code >= 400:
        raise ResearchUnavailable(
            f"the Pi bridge rejected the research request (HTTP {response.status_code}): {response.text[:500]}"
        )
    try:
        body = response.json()
    except ValueError as exc:
        raise ResearchUnavailable(f"the Pi bridge returned a response that is not JSON: {exc}") from exc
    try:
        return ResearchOutcome.model_validate(body)
    except Exception as exc:
        raise ResearchUnavailable(
            f"the Pi bridge returned a response that does not match the expected schema: {exc}"
        ) from exc


def _apply_research(
    candidate: ModelRegistry,
    targets: list[tuple[str, str]],
    outcome: ResearchOutcome,
    current_docs: dict[str, str],
    doc_paths: dict[str, Path],
) -> tuple[list[str], list[str], dict[Path, bytes]]:
    """Fold research results onto `candidate` in place.

    Mutating ModelEntry instances directly and deferring validation to the
    caller's re-validation pass is the same pattern build_candidate already
    uses above for official-source updates.

    Returns (researched_keys, warnings, doc_updates):
      - researched_keys: targets that were auto-enabled this cycle (real,
        non-placeholder priors AND confirmed effort support).
      - warnings: one entry per target that could not be auto-enabled, saying
        why - a target with unusable output stays exactly as the placeholder
        build_candidate already gave it.
      - doc_updates: reference/*.md files whose content actually changed,
        ready to write with the same _atomic_write() helper used for
        models.yaml.
    """
    researched_keys: list[str] = []
    warnings: list[str] = []
    for provider, model_key in targets:
        full_key = f"{provider}/{model_key}"
        entry = candidate.providers[provider].models[model_key]
        priors = outcome.capability_priors.get(full_key)
        if priors is None:
            warnings.append(f"{full_key}: the researcher did not return scores; left as a placeholder.")
            continue
        if all(v == 0.0 for v in priors.values()):
            warnings.append(f"{full_key}: the researcher returned all-zero scores; left as a placeholder.")
            continue
        entry.capability_priors = dict(priors)
        if not entry.supported_efforts:
            warnings.append(
                f"{full_key}: capability priors were researched, but effort support is still "
                "unconfirmed; left disabled until official sources confirm it."
            )
            continue
        entry.review_required = False
        entry.enabled = True
        researched_keys.append(full_key)

    doc_updates: dict[Path, bytes] = {}
    for provider, doc_text in (
        ("claude", outcome.claude_reference_md),
        ("codex", outcome.codex_reference_md),
    ):
        if provider not in current_docs or doc_text is None:
            continue
        if doc_text == current_docs[provider]:
            continue
        doc_updates[doc_paths[provider]] = doc_text.encode("utf-8")

    return researched_keys, warnings, doc_updates


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


def update_models(config: ConfigBundle, settings: Settings | None = None) -> ModelUpdateResult:
    # ponytail: one backend worker; use a process lock and cache invalidation
    # before deploying multiple workers against the same registry directory.
    if not config.reload_lock.acquire(blocking=False):
        raise ModelUpdateError("A model update or configuration reload is already running.")
    try:
        path = config.directory / "models.yaml"
        original = path.read_bytes()
        current = ModelRegistry.model_validate(yaml.safe_load(original))
        sources, claude_docs_raw = fetch_official_models()
        candidate, result = build_candidate(current, sources)

        # Research never-reviewed models before validating: a target folded in
        # here is covered by every guarantee below exactly like an
        # official-source change is - same validation, same backup, same
        # atomic write, same rollback on failure.
        doc_updates: dict[Path, bytes] = {}
        targets = _research_targets(candidate)
        if targets:
            settings = settings or get_settings()
            before_research = candidate.model_dump(mode="json")
            try:
                doc_paths = _reference_paths(settings)
                providers_present = {provider for provider, _ in targets}
                current_docs = {
                    provider: doc_paths[provider].read_text(encoding="utf-8")
                    for provider in providers_present
                    if doc_paths[provider].exists()
                }
                outcome = research_capabilities(
                    settings, targets, candidate, sources, claude_docs_raw, current_docs
                )
                researched_candidate = candidate.model_copy(deep=True)
                researched_keys, apply_warnings, doc_updates = _apply_research(
                    researched_candidate, targets, outcome, current_docs, doc_paths
                )
                researched_candidate = ModelRegistry.model_validate(
                    researched_candidate.model_dump(mode="json")
                )
            except Exception as exc:  # noqa: BLE001 - a research problem must degrade, never fail the update
                result.warnings.append(
                    f"Pi bridge unreachable or returned unusable data; {len(targets)} model(s) "
                    f"left with placeholder priors ({exc}). Rerun Update Models once Pi is running."
                )
            else:
                candidate = researched_candidate
                result.warnings.extend(apply_warnings)
                result.researched = researched_keys
                registry_changed = candidate.model_dump(mode="json") != before_research
                if registry_changed and result.status == "unchanged":
                    _bump_version(current.registry_version, candidate, result)

        config.validate_registry(candidate)
        for provider in ("codex", "claude"):
            if any(p == provider for p, _, _ in current.enabled_configurations()) and not any(
                p == provider for p, _, _ in candidate.enabled_configurations()
            ):
                raise ModelUpdateError(f"Update would leave {provider} without a routable model.")
        if result.status == "unchanged" and not doc_updates:
            return result
        content = (
            "# Official metadata refreshed by Update Models. Capability and burn priors are\n"
            "# local assumptions: either set by hand, or, for a never-reviewed model, derived\n"
            "# by a local Pi research pass grounded in official docs (see source_urls). A model\n"
            "# Pi could not research yet stays disabled with placeholder priors; see reference/.\n"
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

        # models.yaml is now durably committed and reloaded. Reference docs are
        # plain documentation - nothing at runtime parses them - so a failure
        # writing them is reported, never rolled back: reverting models.yaml at
        # this point would desync disk from the live in-memory config, which is
        # worse than a best-effort doc that failed to update.
        for doc_path, doc_bytes in doc_updates.items():
            try:
                _atomic_write(doc_path, doc_bytes)
            except OSError as exc:
                result.warnings.append(f"{doc_path.name} could not be updated: {exc}")
        return result
    except ModelUpdateError:
        raise
    except Exception as exc:
        raise ModelUpdateError(f"Model update failed; registry was not replaced. {exc}") from exc
    finally:
        config.reload_lock.release()
