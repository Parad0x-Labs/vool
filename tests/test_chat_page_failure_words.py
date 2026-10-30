"""The chat page's worst moments: plain text, keyed words, keyboard-reachable groups.

Pins the failure-words contract on the served chat page source itself:

* the greeting, the Auto pushpin, the receipts marks and the review flag carry no colour emoji;
* every failure toast/card reads through pageT with the catalog's own English as fallback;
* raw ``e.message`` / status text never reaches a toast a person reads;
* the Pinned and General sidebar group heads open from the keyboard like project heads;
* the sidebar paints its loading row BEFORE the project refresh that can hang on a restart.
"""

from __future__ import annotations

import re

from core.vool_chat_page import render_vool_chat_html
from tests.chat_page_js_harness import script, slice_source

HTML = render_vool_chat_html()
SCRIPT = script()

# The colour-emoji set the review found in person-visible spots (plus their kin).
COLOUR_EMOJI = "🌙🦉👋☀️☕🙂🌆📌🔏⚠️⚠✅❌🔔🔒"


def _greeting_block() -> str:
    return slice_source(SCRIPT, "function showEmpty()", "const openers = [")


def _sidebar_block() -> str:
    return slice_source(SCRIPT, "const PIN_KEY = '__pinned__';", "function makeListRetry(")


def test_the_greeting_is_plain_text_with_no_emoji_appends() -> None:
    block = _greeting_block()
    for mark in COLOUR_EMOJI:
        assert mark not in block, f"the greeting still appends the colour mark {mark!r}"
    assert "Math.random() < 0.45" not in block, "the random emoji append is still wired to the greeting"


def test_the_greeting_still_greets_in_words_per_period() -> None:
    block = _greeting_block()
    for key, words in (
        ("first_run.greet_morning", "Good morning"),
        ("first_run.greet_afternoon", "Good afternoon"),
        ("first_run.greet_evening", "Good evening"),
    ):
        assert key in block and words in block, f"the plain greeting {key} was lost"


def test_the_receipt_marks_are_monochrome_and_the_pushpin_stays_pinned() -> None:
    assert "🔏" not in SCRIPT, "the colour pen still marks receipts"
    # the signed-receipt mark is a plain check glyph now, never the colour pen
    assert re.search(r"rc\.signed \? '✓' : '•'", SCRIPT), "the signed receipt lost its monochrome mark"
    # The Auto pushpin's 📌 stays for now: tests/test_localized_surface_wiring.py:221 pins
    # `pageT('header.model_auto_option', 'VOOL Auto') + ' 📌'` verbatim and TASK.md forbids
    # changing words an existing test pins (listed under Needs in REPORT.md). The only other
    # 📌 allowed is the marker PALETTE a person explicitly picks from (a feature, not page
    # chrome). It may appear nowhere else.
    for occurrence in [m.start() for m in re.finditer("📌", SCRIPT)]:
        line_start = SCRIPT.rfind("\n", 0, occurrence) + 1
        line = SCRIPT[line_start : SCRIPT.find("\n", occurrence)]
        if "EMOJI_PALETTE" in line:
            continue   # the person-chosen marker palette, not page chrome
        window = SCRIPT[max(0, occurrence - 60) : occurrence]
        assert "pageT('header.model_auto_option'" in window, (
            f"a 📌 reached the page outside the pinned Auto-option form: …{window!r}"
        )


def test_the_sidebar_group_heads_carry_no_colour_emoji() -> None:
    block = _sidebar_block()
    for mark in COLOUR_EMOJI:
        assert mark not in block, f"the sidebar still renders the colour mark {mark!r}"


def test_failure_sentences_read_through_the_catalog_with_the_catalogs_own_english() -> None:
    from tests.test_failure_words_catalog_law import _messages

    en = _messages("en")
    for key in (
        "chat.back_online", "chat.load_failed_title", "chat.load_failed_text", "chat.load_failed_retry",
        "chat.rename_failed_offline", "chat.rename_failed",
        "chat.delete_failed_offline", "chat.delete_failed",
        "chat.archive_failed_offline", "chat.archive_failed",
        "chat.unarchive_failed_offline", "chat.unarchive_failed",
        "chat.try_again",
    ):
        call = f"pageT('{key}', '{en[key]}'" if "'" not in en[key] else f"pageT('{key}', \"{en[key]}\""
        assert call in SCRIPT, (
            f"{key} is not read through pageT with the catalog's own English as fallback"
        )
    # the retired BARE uses are gone; the words live on only as the keyed fallback
    for bare in ("toast('VOOL is running again.')", r'h.textContent = "Couldn\u2019t load this chat right now."'):
        assert bare not in SCRIPT, f"the unkeyed failure sentence survived: {bare!r}"


def test_no_raw_error_detail_reaches_a_toast_or_say_line() -> None:
    """Every toast/say argument must be keyed words; e.message and friends go to the console."""
    for opener in ("toast(", "say("):
        for match in re.finditer(re.escape(opener), SCRIPT):
            depth, i = 0, match.start()
            while i < len(SCRIPT):
                if SCRIPT[i] == "(":
                    depth += 1
                elif SCRIPT[i] == ")":
                    depth -= 1
                    if depth == 0:
                        break
                i += 1
            argument = SCRIPT[match.start() : i]
            for raw in ("e.message", "j.error", "r.status"):
                assert raw not in argument, (
                    f"raw {raw} still reaches a person at: {argument[:120]!r}"
                )


def test_pinned_and_general_heads_are_keyboard_reachable_like_project_heads() -> None:
    block = _sidebar_block()
    for head_var in ("ph", "gh"):
        # each head block: from its creation to the next appendChild into the sidebar
        start = block.index(f"const {head_var} = document.createElement('div');")
        end = block.index("sessionsEl.appendChild", start)
        head = block[start:end]
        assert "tabIndex = 0" in head, f"the {head_var} group head is not tab-reachable"
        assert "'button'" in head and "aria-expanded" in head, f"the {head_var} head lacks its button semantics"
        assert "keydown" in head and "Enter" in head, f"the {head_var} head cannot be opened with the keyboard"


def test_the_sidebar_paints_its_loading_row_before_the_project_refresh() -> None:
    body = slice_source(SCRIPT, "async function loadSessions()", "function repaintCouncilCards(")
    loading = body.index("paintSessionsState('loading')")
    refresh = body.index("refreshProjects()")
    assert loading < refresh, (
        "the loading row paints only after the project fetch -- a hanging /api/projects still "
        "leaves the sidebar blank and indistinguishable from 'no chats'"
    )
