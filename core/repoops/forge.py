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


def provider_for_remote(url: str) -> str:
    """Which forge a remote URL names. Empty when it names none we have an adapter for."""

    text = str(url or "").strip().lower()
    if not text:
        return ""
    if "github.com" in text:
        return "github"
    if "gitlab.com" in text or "/gitlab" in text:
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
