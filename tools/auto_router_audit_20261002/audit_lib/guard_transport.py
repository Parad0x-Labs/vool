"""The synthetic transport guard: the ONLY wire the audit may speak.

``install()`` replaces ``requests.post``/``requests.request``/``requests.get``
and ``urllib.request.urlopen`` for the whole process. Every call whose URL is
not on the scenario allowlist raises :class:`AuditNetworkGuardViolation` and is
recorded, so an accidental live dispatch (catalog refresh, telemetry, a
mis-built endpoint) becomes loud evidence instead of a silent external call.
"""
from __future__ import annotations

import threading
import time
import urllib.request
from collections.abc import Callable
from typing import Any

import requests


class AuditNetworkGuardViolation(RuntimeError):
    """A dispatch attempted to leave for a host the scenario never allowlisted."""


class _SyntheticResponse:
    """A canned requests-like response for a scripted synthetic endpoint."""

    def __init__(self, *, status_code: int, body: dict[str, Any]) -> None:
        self.status_code = status_code
        self._body = body
        self.elapsed = 0.0
        self.closed = False

    def json(self) -> dict[str, Any]:
        return self._body

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(
                f"{self.status_code} error for synthetic endpoint", response=self
            )

    def close(self) -> None:
        self.closed = True


class RecordedCall:
    __slots__ = (
        "allowlisted",
        "at",
        "method",
        "model_field",
        "payload",
        "payload_keys",
        "script",
        "url",
        "usage_field",
    )

    def __init__(self, *, method: str, url: str, allowlisted: bool, script: str,
                 payload: dict[str, Any] | None) -> None:
        self.method = method
        self.url = url
        self.allowlisted = allowlisted
        self.script = script
        self.payload_keys = sorted(payload) if isinstance(payload, dict) else []
        self.payload = payload if isinstance(payload, dict) else None
        self.model_field = str((payload or {}).get("model", "")) if isinstance(payload, dict) else ""
        usage = (payload or {}).get("usage") if isinstance(payload, dict) else None
        self.usage_field = usage
        self.at = time.time()

    def to_dict(self) -> dict[str, Any]:
        payload = None
        if self.payload is not None:
            # keep the trace small but complete enough to audit the envelope
            payload = {
                "model": self.payload.get("model"),
                "messages_count": len(self.payload.get("messages") or []),
                "tools_count": len(self.payload.get("tools") or []),
                "tool_names": [t.get("function", {}).get("name") for t in (self.payload.get("tools") or [])],
                "response_format": self.payload.get("response_format"),
                "max_tokens": self.payload.get("max_tokens"),
                "temperature": self.payload.get("temperature"),
                "messages_chars": sum(len(str(m.get("content") or "")) for m in (self.payload.get("messages") or [])),
                "keys": self.payload_keys,
            }
        return {
            "method": self.method,
            "url": self.url,
            "allowlisted": self.allowlisted,
            "script": self.script,
            "model_field": self.model_field,
            "at": self.at,
            "payload": payload,
        }


