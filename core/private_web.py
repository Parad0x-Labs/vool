"""Private web: send VOOL's web traffic through Tor or the person's VPN, and never around it.

The setting is ``network.private_web.route`` in the operator policy:

* ``off``         -- today's behaviour, unchanged (the default; nothing here runs).
* ``tor``         -- every web request goes through the local Tor SOCKS port.
* ``wireguard``   -- every web request goes through the local SOCKS port of a user-space
                     WireGuard tunnel built from the person's own config file.
* ``system_vpn``  -- traffic leaves directly, through a VPN app the person runs themselves;
                     nothing public may leave until VOOL has checked that the VPN is up.

FAIL CLOSED is the whole point. Two layers hold it:

1. The fetch door (`core.remote_fetch_policy.open_remote`) asks `route_problem()` before every
   request and refuses with a recorded denial when the route is down. When the route is up it
   opens the request through `socks_http_handlers`, so the connection is made BY NAME at the far
   end (no DNS question leaves this machine) and there is no direct path to fall back to.
2. `install_egress_guard()` adds a process audit hook. While a route is on, any socket connect
   or DNS lookup that would leave this machine directly -- from any library, the door or not --
   raises `PrivateWebBlocked` before it happens. That is what covers code that never used the
   door (cloud model calls, email, update checks), and code added later.

Loopback and LAN addresses stay reachable: they never reach the internet, and local models and
the person's own services live there.
"""

from __future__ import annotations

import contextlib
import hashlib
import http.client
import ipaddress
import os
import re
import shlex
import socket
import sys
import threading
import time
import urllib.request
from dataclasses import dataclass
from typing import Any

ROUTE_OFF = "off"
ROUTE_TOR = "tor"
ROUTE_WIREGUARD = "wireguard"
ROUTE_SYSTEM_VPN = "system_vpn"
ROUTES: tuple[str, ...] = (ROUTE_OFF, ROUTE_TOR, ROUTE_WIREGUARD, ROUTE_SYSTEM_VPN)
SOCKS_ROUTES = frozenset({ROUTE_TOR, ROUTE_WIREGUARD})

#: The SOCKS endpoint is always on this machine. A route can never point traffic at a remote
#: proxy by configuration: the remote end of a route is Tor's or the WireGuard tunnel's business.
SOCKS_HOST = "127.0.0.1"

_ROUTE_NAMES = {
    ROUTE_TOR: "Tor",
    ROUTE_WIREGUARD: "your VPN",
    ROUTE_SYSTEM_VPN: "your VPN",
}

#: A successful SOCKS greeting is trusted for this long before the door probes again. Short on
#: purpose: a stale "up" can at worst cost one failed request, never a direct one, because the
#: socket itself still goes through the SOCKS port.
_PROBE_TTL_SECONDS = 3.0
_PROBE_TIMEOUT_SECONDS = 1.5


class PrivateWebBlocked(ConnectionRefusedError):
    """A direct connection or DNS lookup was stopped because Private web is on.

    A ConnectionRefusedError on purpose: every caller that already handles "could not connect"
    (requests, urllib, asyncio) handles this the same way, so a blocked call fails like an
    unreachable host instead of crashing a turn.
    """


class SocksError(OSError):
    """The local SOCKS endpoint refused or broke the connection."""


@dataclass(frozen=True)
class RouteState:
    kind: str = ROUTE_OFF
    socks_port: int = 0

    @property
    def active(self) -> bool:
        return self.kind != ROUTE_OFF

    @property
    def uses_socks(self) -> bool:
        return self.kind in SOCKS_ROUTES

    @property
    def endpoint(self) -> tuple[str, int]:
        return (SOCKS_HOST, int(self.socks_port))

    @property
    def label(self) -> str:
        return _ROUTE_NAMES.get(self.kind, "")


_OFF = RouteState()
_state: RouteState = _OFF
_state_lock = threading.Lock()
_probe_ok_until = 0.0
_probe_endpoint: tuple[str, int] | None = None
_system_vpn_verified_until = 0.0


