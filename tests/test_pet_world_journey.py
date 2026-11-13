from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path

from core.pet_world.model import starter
from tests._toybox_v2_browser import finish_browser, network_guard, shoot
from tests._toybox_v2_browser import served as served
from tests.served_browser import launch_chromium
from tests.test_pet_cosmetics import pack


def chromium_override():
    """The same explicit-executable seam as tests.served_browser, for this test's own launch flags."""
    executable = os.environ.get('VOOL_TEST_CHROMIUM_EXECUTABLE', '').strip()
    return {'executable_path': executable} if executable else {}


def wait(page, expression):
    deadline = time.monotonic()+15
    while time.monotonic() < deadline:
        if page.evaluate("() => Boolean("+expression+")"):
            return
        page.wait_for_timeout(100)
    raise AssertionError("Timed out: "+expression)


def test_real_yard_build_play_save_restart_and_imports(served):
    manager, browser = launch_chromium()
    try:
        page = browser.new_page(viewport={'width': 1400, 'height': 1100})
        errors = []
        page.on('pageerror', lambda e: errors.append(str(e)))
        outside = network_guard(page, served)
        page.goto(served+'/pet-yard')
        wait(page, 'window.VoolYardPage?.controller && window.VoolYardPage?.renderer')
        assert page.evaluate('VoolYardPage.yard.pets.length') == 4
        page.wait_for_timeout(350)
        assert not page.evaluate('VoolYardPage.diagnostics.dirty')
        assert page.locator('#yardNotice').inner_text() != 'unavailable'
        page.locator('#yardCanvas').focus()
        before = page.evaluate('VoolYardPage.controller.serialize()')
        page.keyboard.press('ArrowRight')
        page.wait_for_timeout(900)
        after = page.evaluate('VoolYardPage.controller.serialize()')
        assert after[0]['x'] == before[0]['x']+1
        assert page.evaluate('VoolYardPage.diagnostics.dirty')
        page.locator('[data-mode="build"]').click()
        page.locator('[data-material="clay"]').click()
        point = page.evaluate('VoolYardPage.renderer.project(8,10)')
        canvas = page.locator('#yardCanvas').bounding_box()
        intrinsic = page.locator('#yardCanvas').evaluate('c=>({w:c.width,h:c.height})')
        page.mouse.click(canvas['x']+point['x']*canvas['width']/intrinsic['w'], canvas['y']+point['y']*canvas['height']/intrinsic['h'])
        wait(page, 'VoolYardPage.yard.columns[248].at(-1)[0]==="clay"')
        assert page.evaluate('VoolYardPage.yard.columns[248].at(-1)[1]') == 1
        page.locator('#yardUndo').click()
        assert page.evaluate('VoolYardPage.yard.columns[248].at(-1)[0]') == 'grass'
        page.locator('#yardRedo').click()
        page.locator('#yardSave').click()
        wait(page, 'VoolYardPage.yard.revision===1')
        page.wait_for_timeout(350)
        assert not page.evaluate('VoolYardPage.diagnostics.dirty')
        saved = page.request.get(served+'/api/pet-yard').json()
        assert saved['yard']['columns'][248][-1] == ['clay', 1]
        served.restart()
        page.reload()
        wait(page, 'VoolYardPage.yard?.revision===1')
        assert page.evaluate('VoolYardPage.yard.columns[248].at(-1)[0]') == 'clay'
        page.locator('#yardReduced').check()
        page.locator('#yardRace').click()
        wait(page, 'document.getElementById("yardResult").textContent.includes("1.")')
        page.locator('#yardDig').click()
        assert page.evaluate('VoolYardPage.controller.game') == ''
        assert page.locator('[data-mode="decorate"]').get_attribute('aria-pressed') == 'true'
        # Real file chooser, validation preview, import and durable assignment.
        details = page.locator('#pet-cosmetics details').filter(has=page.locator('input[accept*=".voolpet"]'))
        details.locator('summary').click()
        details.locator('input[type=file]').set_input_files({'name': 'cloud.voolpet', 'mimeType': 'application/json', 'buffer': json.dumps(pack()).encode()})
        button = details.locator('button')
        from playwright.sync_api import expect
        expect(button).to_be_enabled()
        button.click()
        wait(page, 'VoolPetCosmetics.library.pets.length===1')
        lib = page.request.get(served+'/api/pet-cosmetics').json()
        assert lib['pets'][0]['name'] == '雲猫'
        assert not lib['config']['sound']
        assert not errors
        assert not outside()
        shoot(page, 'yard-dark')
        # Quiet reduced motion: no animation timer.
        assert page.evaluate('VoolYardPage.diagnostics.timer') is False
    finally:
        finish_browser(manager, browser)


