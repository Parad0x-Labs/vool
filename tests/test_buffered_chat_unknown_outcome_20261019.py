"""A buffered chat turn whose action ends with an unknown outcome answers typed no-answer, not HTTP 500.

Measured on the 0.7.2 train head (153148e), real daemon, scripted provider: the model writes a
workspace file, the write lands but its acknowledgement is lost. The A6 boundary records the effect
as "Outcome unknown -- reconciliation required", its obligation stays open by law, and
`finalize_answer` refuses the turn (OBLIGATIONS_OPEN). The streamed lane ends that turn with a
typed `no_answer.terminal` frame; the buffered lane (`stream: false`) let the refusal escape the
request and the client got a bare `500 Internal Server Error`.

Everything below is the shipped daemon (`apps/vool_api_server.py`) over HTTP. Only two things are
scripted: the provider's replies, and the storage layer losing the acknowledgement of one write
(the write itself happens). No answer may claim the file was written.
"""
from __future__ import annotations

import json
import os
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest

import tests._served_skill_rig as rig
from tests._served_skill_rig import PROVIDER_MANIFEST_ID, ProviderState, ServedDaemon, make_provider_server

# A turn the runtime hands to the model's native tool loop; the scripted model then seats the
# workspace family and writes one file.
REQUEST = "A unit test in my project fails after my change. Diagnose the root cause before proposing any fix."
CLAIM = "I created the file."
LOST_ACK_MARKER = "lost-ack"

_LAUNCHER = '''
import runpy, sys
import core.runtime_execution_tools as tools
_write = tools.atomic_write_text
def _write_then_lose_the_ack(target, content, *args, **kwargs):
    result = _write(target, content, *args, **kwargs)
    if "%s" in str(target):
        raise ConnectionResetError("storage acknowledgement lost after the write was issued")
    return result
tools.atomic_write_text = _write_then_lose_the_ack
sys.argv = sys.argv[1:]
runpy.run_path(sys.argv[0], run_name="__main__")
''' % LOST_ACK_MARKER


def _scripted_turn(body: dict[str, Any], state: ProviderState, model: str) -> dict[str, Any]:
    tools = body.get("tools") or []
    if not tools:
        return rig._completion(content=CLAIM)
    names = [str((tool.get("function") or {}).get("name") or "") for tool in tools]
    called = [
        str((call.get("function") or {}).get("name") or "")
        for message in body.get("messages") or []
        if message.get("role") == "assistant"
        for call in message.get("tool_calls") or []
    ]
    if "workspace__write_file" not in called:
        if "workspace__write_file" in names:
            return rig._completion(
                tool_calls=[rig._tool_call("w1", "workspace__write_file", {"path": state.target, "content": "hello\n"})],
                finish="tool_calls",
            )
        if "capability__expand_family" in names and "capability__expand_family" not in called:
            return rig._completion(
                tool_calls=[rig._tool_call("x1", "capability__expand_family", {"family": "workspace"})],
                finish="tool_calls",
            )
    return rig._completion(tool_calls=[rig._tool_call("r1", "respond__direct", {"message": CLAIM})], finish="tool_calls")


