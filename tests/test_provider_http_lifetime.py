import json
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import requests

from core import local_ollama_inventory, provider_http

_REAL_INVENTORY_PAYLOAD = local_ollama_inventory._ollama_models_payload


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


@pytest.fixture
def controlled_ollama_transport(monkeypatch):
    """Real localhost origin plus proxy/redirect trap; neither forwards traffic."""
    servers = []
    threads = []
    state = {"origin_calls": [], "trap_calls": [], "redirect": False}
    # urlopen otherwise reuses a proxy opener from a prior fixture's dead port,
    # making redirect refusal accidentally green for the wrong reason.
    monkeypatch.setattr(urllib.request, "_opener", None)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.respond()

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            self.respond()

        def respond(self):
            state[self.server.role + "_calls"].append((self.command, self.path))
            if self.server.role == "origin" and state["redirect"]:
                self.send_response(302)
                # Public intent is captured by the local proxy on old transport;
                # the fixture never makes an external connection or DNS lookup.
                self.send_header("Location", "http://redirect-target.invalid/escaped")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            payload = {"models": [{"name": "transport-contract:1b", "size": 17}]}
            if self.path.endswith("/api/pull"):
                payload = {"status": "success", "total": 1, "completed": 1}
            elif self.path.endswith("/api/chat"):
                payload = {"message": {"role": "assistant", "content": "transport fixture"}, "done": True}
            body = json.dumps(payload).encode() + b"\n"
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    try:
        for role in ("trap", "origin"):
            http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            http.role = role
            servers.append(http)
            thread = threading.Thread(target=http.serve_forever)
            threads.append(thread)
            thread.start()
            state[role] = f"http://127.0.0.1:{http.server_port}"
        for name in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
            monkeypatch.setenv(name, state["trap"])
        monkeypatch.setenv("NO_PROXY", "")
        monkeypatch.setenv("no_proxy", "")
        monkeypatch.delenv("REQUEST_METHOD", raising=False)
        yield state
    finally:
        for http in servers:
            http.shutdown()
            http.server_close()
        for thread in threads:
            thread.join()


@pytest.mark.parametrize("redirect", [False, True])
@pytest.mark.parametrize("lane", ["inventory", "pull", "prewarm", "inference"])
def test_loopback_transport_never_uses_proxy_or_follows_redirect(controlled_ollama_transport, monkeypatch, workers, lane, redirect):
    from core.web.api.runtime import ModelPullProgress, _stream_ollama_pull
    from tests.test_openai_compatible_adapter import _ollama_adapter, _request

    state = controlled_ollama_transport
    state["redirect"] = redirect
    if redirect:
        monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
        monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    monkeypatch.setenv("VOOL_RAW_OLLAMA_API_URL", state["origin"])
    if lane == "inventory":
        result = _REAL_INVENTORY_PAYLOAD(env={}, base_url=state["origin"], path="/api/tags", timeout_seconds=2)
        assert result is None if redirect else result == [{"name": "transport-contract:1b", "size": 17}]
    elif lane == "pull":
        progress = ModelPullProgress("transport-contract:1b")
        if redirect:
            with pytest.raises(urllib.error.HTTPError, match="loopback provider redirect refused"):
                _stream_ollama_pull("transport-contract:1b", progress=progress)
        else:
            _stream_ollama_pull("transport-contract:1b", progress=progress)
            assert progress.public["percent"] == 100
    else:
        adapter = _ollama_adapter(model_name="transport-contract:1b")
        adapter.manifest.runtime_config["base_url"] = state["origin"]
        if lane == "prewarm":
            adapter.manifest.runtime_config["prewarm"] = {"strategy": "ollama_chat", "timeout_seconds": 2}
            result = adapter.prewarm()
            assert result["ok"] is (not redirect)
        elif redirect:
            with pytest.raises(requests.exceptions.TooManyRedirects):
                adapter.run_text_task(_request("transport contract"))
        else:
            assert adapter.run_text_task(_request("transport contract")).output_text == "transport fixture"
    assert state["origin_calls"], "the genuine selected origin must be reached"
    assert state["trap_calls"] == []


def test_background_model_job_keeps_selected_endpoint_when_process_environment_changes(controlled_ollama_transport, monkeypatch):
    from core import local_ollama_inventory, policy_engine
    from core.web.api.runtime import start_ollama_model_pull

    state = controlled_ollama_transport
    # Old behavior cannot escape externally: its ambient endpoint is this trap.
    monkeypatch.setenv("VOOL_RAW_OLLAMA_API_URL", state["trap"])
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    monkeypatch.setattr(local_ollama_inventory, "_ollama_models_payload", _REAL_INVENTORY_PAYLOAD)
    policy_engine.set_operator_policy_values({"network.outbound_enabled": True, "system.local_only_mode": False})
    progress = start_ollama_model_pull("missing-transport-contract:1b", env={"VOOL_RAW_OLLAMA_API_URL": state["origin"]})
    assert progress.wait(timeout=5)
    assert progress.status == "ready"
    assert state["origin_calls"] == [("GET", "/api/tags"), ("POST", "/api/pull")]
    assert state["trap_calls"] == []


def test_disabled_local_model_job_does_not_probe_inventory_during_default_selection(controlled_ollama_transport, monkeypatch):
    from core.web.api.runtime import start_ollama_model_pull

    state = controlled_ollama_transport
    monkeypatch.setattr(local_ollama_inventory, "_ollama_models_payload", _REAL_INVENTORY_PAYLOAD)
    progress = start_ollama_model_pull(env={
        "VOOL_RAW_OLLAMA_API_URL": state["origin"], "VOOL_LOCAL_MODELS_ENABLED": "0",
        "VOOL_ALLOW_OLLAMA_TAGS_FOR_DEFAULT": "1",
    })
    assert progress.wait(timeout=0) and progress.status == "skipped"
    assert state["origin_calls"] == state["trap_calls"] == []


def test_explicit_remote_proxy_configuration_survives_a_loopback_transfer(controlled_ollama_transport, workers, monkeypatch, tmp_path):
    state = controlled_ollama_transport
    monkeypatch.setenv("NETRC", str(tmp_path / "absent-test-netrc"))
    local = provider_http.get(state["origin"] + "/api/tags", timeout=3)
    assert local.status_code == 200
    assert state["trap_calls"] == []
    # This public URI is handled entirely by the nonforwarding local proxy.
    # It is a transport preservation contract, not a cloud/model acceptance.
    remote = provider_http.get("http://explicit-cloud.invalid/api/tags", timeout=3)
    assert remote.status_code == 200
    assert state["trap_calls"] == [("GET", "http://explicit-cloud.invalid/api/tags")]


def test_loopback_provider_redirect_has_a_typed_refusal(controlled_ollama_transport, workers, monkeypatch):
    state = controlled_ollama_transport
    state["redirect"] = True
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    with pytest.raises(requests.exceptions.TooManyRedirects):
        provider_http.get(state["origin"] + "/api/tags", timeout=3)
    assert state["origin_calls"] == [("GET", "/api/tags")]
    assert state["trap_calls"] == []