def test_failure_paths_conflicts_readonly_and_offline_page(served):
    manager, browser = launch_chromium()
    try:
        page = browser.new_page()
        page.goto(served+'/pet-yard')
        wait(page, 'window.VoolYardPage?.controller')
        yard = starter()
        yard['columns'][0] = [['bad-block', 1]]
        r = page.request.post(served+'/api/pet-yard', data={'action': 'save', 'document': json.dumps(yard)})
        assert r.status == 400 and r.json()['error'] == 'block'
        r = page.request.post(served+'/api/pet-yard', data={'action': 'save', 'document': ' '*300000})
        assert r.status == 413
        r = page.request.post(served+'/api/pet-yard', headers={'Origin': 'https://foreign.invalid'}, data={'action': 'growth'})
        assert r.status == 403
        assert page.request.post(served+'/api/pet-yard', data={'action': 'save', 'document': json.dumps(starter())}).status == 200
        assert page.request.post(served+'/api/pet-yard', data={'action': 'save', 'document': json.dumps(starter())}).status == 409
        page.reload()
        wait(page, 'window.VoolYardPage?.controller')
        page.locator('#yardReset').click()
        assert page.locator('#yardConfirm').is_visible()
        page.locator('#yardResetYes').click()
        wait(page, 'VoolYardPage.yard.revision===2')
        page.context.set_offline(True)
        page.locator('#yardSave').click()
        wait(page, '!document.getElementById("yardSave").disabled')
        assert page.evaluate('VoolYardPage.yard.revision') == 2
        page.context.set_offline(False)
        # A corrupt stored copy is preserved until the explicit reset door.
        from storage.db import get_connection
        with get_connection() as conn:
            conn.execute("UPDATE pet_yard_v1 SET document='broken'")
            conn.commit()
        page.reload()
        wait(page, 'VoolYardPage.controller && document.getElementById("yardSave").disabled')
        assert page.locator('#yardNotice').get_attribute('role') == 'alert'
        with get_connection() as conn:
            assert conn.execute('SELECT document FROM pet_yard_v1').fetchone()[0] == 'broken'
    finally:
        finish_browser(manager, browser)


def test_renderer_picking_path_bounds_visibility_and_reference_budget(served):
    from playwright.sync_api import sync_playwright

    manager = sync_playwright().start()
    browser = manager.chromium.launch(**chromium_override(), headless=True,
                                      args=['--disable-gpu', '--disable-accelerated-2d-canvas'])
    try:
        context = browser.new_context(viewport={'width': 1400, 'height': 1000})
        page = context.new_page()
        cdp = page.context.new_cdp_session(page)
        cdp.send('Emulation.setCPUThrottlingRate', {'rate': 4})
        from tests.test_pet_world_lifeform_bridge import _seed
        _seed()
        five = starter()
        five['pets'].append({'id': 'lifeform', 'x': 10, 'y': 10})
        assert page.request.post(served+'/api/pet-yard', data={'action': 'save', 'document': json.dumps(five)}).status == 200
        page.goto(served+'/pet-yard')
        wait(page, 'window.VoolYardPage?.renderer && VoolYardPage.diagnostics.firstPaint!==null')
        assert page.evaluate('VoolPetCosmetics.ready()===VoolPetCosmetics.ready()')
        data = page.evaluate('''()=>{const r=VoolYardPage.renderer,c=VoolYardPage.controller;
        const picked=[];for(const [x,y] of [[2,2],[8,10],[22,22],[5,19]]){const p=r.project(x,y);picked.push(r.pick(p.x,p.y));}
        const full=[],frames=[];for(let i=0;i<8;i++){const changed=structuredClone(r.yard);changed.columns[100]=[[i%2?'stone':'grass',1]];r.setYard(changed);const moving=c.actors.map((p,j)=>({...p,x:p.x+i*.03}));r.setPets(moving);r.frame(performance.now());full.push(r.metrics.full+r.metrics.frame);for(const p of moving)p.x+=.1;r.frame(performance.now()+125);frames.push(r.metrics.frame);}
        return {picked,full,frames,bytes:r.metrics.buffer_bytes,first:VoolYardPage.diagnostics.firstPaint,background_paint:performance.getEntriesByType('paint')[0]?.startTime,navigation:performance.getEntriesByType('navigation')[0].toJSON(),
        water:c.path({x:9,y:11},{x:0,y:0}),blocked:c.path({x:9,y:11},{x:13,y:13})};}''')
        folder = Path(tempfile.gettempdir())/'logs'
        folder.mkdir(parents=True, exist_ok=True)
        (folder/'yard-render-budget.json').write_text(json.dumps(data, indent=2))
        assert data['picked'] == [{'x': 2, 'y': 2}, {'x': 8, 'y': 10}, {'x': 22, 'y': 22}, {'x': 5, 'y': 19}]
        assert data['water'] is None and data['blocked'] is None
        assert data['first'] < 500
        assert data['background_paint'] < 500
        assert max(data['full']) < 12
        assert max(data['frames'][1:]) < 3
        assert data['bytes'] < 80*1024*1024
        cdp.send('Performance.enable')
        before = {x['name']: x['value'] for x in cdp.send('Performance.getMetrics')['metrics']}
        page.wait_for_timeout(10000)
        after = {x['name']: x['value'] for x in cdp.send('Performance.getMetrics')['metrics']}
        backend = browser.new_browser_cdp_session().send('SystemInfo.getInfo')['gpu']['featureStatus']
        data['canvas_backend'] = backend.get('2d_canvas', 'unknown')
        assert 'software' in data['canvas_backend']
        data['idle_cpu_seconds_10s'] = after['TaskDuration']-before['TaskDuration']
        data['heap_and_buffers'] = after['JSHeapUsedSize']+data['bytes']
        (folder/'yard-render-budget.json').write_text(json.dumps(data, indent=2))
        assert data['idle_cpu_seconds_10s'] < .5
        assert data['heap_and_buffers'] < 80*1024*1024
        # Disable the automation driver's focus emulation, then switch a real tab.
        cdp.send('Emulation.setFocusEmulationEnabled', {'enabled': False})
        hidden = context.new_page()
        hidden.goto('about:blank')
        hidden.bring_to_front()
        wait(page, 'document.hidden || !document.hasFocus()')
        count = page.evaluate('VoolYardPage.renderer.metrics.frames')
        page.wait_for_timeout(750)
        assert not page.evaluate('VoolYardPage.diagnostics.timer')
        assert page.evaluate('VoolYardPage.renderer.metrics.frames') == count
        hidden.close()
        cdp.send('Emulation.setFocusEmulationEnabled', {'enabled': True})
        page.bring_to_front()
        page.emulate_media(reduced_motion='reduce')
        page.wait_for_timeout(300)
        assert not page.evaluate('VoolYardPage.diagnostics.timer')
        # Unknown art is visible as a neutral block and does not crash.
        assert page.evaluate('''()=>{const r=VoolYardPage.renderer,y=structuredClone(r.yard);y.columns[25]=[['unknown',1]];r.setYard(y);r.frame(0);return r.pick(r.project(1,1).x,r.project(1,1).y).x;}''') == 1
    finally:
        finish_browser(manager, browser)


