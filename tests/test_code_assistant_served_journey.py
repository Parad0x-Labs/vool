"""The coding assistant's served journey: the production chat door, a real daemon, a scripted
Ollama-dialect model, a disposable Git repository with one real failing pytest.

One scripted tool call per chat turn -- the served tool lane executes the call and renders its
result itself -- and the task id is recovered between turns from the runtime's on-disk journal,
never from the model's prose. Every assertion reads bytes, the journal, or the provider's
recorded offers; none reads the reply text as proof.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
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

pytestmark = [pytest.mark.served]

MODEL = "qwen3-stub:2b"
BUGGY = "def add(a, b):\n    return a - b\n"
FIXED = "def add(a, b):\n    return a + b\n"
TEST = "from calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n"
DEMAND = (
    "Find why this test fails, repair the root cause, run the focused test, run the cumulative pack, "
    "and show me the exact diff and evidence."
)


def _git(root: Path, *args: str) -> None:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "fx",
        "GIT_AUTHOR_EMAIL": "fx@local",
        "GIT_COMMITTER_NAME": "fx",
        "GIT_COMMITTER_EMAIL": "fx@local",
    }
    out = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, timeout=30, env=env)
    assert out.returncode == 0, out.stderr


def _call(name: str, arguments: dict[str, Any], call_id: str) -> dict[str, Any]:
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": arguments}}


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
    provider = ScriptedProvider(
        {MODEL: "no script yet"}, after_tool_result={MODEL: "unused: the runtime renders the tool result itself"}
    )
    daemon = ServedDaemon(
        home,
        env_extra={
            "VOOL_ALWAYS_ON_CATALOG": "1",
            "VOOL_WORKSPACE_ROOT": str(workspace),
            "VOOL_BLACKBOX_DIR": str(store_dir),
            "VOOL_CODE_TASK_DIR": str(store_dir / "code_tasks"),
            # The stub loads no model; without this the resource governor gates the ":2b" tag on a
            # loaded machine ("model_load_gated_low_memory") before the tool-intent call is made.
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
        run_in_home(home, SEED_MANIFEST.format(root=REPO_ROOT, base_url=provider.base_url, registered=[MODEL]))
        provider.reset()
        yield {"home": home, "workspace": workspace, "store_dir": store_dir, "daemon": daemon, "provider": provider}
    finally:
        daemon.stop()
        provider.__exit__(None, None, None)


def _journal(store_dir: Path) -> dict[str, Any]:
    files = sorted((store_dir / "code_tasks").glob("ct-*.json"))
    assert len(files) == 1, files
    return json.loads(files[0].read_text(encoding="utf-8"))


def test_served_root_cause_journey_through_api_chat(served: dict[str, Any]) -> None:
    daemon: ServedDaemon = served["daemon"]
    provider: ScriptedProvider = served["provider"]
    workspace: Path = served["workspace"]
    store_dir: Path = served["store_dir"]
    session = "served-code-task-1"
    turns: list[dict[str, Any]] = []

    def turn(text: str, call: dict[str, Any] | None) -> dict[str, Any]:
        provider.table[MODEL] = call if call is not None else "ok"
        payload = _chat_with_operator(daemon, served["home"], session, text)  # wall clock on a loaded box
        turns.append({"text": text, "call": call, "reply": payload})
        return payload

    first = turn(DEMAND, _call("code__task__open", {"objective": DEMAND}, "c1"))
    files = sorted((store_dir / "code_tasks").glob("ct-*.json")) if (store_dir / "code_tasks").exists() else []
    if not files:
        import sqlite3

        conn = sqlite3.connect(str(served["home"] / "data" / "vool_web0_v2.db"))
        events = conn.execute(
            "SELECT event_type, substr(COALESCE(details_json,''),1,220) FROM runtime_session_events ORDER BY rowid DESC LIMIT 30"
        ).fetchall()
        conn.close()
        message = first.get("message") if isinstance(first.get("message"), dict) else {}
        pytest.fail(
            "the served door executed no code.task.open\n"
            f"reply={str(message.get('content') or first.get('response') or '')[:500]!r}\n"
            f"provider_calls={[(c['path'], len(c['tools']), c['has_tool_result']) for c in provider.calls]}\n"
            f"offered={sorted({t for c in provider.calls for t in c['tools']})[:40]}\n"
            "events=\n  " + "\n  ".join(f"{e[0]} | {e[1]}" for e in events) + "\n" + daemon.log_tail()[-1500:]
        )
    task = _journal(store_dir)
    task_id = task["task_id"]
    assert task["objective"] == DEMAND and task["stage"] == "reproduce", task
    # The model was offered only contracted tools -- every name it saw resolves in the one registry.
    from core.runtime_tool_contracts import runtime_tool_contract_map

    offered = {name for call in provider.calls for name in call.get("tools") or []}
    assert offered, provider.calls
    contracted = set(runtime_tool_contract_map())
    unknown = sorted(n for n in offered if n.replace("__", ".") not in contracted)
    assert not unknown, unknown
    # The planner owns the repair start (it opened the journal before the model's first
    # call), so the model's rounds see the CONTINUATION seat -- and, by the offer's own
    # law, never a second opening while the task is open.
    assert "code__task__step" in offered
    assert "code__task__open" not in offered

    before = hashlib.sha256(BUGGY.encode()).hexdigest()
    turn(
        "reproduce it",
        _call(
            "code__task__step",
            {
                "task_id": task_id,
                "step_id": "s1",
                "intent": "workspace.run_tests",
                "arguments": {"command": "python -m pytest -q test_calc.py"},
            },
            "c2",
        ),
    )
    task = _journal(store_dir)
    assert task["stage"] == "identify", (task["stage"], task["steps"].get("s1"))
    assert task["steps"]["s1"]["executed"] is True and task["steps"]["s1"]["result"]["success"] is False
    turn(
        "identify",
        _call(
            "code__task__identify", {"task_id": task_id, "path": "calc.py", "line": 2, "reason": "add subtracts"}, "c3"
        ),
    )
    # The owner is read through the boundary before its repair may be proposed.
    turn(
        "read the owner",
        _call(
            "code__task__step",
            {"task_id": task_id, "step_id": "s1b", "intent": "workspace.read_file", "arguments": {"path": "calc.py"}},
            "c3b",
        ),
    )
    turn(
        "propose",
        _call(
            "code__task__propose",
            {
                "task_id": task_id,
                "proposal_id": "p1",
                "intent": "workspace.write_file",
                "arguments": {"path": "calc.py", "content": FIXED, "expected_hash": before},
                "rationale": "Owner calc.py: add subtracts instead of adding; restore the sum.",
            },
            "c4",
        ),
    )
    assert (workspace / "calc.py").read_text(encoding="utf-8") == BUGGY  # a preview never mutates
    turn("approve", _call("code__task__approve", {"task_id": task_id, "proposal_id": "p1"}, "c5"))
    assert _journal(store_dir)["stage"] == "mutate"
    turn(
        "apply",
        _call(
            "code__task__step",
            {
                "task_id": task_id,
                "step_id": "s2",
                "intent": "workspace.write_file",
                "arguments": {"path": "calc.py", "content": FIXED, "expected_hash": before},
            },
            "c6",
        ),
    )
    task = _journal(store_dir)
    assert (workspace / "calc.py").read_bytes() == FIXED.encode(), task["steps"]["s2"]
    assert task["steps"]["s2"]["bytes_match_approved_patch"] is True
    assert task["steps"]["s2"]["result"]["blackbox"]["terminal_recorded"] is True
    turn(
        "narrow",
        _call(
            "code__task__step",
            {
                "task_id": task_id,
                "step_id": "s3",
                "intent": "workspace.run_tests",
                "arguments": {"command": "python -m pytest -q test_calc.py"},
            },
            "c7",
        ),
    )
    turn(
        "cumulative",
        _call(
            "code__task__step",
            {
                "task_id": task_id,
                "step_id": "s4",
                "intent": "workspace.run_tests",
                "arguments": {"command": "python -m pytest -q"},
            },
            "c8",
        ),
    )
    turn(
        "diff",
        _call(
            "code__task__step",
            {"task_id": task_id, "step_id": "s5", "intent": "workspace.git_diff", "arguments": {}},
            "c9",
        ),
    )
    task = _journal(store_dir)
    assert task["narrow"]["success"] is True and task["cumulative"]["success"] is True, (
        task["narrow"],
        task["cumulative"],
    )
    assert task["git_diff_paths"] == ["calc.py"], task["git_diff_paths"]
    assert task["stage"] == "report"
    reply = turn("report", _call("code__task__report", {"task_id": task_id}, "c10"))
    message = reply.get("message") if isinstance(reply.get("message"), dict) else {}
    text = str(message.get("content") or reply.get("response") or reply.get("content") or "")
    (store_dir / "report_reply.txt").write_text(text, encoding="utf-8")
    # The final response is the runtime's own report, published through the production gates:
    # it names the verdict and the counts, and no model prose stands in for it.
    assert "completed" in text and "refused" in text, text[:600]
    executed = [s for s, rec in task["steps"].items() if rec["executed"]]
    assert sorted(executed) == ["s1", "s1b", "s2", "s3", "s4", "s5"]
    (store_dir / "served_journey.json").write_text(
        json.dumps(
            {
                "task_id": task_id,
                "offered": sorted(offered),
                "turns": len(turns),
                "report_reply_head": text[:400],
                "provider_calls": len(provider.calls),
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def test_served_report_turn_publishes_the_runtime_rendered_evidence(served: dict[str, Any]) -> None:
    """The final response carries the runtime's own report (passed, failed, refused, unresolved)
    even for a task with nothing done yet: an `unresolved` verdict published through the
    production gates. What made this pass (2026-09-03): a tool-intent response is metered but
    never claims authorship (`record_served_usage(authored=False)`), and a tool whose contract
    declares `renders_final_answer` has its grounded result published without model synthesis."""
    daemon: ServedDaemon = served["daemon"]
    provider: ScriptedProvider = served["provider"]
    store_dir: Path = served["store_dir"]
    session = "served-code-task-2"
    provider.table[MODEL] = _call("code__task__open", {"objective": DEMAND}, "d1")
    daemon.chat(DEMAND, session_id=session, mode="auto", timeout=900.0)
    task_id = _journal(store_dir)["task_id"]
    provider.table[MODEL] = _call("code__task__report", {"task_id": task_id}, "d2")
    reply = daemon.chat("show me the evidence report", session_id=session, mode="auto", timeout=900.0)
    message = reply.get("message") if isinstance(reply.get("message"), dict) else {}
    text = str(message.get("content") or reply.get("response") or "")
    if "unresolved" not in text:
        import sqlite3

        conn = sqlite3.connect(str(served["home"] / "data" / "vool_web0_v2.db"))
        events = conn.execute(
            "SELECT event_type, substr(COALESCE(details_json,''),1,600) FROM runtime_session_events ORDER BY rowid DESC LIMIT 80"
        ).fetchall()
        conn.close()
        wanted = ("grounding", "tool_result_published", "publication", "authorship", "model.call_completed", "task_completed")
        pytest.fail(
            f"reply={text[:300]!r}\nevents=\n  " + "\n  ".join(f"{e[0]} | {e[1]}" for e in events if e[0].startswith(wanted))
        )