def _route_from_policy() -> RouteState:
    from core import policy_engine

    kind = str(policy_engine.get("network.private_web.route", ROUTE_OFF) or ROUTE_OFF).strip().lower()
    if kind not in ROUTES:
        # An unknown value is a setting VOOL cannot honour. Treating it as "off" would send the
        # person's traffic out directly while they believe it is private, so it fails closed as
        # a route that is never up.
        return RouteState(kind="unknown")
    try:
        port = int(policy_engine.get("network.private_web.socks_port", 0) or 0)
    except (TypeError, ValueError):
        port = 0
    if not 0 <= port <= 65535:
        port = 0
    if kind == ROUTE_TOR:
        # VOOL's own Tor (core/tor_runtime.py) picks its port at start. While it is not running
        # the port is 0 and the route reports "not running" -- never a stale configured port.
        try:
            from core import tor_runtime

            port = tor_runtime.shared().socks_port
        except Exception:
            port = 0
    elif kind == ROUTE_WIREGUARD:
        try:
            from core import wireguard_runtime

            port = wireguard_runtime.shared().socks_port
        except Exception:
            port = 0
    return RouteState(kind=kind, socks_port=port)


def refresh() -> RouteState:
    """Re-read the setting and publish it to the guard. Returns the state now in force."""
    global _state
    state = _route_from_policy()
    with _state_lock:
        _state = state
    _ensure_vpn_watch(state)
    return state


def current_route() -> RouteState:
    """The route in force, re-read from the setting (cheap: the policy load is cached)."""
    return refresh()


def set_route_for_process(state: RouteState) -> None:
    """Pin the route for a process that has no policy of its own (the provider worker)."""
    global _state
    with _state_lock:
        _state = state


def mark_system_vpn_verified(seconds: float) -> None:
    """Record that the VPN check passed; public traffic may leave directly for `seconds`."""
    global _system_vpn_verified_until
    _system_vpn_verified_until = time.monotonic() + max(0.0, float(seconds))


def clear_system_vpn_verified() -> None:
    global _system_vpn_verified_until
    _system_vpn_verified_until = 0.0


def _system_vpn_verified() -> bool:
    return time.monotonic() < _system_vpn_verified_until


#: How long one passed check lets traffic leave directly, and how often the watcher re-checks.
#: Short on purpose: after the VPN drops, direct traffic stops within this window.
_SYSTEM_VPN_VALID_SECONDS = 4.0
_SYSTEM_VPN_RECHECK_SECONDS = 2.0
_vpn_watch: threading.Thread | None = None


def check_system_vpn_now() -> bool:
    """Ask the computer whether its VPN carries traffic now; record the answer for the guard."""
    try:
        from core.system_vpn_check import system_vpn_active

        active = system_vpn_active()
    except Exception:
        active = False
    if active:
        mark_system_vpn_verified(_SYSTEM_VPN_VALID_SECONDS)
    else:
        clear_system_vpn_verified()
    return active


def _watch_system_vpn() -> None:
    while _state.kind == ROUTE_SYSTEM_VPN:
        check_system_vpn_now()
        time.sleep(_SYSTEM_VPN_RECHECK_SECONDS)
    clear_system_vpn_verified()


def _ensure_vpn_watch(state: RouteState) -> None:
    """While "My system VPN" is chosen, keep re-checking it, so code outside the fetch door
    (cloud model calls, email) is allowed out only while the VPN is really on."""
    global _vpn_watch
    if state.kind != ROUTE_SYSTEM_VPN:
        return
    with _state_lock:
        if _vpn_watch is not None and _vpn_watch.is_alive():
            return
        _vpn_watch = threading.Thread(target=_watch_system_vpn, name="private-web-vpn-watch", daemon=True)
        _vpn_watch.start()


# --- SOCKS5 (RFC 1928 / 1929), connect only -------------------------------------------------


def _recv_exact(sock: socket.socket, size: int) -> bytes:
    chunks = b""
    while len(chunks) < size:
        part = sock.recv(size - len(chunks))
        if not part:
            raise SocksError("the private route closed the connection")
        chunks += part
    return chunks


_SOCKS_REPLIES = {
    1: "general failure",
    2: "connection not allowed",
    3: "network unreachable",
    4: "host unreachable",
    5: "connection refused",
    6: "timed out",
    7: "command not supported",
    8: "address type not supported",
}


