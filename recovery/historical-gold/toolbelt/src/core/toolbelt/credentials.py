"""Credential discovery — handles only, never values.

Discovery sources, in order of trust:
* ``core.credential_store`` (keychain/vault): existence + label metadata only. The store's own
  API already refuses to return values through ``list_credentials``; we only call
  ``has_credential``/``list_credentials`` and NEVER ``get_credential``.
* ``gh auth status``: provider-confirmed identity/scopes. Parsed for safe fields only.
"""
from __future__ import annotations

import re

from core.toolbelt.models import AuthState, CredentialHandle
from core.toolbelt.probes import ProbeRunner

_GH_ACCOUNT = re.compile(r"Logged in to (\S+) account (\S+)")
_GH_SCOPES = re.compile(r"Token scopes?: (.+)")


def _store_source() -> str | None:
    try:
        from core import credential_store

        mode = credential_store._mode()
        if mode == "auto":
            return "keychain" if credential_store._active_keyring() is not None else "vault"
        return mode if mode in {"keychain", "vault"} else "unknown"
    except Exception:
        return None


def platform_credential_handles(names: list[str]) -> list[CredentialHandle]:
    """Existence-only handles for named credentials in the VOOL platform store."""
    source = _store_source()
    if source is None:
        return []
    out: list[CredentialHandle] = []
    try:
        from core import credential_store

        for name in names:
            exists = bool(credential_store.has_credential(name))
            out.append(CredentialHandle(
                credential_id=name,
                provider=name.split(":", 1)[0] if ":" in name else name,
                source=source,
                status=AuthState.AUTHENTICATED if exists else AuthState.UNKNOWN,
            ))
    except Exception:
        return []
    return [h for h in out if h.status is not AuthState.UNKNOWN]


def gh_credential_handle(runner: ProbeRunner) -> CredentialHandle | None:
    """Parse ``gh auth status`` into a handle. Token lines are never stored at all."""
    result = runner.run(["gh", "auth", "status"])
    text = result.stdout + "\n" + result.stderr
    m = _GH_ACCOUNT.search(text)
    if not m:
        return CredentialHandle(credential_id="github:cli", provider="github",
                                source="gh-store", status=AuthState.UNAUTHENTICATED)
    host, account = m.group(1), m.group(2)
    scopes_m = _GH_SCOPES.search(text)
    scopes = tuple(
        s.strip().strip("'\"") for s in scopes_m.group(1).split(",") if s.strip()
    ) if scopes_m else ()
    return CredentialHandle(
        credential_id=f"github:{account}", provider="github", source="gh-store",
        status=AuthState.AUTHENTICATED, identity=f"{account}@{host}", scopes=scopes,
    )


__all__ = ["gh_credential_handle", "platform_credential_handles"]