def test_small_edits_repaint_the_same_pixels_as_a_full_build(served):
    """A few changed columns repaint only their own boxes; the picture must match a fresh build."""
    manager, browser = launch_chromium()
    try:
        page = browser.new_page(viewport={'width': 1400, 'height': 1000})
        page.goto(served+'/pet-yard')
        wait(page, 'window.VoolYardPage?.renderer && VoolYardPage.diagnostics.firstPaint!==null')
        result = page.evaluate('''()=>{const r=VoolYardPage.renderer;r.setPets([]);r.setGhost(null);r.setToy(null);r.frame(performance.now());
         const edits=[[100,[['stone',3]]],[101,[['grass',1],['brick',4]]],[300,[['water',1]]],[100,[['sand',1]]],[575,[['glass',2],['moss',1]]],[301,[]],[24,[['clay',8]]]];
         for(const [i,col] of edits){const y=structuredClone(r.yard);y.columns[i]=col;r.setYard(y);r.frame(performance.now());}
         const partial=r.metrics.partial;const a=r.canvas.getContext('2d').getImageData(0,0,r.canvas.width,r.canvas.height).data;
         const fresh=document.createElement('canvas');fresh.width=r.canvas.width;fresh.height=r.canvas.height;
         const s=window.VoolYard.createRenderer(fresh,{yard:structuredClone(r.yard),catalog:r.catalog});s.setCamera(r.camera.zoom,r.camera.x,r.camera.y);s.setPets([]);s.frame(performance.now());
         const b=fresh.getContext('2d').getImageData(0,0,fresh.width,fresh.height).data;let diff=0,worst=0,ground=0;for(let i=0;i<a.length;i+=4){const d=Math.max(Math.abs(a[i]-b[i]),Math.abs(a[i+1]-b[i+1]),Math.abs(a[i+2]-b[i+2]));if(d){diff++;worst=Math.max(worst,d);}if(a[i]!==0x25||a[i+1]!==0x37||a[i+2]!==0x46)ground++;}s.destroy();return {partial,diff,worst,ground,pixels:a.length/4};}''')
        assert result['partial'] is True, result
        # Same picture. A decoration edge cut by a repaint box can rasterize a shade differently,
        # so a few faint edge pixels may differ; a missing or misplaced block would not be faint.
        assert result['diff'] <= 32 and result['worst'] <= 12, result
        assert result['ground'] > result['pixels'] // 10, result  # the yard is drawn, not just the backdrop
    finally:
        browser.close()
        manager.stop()


