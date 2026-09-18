from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.request
from typing import Any

from core.api_write_auth import build_signed_write_envelope
from core.public_hive.config import PublicHiveBridgeConfig, _normalize_base_url
from core.public_hive.truth import http_error_detail


def _door_urlopen(request: Any, timeout: float | None = None, context: ssl.SSLContext | None = None) -> Any:
    """Default opener: the ONE outbound HTTP door (`core.remote_fetch_policy.open_remote`),
    so the turn's remote-fetch veto is enforced before any socket opens and the fetch is
    reported into the owning turn's ledger. The computed TLS ``context`` is accepted but not
    forwarded -- consistent with the other rebound lanes (r9c machine download, r-f05 media),
    the door opens sockets with the default verified TLS stack."""

    from core.remote_fetch_policy import open_remote

    return open_remote(request, timeout=float(timeout if timeout is not None else 30))


class PublicHiveHttpClient:
    def __init__(
        self,
        config: PublicHiveBridgeConfig,
        *,
        urlopen: Any | None = None,
        voolbook_token_fn: Any | None = None,
    ) -> None:
        self.config = config
        self._urlopen = urlopen or _door_urlopen
        self._voolbook_token_fn = voolbook_token_fn

    def post_many(
        self,
        route: str,
        *,
        payload: dict[str, Any],
        base_urls: tuple[str, ...],
    ) -> dict[str, Any]:
        posted_to: list[str] = []
        errors: list[str] = []
        for base_url in base_urls:
            try:
                self.post_json(base_url, route, payload)
                posted_to.append(base_url.rstrip("/"))
            except Exception as exc:
                errors.append(f"{base_url.rstrip('/')}: {exc}")
        return {"ok": bool(posted_to), "status": "posted" if posted_to else "failed", "posted_to": posted_to, "errors": errors}

    def get_json(self, base_url: str, route: str) -> Any:
        target_path = route if str(route).startswith("/") else f"/{route}"
        url = f"{str(base_url).rstrip('/')}{target_path}"
        request = urllib.request.Request(url, method="GET")
        request.add_header("Content-Type", "application/json")
        auth_token = self.auth_token_for_url(base_url)
        if auth_token:
            request.add_header("X-Vool-Meet-Token", auth_token)
        context = self.ssl_context_for_url(url)
        with self._urlopen(request, timeout=self.config.request_timeout_seconds, context=context) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if not payload.get("ok"):
            raise ValueError(str(payload.get("error") or f"Meet read failed for {url}"))
        return payload.get("result")

    def post_json(self, base_url: str, route: str, payload: dict[str, Any]) -> dict[str, Any]:
        target_path = route.rstrip("/") or "/"
        signed_payload = dict(payload or {})
        write_grant = self.write_grant_for_request(base_url, target_path)
        if isinstance(write_grant, dict) and write_grant:
            signed_payload["write_grant"] = write_grant
        envelope = build_signed_write_envelope(target_path=target_path, payload=signed_payload)
        raw = json.dumps(envelope, sort_keys=True).encode("utf-8")
        url = f"{str(base_url).rstrip('/')}{target_path}"
        request = urllib.request.Request(url, data=raw, method="POST")
        request.add_header("Content-Type", "application/json")
        auth_token = self.auth_token_for_url(base_url)
        if auth_token:
            request.add_header("X-Vool-Meet-Token", auth_token)
        nb_token = self._get_voolbook_token()
        if nb_token:
            request.add_header("X-VoolBook-Token", nb_token)
        context = self.ssl_context_for_url(url)
        try:
            with self._urlopen(request, timeout=self.config.request_timeout_seconds, context=context) as response:
                response_payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise ValueError(http_error_detail(exc, fallback=f"Meet write failed for {url}")) from exc
        if not response_payload.get("ok"):
            raise ValueError(str(response_payload.get("error") or f"Meet write failed for {url}"))
        return dict(response_payload.get("result") or {})

    def auth_token_for_url(self, url: str) -> str | None:
        normalized = _normalize_base_url(url)
        token = self.config.auth_tokens_by_base_url.get(normalized)
        if token:
            return token
        return self.config.auth_token

    def write_grant_for_request(self, base_url: str, route: str) -> dict[str, Any] | None:
        normalized = _normalize_base_url(base_url)
        scoped_grants = dict(self.config.write_grants_by_base_url.get(normalized) or {})
        grant = scoped_grants.get(route.rstrip("/") or "/")
        return dict(grant) if isinstance(grant, dict) else None

    def ssl_context_for_url(self, url: str) -> ssl.SSLContext | None:
        if not str(url).lower().startswith("https://"):
            return None
        if self.config.tls_insecure_skip_verify:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            return ctx
        if self.config.tls_ca_file:
            return ssl.create_default_context(cafile=self.config.tls_ca_file)
        return ssl.create_default_context()

    def _get_voolbook_token(self) -> str | None:
        if self._voolbook_token_fn is None:
            return None
        try:
            return self._voolbook_token_fn()
        except Exception:
            return None
