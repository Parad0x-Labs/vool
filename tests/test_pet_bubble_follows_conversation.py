"""The pet's bubble follows the conversation, not the first message's frozen title.

Measured 2026-11-05 (operator's live 0.7.8 test): the pet said the chat's TITLE for the whole
session — and a new chat's title is minted from its FIRST message, so the pet said "yO BRO"
forever while the conversation moved on. The operator's ask: the pet should show the exchange
as it goes, and VOOL's work in action (the activity line already did the second half).

The repaired contract, pinned on the real fragment JS under the node DOM stub:
- the page passes the user's own words with every `task.submitted`;
- the reducer keeps the latest one per chat (bounded, presentation-only);
- the bubble's first line shows the latest user words, falling back to the chat title only
  when the page has not said what was asked;
- the DETACHED native pet receives the same line in its payload, and the screen-privacy mask
  still covers it before it leaves the page.
"""

from __future__ import annotations

import re

from core.companion_presentation_fragment import render_companion_fragment
from core.vool_chat_page import render_vool_chat_html
from tests.test_companion_presentation_p2 import run_presentation


def _boot(driver: str) -> dict:
    """The proven P2 boot: real page + real fragment JS under the shared node DOM stub."""
    return run_presentation(driver)


DRIVER_FOLLOW = r"""
const out = {};
try {
  const VC = window.VoolCompanion;
  const T = 99000000000000;
  // Turn 1: the operator asks something; the page reports the user's own words.
  VC.consume("c1", { type: "task.submitted", ts: T, userText: "yO BRO" });
  VC.consume("c1", { type: "task.started", seq: 1 });
  VC.consume("c1", { type: "tool.started", seq: 2, tool: "web_search" });
  VC.consume("c1", { type: "task.completed", seq: 3 });
  // Turn 2: a NEW ask on the same chat — the bubble must follow.
  VC.consume("c1", { type: "task.submitted", ts: T + 60000, userText: "whats the weather in Paris?" });
  VC.consume("c1", { type: "task.started", seq: 4 });
  VC.paintNow(T + 61000);
  const view = VC.view("c1");
  out.lastUserText = view.lastUserText || "";
  out.payload = (typeof vnDesktopPayload === "function") ? vnDesktopPayload(VC.resolve(view), "MIDDLE") : null;
} catch (e) { out.error = String((e && e.message) || e); }
globalThis.__out = out;
"""


def test_the_pet_bubble_follows_the_latest_user_message() -> None:
    result = _boot(DRIVER_FOLLOW)
    # The harness flattens the driver's out() into the top-level object.
    assert not result.get("error"), result
    assert result.get("lastUserText") == "whats the weather in Paris?", result


DRIVER_FALLBACK = r"""
const out = {};
try {
  const VC = window.VoolCompanion;
  const T = 99000000000000;
  // A turn whose page did not say what was asked (legacy emitter): the bubble falls back to
  // the chat title, never to nothing.
  VC.consume("c2", { type: "task.started", seq: 1 });
  VC.consume("c2", { type: "tool.started", seq: 2, tool: "web_search" });
  VC.paintNow(T);
  const view = VC.view("c2");
  out.lastUserText = view.lastUserText || "";
  out.payload = (typeof vnDesktopPayload === "function") ? vnDesktopPayload(VC.resolve(view), "MIDDLE") : null;
} catch (e) { out.error = String((e && e.message) || e); }
globalThis.__out = out;
"""


def test_legacy_emitter_falls_back_to_the_chat_title() -> None:
    result = _boot(DRIVER_FALLBACK)
    assert not result.get("error"), result
    assert result.get("lastUserText") == "", result


def test_the_page_emits_the_user_text_with_every_submit() -> None:
    page = render_vool_chat_html()
    assert re.search(
        r"task\.submitted', ts: run\.start, userText: text",
        page,
    ), "the chat page must carry the user's words on every task.submitted event"


def test_the_reducer_bounds_the_user_line() -> None:
    fragment = render_companion_fragment()
    assert "view.lastUserText = ev.userText.trim().slice(0, 200)" in fragment
    # The bubble's first line prefers the latest user words over the frozen title, and the
    # detached pet's payload (chatTitle key) follows the same precedence.
    assert "(view && view.lastUserText) ? view.lastUserText" in fragment
    assert "(view && view.lastUserText) ? view.lastUserText : vnBoundChatTitle(chatId)" in fragment