def test_toybox_custom_pet_sound_assignments_actual_reducer_and_entry_points(served):
    manager, browser = launch_chromium()
    try:
        page = browser.new_page(viewport={'width': 1280, 'height': 900})
        outside = network_guard(page, served)
        response = page.request.post(served+'/api/pet-cosmetics/import', data={'revision': 0, 'document': json.dumps(pack())})
        identifier = response.json()['pets'][0]['id']
        page.goto(served+'/settings#toybox')
        wait(page, 'window.VoolToybox && document.querySelector("#pet-cosmetics select")')
        panel = page.locator('#pet-cosmetics')
        panel.locator(':scope > label select').nth(0).select_option(identifier)
        panel.locator(':scope > label input[type=checkbox]').check()
        panel.locator(':scope > label select').nth(1).select_option('deep')
        panel.locator(':scope > label select').nth(2).select_option('ja')
        panel.locator(':scope > button').first.click()
        wait(page, 'VoolPetCosmetics.library.config.sound && VoolPetCosmetics.library.config.selected')
        assign = panel.locator('details').first
        assign.locator('summary').click()
        assign.locator('select').nth(0).select_option(identifier)
        assign.locator('select').nth(1).select_option('success')
        assign.locator('select').nth(2).select_option('success')
        assign.locator('select').nth(3).select_option('builtin:deep-ja-happy')
        assign.locator('button').last.click()
        wait(page, 'VoolPetCosmetics.library.config.bindings[VoolPetCosmetics.library.config.selected]?.success?.sound === "builtin:deep-ja-happy"')
        # Real user audio is decoded and converted in the served settings page.
        wav_input = panel.locator('input[accept^="audio/"]')
        from tests.test_pet_cosmetics import sound
        wav_input.set_input_files({'name': 'my-reaction.wav', 'mimeType': 'audio/wav', 'buffer': sound()})
        wait(page, 'VoolPetCosmetics.library.sounds.length===1')
        lib = page.request.get(served+'/api/pet-cosmetics').json()
        assert lib['config']['selected'] == identifier
        assert lib['config']['bindings'][identifier]['success']['sound'] == 'builtin:deep-ja-happy'
        assert lib['sounds'][0]['seconds'] == 1
        page.goto(served+'/chat')
        wait(page, 'window.VoolStage && window.VoolCompanion && window.VoolPetCosmetics.library.config.selected')
        # Consume the existing typed reducer. Completion without verification stays unknown.
        page.locator('#input').click()
        wait(page, 'VoolPetCosmetics.diagnostics.context==="running"')
        page.evaluate("VoolCompanion.consume('voice',{type:'task.started'});VoolStage.mirror('voice',true)")
        page.evaluate("VoolCompanion.consume('voice',{type:'task.completed'});VoolStage.mirror('voice',false)")
        assert page.evaluate('VoolPetCosmetics.currentState') == 'unknown'
        page.evaluate("VoolCompanion.consume('pass-voice',{type:'task.started'});VoolStage.mirror('pass-voice',true)")
        page.evaluate("VoolCompanion.consume('pass-voice',{type:'verification.completed',review_state:'passed'});VoolCompanion.consume('pass-voice',{type:'task.completed'});VoolStage.mirror('pass-voice',false)")
        assert page.evaluate('VoolPetCosmetics.currentState') == 'success'
        wait(page, 'VoolPetCosmetics.diagnostics.plays===1')
        page.evaluate("VoolStage.mirror('pass-voice',false)")
        assert page.evaluate('VoolPetCosmetics.diagnostics.plays') == 1
        assert page.evaluate('Boolean(VoolCompanionWorld.draw.__custom)')
        assert page.locator('#petYardLink').get_attribute('href') == '/pet-yard'
        assert not outside()
    finally:
        finish_browser(manager, browser)


