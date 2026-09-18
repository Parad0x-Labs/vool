"""Served proof: the chip names its coverage and the card never wears the last turn's verdict.

A real daemon (own home, own port, a scripted local model, every network host refused), a real
Chromium, the real chat page. Two things the delivered 0.5.0 build got wrong are asserted on the
rendered DOM:

* the collapsed Proof Chip of a turn that looked nothing up says `no lookups` beside its state
  word -- the same words a turn that quoted two markets used to show (`0 actions · 0 sources ·
  VERIFIED`) now differ from it;
* while the second turn's request is in flight and the server has acknowledged nothing, the new
  task card's stage line is NOT the first turn's `Completed — not independently reviewed.`.
  The window is made observable by holding the `/api/chat` POST at the browser's network layer
  until the DOM has been read; nothing in the runtime is stubbed or slowed for it.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time

import pytest

import tests._reader_served_rig as rig
from tests.served_browser import launch_chromium

TERMINAL = "Completed — not independently reviewed."
LAST_STAGE = """(() => {
  const s = document.querySelectorAll('#log .task-card .tc-stage');
  return s.length ? s[s.length - 1].textContent : null;
})()"""
LAST_TITLE = """(() => {
  const s = document.querySelectorAll('#log .task-card .tc-titletext');
  return s.length ? s[s.length - 1].textContent : null;
})()"""


def _send(page, text: str) -> None:
    page.fill("#input", text)
    page.click("#send")


def _wait_chips(page, count: int, timeout_ms: int = 180_000) -> None:
    page.wait_for_function(
        "n => document.querySelectorAll('.proof-chip .pc-coverage').length >= n", arg=count, timeout=timeout_ms
    )


@pytest.mark.parametrize('missing_format', ['', 'mermaid', 'table'])
def test_no_lookup_chip_says_so_and_the_next_card_does_not_inherit_the_verdict(tmp_path, missing_format):
    from core.entity_ambiguity import AMBIGUITY_SYSTEM_PROMPT

    def reply(body):
        messages = body.get("messages") or []
        if any(AMBIGUITY_SYSTEM_PROMPT in str(m.get("content", "")) for m in messages):
            return json.dumps({"ambiguous": False, "referents": [], "clarification": ""})
        if missing_format == 'mermaid':
            return '| Item | Count |\n|---|---|\n| Records | 9 |'
        if missing_format == 'table':
            return '```mermaid\nflowchart LR\nCollection --> Archive\n```'
        return "A short, plain reply."

    with rig.CapturingProvider(reply_fn=reply) as provider:
        daemon = rig.ServedDaemon(tmp_path / "home", provider=provider,
                                 env_extra={"VOOL_RAW_OLLAMA_API_URL": provider.base_url})
        daemon.register_provider()
        daemon.start()
        try:
            # This is a proof-display test, so seed the existing author certificate
            # for this exact synthetic model through the shared certification fixture.
            subprocess.run([sys.executable, "-c",
                "from storage.model_provider_manifest import list_provider_manifests; "
                "from tests._authorship_certification import certify_for_authorship; "
                "[certify_for_authorship(m) for m in list_provider_manifests() "
                "if m.provider_name == 'reader-stub' and m.model_name == 'reader-drive:stub']"
            ], cwd=rig.REPO_ROOT, env=daemon.env(), check=True, capture_output=True, timeout=30)
            manager, browser = launch_chromium()
            try:
                page = browser.new_page()
                # The loopback fixture is a registered local model; publish its inventory
                # at the browser boundary so selection follows the real local-model picker.
                page.route("**/api/tags", lambda route: route.fulfill(
                    content_type="application/json",
                    body=json.dumps({"models": [{"name": daemon.model}]}),
                ))
                page.goto(daemon.base_url + "/chat", wait_until="domcontentloaded")
                page.wait_for_selector("#input", timeout=30_000)
                page.click("#modelBtn")
                page.locator(f'.local-dyn[data-model="{daemon.model}"]').click()

                _send(page, "Show a table and Mermaid with Collection -> Archive. Use 9 records."
                      if missing_format else "Say hello in one short sentence.")
                _wait_chips(page, 1)
                coverage = page.inner_text(".proof-chip .pc-coverage")
                state = page.inner_text(".proof-chip .pc-state")
                assert provider.calls, "the pinned loopback model must actually be invoked"
                assert coverage == "no lookups", coverage
                assert state in ({"Record incomplete"} if missing_format else {"Record verified", "Record recorded"}), state
                head = page.inner_text(".proof-chip-head")
                # The successful model execution is one ledger action, with no retrieval sources.
                assert "1 action" in head and "0 sources" in head and "no lookups" in head, head
                if missing_format:
                    page.wait_for_function(
                        "() => { const titles = document.querySelectorAll('#log .task-card .tc-titletext'); "
                        "return titles.length && titles[titles.length - 1].textContent.includes('Failed safely'); }",
                        timeout=15_000,
                    )
                    assert 'No usable answer was produced' in page.inner_text('#log')
                    return
                # The first turn's card settles on its own terminal phrase.
                page.wait_for_function(
                    "t => (() => { const s = document.querySelectorAll('#log .task-card .tc-stage'); "
                    "return s.length && s[s.length - 1].textContent === t; })()",
                    arg=TERMINAL,
                    timeout=30_000,
                )

                # Hold the second turn's POST at the network layer: the request is in flight, the
                # server has said nothing, and the page is exactly in the window measured live.
                held: list = []

                def _hold(route):
                    held.append(route)

                page.route("**/api/chat", _hold)
                _send(page, "Now give me one short fact about owls.")
                deadline = time.time() + 15
                while not held and time.time() < deadline:
                    page.wait_for_timeout(50)
                assert held, "the second turn never reached the network"
                page.wait_for_timeout(300)
                stage_in_flight = page.evaluate(LAST_STAGE)
                title_in_flight = page.evaluate(LAST_TITLE)
                cards = page.evaluate("document.querySelectorAll('#log .task-card').length")
                assert cards == 2, cards
                assert stage_in_flight != TERMINAL, (
                    f"the new card wore the previous turn's verdict while in flight: {stage_in_flight!r} under {title_in_flight!r}"
                )
                held[0].continue_()
                page.unroute("**/api/chat")
                _wait_chips(page, 2)
                coverages = page.eval_on_selector_all(".proof-chip .pc-coverage", "els => els.map(e => e.textContent)")
                assert coverages == ["no lookups", "no lookups"], coverages
            finally:
                browser.close()
                manager.stop()
        finally:
            daemon.stop()
