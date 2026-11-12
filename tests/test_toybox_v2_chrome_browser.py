from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from core.toybox_registry_v2 import manifests
from tests._toybox_v2_browser import finish_browser, network_guard, shoot
from tests._toybox_v2_browser import served as served
from tests.served_browser import launch_chromium
from tests.test_pet_world_journey import wait


def apply(page, served, manifest, revision):
    result=page.request.post(served+'/api/toybox/apply',data={'manifest':json.dumps(manifest),'revision':revision,'confirmed':True})
    assert result.status==200
    return result.json()['revision']


@pytest.mark.parametrize('locale',['en','ar'])
def test_dock_drawer_keyboard_mobile_rtl_and_native_actions(served,locale):
    manager,browser=launch_chromium()
    try:
        page=browser.new_page(viewport={'width':1280,'height':900})
        outside=network_guard(page,served)
        manifest=next(m for m,_,_ in manifests() if m['id']=='night-observatory')
        apply(page,served,manifest,0)
        page.goto(served+'/chat?ui_locale='+locale)
        wait(page,'window.VoolToyboxChrome?.diagnostics.dock')
        rail=page.locator('#tb-dock').bounding_box()
        assert rail['width']==56
        assert rail['x']==(1224 if locale=='ar' else 0)
        shoot(page,'dock-rail-'+locale)
        assert page.locator('#sidebar').evaluate('e=>e.inert')
        assert page.locator('#log').bounding_box()['width']>=640
        toggle=page.locator('#tbDockChats')
        toggle.click()
        wait(page,'VoolToyboxChrome.diagnostics.drawer')
        assert page.locator('#sidebar').get_attribute('aria-modal')=='true'
        assert not page.locator('#sidebar').evaluate('e=>e.inert')
        shoot(page,'dock-drawer-'+locale)
        page.locator('#newProject').focus()
        page.keyboard.press('Shift+Tab')
        assert page.locator('#sideResize').evaluate('e=>e===document.activeElement')
        page.keyboard.press('Tab')
        assert page.locator('#newProject').evaluate('e=>e===document.activeElement')
        page.keyboard.press('Escape')
        wait(page,'!VoolToyboxChrome.diagnostics.drawer')
        assert toggle.evaluate('e=>e===document.activeElement')
        page.locator('#tbDockNew').click()
        wait(page,'document.activeElement===document.getElementById("input") && !VoolToyboxChrome.diagnostics.drawer')
        with page.expect_popup() as opened:
            page.locator('#tbDockSettings').click()
        settings=opened.value
        settings.wait_for_url(served+'/settings')
        settings.locator('#paneBody').wait_for()
        assert settings.locator('#tb-dock').count()==0
        assert settings.locator('#tb-perch').count()==0
        settings.close()
        page.bring_to_front()
        page.locator('#tbDockHome').click()
        wait(page,'VoolToyboxChrome.diagnostics.drawer && document.getElementById("homeMenu").open')
        assert page.locator('#petYardLink').is_visible()
        # Actual reducer authority can take focus back from a navigation drawer.
        page.evaluate("VoolCompanion.consume('drawer-approval',{type:'task.started'});VoolCompanion.consume('drawer-approval',{type:'permission.required'});VoolStage.mirror('drawer-approval',false)")
        wait(page,'!VoolToyboxChrome.diagnostics.drawer')
        page.set_viewport_size({'width':480,'height':900})
        rail=page.locator('#tb-dock').bounding_box()
        assert rail['height']==56 and rail['width']==480 and rail['y']==844
        assert page.locator('#main').bounding_box()['height']<=844
        shoot(page,'dock-mobile-'+locale)
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        page.locator('#tbDockChats').click()
        wait(page,'VoolToyboxChrome.diagnostics.drawer')
        page.locator('#sideScrim').click(position={'x':470 if locale=='en' else 10,'y':20})
        wait(page,'!VoolToyboxChrome.diagnostics.drawer')
        assert page.locator('#tbDockChats').evaluate('e=>e===document.activeElement')
        page.locator('#tbDockHome').click()
        wait(page,'VoolToyboxChrome.diagnostics.drawer && document.getElementById("homeMenu").open')
        page.locator('#petYardLink').click()
        wait(page,'window.VoolYardPage?.controller')
        assert page.url.split('?')[0]==served+'/pet-yard'
        assert not outside()
        shoot(page,'dock-yard-'+locale)
    finally:
        finish_browser(manager,browser)


