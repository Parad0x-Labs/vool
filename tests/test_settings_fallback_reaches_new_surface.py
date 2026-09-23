"""The browser fallback for Settings reaches the NEW surface, never the legacy in-chat panel.

Integration defect (settings redesign x chat repairs): `openSettings` fell back to the legacy
`#settingsOverlay` panel when there was no native bridge AND the popup was blocked. That panel is
the one the settings census found misleading (a fal.ai "Test" that makes no request, connection
messages that always name OpenRouter, a "Remove" pinned to one slot). It stays in the markup --
deleting 800 lines in the chat file is a merge problem, not a settings problem -- but no supported
entry path may show it. The fallback now frames the same /settings page inside the chat document,
so the chat, its session and its unsent draft are untouched.
"""
from __future__ import annotations

import re

from core.vool_chat_page import render_vool_chat_html
from core.vool_settings_page import render_vool_settings_html


def _function_body(html: str, name: str) -> str:
    start = html.index(f"function {name}(")
    end = html.index("\nfunction ", start + 1)
    return html[start:end]


def test_the_popup_blocked_fallback_frames_the_new_surface_and_never_the_legacy_panel():
    html = render_vool_chat_html()
    body = _function_body(html, "openSettings")
    assert "openSettingsInFrame(section)" in body
    assert "openLegacySettingsPanel" not in body
    # Two entry points bind `openSettings` straight as a click listener, so the argument may be the
    # click event; anything but a string section is dropped (measured: '/settings#[object PointerEvent]').
    assert "if (typeof section !== 'string') section = '';" in body
    # The legacy opener survives as a definition only: nothing calls it.
    assert html.count("openLegacySettingsPanel(") == 1
    # And nothing else un-hides the legacy overlay.
    assert html.count("settingsOverlay.hidden = false") == 1
    assert 'id="settingsFrameOverlay"' in html and 'id="settingsFrame"' in html


def test_every_supported_entry_path_routes_through_open_settings():
    html = render_vool_chat_html()
    assert "sBtn.addEventListener('click', openSettings)" in html                      # sidebar button
    assert re.search(r"e\.key === ','\) \{ e\.preventDefault\(\); openSettings\(\); \}", html)  # Cmd+,
    assert "openSettings: () => openSettings()," in html                               # page actions (palette)
    assert "openSettings(typeof d.section === 'string' ? d.section : '');" in html    # setup handoff
    assert "d.type === 'vool-setup-open-settings'" in html
    assert "note.addEventListener('click', openSettings)" in html                       # paid-cloud note
    # The native bridge is still preferred, then the named popup, then the frame.
    body = _function_body(html, "openSettings")
    assert body.index("window.pywebview") < body.index("window.open('/settings'") < body.index("openSettingsInFrame(section)")


def test_the_framed_settings_page_asks_its_host_to_close_instead_of_navigating_the_chat_away():
    html = render_vool_settings_html()
    body = _function_body(html, "closeSettingsWindow")
    assert "window.top !== window.self" in body
    assert "postMessage({ type: 'vool-settings-close' }, window.location.origin)" in body
    # The framed branch returns before the tab path that would load /chat inside the frame.
    assert body.index("postMessage") < body.index("window.location.href = '/chat'")
    chat = render_vool_chat_html()
    listener = chat[chat.index("window.addEventListener('message'"):]
    assert "e.origin !== window.location.origin" in listener[:400]
    assert "d.type === 'vool-settings-close'" in listener[:600]
