"""Studio-side HTTP client for the served daemon (public API reads only).

Thin, dependency-free client for the endpoints the studio consumes:
/healthz, /api/runtime/capabilities, /api/runtime/events, /api/council/*.
It never reads anything but the product's public surfaces — no process
introspection, no hidden files, no model internals.
"""

from __future__ import annotations

import contextlib
import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


class WireError(RuntimeError):
    """A transport failure reaching the daemon — never silently swallowed."""


class DemoWireClient:
    def __init__(self, base_url: str, *, default_timeout_s: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.default_timeout_s = float(default_timeout_s)

    def update_base_url(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    def get_json(self, path: str, *, timeout_s: float | None = None) -> Any:
        url = f"{self.base_url}{path}"
        req = urllib.request.Request(url, headers={"accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout_s or self.default_timeout_s) as resp:
                return json.loads(resp.read().decode("utf-8", errors="replace"))
        except urllib.error.HTTPError as exc:
            body = ""
            with contextlib.suppress(Exception):
                body = exc.read().decode("utf-8", errors="replace")[:400]
            raise WireError(f"{path} answered HTTP {exc.code}: {body}") from exc
        except Exception as exc:  # transport
            raise WireError(f"{path} unreachable: {type(exc).__name__}: {exc}") from exc

    def healthz(self) -> dict[str, Any]:
        payload = self.get_json("/healthz")
        if not isinstance(payload, dict):
            raise WireError("/healthz returned a non-dict payload")
        return payload

    def capabilities(self) -> dict[str, Any]:
        payload = self.get_json("/api/runtime/capabilities")
        if not isinstance(payload, dict):
            raise WireError("/api/runtime/capabilities returned a non-dict payload")
        return payload

    def events(self, session_id: str, *, after: int = 0, limit: int = 200) -> list[dict[str, Any]]:
        payload = self.get_json(
            f"/api/runtime/events?session={urllib.parse.quote(session_id)}&after={after}&limit={limit}"
        )
        if not isinstance(payload, dict):
            raise WireError("/api/runtime/events returned a non-dict payload")
        return [row for row in (payload.get("events") or []) if isinstance(row, dict)]

    def council_runs(self) -> list[dict[str, Any]]:
        payload = self.get_json("/api/council/runs")
        if not isinstance(payload, dict):
            return []
        return [row for row in (payload.get("runs") or []) if isinstance(row, dict)]

    def council_scorecard(self, run_id: str) -> dict[str, Any]:
        payload = self.get_json(
            f"/api/council/scorecard?run={urllib.parse.quote(run_id)}"
        )
        return payload if isinstance(payload, dict) else {}
