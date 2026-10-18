"""Client for the Pi bridge.

The backend knows nothing about the Pi SDK, Pi RPC, or how Pi authenticates.
It knows one HTTP contract:

    GET  /health      is the analyzer reachable and logged in
    POST /analyze     task text in, TaskFingerprint out
    GET  /providers   what the user could analyze with
    GET  /models      which models are available

That seam means the bridge can switch from the SDK to RPC to a subprocess
without a single change on this side.

Everything the bridge returns is untrusted input and is validated against the
TaskFingerprint schema before it goes anywhere near the router.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx

from ..config import Settings, get_settings
from ..schemas.task_fingerprint import FingerprintEnvelope
from .analyzer import AnalyzerError, AnalyzerOverrides, TaskAnalyzer

#: Dropped in the shared data directory; scripts/pi-watchdog.ps1 on the host
#: deletes it and restarts the bridge. The backend runs in a container and has
#: no other way to restart a host process.
RESTART_REQUEST = "pi-restart.request"


class PiBridgeAnalyzer(TaskAnalyzer):
    """Talks to the host-local Pi bridge over HTTP."""

    name = "pi"

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._base_url = self._settings.pi_bridge_url

    @property
    def base_url(self) -> str:
        return self._base_url

    async def analyze(
        self, task: str, overrides: AnalyzerOverrides | None = None
    ) -> FingerprintEnvelope:
        if not task or not task.strip():
            raise AnalyzerError("The task is empty.")

        started = time.perf_counter()
        payload: dict[str, Any] = {"task": task, "analysis_profile": "router-v1"}
        # Whatever the user pinned in Settings. Anything omitted falls back to
        # the bridge's own boot configuration; the bridge never substitutes a
        # different model on its own.
        if overrides is not None:
            payload.update(overrides.as_payload())

        try:
            response = await self._post_analyze(payload)
        except httpx.ConnectError:
            # The bridge is down. Reconnect in place rather than making the user
            # do it, then try exactly once more.
            reconnected = await self.reconnect()
            if not reconnected.get("available"):
                raise AnalyzerError(
                    "Cannot reach the Pi bridge, and reconnecting did not bring it back.",
                    detail=str(reconnected.get("note") or ""),
                ) from None
            try:
                response = await self._post_analyze(payload)
            except httpx.ConnectError as exc:
                raise AnalyzerError(
                    f"Cannot reach the Pi bridge at {self._base_url}.", detail=str(exc)
                ) from exc
            except httpx.TimeoutException as exc:
                raise AnalyzerError(
                    f"The Pi bridge did not answer within {self._settings.pi_timeout_seconds:.0f}s.",
                    detail=str(exc),
                ) from exc
        except httpx.TimeoutException as exc:
            raise AnalyzerError(
                f"The Pi bridge did not answer within {self._settings.pi_timeout_seconds:.0f}s.",
                detail=str(exc),
            ) from exc

        if response.status_code == 400:
            raise AnalyzerError(
                "The analyzer configuration is invalid.", detail=_error_detail(response)
            )
        if response.status_code == 503:
            # The configured analyzer exists but cannot be used. The bridge
            # deliberately does not fall back, so surface its reasoning intact
            # rather than reducing it to "unavailable".
            raise AnalyzerError(
                _error_message(response), detail=_error_detail(response)
            )
        if response.status_code >= 400:
            raise AnalyzerError(
                f"The Pi bridge rejected the request ({response.status_code}).",
                detail=_error_detail(response),
            )

        try:
            body = response.json()
        except ValueError as exc:
            raise AnalyzerError(
                "The Pi bridge returned a response that is not JSON.", detail=response.text[:500]
            ) from exc

        try:
            envelope = FingerprintEnvelope.model_validate(body)
        except Exception as exc:
            # The bridge already validates and retries once. Reaching here means
            # the analyzer could not be coerced into the contract at all, and
            # guessing values on its behalf would corrupt the learning data.
            raise AnalyzerError(
                "The analyzer returned a fingerprint that does not match the schema.",
                detail=str(exc)[:1500],
            ) from exc

        if envelope.analyzer.duration_ms is None:
            envelope.analyzer.duration_ms = int((time.perf_counter() - started) * 1000)
        return envelope

    async def _post_analyze(self, payload: dict[str, Any]) -> httpx.Response:
        async with httpx.AsyncClient(timeout=self._settings.pi_timeout_seconds) as client:
            return await client.post(f"{self._base_url}/analyze", json=payload)

    async def reconnect(self, wait_seconds: float = 60.0) -> dict[str, Any]:
        """Restart the bridge through the host watchdog and wait for it. Never raises.

        A fresh process re-reads .env, Pi's credentials (a /login done
        elsewhere) and Pi's model list. When no watchdog picks the request up,
        fall back to the bridge's in-place POST /reload.
        """
        before = await self.status()
        flag = self._settings.data_dir / RESTART_REQUEST
        try:
            flag.parent.mkdir(parents=True, exist_ok=True)
            flag.write_text(str(time.time()), encoding="utf-8")
        except OSError as exc:
            return {**await self._reload(), "reconnect": "failed", "note": f"Cannot write {flag}: {exc}"}

        deadline = time.monotonic() + wait_seconds
        while time.monotonic() < deadline:
            await asyncio.sleep(2)
            if flag.exists():
                continue  # the watchdog has not picked it up yet
            current = await self.status()
            restarted = current.get("started_at") != before.get("started_at")
            if current["available"] and restarted:
                return {**current, "reconnect": "restarted"}

        if flag.exists():
            flag.unlink(missing_ok=True)
            status = await self._reload()
            if status["available"]:
                return {**status, "reconnect": "reloaded"}
            return {
                **status,
                "reconnect": "failed",
                "note": (
                    "The Pi watchdog is not running, so the bridge could not be restarted. "
                    "Run scripts/install-pi-autostart.ps1 once; it starts with Windows after that."
                ),
            }
        return {**await self.status(), "reconnect": "failed"}

    async def _reload(self) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                await client.post(f"{self._base_url}/reload")
        except Exception:  # noqa: BLE001 - status() below reports what is wrong
            pass
        return await self.status()

    async def status(self) -> dict[str, Any]:
        """Never raises. The Settings page must render even when Pi is down."""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f"{self._base_url}/health")
            if response.status_code >= 400:
                return self._down(f"bridge returned HTTP {response.status_code}")
            body = response.json()
        except httpx.ConnectError:
            return self._down("not running")
        except httpx.TimeoutException:
            return self._down("did not respond within 5s")
        except Exception as exc:  # noqa: BLE001 - status must never propagate
            return self._down(str(exc))

        return {
            "status": body.get("status", "ok"),
            "available": bool(body.get("pi_available", False)),
            "analyzer": "pi",
            "url": self._base_url,
            # What the analyzer is configured to be...
            "configured_provider": body.get("configured_provider"),
            "configured_model": body.get("configured_model"),
            "configured_effort": body.get("configured_effort"),
            "configured_from": body.get("configured_from"),
            "authenticated": bool(body.get("authenticated", False)),
            # ...and what a request would actually use right now. These differ
            # exactly when something is wrong, which is why both are reported.
            "actual_provider": body.get("actual_provider"),
            "actual_model": body.get("actual_model"),
            "provider": body.get("actual_provider") or body.get("provider"),
            "model": body.get("actual_model") or body.get("model"),
            "thinking_level": body.get("configured_effort") or body.get("thinking_level"),
            "bridge_version": body.get("version"),
            "started_at": body.get("started_at"),
            "authenticated_providers": body.get("authenticated_providers", []),
            "alternatives": body.get("alternatives", []),
            "error_code": body.get("error_code"),
            "env_file": body.get("env_file"),
            "note": body.get("note"),
        }

    async def providers(self) -> list[dict[str, Any]]:
        return await self._get_list("/providers", "providers")

    async def models(self) -> list[dict[str, Any]]:
        return await self._get_list("/models", "models")

    async def _get_list(self, path: str, key: str) -> list[dict[str, Any]]:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(f"{self._base_url}{path}")
            response.raise_for_status()
            body = response.json()
        except Exception:  # noqa: BLE001 - an empty list is a fine answer here
            return []
        value = body.get(key, []) if isinstance(body, dict) else body
        return value if isinstance(value, list) else []

    def _down(self, reason: str) -> dict[str, Any]:
        return {
            "status": "unavailable",
            "available": False,
            "analyzer": "pi",
            "url": self._base_url,
            "configured_provider": None,
            "configured_model": None,
            "configured_effort": None,
            "authenticated": False,
            "actual_provider": None,
            "actual_model": None,
            "provider": None,
            "model": None,
            "alternatives": [],
            "note": f"Pi bridge {reason}.",
        }


def _error_message(response: httpx.Response) -> str:
    """The bridge's own top-level explanation, for a failure it can describe."""
    try:
        body = response.json()
    except ValueError:
        return f"The Pi bridge returned HTTP {response.status_code}."
    if isinstance(body, dict) and isinstance(body.get("error"), str):
        return body["error"]
    return f"The Pi bridge returned HTTP {response.status_code}."


def _error_detail(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text[:500]
    if isinstance(body, dict):
        for key in ("detail", "error", "message"):
            if key in body:
                return str(body[key])[:1000]
    return str(body)[:500]
