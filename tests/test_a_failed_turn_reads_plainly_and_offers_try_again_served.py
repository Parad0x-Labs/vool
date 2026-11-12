"""A failed turn reads as plain words and offers Try again: the served journey, on the real page.

App bugs 2026-11-12, seen on the real app (247124ed, a local model behind a fault proxy):

  C1  a fault that persisted through the retry ended the turn with a line that read like a log:
      "`qwen3.5:4b` failed before producing a usable answer. No cached or remembered text was
      substituted. Retry the turn." The card offered only View activity and Report a problem.
  C3  a 401 from the LOCAL model was explained as a cloud key problem: "ollama-local rejected the
      stored API key (authentication failed), so no cloud model could run this turn ... Check the
      key in Settings → API Keys". A model on this computer has no key to check.

One real daemon (``apps.vool_api_server``) in a disposable VOOL_HOME, one Ollama-dialect loopback
provider behind the real HTTP door with a fault queue for the answer call, the model certified
through the daemon's own probe, and Chromium on the real chat page's composer:

  S1  the answer call fails with 503 twice (the first call and its one same-model retry): the
      answer is one plain sentence that names the model, says nothing was made up, and carries no
      code span or log wording; the card shows Try again first, then Report a problem; the
      runtime's own verdict for the turn is ``failed``;
  S2  Try again on that card sends the same question once more through the composer, and the
      healthy model answers it in the same chat;
  S3  the local model answers 401: the reply says the model app on this computer asked for a
      sign-in, and never mentions an API key, a cloud model or Settings → API Keys.

The faults are scripted loopback statuses: this proves what the person reads and can do on the
real served path, not any provider's reliability.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest

from tests._blackbox_served_rig import REPO_ROOT, ServedDaemon, run_in_home
from tests._plain_failure_served_rig import (
    ANSWER,
    CHAT_CAPABILITIES,
    MODEL,
    PROVIDER_NAME,
    SEED,
    FaultableProvider,
    _answers,
    _assert_plain,
    _certify,
    _evidence_dir,
    _mark,
    _outcomes,
    _send,
    _wait_for_answer_with,
)

pytestmark = [pytest.mark.served]


@pytest.fixture
def served(tmp_path: Path):
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    provider = FaultableProvider(ANSWER)
    daemon = ServedDaemon(
        home,
        env_extra={
            "VOOL_WORKSPACE_ROOT": str(workspace),
            "VOOL_MODEL_LOAD_FLOOR_GB": "0",
            "OLLAMA_HOST": provider.base_url,
            "VOOL_OLLAMA_URL": provider.base_url,
            "VOOL_OLLAMA_CHAT_URL": f"{provider.base_url}/api/chat",
        },
    )
    provider.__enter__()
    try:
        daemon.start(timeout=240)
        run_in_home(home, SEED.format(root=REPO_ROOT, provider_name=PROVIDER_NAME, model=MODEL, base_url=provider.base_url, capabilities=CHAT_CAPABILITIES, parameter_billions=2.0))
        provider.reset()
        yield {"home": home, "daemon": daemon, "provider": provider, "evidence": _evidence_dir(tmp_path)}
    finally:
        daemon.stop()
        provider.__exit__(None, None, None)


def test_a_failed_turn_reads_plainly_and_its_card_offers_try_again(served: dict[str, Any]) -> None:
    daemon: ServedDaemon = served["daemon"]
    provider: FaultableProvider = served["provider"]
    home: Path = served["home"]
    evidence: Path = served["evidence"]

    certified = _certify(daemon)
    assert certified.get("state") == "verified", certified
    provider.reset()

    from tests import served_browser

    manager, browser = served_browser.launch_chromium()
    try:
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.goto(f"{daemon.base_url}/chat", wait_until="domcontentloaded")
        page.locator("#input").wait_for(state="visible", timeout=30000)
        question = "Summarize what a heat pump does in one sentence."

        # ------------------------------------------------ S1: the fault persists through the retry
        provider.queue(503, 503)
        started, mark, before = time.time(), _mark(home), _answers(page)
        _send(page, question)
        failed_text = _wait_for_answer_with(page, "couldn't get an answer", after=before)
        assert provider.answer_statuses(since=started) == [503, 503], provider.answer_statuses(since=started)
        assert _outcomes(home, after=mark) == ["failed"]
        assert failed_text.startswith("I couldn't get an answer this time:"), failed_text
        assert MODEL in failed_text, failed_text
        assert "Nothing was made up in its place." in failed_text, failed_text
        assert "You can try again." in failed_text, failed_text
        assert ANSWER not in failed_text
        _assert_plain(failed_text)
        # The model name is plain text in the answer, not a code chip.
        last = page.locator("#log .msg.assistant").last
        assert last.locator(".msg-text code").count() == 0
        # The failed card's row offers the way forward first, in plain sight (no hover needed).
        card = page.locator("#log .task-card").last
        retry = card.locator(".tc-actions .tc-retry")
        retry.wait_for(state="visible", timeout=10000)
        assert retry.inner_text().strip() == "Try again"
        labels = [label.strip() for label in card.locator(".tc-actions button").all_inner_texts()]
        assert labels[0] == "Try again" and "Report a problem" in labels, labels
        page.screenshot(path=str(evidence / "s1-failed-turn-plain-with-try-again.png"), full_page=True)

        # ------------------------------------------------ S2: Try again asks the same question once more
        provider.reset()
        started, mark, before = time.time(), _mark(home), _answers(page)
        retry.click()
        answered = _wait_for_answer_with(page, ANSWER, after=before)
        assert ANSWER in answered
        assert provider.answer_statuses(since=started) == [200] * len(provider.answer_statuses(since=started))
        assert provider.answer_statuses(since=started), "Try again never reached the model"
        users = [text.strip() for text in page.locator("#log .msg.user .msg-text").all_inner_texts()]
        assert users == [question, question], users
        assert _outcomes(home, after=mark) == ["completed"]
        page.screenshot(path=str(evidence / "s2-try-again-answered.png"), full_page=True)

        # ------------------------------------------------ S3: the local model wants a sign-in (401)
        provider.reset()
        provider.queue(401, 401)
        started, mark, before = time.time(), _mark(home), _answers(page)
        _send(page, "Name one planet with rings.")
        refused = _wait_for_answer_with(page, "couldn't get an answer", after=before)
        assert provider.answer_statuses(since=started)[0] == 401
        assert _outcomes(home, after=mark) == ["failed"]
        assert "the model app on this computer would not run" in refused, refused
        assert MODEL in refused and "sign in" in refused, refused
        for cloud_wording in ("API key", "API Keys", "cloud", "ollama-local", "authentication failed"):
            assert cloud_wording not in refused, f"{cloud_wording!r} in a local model's refusal: {refused!r}"
        _assert_plain(refused)
        page.locator("#log .task-card").last.locator(".tc-actions .tc-retry").wait_for(state="visible", timeout=10000)
        page.screenshot(path=str(evidence / "s3-local-401-plain.png"), full_page=True)
    finally:
        try:
            browser.close()
        finally:
            manager.stop()
