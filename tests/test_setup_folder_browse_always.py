"""The Setup folder step always offers Browse; capability resolves on press, not on render.

Measured 2026-11-05 (operator's live 0.7.8 test): Setup's folder step showed manual
typing/pasting only — no Browse. The control gated the "Choose a folder…" button on a
ONE-TIME `hasFolderPicker()` check that ran when the row rendered, but pywebview injects its
bridge ASYNCHRONOUSLY after the page loads — in the very window that has the native picker,
the render-time check ran before the bridge arrived and hid the button forever.

The repaired contract:
- the Browse button ALWAYS renders (a hidden button reads as "the product cannot browse");
- capability is resolved at CLICK time, waiting a bounded moment for the bridge;
- a window with no bridge at all (plain browser tab — no web API can hand over a real folder
  path, by browser design) gets an honest click-time note instead of a pre-emptive lie;
- the framed-in-chat path is untouched (host bridge answers via postMessage).
"""

from __future__ import annotations

from core.vool_setup_page import render_vool_setup_html as render_setup_page


def _page() -> str:
    return render_setup_page()


def test_the_browse_button_always_renders() -> None:
    html = _page()
    assert "row.appendChild(choose);" in html, "Browse must render unconditionally"
    # The old render-time gate is gone from the folder control.
    assert "if (hasFolderPicker()) row.appendChild(choose);" not in html


def test_capability_resolves_on_click_with_a_bounded_bridge_wait() -> None:
    html = _page()
    assert "awaitNativeBridge" in html
    assert "3000" in html, "the bridge wait is bounded (3s), never infinite"
    assert "pick_folder" in html


def test_no_bridge_click_gets_an_honest_note() -> None:
    html = _page()
    assert "NO_BRIDGE" in html
    assert "setup.folder.no_bridge" in html
    assert "open VOOL in its app window" in html


def test_the_framed_host_path_is_untouched() -> None:
    html = _page()
    assert "vool-setup-pick-folder" in html, "framed pages still ask the host"
    assert "window.parent.postMessage({ type: 'vool-setup-pick-folder' }" in html


def test_the_render_time_hint_is_gone() -> None:
    """The typed-path hint's RENDER-TIME use is gone (the i18n catalog entry stays — harmless).

    The old control hid Browse AND showed "type the path instead" whenever its one-time bridge
    check ran before pywebview injected — a pre-emptive lie about the window's capability.
    """
    html = _page()
    assert "if (!hasFolderPicker()) host.appendChild" not in html
    assert "if (hasFolderPicker()) row.appendChild(choose);" not in html