def test_yard_keyboard_build_fetch_lifeform_and_light_narrow(served):
    manager, browser = launch_chromium()
    try:
        page = browser.new_page(viewport={'width': 1280, 'height': 1000})
        page.context.add_cookies([{'name': 'vool_appearance', 'value': 'light.100', 'url': served}])
        page.goto(served+'/pet-yard?ui_locale=ja')
        wait(page, 'VoolYardPage.controller')
        assert page.locator('html').get_attribute('data-theme') == 'light'
        assert page.locator('html').get_attribute('lang') == 'ja'
        page.locator('[data-mode="build"]').click()
        page.locator('#yardCanvas').focus()
        page.keyboard.press('ArrowLeft')
        page.keyboard.press('Enter')
        wait(page, 'VoolYardPage.yard.columns[299].length===1 && VoolYardPage.yard.columns[299][0][1]===2')
        page.evaluate("VoolYardPage.controller.onClick('dusk-lynx')")
        page.locator('#yardReduced').check()
        assert page.evaluate('VoolYardPage.controller.selected') == 'dusk-lynx'
        # Actual fetch path out and back, including the reduced-motion path.
        result = page.evaluate('''()=>{const c=VoolYardPage.controller,p=c.serialize().find(p=>p.id===c.selected);const ok=c.fetch({x:p.x+1,y:p.y});c.tick(performance.now());return {ok,game:c.game,position:c.serialize().find(p=>p.id===c.selected),before:p};}''')
        assert result['ok'] and not result['game']
        assert result['position'] == result['before']
        from tests.test_pet_world_lifeform_bridge import _fact
        for i in range(5):
            _fact(i)
        egg = page.request.post(served+'/api/pet-yard', data={'action': 'egg'})
        assert egg.status == 200 and egg.json()['lifeform']['verified']
        grown = page.request.post(served+'/api/pet-yard', data={'action': 'growth'}).json()
        assert grown['verified'] and grown['spec']['stage'] == 'seed'
        page.on('dialog', lambda dialog: dialog.accept())
        page.evaluate('VoolYardPage.load()')
        wait(page, 'document.querySelector("#yardRoster input[aria-label=生命体]")')
        page.locator('#yardRoster input[aria-label="生命体"]').check()
        assert page.evaluate('VoolYardPage.yard.pets.some(p=>p.id==="lifeform")')
        page.evaluate('scrollTo(0,0)')
        shoot(page, 'yard-light-ja')
        page.set_viewport_size({'width': 390, 'height': 850})
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        page.evaluate('scrollTo(0,0)')
        shoot(page, 'yard-narrow-ja')
    finally:
        finish_browser(manager, browser)


def test_missing_display_reports_recovery_without_crash(served):
    manager, browser = launch_chromium()
    try:
        page = browser.new_page()
        errors = []
        page.on('pageerror', lambda e: errors.append(str(e)))
        page.add_init_script("const get=HTMLCanvasElement.prototype.getContext;HTMLCanvasElement.prototype.getContext=function(...a){return this.id==='yardCanvas'?null:get.apply(this,a);};")
        page.goto(served+'/pet-yard')
        wait(page, 'document.getElementById("yardNotice").textContent.includes("newer display")')
        assert page.locator('#yardSave').is_disabled()
        assert page.locator('#yardReload').is_enabled()
        assert not errors
    finally:
        finish_browser(manager, browser)


def test_yard_scene_follows_live_runtime_metadata_and_fetches_a_visible_toy(served):
    from core import live_turns
    from storage.db import get_connection

    live_turns.register_turn('yard-scene', 'yard-turn')
    live_turns.note_checkpoint('yard-scene', 'yard-turn', 'yard-checkpoint')
    with get_connection() as conn:
        conn.execute("INSERT INTO runtime_sessions(session_id,started_at,updated_at,status,last_checkpoint_id,request_preview,last_message) VALUES ('yard-scene','2026-01-01','2026-01-01','running','yard-checkpoint','PRIVATE-YARD-MARKER','PRIVATE-YARD-MARKER')")
        conn.commit()
    manager, browser = launch_chromium()
    try:
        page = browser.new_page()
        page.goto(served+'/pet-yard')
        wait(page, 'VoolYardPage.controller?.actors[0].pose==="work" && Math.round(VoolYardPage.controller.actors[0].x)===13 && Math.round(VoolYardPage.controller.actors[0].y)===14')
        actor = page.evaluate('VoolYardPage.controller.serialize()[0]')
        assert actor['x'] == 13 and actor['y'] == 14
        payload = page.request.get(served+'/api/pet-yard')
        assert payload.json()['companion'] == 'tool' and 'PRIVATE-YARD-MARKER' not in payload.text()
        with get_connection() as conn:
            conn.execute("UPDATE runtime_sessions SET status='pending_approval'")
            conn.commit()
        wait(page, 'VoolYardPage.controller.actors[0].pose==="wave"')
        live_turns.unregister_turn('yard-scene', 'yard-turn')
        with get_connection() as conn:
            conn.execute("UPDATE runtime_sessions SET status='running'")
            conn.commit()
        wait(page, 'VoolYardPage.controller.actors[0].pose==="unknown"')
        with get_connection() as conn:
            conn.execute("UPDATE runtime_sessions SET status='completed'")
            conn.commit()
        wait(page, 'VoolYardPage.controller.actors[0].pose==="idle"')
        result = page.evaluate('''()=>{const c=VoolYardPage.controller,p=c.serialize()[0];return c.fetch({x:p.x+1,y:p.y});}''')
        assert result and page.evaluate('Boolean(VoolYardPage.renderer.toy)')
        wait(page, 'VoolYardPage.controller.game===""')
        assert page.evaluate('VoolYardPage.renderer.toy') is None
        assert not page.evaluate('VoolYardPage.controller.actors[0].carry')
    finally:
        live_turns.unregister_turn('yard-scene', 'yard-turn')
        finish_browser(manager, browser)


