"""Media Studio opens from the Skills panel, not from the crowded header (operator, 2026-09-07).

Served real-Chromium proof: the header carries no Media Studio link; the Skills panel shows a Media Studio entry whose
link opens the same dedicated editor window as before (/media-editor, new window).
"""
from __future__ import annotations

import os

import pytest

import tests._reader_served_rig as rig
from tests.served_browser import launch_chromium


def test_the_chat_page_source_moved_the_link_out_of_the_header():
    from core import vool_chat_page as m

    html = m.render_vool_chat_html()
    head = html[html.index("<header>"):html.index("</header>")]
    assert "Media Studio" not in head and "/media-editor" not in head, "the header still carries the Media Studio link"
    assert "/media-editor" in html, "the editor route must still be reachable from the page"


@pytest.fixture(scope="module")
def served(tmp_path_factory):
    home = tmp_path_factory.mktemp("media-skills") / "home"
    with rig.CapturingProvider(default="ok") as provider:
        daemon = rig.ServedDaemon(home, provider=provider, env_extra={"PLAYWRIGHT_BROWSERS_PATH": os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "")})
        try:
            daemon.start(timeout=180)
        except RuntimeError as exc:
            daemon.stop()
            pytest.skip(f"served daemon could not boot here: {exc}")
        manager, browser = launch_chromium()
        try:
            yield daemon, browser
        finally:
            browser.close()
            manager.stop()
            daemon.stop()


def test_media_studio_is_offered_under_skills_and_opens_the_editor_window(served):
    daemon, browser = served
    page = browser.new_page()
    page.goto(f"{daemon.base_url}/chat", wait_until="networkidle")
    assert page.query_selector("header a[href='/media-editor']") is None, "header link still present"
    page.click("#skillsBtn")
    link = page.wait_for_selector("#pluginsBody a.media-studio-open[href='/media-editor']", timeout=20000)
    assert link.get_attribute("target") == "_blank" and "Media Studio" in page.inner_text("#pluginsBody")
    with page.context.expect_page() as popup:
        link.click()
    editor = popup.value
    editor.wait_for_load_state()
    assert ("/media-editor" in editor.url and "Media Studio" in editor.title()) or "Media Studio" in editor.content()
    editor.close()
    page.close()
