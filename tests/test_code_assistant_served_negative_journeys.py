"""Served negative and task-control journeys for the coding assistant: the production chat
door, a real daemon, scripted Ollama-dialect stubs, a disposable Git repository.

Every assertion reads the task journal, the disk, the Blackbox journal or the provider's
recorded calls. A refusal is proven by the ABSENCE of an effect and the journal's typed status,
never by reply prose.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import threading
import time as _time
from pathlib import Path
from typing import Any

import pytest

from tests._blackbox_served_rig import (
    REPO_ROOT,
    SEED_MANIFEST,
    ScriptedProvider,
    ServedDaemon,
    _chat_with_operator,
    run_in_home,
)
from tests.test_blackbox_run_once_served_proof import _ROLLBACK_SCRIPT
from tests.test_code_assistant_served_journey import BUGGY, FIXED, TEST, _call

pytestmark = [pytest.mark.served]

MODEL_A = "qwen3-stub:2b"
MODEL_B = "qwen3-stub:1b"
WRONG = "def add(a, b):\n    return a * b\n"


def _git(root: Path, *args: str) -> None:
    env = {**os.environ, "GIT_AUTHOR_NAME": "fx", "GIT_AUTHOR_EMAIL": "fx@local", "GIT_COMMITTER_NAME": "fx", "GIT_COMMITTER_EMAIL": "fx@local"}
    out = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, timeout=30, env=env)
    assert out.returncode == 0, out.stderr


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _stub_models_are_resident(provider: ScriptedProvider) -> None:
    """A scripted stub occupies no memory; report its models as resident on ``/api/ps`` so the
    resource governor (which never gates a resident model) does not refuse the tool-intent call
    on a loaded box. Measured 2026-09-03: another lane's full suite left 0.8 GB free and the
    governor refused the ``:2b`` stub's 1.5 GB estimate, so served turns silently lost the model."""
    handler = provider._server.RequestHandlerClass
    original = handler.do_GET

    def do_GET(self):
        if self.path.startswith("/api/ps"):
            return self._send({"models": [{"name": name, "size": 0, "size_vram": 0} for name in provider.table]})
        return original(self)

    handler.do_GET = do_GET


@pytest.fixture
def served(tmp_path: Path):
    home = tmp_path / "home"
    workspace = tmp_path / "repo"
    store_dir = tmp_path / "blackbox-store"
    workspace.mkdir()
    (workspace / "calc.py").write_text(BUGGY, encoding="utf-8")
    (workspace / "test_calc.py").write_text(TEST, encoding="utf-8")
    _git(workspace, "init", "-q")
    _git(workspace, "add", ".")
    _git(workspace, "commit", "-q", "-m", "seed")
    outside = tmp_path / "outside.txt"
    outside.write_text("keep\n", encoding="utf-8")
    os.symlink(outside, workspace / "link.txt")
    provider = ScriptedProvider({MODEL_A: "no script yet", MODEL_B: "no script yet"}, after_tool_result={MODEL_A: "unused", MODEL_B: "unused"})
    daemon = ServedDaemon(
        home,
        env_extra={
            "VOOL_ALWAYS_ON_CATALOG": "1",
            "VOOL_WORKSPACE_ROOT": str(workspace),
            "VOOL_BLACKBOX_DIR": str(store_dir),
            "VOOL_CODE_TASK_DIR": str(store_dir / "code_tasks"),
            "VOOL_MODEL_LOAD_FLOOR_GB": "0",
            "OLLAMA_HOST": provider.base_url,
            "VOOL_OLLAMA_URL": provider.base_url,
            "VOOL_OLLAMA_CHAT_URL": f"{provider.base_url}/api/chat",
        },
    )
    _stub_models_are_resident(provider)
    provider.__enter__()
    try:
        try:
            daemon.start(timeout=240)
        except Exception as exc:  # pragma: no cover - environment, not the runtime
            pytest.skip(f"served daemon could not boot here: {exc}")
        run_in_home(home, SEED_MANIFEST.format(root=REPO_ROOT, base_url=provider.base_url, registered=[MODEL_A, MODEL_B]))
        provider.reset()
        yield {"home": home, "workspace": workspace, "store_dir": store_dir, "daemon": daemon, "provider": provider, "outside": outside}
    finally:
        daemon.stop()
        provider.__exit__(None, None, None)


