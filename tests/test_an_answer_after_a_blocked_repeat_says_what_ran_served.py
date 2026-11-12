"""After a blocked repeat command, the answer says what ran: the served journey, on the real page.

App bugs 2026-11-12 (real app, 247124ed, F1c): "run the command: sed -n 2p orders/week.csv" ran the
command once, the model asked to run the same command again, the repeat guard ended the loop, and
the call that writes the answer (offered no tools) was told "Do not repeat an identical tool call.
Continue the tool actions needed to fulfill the user's request". qwen3.5:4b's answer opened with
"I can't run that command." although the command had run and its output was in the same answer.

One real daemon in a disposable VOOL_HOME, the real sandbox (bubblewrap) running the real command,
a loopback model that asks for the command twice the way the real model did, and Chromium on the
real chat page. The answer-writing call is read at the model's own door: it must say the actions
already ran and that its reply is the final answer, and it must not tell a call with no tools to
keep running tools. The loopback writes its answer the way qwen3.5:4b did on the real app: told to
continue with no tools, it says it can't run the command. The real model's answer is proven on the
Mac (missions script in the hand-over).
"""

from __future__ import annotations

import json
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
    _certify,
    _evidence_dir,
    _mark,
    _outcomes,
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
OBSERVATION_HEADER = "Real tool observations from this same turn follow."
LOOP_INSTRUCTION = "Continue the tool actions needed"
FINAL_INSTRUCTION = "These actions already ran for the user's request in this turn"
#: qwen3.5:4b's answer on the real app when the answer-writing call was told to continue.
REAL_APP_ANSWER = (
    "I can't run that command.\n\nThat specific instruction (sed -n 2p orders/week.csv) was already executed "
    "in the context provided just now, and its output is available. Running it again would be redundant."
)
GOOD_ANSWER = f"I ran {COMMAND} in your workspace. It printed one line: {OUTPUT}"


class RepeatingModel(FaultableProvider):
    """Asks to run the command on every call that offers the tool (so the second ask is a repeat),
    and writes the answer from what its prompt tells it to do."""

    def reply(self, messages: list[dict[str, Any]], prompt: str, tool_names: list[str], lane: str) -> Any:
        if lane == "answer" and self.command:
            if "sandbox__run_command" in tool_names:
                return {"tool_calls": [{"id": f"run-{time.monotonic_ns()}", "type": "function", "function": {
                    "name": "sandbox__run_command", "arguments": {"command": self.command}}}]}
            if not tool_names and OBSERVATION_HEADER in prompt:
                return REAL_APP_ANSWER if LOOP_INSTRUCTION in prompt else GOOD_ANSWER
        return super().reply(messages, prompt, tool_names, lane)


@pytest.fixture
def served(tmp_path: Path):
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    (workspace / "orders").mkdir(parents=True)
    (workspace / "orders" / "week.csv").write_text(f"id,total\n{OUTPUT}\n", encoding="utf-8")
    provider = RepeatingModel(ANSWER, model=MODEL)
    daemon = ServedDaemon(
        home,
        env_extra={
            "VOOL_WORKSPACE_ROOT": str(workspace),
            "VOOL_MODEL_LOAD_FLOOR_GB": "0",
            "OLLAMA_HOST": provider.base_url,
            "VOOL_OLLAMA_URL": provider.base_url,
            "VOOL_OLLAMA_CHAT_URL": f"{provider.base_url}/api/chat",
            # The command lane, as on the real app's run: with the clean loop on, a command request
            # is answered from the files lane, which runs no command.
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


def _event_types(home: Path, *, after: int) -> list[str]:
    return [str(row[0]) for row in _rows(home, "SELECT event_type FROM runtime_session_events WHERE rowid>? ORDER BY rowid", (after,))]


def test_the_answer_after_a_blocked_repeat_says_what_the_command_showed(served: dict[str, Any]) -> None:
    daemon: ServedDaemon = served["daemon"]
    provider: RepeatingModel = served["provider"]
    home: Path = served["home"]
    evidence: Path = served["evidence"]

    certified = _certify(daemon, MODEL)
    assert certified.get("state") == "verified", certified
    provider.reset()
    provider.command = COMMAND

    from tests import served_browser

    manager, browser = served_browser.launch_chromium()
    try:
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.goto(f"{daemon.base_url}/chat", wait_until="domcontentloaded")
        page.locator("#input").wait_for(state="visible", timeout=30000)

        started, mark, before = time.time(), _mark(home), _answers(page)
        _send(page, f"run the command: {COMMAND}")
        # Both answers the loopback can write name the command, so the wait never decides the verdict.
        text = _wait_for_answer_with(page, COMMAND, after=before)
        page.screenshot(path=str(evidence / "f1c-blocked-repeat-says-what-ran.png"), full_page=True)

        # The real turn: the command ran once, the model asked for it again, the guard stopped it.
        events = _event_types(home, after=mark)
        assert events.count("tool_executed") == 1, events
        assert "tool_repeat_blocked" in events, events
        assert _outcomes(home, after=mark) == ["completed"]

        calls = [call for call in provider.calls if call["lane"] == "answer" and call["t"] >= started]
        loop_calls = [call for call in calls if "sandbox__run_command" in call["tools"]]
        answer_calls = [call for call in calls if not call["tools"] and OBSERVATION_HEADER in call["full"]]
        assert len(loop_calls) == 2, [call["tools"] for call in calls]
        assert len(answer_calls) == 1, [call["tools"] for call in calls]
        # Inside the loop the model is still told to continue; the answer-writing call is told the
        # command already ran and its reply is the final answer.
        assert LOOP_INSTRUCTION in loop_calls[1]["full"]
        final_prompt = answer_calls[0]["full"]
        assert FINAL_INSTRUCTION in final_prompt and "this reply is the final answer" in final_prompt
        assert LOOP_INSTRUCTION not in final_prompt and "Do not repeat an identical tool call" not in final_prompt
        assert OUTPUT in final_prompt

        # What the person reads.
        assert not text.lstrip().lower().startswith("i can't run"), text
        assert "can't run that command" not in text, text
        assert OUTPUT in text and COMMAND in text, text
        (evidence / "f1c-answer-prompt.json").write_text(
            json.dumps({"answer": text, "answer_call_prompt_tail": final_prompt[-2400:]}, indent=2), encoding="utf-8"
        )
    finally:
        try:
            browser.close()
        finally:
            manager.stop()
