from __future__ import annotations

import json

from core.toybox import validate
from core.toybox_fragment import skin_css
from core.toybox_pixel import EXPRESSIONS, MASCOTS, SKINS, artwork, mascot_pixels
from tests._toybox_v2_browser import finish_browser
from tests._toybox_v2_browser import served as served
from tests.served_browser import launch_chromium
from tests.test_toybox_flagships_a_browser import run_matrix


def test_pixel_palette_art_and_expressions_stay_original_and_bounded():
    skin = validate(json.dumps(SKINS[0]))[0]
    assert len(skin_css(skin).encode()) <= 60_000
    for p in skin['palettes'].values():
        drawing = artwork(skin['id'], p)
        assert set(drawing) == {'header', 'empty', 'perch'}
        assert sum(len(s.encode()) for s in drawing.values()) < 160_000
        assert all('crispEdges' in s and not any(k in s for k in ('<script', 'href=', '<text', '<image')) for s in drawing.values())
    for kind in MASCOTS:
        frames = [mascot_pixels(kind, e) for e in EXPRESSIONS]
        assert all(len(f) == 48 and all(len(r) == 48 for r in f) for f in frames)
        assert len({json.dumps(f) for f in frames}) == len(EXPRESSIONS)


def test_pixel_skin_real_pages(served):
    run_matrix(served, SKINS[0], shutdown=finish_browser)


def test_pixel_faces_follow_verified_status_and_stop_drawing_when_motion_is_off(served):
    manager, browser = launch_chromium()
    try:
        page = browser.new_page()
        result = page.request.post(served+'/api/toybox/apply', data={
            'manifest': json.dumps(SKINS[0]), 'revision': 0, 'confirmed': True})
        assert result.status == 200
        page.goto(served+'/chat')
        page.wait_for_function('window.VoolStage && window.VoolCompanion && document.getElementById("tb-effect")')
        page.bring_to_front()
        page.evaluate("""()=>{window.effectDraws=0;const original=CanvasRenderingContext2D.prototype.clearRect;
         CanvasRenderingContext2D.prototype.clearRect=function(...args){if(this.canvas.id==='tb-effect')window.effectDraws++;return original.apply(this,args);};
         VoolCompanion.consume('face',{type:'task.started'});VoolStage.mirror('face',true);}""")
        page.wait_for_function('window.effectDraws>=2')
        active = page.locator('#tb-effect').evaluate('el=>el.toDataURL()')
        page.evaluate("VoolCompanion.consume('face',{type:'permission.required'});VoolStage.mirror('face',false)")
        assert page.locator('html').get_attribute('data-vool-motion') == 'off'
        count = page.evaluate('window.effectDraws')
        page.wait_for_timeout(350)
        assert page.evaluate('window.effectDraws') == count
        page.evaluate("VoolCompanion.consume('plain',{type:'task.started'});VoolCompanion.consume('plain',{type:'task.completed'});VoolStage.mirror('plain',false)")
        assert page.locator('html').get_attribute('data-vool-activity') == 'unknown'
        page.evaluate("VoolCompanion.consume('passed',{type:'task.started'});VoolCompanion.consume('passed',{type:'verification.completed',review_state:'passed'});VoolCompanion.consume('passed',{type:'task.completed'});VoolStage.mirror('passed',false)")
        assert page.locator('html').get_attribute('data-vool-activity') == 'success'
        assert page.locator('#tb-effect').evaluate('el=>el.toDataURL()') != active
        page.emulate_media(reduced_motion='reduce')
        page.wait_for_timeout(50)
        count = page.evaluate('window.effectDraws')
        page.wait_for_timeout(350)
        assert page.evaluate('window.effectDraws') == count
    finally:
        finish_browser(manager, browser)
