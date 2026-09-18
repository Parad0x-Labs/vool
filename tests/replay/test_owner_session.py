"""Replay the owner's real session, verbatim, and score real outcomes.

Every fix before this was driven against folders invented for the purpose, with phrasings chosen by
the person writing the fix, inside this repo — a git repository with no spaces in its path. The
owner's project is `/Users/example-user/Desktop/VOOL WEBSITE`: **not a git repo, space in the
path**. None of that was ever exercised, and the fixes were reported as VERIFIED anyway.

So the wordings below are copied character-for-character from the transcript, typos included, and
the assertions are about what actually happened — was the named file opened, is the completion
claim backed by a record — not about whether some string appears in a reply.

The workspace is the owner's real one when present; a same-shaped fixture otherwise, so this stays
runnable on any machine. The shape is what matters: no `.git`, a space in the directory name,
HTML/CSS/JS rather than Python.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core import execution_records
from core.agent_runtime.action_honesty_validator import enforce_final_action_honesty
from core.agent_runtime.source_audit import is_source_path
from core.agent_runtime.workspace_audit import audit_target_in, run_workspace_audit
from core.execution.planner import _asks_which_workspace
from core.runtime_execution_tools import execute_runtime_tool

OWNER_WORKSPACE = Path("/Users/example-user/Desktop/VOOL WEBSITE")

# Verbatim from the session transcript. Typos preserved deliberately: "u", "whats", the missing
# apostrophes and the repeated punctuation are exactly what a real user types, and normalising them
# would recreate the original mistake of testing prose nobody actually wrote.
SAID_AUDIT = (
    "great, i need you to run audit for this - app-landing/index.html tell me if u can spot any "
    "security issues and such?"
)
SAID_WORKSPACE = "what folder is in use for this workspace?"
SAID_WORKSPACE_2 = "do you see what folder our workspace is set on?"
SAID_DID_YOU = "did u run the audit!??!?!"

# What the app actually claimed, having never opened the named file.
FABRICATED_REPLY = (
    "Yes, the audit ran — it inspected the whole workspace (14 JS source files, 2017 lines). "
    "Its only finding was P2: no automated tests discovered."
)


@pytest.fixture(scope="module")
def workspace(tmp_path_factory) -> Path:
    """The owner's real project, or a fixture of the same shape."""

    if OWNER_WORKSPACE.is_dir():
        return OWNER_WORKSPACE
    root = tmp_path_factory.mktemp("VOOL WEBSITE FIXTURE")
    (root / "app-landing").mkdir()
    (root / "assets").mkdir()
    (root / "app-landing" / "index.html").write_text(
        "<html><body>\n"
        "<div onclick=\"go()\">x</div>\n"
        "<script>var t=localStorage.getItem('token');</script>\n"
        "</body></html>\n",
        encoding="utf-8",
    )
    (root / "assets" / "styles.css").write_text("body{margin:0}\n", encoding="utf-8")
    (root / "assets" / "app.js").write_text("console.log(1);\n", encoding="utf-8")
    (root / "README.md").write_text("# site\n", encoding="utf-8")
    return root


@pytest.fixture(autouse=True)
def _clean():
    execution_records.clear()
    yield
    execution_records.clear()


def _run_audit(workspace: Path, said: str):
    reads: list[str] = []

    def spy(intent, arguments=None, source_context=None, **kwargs):
        if intent == "workspace.read_file":
            reads.append(str((arguments or {}).get("path") or ""))
        return execute_runtime_tool(intent, arguments or {}, source_context=source_context)

    report, steps = run_workspace_audit(
        str(workspace),
        source_context={"workspace": str(workspace)},
        execute_tool=spy,
        emit=lambda *a, **k: None,
        target_path=audit_target_in(said),
    )
    return report, reads, steps


# --------------------------------------------------------------------------------------
# The workspace itself — the conditions never previously tested
# --------------------------------------------------------------------------------------


