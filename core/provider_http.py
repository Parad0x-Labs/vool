"""Requests-compatible provider I/O with a parent-owned transfer lifetime.

Socket inactivity timeouts are not deadlines. Only the HTTP transfer runs in a
spawned worker; admission, credentials, interpretation and publication stay in
the runtime. No request data is placed in argv, environment or temporary files.
"""
from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
import os
import queue
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import suppress
from pathlib import Path

import requests as _requests
from requests import HTTPError as HTTPError
from requests import exceptions as exceptions

_MAX_BODY = 64 * 1024 * 1024
_MAX_FRAME = 128 * 1024


class ProviderCallCancelled(RuntimeError):
    """The caller's ``cancel_check`` came true: the transfer worker is ended, no reply is read.

    Carries the adapters' stable ``model_call_cancelled`` code, so a stopped call is never taken
    for a transport failure of the provider.
    """

    def __init__(self) -> None:
        super().__init__("model_call_cancelled")


def is_loopback_url(url):
    try:
        parsed = urllib.parse.urlparse(str(url))
        host = str(parsed.hostname or "").lower()
        if parsed.scheme not in {"http", "https"}:
            return False
        if host == "localhost":
            return True
        return ipaddress.ip_address(host).is_loopback
    except (TypeError, ValueError):
        return False


class _RefuseLoopbackRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, "loopback provider redirect refused", headers, fp)


def urlopen(request, *, timeout, no_redirects=False, no_proxy=False):
    """Local provider calls use direct sockets; reviewed calls refuse redirects."""
    url = getattr(request, "full_url", request)
    if is_loopback_url(url) or no_proxy:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _RefuseLoopbackRedirect())
        return opener.open(request, timeout=timeout)
    route = _private_route()
    if route is not None:
        if route.uses_socks:
            from core import private_web

            handlers = [urllib.request.ProxyHandler({}), *private_web.socks_http_handlers(route, isolation="vool")]
            if no_redirects:
                # A reviewed request is the approved bytes to the approved host: no redirect, on any route.
                handlers.append(_RefuseLoopbackRedirect())
            return urllib.request.build_opener(*handlers).open(request, timeout=timeout)
    if no_redirects:
        return urllib.request.build_opener(_RefuseLoopbackRedirect()).open(request, timeout=timeout)
    return urllib.request.urlopen(request, timeout=timeout)


class PrivateWebUnavailable(_requests.exceptions.ConnectionError):
    """Private web is on and its route is down: the call was not made. The message is plain."""


def _private_route():
    """The Private web route in force, or None when it is off. Raises when it is on but down."""
    try:
        from core import private_web
    except ImportError:  # the worker process, which has no package path
        return None
    route = private_web.current_route()
    if not route.active:
        return None
    problem = private_web.route_problem(route)
    if problem:
        raise PrivateWebUnavailable(problem)
    return route


def _route_for_worker(url):
    """What the worker must do with the route: nothing for loopback or Private web off."""
    if is_loopback_url(url):
        return None
    route = _private_route()
    if route is None:
        return None
    if route.uses_socks:
        return {"kind": route.kind, "socks_port": int(route.socks_port)}
    # A checked system VPN: traffic leaves directly through the person's VPN.
    return None