@pytest.fixture(scope="module")
def served(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("unknown-outcome")
    launcher = tmp / "lossy_daemon.py"
    launcher.write_text(_LAUNCHER, encoding="utf-8")
    state = ProviderState()
    state.target = ""
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(rig, "_turn_response", _scripted_turn)
        server, port = make_provider_server(state)
        home = tmp / "home"
        home.mkdir()
        daemon = ServedDaemon(home, port, extra_env={"PYTHONPATH": os.pathsep.join(
            [str(rig.REPO_ROOT), os.environ.get("PYTHONPATH", "")]).rstrip(os.pathsep)})
        real_popen = subprocess.Popen

        def lossy_popen(command, *args, **kwargs):
            return real_popen([command[0], str(launcher), *command[1:]], *args, **kwargs)

        patch.setattr(subprocess, "Popen", lossy_popen)
        try:
            daemon.start()
        finally:
            patch.setattr(subprocess, "Popen", real_popen)
        try:
            daemon.pin_provider_model()
            certified = daemon.certify_provider_model()
            assert (certified.get("result") or certified).get("state") == "verified", json.dumps(certified)[:400]
            yield type("Served", (), {"daemon": daemon, "state": state, "tmp": tmp})
        finally:
            daemon.stop()
            server.shutdown()


def _post(daemon: ServedDaemon, path: str, payload: dict[str, Any]) -> tuple[int, bytes]:
    request = urllib.request.Request(
        f"{daemon.base_url}{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"content-type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=420) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def _turn(served, path: str, *, target: str, stream: bool, session: str) -> tuple[int, bytes, Path]:
    workspace = served.tmp / session
    workspace.mkdir()
    served.state.target = target
    status, body = _post(served.daemon, path, {
        "model": PROVIDER_MANIFEST_ID,
        "messages": [{"role": "user", "content": REQUEST}],
        "stream": stream,
        "mode": "auto",
        "workspace": str(workspace),
        "session_id": session,
    })
    return status, body, workspace


def _unknown_effect_for(daemon: ServedDaemon, target: str) -> bool:
    rows = daemon.get("/api/runtime/unresolved-effects").get("unresolved_effects") or []
    return any(row.get("tool_name") == "workspace.write_file" and target in json.dumps(row) for row in rows)


@pytest.mark.parametrize(
    ("path", "content_of"),
    [
        ("/v1/chat/completions", lambda reply: reply["choices"][0]["message"]["content"]),
        ("/api/chat", lambda reply: reply["message"]["content"]),
    ],
    ids=["openai", "ollama"],
)
def test_buffered_unknown_outcome_is_typed_no_answer_not_500(served, path, content_of):
    target = f"{LOST_ACK_MARKER}-{path.strip('/').replace('/', '-')}.txt"
    status, body, workspace = _turn(served, path, target=target, stream=False, session=f"buffered-{target}")

    assert status == 200, (status, body[:400])
    reply = json.loads(body)
    # The write happened but could not be proven: the runtime holds it as unknown, durably.
    assert (workspace / target).is_file()
    assert _unknown_effect_for(served.daemon, target)
    # No answer bytes, so nothing claims the file was created; the typed terminal says why.
    assert content_of(reply) == ""
    terminal = reply["vool_terminal"]
    assert terminal["type"] == "no_answer.terminal"
    assert terminal["status"] == "no_answer_terminal"
    assert terminal["reason_code"] == "turn_failed"
    assert "OBLIGATIONS_OPEN" in terminal["detail"]


def test_streamed_twin_ends_with_the_same_typed_terminal(served):
    target = f"{LOST_ACK_MARKER}-stream.txt"
    status, body, workspace = _turn(served, "/v1/chat/completions", target=target, stream=True, session="streamed")

    assert status == 200
    assert _unknown_effect_for(served.daemon, target)
    frames = [json.loads(line[6:]) for line in body.decode("utf-8").splitlines() if line.startswith("data: {")]
    terminals = [frame["vool_terminal"] for frame in frames if "vool_terminal" in frame]
    assert [terminal["reason_code"] for terminal in terminals] == ["turn_failed"]
    assert not any((choice.get("delta") or {}).get("content") for frame in frames for choice in frame.get("choices") or [])


def test_a_write_whose_acknowledgement_arrives_still_answers(served):
    """Negative control: the same scripted turn, but the write is acknowledged."""
    target = "acknowledged.txt"
    status, body, workspace = _turn(served, "/v1/chat/completions", target=target, stream=False, session="acknowledged")

    assert status == 200, (status, body[:400])
    reply = json.loads(body)
    assert (workspace / target).read_text(encoding="utf-8") == "hello\n"
    assert not _unknown_effect_for(served.daemon, target)
    assert "vool_terminal" not in reply
    assert reply["choices"][0]["message"]["content"].strip()
