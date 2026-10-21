"""SERVED: Auto with no model set up never sends a request to a dead local model endpoint.

Measured on a fresh install (no local model running, no cloud model chosen): after every turn the
memory fact extractor posted to the dead Ollama ``/api/chat``, and the Activity panel's failure
line read "Task did not meet its completion requirements." for a turn no model could answer.

Real daemons (``apps.vool_api_server``, own process and home). Every Ollama endpoint points at a
local RECORDER that logs each request and hangs up without answering, which is what a machine
with no Ollama looks like to a client (one variant answers the model listing, which is what a
running Ollama looks like). The cloud lane is the SYNTHETIC OpenAI-compatible service of
``tests/test_conversation_intent_served``. The daemons' internet is a dead proxy.

Pinned:
* Auto with nothing set up: no inference request reaches the local endpoint, the reply says no
  local model is running, and the Activity failure line says no model could answer;
* a picked local model is still tried and still says no local model is running;
* a picked cloud model is still called, once;
* with a local model running, the fact extractor still posts to it.
"""
from __future__ import annotations

import json
import socketserver
import threading
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.test_conversation_intent_served import (
    OR_LANE_ID,
    OR_MODEL,
    OpenAICompatibleService,
    OpenRouterCompatibleDaemon,
    _answer_text,
    _or_scripted,
)
from tests.usepod.test_usepod_served_flow import UsePodServedDaemon, _session

DEAD = "http://127.0.0.1:9"
PROMPT = "explain how photosynthesis works in two sentences"
INFERENCE_PATHS = ("/api/chat", "/api/generate", "/v1/chat/completions")


class LocalEndpointRecorder:
    """Logs every request line it receives, then hangs up without a reply. With ``lists_models``
    it answers ``GET /api/tags`` with one chat model, as a running Ollama would."""

    def __init__(self, *, lists_models: bool = False) -> None:
        self.requests: list[str] = []
        self.bodies: list[tuple[str, str]] = []
        self.lock = threading.Lock()
        recorder = self

        class _Handler(socketserver.BaseRequestHandler):
            def handle(self) -> None:
                self.request.settimeout(5)
                data = b""
                try:
                    while b"\r\n\r\n" not in data:
                        chunk = self.request.recv(4096)
                        if not chunk:
                            break
                        data += chunk
                except OSError:
                    pass
                head, _, body = data.partition(b"\r\n\r\n")
                line = head.split(b"\r\n", 1)[0].decode("latin-1", "replace")
                if not line:
                    return
                length = 0
                for header in head.split(b"\r\n")[1:]:
                    name, _, value = header.partition(b":")
                    if name.strip().lower() == b"content-length":
                        length = int(value.strip() or 0)
                try:
                    while len(body) < length:
                        chunk = self.request.recv(65536)
                        if not chunk:
                            break
                        body += chunk
                except OSError:
                    pass
                with recorder.lock:
                    recorder.requests.append(line)
                    recorder.bodies.append((line, body.decode("utf-8", "replace")))
                if lists_models and line.startswith("GET /api/tags"):
                    body = json.dumps({"models": [{"name": "qwen3:4b", "details": {"families": ["qwen3"]}}]}).encode()
                    self.request.sendall(
                        b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "
                        + str(len(body)).encode() + b"\r\nConnection: close\r\n\r\n" + body
                    )

        self._server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _Handler)
        self._server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}"
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()

    def inference_requests(self) -> list[str]:
        with self.lock:
            return [line for line in self.requests if line.startswith("POST ") and any(path in line for path in INFERENCE_PATHS)]


def _local_endpoints(env: dict[str, str], url: str) -> dict[str, str]:
    env.update({
        "OLLAMA_HOST": url,
        "VOOL_OLLAMA_URL": url,
        "VOOL_RAW_OLLAMA_API_URL": url,
        "VOOL_OLLAMA_CHAT_URL": f"{url}/api/chat",
        "VOOL_OLLAMA_PS_URL": f"{url}/api/ps",
        "VOOL_OLLAMA_TAGS_URL": f"{url}/api/tags",
    })
    loopback = "127.0.0.1,localhost,::1"
    for name in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
        env[name] = DEAD
    env["NO_PROXY"] = env["no_proxy"] = loopback
    return env


class BareDaemon(UsePodServedDaemon):
    """No provider configured; the local endpoints are the recorder."""

    def __init__(self, home: Path, recorder: LocalEndpointRecorder) -> None:
        super().__init__(home)
        self.recorder = recorder

    def env(self) -> dict[str, str]:
        return _local_endpoints(super().env(), self.recorder.url)

    def turn(self, text: str, session_id: str, model: str = "") -> tuple[int, object]:
        payload = {"messages": [{"role": "user", "content": text}], "stream": False, "session_id": session_id, "mode": "auto"}
        if model:
            payload["model"] = model
        return self.call("POST", "/api/chat", payload, timeout=300.0)


class CloudDaemon(OpenRouterCompatibleDaemon):
    def __init__(self, home: Path, service: OpenAICompatibleService, recorder: LocalEndpointRecorder) -> None:
        super().__init__(home, service)
        self.recorder = recorder

    def env(self) -> dict[str, str]:
        return _local_endpoints(super().env(), self.recorder.url)