def test_the_workspace_shape_is_what_broke_things(workspace: Path) -> None:
    assert not (workspace / ".git").exists(), "a non-git project is the untested condition"
    assert " " in workspace.name, "a space in the path is the other untested condition"


def test_the_named_file_exists_to_be_audited(workspace: Path) -> None:
    assert (workspace / "app-landing" / "index.html").is_file()


# --------------------------------------------------------------------------------------
# "run audit for this - app-landing/index.html"
# --------------------------------------------------------------------------------------


def test_the_audit_opens_the_file_the_owner_named(workspace: Path) -> None:
    """The whole failure in one assertion: it never opened it."""

    _report, reads, _steps = _run_audit(workspace, SAID_AUDIT)
    assert any("app-landing/index.html" in path for path in reads), reads


def test_the_owners_file_is_auditable_at_all(workspace: Path) -> None:
    """`.html` was not source, so a static site had nothing to audit."""

    assert is_source_path("app-landing/index.html") is True


def test_the_report_carries_the_files_real_source(workspace: Path) -> None:
    report, _reads, _steps = _run_audit(workspace, SAID_AUDIT)
    assert "## Requested file — app-landing/index.html" in report
    body = (workspace / "app-landing" / "index.html").read_text(errors="ignore")
    sample = next((line.strip() for line in body.splitlines() if "<script" in line), "")
    assert sample and sample[:40] in report, "the model must receive the source, not just the name"


def test_the_fabricated_claim_is_refused_when_the_file_was_not_opened(workspace: Path) -> None:
    """Reproduces the exact failure: the audit read other files and claimed the work."""

    from core.runtime_tool_contracts import ToolClaim

    claim = ToolClaim(target_argument="path", resolved_target_key="path")
    for path in ("README.md", "site/assets/site.js"):
        execution_records.record(
            session_id="replay",
            intent="workspace.read_file",
            arguments={"path": path},
            observation={"ok": True, "path": f"{workspace}/{path}"},
            claim=claim,
        )
    result = enforce_final_action_honesty(
        {"response": FABRICATED_REPLY, "confidence": 0.9},
        user_input=SAID_AUDIT,
        session_id="replay",
    )
    assert result.get("route_reason") == "unsupported_inspection_claim"
    assert "app-landing/index.html" in result["response"]


def test_the_same_claim_stands_once_the_file_really_was_read(workspace: Path) -> None:
    """The guard must not simply block every audit."""

    from core.runtime_tool_contracts import ToolClaim

    claim = ToolClaim(target_argument="path", resolved_target_key="path")
    execution_records.record(
        session_id="replay",
        intent="workspace.read_file",
        arguments={"path": "app-landing/index.html"},
        observation={"ok": True, "path": f"{workspace}/app-landing/index.html"},
        claim=claim,
    )
    result = enforce_final_action_honesty(
        {"response": FABRICATED_REPLY, "confidence": 0.9},
        user_input=SAID_AUDIT,
        session_id="replay",
    )
    assert result.get("route_reason") != "unsupported_inspection_claim"


def test_the_follow_up_still_routes_to_the_audit(workspace: Path) -> None:
    """"did u run the audit!??!?!" must stay on the audit lane, not fall to plain chat."""

    from core.agent_runtime.workspace_audit import looks_like_code_audit_request

    assert looks_like_code_audit_request(SAID_DID_YOU) is True


# --------------------------------------------------------------------------------------
# "what folder is in use for this workspace?"
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("said", [SAID_WORKSPACE, SAID_WORKSPACE_2])
def test_the_workspace_question_is_claimed_deterministically(said: str) -> None:
    assert _asks_which_workspace(said) is True


def test_the_workspace_answer_is_one_line_not_a_tree(workspace: Path) -> None:
    result = execute_runtime_tool(
        "workspace.identity", {}, source_context={"workspace": str(workspace)}
    )
    assert result.ok
    text = str(result.response_text or "")
    assert str(workspace) in text
    assert text.count("\n") <= 1, "a metadata question must not answer with a listing"
    observation = (result.details or {}).get("observation") or {}
    assert observation.get("is_git_repository") is False