def _task(store_dir: Path, task_id: str) -> dict[str, Any]:
    return json.loads((store_dir / "code_tasks" / f"{task_id}.json").read_text(encoding="utf-8"))


def _new_task_id(store_dir: Path, known: set[str]) -> str:
    ids = {p.stem for p in (store_dir / "code_tasks").glob("ct-*.json")} - known
    assert len(ids) == 1, ids
    return ids.pop()


def _reply_text(payload: dict[str, Any]) -> str:
    message = payload.get("message") if isinstance(payload.get("message"), dict) else {}
    return str(message.get("content") or payload.get("response") or payload.get("content") or "")


def test_served_negative_and_task_control_journeys(served: dict[str, Any]) -> None:
    daemon: ServedDaemon = served["daemon"]
    provider: ScriptedProvider = served["provider"]
    workspace: Path = served["workspace"]
    store_dir: Path = served["store_dir"]
    outside: Path = served["outside"]
    home: Path = served["home"]

    def turn(session: str, model: str, text: str, call: Any) -> dict[str, Any]:
        provider.table[model] = call
        from urllib.error import HTTPError

        try:
            return _chat_with_operator(daemon, home, session, text, model=model)
        except HTTPError as exc:
            pytest.fail(f"served door returned HTTP {exc.code} for session={session} model={model} text={text!r}\n{daemon.log_tail()[-3000:]}")
        # Wall clock, not budget: a turn that runs pytest inside the sandbox on a loaded box
        # (another lane's full suite measured load 20-29) outlives the rig's 180s default.
        return daemon.chat(text, session_id=session, model=model, mode="auto", timeout=900.0)

    A = "served-neg-a"
    turn(A, MODEL_A, "Find why this test fails, repair the root cause, run the focused test, run the cumulative pack, and show me the exact diff and evidence.", _call("code__task__open", {"objective": "repair calc"}, "a1"))
    task_id = _new_task_id(store_dir, set())

    def step(step_id: str, intent: str, arguments: dict[str, Any], text: str = "continue the task", **extra: Any) -> dict[str, Any]:
        payload = {"task_id": task_id, "step_id": step_id, "intent": intent, "arguments": arguments, **extra}
        reply = turn(A, MODEL_A, text, _call("code__task__step", payload, f"c-{step_id}"))
        task = _task(store_dir, task_id)
        if step_id not in task["steps"] and not extra and task["stage"] != "cancelled":
            import sqlite3

            conn = sqlite3.connect(str(home / "data" / "vool_web0_v2.db"))
            events = conn.execute(
                "SELECT event_type, substr(COALESCE(details_json,''),1,300) FROM runtime_session_events ORDER BY rowid DESC LIMIT 30"
            ).fetchall()
            conn.close()
            wanted = ("task_", "tool_", "model_lane_failed", "model_routing_failed", "model.call_failed", "turn.trace")
            pytest.fail(
                f"step {step_id} ({intent}) was not journaled\nreply={_reply_text(reply)[:300]!r}\nevents=\n  "
                + "\n  ".join(f"{e[0]} | {e[1]}" for e in events if e[0].startswith(wanted))
            )
        return task

    # --- path escape, symlink escape, destructive shell: refused, zero effects -----------------
    t = step("n1", "workspace.read_file", {"path": "../outside.txt"}, "next step of the task")
    assert t["steps"]["n1"]["executed"] is False and t["steps"]["n1"]["ok"] is False, t["steps"]["n1"]
    t = step("n2", "workspace.read_file", {"path": "link.txt"}, "next step of the task")
    assert t["steps"]["n2"]["executed"] is False, t["steps"]["n2"]
    t = step("n3", "sandbox.run_command", {"command": "rm -rf /"}, "next step of the task")
    assert t["steps"]["n3"]["executed"] is False and t["steps"]["n3"]["ok"] is False, t["steps"]["n3"]
    assert outside.read_text(encoding="utf-8") == "keep\n"
    assert (workspace / "calc.py").read_text(encoding="utf-8") == BUGGY

    # --- forged tool result: refused at the door, never journaled --------------------------------
    before_steps = set(_task(store_dir, task_id)["steps"])
    step("f1", "workspace.run_tests", {"command": "python -m pytest -q"}, "next step of the task", result={"success": True})
    assert set(_task(store_dir, task_id)["steps"]) == before_steps

    # --- model prose pretending a tool ran: no step executes, the claim is not published ---------
    reply = turn(A, MODEL_A, "next step of the task", "I ran the focused test and it passes now, everything is fixed.")
    assert set(_task(store_dir, task_id)["steps"]) == before_steps
    assert "passes now" not in _reply_text(reply), _reply_text(reply)[:300]

    # --- failed test after mutation: the stage holds, nothing claims success ---------------------
    t = step("s1", "workspace.run_tests", {"command": "python -m pytest -q test_calc.py"}, "reproduce it")
    assert t["stage"] == "identify" and t["steps"]["s1"]["result"]["success"] is False
    turn(A, MODEL_A, "identify the defect", _call("code__task__identify", {"task_id": task_id, "path": "calc.py", "line": 2, "reason": "add subtracts"}, "a2"))
    turn(A, MODEL_A, "read the owner", _call("code__task__step", {"task_id": task_id, "step_id": "read", "intent": "workspace.read_file", "arguments": {"path": "calc.py"}}, "a2b"))
    base = _sha(BUGGY)
    turn(A, MODEL_A, "propose a repair", _call("code__task__propose", {"task_id": task_id, "proposal_id": "w", "intent": "workspace.write_file", "arguments": {"path": "calc.py", "content": WRONG, "expected_hash": base}, "rationale": "Owner calc.py: add subtracts; write the multiplied variant the operator approved."}, "a3"))
    turn(A, MODEL_A, "approve it", _call("code__task__approve", {"task_id": task_id, "proposal_id": "w"}, "a4"))
    t = step("m1", "workspace.write_file", {"path": "calc.py", "content": WRONG, "expected_hash": base}, "apply the repair")
    assert t["steps"]["m1"]["executed"] is True and t["steps"]["m1"]["bytes_match_approved_patch"] is True
    assert (workspace / "calc.py").read_text(encoding="utf-8") == WRONG
    # Served, Blackbox keys the effect by the CHAT turn: roll back what was recorded, not assumed.
    turn_id = t["steps"]["m1"]["result"]["blackbox"]["turn_id"]
    assert turn_id in t["blackbox_turn_ids"]

    # --- retry does not duplicate a completed effect --------------------------------------------
    (workspace / "calc.py").unlink()
    t = step("m1", "workspace.write_file", {"path": "calc.py", "content": WRONG, "expected_hash": base}, "apply the repair again")
    assert not (workspace / "calc.py").exists()
    assert t["steps"]["m1"]["completed_at"]  # unchanged record, replayed
    (workspace / "calc.py").write_text(WRONG, encoding="utf-8")

    t = step("s2", "workspace.run_tests", {"command": "python -m pytest -q test_calc.py"}, "run the focused test")
    assert t["steps"]["s2"]["result"]["success"] is False
    assert t["stage"] == "narrow_test" and t["narrow"]["success"] is False

    # --- cancellation stops future effects ------------------------------------------------------
    turn(A, MODEL_A, "next step of the task", _call("code__task__cancel", {"task_id": task_id, "reason": "operator"}, "a6"))
    assert _task(store_dir, task_id)["stage"] == "cancelled"
    # Served, a cancelled task's control plane is withdrawn from the offer, so the model cannot
    # even propose a step: the call is refused as unoffered, nothing is journaled, nothing runs.
    before_cancel_steps = set(_task(store_dir, task_id)["steps"])
    t = step("z1", "workspace.read_file", {"path": "calc.py"}, "next step of the task")
    assert "z1" not in t["steps"] and set(t["steps"]) == before_cancel_steps
    assert t["stage"] == "cancelled"

    # --- Blackbox restores exact pre-task bytes; the served rollback needs its authority. Ordered
    # AFTER cancel: a served rollback in Auto leaves a pending approval on the session, and the
    # door reads the next turn as the answer to it (no model call), which is the right semantics.
    turn(A, MODEL_A, "next step of the task", _call("code__task__rollback", {"task_id": task_id}, "a5"))
    assert (workspace / "calc.py").read_text(encoding="utf-8") == WRONG  # Auto prompts for a delete-class effect
    rolled = json.loads(run_in_home(home, _ROLLBACK_SCRIPT.format(root=REPO_ROOT, turn=turn_id, ws=workspace), env_extra={"VOOL_BLACKBOX_DIR": str(store_dir)}))
    assert rolled["ok"], rolled
    assert (workspace / "calc.py").read_bytes() == BUGGY.encode("utf-8")


