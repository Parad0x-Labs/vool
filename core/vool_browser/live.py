"""Owner-supervised browsing. Remote documents are inert, untrusted evidence.

The existing engine discovery and pinned HTTP gateway own launch and transport.
The model can propose, inspect and stop. Only the same-origin owner HTTP door
can consume a proposal. Nothing in a tool argument can approve one.
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import copy
import ipaddress
import json
import shutil
import socket
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


def session(chat: str, *, create: bool = False) -> LiveSession | None:
    key = _key(chat)
    with _LOCK:
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


def _fetch(url: str, method: str, headers: dict, data: bytes | None, fixture_origin: str, stopped: tuple[threading.Event, threading.Event], source_context: dict, download: bool) -> tuple[int, dict, bytes]:
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
    with named_background_effect_scope("browser.owner_action", source_context={**source_context, "surface": "api", "platform": "web_companion", "allow_remote_fetch": True}):
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
    def __init__(self, chat: str, *, fixture_origin: str = "") -> None:
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
        self.revision = 0
        self.last_activity = time.monotonic()
        self._mutex = threading.RLock()
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run, daemon=True, name="live-browser")
        self._thread.start()
        self._context = None
        self._driver = None
        self._driver_manager = None
        self._driver_start = None
        self._opening = None
        self._page = None
        self._task = None
        self._permit: dict[str, Any] | None = None
        self._blocked = ""
        self._stopped = threading.Event()
        self._routes: set[asyncio.Task] = set()
        self._execution_task = None
        self.cleanup_metrics = {}
        self._owned_processes: dict[int, float] = {}
        self._driver_pid = 0
        self._download: tuple[str, bytes] | None = None

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

        self._browser = await self._driver.chromium.launch(
            executable_path=binary, headless=True, args=flags, timeout=5000,
            downloads_path=str(self.profile / "downloads"), env={**os.environ, "TMPDIR": str(self.profile)},
        )
        cdp = await self._browser.new_browser_cdp_session()
        info = await cdp.send("SystemInfo.getProcessInfo")
        for process in info["processInfo"]:
            if process["type"] == "browser":
                self._record_process(int(process["id"]))
        await cdp.detach()
        self._context = await self._browser.new_context(
            java_script_enabled=False, service_workers="block", accept_downloads=False,
            permissions=[], viewport={"width": 1100, "height": 720},
        )
        await self._context.route("**/*", self._route)
        await self._context.route_web_socket("**/*", lambda ws: ws.close())
        self._page = self._context.pages[0] if self._context.pages else await self._context.new_page()
        self._page.set_default_timeout(TIMEOUT * 1000)
        self._context.on("page", lambda page: asyncio.create_task(page.close()) if page != self._page else None)
        self._page.on("dialog", lambda dialog: asyncio.create_task(dialog.dismiss()))
        self._page.on("download", lambda download: asyncio.create_task(download.cancel()))

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
            status, headers, raw = await asyncio.to_thread(_fetch, request.url, request.method, await request.all_headers(), request.post_data_buffer, self.fixture_origin, (self._stopped, permit["cancelled"]), {"session_id": self.chat, "private_chat": self.private}, False)
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
        with self._mutex:
            if not self.closed and not available():
                self.stop()
            return {"ok": True, "id": self.id, "state": self.state, "takeover": self.takeover,
                    "pending": copy.deepcopy(self.pending), "url": self.url, "revision": self.revision,
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
            if op == "scroll":
                safe["direction"] = "up" if args.get("direction") == "up" else "down"
            token = uuid.uuid4().hex
            self.pending = {"id": token, "op": op, "args": safe, "actor": actor,
                            "revision": self.revision, "expires": time.monotonic() + 120}
            self.state = "approval_required"
            return {"ok": False, "status": "approval_required", "view_url": "/browser?chat=" + quote(self.chat), "proposal": copy.deepcopy(self.pending)}

    def decide(self, proposal_id: str, approve: bool) -> dict[str, Any]:
        _check()
        with self._mutex:
            pending = self.pending
            if not pending or pending["id"] != proposal_id:
                raise BrowserFault("stale_approval")
            self.pending = None  # consume before dispatch; no replay, even after failure
            if not approve:
                self.state = "refused"
                return self.snapshot()
            if pending["expires"] < time.monotonic() or pending["revision"] != self.revision:
                self.state = "refused"
                raise BrowserFault("stale_approval")
            self.state = "running"
            self._task = asyncio.run_coroutine_threadsafe(self._execute_bounded(pending), self._loop)
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
                self._permit = {"url": args["url"], "method": "GET", "used": True, "op": op, "cancelled": threading.Event()}
                cookies = await self._context.cookies(args["url"])
                headers = {"Cookie": "; ".join(c["name"] + "=" + c["value"] for c in cookies)}
                status, response_headers, raw = await asyncio.to_thread(
                    _fetch, args["url"], "GET", headers, None, self.fixture_origin,
                    (self._stopped, self._permit["cancelled"]),
                    {"session_id": self.chat, "private_chat": self.private}, True,
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
                self._permit = {"url": args["url"].split("#")[0], "method": "GET", "used": False, "op": op, "cancelled": threading.Event()}
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
                        self._permit = {"url": args["target"], "method": args["method"], "used": False, "op": op, "cancelled": threading.Event()}
                        async with self._page.expect_navigation(wait_until="domcontentloaded", timeout=TIMEOUT * 1000):
                            await node.press("Enter")
                else:
                    self._permit = {"url": args["target"], "method": args["method"], "used": False, "op": op, "cancelled": threading.Event()}
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
            outcome = "stopped" if self.closed else "ready"
        except Exception as exc:
            outcome = "ready" if self._download else (self._blocked or (str(exc) if isinstance(exc, BrowserFault) else "page_failed"))
        finally:
            if self._permit:
                self._permit["cancelled"].set()
            self._permit = None
            if observe and not self.closed and self._page is not None:
                try:
                    await self._observe()
                except Exception:
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

    async def _observe(self) -> None:
        self.url = self._page.url
        # Fixed source, never a caller/page expression. Stable visible-node handles
        # are regenerated after every action and invalidated by the revision.
        self.nodes = await self._page.evaluate("""() => {
          document.querySelectorAll('[data-vool-live]').forEach(e => e.removeAttribute('data-vool-live'));
          return [...document.querySelectorAll('a,button,input,textarea,select')]
            .filter(e => e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden')
            .slice(0,100).map((e,i) => {
              e.setAttribute('data-vool-live', String(i)); const f=e.form;
              const method=f ? (e.getAttribute('formmethod')||f.method||'GET').toUpperCase() : 'GET';
              let target=e.href || (f ? new URL(e.getAttribute('formaction')||f.action, document.baseURI).href : location.href);
              if(f && method==='GET' && !f.querySelector('input[type=password]')) {
                const address=new URL(target); const fields=new URLSearchParams();
                for(const [key,value] of new FormData(f)) fields.append(key, typeof value==='string' ? value : value.name);
                if(e.type==='submit' && e.name) fields.append(e.name,e.value);
                address.search=fields.toString(); target=address.href;
              }
              return {selector:'[data-vool-live="'+i+'"]', tag:e.tagName.toLowerCase(), name:e.name||'',
                label:(e.innerText||e.getAttribute('aria-label')||e.getAttribute('name')||e.type||'').slice(0,120),
                type:e.type||'', navigates:!!e.href || (!!f && e.type==='submit'),
                target:target.slice(0,4096), target_too_long:target.length>4096, method,
                password_form:!!f && !!f.querySelector('input[type=password]'),
                options:e.tagName==='SELECT' ? [...e.options].slice(0,100).map(o=>({value:o.value,label:o.label.slice(0,120)})) : [],
                payment:!!e.closest('[data-payment]')};
            });
        }""")
        text = (await self._page.locator("body").inner_text())[:50000]
        self.result["page"] = Untrusted.wrap(text, url=self.url, origin=urlsplit(self.url).netloc, title=(await self._page.title())[:300])
        self.frame = base64.b64encode(await self._page.screenshot(type="jpeg", quality=65, timeout=3000)).decode()
        self.revision += 1

    def take_over(self, value: bool) -> dict[str, Any]:
        with self._mutex:
            self.takeover = value
            self.pending = None
            if value and self._task and not self._task.done():
                if self._permit:
                    self._permit["cancelled"].set()
                self._task.cancel()
                settling = asyncio.run_coroutine_threadsafe(self._settle_takeover(), self._loop)
                try:
                    settling.result(timeout=3)
                except Exception as exc:
                    raise BrowserFault("busy") from exc
            elif self.state == "approval_required":
                self.state = "ready"
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
            self.url = ""
            self._document_origin = ""
            self.state = "stopping"
            self._stopped.set()
        future = asyncio.run_coroutine_threadsafe(self._close(), self._loop)
        try:
            future.result(timeout=12)
        except Exception as exc:
            self.cleanup_metrics["failure"] = type(exc).__name__
            self.state = "cleanup_failed"
            raise BrowserFault("cleanup_failed") from exc
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=2)
        if self.profile.exists():
            shutil.rmtree(self.profile)
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
        for pid, created in owned:
            if pid == self._driver_pid and not include_driver:
                continue
            try:
                process = psutil.Process(pid)
                if process.create_time() != created:
                    continue
                process.terminate()
                try:
                    process.wait(timeout=0.5)
                except psutil.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=1)
            except psutil.NoSuchProcess:
                pass

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
            await asyncio.gather(*closing, return_exceptions=True)
        if self._driver:
            self.cleanup_metrics["phase"] = "driver_close"
            stopping = asyncio.create_task(self._driver.stop())
            done, _ = await asyncio.wait({stopping}, timeout=1)
            if not done:
                await asyncio.to_thread(self._stop_owned_processes, True)
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
    from core.remote_fetch_policy import remote_fetch_allowed_by_context
    from core.vool_browser.live_contracts import SUPPORTED

    if intent not in SUPPORTED:
        raise BrowserFault("unsupported")
    chat = str(source_context.get("session_id") or source_context.get("runtime_session_id") or "")
    op = intent.removeprefix("vool-browser.")
    local = op in {"cancel", "session.close", "session.status", "inspect"}
    if not local:
        _check()
        if not remote_fetch_allowed_by_context(source_context):
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
        return item.propose("scroll", {"direction": "up" if arguments["mode"] == "scroll_up" else "down"})
    if op in {"session.status", "inspect"}:
        snap = item.snapshot()
        if op == "session.status":
            snap.pop("frame", None)
            snap.pop("nodes", None)
            snap.pop("result", None)
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
    return item.propose(op, arguments)
