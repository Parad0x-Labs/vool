"""A buffered served turn that ran a command commits what the streamed lane commits, not a 500.

Measured on 247124ed and again on bb35ff83 through the real daemon: a buffered /api/chat turn
ran `python3 build.py` through `sandbox__run_command`, the model's summary came back empty, and
the person got HTTP 500 "K-08 violation: final-action-honesty would mutate post-admission bytes".
The command had run (its file was rewritten); the streamed lane, with the same model and the same
script, committed an answer. The buffered door re-checked the honesty seal under the request id
instead of the turn id the seal used, so the turn's own execution looked unbacked (see
tests/test_sealed_turn_key_boundary.py for the unit-level account).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests._blackbox_served_rig import (
    REPO_ROOT,
    SEED_MANIFEST,
    ScriptedProvider,
    ServedDaemon,
    _chat_with_operator,
    run_in_home,
)
from tests.test_code_assistant_served_journey import _stub_models_are_resident

pytestmark = [pytest.mark.served]

MODEL = "qwen3-stub:2b"
PROMPT = "Run the build script in my project with python3 and tell me what happened"
ORIGINAL = "original line\n"
REWRITTEN = "rewritten by the build\n"
# The buffered reply lists the steps it ran; the streamed lane showed them live as tool events
# and leaves them out of the text (research_tool_loop_facade, include_step_summary). Everything
# else in the committed answer must match.
_STEP_SUMMARY_RE = re.compile(r"\n*Real steps completed:\n(?:- [^\n]*\n?)+")


def _committed(reply: dict) -> str:
    commit = reply.get("vool_response_commit")
    return str(commit.get("canonical_content") or "") if isinstance(commit, dict) else ""


def test_a_buffered_command_turn_with_an_empty_summary_commits_like_the_streamed_lane(tmp_path: Path) -> None:
    home, workspace = tmp_path / "home", tmp_path / "project"
    workspace.mkdir()
    (workspace / "keep.txt").write_text(ORIGINAL, encoding="utf-8")
    (workspace / "build.py").write_text(
        f"open('keep.txt', 'w').write({REWRITTEN!r})\nprint('built')\n", encoding="utf-8"
    )
    run_command = {
        "id": "call-run",
        "type": "function",
        "function": {"name": "sandbox__run_command", "arguments": {"command": "python3 build.py"}},
    }
    provider = ScriptedProvider({MODEL: run_command}, after_tool_result={MODEL: ""})
    provider.__enter__()
    try:
        _stub_models_are_resident(provider)
        daemon = ServedDaemon(
            home,
            env_extra={
                "VOOL_ALWAYS_ON_CATALOG": "1",
                "VOOL_WORKSPACE_ROOT": str(workspace),
                "OLLAMA_HOST": provider.base_url,
                "VOOL_OLLAMA_URL": provider.base_url,
                "VOOL_OLLAMA_CHAT_URL": f"{provider.base_url}/api/chat",
            },
        )
        try:
            daemon.start(timeout=300)
            run_in_home(home, SEED_MANIFEST.format(root=REPO_ROOT, base_url=provider.base_url, registered=[MODEL]))
            provider.reset()

            buffered = _chat_with_operator(daemon, home, "command-turn-buffered", PROMPT, model=MODEL)
            assert (workspace / "keep.txt").read_text(encoding="utf-8") == REWRITTEN, "the command did not run"

            (workspace / "keep.txt").write_text(ORIGINAL, encoding="utf-8")
            daemon.chat = daemon.chat_stream  # the same journey through the lane the chat page uses
            streamed = _chat_with_operator(daemon, home, "command-turn-streamed", PROMPT, model=MODEL)
            assert (workspace / "keep.txt").read_text(encoding="utf-8") == REWRITTEN, "the command did not run"
            log = daemon.log_tail(600)
        finally:
            daemon.stop()
    finally:
        provider.__exit__(None, None, None)

    assert "K-08 violation" not in log
    assert _committed(buffered), "the buffered turn committed no answer"
    assert "Real steps completed:" in _committed(buffered), "the buffered turn did not list the command it ran"
    assert "Real steps completed:" not in _committed(streamed)
    assert _STEP_SUMMARY_RE.sub("\n", _committed(buffered)).strip() == _committed(streamed).strip()
