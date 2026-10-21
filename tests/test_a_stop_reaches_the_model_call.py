"""A Stop reaches the model call that is in flight, so the next message does not wait for it.

Measured on the 0.7.2 train (153148eb), served daemon + real browser: after a Stop, the next
message in the same chat waited ~57 s. The stopped turn's model call (a blocking POST) ran on
until the model answered, because the cancel was only looked at before and after the call; the
turn held the chat the whole time.

The provider transfer already runs in a worker process the runtime owns. A call that carries the
person's cancel now ends that worker as soon as the cancel is set and raises the adapters' own
``model_call_cancelled``. A call without a cancel check behaves exactly as before, and an expired
turn deadline keeps ending the call through its own timeout, not as a cancel.
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request, urlopen

import pytest
import requests

import tests._reader_served_rig as rig
from adapters.base_adapter import ModelRequest
from core import provider_http
from core.provider_call_deadline import PROVIDER_DEADLINE_KEY
from core.runtime_active_clock import monotonic as active_monotonic


@pytest.fixture
def held_server():
    """``/hold`` answers only when released (headers too, like a buffered Ollama reply);
    ``/trickle`` sends its headers at once and then a byte now and then."""

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            self.server.seen.append(self.path)
            try:
                if self.path == "/trickle":
                    self.send_response(200)
                    self.send_header("Content-Type", "application/x-ndjson")
                    self.end_headers()
                    for _ in range(300):
                        if self.server.release.wait(0.1):
                            break
                        self.wfile.write(b"{}\n")
                        self.wfile.flush()
                    return
                if self.path == "/hold":
                    self.server.release.wait(30)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"message": "late"}).encode())
            except (BrokenPipeError, ConnectionResetError):
                pass

    http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    http.release = threading.Event()
    http.seen = []
    thread = threading.Thread(target=http.serve_forever)
    thread.start()
    yield http, f"http://127.0.0.1:{http.server_port}"
    http.release.set()
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
    assert all(process.poll() is not None for process in processes), "a transfer worker outlived its call"
    assert not [t for t in threading.enumerate() if t.name.startswith("provider-http-")]


def _cancel_after(seconds: float) -> threading.Event:
    flag = threading.Event()
    threading.Timer(seconds, flag.set).start()
    return flag


def test_a_cancel_ends_a_buffered_call_that_is_waiting_for_its_answer(held_server, workers) -> None:
    http, url = held_server
    flag = _cancel_after(0.4)
    started = time.monotonic()
    with pytest.raises(RuntimeError, match=r"^model_call_cancelled$"):
        provider_http.post(url + "/hold", json={"prompt": "p"}, timeout=20, cancel_check=flag.is_set)
    assert time.monotonic() - started < 2.0
    assert http.seen == ["/hold"], "the call must have reached the socket before the cancel"
    assert workers and workers[0].poll() is not None, "the worker process is gone the moment the call ends"


def test_a_cancel_ends_a_streamed_call_between_two_lines(held_server, workers) -> None:
    _, url = held_server
    flag = threading.Event()
    response = provider_http.post(url + "/trickle", json={}, timeout=20, stream=True, cancel_check=flag.is_set)
    lines = response.iter_lines()
    next(lines)
    flag.set()
    started = time.monotonic()
    with pytest.raises(RuntimeError, match=r"^model_call_cancelled$"):
        list(lines)
    assert time.monotonic() - started < 1.0
    assert workers[0].poll() is not None


def test_a_call_whose_cancel_is_never_set_answers_as_before(held_server, workers) -> None:
    http, url = held_server
    threading.Timer(0.3, http.release.set).start()
    response = provider_http.post(url + "/hold", json={}, timeout=20, cancel_check=lambda: False)
    assert response.status_code == 200 and response.json() == {"message": "late"}


def test_a_cancel_check_that_breaks_ends_the_call_instead_of_leaving_it_unstoppable(held_server, workers) -> None:
    _, url = held_server

    def broken() -> bool:
        raise ValueError("cancel state unreadable")

    with pytest.raises(ValueError, match="cancel state unreadable"):
        provider_http.post(url + "/hold", json={}, timeout=20, cancel_check=broken)


def test_only_the_persons_cancel_ends_the_call_an_expired_deadline_stays_a_timeout() -> None:
    from adapters.openai_compatible_adapter import _person_cancel_check

    cancelled = threading.Event()
    live = ModelRequest(task_kind="chat", prompt="p", cancel_check=cancelled.is_set,
                        metadata={PROVIDER_DEADLINE_KEY: active_monotonic() + 60})
    check = _person_cancel_check(live)
    assert check is not None and check() is False
    cancelled.set()
    assert check() is True
    # The router's cancel check also turns true once the turn deadline passes. That expiry keeps
    # ending the call through its clamped transport timeout (a typed timeout), never as a cancel.
    expired = ModelRequest(task_kind="chat", prompt="p", cancel_check=lambda: True,
                           metadata={PROVIDER_DEADLINE_KEY: active_monotonic() - 1})
    assert _person_cancel_check(expired)() is False
    # No cancel check at all: the transfer is called exactly as before, with no cancel option.
    assert _person_cancel_check(ModelRequest(task_kind="chat", prompt="p")) is None


# ---------------------------------------------------------------------------------------------
# The router seam: a request built without the turn's cancel (the conductor's own calls, measured
# with cancel_check=None on the stopped turn) still stops with the turn.
# ---------------------------------------------------------------------------------------------


def _request_seen_by_the_adapter(request: ModelRequest, source_context: dict) -> ModelRequest:
    from types import SimpleNamespace
    from unittest import mock

    from adapters.base_adapter import ModelResponse
    from core.final_answer_authorship import AuthorCertification
    from core.memory_first_router import MemoryFirstRouter
    from storage.model_provider_manifest import ModelProviderManifest

    manifest = ModelProviderManifest(
        provider_name="cancel-seam", model_name="local-test", source_type="http", adapter_type="openai_compatible",
        license_name="test", license_reference="test", weight_location="external", runtime_dependency="http",
        capabilities=["summarize"], runtime_config={"base_url": "http://127.0.0.1:9"}, metadata={"deployment_class": "local"},
    )
    seen: list[ModelRequest] = []
    adapter = mock.Mock()
    adapter.health_check.return_value = {"ok": True}
    adapter.supports_streaming.return_value = False

    def run_text_task(req):
        seen.append(req)
        return ModelResponse(output_text="answer")

    adapter.run_text_task.side_effect = run_text_task
    router = MemoryFirstRouter()
    with mock.patch.object(router.registry, "build_adapter", return_value=adapter), mock.patch(
        "core.memory_first_router.should_probe_health", return_value=False
    ), mock.patch(
        "core.final_answer_authorship.author_certification",
        return_value=AuthorCertification(True, "passed", "measured_tool_certification", manifest.provider_id),
    ):
        _, response, error = router._invoke_manifest(
            manifest=manifest, request=request, output_mode="plain_text",
            task=SimpleNamespace(task_id="task-cancel-seam"), source_context=source_context,
        )
    assert error is None and response is not None and len(seen) == 1, error
    return seen[0]


def test_a_conductor_request_without_a_cancel_check_stops_with_the_turns_cancel_only() -> None:
    cancel = threading.Event()
    context = {"cancel_event": cancel}
    seen = _request_seen_by_the_adapter(ModelRequest(task_kind="normalization_assist", prompt="p"), context)
    assert seen.cancel_check is not None and seen.is_cancelled() is False
    # The turn deadline passing is not a cancel: it keeps ending the call the way it always has.
    context[PROVIDER_DEADLINE_KEY] = active_monotonic() - 1
    seen.metadata[PROVIDER_DEADLINE_KEY] = active_monotonic() - 1
    assert seen.is_cancelled() is False
    assert _person_cancel_check_of(seen)() is False
    cancel.set()
    assert seen.is_cancelled() is True


def test_a_request_with_its_own_cancel_check_keeps_it() -> None:
    def own() -> bool:
        return False

    seen = _request_seen_by_the_adapter(
        ModelRequest(task_kind="chat", prompt="p", cancel_check=own), {"cancel_event": threading.Event()}
    )
    assert seen.cancel_check is own


def test_a_turn_without_a_cancel_marker_hands_the_request_on_unchanged() -> None:
    seen = _request_seen_by_the_adapter(ModelRequest(task_kind="normalization_assist", prompt="p"), {})
    assert seen.cancel_check is None


def _person_cancel_check_of(request: ModelRequest):
    from adapters.openai_compatible_adapter import _person_cancel_check

    return _person_cancel_check(request)


# ---------------------------------------------------------------------------------------------
# The served workflow: real daemon, real /api/chat, real /api/chat/cancel, the next message.
# ---------------------------------------------------------------------------------------------

STORY = "tell me a very long story about a lighthouse keeper"
AGAIN = "hi again, can you hear me?"


class _HeldModel:
    def __init__(self) -> None:
        self.release = threading.Event()
        self.held_returned: list[float] = []

    def __call__(self, body: dict) -> str | None:
        messages = [m for m in (body.get("messages") or []) if isinstance(m, dict)]
        last_user = next((str(m.get("content") or "") for m in reversed(messages) if m.get("role") == "user"), "")
        if STORY in last_user:
            self.release.wait(60)
            self.held_returned.append(time.monotonic())
            return "Once upon a time a keeper named Ada (LATE DRAFT)"
        return None


def _transfer_workers(daemon_pid: int) -> list[int]:
    """The daemon's live provider transfer workers (its child processes running provider_http)."""
    found = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            stat = (entry / "stat").read_text()
            cmdline = (entry / "cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", "replace")
        except OSError:
            continue
        ppid = int(stat.rsplit(")", 1)[1].split()[1])
        if ppid == daemon_pid and "provider_http.py" in cmdline and "--worker" in cmdline:
            found.append(int(entry.name))
    return found