def _greet(sock: socket.socket, isolation: str) -> None:
    methods = b"\x00\x02" if isolation else b"\x00"
    sock.sendall(b"\x05" + bytes([len(methods)]) + methods)
    version, method = _recv_exact(sock, 2)
    if version != 5:
        raise SocksError("the private route did not answer as SOCKS5")
    if method == 0x02:
        # Tor isolates circuits by SOCKS credentials (IsolateSOCKSAuth is on by default), so a
        # different user name per chat means different chats never share an exit.
        user = (isolation or "vool").encode("utf-8")[:255]
        sock.sendall(b"\x01" + bytes([len(user)]) + user + b"\x01" + b"x")
        _ver, status = _recv_exact(sock, 2)
        if status != 0:
            raise SocksError("the private route refused VOOL's sign-in")
    elif method != 0x00:
        raise SocksError("the private route offered no usable sign-in method")


def socks5_connect(
    endpoint: tuple[str, int],
    host: str,
    port: int,
    *,
    timeout: Any = socket._GLOBAL_DEFAULT_TIMEOUT,
    isolation: str = "",
) -> socket.socket:
    """Open a TCP stream to (host, port) through the SOCKS5 endpoint.

    Names are sent as names (address type 3), so the lookup happens at the far end and no DNS
    question for the site ever leaves this machine.
    """
    sock = socket.create_connection(endpoint, timeout=timeout)
    try:
        _greet(sock, isolation)
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            name = host.rstrip(".").encode("idna")
            if not name or len(name) > 255:
                raise SocksError("the address is not a valid host name")
            address = b"\x03" + bytes([len(name)]) + name
        else:
            address = (b"\x01" if ip.version == 4 else b"\x04") + ip.packed
        sock.sendall(b"\x05\x01\x00" + address + int(port).to_bytes(2, "big"))
        version, reply, _rsv, atyp = _recv_exact(sock, 4)
        if version != 5:
            raise SocksError("the private route did not answer as SOCKS5")
        if reply != 0:
            raise SocksError(f"the private route could not reach the site ({_SOCKS_REPLIES.get(reply, reply)})")
        if atyp == 1:
            _recv_exact(sock, 4 + 2)
        elif atyp == 4:
            _recv_exact(sock, 16 + 2)
        elif atyp == 3:
            (length,) = _recv_exact(sock, 1)
            _recv_exact(sock, length + 2)
        else:
            raise SocksError("the private route sent an unknown address type")
        return sock
    except BaseException:
        sock.close()
        raise


def probe_socks(endpoint: tuple[str, int], *, timeout: float = _PROBE_TIMEOUT_SECONDS) -> bool:
    """True when something on the endpoint answers a SOCKS5 greeting."""
    try:
        with socket.create_connection(endpoint, timeout=timeout) as sock:
            sock.settimeout(timeout)
            sock.sendall(b"\x05\x02\x00\x02")
            version, method = _recv_exact(sock, 2)
            return version == 5 and method in (0x00, 0x02)
    except OSError:
        return False


def route_problem(state: RouteState | None = None) -> str | None:
    """None when the route can carry traffic; otherwise a plain sentence saying why not."""
    global _probe_ok_until, _probe_endpoint
    state = state if state is not None else current_route()
    if not state.active:
        return None
    if state.kind not in ROUTES:
        return "Private web is set to a route VOOL does not know, so nothing was sent."
    if state.kind == ROUTE_SYSTEM_VPN:
        if _system_vpn_verified() or check_system_vpn_now():
            return None
        return "Private web could not confirm your VPN is on, so nothing was sent."
    if not state.socks_port:
        return f"Private web through {state.label} is not running, so nothing was sent."
    if state.kind == ROUTE_TOR and _tor_owns(state):
        from core import tor_runtime

        if not tor_runtime.shared().bootstrapped():
            return "Private web through Tor is still connecting, so nothing was sent yet."
    if state.kind == ROUTE_WIREGUARD and _wireguard_owns(state):
        # A running helper is not enough: the tunnel itself must carry a connection.
        from core import wireguard_runtime

        if wireguard_runtime.shared().tunnel_alive():
            return None
        return "Private web through your VPN is down, so nothing was sent."
    now = time.monotonic()
    if _probe_endpoint == state.endpoint and now < _probe_ok_until:
        return None
    if probe_socks(state.endpoint):
        _probe_endpoint = state.endpoint
        _probe_ok_until = now + _PROBE_TTL_SECONDS
        return None
    _probe_ok_until = 0.0
    return f"Private web through {state.label} is down, so nothing was sent."


