from tests._toybox_v2_browser import finish_browser
from tests._toybox_v2_browser import served as served
from tests.served_browser import launch_chromium


def test_idle_chat_does_not_fetch_or_retain_the_skin_gallery(served):
    manager, browser = launch_chromium()
    try:
        page = browser.new_page()
        reads = []
        page.on('request', lambda r: reads.append(r.url) if r.url == served + '/api/toybox' else None)
        page.goto(served + '/chat')
        page.wait_for_load_state('networkidle')
        assert reads
        before = len(reads)
        page.wait_for_timeout(5500)
        assert len(reads) == before
        page.evaluate("window.dispatchEvent(new Event('focus'))")
        page.wait_for_function("document.documentElement.dataset.skin==='default'")
        page.wait_for_timeout(100)
        assert len(reads) == before + 1
    finally:
        finish_browser(manager, browser)
