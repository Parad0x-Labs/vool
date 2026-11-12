"""Linux without bubblewrap: a command turn says so in one plain sentence, on the real page.

App bugs 2026-11-12 (real app, F1): with no bubblewrap on the box, "run the command: sed -n 2p
orders/week.csv" answered with the sandbox's engineering paragraph ("Sandbox isolation
unavailable: OS-level filesystem confinement and network isolation are required ... bwrap ...
sandbox-exec ... util-linux ... network_isolation_mode='heuristic_only' ...").

One real daemon whose PATH holds every system program except bwrap (the only test seam; nothing in
VOOL is replaced), a loopback model that asks to run the command the way a model does, and
Chromium on the real chat page. The refusal is unchanged (no command runs unconfined); what the
person reads is one plain sentence with the one thing to do.
"""

from __future__ import annotations

import os
import sys
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
    _send,
    _wait_for_answer_with,
)

pytestmark = [
    pytest.mark.served,
    pytest.mark.skipif(not sys.platform.startswith("linux"), reason="bubblewrap is the Linux sandbox"),
]

#: Tool selection needs a model of at least 4B (core.model_selection_policy); the command turn on
#: the real app ran on qwen3.5:4b.
MODEL = "qwen3-stub:8b"

ENGINEERING_WORDS = (
    "Sandbox isolation unavailable",
    "OS-level",
    "confinement",
    "sandbox-exec",
    "util-linux",
    "namespace",
    "network_isolation_mode",
    "backend",
)


def _path_without_bwrap(root: Path) -> str:
    """A PATH holding every program the system PATH holds, except bwrap."""
    bin_dir = root / "bin-without-bwrap"
    bin_dir.mkdir()
    for directory in os.environ.get("PATH", "").split(os.pathsep):
        if not directory or not Path(directory).is_dir():
            continue
        for entry in Path(directory).iterdir():
            target = bin_dir / entry.name
            if entry.name == "bwrap" or target.exists() or target.is_symlink():
                continue
            target.symlink_to(entry)
    return str(bin_dir)


@pytest.fixture
def served(tmp_path: Path):
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "orders").mkdir()
    (workspace / "orders" / "week.csv").write_text("id,total\nreceipt-51ab-cobalt,125\n", encoding="utf-8")
    provider = FaultableProvider(ANSWER, model=MODEL)
    daemon = ServedDaemon(
        home,
        env_extra={
            "VOOL_WORKSPACE_ROOT": str(workspace),
            "VOOL_MODEL_LOAD_FLOOR_GB": "0",
            "OLLAMA_HOST": provider.base_url,
            "VOOL_OLLAMA_URL": provider.base_url,
            "VOOL_OLLAMA_CHAT_URL": f"{provider.base_url}/api/chat",
            "PATH": _path_without_bwrap(tmp_path),
            # The command lane, as on 247124ed where the clean loop was off by default; with it on,
            # a command request is answered from the files lane, which runs no command.
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


def test_a_command_turn_without_bubblewrap_answers_with_one_plain_sentence(served: dict[str, Any]) -> None:
    daemon: ServedDaemon = served["daemon"]
    provider: FaultableProvider = served["provider"]
    home: Path = served["home"]
    evidence: Path = served["evidence"]

    certified = _certify(daemon, MODEL)
    assert certified.get("state") == "verified", certified
    provider.reset()
    provider.command = "sed -n 2p orders/week.csv"

    from tests import served_browser

    manager, browser = served_browser.launch_chromium()
    try:
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.goto(f"{daemon.base_url}/chat", wait_until="domcontentloaded")
        page.locator("#input").wait_for(state="visible", timeout=30000)

        mark, before = _mark(home), _answers(page)
        _send(page, "run the command: sed -n 2p orders/week.csv")
        text = _wait_for_answer_with(page, "can't run commands", after=before)
        page.screenshot(path=str(evidence / "f1-no-bubblewrap-plain.png"), full_page=True)

        assert text.startswith("I can't run commands on this computer yet:"), text
        assert "sudo apt install bubblewrap" in text, text
        for word in ENGINEERING_WORDS:
            assert word not in text, f"{word!r} reached the person: {text!r}"
        assert "receipt-51ab-cobalt" not in text, "a command ran with no sandbox"
        assert _mark(home) > mark
    finally:
        try:
            browser.close()
        finally:
            manager.stop()
