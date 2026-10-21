from __future__ import annotations

import pytest

from tests import _reader_served_rig as rig
from tests import served_browser


@pytest.fixture(scope='module')
def surface(tmp_path_factory):
    with rig.CapturingProvider(default='Ready.') as provider:
        daemon = rig.ServedDaemon(tmp_path_factory.mktemp('palette-ready') / 'home', provider=provider)
        try:
            daemon.start(timeout=120)
            manager, browser = served_browser.launch_chromium()
            try:
                yield browser, daemon.base_url
            finally:
                browser.close()
                manager.stop()
        finally:
            daemon.stop()


@pytest.mark.parametrize('bridge_at_boot', [False, True])
def test_windows_desktop_hint_follows_bridge_readiness(surface, bridge_at_boot):
    browser, origin = surface
    page = browser.new_page(viewport={'width': 1280, 'height': 900})
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.route('**/*', lambda route: route.continue_() if route.request.url.startswith(origin) else route.abort())
    page.add_init_script("Object.defineProperty(navigator, 'platform', {get: () => 'Win32'});"
                         "Object.defineProperty(navigator, 'userAgentData', {get: () => undefined});")
    if bridge_at_boot:
        page.add_init_script('window.pywebview = {api: {}};')
    try:
        page.goto(origin + '/chat', wait_until='domcontentloaded')
        page.wait_for_selector('#vpHint')
        hint = page.locator('#vpHint')
        if not bridge_at_boot:
            assert 'Ctrl+N' not in hint.inner_text()
            page.evaluate("window.dispatchEvent(new Event('pywebviewready'))")
            assert 'Ctrl+N' not in hint.inner_text(), 'an event without a bridge is still a browser tab'
            page.evaluate('window.pywebview = {api: {}};')
            assert 'Ctrl+N' not in hint.inner_text(), 'the desktop readiness event owns the update'
        page.evaluate("window.dispatchEvent(new Event('pywebviewready'))")
        page.wait_for_function("document.querySelector('#vpHint').textContent.includes('Ctrl+N')")
        page.evaluate("window.dispatchEvent(new Event('pywebviewready'))")
        assert page.locator('#vpHint').count() == 1
        assert hint.inner_text().count('Ctrl+N') == 1
        assert 'Ctrl+K' in hint.inner_text() and 'Ctrl+F' in hint.inner_text()
        assert errors == []
    finally:
        page.close()
