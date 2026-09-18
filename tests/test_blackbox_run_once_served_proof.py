"""SERVED proof: a real ``run_once`` turn, entered through the daemon's HTTP door, in which the
model (a scripted provider over a real socket) calls ``workspace__write_file``; the daemon
executes it against a real workspace root; the Blackbox journal in the daemon's own store records
the effect under the daemon's turn identity; and a SUBSEQUENT, explicit operator rollback -- a
separate process, crossing ``execute_authorized_runtime_tool`` -- restores the exact bytes.

Nothing here mocks the runtime: the provider is a stub endpoint (this machine is cloud-only for
model execution), everything after the wire is production code from THIS checkout.

Marked ``served``: boots a daemon (~5-20 s). Skipped only when the daemon cannot boot in this
environment, and then it SAYS so -- a skip is not a pass.

Boundary truth (2026-09-02): the served write below is claimed by the DETERMINISTIC workspace
fast path (route ``turn:api``, no tool catalogue offered), which crosses the same authorized
boundary as the model loop. A second phrasing meant for the model tool loop ("write a haiku ...
save it to haiku.txt") was claimed by the builder lane instead, whose single-file generation went
to the stub and returned nothing -- so a served MODEL-tool-loop write is NOT verified here. The
model route is verified in-process only, by ``execute_authorized_runtime_tool`` in
``test_blackbox_flight_recorder.py``. Routing is outside this lane's scope.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import textwrap
from pathlib import Path
from typing import Any

import pytest

from tests._blackbox_served_rig import REPO_ROOT, SEED_MANIFEST, ScriptedProvider, ServedDaemon, run_in_home

pytestmark = [pytest.mark.served]

MODEL = "qwen3-stub:2b"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _reply_text(payload: dict[str, Any]) -> str:
    message = payload.get("message") if isinstance(payload.get("message"), dict) else {}
    return str(message.get("content") or payload.get("response") or payload.get("content") or "")


def _events(home: Path, *kinds: str) -> list[tuple[str, str]]:
    conn = sqlite3.connect(str(home / "data" / "vool_web0_v2.db"))
    try:
        rows = conn.execute(
            "SELECT event_type, COALESCE(details_json, '') FROM runtime_session_events ORDER BY rowid DESC LIMIT 400"
        ).fetchall()
    finally:
        conn.close()
    return [(row[0], row[1]) for row in rows if not kinds or row[0] in kinds]


def _journal(store_dir: Path) -> list[dict[str, Any]]:
    from storage.blackbox.journal import Journal

    return [dict(e) for e in Journal(store_dir).entries()]


@pytest.fixture
def served(tmp_path: Path):
    home = tmp_path / "home"
    workspace = tmp_path / "ws"
    store_dir = tmp_path / "blackbox-store"
    workspace.mkdir()
    (workspace / "settings.log").write_bytes(b"retries = 5\ntimeout_seconds = 30\n")
    tool_call = {
        "id": "call-1",
        "type": "function",
        "function": {"name": "workspace__write_file", "arguments": {"path": "notes/plan.md", "content": "# plan\n- step one\n"}},
    }
    provider = ScriptedProvider({MODEL: tool_call}, after_tool_result={MODEL: "unused: the runtime renders the tool result itself"})
    daemon = ServedDaemon(
        home,
        env_extra={
            "VOOL_ALWAYS_ON_CATALOG": "1",
            "VOOL_WORKSPACE_ROOT": str(workspace),
            "VOOL_BLACKBOX_DIR": str(store_dir),
            # This machine is cloud-only for model execution and a real Ollama listens on 11434.
            # EVERY default local endpoint is pointed at the stub so no lane -- the builder's own
            # OLLAMA_HOST client included -- can reach a real local model from this daemon.
            "OLLAMA_HOST": provider.base_url,
            "VOOL_OLLAMA_URL": provider.base_url,
            "VOOL_OLLAMA_CHAT_URL": f"{provider.base_url}/api/chat",
        },
    )
    provider.__enter__()
    try:
        try:
            daemon.start(timeout=240)
        except Exception as exc:  # pragma: no cover - environment, not the recorder
            pytest.skip(f"served daemon could not boot here: {exc}")
        run_in_home(home, SEED_MANIFEST.format(root=REPO_ROOT, base_url=provider.base_url, registered=[MODEL]))
        provider.reset()
        yield {"home": home, "workspace": workspace, "store_dir": store_dir, "daemon": daemon, "provider": provider}
    finally:
        daemon.stop()
        provider.__exit__(None, None, None)


def test_served_run_once_write_is_journaled_and_operator_rollback_restores_exact_bytes(served: dict[str, Any]) -> None:
    home: Path = served["home"]
    workspace: Path = served["workspace"]
    store_dir: Path = served["store_dir"]
    daemon: ServedDaemon = served["daemon"]
    provider: ScriptedProvider = served["provider"]

    # `mode: auto` at the door: the session would otherwise default to Manual, where a create
    # answers pending_approval and nothing lands (measured on the first served run).
    payload = daemon.chat(
        "create a file notes/plan.md with a short plan for this workspace", session_id="served-blackbox-1", mode="auto"
    )
    text = _reply_text(payload)
    offered = [call for call in provider.calls if call.get("tools")]
    lane = "model_tool_loop" if offered else "deterministic_or_builder"

    target = workspace / "notes" / "plan.md"
    assert target.exists(), (
        f"the served write never landed (lane={lane}). reply={text[:400]!r}\n"
        f"provider_calls={provider.calls[:4]}\nevents={_events(home)[:14]}\n{daemon.log_tail()}"
    )
    written = target.read_bytes()
    assert written, "an empty file is not the requested plan"

    entries = _journal(store_dir)
    intended = [e for e in entries if e.get("kind") == "effect_intended" and e.get("path") == "notes/plan.md"]
    terminal = [e for e in entries if e.get("kind") == "effect_terminal" and e.get("path") == "notes/plan.md"]
    assert len(intended) == 1 and len(terminal) == 1, [(e.get("kind"), e.get("path")) for e in entries]
    intended, terminal = intended[0], terminal[0]
    # Evidence for the report: which lane produced the effect and what the wire said.
    (store_dir / "served_lane.json").write_text(json.dumps(
        {"lane": lane, "offered_tools": (offered[0]["tools"] if offered else []), "route": intended.get("route"),
         "attempt_id": intended.get("attempt_id"), "turn_id": intended.get("turn_id"), "session_id": intended.get("session_id"),
         "wire_session_id": "served-blackbox-1", "reply_head": text[:300]},
        indent=2), encoding="utf-8")
    assert intended["root"] == str(workspace.resolve())
    assert intended["intent"] == "workspace.write_file"
    assert intended["operation"] == "create"
    # The daemon binds the wire `session_id` to its own canonical chat identity (`openclaw:<hash>`);
    # the journal carries THAT, the identity every receipt in the daemon's own store uses.
    assert intended["session_id"], intended
    assert intended["attempt_id"].startswith("attempt-"), intended["attempt_id"]
    assert intended["turn_id"] and not intended["turn_id"].startswith("direct:"), intended["turn_id"]
    assert intended["authority"]["effect"] == "allow", intended["authority"]
    assert intended["authority"]["route"] == "authorized_execution_boundary"
    assert intended["before"]["exists"] is False
    assert intended["intended"]["after_sha256"] == _sha(written)
    assert terminal["outcome"] == "succeeded"
    assert terminal["after"]["sha256"] == _sha(written)
    assert int(terminal["seq"]) > int(intended["seq"])
    turn_id = intended["turn_id"]

    # A user edits the file after the turn: the operator's rollback must refuse, not overwrite.
    target.write_bytes(written + b"- my own edit\n")
    refused = json.loads(run_in_home(home, _ROLLBACK_SCRIPT.format(root=REPO_ROOT, turn=turn_id, ws=workspace),
                                     env_extra={"VOOL_BLACKBOX_DIR": str(store_dir)}))
    assert refused["status"] == "blackbox_rollback_conflict", refused
    assert refused["details"]["blackbox_rollback"]["conflicts"][0]["reason"] == "content_changed"
    assert target.read_bytes() == written + b"- my own edit\n"

    # The edit is undone by the user; the rollback now restores the pre-turn state exactly: the
    # file did not exist, so it is removed, and the directory the turn created goes with it.
    target.write_bytes(written)
    rolled = json.loads(run_in_home(home, _ROLLBACK_SCRIPT.format(root=REPO_ROOT, turn=turn_id, ws=workspace),
                                    env_extra={"VOOL_BLACKBOX_DIR": str(store_dir)}))
    assert rolled["ok"], rolled
    assert rolled["status"] == "executed"
    assert rolled["details"]["permission"]["route"] == "authorized_execution_boundary"
    assert rolled["details"]["permission"]["effect"] == "allow"
    assert sorted(rolled["details"]["blackbox_rollback"]["removed_paths"]) == ["notes", "notes/plan.md"]
    assert not target.exists()
    assert not (workspace / "notes").exists()
    assert (workspace / "settings.log").read_bytes() == b"retries = 5\ntimeout_seconds = 30\n"

    after = _journal(store_dir)
    kinds = [e.get("kind") for e in after]
    assert kinds.count("rollback_committed") == 1
    assert kinds.count("rollback_refused") == 1
    committed = next(e for e in after if e.get("kind") == "rollback_committed")
    assert committed["rollback_of_turn"] == turn_id
    assert committed["operator"] == "served-proof-operator"
    from storage.blackbox.journal import Journal

    report = Journal(store_dir).verify()
    assert report.ok, report

    # Idempotent: the same operator request again writes nothing and says so.
    again = json.loads(run_in_home(home, _ROLLBACK_SCRIPT.format(root=REPO_ROOT, turn=turn_id, ws=workspace),
                                   env_extra={"VOOL_BLACKBOX_DIR": str(store_dir)}))
    assert again["status"] == "already_rolled_back", again
    assert [e.get("kind") for e in _journal(store_dir)].count("rollback_committed") == 1


_ROLLBACK_SCRIPT = textwrap.dedent(
    '''
    import json, sys
    sys.path.insert(0, "{root}")
    from core.blackbox.operator import rollback_turn
    from core.mode_permission_policy import PermissionAction, grant_internal_authority, set_active_mode
    session = "operator:served-proof"
    set_active_mode(session, "auto")
    token = grant_internal_authority(
        label="blackbox-served-proof-operator", duration_seconds=120, intents=["workspace.rollback_last_change"],
        workspace_root="{ws}",
        actions=[PermissionAction.DELETE_FILES, PermissionAction.OVERWRITE_EXISTING_FILES, PermissionAction.MODIFY_FILES],
    )
    result = rollback_turn("{turn}", workspace_root="{ws}", session_id=session, operator="served-proof-operator",
                           source_context={{"operating_mode": "auto", "surface": "cli"}}, authority_token=token)
    print(json.dumps({{"ok": result.ok, "status": result.status, "response_text": result.response_text, "details": result.details}}, default=str))
    '''
)

