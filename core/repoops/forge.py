"""RepoOps' bridge to the KAS forge boundary.

RepoOps never learns which forge it is talking to. It asks for a `ForgeAdapter` by provider id
and gets the one shared contract back; GitHub and GitLab differences stop inside
`core.kas.adapters`. Nothing here re-decides permission, effect or privacy — building the
adapter builds a VOOL transport, and that transport is where those decisions already live.

The credential is named, never held: a session carries a binding id, the transport resolves it,
and no secret value passes through this module or reaches the session journal.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

from core.kas.contract import ForgeAdapter, TransportDeniedError, TransportUnknownError

#: Test seam. A hermetic remote fixture installs a recorded transport here so a test drives the
#: REAL adapter, the REAL contract and the REAL runtime with no socket. Production never sets it,
#: and setting it cannot widen what an adapter may do: the adapter still holds exactly one
#: callable and no other reach.
_TRANSPORT_FACTORY: Callable[..., Any] | None = None


def install_transport_factory(factory: Callable[..., Any] | None) -> None:
    global _TRANSPORT_FACTORY
    _TRANSPORT_FACTORY = factory


def transport_factory() -> Callable[..., Any] | None:
    return _TRANSPORT_FACTORY


def _remote_hostname(url: str) -> str:
    """The host a remote URL names: scheme forms (https://, ssh://, git://), SCP-style git
    remotes (git@host:path) and bare host/path shapes. A host that merely embeds a
    provider's name (github.com.evil.com) names ITSELF, not that provider."""

    text = str(url or "").strip()
    if not text:
        return ""
    if "://" in text:
        return str(urlsplit(text).hostname or "").strip().lower()
    head = text.split("/", 1)[0]
    if "@" in head:
        head = head.rsplit("@", 1)[1]
    return head.split(":", 1)[0].strip().lower()


def _provider_hosts(provider: str) -> tuple[str, ...]:
    """The hosts that name `provider`: its public hosts, plus the owner's base-URL override
    host when one is configured — the same override law ``core.kas.registry`` pins the
    adapter's transport with, read on the VOOL side of the boundary."""

    import os

    hosts = {"github": ("github.com", "www.github.com"), "gitlab": ("gitlab.com", "www.gitlab.com")}[provider]
    override = str(os.environ.get(f"VOOL_FORGE_BASE_URL_{provider.upper()}") or "").strip()
    if override:
        host = str(urlsplit(override).hostname or "").strip().lower()
        if host:
            hosts += (host,)
    return hosts


def _remote_path(url: str) -> str:
    """The path component of a remote URL — scheme forms parsed, bare host/path split at the
    first slash. A ``/gitlab`` marker in HERE names a path-hosted GitLab; the same marker
    found in the raw text would also fire for a host like ``gitlab.com.evil.attacker.test``,
    because ``://gitlab`` already contains it."""

    text = str(url or "").strip()
    if "://" in text:
        return str(urlsplit(text).path or "")
    if "/" in text:
        return "/" + text.split("/", 1)[1]
    return ""


def provider_for_remote(url: str) -> str:
    """Which forge a remote URL names. Empty when it names none we have an adapter for.

    The match is on the HOST the remote names, exactly. A substring match would classify
    github.com.evil.com as GitHub and mygitlab.com as GitLab, pointing an
    operator-authorized forge action at the wrong real forge; the transport's host pinning
    keeps such a mislabel from ever reaching an arbitrary host, and this exact match keeps
    it from reaching the wrong pinned one. The supported self-hosted lanes stay open: the
    owner's base-URL override host classifies, and a ``/gitlab`` path still selects GitLab
    for path-hosted instances."""

    text = str(url or "").strip()
    if not text:
        return ""
    host = _remote_hostname(text)
    if host:
        if host in _provider_hosts("github"):
            return "github"
        if host in _provider_hosts("gitlab"):
            return "gitlab"
    if "/gitlab" in _remote_path(text).lower():
        return "gitlab"
    return ""


def namespace_for_remote(url: str) -> str:
    """`owner/repo` (or the GitLab project path) a remote URL names."""

    text = str(url or "").strip()
    if not text:
        return ""
    if text.endswith(".git"):
        text = text[: -len(".git")]
    if text.startswith("git@"):
        _, _, tail = text.partition(":")
        return tail.strip("/")
    for marker in ("://",):
        if marker in text:
            _, _, tail = text.partition(marker)
            _, _, path = tail.partition("/")
            return path.strip("/")
    return text.strip("/")


def open_forge(
    *,
    provider: str,
    namespace: str,
    auth_binding: str = "",
    base_url: str = "",
    source_context: dict[str, Any] | None = None,
) -> ForgeAdapter:
    from core.kas.registry import forge_adapter

    return forge_adapter(
        provider,
        namespace=namespace,
        base_url=base_url,
        auth_binding=auth_binding,
        source_context=source_context,
        transport_factory=_TRANSPORT_FACTORY,
    )


__all__ = [
    "TransportDeniedError",
    "TransportUnknownError",
    "install_transport_factory",
    "namespace_for_remote",
    "open_forge",
    "provider_for_remote",
    "transport_factory",
]