def resolved_at_far_end(host: str) -> bool:
    """True when `host` is a name that Tor or the WireGuard tunnel will look up, not this machine.

    Callers that look a name up first to check it is public skip that lookup then (it would be a
    DNS question leaving this machine, and the guard stops it). The far end cannot reach this
    machine's own network, which is what that check protected. Address literals are not names:
    callers keep checking those exactly as before.
    """
    name = str(host or "").strip().rstrip(".")
    if not name or _is_ip_literal(name) or _name_stays_local(name):
        return False
    return current_route().uses_socks


def url_stays_local(url: str) -> bool:
    """True when the URL names this machine or its local network (no route needed or wanted)."""
    import urllib.parse

    try:
        host = urllib.parse.urlsplit(str(url or "")).hostname or ""
    except ValueError:
        return False
    host = host.strip().rstrip(".")
    if not host:
        return False
    if _is_ip_literal(host):
        return _ip_stays_local(host)
    return _name_stays_local(host)


def _tor_owns(state: RouteState) -> bool:
    try:
        from core import tor_runtime

        return tor_runtime.shared().socks_port == state.socks_port > 0
    except Exception:
        return False


def _wireguard_owns(state: RouteState) -> bool:
    try:
        from core import wireguard_runtime

        return wireguard_runtime.shared().socks_port == state.socks_port > 0
    except Exception:
        return False


def isolation_key(session_id: str) -> str:
    """A stable, non-identifying SOCKS user name for one chat."""
    session = str(session_id or "").strip()
    if not session:
        return "vool"
    return "vool-" + hashlib.sha256(session.encode("utf-8")).hexdigest()[:16]


def socks_http_handlers(state: RouteState, *, context: Any = None, isolation: str = "") -> list[Any]:
    """urllib handlers whose sockets are SOCKS streams to the route's endpoint.

    The URL's host name stays on the connection for Host, TLS SNI and certificate checks; only
    how the socket is made changes (the same shape as `core.pinned_http`).
    """
    if not state.uses_socks or not state.socks_port:
        raise ValueError("socks handlers need a running SOCKS route")
    endpoint = state.endpoint

    def connect(address, timeout=socket._GLOBAL_DEFAULT_TIMEOUT, source_address=None):
        host, port = address
        return socks5_connect(endpoint, str(host), int(port), timeout=timeout, isolation=isolation)

    class PrivateHTTPConnection(http.client.HTTPConnection):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._create_connection = connect

    class PrivateHTTPSConnection(http.client.HTTPSConnection):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._create_connection = connect

    class HTTPHandler(urllib.request.HTTPHandler):
        def http_open(self, request):
            return self.do_open(PrivateHTTPConnection, request)

    class HTTPSHandler(urllib.request.HTTPSHandler):
        def https_open(self, request):
            return self.do_open(PrivateHTTPSConnection, request, context=self._context)

    return [HTTPHandler(), HTTPSHandler(context=context)]


# --- Browser engines (separate processes the guard cannot see) ------------------------------


class BrowserRoute:
    """How to launch a Chromium-family engine under the route in force.

    `problem` set means: do not launch, say this instead. Otherwise `flags` go on the command
    line and `playwright_proxy` (when not None) is Playwright's `proxy=` argument.
    """

    def __init__(self, *, problem: str | None = None, flags: tuple[str, ...] = (), playwright_proxy: dict[str, str] | None = None) -> None:
        self.problem = problem
        self.flags = flags
        self.playwright_proxy = playwright_proxy


