"""After a command ran under a model not yet checked to write answers: the served journey, on the page.

Ledger V-050 (2026-11-12, served chat on the v0.8 line): an uncertified model asked to run a
command, the command ran, the model wrote nothing after it, and the reply said "I can't publish this
answer ... nothing this turn retrieved, computed or observed backs it".

One real daemon in a disposable VOOL_HOME, the real sandbox (bubblewrap) running the real command,
a loopback model that is NOT certified (no certification run), asks for the command once and writes
nothing after the result, and Chromium on the real chat page. The reply names the command and shows
what it printed; it never says nothing backs it, and it is not the uncertified-writer refusal.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from tests._blackbox_served_rig import REPO_ROOT, ServedDaemon, run_in_home
from tests._plain_failure_served_rig import (
    ANSWER,
    CHAT_CAPABILITIES,
    PROVIDER_NAME,
    SEED,
    FaultableProvider,
    _answers,
    _evidence_dir,
    _mark,
    _rows,
    _send,
    _wait_for_answer_with,
)


def _sandbox_runs_here() -> bool:
    bwrap = shutil.which("bwrap")
    if not bwrap:
        return False
    probe = subprocess.run([bwrap, "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc", "--unshare-all", "true"], capture_output=True, timeout=30)
    return probe.returncode == 0


pytestmark = [
    pytest.mark.served,
    pytest.mark.skipif(not sys.platform.startswith("linux"), reason="the real command runs in the Linux sandbox"),
    pytest.mark.skipif(sys.platform.startswith("linux") and not _sandbox_runs_here(), reason="bubblewrap cannot start on this machine"),
]

#: Tool selection needs a model of at least 4B (core.model_selection_policy).
MODEL = "qwen3-stub:8b"
COMMAND = "sed -n 2p orders/week.csv"
OUTPUT = "receipt-51ab-cobalt,125"
NOTHING_BACKS_IT = "nothing this turn retrieved, computed or observed backs it"


class QuietAfterTheCommand(FaultableProvider):
    """Asks to run the command once, then writes nothing (the measured model's move)."""

    def reply(self, messages: list[dict[str, Any]], prompt: str, tool_names: list[str], lane: str) -> Any:
        if lane == "answer" and self.command:
            if "sandbox__run_command" in tool_names and not any(str(m.get("role") or "") == "tool" for m in messages) \
                    and "Real tool observations from this same turn follow" not in prompt:
                return {"tool_calls": [{"id": "run-1", "type": "function", "function": {
                    "name": "sandbox__run_command", "arguments": {"command": self.command}}}]}
            return ""
        return super().reply(messages, prompt, tool_names, lane)


@pytest.fixture
def served(tmp_path: Path):
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    (workspace / "orders").mkdir(parents=True)
    (workspace / "orders" / "week.csv").write_text(f"id,total\n{OUTPUT}\n", encoding="utf-8")
    provider = QuietAfterTheCommand(ANSWER, model=MODEL)
    daemon = ServedDaemon(
        home,
        env_extra={
            "VOOL_WORKSPACE_ROOT": str(workspace),
            "VOOL_MODEL_LOAD_FLOOR_GB": "0",
            "OLLAMA_HOST": provider.base_url,
            "VOOL_OLLAMA_URL": provider.base_url,
            "VOOL_OLLAMA_CHAT_URL": f"{provider.base_url}/api/chat",
            # The command lane, as on the measured run (the clean loop answers from the files lane).
            "VOOL_CLEAN_LOCAL_LOOP": "0",
        },
    )
    provider.__enter__()
    try:
        daemon.start(timeout=240)
        run_in_home(home, SEED.format(root=REPO_ROOT, provider_name=PROVIDER_NAME, model=MODEL, base_url=provider.base_url, capabilities=[*CHAT_CAPABILITIES, "tool_intent"], parameter_billions=8.0))
        provider.reset()
        yield {"home": home, "daemon": daemon, "provider": provider, "evidence": _evidence_dir(tmp_path)}
    finally:
        daemon.stop()
        provider.__exit__(None, None, None)


def test_a_command_that_ran_under_an_unchecked_model_answers_with_its_result(served: dict[str, Any]) -> None:
    daemon: ServedDaemon = served["daemon"]
    provider: QuietAfterTheCommand = served["provider"]
    home: Path = served["home"]
    evidence: Path = served["evidence"]
    # No certification run: the model is not cleared to write answers here.
    provider.command = COMMAND

    from tests import served_browser

    manager, browser = served_browser.launch_chromium()
    try:
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.goto(f"{daemon.base_url}/chat", wait_until="domcontentloaded")
        page.locator("#input").wait_for(state="visible", timeout=30000)

        mark, before = _mark(home), _answers(page)
        _send(page, f"run the command: {COMMAND}")
        text = _wait_for_answer_with(page, COMMAND, after=before)
        page.screenshot(path=str(evidence / "v050-command-result-backs-the-answer.png"), full_page=True)

        executed = [row for row in _rows(home, "SELECT event_type FROM runtime_session_events WHERE rowid>? ORDER BY rowid", (mark,)) if row[0] == "tool_executed"]
        assert len(executed) == 1, "the command did not run exactly once"
        assert NOTHING_BACKS_IT not in text, text
        assert "I can't publish this answer" not in text, text
        assert f"I ran {COMMAND}" in text and OUTPUT in text, text
    finally:
        try:
            browser.close()
        finally:
            manager.stop()
        time.sleep(0)