def test_original_perches_follow_pet_without_touching_its_styles_or_islands(served):
    manager,browser=launch_chromium()
    try:
        page=browser.new_page(viewport={'width':1280,'height':900},reduced_motion='reduce')
        outside=network_guard(page,served)
        page.goto(served+'/chat')
        wait(page,'document.querySelector("#companionLayer > .vool-ninja > canvas")')
        properties=['position','width','height','display','zIndex','color','backgroundColor','fontFamily','pointerEvents','opacity']
        def pet_style():
            return page.locator('#companionLayer > .vool-ninja > canvas').evaluate('(e,props)=>Object.fromEntries(props.map(p=>[p,getComputedStyle(e)[p]]))',properties)
        baseline={}
        for theme in ['dark','light']:
            page.context.add_cookies([{'name':'vool_appearance','value':theme+'.100','url':served}])
            page.goto(served+'/chat')
            wait(page,'document.querySelector("#companionLayer > .vool-ninja > canvas")')
            baseline[theme]=pet_style()
        revision=0
        for manifest,_,_ in manifests():
            revision=apply(page,served,manifest,revision)
            for theme in ['dark','light']:
                page.context.add_cookies([{'name':'vool_appearance','value':theme+'.100','url':served}])
                page.goto(served+'/chat')
                wait(page,'window.VoolToyboxChrome?.diagnostics.perch')
                assert pet_style()==baseline[theme]
                assert page.locator('#tb-perch').get_attribute('aria-hidden')=='true'
                assert page.locator('#tb-perch').evaluate('e=>getComputedStyle(e).pointerEvents')=='none'
                assert page.locator('#tb-perch').evaluate('e=>getComputedStyle(e).zIndex')=='29'
                assert page.locator('#tb-perch').evaluate('e=>getComputedStyle(e).backgroundImage').startswith('url("data:image/svg+xml,')
                aligned=page.evaluate('''()=>{const c=document.querySelector('#companionLayer > .vool-ninja > canvas'),r=c.getBoundingClientRect(),b=document.getElementById('tb-perch').getBoundingClientRect(),d=c.getContext('2d').getImageData(0,0,c.width,c.height).data;let foot=0;outer:for(let y=c.height-1;y>=0;y--)for(let x=Math.floor(c.width/4);x<Math.ceil(c.width*3/4);x++)if(d[(y*c.width+x)*4+3]>127){foot=y+1;break outer;}return Math.abs(b.top+b.height*5/32-(r.top+foot*r.height/c.height));}''')
                assert aligned<2
                before=page.locator('#tb-perch').bounding_box()
                # The real native drag handle moves the existing sprite; the frame follows it.
                hit=page.locator('#companionLayer > .vool-ninja > .vn-hit').evaluate('''e=>{const r=e.getBoundingClientRect();for(let y=r.top+8;y<r.bottom-8;y+=12)for(let x=r.left+8;x<r.right-8;x+=12)if(document.elementFromPoint(x,y)===e)return {x,y};return null;}''')
                assert hit is not None
                page.mouse.move(hit['x'],hit['y'])
                page.mouse.down()
                page.mouse.move(hit['x']+(-80 if before['x']>640 else 80),hit['y']+(-30 if before['y']>450 else 30),steps=8)
                page.mouse.up()
                wait(page,'Math.abs(document.getElementById("tb-perch").getBoundingClientRect().x-'+str(before['x'])+')>30')
                assert pet_style()==baseline[theme]
                # Real protected element, made visible in the same way as the trust harness.
                page.evaluate('''()=>{const p=document.getElementById('tb-perch').getBoundingClientRect(),island=document.getElementById('permBar');window.originalIslandStyle=island.getAttribute('style');island.hidden=false;island.style.cssText=`position:fixed;left:${p.x}px;top:${p.y}px;width:${p.width}px;height:40px;z-index:35`;document.body.classList.add('answer-pending');const actual=island.getBoundingClientRect();island.style.transform=`translate(${p.x-actual.x}px,${p.y-actual.y}px)`;}''')
                wait(page,'!VoolToyboxChrome.diagnostics.perch')
                assert page.locator('#permBar').evaluate('e=>{const r=e.getBoundingClientRect();return document.elementFromPoint(r.x+r.width/2,r.y+r.height/2)?.closest("#permBar")===e;}')
                page.evaluate('''()=>{const e=document.getElementById('permBar');e.hidden=true;if(originalIslandStyle===null)e.removeAttribute('style');else e.setAttribute('style',originalIslandStyle);document.body.classList.remove('answer-pending');}''')
                wait(page,'VoolToyboxChrome.diagnostics.perch')
                if manifest['id']=='dawn-circuit' and theme=='dark':
                    shoot(page,'perch-dawn-circuit')
        assert not outside()
    finally:
        finish_browser(manager,browser)