def browser_route(state: RouteState | None = None) -> BrowserRoute:
    state = state if state is not None else current_route()
    if not state.active:
        return BrowserRoute()
    problem = route_problem(state)
    if problem:
        return BrowserRoute(problem=problem)
    if not state.uses_socks:
        return BrowserRoute()  # a checked system VPN carries the engine's traffic itself
    server = f"socks5://{SOCKS_HOST}:{int(state.socks_port)}"
    flags = (
        f"--proxy-server={server}",
        # Chromium sends names to a SOCKS5 proxy, but its own prefetch and helper lookups do not
        # go through it. Answering every name "not found" locally (except the proxy itself) means
        # no DNS question can leave this machine from the engine.
        f"--host-resolver-rules=MAP * ~NOTFOUND , EXCLUDE {SOCKS_HOST}",
        "--disable-quic",
        "--proxy-bypass-list=<-loopback>",
    )
    return BrowserRoute(flags=flags, playwright_proxy={"server": server})


# --- The guard ------------------------------------------------------------------------------


#: Addresses that never leave this machine or its own network. Named explicitly: Python's
#: `is_private` also covers documentation and benchmarking ranges, which are not "local".
_LOCAL_NETWORKS = tuple(
    ipaddress.ip_network(net)
    for net in (
        "127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16",
        "::1/128", "fc00::/7", "fe80::/10",
    )
)


def _ip_stays_local(text: str) -> bool:
    try:
        ip = ipaddress.ip_address(str(text).split("%", 1)[0])
    except ValueError:
        return False
    if ip.version == 6 and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return any(ip in net for net in _LOCAL_NETWORKS if net.version == ip.version)


def _is_ip_literal(text: str) -> bool:
    try:
        ipaddress.ip_address(str(text).split("%", 1)[0])
    except ValueError:
        return False
    return True


def _name_stays_local(host: Any) -> bool:
    if host is None:
        return True
    if isinstance(host, (bytes, bytearray)):
        host = bytes(host).decode("idna", "replace")
    name = str(host).strip().rstrip(".").lower()
    if not name or name == "localhost" or name.endswith(".localhost") or name.endswith(".local"):
        return True
    if _is_ip_literal(name):
        # A literal is not a DNS question; whether it may be CONNECTED to is the connect
        # event's decision.
        return True
    with contextlib.suppress(OSError):
        if name == socket.gethostname().strip().rstrip(".").lower():
            return True
    return False


def _guard_allows_public() -> bool:
    state = _state
    if not state.active:
        return True
    return state.kind == ROUTE_SYSTEM_VPN and _system_vpn_verified()


#: Programs that go online by themselves. A child process does not inherit this process's audit
#: hook, so while a route is on these are not started at all (fail closed), whatever asked.
_NETWORK_PROGRAMS = frozenset({
    "curl", "wget", "pip", "pip3", "uv", "uvx", "npm", "npx", "pnpm", "yarn", "brew", "ssh",
    "scp", "sftp", "rsync", "yt-dlp", "youtube-dl", "aria2c", "nc", "ncat", "telnet", "ftp",
})
_GIT_NETWORK_COMMANDS = frozenset({"clone", "fetch", "pull", "push", "ls-remote", "submodule", "remote"})
_URL_ARG = re.compile(r"^[a-z][a-z0-9+.-]*://([^/:@]*@)?(\[[^\]]+\]|[^/:?#]+)", re.I)


def _program_goes_online(argv: list[str]) -> str:
    """The program's name when starting it would send traffic past the route, else ""."""
    if not argv:
        return ""
    name = os.path.basename(argv[0]).lower().removesuffix(".exe")
    rest = [str(arg) for arg in argv[1:]]
    if any(arg.startswith("--proxy-server=") for arg in rest):
        return ""  # a browser launched with the route (core.private_web.browser_route)
    if name in {"open", "xdg-open"}:
        return ""  # hands a link to the person's own browser, which is theirs, not VOOL's traffic
    if name in _NETWORK_PROGRAMS:
        return name
    if name.startswith("python") and len(rest) >= 2 and rest[0] == "-m" and rest[1] in {"pip", "ensurepip"}:
        return "pip"
    if name == "git" and any(arg in _GIT_NETWORK_COMMANDS for arg in rest):
        return "git"
    if name == "ollama" and rest[:1] in (["pull"], ["push"]):
        return "ollama"
    for arg in rest:
        match = _URL_ARG.match(arg.strip())
        if match and not _host_stays_local(match.group(2).strip("[]")):
            return name
    return ""


