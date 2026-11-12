from __future__ import annotations

import json
from pathlib import Path

from core.i18n.catalog import load_catalog_data, source_catalog_sha256
from core.pet_world_page import render_pet_world_html
from core.web.api import pet_world_api
from tests._pet_world_support import home as home
from tests._toybox_v2_browser import served as served


def test_all_yard_strings_have_local_catalogs_and_first_paint_direction():
    english = load_catalog_data('en')['messages']
    keys = {key for key in english if key.startswith('yard.')}
    assert len(keys) >= 170
    for path in Path('core/i18n/catalogs').glob('*.json'):
        data = load_catalog_data(path.stem)
        assert keys <= data['messages'].keys()
        if path.stem != 'en':
            assert data['source_catalog_sha256'] == source_catalog_sha256()
        page = render_pet_world_html(path.stem, ('light', 100))
        assert 'data-theme="light"' in page
        assert 'dir="rtl"' in page if path.stem in ('ar', 'he') else 'dir="ltr"' in page
        assert data['messages']['yard.title'] in page


def test_operator_guard_all_paths_and_read_never_feeds(home):
    class Runtime:
        runtime_version_stamp = {}
        def status_snapshot(self):
            return {}
    # A real dispatcher RuntimeServices is exercised by the served journey; direct
    # denial happens before any runtime or storage operation here.
    from core.web.api.runtime import RuntimeServices
    runtime = RuntimeServices(display_name='VOOL')
    try:
        for action in ('save', 'race', 'growth', 'egg', 'reset'):
            result = pet_world_api.post('/api/pet-yard', {'action': action}, {}, runtime, '192.0.2.2')
            assert result.status == 403
        result = pet_world_api.post('/api/pet-cosmetics/import', {}, {'Content-Type': 'text/plain'}, runtime, '127.0.0.1')
        assert result.status == 415
        result = pet_world_api.get('/api/pet-yard/lifeform', {}, {}, runtime, '127.0.0.1')
        assert json.loads(result.body)['exists'] is False
        result = pet_world_api.get('/api/pet-cosmetics', {}, {}, runtime, '192.0.2.2')
        assert result.status == 403
    finally:
        runtime.shutdown()


def test_embedded_pixel_font_is_real_offline_and_has_its_licence(served):
    import base64
    from io import BytesIO

    from PIL import ImageFont

    from core.toybox_font import FONT, LICENSE, css
    from core.toybox_type import STACKS

    font = ImageFont.truetype(BytesIO(base64.b64decode(FONT)), 16)
    assert font.getlength('Pet Yard') > 0
    assert 'SIL OPEN FONT LICENSE Version 1.1' in LICENSE
    assert Path('core/pet_world/licenses/silkscreen-OFL.txt').read_text() == LICENSE
    assert STACKS['pixel-display'].startswith('VoolPixel,')
    assert 'data:font/ttf;base64,' in css() and 'https:' not in css()

    from core.toybox_pixel import SKINS
    from tests._toybox_v2_browser import finish_browser
    from tests.served_browser import launch_chromium

    manager, browser = launch_chromium()
    try:
        page = browser.new_page()
        result = page.request.post(served+'/api/toybox/preview', data={'manifest': json.dumps(SKINS[0])})
        assert result.status == 200
        page.set_content(result.json()['preview'])
        assert page.evaluate('async()=> (await document.fonts.load("16px VoolPixel")).length') == 1
    finally:
        finish_browser(manager, browser)
