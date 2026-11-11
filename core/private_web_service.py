"""Turning Private web on and off: the setting, and the Tor / VPN helper behind it.

The order is what keeps it fail closed:

* switching a route ON writes the setting first, so from that moment nothing leaves except
  through the route, and only then starts Tor or the VPN helper (in the background: Tor can take
  a minute to connect, and until it has, Private web refuses rather than waits);
* switching OFF writes the setting first, then stops the helper.
"""

from __future__ import annotations

import threading
from typing import Any

from core import private_web

_lock = threading.RLock()
_last_error: dict[str, str] = {}
_starter: threading.Thread | None = None
TOR_USER = "private_web"


def _write_route(kind: str) -> None:
    from core import policy_engine

    policy_engine.set_operator_policy_values({"network.private_web.route": kind})
    private_web.refresh()


def _start_helper(kind: str) -> None:
    """Runs in the background. Records a plain error when the helper cannot start."""
    _last_error.pop(kind, None)
    try:
        if kind == private_web.ROUTE_TOR:
            from core import tor_runtime

            tor_runtime.shared().start(TOR_USER)
        elif kind == private_web.ROUTE_WIREGUARD:
            from core import wireguard_runtime

            wireguard_runtime.shared().start()
    except Exception as exc:
        from core.tor_runtime import TorUnavailable
        from core.wireguard_runtime import WireGuardFileProblem, WireGuardUnavailable

        # Only the helpers' own errors are written for the person; anything else (a disk or
        # permission error) gets one plain sentence rather than its technical text.
        plain = isinstance(exc, (TorUnavailable, WireGuardUnavailable, WireGuardFileProblem))
        _last_error[kind] = (str(exc) if plain else "") or "It could not be started on this computer."
    finally:
        private_web.refresh()


def _stop_helpers(except_kind: str = "") -> None:
    if except_kind != private_web.ROUTE_TOR:
        from core import tor_runtime

        tor_runtime.shared().release(TOR_USER)
    if except_kind != private_web.ROUTE_WIREGUARD:
        from core import wireguard_runtime

        wireguard_runtime.shared().stop()


def set_route(kind: str, *, wait: bool = False) -> dict[str, Any]:
    kind = str(kind or "").strip().lower()
    if kind not in private_web.ROUTES:
        raise ValueError("unknown_route")
    global _starter
    with _lock:
        _write_route(kind)
        _stop_helpers(except_kind=kind)
        if kind in private_web.SOCKS_ROUTES:
            _starter = threading.Thread(target=_start_helper, args=(kind,), name="private-web-start", daemon=True)
            _starter.start()
        elif kind == private_web.ROUTE_SYSTEM_VPN:
            private_web.check_system_vpn_now()
    if wait and _starter is not None:
        _starter.join()
    return status()


def save_vpn_file(text: str) -> dict[str, Any]:
    from core import wireguard_runtime

    profile = wireguard_runtime.save_wireguard_file(text)
    with _lock:
        if private_web.current_route().kind == private_web.ROUTE_WIREGUARD:
            wireguard_runtime.shared().stop()
            threading.Thread(target=_start_helper, args=(private_web.ROUTE_WIREGUARD,), daemon=True).start()
    result = status()
    result["vpn_server"] = profile.endpoint.rsplit(":", 1)[0]
    return result


def forget_vpn_file() -> dict[str, Any]:
    from core import wireguard_runtime

    with _lock:
        wireguard_runtime.shared().stop()
        wireguard_runtime.forget_wireguard_file()
        private_web.refresh()
    return status()


def status() -> dict[str, Any]:
    from core import policy_engine, tor_runtime, wireguard_runtime

    route = private_web.current_route()
    problem = private_web.route_problem(route) if route.active else None
    starting = bool(_starter is not None and _starter.is_alive())
    if not route.active:
        state = "off"
    elif problem is None:
        state = "on"
    elif starting:
        state = "starting"
    else:
        state = "down"
    detail = ""
    if state == "down":
        detail = _last_error.get(route.kind) or problem or ""
    elif state == "starting":
        detail = "Connecting. Until it is ready, nothing goes online."
    return {
        "ok": True,
        "route": route.kind,
        "state": state,
        "detail": detail,
        "vpn_file": wireguard_runtime.stored_file_path().exists(),
        "tor_available": bool(tor_runtime.find_tor_binary()),
        "vpn_helper_available": bool(wireguard_runtime.find_wireproxy_binary()),
        "local_only": bool(policy_engine.local_only_mode()),
    }


def activate_at_start() -> None:
    """At app start: bring the chosen route's helper up in the background. Off does nothing."""
    route = private_web.current_route()
    if route.kind in private_web.SOCKS_ROUTES:
        global _starter
        _starter = threading.Thread(target=_start_helper, args=(route.kind,), name="private-web-start", daemon=True)
        _starter.start()


__all__ = ["activate_at_start", "forget_vpn_file", "save_vpn_file", "set_route", "status"]