def _host_stays_local(host: str) -> bool:
    if _is_ip_literal(host):
        return _ip_stays_local(host)
    return _name_stays_local(host)


def _argv_of(event: str, args: tuple[Any, ...]) -> list[str]:
    if event == "subprocess.Popen":
        executable, argv = (*args, None, None)[:2]
        if isinstance(argv, (str, bytes, os.PathLike)):
            argv = shlex.split(os.fsdecode(argv)) if isinstance(argv, (str, bytes)) else [os.fspath(argv)]
        argv = [os.fsdecode(a) if isinstance(a, (bytes, os.PathLike)) else str(a) for a in (argv or [])]
        if executable:
            argv = [os.fsdecode(executable), *argv[1:]] if argv else [os.fsdecode(executable)]
        return argv
    if event == "os.system":
        command = args[0] if args else ""
        command = os.fsdecode(command) if isinstance(command, bytes) else str(command or "")
        with contextlib.suppress(ValueError):
            return shlex.split(command)
        return command.split()
    if event in ("os.posix_spawn", "os.exec"):
        argv = args[1] if len(args) > 1 else []
        return [os.fsdecode(a) if isinstance(a, (bytes, os.PathLike)) else str(a) for a in (argv or [])]
    return []


def _audit(event: str, args: tuple[Any, ...]) -> None:
    if event in ("subprocess.Popen", "os.system", "os.posix_spawn", "os.exec"):
        if _guard_allows_public():
            return
        program = _program_goes_online(_argv_of(event, args))
        if program:
            raise PrivateWebBlocked(
                f"Private web is switched on, so {program} was not started: it would go online directly"
            )
        return
    if not event.startswith("socket."):
        return
    if _guard_allows_public():
        return
    if event == "socket.connect" or event == "socket.sendto":
        address = args[1] if len(args) > 1 else None
        if not isinstance(address, tuple) or not address:
            return  # AF_UNIX and friends never leave the machine
        host = address[0]
        if isinstance(host, str) and _ip_stays_local(host):
            return
        if isinstance(host, str) and not _is_ip_literal(host) and _name_stays_local(host):
            return
        raise PrivateWebBlocked(f"Private web is switched on, so a direct connection to {host} was stopped")
    if event in ("socket.getaddrinfo", "socket.gethostbyname", "socket.gethostbyname_ex"):
        host = args[0] if args else None
        if _name_stays_local(host):
            return
        raise PrivateWebBlocked("Private web is switched on, so a direct DNS lookup was stopped")
    if event == "socket.gethostbyaddr":
        host = args[0] if args else None
        if isinstance(host, str) and (_ip_stays_local(host) or (_name_stays_local(host) and not _is_ip_literal(host))):
            return
        raise PrivateWebBlocked("Private web is switched on, so a direct DNS lookup was stopped")


_guard_installed = False
_guard_lock = threading.Lock()


def install_egress_guard(*, refresh_state: bool = True) -> bool:
    """Install the audit hook once per process. Returns True when this call installed it.

    Audit hooks cannot be removed, so the hook itself reads the live route each time and does
    nothing while Private web is off.
    """
    global _guard_installed
    with _guard_lock:
        if _guard_installed:
            return False
        sys.addaudithook(_audit)
        _guard_installed = True
    if refresh_state:
        with contextlib.suppress(Exception):
            refresh()
    return True


def egress_guard_installed() -> bool:
    return _guard_installed


__all__ = [
    "ROUTES",
    "ROUTE_OFF",
    "ROUTE_SYSTEM_VPN",
    "ROUTE_TOR",
    "ROUTE_WIREGUARD",
    "BrowserRoute",
    "PrivateWebBlocked",
    "RouteState",
    "SocksError",
    "browser_route",
    "clear_system_vpn_verified",
    "current_route",
    "egress_guard_installed",
    "install_egress_guard",
    "isolation_key",
    "mark_system_vpn_verified",
    "probe_socks",
    "refresh",
    "resolved_at_far_end",
    "route_problem",
    "set_route_for_process",
    "socks5_connect",
    "socks_http_handlers",
    "url_stays_local",
]
