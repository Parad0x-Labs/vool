from __future__ import annotations

import base64
import json
from io import BytesIO

from PIL import Image

from tests._toybox_v2_browser import finish_browser, network_guard
from tests._toybox_v2_browser import served as served
from tests.served_browser import launch_chromium
from tests.test_toybox_v2_contract import fixture_skin


def test_every_optional_stage_field_has_a_visible_bounded_effect(served):
    manifest = fixture_skin()
    manifest['stage'].update(frame={'set': 'none', 'art': {}}, scene={'id': 'sky', 'density': 'lively'},
                             accents={'selection': 'panel', 'caret': 'accent'})
    manifest['assets'] = []
    for slot, size, color in [('top', (120, 32), 'red'), ('side', (32, 160), 'green'),
                              ('corner', (48, 48), 'blue'), ('empty', (640, 400), 'purple')]:
        output = BytesIO()
        Image.new('RGB', size, color).save(output, 'PNG')
        path = 'assets/' + slot + '.png'
        manifest['assets'].append({'path': path, 'png': base64.b64encode(output.getvalue()).decode()})
        if slot == 'empty':
            manifest['stage']['empty'] = {'art': path}
        else:
            manifest['stage']['frame']['art'][slot] = path
    manager, browser = launch_chromium()
    try:
        page = browser.new_page(viewport={'width': 1280, 'height': 900})
        guard = network_guard(page, served)
        result = page.request.post(served + '/api/toybox/apply', data={
            'manifest': json.dumps(manifest), 'revision': 0, 'confirmed': True})
        assert result.status == 200, result.text()
        page.goto(served + '/chat')
        page.wait_for_function('window.VoolStage && window.VoolCompanion')
        values = page.evaluate('''()=>{
          const style=s=>getComputedStyle(document.querySelector(s));
          const empty=getComputedStyle(document.querySelector('.empty'),'::before');
          return {slots:['#main>header','#sidebar','#main>footer'].map(s=>style(s).borderImageSource),
           scene:style('#main>header').backgroundImage,caret:style('#input').caretColor,
           accent:style('#main').getPropertyValue('--accent').trim(),
           selection:getComputedStyle(document.querySelector('#input'),'::selection').backgroundColor,
           panel:style('#main').getPropertyValue('--panel').trim(),empty:[empty.height,empty.display,empty.backgroundSize]};
        }''')
        assert len(set(values['slots'])) == 3 and all('data:image/png' in s for s in values['slots'])
        assert 'data:image/svg' in values['scene']
        assert values['empty'] == ['190px', 'block', 'contain']
        def rgb(color):
            return page.evaluate("color=>{const n=document.createElement('i');n.style.color=color;document.body.append(n);const c=getComputedStyle(n).color;n.remove();return c}", color)

        assert values['caret'] == rgb(values['accent'])
        assert values['selection'] == rgb(values['panel'])
        # The image is a block before the greeting, rather than a background behind text.
        assert page.locator('.empty').evaluate("el=>getComputedStyle(el,'::before').pointerEvents") == 'none'
        page.emulate_media(forced_colors='active')
        page.wait_for_function("""()=>{const el=document.querySelector('.empty');return el &&
          getComputedStyle(el,'::before').display==='none';}""")
        assert page.locator('#main>header').evaluate('el=>getComputedStyle(el).backgroundImage') == 'none'
        assert page.evaluate("getComputedStyle(document.querySelector('.empty'),'::before').display") == 'none'
        assert not guard()
    finally:
        finish_browser(manager, browser)