def test_long_audio_is_rejected_before_sample_decode_and_short_clips_import(served):
    import wave
    from io import BytesIO

    from tests.test_pet_cosmetics import sound

    manager, browser = launch_chromium()
    try:
        page = browser.new_page()
        page.goto(served+'/settings#toybox')
        wait(page, 'document.querySelector("#pet-cosmetics input[accept^=audio]")')
        page.evaluate('''()=>{window.sampleDecodes=0;const old=AudioContext.prototype.decodeAudioData;AudioContext.prototype.decodeAudioData=function(...a){window.sampleDecodes++;return old.apply(this,a);};}''')
        raw = sound()
        with wave.open(BytesIO(raw), 'rb') as source:
            frames = source.readframes(source.getnframes())
        out = BytesIO()
        with wave.open(out, 'wb') as target:
            target.setnchannels(1)
            target.setsampwidth(2)
            target.setframerate(24000)
            target.writeframes(frames*10)
        field = page.locator('#pet-cosmetics input[accept^="audio/"]')
        field.set_input_files({'name': 'long.wav', 'mimeType': 'audio/wav', 'buffer': out.getvalue()})
        wait(page, 'document.querySelector(".pc-notice").textContent.includes("not supported")')
        assert page.evaluate('window.sampleDecodes') == 0
        assert page.request.get(served+'/api/pet-cosmetics').json()['sounds'] == []
        field.set_input_files({'name': 'short.wav', 'mimeType': 'audio/wav', 'buffer': raw})
        wait(page, 'VoolPetCosmetics.library.sounds.length===1')
        assert page.evaluate('window.sampleDecodes') == 1
        import base64

        from core.pet_world.audio_data import CLIPS
        mp3 = base64.b64decode(CLIPS['deep-ja-happy'])
        # The shipped clip exercises an actual MPEG import, not a renamed WAV.
        field = page.locator('#pet-cosmetics input[accept^="audio/"]')
        field.set_input_files({'name': 'short.mp3', 'mimeType': 'audio/mpeg', 'buffer': mp3})
        wait(page, 'VoolPetCosmetics.library.sounds.length===2')
        assert page.evaluate('window.sampleDecodes') == 2
    finally:
        finish_browser(manager, browser)


def test_yard_skin_chrome_pet_click_and_brief_follow(served):
    from core.toybox_registry_v2 import manifests
    from tests._toybox_v2_browser import contrast_report

    manager, browser = launch_chromium()
    try:
        page = browser.new_page(viewport={'width': 1280, 'height': 1000})
        revision = 0
        for manifest, _, _ in manifests():
            result = page.request.post(served+'/api/toybox/apply', data={'manifest': json.dumps(manifest), 'revision': revision, 'confirmed': True})
            assert result.status == 200
            revision = result.json()['revision']
            for theme in ('dark', 'light'):
                page.context.add_cookies([{'name': 'vool_appearance', 'value': theme+'.100', 'url': served}])
                page.goto(served+'/pet-yard')
                wait(page, 'window.VoolYardPage?.renderer')
                expected = manifest['palettes'][theme]['accent']
                assert page.locator('#main').evaluate('e=>getComputedStyle(e).getPropertyValue("--accent").trim()') == expected
                assert page.locator('#main > header').evaluate('e=>getComputedStyle(e).backgroundImage') != 'none'
                assert not contrast_report(page)
                if manifest['id'] == 'dawn-circuit':
                    assert page.evaluate('async()=> (await document.fonts.load("16px VoolPixel")).length') == 1
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        page.locator('#yardCanvas').scroll_into_view_if_needed()
        actor = page.evaluate('VoolYardPage.controller.serialize()[0]')
        point = page.evaluate('p=>VoolYardPage.renderer.project(p.x,p.y)', actor)
        box = page.locator('#yardCanvas').bounding_box()
        page.mouse.click(box['x']+point['x'], box['y']+point['y']-55)
        assert page.evaluate('VoolYardPage.controller.actors[0].pose') == 'love'
        page.wait_for_timeout(1700)
        result = page.evaluate('''()=>{const c=VoolYardPage.controller,p=c.serialize()[0];return c.follow({x:p.x+2,y:p.y});}''')
        assert result
        wait(page, 'VoolYardPage.controller.actors[0].path.length===0 && !VoolYardPage.controller.actors[0].route')
        assert page.evaluate('VoolYardPage.controller.serialize()[0].x') == actor['x']+2
        page.evaluate('scrollTo(0,0)')
        shoot(page, 'yard-dawn-light-final')
    finally:
        finish_browser(manager, browser)