class _Transfer:
    def __init__(self, method, url, options, cancel_check=None):
        # Opt-in and parent-side only: never serialized to the worker. None leaves the transfer
        # exactly as it was.
        self.cancel_check = cancel_check
        timeout = options.get("timeout", 30.0)
        budget = timeout[-1] if isinstance(timeout, (tuple, list)) else timeout
        self.deadline = time.monotonic() + max(0.001, float(budget))
        self.stopped = threading.Event()
        self.expired = threading.Event()
        self.lock = threading.Lock()
        self.frames = queue.Queue(maxsize=8)
        # Decided BEFORE a worker exists: a route that is down refuses here, with nothing spawned.
        route = _route_for_worker(url)
        wire = json.dumps({"method": method, "url": url, "options": options,
                           "deadline": self.deadline, "private_route": route}).encode()
        env = dict(os.environ)
        for name in ("PYTHONPATH", "PYTHONSTARTUP", "PYTHONINSPECT"):
            env.pop(name, None)
        self.process = subprocess.Popen(
            [sys.executable, "-u", "-B", "-s", "-P", str(Path(__file__).resolve()), "--worker"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            env=env,
        )
        self.writer = threading.Thread(target=self._write, args=(wire,), name="provider-http-input")
        self.reader = threading.Thread(target=self._read, name="provider-http-output")
        self.watchdog = threading.Thread(target=self._watch, name="provider-http-deadline")
        try:
            self.writer.start()
            self.reader.start()
            self.watchdog.start()
        except BaseException:
            self.close()
            raise

    def _put(self, frame):
        while not self.stopped.is_set():
            try:
                self.frames.put(frame, timeout=0.05)
                return
            except queue.Full:
                continue

    def _write(self, wire):
        try:
            self.process.stdin.write(wire)
            self.process.stdin.close()
        except (OSError, ValueError):
            self._put({"type": "error", "class": "ConnectionError"})

    def _read(self):
        try:
            while not self.stopped.is_set():
                line = self.process.stdout.readline(_MAX_FRAME + 1)
                if not line:
                    self._put({"type": "eof"})
                    return
                if len(line) > _MAX_FRAME:
                    raise ValueError("oversized worker frame")
                self._put(json.loads(line))
        except (OSError, ValueError):
            self._put({"type": "error", "class": "ConnectionError"})

    def _terminate(self):
        with self.lock:
            if self.process.poll() is None:
                self.process.kill()
            self.process.wait(timeout=2.0)

    def _watch(self):
        if not self.stopped.wait(max(0, self.deadline - time.monotonic())):
            self.expired.set()
            self.stopped.set()
            self._terminate()

    def receive(self):
        while True:
            if self.cancel_check is not None and self.cancel_check():
                self.close()
                raise ProviderCallCancelled()
            remaining = self.deadline - time.monotonic()
            if self.expired.is_set() or remaining <= 0:
                raise exceptions.Timeout("provider transfer exceeded its wall-clock deadline")
            try:
                frame = self.frames.get(timeout=min(remaining, 0.05))
            except queue.Empty:
                continue
            if frame.get("type") == "error":
                error = frame.get("class")
                cls = (
                    exceptions.Timeout if error in {"Timeout", "ReadTimeout", "ConnectTimeout"}
                    else exceptions.TooManyRedirects if error == "TooManyRedirects"
                    else exceptions.ConnectionError
                )
                raise cls("provider HTTP worker failed: " + str(error))
            if frame.get("type") == "eof":
                if time.monotonic() >= self.deadline or self.process.poll() == 124:
                    raise exceptions.Timeout("provider transfer exceeded its wall-clock deadline")
                raise exceptions.ConnectionError("provider HTTP worker exited before completing its response")
            return frame

    def close(self):
        self.stopped.set()
        self._terminate()
        for thread in (self.writer, self.reader, self.watchdog):
            if thread.ident is not None and thread is not threading.current_thread():
                thread.join(timeout=2.0)
                if thread.is_alive():
                    raise RuntimeError("provider HTTP worker cleanup did not finish")
        for pipe in (self.process.stdin, self.process.stdout):
            with suppress(OSError):
                pipe.close()


class _Response(_requests.Response):
    def __init__(self, owner, header):
        super().__init__()
        self.owner = owner
        self.status_code = int(header["status"])
        self.headers.update(header["headers"])
        self.url = header["url"]
        self.encoding = _requests.utils.get_encoding_from_headers(self.headers)

    @property
    def content(self):
        if self._content is False:
            if self._content_consumed:
                raise exceptions.StreamConsumedError()
            self._content = b"".join(self.iter_content())
        return self._content

    def iter_content(self, chunk_size=1, decode_unicode=False):
        if self._content_consumed:
            if isinstance(self._content, bool):
                raise exceptions.StreamConsumedError()
            iterator = _requests.utils.iter_slices(self._content, chunk_size)
            return _requests.utils.stream_decode_response_unicode(iterator, self) if decode_unicode else iterator

        def chunks():
            total = 0
            try:
                while True:
                    frame = self.owner.receive()
                    if frame["type"] == "done":
                        self._content_consumed = True
                        return
                    if frame["type"] != "chunk":
                        raise exceptions.ConnectionError("unexpected provider worker frame")
                    chunk = base64.b64decode(frame["data"], validate=True)
                    total += len(chunk)
                    if total > _MAX_BODY:
                        raise exceptions.ConnectionError("provider response exceeded the body limit")
                    yield chunk
            finally:
                self.close()
        iterator = chunks()
        return _requests.utils.stream_decode_response_unicode(iterator, self) if decode_unicode else iterator

    def close(self):
        self.owner.close()


def _request(method, url, **options):
    from core.disclosure.wire import DisclosureRefused, WirePayload

    payload = options.get("json")
    preview_id = ""
    if isinstance(payload, WirePayload):
        wire = payload.consume_wire(url)
        preview_id = payload.preview_id
        options.pop("json")
        options["_disclosure_body_b64"] = base64.b64encode(wire).decode("ascii")
        options["_disclosure_sha256"] = hashlib.sha256(wire).hexdigest()
        options["_disclosure_no_proxy"] = payload.cloud_destination is False
        options["headers"] = {**dict(options.get("headers") or {}), "Content-Type": "application/json"}
        # A reviewed destination cannot redirect the body to an unreviewed one.
        options["allow_redirects"] = False
    elif not is_loopback_url(url) and isinstance(payload, dict) and ("messages" in payload or "prompt" in payload):
        raise DisclosureRefused("Cloud model requests must pass the disclosure boundary.")
    streaming = bool(options.pop("stream", False))
    # ``cancel_check``: a callable the transfer polls while it waits; when it returns true the
    # worker is ended and ProviderCallCancelled is raised. A check that raises ends the call too.
    cancel_check = options.pop("cancel_check", None)
    owner = _Transfer(method, url, options, cancel_check)
    try:
        header = owner.receive()
        if header.get("type") != "response":
            raise exceptions.ConnectionError("provider worker did not return response headers")
        if preview_id:
            from core.disclosure.wire import note_sent

            note_sent(preview_id)
        if isinstance(payload, WirePayload) and 300 <= header.get("status", 0) < 400:
            raise exceptions.TooManyRedirects("reviewed provider redirect refused")
        response = _Response(owner, header)
        if not streaming:
            _ = response.content
        return response
    except BaseException:
        owner.close()
        raise


def post(url, **options):
    return _request("POST", url, **options)


def get(url, **options):
    return _request("GET", url, **options)


#: The only caller headers that may follow a redirect to ANOTHER origin. ``requests`` strips
#: ``Authorization`` there by itself, but a key placed in any other header (a custom endpoint's
#: ``api-key`` / ``x-api-key``) would be re-sent to whatever host the redirect names.
_CROSS_ORIGIN_SAFE_HEADERS = frozenset({"accept", "accept-encoding", "content-type", "user-agent"})


class _KeySafeSession(_requests.Session):
    def rebuild_auth(self, prepared_request, response):
        super().rebuild_auth(prepared_request, response)
        if self.should_strip_auth(response.request.url, prepared_request.url):
            for name in [n for n in prepared_request.headers if n.lower() not in _CROSS_ORIGIN_SAFE_HEADERS]:
                del prepared_request.headers[name]


def _worker():
    def emit(frame):
        print(json.dumps(frame, separators=(",", ":")), flush=True)

    timer = None
    try:
        invocation = json.load(sys.stdin)
        # If the runtime itself exits, the transfer still cannot outlive its grant.
        timer = threading.Timer(max(0, invocation["deadline"] - time.monotonic()), lambda: os._exit(124))
        timer.daemon = True
        timer.start()
        options = invocation["options"]
        if "_disclosure_body_b64" in options:
            body = base64.b64decode(options.pop("_disclosure_body_b64"), validate=True)
            if hashlib.sha256(body).hexdigest() != options.pop("_disclosure_sha256"):
                raise ValueError("provider body changed after preview")
            options["data"] = body
        if isinstance(options.get("timeout"), list):
            options["timeout"] = tuple(options["timeout"])
        direct_local = options.pop("_disclosure_no_proxy", False) is True
        loopback = is_loopback_url(invocation["url"]) or direct_local
        # Private web stays the authority for every non-loopback socket: the parent decided the
        # route before this worker existed, and a local-model destination still takes it.
        private_route = invocation.get("private_route")
        if private_route:
            _worker_use_private_route(private_route)
        return_redirect = options.get("allow_redirects") is False
        # Each transfer owns its session. A local invocation never changes the
        # proxy/redirect behavior of a later explicitly authorized cloud call.
        with _KeySafeSession() as session:
            if loopback or private_route:
                session.trust_env = False
            if private_route:
                options.pop("proxies", None)
            if loopback:
                session.trust_env = False
                options.pop("proxies", None)
                options["allow_redirects"] = False
            with session.request(invocation["method"], invocation["url"], stream=True, **options) as response:
                if loopback and response.is_redirect and not return_redirect:
                    raise exceptions.TooManyRedirects("loopback provider redirect refused")
                emit({"type": "response", "status": response.status_code,
                      "headers": dict(response.headers), "url": response.url})
                for chunk in response.iter_content(chunk_size=512):
                    emit({"type": "chunk", "data": base64.b64encode(chunk).decode("ascii")})
                emit({"type": "done"})
    except Exception as exc:
        emit({"type": "error", "class": type(exc).__name__})
    finally:
        if timer is not None:
            timer.cancel()


def _worker_use_private_route(spec):
    """Inside the worker: every socket goes through the route's SOCKS port, by name, and the
    egress guard stops anything that would leave directly. The worker has no package path, so
    the route module is loaded from its file next to this one."""
    import importlib.util

    import urllib3.util.connection as urllib3_connection

    module_path = Path(__file__).resolve().with_name("private_web.py")
    loader_spec = importlib.util.spec_from_file_location("vool_private_web_worker", module_path)
    private_web = importlib.util.module_from_spec(loader_spec)
    sys.modules["vool_private_web_worker"] = private_web
    loader_spec.loader.exec_module(private_web)
    route = private_web.RouteState(kind=str(spec["kind"]), socks_port=int(spec["socks_port"]))
    if not route.uses_socks or not route.socks_port:
        raise exceptions.ConnectionError("private route is not usable")
    private_web.set_route_for_process(route)
    private_web.install_egress_guard(refresh_state=False)

    import socket as _socket

    from urllib3.util.timeout import _DEFAULT_TIMEOUT

    def create_connection(address, timeout=_DEFAULT_TIMEOUT, source_address=None, socket_options=None):
        host, port = address
        if timeout is _DEFAULT_TIMEOUT:
            timeout = _socket._GLOBAL_DEFAULT_TIMEOUT
        sock = private_web.socks5_connect(route.endpoint, str(host), int(port), timeout=timeout, isolation="vool")
        for option in socket_options or ():
            with suppress(OSError):
                sock.setsockopt(*option)
        return sock

    urllib3_connection.create_connection = create_connection


if __name__ == "__main__" and sys.argv[1:] == ["--worker"]:
    _worker()