def test_native_docking_places_perch_before_current_frame_paint(served):
    manager, browser = launch_chromium()
    records = []
    try:
        page = browser.new_page(viewport={'width':1280,'height':900}, reduced_motion='reduce')
        manifest = next(m for m, *_ in manifests() if m['id']=='pocket-transceiver')
        apply(page, served, manifest, 0)
        for theme in ('dark', 'light'):
            page.context.add_cookies([{'name':'vool_appearance','value':theme+'.100','url':served}])
            page.goto(served+'/chat')
            wait(page, 'window.VoolToyboxChrome?.diagnostics.perch')
            hit = page.locator('#companionLayer > .vool-ninja > .vn-hit').evaluate('''e=>{const r=e.getBoundingClientRect();for(let y=r.top+8;y<r.bottom-8;y+=12)for(let x=r.left+8;x<r.right-8;x+=12)if(document.elementFromPoint(x,y)===e)return {x,y};return null;}''')
            assert hit is not None
            page.mouse.move(hit['x'],hit['y'])
            page.mouse.down()
            page.mouse.move(hit['x']-80,hit['y']-40,steps=8)
            page.mouse.up()
            wait(page, 'VoolCompanion.pos().mode==="free" && VoolToyboxChrome.diagnostics.perch')
            sample = page.evaluate('''()=>new Promise(resolve=>{
              requestAnimationFrame(()=>VoolCompanion.dock());
              requestAnimationFrame(()=>{
                const c=document.querySelector('#companionLayer > .vool-ninja > canvas'),r=c.getBoundingClientRect(),p=document.getElementById('tb-perch'),b=p.getBoundingClientRect(),d=c.getContext('2d').getImageData(0,0,c.width,c.height).data;
                let foot=0;outer:for(let y=c.height-1;y>=0;y--)for(let x=Math.floor(c.width/4);x<Math.ceil(c.width*3/4);x++)if(d[(y*c.width+x)*4+3]>127){foot=y+1;break outer;}
                resolve({delta:Math.abs(b.top+b.height*5/32-(r.top+foot*r.height/c.height)),canvas:r.toJSON(),perch:b.toJSON(),foot,hidden:p.hidden,mode:VoolCompanion.pos().mode});
              });
            })''')
            records.append({'theme':theme, **sample})
            (Path(os.environ['TMPDIR'])/'perch-native-frame-order.json').write_text(json.dumps(records,indent=2))
            assert sample['mode']=='docked'
            assert not sample['hidden']
            assert sample['delta']<2
    finally:
        finish_browser(manager,browser)