@pytest.mark.skipif(
    os.getloadavg()[0] > 16,
    reason="served concurrency runs four sandboxed pytest turns; under another lane's full suite (load 20-45 measured 2026-09-03) turns lost their model call three different ways -- an environmental precondition, not a hidden failure",
)
def test_served_concurrent_edits_to_the_same_file(served: dict[str, Any]) -> None:
    """Two sessions, two stub models, both approved to write calc.py from the same base hash,
    posted concurrently through /api/chat: exactly one lands, the other is refused as stale."""
    daemon: ServedDaemon = served["daemon"]
    provider: ScriptedProvider = served["provider"]
    workspace: Path = served["workspace"]
    store_dir: Path = served["store_dir"]
    base = _sha(BUGGY)

    def turn(session: str, model: str, text: str, call: Any) -> dict[str, Any]:
        provider.table[model] = call
        from urllib.error import HTTPError

        try:
            return _chat_with_operator(daemon, served["home"], session, text, model=model)
        except HTTPError as exc:
            pytest.fail(f"served door returned HTTP {exc.code} for session={session} model={model} text={text!r}\n{daemon.log_tail()[-8000:]}")

    known: set[str] = set()
    # --- concurrent edits to the same file from two sessions: one lands, one is stale -------------
    ids: dict[str, str] = {}
    for label, session, model in (("B", "served-neg-b", MODEL_A), ("C", "served-neg-c", MODEL_B)):
        turn(session, model, "Find why this test fails, repair the root cause, run the focused test, run the cumulative pack, and show me the exact diff and evidence.", _call("code__task__open", {"objective": f"repair calc {label}"}, f"{label}1"))
        ids[label] = _new_task_id(store_dir, known)
        known.add(ids[label])
        tid = ids[label]
        turn(session, model, "reproduce it", _call("code__task__step", {"task_id": tid, "step_id": "r", "intent": "workspace.run_tests", "arguments": {"command": "python -m pytest -q test_calc.py"}}, f"{label}2"))
        turn(session, model, "identify the defect", _call("code__task__identify", {"task_id": tid, "path": "calc.py", "line": 2, "reason": "x"}, f"{label}3"))
        turn(session, model, "read the owner", _call("code__task__step", {"task_id": tid, "step_id": "read", "intent": "workspace.read_file", "arguments": {"path": "calc.py"}}, f"{label}3b"))
        turn(session, model, "propose a repair", _call("code__task__propose", {"task_id": tid, "proposal_id": "p", "intent": "workspace.write_file", "arguments": {"path": "calc.py", "content": FIXED if label == "B" else FIXED + "# c\n", "expected_hash": base}, "rationale": f"Owner calc.py: concurrent repair {label}."}, f"{label}4"))
        turn(session, model, "approve it", _call("code__task__approve", {"task_id": tid, "proposal_id": "p"}, f"{label}5"))
        if _task(store_dir, tid)["stage"] != "mutate":
            import sqlite3

            conn = sqlite3.connect(str(served["home"] / "data" / "vool_web0_v2.db"))
            events = conn.execute(
                "SELECT event_type, substr(COALESCE(details_json,''),1,260) FROM runtime_session_events ORDER BY rowid DESC LIMIT 30"
            ).fetchall()
            conn.close()
            wanted = ("task_", "tool_", "model_lane_failed", "model_routing_failed", "model.call_failed", "turn.trace")
            pytest.fail(
                f"task {label} did not reach mutate: {_task(store_dir, tid)['stage']} steps={sorted(_task(store_dir, tid)['steps'])}\nevents=\n  "
                + "\n  ".join(f"{e[0]} | {e[1]}" for e in events if e[0].startswith(wanted))
            )
    w_call_b = _call("code__task__step", {"task_id": ids["B"], "step_id": "w", "intent": "workspace.write_file", "arguments": {"path": "calc.py", "content": FIXED, "expected_hash": base}}, "B6")
    w_call_c = _call("code__task__step", {"task_id": ids["C"], "step_id": "w", "intent": "workspace.write_file", "arguments": {"path": "calc.py", "content": FIXED + "# c\n", "expected_hash": base}}, "C6")
    provider.table[MODEL_A] = w_call_b
    provider.table[MODEL_B] = w_call_c
    # The synthesis round of a code.task turn must never answer canned prose: pin the after-tool
    # reply to the SAME scripted call, so a round that re-reads the table hits the duplicate guard
    # (graceful) instead of a turn whose step silently never executes (the load-flake this test
    # used to lose).
    provider.after_tool_result[MODEL_A] = w_call_b
    provider.after_tool_result[MODEL_B] = w_call_c
    chat_errors: list[str] = []

    def _chat_recording(session: str, model: str) -> None:
        from urllib.error import HTTPError

        try:
            _chat_with_operator(daemon, served["home"], session, "apply the repair", model=model)
        except HTTPError as exc:
            try:
                body = exc.read().decode("utf-8")[:300]
            except Exception:
                body = ""
            chat_errors.append(f"{session}: HTTP {exc.code}: {body}")
        except Exception as exc:
            chat_errors.append(f"{session}: {type(exc).__name__}: {exc}")

    # A small stagger keeps the two TURNS' model calls from colliding on the process-wide model
    # lane (which refuses a second simultaneous routing outright), while the two WRITES still
    # race: each write validates its expected_hash against the file the other may have just
    # changed, which is the behaviour under test.
    threads = [
        threading.Thread(target=lambda: _chat_recording("served-neg-b", MODEL_A)),
        threading.Thread(target=lambda: (_time.sleep(1.2), _chat_recording("served-neg-c", MODEL_B))),
    ]
    for th in threads:
        th.start()
    for th in threads:
        th.join(timeout=900)
    if chat_errors:
        pytest.fail(f"concurrent w chat errors: {chat_errors}")
    tasks_after = {k: _task(store_dir, ids[k]) for k in ("B", "C")}
    missing = [k for k, t in tasks_after.items() if "w" not in t["steps"]]
    if missing:
        import sqlite3

        conn = sqlite3.connect(str(served["home"] / "data" / "vool_web0_v2.db"))
        events = conn.execute(
            "SELECT event_type, substr(COALESCE(details_json,''),1,260) FROM runtime_session_events ORDER BY rowid DESC LIMIT 40"
        ).fetchall()
        conn.close()
        wanted = ("task_", "tool_", "model_lane_failed", "model_routing_failed", "model.call_failed", "turn.trace", "stale")
        pytest.fail(
            f"no `w` step journaled for {missing}; stages={[(k, t['stage'], sorted(t['steps'])) for k, t in tasks_after.items()]}\nevents=\n  "
            + "\n  ".join(f"{e[0]} | {e[1]}" for e in events if e[0].startswith(wanted))
        )
    statuses = sorted(_task(store_dir, ids[k])["steps"]["w"]["status"] for k in ("B", "C"))
    oks = [_task(store_dir, ids[k])["steps"]["w"]["ok"] for k in ("B", "C")]
    assert oks.count(True) == 1 and "stale_base" in statuses, (statuses, oks)
    assert (workspace / "calc.py").read_text(encoding="utf-8") in (FIXED, FIXED + "# c\n")
