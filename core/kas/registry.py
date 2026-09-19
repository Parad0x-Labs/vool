"""Which adapters exist, and the one way to build one.

An adapter is never constructed by its caller. `forge_adapter` builds the transport in VOOL and
injects it, so there is no code path that produces an adapter holding an egress VOOL did not
make. That is the runtime half of the law :mod:`core.kas.conformance` checks statically.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from core.kas.contract import AdapterConfig, CalendarAdapter, ExternalAdapter, ForgeAdapter

_ADAPTERS: dict[tuple[str, str], type[ExternalAdapter]] = {}

#: The hosts each provider is allowed to reach when no explicit base_url is configured. Pinned
#: in VOOL, not in the adapter: an adapter that computes some other URL is refused by the
#: transport rather than trusted.
DEFAULT_HOSTS: dict[str, tuple[str, ...]] = {
    "github": ("api.github.com", "github.com", "codeload.github.com"),
    "gitlab": ("gitlab.com",),
    # Calendar providers keyed by their CALENDAR-family provider ids.
    "google": ("www.googleapis.com",),
    "graph": ("graph.microsoft.com",),
}

DEFAULT_BASE_URLS: dict[str, str] = {
    "github": "https://api.github.com",
    "gitlab": "https://gitlab.com/api/v4",
    "google": "https://www.googleapis.com/calendar/v3",
    "graph": "https://graph.microsoft.com/v1.0",
}


def _owner_base_url_override(provider_id: str) -> str:
    """An owner-set base URL for a self-hosted forge (GitHub Enterprise, gitlab.self-hosted).

    Read HERE, on the VOOL side of the boundary -- adapters stay environment-blind by law.
    The override widens nothing by itself: `forge_adapter` still pins the adapter to the
    resolved base's host, and nothing but an explicit owner environment variable sets it."""
    import os

    key = f"VOOL_FORGE_BASE_URL_{str(provider_id or '').strip().upper()}"
    return str(os.environ.get(key) or "").strip()


def register_adapter(cls: type[ExternalAdapter]) -> type[ExternalAdapter]:
    kind = str(getattr(cls, "kind", "") or "").strip()
    provider = str(getattr(cls, "provider_id", "") or "").strip()
    if not kind or not provider:
        raise ValueError("an adapter must declare both `kind` and `provider_id`")
    _ADAPTERS[(kind, provider)] = cls
    return cls


def registered_adapters() -> dict[tuple[str, str], type[ExternalAdapter]]:
    _load_builtin_adapters()
    return dict(_ADAPTERS)


def _load_builtin_adapters() -> None:
    from core.kas.adapters import caldav as _caldav  # noqa: F401
    from core.kas.adapters import eventkit_mac as _eventkit_mac  # noqa: F401
    from core.kas.adapters import github as _github  # noqa: F401
    from core.kas.adapters import gitlab as _gitlab  # noqa: F401
    from core.kas.adapters import google_calendar as _google_calendar  # noqa: F401
    from core.kas.adapters import graph_calendar as _graph_calendar  # noqa: F401


def _allowed_hosts(provider_id: str, base_url: str) -> tuple[str, ...]:
    from urllib.parse import urlsplit

    hosts = set(DEFAULT_HOSTS.get(provider_id, ()))
    host = str(urlsplit(str(base_url or "")).hostname or "").strip().lower()
    if host:
        hosts.add(host)
    return tuple(sorted(hosts))


def forge_adapter(
    provider_id: str,
    *,
    namespace: str,
    base_url: str = "",
    auth_binding: str = "",
    source_context: dict[str, Any] | None = None,
    transport_factory: Callable[..., Any] | None = None,
    options: dict[str, str] | None = None,
) -> ForgeAdapter:
    """Build a forge adapter with a VOOL-built transport bound to it.

    ``transport_factory`` exists for hermetic tests: a fixture supplies a recorded transport so
    a test drives the REAL adapter and the REAL runtime without a socket. It never widens what
    an adapter may do — the adapter still has exactly one callable and no other reach.
    """

    _load_builtin_adapters()
    provider = str(provider_id or "").strip().lower()
    cls = _ADAPTERS.get(("forge", provider))
    if cls is None:
        known = sorted(p for (k, p) in _ADAPTERS if k == "forge")
        raise LookupError(f"no forge adapter for `{provider}`; known: {', '.join(known) or 'none'}")
    resolved_base = str(base_url or _owner_base_url_override(provider) or DEFAULT_BASE_URLS.get(provider, "")).strip()
    if not resolved_base:
        raise ValueError(f"forge adapter `{provider}` has no base_url and no default")
    config = AdapterConfig(
        provider_id=provider,
        base_url=resolved_base.rstrip("/"),
        namespace=str(namespace or "").strip(),
        auth_binding=str(auth_binding or "").strip(),
        options=dict(options or {}),
    )
    if transport_factory is not None:
        transport = transport_factory(config=config, source_context=source_context)
    else:
        from core.kas.transport import build_transport

        transport = build_transport(
            source_context=source_context,
            provider_id=provider,
            allowed_hosts=_allowed_hosts(provider, resolved_base),
        )
    adapter = cls(transport=transport, config=config)
    if not isinstance(adapter, ForgeAdapter):
        raise TypeError(f"`{provider}` is registered under kind `forge` but is not a ForgeAdapter")
    return adapter


def calendar_adapter(
    provider_id: str,
    *,
    base_url: str,
    auth_binding: str = "",
    source_context: dict[str, Any] | None = None,
    transport_factory: Callable[..., Any] | None = None,
    options: dict[str, str] | None = None,
) -> CalendarAdapter:
    """Build a calendar adapter the same single way a forge adapter is built.

    A calendar provider has no default host: unlike the forges, every CalDAV deployment (and
    every tenant of it) lives at an operator-named URL, so ``base_url`` is REQUIRED and the
    transport pins its host exactly as it pins api.github.com for the forge family.
    ``transport_factory`` keeps hermetic-recorded-transport tests driving the real adapter
    without a socket, identical to the forge lane.
    """

    _load_builtin_adapters()
    provider = str(provider_id or "").strip().lower()
    cls = _ADAPTERS.get(("calendar", provider))
    if cls is None:
        known = sorted(p for (k, p) in _ADAPTERS if k == "calendar")
        raise LookupError(f"no calendar adapter for `{provider}`; known: {', '.join(known) or 'none'}")
    resolved_base = str(base_url or DEFAULT_BASE_URLS.get(provider, "")).strip()
    if not resolved_base:
        raise ValueError(f"calendar adapter `{provider}` requires an explicit base_url")
    config = AdapterConfig(
        provider_id=provider,
        base_url=resolved_base.rstrip("/"),
        namespace="",
        auth_binding=str(auth_binding or "").strip(),
        options=dict(options or {}),
    )
    if transport_factory is not None:
        transport = transport_factory(config=config, source_context=source_context)
    else:
        from core.kas.transport import build_transport

        transport = build_transport(
            source_context=source_context,
            provider_id=provider,
            allowed_hosts=_allowed_hosts(provider, resolved_base),
        )
    adapter = cls(transport=transport, config=config)
    if not isinstance(adapter, CalendarAdapter):
        raise TypeError(f"`{provider}` is registered under kind `calendar` but is not a CalendarAdapter")
    return adapter


__all__ = ["DEFAULT_BASE_URLS", "DEFAULT_HOSTS", "calendar_adapter", "forge_adapter", "register_adapter", "registered_adapters"]
