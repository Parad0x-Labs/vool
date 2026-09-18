"""C18 multilingual capability parity — typed task outcomes through real ``/api/chat``.

STATUS after the C18 FINAL CORRECTION (context is not authority): only the ENGLISH demand
retains typed routing authority today (the verb-anchored repair rule); the Lithuanian,
Spanish, German and Japanese equivalents of the same demand carry NO typed execution-demand
authority and must FAIL CLOSED. This file proves both halves over a real ``/api/chat`` socket:

  POSITIVE (en): the full typed chain — the runtime offers its contracted code-task tools
  (production selection), opens a typed task with the literal demand as objective, executes a
  journaled workspace read, and applies an approval-gated literal write whose bytes must equal
  the approved patch EXACTLY (``bytes_match_approved_patch`` receipt), after proving the
  preview never mutated the file; a cancelled task refuses further writes with no journal entry
  and no effects.

  NEGATIVE (lt/es/de/ja): the equivalent demands carry the same pasted command anchor but no
  typed action authority, so the control plane is NOT seated. Even a provider scripted to
  ATTEMPT ``code__task__open`` is refused as unregistered: no task journal, no effects, file
  bytes untouched. This fail-closed behavior is the honest replacement for the withdrawn
  command-anchor shortcut — multilingual code-task execution stays PARTIAL for the C12 owner.

What the SCRIPTED provider supplies (disclosed): the model's tool CHOICE per turn, picked from
the tools the runtime itself offered on the wire. Everything else — capability selection,
offer contracts, read/write execution, hash/patch verification, approval gating, denial,
journaling — is production code from this checkout. Nothing here is a real-model claim.
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
    run_in_home,
)

MODEL = "qwen3-stub:2b"
BUGGY = "def add(a, b):\n    return a - b\n"
TEST = "from calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n"

#: One equivalent repair demand + one language-marked literal patch per parity language. Each
#: demand carries the same LANGUAGE-NEUTRAL anchor — the literal failing command a real
#: operator of any language keeps verbatim — because the routing seam
#: (``task_router.looks_like_bounded_repo_repair_request``) accepts that anchor in every
#: language, while translated verbs alone still mis-route (recorded gap, C12 owner).
LANG_CASES = {
    "en": {
        "demand": (
            "Find why this test fails (python -m pytest -q), repair the root "
            "cause and show me the exact diff."
        ),
        "literal": "def add(a, b):\n    return a + b  # sum\n",
    },
    "lt": {
        "demand": (
            "Išsiaiškink, kodėl šis testas nepavyksta (python -m pytest -q), "
            "pataisyk pagrindinę priežastį ir parodyk skirtumą."
        ),
        "literal": "def add(a, b):\n    return a + b  # sudėtis\n",
    },
    "es": {
        "demand": (
            "Descubre por qué falla esta prueba (python -m pytest -q), repara "
            "la causa y muéstrame la diferencia."
        ),
        "literal": "def add(a, b):\n    return a + b  # suma\n",
    },
    "de": {
        "demand": (
            "Finde heraus, warum dieser Test fehlschlägt (python -m pytest -q), "
            "behebe die Ursache und zeig mir den Unterschied."
        ),
        "literal": "def add(a, b):\n    return a + b  # Summe\n",
    },
    "ja": {
        "demand": (
            "このテストが失敗する原因を調べて (python -m pytest -q)、"
            "根本原因を修正して、差分を見せてください。"
        ),
        "literal": "def add(a, b):\n    return a + b  # 足し算\n",
    },
}


def _git(root: Path, *args: str) -> None:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "c18",
        "GIT_AUTHOR_EMAIL": "c18@local",
        "GIT_COMMITTER_NAME": "c18",
        "GIT_COMMITTER_EMAIL": "c18@local",
    }
    out = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, timeout=30, env=env
    )
    assert out.returncode == 0, out.stderr


def _call(name: str, arguments: dict[str, Any], call_id: str) -> dict[str, Any]:
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": arguments}}


def _stub_models_are_resident(provider: ScriptedProvider) -> None:
    handler = provider._server.RequestHandlerClass
    original = handler.do_GET

    def do_GET(self):
        if self.path.startswith("/api/ps"):
            return self._send(
                {"models": [{"name": n, "size": 0, "size_vram": 0} for n in provider.table]}
            )
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
        {MODEL: "no script yet"},
        after_tool_result={MODEL: "unused: the runtime renders the tool result itself"},
    )
    daemon = ServedDaemon(
        home,
        env_extra={
            "VOOL_ALWAYS_ON_CATALOG": "1",
            "VOOL_WORKSPACE_ROOT": str(workspace),
            "VOOL_BLACKBOX_DIR": str(store_dir),
            "VOOL_CODE_TASK_DIR": str(store_dir / "code_tasks"),
            "VOOL_MODEL_LOAD_FLOOR_GB": "0",
            "VOOL_DISABLE_WEB": "1",
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
        run_in_home(
            home,
            SEED_MANIFEST.format(
                root=REPO_ROOT, base_url=provider.base_url, registered=[MODEL]
            ),
        )
        # healthz precedes full provider-registry readiness; without this beat the first turn
        # of a freshly booted daemon can race its own registration.
        import time as _time

        _time.sleep(3.0)
        provider.reset()
        yield {
            "home": home,
            "workspace": workspace,
            "store_dir": store_dir,
            "daemon": daemon,
            "provider": provider,
        }
    finally:
        daemon.stop()
        provider.__exit__(None, None, None)


def _journal(store_dir: Path) -> dict[str, Any]:
    files = sorted((store_dir / "code_tasks").glob("ct-*.json"))
    assert len(files) == 1, files
    return json.loads(files[0].read_text(encoding="utf-8"))


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# POSITIVE CONTROL — the one language whose demand carries typed action authority today.
@pytest.mark.parametrize("lang", ["en"])
def test_equivalent_multilingual_demands_drive_the_same_typed_capability_chain(served, lang: str) -> None:
    case = LANG_CASES[lang]
    daemon: ServedDaemon = served["daemon"]
    provider: ScriptedProvider = served["provider"]
    workspace: Path = served["workspace"]
    store_dir: Path = served["store_dir"]
    session = f"c18-capability-{lang}"

    def turn(text: str, call: dict[str, Any] | None) -> dict[str, Any]:
        provider.table[MODEL] = call if call is not None else "ok"
        return daemon.chat(text, session_id=session, mode="auto", timeout=900.0)

    def offered_tools() -> set[str]:
        return {name for call in provider.calls for name in call.get("tools") or []}

    # 1. OPEN — the runtime offered its contracted code-task tools (production selection) and
    #    opened a typed task whose objective is the literal demand in this language.
    turn(case["demand"], _call("code__task__open", {"objective": case["demand"]}, "c1"))
    offered = offered_tools()
    assert "code__task__open" in offered, sorted(offered)[:40]
    task = _journal(store_dir)
    task_id = task["task_id"]
    assert task["objective"] == case["demand"], task["objective"]
    assert task["stage"] == "reproduce", task["stage"]

    # 2. REPRODUCE, IDENTIFY, READ — the stage machine walks reproduce → identify; the read
    #    receipt carries the exact content hash of the owner file.
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
            "c1b",
        ),
    )
    task = _journal(store_dir)
    assert task["stage"] == "identify", task["stage"]
    assert task["steps"]["s1"]["executed"] is True, task["steps"].get("s1")
    turn(
        "identify the defect",
        _call(
            "code__task__identify",
            {"task_id": task_id, "path": "calc.py", "line": 2, "reason": "add subtracts"},
            "c2",
        ),
    )
    turn(
        "read the owner",
        _call(
            "code__task__step",
            {
                "task_id": task_id,
                "step_id": "read",
                "intent": "workspace.read_file",
                "arguments": {"path": "calc.py"},
            },
            "c2b",
        ),
    )
    task = _journal(store_dir)
    read_step = task["steps"]["read"]
    assert read_step["executed"] is True and read_step["ok"] is True, read_step
    assert read_step["status"] == "executed", read_step
    assert read_step["result"]["hash"] == _sha(BUGGY), read_step["result"]

    # 3. PROPOSE the literal patch — the preview never mutates the file: zero unauthorized
    #    effects before approval.
    base = _sha(BUGGY)
    turn(
        "propose",
        _call(
            "code__task__propose",
            {
                "task_id": task_id,
                "proposal_id": "p1",
                "intent": "workspace.write_file",
                "arguments": {"path": "calc.py", "content": case["literal"], "expected_hash": base},
                "rationale": "Owner calc.py: add subtracts instead of adding; restore the sum.",
            },
            "c3",
        ),
    )
    assert (workspace / "calc.py").read_bytes() == BUGGY.encode(), "preview mutated the file"

    # 4. APPROVE, APPLY — the applied bytes must equal the approved literal EXACTLY, with the
    #    typed byte-fidelity receipt.
    turn("approve", _call("code__task__approve", {"task_id": task_id, "proposal_id": "p1"}, "c4"))
    assert _journal(store_dir)["stage"] == "mutate", _journal(store_dir)["stage"]
    turn(
        "apply",
        _call(
            "code__task__step",
            {
                "task_id": task_id,
                "step_id": "apply",
                "intent": "workspace.write_file",
                "arguments": {"path": "calc.py", "content": case["literal"], "expected_hash": base},
            },
            "c5",
        ),
    )
    task = _journal(store_dir)
    assert task["steps"]["apply"]["executed"] is True, task["steps"].get("apply")
    assert task["steps"]["apply"]["bytes_match_approved_patch"] is True, task["steps"]["apply"]
    assert (workspace / "calc.py").read_bytes() == case["literal"].encode("utf-8"), (
        "the applied bytes are not the approved literal"
    )

    # 5. DENIED EFFECT — after cancellation the control plane is withdrawn: a further write is
    #    refused as unoffered, nothing is journaled, and the file keeps its exact bytes.
    turn("cancel", _call("code__task__cancel", {"task_id": task_id, "reason": "operator"}, "c6"))
    assert _journal(store_dir)["stage"] == "cancelled"
    turn(
        "write more",
        _call(
            "code__task__step",
            {
                "task_id": task_id,
                "step_id": "after_cancel",
                "intent": "workspace.write_file",
                "arguments": {
                    "path": "calc.py",
                    "content": case["literal"] + "# unauthorized\n",
                    "expected_hash": _sha(case["literal"]),
                },
            },
            "c7",
        ),
    )
    task = _journal(store_dir)
    assert task["stage"] == "cancelled"
    assert "after_cancel" not in task["steps"], task["steps"].get("after_cancel")
    assert (workspace / "calc.py").read_bytes() == case["literal"].encode("utf-8"), (
        "an effect ran after the task's control plane was withdrawn"
    )


# NEGATIVE CONTROLS — command context without typed action demand fails closed. The provider
# is scripted to ATTEMPT the control-plane open anyway (the strongest form: a misbehaving
# model cannot conjure authority), and the runtime must refuse it unoffered: no task journal,
# no effects, file bytes untouched.
@pytest.mark.parametrize("lang", ["lt", "es", "de", "ja"])
def test_non_english_demands_fail_closed_without_action_authority(served, lang: str) -> None:
    case = LANG_CASES[lang]
    daemon: ServedDaemon = served["daemon"]
    provider: ScriptedProvider = served["provider"]
    workspace: Path = served["workspace"]
    store_dir: Path = served["store_dir"]
    session = f"c18-fail-closed-{lang}"

    provider.table[MODEL] = _call("code__task__open", {"objective": case["demand"]}, "x1")
    daemon.chat(case["demand"], session_id=session, mode="auto", timeout=900.0)

    offered = {name for call in provider.calls for name in call.get("tools") or []}
    assert "code__task__open" not in offered, (
        f"{lang}: the code-task control plane was seated without typed action demand"
    )
    journals = list((store_dir / "code_tasks").glob("ct-*.json")) if (store_dir / "code_tasks").exists() else []
    assert not journals, f"{lang}: a code task was opened without action authority"
    assert (workspace / "calc.py").read_bytes() == BUGGY.encode(), (
        f"{lang}: workspace bytes changed without action authority"
    )
