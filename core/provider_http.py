"""Requests-compatible provider I/O with a parent-owned transfer lifetime.

Socket inactivity timeouts are not deadlines. Only the HTTP transfer runs in a
spawned worker; admission, credentials, interpretation and publication stay in
the runtime. No request data is placed in argv, environment or temporary files.
"""
from __future__ import annotations

import base64
import json
import os
import queue
import subprocess
import sys
import threading
import time
from contextlib import suppress
from pathlib import Path

import requests as _requests
from requests import HTTPError as HTTPError
from requests import exceptions as exceptions

_MAX_BODY = 64 * 1024 * 1024
_MAX_FRAME = 128 * 1024


class _Transfer:
    def __init__(self, method, url, options):
        timeout = options.get("timeout", 30.0)
        budget = timeout[-1] if isinstance(timeout, (tuple, list)) else timeout
        self.deadline = time.monotonic() + max(0.001, float(budget))
        self.stopped = threading.Event()
        self.expired = threading.Event()
        self.lock = threading.Lock()
        self.frames = queue.Queue(maxsize=8)
        wire = json.dumps({"method": method, "url": url, "options": options,
                           "deadline": self.deadline}).encode()
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
            remaining = self.deadline - time.monotonic()
            if self.expired.is_set() or remaining <= 0:
                raise exceptions.Timeout("provider transfer exceeded its wall-clock deadline")
            try:
                frame = self.frames.get(timeout=min(remaining, 0.05))
            except queue.Empty:
                continue
            if frame.get("type") == "error":
                error = frame.get("class")
                cls = exceptions.Timeout if error in {"Timeout", "ReadTimeout", "ConnectTimeout"} else exceptions.ConnectionError
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
    streaming = bool(options.pop("stream", False))
    owner = _Transfer(method, url, options)
    try:
        header = owner.receive()
        if header.get("type") != "response":
            raise exceptions.ConnectionError("provider worker did not return response headers")
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
        if isinstance(options.get("timeout"), list):
            options["timeout"] = tuple(options["timeout"])
        with _requests.request(invocation["method"], invocation["url"], stream=True, **options) as response:
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


if __name__ == "__main__" and sys.argv[1:] == ["--worker"]:
    _worker()
