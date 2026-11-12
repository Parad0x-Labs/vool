"""A Settings toggle says its own state, in words and color.

Measured 2026-11-03 (served chats, build 8af687ca): the operator could not tell an on toggle
from an off one without studying the knob — the switch rendered only `aria-checked` (a knob's
position), with no words and no state color. These tests pin the repaired contract on the
rendered page: every toggle control carries a state word ("On"/"Off") beside the switch, the
word and its dot use the same green/red vocabulary as every status pill on the page
(--ok/--bad), and ONE painter drives every state change (initial render, click, refused-write
snap-back, Retry) so the words can never drift from what aria-checked says.
"""

from __future__ import annotations

from core.vool_settings_page import render_vool_settings_html


def _page() -> str:
    return render_vool_settings_html(build_commit="test")


def test_toggle_controls_carry_a_state_word_next_to_the_switch() -> None:
    html = _page()
    assert 'el(\'div\', \'sw-row\')' in html, "switch and its state word share one row"
    assert "el('span', 'sw-state')" in html, "the state word is rendered beside the switch"
    assert "paintSwitchState(b, v)" in html, "initial paint comes from the stored value"


def test_the_state_word_says_on_and_off_in_words() -> None:
    html = _page()
    assert "on ? 'On' : 'Off'" in html, "the word names the state, never only a color"


def test_the_state_word_uses_the_pill_green_and_red() -> None:
    """Same vocabulary as the status pills: --ok for on, --bad for off, never new colors."""
    html = _page()
    assert ".sw-state.on { color:var(--ok); }" in html
    assert ".sw-state.off { color:var(--bad); }" in html
    assert ".sw-state.on .sw-dot { background:var(--ok); }" in html
    assert ".sw-state.off .sw-dot { background:var(--bad); }" in html


def test_one_painter_drives_every_state_change() -> None:
    """Click, snap-back and Retry must all go through the same painter."""
    html = _page()
    assert html.count("paintSwitchState(") >= 4, (
        "initial paint, both click transitions, and the Retry snap-back all repaint"
    )
    assert "paintSwitchState(b, shownValue(row))" in html, "the authority's answer repaints"
    assert "if (b) paintSwitchState(b, valueOf(row))" in html, "Retry repaints from the store"
    # The old direct-write path is gone: nothing may set aria-checked outside the painter.
    assert "setAttribute('aria-checked'" not in html.split("function paintSwitchState")[0].split("function control")[-1].replace(
        "paintSwitchState(b, v)", ""
    ), "control() no longer writes aria-checked by hand"