class GuardTransport:
    """Callable standing in for ``requests.post`` (+ siblings) during a scenario."""

    def __init__(self) -> None:
        self.allowlist: list[str] = []
        self.scripts: dict[str, str] = {}
        self.default_script: str = "ok_text:synthetic answer"
        self.calls: list[RecordedCall] = []
        self.violations: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._call_counts: dict[str, int] = {}

    # -- configuration ------------------------------------------------------
    def configure(self, *, allowlist: list[str], scripts: dict[str, str], default: str) -> None:
        self.allowlist = [str(p) for p in allowlist]
        self.scripts = {str(k): str(v) for k, v in scripts.items()}
        self.default_script = str(default)
        self.calls = []
        self.violations = []
        self._call_counts = {}

    def _url_allowlisted(self, url: str) -> bool:
        return any(str(url).startswith(prefix) for prefix in self.allowlist)

    def _script_for(self, url: str) -> str:
        """Resolve the script for a URL. A "|" separated list advances per call."""
        prefix = next((p for p in self.scripts if str(url).startswith(p)), None)
        if prefix is None:
            return self.default_script
        count = self._call_counts.get(prefix, 0)
        self._call_counts[prefix] = count + 1
        parts = self.scripts[prefix].split("|")
        return parts[min(count, len(parts) - 1)]

    # -- requests API -------------------------------------------------------
    def _enforce(self, method: str, url: str, payload: dict[str, Any] | None) -> str:
        """Record the call, refuse non-allowlisted hosts, return the script."""
        raw = str(url)
        loopback_read = method in ("GET", "URLOPEN") and raw.startswith(
            ("http://127.0.0.1", "http://localhost", "http://[::1]")
        )
        allowlisted = self._url_allowlisted(raw) or loopback_read
        script = self._script_for(raw)
        with self._lock:
            self.calls.append(
                RecordedCall(method=method, url=raw, allowlisted=allowlisted,
                             script=script, payload=payload)
            )
        if not allowlisted:
            violation = {
                "method": method,
                "url": raw,
                "reason": "host_not_in_scenario_allowlist",
                "at": time.time(),
            }
            with self._lock:
                self.violations.append(violation)
            raise AuditNetworkGuardViolation(
                f"audit guard: dispatch to non-synthetic host refused: {method} {raw}"
            )
        return script

    def post(self, url: str, *args: Any, **kwargs: Any) -> Any:
        payload = kwargs.get("json") if isinstance(kwargs.get("json"), dict) else None
        script = self._enforce("POST", str(url), payload)
        return self._synthetic_response(script)

    def request(self, method: str = "GET", url: str = "", *args: Any, **kwargs: Any) -> Any:
        payload = kwargs.get("json") if isinstance(kwargs.get("json"), dict) else None
        script = self._enforce(str(method).upper(), str(url), payload)
        return self._synthetic_response(script)

    def get(self, url: str, *args: Any, **kwargs: Any) -> Any:
        script = self._enforce("GET", str(url), None)
        return self._synthetic_response(script)

    def _synthetic_response(self, script: str) -> _SyntheticResponse:
        from . import synthetic as syn

        spec = syn.resolve_script(script)
        kind = spec["kind"]
        if kind == "timeout":
            raise requests.exceptions.Timeout(f"synthetic timeout for script {script!r}")
        if kind == "http":
            code = int(spec["payload"] or 500)
            return _SyntheticResponse(status_code=code, body={"error": {"code": code}})
        # the model field: pull it from the recorded call's payload so the
        # canned body reports the model actually requested on the wire
        model_name = ""
        with self._lock:
            for call in reversed(self.calls):
                if call.payload is not None and call.model_field:
                    model_name = call.model_field
                    break
        body = syn.response_payload_for(script, model_name=model_name or "synthetic-model")
        return _SyntheticResponse(status_code=200, body=body)

    # -- urlopen guard --------------------------------------------------------
    def urlopen(self, url: Any, *args: Any, **kwargs: Any) -> Any:
        raw = str(getattr(url, "full_url", url) or url)
        if raw.startswith(("http://", "https://")):
            # loopback GET-style reads are allowed+recorded by _enforce's
            # loopback_read rule; everything else http(s) is refused
            self._enforce("URLOPEN", raw, None)
            raise AuditNetworkGuardViolation(
                f"audit guard: urlopen to http(s) host refused by scenario allowlist: {raw}"
            )
        # non-http schemes (file:// reads of our own fixtures) pass through
        return self._real_urlopen(url, *args, **kwargs)

    # -- reporting ------------------------------------------------------------
    def calls_to_dict(self) -> list[dict[str, Any]]:
        with self._lock:
            return [c.to_dict() for c in self.calls]


class _Installed:
    def __init__(self) -> None:
        self.active = False
        self.guard: GuardTransport | None = None
        self._saved: dict[str, Callable[..., Any]] = {}


_INSTALLED = _Installed()


