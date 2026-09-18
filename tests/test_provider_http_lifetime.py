import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import requests

from core import provider_http


@pytest.fixture
def server():
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            self.server.requests.append(self.path)
            try:
                if self.path == "/headers":
                    for _ in range(80):
                        if self.server.stopped.wait(0.03):
                            break
                        self.wfile.write(b" ")
                        self.wfile.flush()
                    return
                self.send_response(400 if self.path == "/error" else 200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                if self.path in {"/slow", "/idle"}:
                    for _ in range(120):
                        if self.server.stopped.wait(0.02):
                            break
                        self.wfile.write(b" ")
                        self.wfile.flush()
                elif self.path == "/sse":
                    self.wfile.write('data: {"text":"caf\u00e9"}\n\ndata: [DONE]\n\n'.encode())
                else:
                    self.wfile.write(json.dumps({"message": "hello", "number": 17}).encode())
            except (BrokenPipeError, ConnectionResetError):
                pass

    http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    http.stopped = threading.Event()
    http.requests = []
    thread = threading.Thread(target=http.serve_forever)
    thread.start()
    yield http, f"http://127.0.0.1:{http.server_port}"
    http.stopped.set()
    http.shutdown()
    http.server_close()
    thread.join()


@pytest.fixture
def workers(monkeypatch):
    processes = []
    spawn = provider_http.subprocess.Popen

    def tracked(*args, **kwargs):
        process = spawn(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(provider_http.subprocess, "Popen", tracked)
    yield processes
    assert all(process.poll() is not None for process in processes)
    assert not [t for t in threading.enumerate() if t.name.startswith("provider-http-")]


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("path", ["/slow", "/headers"])
def test_trickling_body_or_headers_cannot_extend_transfer_deadline(server, workers, streaming, path):
    http, url = server
    started = time.monotonic()
    with pytest.raises(requests.Timeout):
        response = provider_http.post(url + path, json={"prompt": "probe"}, timeout=0.7, stream=streaming)
        list(response.iter_lines())
    assert http.requests == [path], "the test must reach the real socket, not expire during startup"
    assert time.monotonic() - started < 1.3


def test_json_status_and_secret_transport_remain_compatible(server, workers):
    _, url = server
    response = provider_http.post(url + "/error", json={"prompt": "probe"},
                                  headers={"Authorization": "Bearer synthetic-secret"}, timeout=3)
    assert response.json() == {"message": "hello", "number": 17}
    with pytest.raises(requests.HTTPError):
        response.raise_for_status()
    assert "synthetic-secret" not in repr(workers[0].args)
    assert "-P" in workers[0].args, "core/operator must not shadow the standard library"
    response.close()


def test_unread_stream_expires_and_reaps_worker(server, workers):
    _, url = server
    response = provider_http.post(url + "/idle", json={}, timeout=0.7, stream=True)
    time.sleep(0.85)
    assert workers[0].poll() is not None
    with pytest.raises(requests.Timeout):
        list(response.iter_lines())
    response.close()


def test_early_close_reaps_worker_without_waiting_for_deadline(server, workers):
    _, url = server
    response = provider_http.post(url + "/idle", json={}, timeout=10, stream=True)
    started = time.monotonic()
    response.close()
    assert time.monotonic() - started < 0.5


def test_worker_has_its_own_deadline_if_parent_watchdog_is_gone(server, workers, monkeypatch):
    _, url = server
    monkeypatch.setattr(provider_http._Transfer, "_watch", lambda self: None)
    response = provider_http.post(url + "/idle", json={}, timeout=0.7, stream=True)
    time.sleep(0.85)
    assert workers[0].poll() == 124
    response.close()


def test_stream_preserves_unicode_and_frame_boundaries(server, workers):
    _, url = server
    response = provider_http.post(url + "/sse", json={}, timeout=3, stream=True)
    assert list(response.iter_lines(decode_unicode=True)) == [
        'data: {"text":"caf\u00e9"}', "", "data: [DONE]", "",
    ]


def test_partial_thread_start_failure_reaps_the_created_worker(server, workers, monkeypatch):
    _, url = server
    start = threading.Thread.start

    def fail_reader(thread):
        if thread.name == "provider-http-output":
            raise RuntimeError("reader startup failed")
        return start(thread)

    monkeypatch.setattr(threading.Thread, "start", fail_reader)
    with pytest.raises(RuntimeError, match="reader startup failed"):
        provider_http.post(url, json={}, timeout=3)