def _settle(recorder: LocalEndpointRecorder, before: int, seconds: float) -> list[str]:
    """The fact extractor runs on its own thread after the reply: wait for it to reach the wire,
    or for ``seconds`` to pass, and return the inference requests seen since ``before``."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline and not recorder.inference_requests()[before:]:
        time.sleep(0.5)
    return recorder.inference_requests()[before:]


@pytest.fixture(scope="module")
def bare(tmp_path_factory):
    recorder = LocalEndpointRecorder()
    daemon = BareDaemon(tmp_path_factory.mktemp("auto-no-model") / "home", recorder)
    try:
        daemon.start()
        yield SimpleNamespace(daemon=daemon, recorder=recorder)
    finally:
        daemon.stop()
        recorder.stop()


def test_auto_with_nothing_set_up_sends_nothing_to_the_local_endpoint(bare) -> None:
    session = _session(f"auto-no-model-{uuid.uuid4()}")
    before = len(bare.recorder.inference_requests())
    status, answer = bare.daemon.turn(PROMPT, session)
    reached = _settle(bare.recorder, before, seconds=8.0)
    events = bare.daemon.events(session)
    reply = _answer_text(answer)
    assert status == 200, answer
    assert reached == [], f"inference requests reached the dead local endpoint: {reached}"
    assert "no local model is running" in reply, reply
    assert not [e for e in events if e.get("event_type") == "model.call_started"], "no model call may start"
    failed = [e for e in events if e.get("event_type") == "task_failed"]
    assert failed and failed[-1].get("message") == "No model was available to answer this message.", failed


def test_a_picked_local_model_is_still_tried_and_says_it_is_not_running(bare) -> None:
    session = _session(f"picked-local-{uuid.uuid4()}")
    status, answer = bare.daemon.turn(PROMPT, session, model="ollama-local:qwen3:4b")
    events = bare.daemon.events(session)
    reply = _answer_text(answer)
    assert status == 200, answer
    selected = [e for e in events if e.get("event_type") == "model_lane_selected"]
    assert selected and selected[-1].get("provider_id") == "ollama-local:qwen3:4b", [e.get("message") for e in selected]
    assert "no local model is running" in reply, reply


@pytest.fixture(scope="module")
def cloud(tmp_path_factory):
    recorder = LocalEndpointRecorder()
    service = OpenAICompatibleService().start()
    service.reply = _or_scripted
    daemon = CloudDaemon(tmp_path_factory.mktemp("picked-cloud") / "home", service, recorder)
    try:
        daemon.start()
        status, catalog = daemon.call("GET", "/api/cloud/models?provider=openrouter")
        assert status == 200, catalog
        status, pinned = daemon.call("POST", "/api/cloud/model", {"model": OR_MODEL, "provider": "openrouter", "confirm_paid": True})
        assert status == 200 and pinned.get("ok") is True, pinned
        status, certified = daemon.call(
            "POST", "/api/model-tool-certification/run", {"provider_name": "openrouter-byok", "model_name": OR_MODEL}, timeout=420.0
        )
        assert status == 200, certified
        yield SimpleNamespace(daemon=daemon, service=service, recorder=recorder)
    finally:
        daemon.stop()
        service.stop()
        recorder.stop()


def test_a_picked_cloud_model_is_still_called(cloud) -> None:
    session = _session(f"picked-cloud-{uuid.uuid4()}")
    before = len(cloud.service.inference_requests())
    status, answer = cloud.daemon.chat("Name three primary colours.", session_id=session)
    arrived = cloud.service.inference_requests()[before:]
    events = cloud.daemon.events(session)
    assert status == 200, answer
    assert len(arrived) == 1 and arrived[0].get("model") == OR_MODEL, len(arrived)
    assert {e.get("provider_id") for e in events if e.get("event_type") == "model.call_started"} == {OR_LANE_ID}
    assert _answer_text(answer).startswith("synthetic openrouter reply"), _answer_text(answer)


@pytest.fixture(scope="module")
def running(tmp_path_factory):
    recorder = LocalEndpointRecorder(lists_models=True)
    daemon = BareDaemon(tmp_path_factory.mktemp("local-running") / "home", recorder)
    try:
        daemon.start()
        yield SimpleNamespace(daemon=daemon, recorder=recorder)
    finally:
        daemon.stop()
        recorder.stop()


def test_with_a_local_model_running_the_fact_extractor_still_posts_to_it(running) -> None:
    session = _session(f"local-running-{uuid.uuid4()}")
    before = len(running.recorder.inference_requests())
    status, answer = running.daemon.turn("My favourite tree is the silver birch.", session)
    posted = _settle(running.recorder, before, seconds=20.0)
    assert status == 200, answer
    assert any("/api/chat" in line for line in posted), running.recorder.requests
    with running.recorder.lock:
        extractor_posts = [body for line, body in running.recorder.bodies if "/api/chat" in line and "memory extraction agent" in body]
    assert extractor_posts, "the fact extractor's request never reached the running local model"