def _post(daemon, path: str, body: dict, timeout: float = 60) -> dict:
    request = Request(f"{daemon.base_url}{path}", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode())


def _served_rows(daemon, session: str) -> list[dict]:
    path = daemon.home / "data" / "conversation_log.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()] if path.exists() else []
    return [row for row in rows if row.get("session_id") == session]


def _history(daemon, session: str) -> list[tuple[str, str]]:
    with urlopen(f"{daemon.base_url}/api/chat/history?session={session}", timeout=60) as response:
        payload = json.loads(response.read().decode())
    return [(m.get("role"), m.get("content")) for m in payload.get("messages", [])]


@pytest.mark.skipif(not Path("/proc").is_dir(), reason="the orphan check reads the process table from /proc")
def test_served_stop_then_send_at_once_the_next_message_starts_promptly_and_records_once(tmp_path) -> None:
    held = _HeldModel()
    with rig.CapturingProvider(default="Hello there.", reply_fn=held) as provider:
        daemon = rig.ServedDaemon(tmp_path / "home", provider=provider)
        daemon.start()
        try:
            assert daemon.certify(timeout=180).get("state") == "verified"
            session = rig.canonical_session("stop-then-send-" + uuid.uuid4().hex[:8])
            turn_id = uuid.uuid4().hex
            body = {"messages": [{"role": "user", "content": STORY}], "stream": True, "stream_task_events": True,
                    "session_id": session, "turn_id": turn_id, "model": daemon.model, "mode": "ask"}
            stop_reading = threading.Event()

            def _read() -> None:
                request = Request(f"{daemon.base_url}/api/chat", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}, method="POST")
                with urlopen(request, timeout=300) as response:
                    for _raw in response:
                        if stop_reading.is_set():
                            return  # the page aborts its fetch

            threading.Thread(target=_read, daemon=True).start()
            deadline = time.time() + 120
            while time.time() < deadline and not any(STORY in c["text"] for c in provider.calls):
                time.sleep(0.05)
            assert any(STORY in c["text"] for c in provider.calls), "the turn never reached the model"
            assert _transfer_workers(daemon.process.pid), "the stopped call must be in flight in its worker"

            assert _post(daemon, "/api/chat/cancel", {"session_id": session, "turn_id": turn_id})["state"] == "cancelled"
            stopped_at = time.monotonic()
            stop_reading.set()
            while time.monotonic() - stopped_at < 5 and _transfer_workers(daemon.process.pid):
                time.sleep(0.05)
            assert not _transfer_workers(daemon.process.pid), "the stopped call's worker process was left running"

            calls_before = len(provider.calls)
            reached: list[float] = []

            def _watch_second() -> None:
                while time.monotonic() - stopped_at < 90 and not reached:
                    if any(AGAIN in c["text"] for c in provider.calls[calls_before:]):
                        reached.append(time.monotonic())
                    time.sleep(0.02)

            watcher = threading.Thread(target=_watch_second, daemon=True)
            watcher.start()
            answer = daemon.chat(AGAIN, session_id=session, timeout=120)
            watcher.join(timeout=5)
            assert reached, "the next message never reached the model"
            assert reached[0] - stopped_at < 5.0, f"the next message waited {reached[0] - stopped_at:.1f}s for the stopped turn"
            assert not held.held_returned, "the next message waited for the stopped call's answer"
            assert "Hello there." in json.dumps(answer)

            held.release.set()  # the provider finishes the abandoned call late
            time.sleep(4.0)
            rows = [(r["user"], r["assistant"], r.get("commit_state")) for r in _served_rows(daemon, session)]
            assert rows == [(STORY, "", "stopped"), (AGAIN, "Hello there.", "committed")], rows
            assert "LATE DRAFT" not in json.dumps(_history(daemon, session))
        finally:
            held.release.set()
            daemon.stop()
