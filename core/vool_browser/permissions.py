"""Per-origin permissions for a browser session: deny by default, typed grants.

A grant exists ONLY as a typed tool argument (`vool-browser.permission.grant`)
recorded in the session registry. Nothing a page renders can create, widen, or
revoke one — there is no code path from page content to this store, which is
the structural form of the untrusted-evidence law.

Permission kinds:
- navigation   the engine may navigate/redirect to this origin
- downloads    files from this origin may be saved (bounded)
- uploads      staged files may be attached into this origin's forms
- new_tabs     this origin may open additional tabs/windows
"""
from __future__ import annotations

from urllib.parse import urlsplit

PERMISSION_KINDS = ("navigation", "downloads", "uploads", "new_tabs")


class PermissionError_(ValueError):  # noqa: N801 - the trailing underscore deliberately
    # avoids shadowing the builtin PermissionError; renaming it is a public-API change.
    pass


def normalize_origin(value: str) -> str:
    """host:port of an http(s) URL or a host[:port] string; refuses everything else."""

    text = str(value or "").strip()
    if "://" in text:
        parts = urlsplit(text)
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            raise PermissionError_(f"origin must be an http(s) URL or host[:port], got {value!r}")
        host = parts.hostname
        port = parts.port
    else:
        text = text.rstrip("/")
        if "/" in text or not text:
            raise PermissionError_(f"origin must be an http(s) URL or host[:port], got {value!r}")
        host, _, port_text = text.partition(":")
        port = int(port_text) if port_text else None
    if not host:
        raise PermissionError_(f"origin has no host: {value!r}")
    return f"{host}:{port}" if port else host


def origin_of(url: str) -> str:
    parts = urlsplit(str(url or ""))
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        return ""
    return f"{parts.hostname}:{parts.port}" if parts.port else parts.hostname


class GrantStore:
    """The session's grants: {origin: set[kind]}. Everything is denied by default."""

    def __init__(self, seed: dict[str, list[str]] | None = None) -> None:
        self._grants: dict[str, set[str]] = {}
        for origin, kinds in (seed or {}).items():
            self._grants[normalize_origin(origin)] = {k for k in kinds if k in PERMISSION_KINDS}

    def grant(self, origin: str, kind: str) -> str:
        if kind not in PERMISSION_KINDS:
            raise PermissionError_(
                f"unknown permission kind {kind!r}; one of {PERMISSION_KINDS}")
        key = normalize_origin(origin)
        self._grants.setdefault(key, set()).add(kind)
        return key

    def revoke(self, origin: str, kind: str) -> None:
        key = normalize_origin(origin)
        self._grants.get(key, set()).discard(kind)

    def allows(self, origin: str, kind: str) -> bool:
        return kind in self._grants.get(normalize_origin(origin), set())

    def snapshot(self) -> dict[str, list[str]]:
        return {origin: sorted(kinds) for origin, kinds in sorted(self._grants.items())}

    def origins_for(self, kind: str) -> list[str]:
        return sorted(origin for origin, kinds in self._grants.items() if kind in kinds)