def test_custom_pet_refreshes_in_reduced_motion_and_keeps_native_previews(served):
    manager, browser = launch_chromium()
    try:
        page = browser.new_page(reduced_motion='reduce')
        result = page.request.post(served+'/api/pet-cosmetics/import', data={'revision': 0, 'document': json.dumps(pack())}).json()
        config = result['config']
        config['selected'] = result['pets'][0]['id']
        assert page.request.post(served+'/api/pet-cosmetics/configure', data={'revision': 1, 'config': config}).status == 200
        page.goto(served+'/chat')
        wait(page, 'window.VoolPetCosmetics?.activePet.startsWith("custom-")')
        wait(page, 'document.querySelector("#companionLayer > .vool-ninja > canvas")?.getContext("2d").getImageData(24,24,1,1).data[0]===118')
        before = page.locator('#companionLayer > .vool-ninja > canvas').evaluate('c=>c.toDataURL()')
        # Existing preview canvases sit inside the same layer, but keep their own roster art.
        different = page.evaluate('''()=>{const c=document.createElement('canvas');c.width=c.height=48;const box=document.createElement('div');box.append(c);document.getElementById('companionLayer').append(box);VoolCompanionWorld.draw(c.getContext('2d'),'idle',0,{character:'beetle'});const result=c.toDataURL();box.remove();return result;}''')
        assert different != before
        page.evaluate("VoolCompanion.consume('crowd',{type:'task.started'});VoolStage.mirror('crowd',true)")
        assert page.evaluate('''()=>{const g=document.querySelector('#companionLayer > .vool-ninja > canvas').getContext('2d');return VoolCompanionWorld.scene(g,'pair',0,{character:'beetle'});}''')
        # Pair scene still uses the imported art and marks two actual scene participants.
        assert page.locator('#companionLayer > .vool-ninja > canvas').evaluate('c=>c.getContext("2d").getImageData(24,24,1,1).data[0]') == 118
        cfg = page.request.get(served+'/api/pet-cosmetics').json()
        cfg['config']['selected'] = 'dusk-lynx'
        assert page.request.post(served+'/api/pet-cosmetics/configure', data={'revision': cfg['revision'], 'config': cfg['config']}).status == 200
        page.evaluate('VoolPetCosmetics.refresh()')
        assert page.evaluate('VoolPetCosmetics.activePet') == 'dusk-lynx'
        wait(page, "(()=>{const c=document.createElement('canvas');c.width=c.height=48;if(!VoolPetCosmetics.draw(c.getContext('2d'),'dusk-lynx',VoolPetCosmetics.renderedState,0))return false;const expected=c.getContext('2d').getImageData(0,0,48,42).data,actual=document.querySelector('#companionLayer > .vool-ninja > canvas').getContext('2d').getImageData(0,0,48,42).data;return actual.every((v,i)=>v===expected[i]);})()")
    finally:
        finish_browser(manager, browser)


def test_empty_roster_and_motion_changes_preserve_manual_position(served):
    manager, browser = launch_chromium()
    try:
        page = browser.new_page(reduced_motion='reduce')
        yard = starter()
        yard['pets'] = []
        response = page.request.post(served+'/api/pet-yard', data={'action': 'save', 'document': json.dumps(yard)})
        assert response.status == 200
        errors = []
        page.on('pageerror', lambda e: errors.append(str(e)))
        page.goto(served+'/pet-yard')
        wait(page, 'window.VoolYardPage?.controller')
        assert page.evaluate('VoolYardPage.controller.serialize()') == []
        for id_ in ['yardFetch', 'yardRace', 'yardDig', 'yardSit']:
            assert page.locator('#'+id_).is_disabled()
        assert page.locator('#yardEmotes button:disabled').count() == 4
        assert not page.evaluate('VoolYardPage.diagnostics.dirty')
        assert page.locator('#yardNotice').inner_text() == 'Up to four pets, plus your lifeform.'
        page.locator('#yardRoster input[aria-label="Spark"]').check()
        before = page.evaluate('VoolYardPage.controller.serialize()[0]')
        assert page.evaluate('VoolYardPage.controller.moveBy(1,0)')
        moved = page.evaluate('VoolYardPage.controller.serialize()[0]')
        assert moved['x'] == before['x']+1 and moved['y'] == before['y']
        page.locator('#yardReduced').check()
        assert page.evaluate('VoolYardPage.controller.serialize()[0]') == moved
        page.emulate_media(reduced_motion='no-preference')
        assert page.evaluate('VoolYardPage.controller.serialize()[0]') == moved
        page.locator('#yardName').fill('Cloud Garden')
        page.emulate_media(reduced_motion='reduce')
        page.wait_for_timeout(100)
        assert page.locator('#yardName').input_value() == 'Cloud Garden'
        page.locator('#yardName').press('Tab')
        assert page.evaluate('VoolYardPage.controller.serialize()[0]') == moved
        assert page.evaluate('VoolYardPage.yard.name') == 'Cloud Garden'
        assert not errors
    finally:
        finish_browser(manager, browser)


