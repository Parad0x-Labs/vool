"""Post-restart health checks — the gate between "restarted" and "verified".

An update is not successful because the process started; it is successful when the
restarted app answers and reports the version the update installed. The probe is a
pure injected callable in tests; the default polls the app's local health endpoint.
"""
from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class HealthResult:
    ok: bool
    detail: str = ""
    reported_version: str = ""

    @property
    def plain_message(self) -> str:
        if self.ok:
            return "The app came back up and is running the new version."
        return "The app didn't come back up as expected after the update."


def health_identity(payload: dict) -> str:
    """The exact installed identity a health body reports, against the CURRENT
    production /healthz schema: `runtime.commit_full` (the exact checkout SHA a
    packaged install stamps at build time), then `runtime.build_id`, then
    `runtime.app_version`. The bare top-level `version` key remains the last
    fallback for sandbox rigs that predate the runtime stamp."""
    runtime = payload.get("runtime")
    if isinstance(runtime, dict):
        for key in ("commit_full", "build_id", "app_version"):
            value = str(runtime.get(key) or "").strip()
            if value:
                return value
    return str(payload.get("version") or "").strip()


def _default_probe_once(url: str, timeout: float) -> tuple[bool, str, str]:
    import urllib.request

    try:
        with urllib.request.urlopen(str(url), timeout=timeout) as response:
            body = response.read(65536).decode("utf-8", "replace")
            version = ""
            try:
                payload = json.loads(body)
                if isinstance(payload, dict):
                    version = health_identity(payload)
            except ValueError:
                pass
            return (200 <= response.status < 300, body[:200], version)
    except Exception as exc:
        return (False, str(exc), "")


class HttpHealthProbe:
    """Poll `url` until it answers 2xx (and reports `expected_version` when given) or
    the deadline passes. `probe_once` is injectable; `sleeper` too (tests: no waits)."""

    def __init__(
        self,
        url: str,
        *,
        poll_interval: float = 1.0,
        request_timeout: float = 3.0,
        probe_once: Callable[[str, float], tuple[bool, str, str]] | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.url = str(url)
        self.poll_interval = poll_interval
        self.request_timeout = request_timeout
        self._probe_once = probe_once or _default_probe_once
        self._sleep = sleeper
        self._clock = clock

    def probe(self, *, timeout: float, expected_version: str | None = None) -> HealthResult:
        deadline = self._clock() + max(0.0, float(timeout))
        last = (False, "never probed", "")
        while True:
            ok, detail, version = self._probe_once(self.url, self.request_timeout)
            if expected_version:
                ok = ok and version == str(expected_version)
            last = (ok, detail, version)
            if ok:
                return HealthResult(True, detail, version)
            if self._clock() >= deadline:
                return HealthResult(False, detail or last[1], version)
            self._sleep(self.poll_interval)


__all__ = ["HealthResult", "HttpHealthProbe", "health_identity"]
