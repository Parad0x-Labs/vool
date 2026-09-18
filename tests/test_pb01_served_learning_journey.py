"""PB01 — the served learning journey over /api/chat on one daemon, one isolated home.

The store is seeded ONLY by the journey itself, through the real code-task lane (the same
served path the code-assistant journey proves): a workspace repair with a validated focused
test run learns a CANDIDATE; an independent second witness of the SAME recipe promotes it; a
different validation command must not; a later matching turn consumes the lesson AND the
learned guidance reaches the provider's own request wire (asserted at the scripted provider,
not from envelope ids); an irrelevant stopword-overlapping turn consumes nothing; the
operator's correction stops reuse immediately; a cold restart preserves the store; deletion
forgets the lesson; the sufficiency writer's observations land durably under the daemon home.

Scripted-provider boundary evidence: this file proves the LOOP mechanics — promotion,
relevance, delivery, correction, persistence — not model-authored usefulness, which the
separately-labelled holdout leg owns.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

import pytest

from tests._blackbox_served_rig import REPO_ROOT, SEED_MANIFEST, ScriptedProvider, ServedDaemon, run_in_home

MODEL = "qwen3-stub:2b"
BUGGY = "def add(a, b):\n    return a - b\n"
FIXED = "def add(a, b):\n    return a + b\n"
TEST = "from calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n"
COMMAND = "python -m pytest -q test_calc.py"
DEMAND = "Find why this test fails, repair the root cause, run the focused test, and show me the evidence."


def _call(name: str, arguments: dict[str, Any], call_id: str) -> dict[str, Any]:
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": arguments}}


def _stub_models_are_resident(provider: ScriptedProvider) -> None:
    handler = provider._server.RequestHandlerClass
    original = handler.do_GET

    def do_GET(self):
        if self.path.startswith("/api/ps"):
            return self._send({"models": [{"name": name, "size": 0, "size_vram": 0} for name in provider.table]})
        return original(self)

    handler.do_GET = do_GET


def _git(root: Path, *args: str) -> None:
    env = {**os.environ, "GIT_AUTHOR_NAME": "fx", "GIT_AUTHOR_EMAIL": "fx@local", "GIT_COMMITTER_NAME": "fx", "GIT_COMMITTER_EMAIL": "fx@local"}
    out = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, timeout=30, env=env)
    assert out.returncode == 0, out.stderr


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


def _procedures(daemon: ServedDaemon) -> list[dict[str, Any]]:
    with urlopen(f"{daemon.base_url}/api/learning/procedures", timeout=30) as response:
        return list(json.loads(response.read().decode("utf-8")).get("procedures") or [])


def _operator_call(daemon: ServedDaemon, path: str, *, method: str, body: dict | None = None) -> tuple[int, dict]:
    request = Request(
        f"{daemon.base_url}{path}",
        data=json.dumps(body or {}).encode("utf-8") if method in {"POST", "DELETE"} else None,
        headers={"Content-Type": "application/json"},
        method=method,
    )
    try:
        with urlopen(request, timeout=30) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except Exception as exc:  # urllib raises on 4xx — surface status + body
        code = getattr(exc, "code", 0)
        payload = {}
        with contextlib.suppress(Exception):
            payload = json.loads(exc.read().decode("utf-8"))
        return code, payload


def _run_witness(daemon: ServedDaemon, provider: ScriptedProvider, workspace: Path, store_dir: Path, *, session: str, command: str, call_prefix: str) -> None:
    """One full verified repair through the served code-task lane: reproduce -> identify ->
    read owner -> propose -> approve -> apply (write) -> narrow test (the validated witness)."""
    provider.table[MODEL] = _call("code__task__open", {"objective": DEMAND}, f"{call_prefix}-1")
    daemon.chat(DEMAND, session_id=session, mode="auto", timeout=900.0)
    files = [f for f in (store_dir / "code_tasks").glob("ct-*.json") if f.stat().st_mtime_ns == max(x.stat().st_mtime_ns for x in (store_dir / "code_tasks").glob("ct-*.json"))]
    assert files, f"witness {session}: no code task opened"
    task_id = json.loads(files[-1].read_text(encoding="utf-8"))["task_id"]
    before = hashlib.sha256((workspace / "calc.py").read_bytes()).hexdigest()

    replies: list[str] = []

    def step(step_id: str, intent: str, arguments: dict[str, Any], n: str) -> None:
        provider.table[MODEL] = _call(
            "code__task__step", {"task_id": task_id, "step_id": step_id, "intent": intent, "arguments": arguments}, f"{call_prefix}-{n}"
        )
        out = daemon.chat("c", session_id=session, mode="auto", timeout=900.0)
        replies.append(f"{step_id}: {str(out.get('response') or out.get('message', {}).get('content') or out)[:160]}")

    step("s1", "workspace.run_tests", {"command": command}, "2")  # reproduce (red)
    provider.table[MODEL] = _call(
        "code__task__identify", {"task_id": task_id, "path": "calc.py", "line": 2, "reason": "add subtracts"}, f"{call_prefix}-3"
    )
    daemon.chat("c", session_id=session, mode="auto", timeout=900.0)
    step("s1b", "workspace.read_file", {"path": "calc.py"}, "4")
    provider.table[MODEL] = _call(
        "code__task__propose",
        {
            "task_id": task_id,
            "proposal_id": "p1",
            "intent": "workspace.write_file",
            "arguments": {"path": "calc.py", "content": FIXED, "expected_hash": before},
            "rationale": "Owner calc.py: add subtracts instead of adding; restore the sum.",
        },
        f"{call_prefix}-5",
    )
    daemon.chat("c", session_id=session, mode="auto", timeout=900.0)
    provider.table[MODEL] = _call("code__task__approve", {"task_id": task_id, "proposal_id": "p1"}, f"{call_prefix}-6")
    daemon.chat("c", session_id=session, mode="auto", timeout=900.0)
    step("s2", "workspace.write_file", {"path": "calc.py", "content": FIXED, "expected_hash": before}, "7")
    if (workspace / "calc.py").read_text(encoding="utf-8") != FIXED:
        all_files = sorted((store_dir / "code_tasks").glob("ct-*.json"))
        journal = json.loads(max(all_files, key=lambda f: f.stat().st_mtime_ns).read_text(encoding="utf-8"))
        pytest.fail(
            f"witness {session}: apply did not land || stage={journal.get('stage')} "
            f"steps={ {k: (v.get('executed'), v.get('status')) for k, v in journal.get('steps', {}).items()} } "
            f"|| replies={replies} || provider_calls={[(c['path'], c['tools'][:3], c['has_tool_result']) for c in provider.calls[-8:]]}"
        )
    step("s3", "workspace.run_tests", {"command": command}, "8")  # the validated witness run


def test_served_learning_journey_learn_promote_reuse_irrelevance_correction_restart_forget(served: dict[str, Any]) -> None:
    daemon: ServedDaemon = served["daemon"]
    provider: ScriptedProvider = served["provider"]
    workspace: Path = served["workspace"]
    home: Path = served["home"]
    store_dir: Path = served["store_dir"]

    # 1. LEARN — one verified execution stages a CANDIDATE (never reusable yet).
    _run_witness(daemon, provider, workspace, store_dir, session="pb01-a", command=COMMAND, call_prefix="a")
    procedures = _procedures(daemon)
    assert procedures, "the verified repair learned nothing — no procedure records"
    assert all(str(p.get("status")) == "candidate" for p in procedures), procedures
    procedure_id = str(procedures[0]["procedure_id"])

    # 2. IDENTITY GUARD — a different validation command on the same class must NOT promote.
    (workspace / "calc.py").write_text(BUGGY, encoding="utf-8")
    _run_witness(daemon, provider, workspace, store_dir, session="pb01-wrong", command="python -m pytest -q test_calc.py -k add", call_prefix="w")
    stored = {str(p["procedure_id"]): p for p in _procedures(daemon)}
    assert stored[procedure_id]["status"] == "candidate", "a different command digest must not promote the first lesson"

    # 3. INDEPENDENT RE-VERIFICATION — a second witness of the SAME recipe promotes it.
    (workspace / "calc.py").write_text(BUGGY, encoding="utf-8")
    _run_witness(daemon, provider, workspace, store_dir, session="pb01-b", command=COMMAND, call_prefix="b")
    stored = {str(p["procedure_id"]): p for p in _procedures(daemon)}
    assert stored[procedure_id]["status"] == "promoted", stored[procedure_id]
    assert stored[procedure_id].get("promoted_at"), "promotion must record when it happened"
    assert int(stored[procedure_id].get("evidence_count") or 0) >= 2, "promotion rests on two independent witnesses"

    # 4. MATCHING REUSE — the lesson is consumed AND the guidance reaches the PROVIDER wire.
    (workspace / "calc.py").write_text(BUGGY, encoding="utf-8")
    provider.reset()
    provider.table[MODEL] = _call("code__task__open", {"objective": DEMAND}, "r1")
    daemon.chat(DEMAND, session_id="pb01-reuse", mode="auto", timeout=900.0)
    consuming = [
        c for c in provider.calls
        if "Learned guidance" in (str(c.get("system") or "") + str(c.get("full_prompt") or ""))
    ]
    import sqlite3

    conn = sqlite3.connect(str(home / "data" / "vool_web0_v2.db"))
    learn_events = conn.execute(
        "SELECT event_type, COALESCE(details_json,'') FROM runtime_session_events WHERE event_type LIKE 'learning%' ORDER BY rowid DESC LIMIT 4"
    ).fetchall()
    conn.close()
    assert consuming, (
        f"no guidance on the wire || calls={[(c['path'], str(c.get('system') or '')[:80]) for c in provider.calls]} "
        f"|| learning_events={learn_events} || procedures={_procedures(daemon)}"
    )
    assert any(f"procedure_id: {procedure_id}" in (str(c.get("system") or "") + str(c.get("full_prompt") or "")) for c in consuming), "provenance must ride the delivered block"

    # 5. IRRELEVANT NON-REUSE — vocabulary overlap alone consumes nothing (stopword law).
    provider.reset()
    provider.table[MODEL] = "After lunch is fine."
    daemon.chat("What should I do after lunch, before dinner? Answer in words only.", session_id="pb01-irrel", mode="auto", timeout=900.0)
    assert provider.calls, "the irrelevant turn never reached a model"
    assert not any("Learned guidance" in (str(c.get("system") or "") + str(c.get("full_prompt") or "")) for c in provider.calls), (
        "false relevance: an irrelevant turn received learned guidance"
    )

    # 6. OPERATOR CORRECTION — invalidate stops reuse immediately (owner-local route).
    status, payload = _operator_call(
        daemon, f"/api/learning/procedures/{procedure_id}/invalidate", method="POST", body={"reason": "wrong lesson for this repo"}
    )
    assert status == 200 and payload.get("ok") is True, (status, payload)
    stored = {str(p["procedure_id"]): p for p in _procedures(daemon)}
    assert stored[procedure_id]["status"] == "demoted"

    provider.reset()
    provider.table[MODEL] = _call("code__task__open", {"objective": DEMAND}, "r2")
    daemon.chat(DEMAND, session_id="pb01-corrected", mode="auto", timeout=900.0)
    assert not any("Learned guidance" in (str(c.get("system") or "") + str(c.get("full_prompt") or "")) for c in provider.calls), (
        "a demoted lesson must not deliver guidance"
    )

    # 7. RESTART — the store survives a cold daemon restart (state + correction).
    daemon.stop()
    daemon.start(timeout=240)
    stored = {str(p["procedure_id"]): p for p in _procedures(daemon)}
    assert procedure_id in stored and stored[procedure_id]["status"] == "demoted"

    # 8. THE SUFFICIENCY WRITER — CLOSED: the /api/chat finalize closure joins THIS TURN'S OWN
    # stored events (provider/model/routing-task-kind identity x terminal stage verdict) and
    # writes typed observations under the daemon home; cancelled and transport-failed turns
    # record nothing, and model selection consumes only eligible rows. The eight served
    # scenarios live in tests/test_pb01_served_sufficiency_served.py; here assert the journey's
    # own model turns left durable rows with real runtime identity.
    store = home / "data" / "learning" / "sufficiency_observations.json"
    assert store.exists(), "the served journey's model turns must leave a sufficiency store"
    observations = list(json.loads(store.read_text(encoding="utf-8")).get("observations") or [])
    assert observations, "the journey's model turns must write sufficiency observations"
    assert all(str(o.get("session_id") or "").strip() for o in observations), observations
    assert all(
        str(o.get("task_kind") or "").strip() and str(o.get("task_kind")) != "unknown"
        for o in observations
    ), observations

    # 9. FORGET — deletion removes the lesson; a traversal-shaped id never touches the filesystem.
    status, payload = _operator_call(daemon, f"/api/learning/procedures/{procedure_id}", method="DELETE")
    assert status == 200 and payload.get("deleted") is True, (status, payload)
    assert all(str(p["procedure_id"]) != procedure_id for p in _procedures(daemon))
    status, _ = _operator_call(daemon, "/api/learning/procedures/not-a-valid-id", method="DELETE")
    assert status == 400, "an invalid id on the matched path must be refused before any filesystem access"
    status, _ = _operator_call(daemon, "/api/learning/procedures/..%2Fsecrets", method="DELETE")
    assert status in {400, 404, 405}, "a traversal-shaped path must never reach the store"