def install() -> GuardTransport:
    """Install the process-wide guard. Idempotent; returns the live guard.

    Patched surfaces, in addition to the requests library and urllib:
    ``core.provider_http.post/get`` — the adapter layer imports provider_http
    AS ``requests`` (``adapters/openai_compatible_adapter.py``), and its real
    implementation spawns a worker SUBPROCESS whose network happens outside
    this process; replacing the module functions keeps the dispatch observable
    here. That is the same seam the canonical routing tests patch.
    """
    if _INSTALLED.active and _INSTALLED.guard is not None:
        return _INSTALLED.guard
    guard = GuardTransport()
    _INSTALLED._saved = {
        "requests.post": requests.post,
        "requests.request": requests.request,
        "requests.get": requests.get,
        "urlopen": urllib.request.urlopen,
    }
    try:
        import core.provider_http as _provider_http

        for fn in ("post", "get", "request"):
            if hasattr(_provider_http, fn):
                _INSTALLED._saved[f"provider_http.{fn}"] = getattr(_provider_http, fn)
        del _provider_http
    except Exception:
        pass
    guard._real_urlopen = _INSTALLED._saved["urlopen"]  # type: ignore[attr-defined]
    requests.post = guard.post  # type: ignore[assignment]
    requests.request = guard.request  # type: ignore[assignment]
    requests.get = guard.get  # type: ignore[assignment]
    urllib.request.urlopen = guard.urlopen  # type: ignore[assignment]
    try:
        import core.provider_http as _provider_http

        _provider_http.post = guard.post  # type: ignore[assignment]
        if "provider_http.get" in _INSTALLED._saved:
            _provider_http.get = guard.get  # type: ignore[assignment]
        if "provider_http.request" in _INSTALLED._saved:
            _provider_http.request = guard.request  # type: ignore[assignment]
    except Exception:
        pass
    _INSTALLED.active = True
    _INSTALLED.guard = guard
    return guard


def uninstall() -> None:
    """Restore the real transports (used by tests and on harness shutdown)."""
    if not _INSTALLED.active:
        return
    requests.post = _INSTALLED._saved["requests.post"]  # type: ignore[assignment]
    requests.request = _INSTALLED._saved["requests.request"]  # type: ignore[assignment]
    requests.get = _INSTALLED._saved["requests.get"]  # type: ignore[assignment]
    urllib.request.urlopen = _INSTALLED._saved["urlopen"]  # type: ignore[assignment]
    try:
        import core.provider_http as _provider_http

        for fn in ("post", "get", "request"):
            key = f"provider_http.{fn}"
            if key in _INSTALLED._saved:
                setattr(_provider_http, fn, _INSTALLED._saved[key])
    except Exception:
        pass
    _INSTALLED.active = False
    _INSTALLED.guard = None


def active_guard() -> GuardTransport | None:
    return _INSTALLED.guard if _INSTALLED.active else None


def self_test() -> dict[str, bool]:
    """Validate the guard itself: an external host MUST be refused."""
    results: dict[str, bool] = {}
    probe = GuardTransport()
    probe.configure(
        allowlist=["https://synthetic-free.openrouter.invalid/v1"],
        scripts={},
        default="ok_text:probe",
    )
    try:
        probe.post("https://openrouter.ai/api/v1/chat/completions", json={"model": "x"})
        results["external_post_refused"] = False
    except AuditNetworkGuardViolation:
        results["external_post_refused"] = True
    except Exception:
        results["external_post_refused"] = False
    try:
        probe.get("https://example.com")
        results["external_get_refused"] = False
    except AuditNetworkGuardViolation:
        results["external_get_refused"] = True
    except Exception:
        results["external_get_refused"] = False
    try:
        probe.post("https://synthetic-free.openrouter.invalid/v1/chat/completions",
                   json={"model": "synthetic/tiny-free:free"})
        results["allowlisted_post_served"] = True
    except Exception:
        results["allowlisted_post_served"] = False
    results["violations_recorded"] = len(probe.violations) == 2
    return results
