"""Folder names inside a path are not the user's words.

Review finding on 3422a2e1 (2026-10-18): an explicit "Inside <path>/alpha create <file> with
exactly this content" turn returned audit advice and wrote nothing whenever the workspace path ran
through a folder named "review" (the Mac review scratch folder did; CI temp folders do not, which
is why the existing explicit-write test passed in CI and failed on the Mac). The audit lane read
"review" in the path as the verb and "failure" in a folder name as a verdict.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from apps.vool_agent import VoolAgent
from core.agent_runtime.workspace_audit import looks_like_code_audit_request


@pytest.mark.parametrize("folders", ["review", "code-review/failure-notes/drafts"])
def test_explicit_writes_run_when_the_workspace_path_names_audit_words(tmp_path, folders):
    workspace = tmp_path / folders
    workspace.mkdir(parents=True)
    root = str(workspace.resolve())
    agent = VoolAgent(backend_name="test-backend", device="path-words", persona_id="default")
    agent.start()
    session = "openclaw:path-words-" + str(len(folders))
    context = {"operating_mode": "auto", "surface": "openclaw", "platform": "openclaw", "workspace": root}

    made = agent.run_once(f"Create a folder named notes inside {root}.", session_id_override=session, source_context=context)
    assert made["mode"] == "tool_executed", made.get("response")

    text = agent.run_once(
        f"Inside {root}/notes create plan.txt with exactly this content: PATH-WORDS-OK",
        session_id_override=session,
        source_context=context,
    )
    assert text["mode"] == "tool_executed", text.get("response")
    assert (workspace / "notes" / "plan.txt").read_text(encoding="utf-8") == "PATH-WORDS-OK"

    code = agent.run_once(
        f"Inside {root}/notes create scale.py with exactly this code:\n\n"
        "def scale(x: int) -> int:\n"
        "    return 3 * x\n",
        session_id_override=session,
        source_context=context,
    )
    assert code["mode"] == "tool_executed", code.get("response")
    assert (workspace / "notes" / "scale.py").read_text(encoding="utf-8") == "def scale(x: int) -> int:\n    return 3 * x"


@pytest.mark.parametrize(
    "text",
    [
        "Inside /srv/team/review/alpha create util.py with exactly this code: def f(): return 1",
        "Inside /data/review/out/test-failure/run create log.txt with exactly this content: OK",
        "Inside ~/projects/review/app create main.py with exactly this code: print(1)",
        "Inside C:\\Users\\me\\review\\alpha create util.py with exactly this code: x = 1",
    ],
)
def test_a_folder_named_review_is_not_an_audit_request(text):
    assert looks_like_code_audit_request(text) is False


@pytest.mark.parametrize(
    "text",
    [
        "review this code for bugs",
        "please review/audit this code",
        "review ~/code for bugs and security issues",
        "audit svc/queue.py",
        "review src/review/app.py for bugs",
    ],
)
def test_real_audit_requests_still_open_the_audit(text):
    assert looks_like_code_audit_request(text) is True
