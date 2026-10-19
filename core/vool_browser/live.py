"""Owner-supervised browsing. Remote documents are inert, untrusted evidence.

The existing engine discovery and pinned HTTP gateway own launch and transport.
The model can propose, inspect and stop. Only the same-origin owner HTTP door
can consume a proposal. Nothing in a tool argument can approve one.
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import contextvars
import copy
import ipaddress
import json
import shutil
import socket
import tempfile
import threading
import time
import uuid
import zlib
from datetime import timezone
from email.utils import parsedate_to_datetime
from http.cookies import CookieError, SimpleCookie
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import parse_qsl, quote, urlencode, urljoin, urlsplit, urlunsplit
from urllib.request import Request

from core.runtime_paths import active_data_dir
from core.vool_browser.engine import find_engine_binary, isolation_launch_flags, refuse_real_profile_target
from core.vool_browser.ops import Untrusted

MAX_BYTES = 2_000_000
TIMEOUT = 8
_LOCK = threading.RLock()
_SESSIONS: dict[tuple[str, str], LiveSession] = {}


class BrowserFault(ValueError):
    pass


def _exited(process: Any) -> bool:
    import psutil

    try:
        return process.status() == psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return True


def _root() -> Path:
    return active_data_dir() / "live_browser"


def enabled() -> bool:
    try:
        return json.loads((_root() / "setting.json").read_text()).get("enabled") is True
    except (OSError, ValueError, AttributeError):
        return False


def configured() -> bool:
    return (_root() / "setting.json").exists()


def available() -> bool:
    from core import policy_engine

    return enabled() and policy_engine.allow_web_fallback() and not policy_engine.local_only_mode()


def settings() -> dict[str, Any]:
    from core import policy_engine

    return {"ok": True, "enabled": enabled(), "available": available(),
            "local_only": policy_engine.local_only_mode(), "web_enabled": policy_engine.allow_web_fallback()}


def set_enabled(value: bool) -> dict[str, Any]:
    from core import policy_engine

    if type(value) is not bool:
        raise BrowserFault("invalid_setting")
    if value and (policy_engine.local_only_mode() or not policy_engine.allow_web_fallback()):
        raise BrowserFault("web_off")
    with _LOCK:
        _root().mkdir(parents=True, exist_ok=True, mode=0o700)
        target = _root() / "setting.json"
        temp = target.with_suffix(".tmp")
        temp.write_text(json.dumps({"enabled": value}))
        temp.replace(target)
        if not value:
            for item in list(_SESSIONS.values()):
                item.stop()
    return settings()


def _check() -> None:
    if not available():
        raise BrowserFault("web_off")


def _key(chat: str) -> tuple[str, str]:
    if not chat or len(chat) > 256:
        raise BrowserFault("chat_required")
    return str(active_data_dir()), chat


def chat_active(chat: str) -> bool:
    from core.context_namespace import load_chat_namespace

    try:
        namespace = load_chat_namespace(chat)
        return namespace is None or namespace.lifecycle_state == "active"
    except Exception:
        raise BrowserFault("chat_inactive") from None


def session(chat: str, *, create: bool = False) -> LiveSession | None:
    key = _key(chat)
    with _LOCK:
        if create and not chat_active(chat):
            raise BrowserFault("chat_inactive")
        item = _SESSIONS.get(key)
        if item is not None and not item.closed:
            from core.private_chat import is_private

            if item.private != is_private(chat):
                item.stop()
        if create and item is not None and item.closed and item.state != "stopped":
            raise BrowserFault("busy")
        if create and (item is None or item.closed):
            _check()
            item = LiveSession(chat)
            _SESSIONS[key] = item
        return item


def stop_chat(chat: str) -> None:
    key = _key(chat)
    with _LOCK:
        item = _SESSIONS.get(key)
        if item:
            item.stop()
            _SESSIONS.pop(key, None)


def stop_all() -> None:
    with _LOCK:
        for item in list(_SESSIONS.values()):
            item.stop()
        _SESSIONS.clear()


def _canonical_url(value: Any) -> str:
    import idna

    try:
        parts = urlsplit(str(value or "").strip())
        if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
            raise ValueError("destination")
        host = parts.hostname
        host = f"[{host}]" if ":" in host else idna.encode(host, uts46=True).decode("ascii")
        port = parts.port
        authority = host if port is None or port == (443 if parts.scheme == "https" else 80) else f"{host}:{port}"
        result = urlunsplit((parts.scheme, authority, quote(parts.path or "/", safe="/%:@!$&'()*+,;=-._~"), quote(parts.query, safe="/%?:@!$&'()*+,;=-._~"), ""))
        if len(result) > 4096:
            raise ValueError("destination")
        return result
    except (ValueError, UnicodeError):
        raise BrowserFault("destination_refused") from None


def _addresses(url: str, fixture_origin: str = "") -> tuple[str, ...]:
    parts = urlsplit(url)
    if parts.scheme not in {"https", "http"} or not parts.hostname or parts.username or parts.password:
        raise BrowserFault("destination_refused")
    if parts.fragment:
        parts = parts._replace(fragment="")
    from core.runtime_served_origins import runtime_origin_refusal

    if runtime_origin_refusal(url):
        raise BrowserFault("destination_refused")
    answers = tuple(sorted({r[4][0] for r in socket.getaddrinfo(parts.hostname, parts.port or (443 if parts.scheme == "https" else 80), type=socket.SOCK_STREAM)}))
    fixture = bool(fixture_origin) and f"{parts.scheme}://{parts.netloc}" == fixture_origin
    if not answers or (any(not ipaddress.ip_address(a).is_global or ipaddress.ip_address(a).is_multicast for a in answers) and not fixture):
        raise BrowserFault("destination_refused")
    return answers


def _fetch(url: str, method: str, headers: dict, data: bytes | None, fixture_origin: str, stopped: tuple[threading.Event, threading.Event], source_context: dict, download: bool, telemetry=None) -> tuple[int, dict, bytes]:
    """No ambient proxy, pinned DNS, no redirects, bounded decoded response."""
    from core.effect_gateway import named_background_effect_scope
    from core.remote_fetch_policy import open_remote

    _check()
    addresses = _addresses(url, fixture_origin)
    if any(event.is_set() for event in stopped):
        raise BrowserFault("stopped")
    _check()
    safe_headers = {k: v for k, v in headers.items() if k.lower() in {"content-type", "cookie", "accept", "user-agent"}}
    safe_headers["Accept-Encoding"] = "identity"
    req = Request(url, data=data, headers=safe_headers, method=method)
    with contextlib.ExitStack() as scope:
        scope.enter_context(named_background_effect_scope("browser.owner_action", source_context={**source_context, "surface": "api", "platform": "web_companion", "allow_remote_fetch": True}))
        if telemetry is not None:
            scope.enter_context(telemetry())
        try:
            response = open_remote(req, timeout=TIMEOUT, redirect_policy="refuse", pinned_addresses=addresses)
        except HTTPError as exc:
            if 300 <= exc.code < 400:
                return exc.code, {**dict(exc.headers), "_cookies": exc.headers.get_all("Set-Cookie", [])}, b""
            response = exc
        with response:
            status = response.status
            if download and not 200 <= status < 300:
                raise BrowserFault("page_failed")
            result_headers = dict(response.headers)
            result_headers["_cookies"] = response.headers.get_all("Set-Cookie", [])
            if 300 <= status < 400:
                return status, result_headers, b""
            if int(response.headers.get("Content-Length", "0")) > MAX_BYTES:
                raise BrowserFault("page_too_large")
            media_type = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
            if not download and ("attachment" in response.headers.get("Content-Disposition", "").lower() or media_type not in {"text/html", "text/plain", "application/xhtml+xml"}):
                raise BrowserFault("download_refused")
            chunks = []
            size = 0
            while size <= MAX_BYTES:
                if any(event.is_set() for event in stopped):
                    raise BrowserFault("stopped")
                chunk = response.read(min(65536, MAX_BYTES + 1 - size))
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
            raw = b"".join(chunks)
            if len(raw) > MAX_BYTES:
                raise BrowserFault("page_too_large")
            encoding = response.headers.get("Content-Encoding", "identity").strip().lower()
            if encoding in {"gzip", "deflate"}:
                decoder = zlib.decompressobj(31 if encoding == "gzip" else 15)
                try:
                    decoded = decoder.decompress(raw, MAX_BYTES + 1)
                except zlib.error:
                    if encoding != "deflate":
                        raise BrowserFault("page_failed") from None
                    decoder = zlib.decompressobj(-15)
                    decoded = decoder.decompress(raw, MAX_BYTES + 1)
                if len(decoded) > MAX_BYTES or decoder.unconsumed_tail:
                    raise BrowserFault("page_too_large")
                if not decoder.eof or decoder.unused_data:
                    raise BrowserFault("page_failed")
                raw = decoded
            elif encoding not in {"identity", ""}:
                raise BrowserFault("page_failed")
            return status, result_headers, raw


class LiveSession:
    def __init__(self, chat: str, *, fixture_origin: str = "", lease_seconds: float = 120) -> None:
        from core.private_chat import is_private

        self.chat = chat
        self.private = is_private(chat)
        # Test fixtures can allow exactly their own random loopback origin through
        # this Python constructor. No request or tool field can set this value.
        self.fixture_origin = fixture_origin
        self.id = uuid.uuid4().hex
        self.profile = _root() / "profiles" / self.id
        self.closed = False
        self.takeover = False
        self.pending: dict[str, Any] | None = None
        self.state = "ready"
        self.result: dict[str, Any] = {}
        self.frame = ""
        self.url = ""
        self._document_origin = ""
        self.nodes: list[dict] = []
        self._forms: list[dict] = []
        self._submission_previews: dict[str, list[dict]] = {}
        self.revision = 0
        self.last_activity = time.monotonic()
        self._lease_seconds = max(1, float(lease_seconds))
        self._owner_deadline = self.last_activity + self._lease_seconds
        self._completed: dict[str, dict] = {}
        self._telemetry: dict[str, Any] = {}
        self._lease_cleanup = None
        self._mutex = threading.RLock()
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=contextvars.Context().run, args=(self._run,), daemon=True, name="live-browser")
        self._thread.start()
        self._context = None
        self._driver = None
        self._driver_manager = None
        self._driver_start = None
        self._opening = None
        self._engine_closed = False
        self._loop_stop_requested = False
        self._closing_future = None
        self._page = None
        self._task = None
        self._permit: dict[str, Any] | None = None
        self._blocked = ""
        self._stopped = threading.Event()
        self._routes: set[asyncio.Task] = set()
        self._execution_task = None
        self.cleanup_metrics = {}
        self._owned_processes: dict[int, float] = {}
        # Chromium's own scratch directory (Unix sockets, temp files); created at
        # launch, removed with the profile.
        self._engine_tmp: Path | None = None
        self._driver_pid = 0
        self._download: tuple[str, bytes] | None = None
        self._proxy_server = None
        self._proxy_clients: set[Any] = set()
        self.engine_network_refusals = 0
        self._loop.call_soon_threadsafe(self._check_owner_lease)

    def owner_heartbeat(self) -> None:
        """Only the owner HTTP state door renews presence, never model reads."""
        with self._mutex:
            if not self.closed:
                self._owner_deadline = time.monotonic() + self._lease_seconds

    def _check_owner_lease(self) -> None:
        if self.closed:
            return
        if time.monotonic() < self._owner_deadline:
            self._loop.call_later(min(5, self._lease_seconds / 2), self._check_owner_lease)
            return
        # Cleanup cannot join this event loop from its own thread. The owned
        # cleanup thread leaves the loop free to finish context/driver closure.
        self._lease_cleanup = threading.Thread(target=self._expire_owner_lease, daemon=True)
        self._lease_cleanup.start()

    def _expire_owner_lease(self) -> None:
        # Preserve cleanup_failed and the retryable session for the owner.
        with contextlib.suppress(BrowserFault):
            self.stop()

    def _complete(self, proposal_id: str, result: dict) -> None:
        with self._mutex:
            self._completed[proposal_id] = result
            while len(self._completed) > 8:
                self._completed.pop(next(iter(self._completed)))

    def wait_for_owner(self, proposal: dict, source_context: dict) -> dict:
        """Continue the original served tool call after its exact owner decision."""
        from core.i18n import catalog_for
        from core.runtime_task_events import emit_runtime_event
        from core.tool_intent_executor import _turn_was_cancelled

        proposal_id = proposal["id"]
        view_url = "/browser?chat=" + quote(self.chat)
        emit_runtime_event(
            source_context, event_type="browser_approval_required",
            message=catalog_for(str(source_context.get("ui_locale") or "en")).text("live.approval_required"),
            details={"view_url": view_url, "proposal_id": proposal_id, "op": proposal["op"]},
        )
        from core import runtime_active_clock

        with contextlib.ExitStack() as human_wait:
            if runtime_active_clock.enabled():
                human_wait.enter_context(runtime_active_clock.paused())
            return self._wait_for_decision(proposal, source_context, human_wait, _turn_was_cancelled)

    def _wait_for_decision(self, proposal: dict, source_context: dict, human_wait, cancelled) -> dict:
        proposal_id = proposal["id"]
        view_url = "/browser?chat=" + quote(self.chat)
        while time.monotonic() < proposal["expires"] + 22:
            if cancelled(source_context) or not available():
                self.stop()
                return {"ok": False, "status": "cancelled" if available() else "web_off"}
            with self._mutex:
                if self.closed:
                    return {"ok": False, "status": self.state}
                if not self.pending or self.pending["id"] != proposal_id:
                    human_wait.close()  # Engine/model time remains chargeable.
                if proposal_id in self._completed:
                    return {**copy.deepcopy(self._completed.pop(proposal_id)), "view_url": view_url}
                if self.takeover:
                    return {"ok": False, "status": "paused", "view_url": view_url}
                if self.pending and self.pending["id"] == proposal_id and time.monotonic() >= proposal["expires"]:
                    self.pending = None
                    self._submission_previews.pop(proposal_id, None)
                    self._telemetry.pop(proposal_id, None)
                    self.state = "refused"
                    return {"ok": False, "status": "stale_approval", "view_url": view_url}
            self._stopped.wait(.05)
        self.stop()
        return {"ok": False, "status": "operation_timeout", "view_url": view_url}

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()
        tasks = asyncio.all_tasks(self._loop)
        for task in tasks:
            task.cancel()
        self._loop.run_until_complete(asyncio.gather(*tasks, return_exceptions=True))
        self._loop.run_until_complete(self._loop.shutdown_asyncgens())
        self._loop.close()

    async def _open(self) -> None:
        # Takeover can cancel an operation while the engine is starting. One
        # shielded initializer prevents a partial context or duplicate driver.
        if self._opening is None:
            self._opening = asyncio.create_task(self._initialize())
        await asyncio.shield(self._opening)

    async def _initialize(self) -> None:
        if self._context is not None:
            return
        from playwright.async_api import async_playwright

        binary = find_engine_binary()
        if not binary:
            raise BrowserFault("engine_missing")
        if not self.profile.resolve().is_relative_to(active_data_dir().resolve()) or refuse_real_profile_target(str(self.profile.resolve())):
            raise BrowserFault("invalid_profile")
        self.profile.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self._driver_start is None:
            self._driver_manager = async_playwright()
            self._driver_start = asyncio.create_task(self._driver_manager.start())
        self._driver = await asyncio.shield(self._driver_start)
        if self.closed:
            raise BrowserFault("stopped")
        # The driver's actual subprocess handle, never a process-name search.
        proc = self._driver._impl_obj._connection._transport._proc
        self._driver_pid = proc.pid
        self._record_process(proc.pid)
        flags = [a for a in isolation_launch_flags(profile_dir=str(self.profile)) if not a.startswith("--user-data-dir=")]
        import os

        # Chromium puts its Unix sockets under TMPDIR. A socket path is limited to
        # about 100 bytes, and the profile path under the VOOL data directory can
        # already be that long, so on Linux the engine died at launch (SIGTRAP) and
        # every page answered page_failed. A short private directory of its own,
        # owner-only and removed with the profile, keeps the sockets valid.
        if self._engine_tmp is None:
            self._engine_tmp = Path(tempfile.mkdtemp(prefix="vool-lb-"))
        self._proxy_server = await asyncio.start_server(self._deny_engine_network, '127.0.0.1', 0)
        proxy_port = self._proxy_server.sockets[0].getsockname()[1]
        # Only the approved Python gateway can reach a site. Chromium's background
        # services see a rejecting loopback proxy; DNS and QUIC cannot bypass it.
        flags += ['--disable-quic', '--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1']
        self._context = await self._driver.chromium.launch_persistent_context(
            user_data_dir=str(self.profile), executable_path=binary, headless=True,
            args=flags, timeout=10000, chromium_sandbox=True,
            proxy={'server':f'http://127.0.0.1:{proxy_port}', 'bypass':'<-loopback>'},
            downloads_path=str(self.profile / "downloads"), env={**os.environ, "TMPDIR": str(self._engine_tmp)},
            java_script_enabled=False, service_workers="block", accept_downloads=False,
            permissions=[], viewport={"width": 1100, "height": 720},
        )
        self._browser = self._context.browser
        self._record_driver_tree()
        await self._context.route("**/*", self._route)
        await self._context.route_web_socket("**/*", lambda ws: ws.close())
        self._page = self._context.pages[0] if self._context.pages else await self._context.new_page()
        self._page.set_default_timeout(TIMEOUT * 1000)
        self._context.on("page", lambda page: asyncio.create_task(page.close()) if page != self._page else None)
        self._page.on("dialog", lambda dialog: asyncio.create_task(dialog.dismiss()))
        self._page.on("download", lambda download: asyncio.create_task(download.cancel()))

    async def _deny_engine_network(self, reader, writer) -> None:
        self._proxy_clients.add(writer)
        self.engine_network_refusals += 1
        try:
            with contextlib.suppress(asyncio.TimeoutError, ConnectionError):
                await asyncio.wait_for(reader.read(4096), timeout=1)
                writer.write(b'HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\nConnection: close\r\n\r\n')
                await writer.drain()
        finally:
            writer.close()
            with contextlib.suppress(asyncio.TimeoutError, ConnectionError):
                await asyncio.wait_for(writer.wait_closed(), timeout=1)
            self._proxy_clients.discard(writer)

    async def _route(self, route: Any) -> None:
        request = route.request
        task = asyncio.current_task()
        self._routes.add(task)
        permit = self._permit
        try:
            _check()
            if self.closed or not permit or request.url != permit["url"] or request.method != permit["method"] or permit["used"]:
                await route.abort()
                return
            permit["used"] = True
            status, headers, raw = await asyncio.to_thread(_fetch, request.url, request.method, await request.all_headers(), request.post_data_buffer, self.fixture_origin, (self._stopped, permit["cancelled"]), {"session_id": self.chat, "private_chat": self.private}, False, permit.get("telemetry"))
            if self.closed or self._permit is not permit or not available():
                await route.abort()
                return
            await self._accept_cookies(request.url, headers.get("_cookies", []))
            low = {k.lower(): v for k, v in headers.items()}
            if 300 <= status < 400:
                self._blocked = "redirect_refused"
                self.result = {"redirect": Untrusted.wrap(urljoin(request.url, low.get("location", "")), url=request.url, origin="")}
                await route.abort()
                return
            # No remote scripts, frames, forms that target another browsing context,
            # refresh navigation or subresources can escape the request gate.
            clean = {k: v for k, v in headers.items() if k.lower() == "content-type"}
            clean["X-DNS-Prefetch-Control"] = "off"
            clean["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-src 'none'; base-uri 'none'"
            await route.fulfill(status=status, headers=clean, body=raw)
            parts = urlsplit(request.url)
            self._document_origin = (parts.scheme, parts.netloc)
        except asyncio.CancelledError:
            with contextlib.suppress(Exception):
                await route.abort()
            raise
        except Exception as exc:
            self._blocked = str(exc) if isinstance(exc, BrowserFault) else "connection_failed"
            self.result["failure_class"] = type(exc).__name__
            with contextlib.suppress(Exception):
                await route.abort()
        finally:
            self._routes.discard(task)

    async def _accept_cookies(self, url: str, headers: list[str]) -> None:
        host = urlsplit(url).hostname
        cookies = []
        for value in headers:
            jar = SimpleCookie()
            try:
                jar.load(value)
            except CookieError:
                continue
            for morsel in jar.values():
                # Host-only scope: a response cannot plant a cookie for another
                # site or a public suffix. No cookie jar is ever persisted.
                declared = morsel["domain"].lstrip(".").lower()
                if declared and host != declared and not host.endswith("." + declared):
                    continue
                default_path = urlsplit(url).path.rpartition("/")[0] or "/"
                path = morsel["path"] if morsel["path"].startswith("/") else default_path
                cookie = {"name": morsel.key, "value": morsel.value, "domain": host,
                          "path": path, "secure": bool(morsel["secure"]),
                          "httpOnly": bool(morsel["httponly"])}
                same_site = morsel["samesite"].capitalize()
                if same_site in {"Lax", "Strict", "None"}:
                    cookie["sameSite"] = same_site
                try:
                    if morsel["max-age"]:
                        cookie["expires"] = max(1, time.time() + int(morsel["max-age"]))
                    elif morsel["expires"]:
                        date = parsedate_to_datetime(morsel["expires"])
                        cookie["expires"] = max(1, (date if date.tzinfo else date.replace(tzinfo=timezone.utc)).timestamp())
                except (TypeError, ValueError, OverflowError):
                    continue
                cookies.append(cookie)
        if cookies:
            await self._context.add_cookies(cookies)

    def snapshot(self) -> dict[str, Any]:
        if not self.closed and not available():
            self.stop()
        with self._mutex:
            pending = copy.deepcopy(self.pending)
            if pending and pending['id'] in self._submission_previews:
                pending['submission'] = copy.deepcopy(self._submission_previews[pending['id']])
            return {"ok": True, "id": self.id, "state": self.state, "takeover": self.takeover,
                    "pending": pending, "url": self.url, "revision": self.revision,
                    "nodes": self.nodes, "frame": self.frame, "result": self.result,
                    "private": self.private, "closed": self.closed, "download_ready": self._download is not None}

    def propose(self, op: str, args: dict[str, Any], *, actor: str = "agent") -> dict[str, Any]:
        _check()
        with self._mutex:
            self.last_activity = time.monotonic()
            if self.closed or self.state == "running" or self.pending:
                raise BrowserFault("busy")
            if actor == "agent" and self.takeover:
                raise BrowserFault("paused")
            if op not in {"navigate", "inspect", "click", "type", "scroll", "download", "upload"}:
                raise BrowserFault("unsupported")
            safe: dict[str, Any] = {}
            review = None
            if op in {"navigate", "download"}:
                url = _canonical_url(args.get("url"))
                safe["url"] = url
                if op == "download":
                    parts = urlsplit(url)
                    if (parts.scheme, parts.netloc) != self._document_origin:
                        raise BrowserFault("destination_refused")
            if op in {"click", "type", "upload"}:
                selector = str(args.get("selector") or "")
                # Only a currently visible, observed node is actionable. Model CSS,
                # coordinates, page-supplied tool calls and hidden nodes cannot mint it.
                node = next((n for n in self.nodes if selector == n["selector"]), None)
                if node is None:
                    raise BrowserFault("element_missing")
                if node.get("target_too_long"):
                    raise BrowserFault("destination_refused")
                safe.update(node)
                if node.get("payment"):
                    raise BrowserFault("use_wallet")
                if node.get("navigates") or (op == "type" and args.get("submit")):
                    if node.get("password_form") and node["method"] == "GET":
                        raise BrowserFault("invalid_input")
                    safe["target"] = _canonical_url(node["target"])
                if op == "type":
                    text = str(args.get("text") or "")
                    if len(text) > 10000 or node.get("type") == "file":
                        raise BrowserFault("invalid_input")
                    if node["tag"] == "select":
                        choices = [o for o in node.get("options", []) if text in {o["value"], o["label"]}]
                        if len(choices) != 1 or args.get("submit"):
                            raise BrowserFault("invalid_input")
                        text = choices[0]["value"]
                    safe.update(text=text, submit=bool(args.get("submit")))
                    if safe["submit"] and safe["method"] == "GET" and node.get("name"):
                        parts = urlsplit(safe["target"])
                        pairs = parse_qsl(parts.query, keep_blank_values=True)
                        if sum(k == node["name"] for k, _ in pairs) != 1:
                            raise BrowserFault("invalid_input")
                        pairs = [(k, text if k == node["name"] else v) for k, v in pairs]
                        safe["target"] = urlunsplit(parts._replace(query=urlencode(pairs)))
                if op == "upload":
                    file = args.get("file")
                    if node.get("type") != "file" or not isinstance(file, dict):
                        raise BrowserFault("invalid_input")
                    try:
                        raw = base64.b64decode(str(file.get("buffer") or ""), validate=True)
                    except ValueError:
                        raise BrowserFault("invalid_input") from None
                    if len(raw) > 1_000_000:
                        raise BrowserFault("page_too_large")
                    safe["file"] = {"name": Path(str(file.get("name") or "upload")).name[:100], "mimeType": "application/octet-stream", "buffer": base64.b64encode(raw).decode()}
                submits = (op == "click" and node.get("form", -1) >= 0 and node.get("navigates")) or (op == "type" and safe.get("submit"))
                if submits:
                    if node.get("form", -1) < 0:
                        raise BrowserFault("invalid_input")
                    from core.vool_browser.live_forms import submission_preview
                    from core.vool_browser.live_requests import submission_allowed

                    if actor == "agent" and not submission_allowed():
                        raise BrowserFault("request_scope_required")
                    try:
                        review = submission_preview(self._forms[node['form']], node, safe)
                    except ValueError:
                        raise BrowserFault("invalid_input") from None
            if op == "scroll":
                safe["direction"] = "up" if args.get("direction") == "up" else "down"
            token = uuid.uuid4().hex
            if actor == "agent":
                from core.remote_fetch_policy import capture_remote_fetch_telemetry

                self._telemetry[token] = capture_remote_fetch_telemetry()
            self.pending = {"id": token, "op": op, "args": safe, "actor": actor,
                            "revision": self.revision, "expires": time.monotonic() + 120}
            if review is not None:
                self._submission_previews[token] = review
            self.state = "approval_required"
            return {"ok": False, "status": "approval_required", "view_url": "/browser?chat=" + quote(self.chat), "proposal": copy.deepcopy(self.pending)}

    def decide(self, proposal_id: str, approve: bool) -> dict[str, Any]:
        _check()
        with self._mutex:
            pending = self.pending
            if not pending or pending["id"] != proposal_id:
                raise BrowserFault("stale_approval")
            self._submission_previews.pop(proposal_id, None)
            self.pending = None  # consume before dispatch; no replay, even after failure
            if not approve:
                self._telemetry.pop(pending["id"], None)
                self.state = "refused"
                self._complete(pending["id"], {"ok": False, "status": "refused"})
                return self.snapshot()
            if pending["expires"] < time.monotonic() or pending["revision"] != self.revision:
                self._telemetry.pop(pending["id"], None)
                self.state = "refused"
                raise BrowserFault("stale_approval")
            self.state = "running"
            pending["_telemetry"] = self._telemetry.pop(pending["id"], None)
            # An owner-approved command has its own execution context. A nested
            # caller cannot accidentally transfer model confinement or authority.
            self._task = contextvars.Context().run(asyncio.run_coroutine_threadsafe, self._execute_bounded(pending), self._loop)
            return {"ok": True, "state": "running"}

    async def _execute_bounded(self, proposal: dict) -> None:
        try:
            await asyncio.wait_for(self._execute(proposal), timeout=20)
        except asyncio.TimeoutError:
            self._permit = None
            if not self.closed:
                self.result = {"failure_class": "operation_timeout"}
                self.nodes = []
                self.frame = ""
                self.state = "page_failed"
                self._complete(proposal["id"], {"ok": False, "status": "page_failed"})

    async def _execute(self, proposal: dict) -> None:
        self._execution_task = asyncio.current_task()
        observe = True
        try:
            _check()
            if self.closed:
                raise BrowserFault("stopped")
            await self._open()
            if self.closed:
                raise BrowserFault("stopped")
            op, args = proposal["op"], proposal["args"]
            self._blocked = ""
            self.result = {}
            self._download = None
            if op == "download":
                # Fetching an approved attachment must not navigate the live
                # document to an aborted/error page and lose its origin.
                self._permit = {"url": args["url"], "method": "GET", "used": True, "op": op, "cancelled": threading.Event(), "telemetry": proposal.get("_telemetry")}
                cookies = await self._context.cookies(args["url"])
                headers = {"Cookie": "; ".join(c["name"] + "=" + c["value"] for c in cookies)}
                status, response_headers, raw = await asyncio.to_thread(
                    _fetch, args["url"], "GET", headers, None, self.fixture_origin,
                    (self._stopped, self._permit["cancelled"]),
                    {"session_id": self.chat, "private_chat": self.private}, True, proposal.get("_telemetry"),
                )
                if self.closed:
                    raise BrowserFault("stopped")
                await self._accept_cookies(args["url"], response_headers.get("_cookies", []))
                if 300 <= status < 400:
                    self._blocked = "redirect_refused"
                    low = {k.lower(): v for k, v in response_headers.items()}
                    self.result = {"redirect": Untrusted.wrap(urljoin(args["url"], low.get("location", "")), url=args["url"], origin="")}
                else:
                    self._download = ("download.bin", raw)
            elif op == "navigate":
                self._permit = {"url": args["url"].split("#")[0], "method": "GET", "used": False, "op": op, "cancelled": threading.Event(), "telemetry": proposal.get("_telemetry")}
                await self._page.goto(args["url"], wait_until="domcontentloaded", timeout=TIMEOUT * 1000)
            elif op in {"click", "type", "upload"}:
                node = self._page.locator(args["selector"])
                if await node.count() != 1 or not await node.is_visible():
                    raise BrowserFault("element_missing")
                if op == "upload":
                    payload = dict(args["file"])
                    payload["buffer"] = base64.b64decode(payload["buffer"])
                    await node.set_input_files(payload)
                elif op == "type":
                    if args["tag"] == "select":
                        await node.select_option(value=args["text"])
                    else:
                        await node.fill(args["text"])
                    if args.get("submit"):
                        self._permit = {"url": args["target"], "method": args["method"], "used": False, "op": op, "cancelled": threading.Event(), "telemetry": proposal.get("_telemetry")}
                        async with self._page.expect_navigation(wait_until="domcontentloaded", timeout=TIMEOUT * 1000):
                            await node.press("Enter")
                else:
                    self._permit = {"url": args["target"], "method": args["method"], "used": False, "op": op, "cancelled": threading.Event(), "telemetry": proposal.get("_telemetry")}
                    if args.get("navigates"):
                        async with self._page.expect_navigation(wait_until="domcontentloaded", timeout=TIMEOUT * 1000):
                            await node.click()
                    else:
                        await node.click()
            elif op == "scroll":
                await self._page.mouse.wheel(0, -600 if args["direction"] == "up" else 600)
            outcome = "ready" if not self._blocked else self._blocked
        except asyncio.CancelledError:
            observe = False
            outcome = "stopped" if self.closed else "paused" if self.takeover else "cancelled"
        except Exception as exc:
            self.result["failure_class"] = type(exc).__name__
            outcome = "ready" if self._download else (self._blocked or (str(exc) if isinstance(exc, BrowserFault) else "page_failed"))
        finally:
            proposal.pop("_telemetry", None)
            if self._permit:
                self._permit["cancelled"].set()
            self._permit = None
            if observe and not self.closed and self._page is not None:
                try:
                    await self._observe()
                except Exception as exc:
                    self.result["observation_failure_class"] = type(exc).__name__
                    self.nodes = []
                    self.frame = ""
                    if outcome == "ready" and not self._download:
                        outcome = "page_failed"
            if not self.closed:
                from core.vool_browser.receipts import build_receipt

                self.result["receipt"] = build_receipt(
                    op=proposal["op"], session=self.id, outcome=outcome,
                    final_url=self.url, origin=urlsplit(self.url).netloc,
                    approved_proposal_id=proposal["id"], revision=proposal["revision"],
                )
                self.state = outcome
                completed = {"ok": outcome == "ready", "status": "executed" if outcome == "ready" else outcome, "receipt": self.result["receipt"]}
                if outcome == "ready" and self.result.get("page"):
                    completed.update(copy.deepcopy(self.result["page"]))
                    completed["result"] = copy.deepcopy(self.result)
                    completed["nodes"] = {"content_class": "untrusted_page_evidence", "authority": "none", "items": copy.deepcopy(self.nodes)}
                self._complete(proposal["id"], completed)

    async def _observe(self) -> None:
        self.url = self._page.url
        # Fixed source, never a caller/page expression. Stable visible-node handles
        # are regenerated after every action and invalidated by the revision.
        from core.vool_browser.live_forms import OBSERVE, payment_form

        observed = await self._page.evaluate(OBSERVE)
        if self.closed:
            return
        self.nodes, self._forms = observed['nodes'], observed['forms']
        for node in self.nodes:
            form = self._forms[node['form']] if node['form'] >= 0 else None
            if form is not None and payment_form(form):
                node['payment'] = True
        text = (await self._page.locator("body").inner_text())[:50000]
        self.result["page"] = Untrusted.wrap(text, url=self.url, origin=urlsplit(self.url).netloc, title=(await self._page.title())[:300])
        self.frame = base64.b64encode(await self._page.screenshot(type="jpeg", quality=65, timeout=3000)).decode()
        self.revision += 1

    def take_over(self, value: bool) -> dict[str, Any]:
        with self._mutex:
            self.takeover = value
            if self.pending:
                self._telemetry.pop(self.pending["id"], None)
                self._submission_previews.pop(self.pending["id"], None)
            self.pending = None
            running = bool(value and self._task and not self._task.done())
            if running:
                if self._permit:
                    self._permit["cancelled"].set()
                self._task.cancel()
            elif self.state == "approval_required":
                self.state = "ready"
        if running:
            # Cancellation completes through _complete, which needs _mutex.
            # Never hold the caller's lock while waiting on that event loop.
            settling = asyncio.run_coroutine_threadsafe(self._settle_takeover(), self._loop)
            try:
                settling.result(timeout=3)
            except Exception as exc:
                raise BrowserFault("busy") from exc
        return self.snapshot()

    async def _settle_takeover(self) -> None:
        # Let cancellation reach the asyncio task even if the outer future was
        # cancelled before its coroutine began. No new operation overlaps it.
        await asyncio.sleep(0)
        task = self._execution_task
        if task and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if not self.closed:
            self.state = "ready"

    def stop(self) -> None:
        with self._mutex:
            if self.closed and self.state != "cleanup_failed":
                return
            self.closed = True
            self.pending = None
            self.frame = ""
            self.nodes = []
            self.result = {}
            self._download = None
            self._completed.clear()
            self._telemetry.clear()
            self._forms.clear()
            self._submission_previews.clear()
            self.url = ""
            self._document_origin = ""
            self.state = "stopping"
            self._stopped.set()
        try:
            if not self._engine_closed:
                future = self._closing_future
                if future is None or (future.done() and (future.cancelled() or future.exception())):
                    future = asyncio.run_coroutine_threadsafe(self._close(), self._loop)
                    self._closing_future = future
                future.result(timeout=12)
                self._engine_closed = True
            if not self._loop_stop_requested:
                self._loop.call_soon_threadsafe(self._loop.stop)
                self._loop_stop_requested = True
            self._thread.join(timeout=2)
            if self._thread.is_alive():
                raise BrowserFault("cleanup_failed")
            # Closed transport proxies retain URLs and request metadata. Do not
            # keep them in a completed private session or across a purge retry.
            self._page = self._context = self._driver = None
            self._driver_manager = self._driver_start = self._opening = None
            self._task = self._execution_task = self._closing_future = None
            self._browser = None
            self._proxy_server = None
            self._proxy_clients.clear()
            self._owned_processes.clear()
            self._routes.clear()
            if self.profile.exists() or self.profile.is_symlink():
                shutil.rmtree(self.profile)
            if self._engine_tmp is not None:
                if self._engine_tmp.exists() or self._engine_tmp.is_symlink():
                    shutil.rmtree(self._engine_tmp)
                self._engine_tmp = None
        except Exception as exc:
            self.cleanup_metrics["failure"] = type(exc).__name__
            self.state = "cleanup_failed"
            raise BrowserFault("cleanup_failed") from exc
        self.state = "stopped"

    def _record_process(self, pid: int) -> None:
        import psutil

        process = psutil.Process(pid)
        self._owned_processes[pid] = process.create_time()

    def _stop_owned_processes(self, include_driver: bool = False) -> None:
        import psutil

        # IDs originate only from our launch handle and our own browser's CDP
        # session. Creation times fence PID reuse. No names or global scans.
        owned = list(self._owned_processes.items())
        owned.sort(key=lambda entry: entry[0] == self._driver_pid)
        waiting = []
        for pid, created in owned:
            if pid == self._driver_pid and not include_driver:
                continue
            try:
                process = psutil.Process(pid)
                if process.create_time() != created:
                    continue
                process.terminate()
                waiting.append(process)
            except psutil.NoSuchProcess:
                pass
        _, alive = psutil.wait_procs(waiting, timeout=0.5)
        for process in alive:
            with contextlib.suppress(psutil.NoSuchProcess):
                if process.create_time() == self._owned_processes.get(process.pid):
                    process.kill()
        _, alive = psutil.wait_procs(alive, timeout=1)
        # A killed engine child whose parent died first is reparented to PID 1. Where
        # PID 1 does not reap (a container's own entry process), it stays a zombie: it
        # has exited and holds nothing but its table entry, so it is stopped. Any
        # process still running keeps the failure.
        alive = [process for process in alive if not _exited(process)]
        if alive:
            raise BrowserFault("cleanup_failed")

    def _record_driver_tree(self) -> None:
        import psutil

        # A cancelled launch may not yet have returned its browser handle. The
        # exact transport subprocess still owns that launch and its descendants.
        manager = self._driver_manager
        transport = getattr(getattr(manager, "_connection", None), "_transport", None)
        proc = getattr(transport, "_proc", None)
        if proc is None:
            return
        self._driver_pid = proc.pid
        try:
            if proc.pid not in self._owned_processes:
                self._record_process(proc.pid)
            parent = psutil.Process(proc.pid)
            if parent.create_time() != self._owned_processes[proc.pid]:
                return
            for child in parent.children(recursive=True):
                self._owned_processes[child.pid] = child.create_time()
        except psutil.NoSuchProcess:
            pass

    async def _close(self) -> None:
        started = time.monotonic()
        task = self._execution_task
        self.cleanup_metrics["phase"] = "driver_start"
        opening = self._opening
        starting = self._driver_start
        if opening and not starting:
            opening.cancel()
            await asyncio.gather(opening, return_exceptions=True)
        if starting and not starting.done():
            # Wait only for the owned subprocess handle, never for the page
            # operation (which may itself be hung). Shield keeps it recoverable.
            until = time.monotonic() + 1
            while not starting.done() and not self._driver_pid and time.monotonic() < until:
                self._record_driver_tree()
                await asyncio.sleep(.01)
            self._record_driver_tree()
            if not starting.done():
                await asyncio.to_thread(self._stop_owned_processes, True)
                done, _ = await asyncio.wait({starting}, timeout=1)
                if not done:
                    raise BrowserFault("cleanup_failed")
        if starting and starting.done() and not starting.cancelled():
            with contextlib.suppress(Exception):
                self._driver = starting.result()
        self._record_driver_tree()
        if task and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if opening and not opening.done():
            opening.cancel()
            await asyncio.gather(opening, return_exceptions=True)
        self.cleanup_metrics["phase"] = "engine_close"
        routes = list(self._routes)
        for route_task in routes:
            route_task.cancel()
        if routes:
            await asyncio.gather(*routes, return_exceptions=True)
        if self._proxy_server:
            self._proxy_server.close()
            await self._proxy_server.wait_closed()
        for writer in tuple(self._proxy_clients):
            writer.close()
        self._proxy_clients.clear()
        closing = []
        if self._context:
            closing.append(asyncio.create_task(self._context.close()))
        if getattr(self, "_browser", None):
            closing.append(asyncio.create_task(self._browser.close()))
        if closing:
            await asyncio.wait(closing, timeout=1)
        self._record_driver_tree()
        await asyncio.to_thread(self._stop_owned_processes)
        if closing:
            _, pending = await asyncio.wait(closing, timeout=1)
            for pending_task in pending:
                pending_task.cancel()
            await asyncio.gather(*closing, return_exceptions=True)
        if self._driver:
            self.cleanup_metrics["phase"] = "driver_close"
            stopping = asyncio.create_task(self._driver.stop())
            done, _ = await asyncio.wait({stopping}, timeout=1)
            if not done:
                await asyncio.to_thread(self._stop_owned_processes, True)
                done, _ = await asyncio.wait({stopping}, timeout=1)
                if not done:
                    stopping.cancel()
            await asyncio.gather(stopping, return_exceptions=True)
        if task and task is not asyncio.current_task() and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self.cleanup_metrics["total"] = time.monotonic() - started
        self.cleanup_metrics["phase"] = "closed"

    def download(self) -> tuple[str, bytes] | None:
        result = self._download
        self._download = None
        return result


def tool_intent(intent: str, arguments: dict, source_context: dict) -> dict:
    """The existing chat tool seam; tool payloads only propose, never decide."""
    from core.remote_fetch_policy import local_only_active, remote_fetch_allowed_by_context
    from core.vool_browser.live_contracts import SUPPORTED
    from core.vool_browser.live_requests import confine, tool_allowed

    if intent not in SUPPORTED:
        raise BrowserFault("unsupported")
    confine()
    if not tool_allowed(intent, arguments):
        raise BrowserFault("request_scope_required")
    chat = str(source_context.get("session_id") or source_context.get("runtime_session_id") or "")
    op = intent.removeprefix("vool-browser.")
    local = op in {"cancel", "session.close", "session.status", "inspect"}
    if local_only_active(source_context) and op not in {"cancel", "session.close", "session.status"}:
        item = session(chat)
        if item:
            item.stop()
        raise BrowserFault("web_off")
    if not local:
        _check()
        if local_only_active(source_context) or not remote_fetch_allowed_by_context(source_context):
            raise BrowserFault("web_off")
    item = session(chat, create=not local)
    if item is None:
        raise BrowserFault("unknown_session")
    if op == "session.close" and item.pending:
        return {"ok": False, "status": "approval_required", "view_url": "/browser?chat=" + quote(chat)}
    if op in {"cancel", "session.close"}:
        item.stop()
        return {"ok": True, "status": "stopped"}
    if op == "inspect" and arguments.get("mode") in {"scroll_up", "scroll_down"}:
        if not remote_fetch_allowed_by_context(source_context):
            raise BrowserFault("web_off")
        return _tool_proposal(item, "scroll", {"direction": "up" if arguments["mode"] == "scroll_up" else "down"}, source_context)
    if op in {"session.status", "inspect"}:
        snap = item.snapshot()
        if op == "session.status":
            snap.pop("frame", None)
            snap.pop("nodes", None)
            snap.pop("result", None)
            if snap.get("pending"):
                # The owner may have entered a password or selected a file in
                # the pane. Model status needs approval metadata, not its bytes.
                snap["pending"] = {key: snap["pending"][key] for key in ("id", "op", "actor", "expires")}
            snap["status"] = "executed"
            return snap
        if snap["pending"]:
            return {"ok": False, "status": "approval_required", "view_url": "/browser?chat=" + quote(chat)}
        result = snap["result"]
        page = result.get("page")
        if not page or snap["state"] != "ready":
            return {"ok": False, "status": snap["state"] if snap["state"] != "ready" else "no_page"}
        return {"ok": True, "status": "executed", "result": result,
                "receipt": result.get("receipt"), **page,
                "nodes": {"content_class": "untrusted_page_evidence", "authority": "none", "items": snap["nodes"]}}
    if op == "session.open":
        op = "navigate"
        arguments = {"url": arguments.get("start_url")}
    return _tool_proposal(item, op, arguments, source_context)


def _tool_proposal(item: LiveSession, op: str, arguments: dict, source_context: dict) -> dict:
    proposed = item.propose(op, arguments)
    if op == "navigate":
        from core.vool_browser.live_requests import note_navigation_proposal

        note_navigation_proposal(arguments["url"])
    if source_context.get("live_browser_wait_for_owner") is True:
        return item.wait_for_owner(proposed["proposal"], source_context)
    return proposed
