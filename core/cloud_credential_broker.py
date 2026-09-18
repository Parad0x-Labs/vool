from __future__ import annotations

import os
import threading


class CloudCredentialBroker:
    """Resolve BYOK credentials without exposing them to routing or persistence layers."""

    def __init__(self) -> None:
        self._session: dict[str, str] = {}
        self._lock = threading.RLock()

    def set_session_credential(self, name: str, value: str) -> None:
        key = str(name or "").strip()
        secret = str(value or "")
        if not key or not secret:
            raise ValueError("credential name and value are required")
        with self._lock:
            self._session[key] = secret

    def resolve(self, name: str, *, env_name: str = "") -> str | None:
        key = str(name or "").strip()
        with self._lock:
            if key and self._session.get(key):
                return self._session[key]
        environment_key = str(env_name or "").strip()
        if environment_key and os.getenv(environment_key):
            return str(os.getenv(environment_key) or "")
        if not key:
            return None
        try:
            from core import credential_store

            return credential_store.get_credential(key)
        except Exception:
            return None

    def has(self, name: str, *, env_name: str = "") -> bool:
        return bool(self.resolve(name, env_name=env_name))

    def clear_session_credential(self, name: str) -> bool:
        with self._lock:
            return self._session.pop(str(name or "").strip(), None) is not None

    def clear_all_session_credentials(self) -> None:
        with self._lock:
            self._session.clear()

    def revoke_persisted_credential(self, name: str) -> bool:
        self.clear_session_credential(name)
        try:
            from core import credential_store

            return credential_store.delete_credential(str(name or "").strip())
        except Exception:
            return False


__all__ = ["CloudCredentialBroker"]
