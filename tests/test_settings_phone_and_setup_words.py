"""Settings at phone width, the setup line in one place, no Mac-only words on Linux.

Job settings-phone-linux-words-20261029 (fresh-test item 15, non-money parts):

* at narrow widths the Settings section menu collapses into the drawer pattern the chat page
  already uses, instead of stacking on top and taking half the screen; the desktop column
  must not change;
* the chat page's "Finish setup" line belongs to a NEW, EMPTY chat only -- never to a chat
  that already has messages, and never again once dismissed or complete (those two verdicts
  are the server's, /api/setup/state);
* setup words say "On this computer" everywhere: English source, page fallbacks, and every
  locale stamped against the current English bytes.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from core.i18n.catalog import CATALOGS_DIR
from core.vool_chat_page import render_vool_chat_html
from core.vool_settings_page import render_vool_settings_html
from core.vool_setup_page import render_vool_setup_html

CATALOGS = CATALOGS_DIR
NON_EN = sorted(p.name for p in CATALOGS.glob("*.json") if p.name != "en.json")


def _settings_html() -> str:
    return render_vool_settings_html()


def _media_block(html: str) -> str:
    """The narrow-window media rule and everything after it (the page's last style rules)."""
    at = html.index("@media (max-width: 720px)")
    return html[at:html.index("</style>", at)]


# --- 1. phone width --------------------------------------------------------------------------

def test_narrow_menu_collapses_into_a_drawer() -> None:
    html = _settings_html()
    media = _media_block(html)
    # The section list overlays the page and starts closed; the scrim is its way out.
    assert "#side { position:fixed;" in media
    assert "body:not(.side-open) #side { display:none; }" in media
    assert "body.side-open #sideScrim { display:block; }" in media
    # The old phone layout stacked the whole menu on top and capped its scroll: gone.
    assert "max-height:190px" not in html
    assert "border-bottom:1px solid var(--border);" not in media
    # A way to open the list, stuck to the top of the content so it is always reachable.
    assert 'id="sideToggle"' in html
    assert 'aria-controls="side"' in html
    assert 'data-i18n="settings.nav.menu"' in html
    assert "#sideToggle { display:inline-flex; }" in media
    # The content takes the screen and scrolls in its own pane, not in a leftover strip.
    assert "#pane { flex:1 1 auto; overflow-y:auto;" in media


def test_desktop_layout_is_unchanged() -> None:
    html = _settings_html()
    before_media = html[: html.index("@media (max-width: 720px)")]
    # The static side column is still the desktop layout, outside any media rule.
    assert "#side { flex:0 0 248px; width:248px;" in before_media
    assert "position:fixed" not in before_media.split("#side {", 1)[1].split("}", 1)[0]
    # The drawer pieces are invisible on a wide window.
    assert "#sideScrim { display:none;" in before_media
    assert "#sideToggle { display:none;" in before_media
    # And the toggle is wired: Escape and a section choice close the drawer again.
    assert "closeSide()" in html
    assert "escapeClosesInnermost" in html


# --- 2. the setup line shows in one place only ------------------------------------------------

def test_setup_line_is_gated_to_a_new_empty_chat() -> None:
    html = render_vool_chat_html()
    # Eligibility is the displayed chat's own transcript: no messages, no live run.
    assert "function setupLineEligible()" in html
    assert "return !!view && !view.history.length && !(view.run && !view.run.ended);" in html
    # Both paint paths (the state load and the no-model hand-back) route through the same gate.
    assert html.count("setupLineReflect();") >= 3
    assert "line.hidden = !(setupLineWanted && setupLineEligible());" in html
    # The unconditional "the server said show, so show" paint is gone.
    assert "line.hidden = !show;" not in html
    assert "line.hidden = !setupLineWanted;" not in html
    # The first message in the chat ends the line's eligibility.
    assert "recordUserMessage(chatId" in html  # still the one history choke point


def test_setup_line_reflect_follows_every_emptiness_change() -> None:
    html = render_vool_chat_html()
    # New chats (both minters) and the transcript renderer re-paint; the send path re-paints
    # right after the user message lands in history.
    for anchor in ("function newChat()", "function newChatInProject(", "function renderChat("):
        at = html.index(anchor)
        assert "setupLineReflect();" in html[at : at + 2000], f"no reflect near {anchor}"


# --- 3. no Mac-only words on Linux ------------------------------------------------------------

def test_served_setup_and_settings_never_say_my_mac() -> None:
    for html in (render_vool_setup_html(), _settings_html()):
        assert "On my Mac" not in html
        assert "my Mac" not in html
    # The setup page carries no Mac-only machine words at all. Settings keeps its wallet
    # recovery copy and a label-stripping regex (money rows are the owner's call, listed in
    # the job REPORT) — everything else must speak of this computer.
    setup_html = render_vool_setup_html()
    assert "this Mac" not in setup_html


def test_english_catalog_has_no_mac_words_in_setup_or_the_menu_button() -> None:
    en = json.loads((CATALOGS / "en.json").read_text(encoding="utf-8"))
    messages = en["messages"]
    offenders = {
        key: value
        for key, value in messages.items()
        if key.startswith(("setup.", "settings.nav."))
        and ("my Mac" in value or "this Mac" in value)
    }
    assert not offenders, f"Mac-only words left in en.json: {offenders}"
    # The drawer's label exists in the English source.
    assert messages.get("settings.nav.menu") == "Menu"


def test_every_locale_is_stamped_against_the_current_english_and_carries_the_new_keys() -> None:
    stamp = hashlib.sha256((CATALOGS / "en.json").read_bytes()).hexdigest()
    assert len(NON_EN) == 16, NON_EN
    for name in NON_EN:
        data = json.loads((CATALOGS / name).read_text(encoding="utf-8"))
        assert data.get("source_catalog_sha256") == stamp, f"{name} is stamped against old English bytes"
        messages = data["messages"]
        assert messages.get("settings.nav.menu"), f"{name} has no settings.nav.menu"
        # The computer wording reached every language, Mac wording left none of them.
        for key in (
            "setup.step.thinking.sentence",
            "setup.thinking.local_title",
            "setup.thinking.online_sub",
            "setup.status.saved_thinking",
            "setup.step.name.sentence",
            "setup.caption.thinking",
        ):
            assert key in messages, f"{name} is missing {key}"
            assert "my Mac" not in messages[key], f"{name} {key} still says my Mac"