def test_remove_unsaved_pet_preserves_an_explicit_recovery_choice(served):
    manager,browser=launch_chromium()
    try:
        page=browser.new_page(reduced_motion='reduce')
        response=page.request.post(served+'/api/pet-cosmetics/import',data={'revision':0,'document':json.dumps(pack())})
        assert response.status==200
        custom=response.json()['pets'][0]['id']
        page.goto(served+'/pet-yard')
        wait(page,'window.VoolYardPage?.controller && window.VoolPetCosmetics?.library.pets.length===1')
        page.locator('#yardRoster input[aria-label="Spark"]').uncheck()
        page.locator('#yardRoster input[aria-label="雲猫"]').check()
        page.locator('#yardName').fill('Keep this garden')
        page.locator('#yardName').press('Tab')
        assert page.evaluate('id=>VoolYardPage.yard.pets.some(p=>p.id===id)',custom)
        page.on('dialog',lambda d:d.accept())
        page.locator('#pet-cosmetics .pc-row').filter(has_text='雲猫').get_by_role('button',name='Remove',exact=True).click()
        wait(page,'VoolPetCosmetics.library.pets.length===0')
        assert page.locator('#yardRoster input[aria-label="雲猫"]').count()==1
        assert page.locator('#yardRoster input[aria-label="雲猫"]').is_checked()
        page.locator('#yardRoster input[aria-label="雲猫"]').click()
        wait(page,'!VoolYardPage.yard.pets.some(p=>p.id==='+json.dumps(custom)+')')
        assert page.locator('#yardRoster input[aria-label="雲猫"]').count()==0
        page.locator('#yardSave').click()
        wait(page,'VoolYardPage.yard.revision===1')
        saved=page.request.get(served+'/api/pet-yard').json()['yard']
        assert saved['name']=='Keep this garden'
        assert not any(p['id']==custom for p in saved['pets'])
    finally:
        finish_browser(manager,browser)


def test_terminal_pet_expression_and_voice_override_stale_work_category(served):
    import base64
    from io import BytesIO

    from PIL import Image
    p=pack()
    image=Image.new('RGBA',(96,48),'#3cb570')
    image.paste('#3254d0',(48,0,96,48))
    out=BytesIO();image.save(out,'PNG')
    p['atlas']['png']=base64.b64encode(out.getvalue()).decode()
    p['frames']['happy']={d:[0] for d in ['front','left','right']}
    from core.pet_cosmetics import STATES
    for state in STATES:
        p['frames'][state]={d:[1] for d in ['front','left','right']}
    manager,browser=launch_chromium()
    try:
        page=browser.new_page(reduced_motion='reduce')
        response=page.request.post(served+'/api/pet-cosmetics/import',data={'revision':0,'document':json.dumps(p)})
        assert response.status==200
        library=response.json();custom=library['pets'][0]['id'];config=library['config']
        config.update(selected=custom,sound=True)
        config['bindings'][custom]={'read':{'expression':'happy','sound':'builtin:bright-en-happy'},**{state:{'expression':state,'sound':''} for state in ['failure','unknown','cancelled']}}
        assert page.request.post(served+'/api/pet-cosmetics/configure',data={'revision':library['revision'],'config':config}).status==200
        page.goto(served+'/chat')
        wait(page,'window.VoolPetCosmetics?.activePet.startsWith("custom-")')
        wait(page,'document.querySelector("#companionLayer > .vool-ninja > canvas")?.getContext("2d").getImageData(24,24,1,1).data[0]===50')
        page.mouse.click(10,10)
        page.evaluate('VoolPetCosmetics.unlock()')
        wait(page,'VoolPetCosmetics.diagnostics.context==="running"')
        for state,event in [('failure','task.failed'),('unknown','task.completed'),('cancelled','task.cancelled')]:
            active=page.evaluate("""state=>{const token='late-'+state;VoolCompanion.consume(token,{type:'task.started'});VoolCompanion.consume(token,{type:'tool.started',tool:'read_file'});VoolStage.mirror(token,false);VoolPetCosmetics.activity('tool',token,'READING_FILES');const g=document.querySelector('#companionLayer > .vool-ninja > canvas').getContext('2d');VoolCompanionWorld.draw(g,'tool',0,{character:'spark'});return [...g.getImageData(24,24,1,1).data];}""",state)
            assert active==[60,181,112,255]
            result=page.evaluate("""({state,event})=>{const token='late-'+state;VoolCompanion.consume(token,{type:event});VoolStage.mirror(token,false);VoolPetCosmetics.activity(state,token,'READING_FILES');const g=document.querySelector('#companionLayer > .vool-ninja > canvas').getContext('2d');VoolCompanionWorld.draw(g,state,0,{character:'spark'});return {pixel:[...g.getImageData(24,24,1,1).data],state:document.documentElement.dataset.voolActivity};}""",{'state':state,'event':event})
            assert result['state']==state
            assert result['pixel']==[50,84,208,255]
            page.wait_for_timeout(750)
            assert page.evaluate('VoolPetCosmetics.diagnostics.plays')==0
    finally:
        finish_browser(manager,browser)
