"""The PR description is served, not just callable: a real daemon, a real /api/chat turn,
a scripted model proposal, and the published reply IS the runtime-rendered description.

The rig is the proven served-drive shape: everything between the HTTP request and the task
journal's bytes is production code from this checkout; only the model is a stub that answers
from a table. `pytest.mark.served` keeps this out of the default in-process runs.
"""

from __future__ import annotations

import hashlib
import json
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
from tests.test_code_assistant_served_journey import BUGGY, DEMAND, FIXED, TEST, _call, _git, _stub_models_are_resident

pytestmark = [pytest.mark.served]

MODEL = "qwen3-stub:2b"


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


def _reply_text(payload: dict[str, Any]) -> str:
    message = payload.get("message") if isinstance(payload.get("message"), dict) else {}
    return str(message.get("content") or payload.get("response") or payload.get("content") or "")


def test_pr_description_is_published_by_a_served_chat_turn(served: dict[str, Any]) -> None:
    daemon: ServedDaemon = served["daemon"]
    provider: ScriptedProvider = served["provider"]
    store_dir: Path = served["store_dir"]
    session = "served-pr-description-1"

    def turn(text: str, call: dict[str, Any] | None) -> str:
        provider.table[MODEL] = call if call is not None else "ok"
        payload = _chat_with_operator(daemon, served["home"], session, text)
        return _reply_text(payload)

    turn(DEMAND, _call("code__task__open", {"objective": DEMAND}, "c1"))
    task = _journal(store_dir)
    task_id = task["task_id"]

    turn("reproduce it", _call(
        "code__task__step",
        {"task_id": task_id, "step_id": "s1", "intent": "workspace.run_tests", "arguments": {"command": "python -m pytest -q test_calc.py"}},
        "c2",
    ))
    assert _journal(store_dir)["stage"] == "identify"

    turn("read the owner", _call(
        "code__task__step",
        {"task_id": task_id, "step_id": "s2", "intent": "workspace.read_file", "arguments": {"path": "calc.py"}},
        "c3",
    ))
    turn("identify", _call(
        "code__task__identify",
        {"task_id": task_id, "path": "calc.py", "line": 2, "reason": "add subtracts instead of adding"},
        "c4",
    ))
    before = hashlib.sha256(BUGGY.encode()).hexdigest()
    turn("propose", _call(
        "code__task__propose",
        {
            "task_id": task_id,
            "proposal_id": "p1",
            "intent": "workspace.write_file",
            "arguments": {"path": "calc.py", "content": FIXED, "expected_hash": before},
            "rationale": "Owner calc.py: add subtracts instead of adding.",
        },
        "c5",
    ))
    turn("approve", _call("code__task__approve", {"task_id": task_id, "proposal_id": "p1"}, "c6"))
    turn("apply it", _call(
        "code__task__step",
        {"task_id": task_id, "step_id": "s3", "intent": "workspace.write_file", "arguments": {"path": "calc.py", "content": FIXED, "expected_hash": before}},
        "c7",
    ))
    turn("narrow test", _call(
        "code__task__step",
        {"task_id": task_id, "step_id": "s4", "intent": "workspace.run_tests", "arguments": {"command": "python -m pytest -q test_calc.py"}},
        "c8",
    ))
    assert _journal(store_dir)["stage"] == "cumulative"
    turn("cumulative test", _call(
        "code__task__step",
        {"task_id": task_id, "step_id": "s5", "intent": "workspace.run_tests", "arguments": {"command": "python -m pytest -q"}},
        "c9",
    ))
    assert _journal(store_dir)["stage"] == "inspect_diff"
    turn("show the diff", _call(
        "code__task__step",
        {"task_id": task_id, "step_id": "s6", "intent": "workspace.git_diff", "arguments": {}},
        "c10",
    ))
    assert _journal(store_dir)["stage"] == "report"

    # The deliverable turn: the model asks for the PR description; the runtime renders it.
    # The complete reviewable title and body must reach chat as well as the durable journal.
    reply = turn("prepare the pull request description", _call("code__task__pr_description", {"task_id": task_id}, "c11"))
    assert "calc.py" in reply, reply[:400]
    assert "add subtracts" in reply
    prepared = _journal(store_dir)["pr_description"]
    assert prepared['body'] in reply, reply
    assert 'failed after the repair' not in reply, reply
    assert prepared["title"].startswith("Repair calc.py")
    assert "python -m pytest -q test_calc.py" in prepared["body"]
    assert "no pull request was opened" in prepared["body"].lower()

    # The offer seated the projection at the report stage, and nothing uncontracted appeared.
    offered = {name for call in provider.calls for name in call.get("tools") or []}
    assert any("pr_description" in name for name in offered), sorted(offered)[-20:]
    from core.runtime_tool_contracts import runtime_tool_contract_map

    contracted = set(runtime_tool_contract_map())
    unknown = sorted(n for n in offered if n.replace("__", ".") not in contracted)
    assert not unknown, unknown


def test_served_pr_description_refuses_before_the_evidence_exists(served: dict[str, Any]) -> None:
    daemon: ServedDaemon = served["daemon"]
    provider: ScriptedProvider = served["provider"]
    store_dir: Path = served["store_dir"]
    session = "served-pr-description-2"

    provider.table[MODEL] = _call("code__task__open", {"objective": DEMAND}, "c1")
    daemon.chat(DEMAND, session_id=session, mode="auto", timeout=900.0)
    task_id = _journal(store_dir)["task_id"]

    provider.table[MODEL] = _call("code__task__pr_description", {"task_id": task_id}, "c2")
    payload = daemon.chat("prepare the PR description now", session_id=session, mode="auto", timeout=900.0)
    reply = _reply_text(payload)
    # The refusal publishes honestly: the runtime will not put an unbacked description out,
    # so the reply is a truthful non-publication (either the projection's own refusal text or
    # the answer gate's), never a fabricated PR body.
    assert "## What changed" not in reply
    assert "Repair" not in reply
    task = _journal(store_dir)
    assert task["stage"] == "reproduce", "a refused projection must not advance the task"
    assert not task.get("pr_description"), "nothing was prepared"
