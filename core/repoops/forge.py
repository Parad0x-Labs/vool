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
    provider's name (github.com.evil.com) names ITSELF, not that provider. Malformed input
    (e.g. an unclosed IPv6 bracket) names no host — the caller refuses it, never raises."""

    text = str(url or "").strip()
    if not text:
        return ""
    if "://" in text:
        try:
            return str(urlsplit(text).hostname or "").strip().lower()
        except ValueError:
            return ""
    head = text.split("/", 1)[0]
    if "@" in head:
        head = head.rsplit("@", 1)[1]
    return head.split(":", 1)[0].strip().lower()


def _provider_hosts(provider: str) -> tuple[str, ...]:
    """The hosts that name `provider`: its public hosts, plus the owner's base-URL override
    host when one is configured — the same override law ``core.kas.registry`` pins the
    adapter's transport with, read on the VOOL side of the boundary. This is the ONLY
    self-hosted authority: a remote classifies as GitLab when its HOST is exactly one of
    these, never because of path text."""

    import os

    hosts = {"github": ("github.com", "www.github.com"), "gitlab": ("gitlab.com", "www.gitlab.com")}[provider]
    override = str(os.environ.get(f"VOOL_FORGE_BASE_URL_{provider.upper()}") or "").strip()
    if override:
        host = _remote_hostname(override)
        if host:
            hosts += (host,)
    return hosts


def provider_for_remote(url: str) -> str:
    """Which forge a remote URL names. Empty when it names none we have an adapter for.

    The match is on the HOST the remote names, exactly, against the provider's public hosts
    plus the owner's configured override host. A substring match (in the host or anywhere in
    the path) would classify github.com.evil.com, mygitlab.com or
    unrelated.example/gitlab-mirror/… as a real provider and point an operator-authorized
    forge action at the WRONG real pinned forge — the transport's host pinning keeps such a
    mislabel from reaching an arbitrary host, but pinning cannot make the choice of provider
    correct. Self-hosted and path-hosted instances classify through their configured
    authority: set VOOL_FORGE_BASE_URL_GITLAB (or _GITHUB) to the instance's origin, and
    remotes on exactly that host — any path layout — classify. Malformed remotes name no
    host and classify nothing; callers turn that into their own refusal."""

    text = str(url or "").strip()
    if not text:
        return ""
    host = _remote_hostname(text)
    if not host:
        return ""
    if host in _provider_hosts("github"):
        return "github"
    if host in _provider_hosts("gitlab"):
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
