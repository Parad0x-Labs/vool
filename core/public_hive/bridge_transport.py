from __future__ import annotations

import ssl
from typing import Any

from core.public_hive.config import _normalize_base_url

_UNSET_SENTINEL = object()


class PublicHiveBridgeTransportMixin:
    def _get_voolbook_token(self) -> str | None:
        if self._voolbook_token is _UNSET_SENTINEL:
            try:
                from core.voolbook_identity import load_local_token

                self._voolbook_token = load_local_token()
            except Exception:
                self._voolbook_token = None
        return self._voolbook_token

    def enabled(self) -> bool:
        return bool(self.config.enabled and self.config.meet_seed_urls)

    def auth_configured(self) -> bool:
        from core.public_hive_bridge import public_hive_has_auth

        return public_hive_has_auth(self.config)

    def write_enabled(self) -> bool:
        from core.public_hive_bridge import public_hive_write_enabled

        return public_hive_write_enabled(self.config)

    def _post_many(
        self,
        route: str,
        *,
        payload: dict[str, Any],
        base_urls: tuple[str, ...],
    ) -> dict[str, Any]:
        return self._client.post_many(route, payload=payload, base_urls=base_urls)

    def _get_json(self, base_url: str, route: str) -> Any:
        return self._with_topic_failover(base_url, lambda candidate: self._client.get_json(candidate, route))

    def _post_json(self, base_url: str, route: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._with_topic_failover(base_url, lambda candidate: self._client.post_json(candidate, route, payload))

    def _auth_token_for_url(self, url: str) -> str | None:
        return self._client.auth_token_for_url(url)

    def _write_grant_for_request(self, base_url: str, route: str) -> dict[str, Any] | None:
        return self._client.write_grant_for_request(base_url, route)

    def _ssl_context_for_url(self, url: str) -> ssl.SSLContext | None:
        return self._client.ssl_context_for_url(url)

    def _with_topic_failover(self, base_url: str, request_fn: Any) -> Any:
        candidates = self._topic_request_base_urls(base_url)
        last_error: Exception | None = None
        for candidate in candidates:
            try:
                result = request_fn(candidate)
            except Exception as exc:
                last_error = exc
                continue
            self._remember_topic_base_url(candidate)
            return result
        if last_error is not None:
            raise last_error
        return request_fn(base_url)

    def _topic_request_base_urls(self, base_url: str) -> tuple[str, ...]:
        requested = _normalize_base_url(base_url)
        topic_target = _normalize_base_url(str(self.config.topic_target_url or "").strip())
        if not requested or requested != topic_target:
            return (str(base_url).rstrip("/"),)

        preferred = _normalize_base_url(str(getattr(self, "_preferred_topic_base_url", "") or "").strip())
        ordered: list[str] = []
        if preferred:
            ordered.append(preferred)
        ordered.append(requested)
        ordered.extend(
            _normalize_base_url(str(url).strip()) for url in list(self.config.meet_seed_urls or ()) if str(url).strip()
        )

        deduped: list[str] = []
        seen: set[str] = set()
        for candidate in ordered:
            if not candidate or candidate in seen:
                continue
            deduped.append(candidate)
            seen.add(candidate)
        return tuple(deduped or [str(base_url).rstrip("/")])

    def _remember_topic_base_url(self, base_url: str) -> None:
        candidate = _normalize_base_url(base_url)
        if not candidate:
            return
        known = {
            _normalize_base_url(str(url).strip())
            for url in list(self.config.meet_seed_urls or ())
            if str(url).strip()
        }
        if candidate in known:
            self._preferred_topic_base_url = candidate
