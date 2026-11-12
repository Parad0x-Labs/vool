"""Budget and trust measurements use the served application, including live insertion."""
from __future__ import annotations

import json
import os
import tempfile
import time
import uuid
from pathlib import Path

import pytest

from tests._toybox_v2_browser import finish_browser, network_guard
from tests._toybox_v2_browser import served as served
from tests.served_browser import launch_chromium


def _protected_probe(page):
    from core.toybox_fragment import TRUSTED

    return page.evaluate(r"""selectors=>{
      const old=document.getElementById('trust-fixtures');if(old)old.remove();
      const host=document.createElement('div');host.id='trust-fixtures';document.body.append(host);
      return selectors.split(',').map((selector,i)=>{
       const n=document.createElement('section');
       if(selector.startsWith('#'))n.id=selector.slice(1);
       else if(selector.startsWith('.'))n.className=selector.slice(1).replaceAll('.',' ');
       else {const m=selector.match(/\[([^=]+)="([^"]+)"\]/);n.setAttribute(m[1],m[2]);}
       n.style.cssText='position:fixed!important;left:8px!important;top:'+(8+i*35)+'px!important;width:350px!important;height:30px!important;display:block!important;z-index:9999!important';
       n.innerHTML='<span class="msg-text"><button class="ctrl-btn">Protected sample</button></span>';host.append(n);
       const child=n.querySelector('button'),read=el=>{const s=getComputedStyle(el),r=el.getBoundingClientRect();return [s.color,s.backgroundColor,s.borderColor,s.fontFamily,s.fontSize,s.opacity,s.visibility,s.zIndex,[r.width,r.height]];};
       const r=n.getBoundingClientRect();let covered=false;
       for(let x=0;x<5;x++)for(let y=0;y<5;y++){
        const top=document.elementsFromPoint(r.x+(x+.5)*r.width/5,r.y+(y+.5)*r.height/5)[0];if(!top||!(top===n||n.contains(top)))covered=true;
       }
       return {selector,root:read(n),child:read(child),covered};
      });
    }""", TRUSTED)


def _measure(page, *, stream=False, seconds=10):
    client=page.context.new_cdp_session(page)
    labels=['vool-budget-'+uuid.uuid4().hex+'-'+side for side in ('start','end')]
    marks=[]
    client.on('Performance.metrics',lambda event: marks.append({**event,'received':time.monotonic()}) if event['title'] in labels else None)
    client.send('Emulation.setCPUThrottlingRate',{'rate':4})
    client.send('Performance.enable')
    # Snapshot diagnostics before the measured interval. CDP replies can be delayed
    # independently of the page's frame sampler; retain both clocks below.
    trace_before=page.evaluate("({attrs:{...document.documentElement.dataset},animations:document.getAnimations().map(a=>[a.animationName,a.playState,a.effect.target.id,a.effect.pseudoElement])})")
    try:
        started=time.monotonic()
        outside_before={x['name']:x['value'] for x in client.send('Performance.getMetrics')['metrics']}
        received_before=time.monotonic()
        values=page.evaluate("""({ms,stream,labels})=>new Promise((resolve,reject)=>{
         const root=document.documentElement,samples=[],longs=[],issues=new Set(),changes=[],expected=stream?'streaming':'idle';
         let start=0,last=0,timer,deadline,frame,ambient,ambientOff=null,changeCount=0,initial,initialVisibility;
         const o=new PerformanceObserver(list=>longs.push(...list.getEntries().map(e=>e.duration)));
         const visibility=()=>({visible:!document.hidden,focused:document.hasFocus()});
         function visible(){const v=visibility();if(!v.visible)issues.add('hidden');if(!v.focused)issues.add('unfocused');}
         function observe(records){for(const r of records){const value=root.getAttribute(r.attributeName);changeCount++;
          if(changes.length<16)changes.push({attribute:r.attributeName,old:r.oldValue,value,ms:performance.now()-start});
          if(r.attributeName==='data-vool-motion'&&(r.oldValue!=='full'||value!=='full'))issues.add('motion changed');
          if(r.attributeName==='data-vool-activity'&&(r.oldValue!==expected||value!==expected))issues.add('activity changed');
          if(!stream&&r.attributeName==='data-vool-ambient'){if(ambientOff===null)ambientOff=performance.now()-start;if(r.oldValue!=='on'||value!=='off')issues.add('ambience restarted');}
         }}
         function cleanup(){clearInterval(timer);clearTimeout(deadline);cancelAnimationFrame(frame);longs.push(...o.takeRecords().map(e=>e.duration));o.disconnect();ambient?.disconnect();document.removeEventListener('visibilitychange',visible);window.removeEventListener('focus',visible);window.removeEventListener('blur',visible);}
         function f(t){samples.push(t-last);last=t;if(t-start<ms)frame=requestAnimationFrame(f);else{
          observe(ambient.takeRecords());visible();cleanup();
          console.timeStamp(labels[1]);
          resolve({samples:samples.slice(2),longs,elapsed:performance.now()-start,initial,final:{...root.dataset},ambientOff,issues:[...issues],changes,changeCount,visibility:{initial:initialVisibility,final:visibility()}});
         }}
         try{
          o.observe({entryTypes:['longtask']});
          if(stream){VoolCompanion.consume('budget',{type:'task.started'});VoolStage.mirror('budget',true);timer=setInterval(()=>VoolStage.mirror('budget',true),100);}
          else VoolStage.mirror('idle',false);
          // Observe after the intentional arming transition, before any frame can run.
          initial={...root.dataset};initialVisibility=visibility();visible();
          ambient=new MutationObserver(observe);ambient.observe(root,{attributes:true,attributeOldValue:true,attributeFilter:['data-vool-ambient','data-vool-motion','data-vool-activity']});
          document.addEventListener('visibilitychange',visible);window.addEventListener('focus',visible);window.addEventListener('blur',visible);
          // Observe the state before either counter boundary. Re-arm the full
          // ambience inside it; identical state writes leave the observer intact.
          console.timeStamp(labels[0]);start=performance.now();last=start;
          VoolStage.mirror(stream?'budget':'idle',stream);
          deadline=setTimeout(()=>{observe(ambient.takeRecords());cleanup();reject(Error('Budget observation did not finish: '+JSON.stringify({initial,issues:[...issues],changes,elapsed:performance.now()-start})));},ms+1000);
          frame=requestAnimationFrame(f);
         }catch(error){cleanup();reject(error);}
        })""",{'ms':seconds*1000,'stream':stream,'labels':labels})
        received_frames=time.monotonic()
        outside_after={x['name']:x['value'] for x in client.send('Performance.getMetrics')['metrics']}
        received_after=time.monotonic()
    finally:
        client.send('Emulation.setCPUThrottlingRate',{'rate':1})
        client.detach()
    # console.timeStamp emits the same CDP counters inside the sampled page.
    # Host replies, result serialization and unrelated work outside it cannot
    # extend this interval. Retain the outside snapshots for attribution only.
    assert [mark['title'] for mark in marks] == labels, marks
    before,after=({x['name']:x['value'] for x in mark['metrics']} for mark in marks)
    samples=sorted(values['samples'])
    result={'seconds':seconds,'task_seconds':after['TaskDuration']-before['TaskDuration'],
            'metric_seconds':after['Timestamp']-before['Timestamp'],
            'page_seconds':values['elapsed']/1000,'sample_count':len(samples),'stream':stream,
            'sample_initial':values['initial'],'sample_final':values['final'],
            'ambient_on_seconds':None if values['ambientOff'] is None else values['ambientOff']/1000,
            'observation_issues':values['issues'],'observation_changes':values['changes'],
            'observation_change_count':values['changeCount'],'visibility':values['visibility'],
            'host_seconds':received_after-received_before,
            'measurement_source':'in_page_console_timestamps','boundary_marks':marks,
            'reply_seconds':{'before':received_before-started,'frames':received_frames-received_before,'after':received_after-received_frames},
            'raw_metrics':{'before':before,'after':after},
            'outside_raw_metrics':{'before':outside_before,'after':outside_after},
            'outside_metric_seconds':outside_after['Timestamp']-outside_before['Timestamp'],
            'outside_task_seconds':outside_after['TaskDuration']-outside_before['TaskDuration'],
            'p95_ms':samples[int(len(samples)*.95)],'long_tasks_ms':values['longs'],
            'before':trace_before,'after':page.evaluate("({attrs:{...document.documentElement.dataset},animations:document.getAnimations().map(a=>[a.animationName,a.playState,a.effect.target.id,a.effect.pseudoElement])})"),
            'metrics':{k:after[k]-before[k] for k in ('ScriptDuration','LayoutDuration','RecalcStyleDuration','LayoutCount','RecalcStyleCount')}}
    # Keep even an invalid first/default observation when later assertions fail.
    with (Path(tempfile.gettempdir())/'stage-budget-observations.jsonl').open('a') as output:
        output.write(json.dumps(result)+'\n')
    return result


def _assert_measurement_window(sample):
    # The absolute +0.1 s CPU allowance below is for a ten-second observation.
    # Never dilute the five-second ambience with an overlong capture or retry it.
    assert sample['seconds'] <= sample['metric_seconds'] <= sample['seconds']+.25, sample
    assert sample['seconds'] <= sample['page_seconds'] <= sample['seconds']+.25, sample
    assert sample['measurement_source'] == 'in_page_console_timestamps', sample
    assert sample['visibility']['initial'] == {'visible':True,'focused':True}, sample
    assert sample['visibility']['final'] == {'visible':True,'focused':True}, sample
    assert not sample['observation_issues'], sample
    assert sample['sample_final']['voolMotion'] == 'full', sample
    if sample['stream']:
        assert sample['sample_initial']['voolMotion'] == 'full', sample
        assert sample['sample_initial']['voolActivity'] == sample['sample_final']['voolActivity'] == 'streaming', sample
    if not sample['stream']:
        assert sample['sample_initial']['voolActivity'] == 'idle', sample
        assert sample['sample_initial']['voolMotion'] == 'full', sample
        assert sample['sample_initial']['voolAmbient'] == 'on', sample
        assert sample['sample_final']['voolAmbient'] == 'off', sample
        assert sample['ambient_on_seconds'] is not None and 4.9 <= sample['ambient_on_seconds'] <= 5.25, sample


def _tabs(page):
    # Browser-chrome traversal differs for an inactive reused page. Establish
    # foreground focus before comparing all forty stops, including the boundary.
    page.bring_to_front()
    # The standard-layout option preserves the original forty native focus stops.
    # A modal dock has its own keyboard contract, checked before this comparison.
    origin=page.evaluate('location.origin')
    settings=page.request.get(origin+'/api/toybox').json()
    if page.evaluate('Boolean(window.VoolToyboxChrome?.diagnostics.dock)'):
        page.locator('#tbDockChats').focus()
        dock=[]
        for _ in range(4):
            dock.append(page.evaluate('document.activeElement.id'))
            page.keyboard.press('Tab')
        assert dock==['tbDockChats','tbDockNew','tbDockHome','tbDockSettings']
    response=page.request.post(origin+'/api/toybox/configure',data={'revision':settings['revision'],'confirmed':True,'motion':settings['motion'],'layout_override':'standard'})
    assert response.status==200
    page.reload()
    page.wait_for_load_state('networkidle')
    page.bring_to_front()
    assert page.locator('html').get_attribute('data-vool-layout')=='standard'
    page.locator('#newProject').focus()
    ids=[]
    for _ in range(40):
        page.keyboard.press('Tab')
        ids.append(page.evaluate("document.activeElement.id||document.activeElement.tagName+':'+document.activeElement.getAttribute('role')+':'+document.activeElement.textContent.trim().slice(0,25)"))
    revision=page.request.get(origin+'/api/toybox').json()['revision']
    response=page.request.post(origin+'/api/toybox/configure',data={'revision':revision,'confirmed':True,'motion':settings['motion'],'layout_override':settings['layout_override']})
    assert response.status==200
    page.reload()
    page.wait_for_load_state('networkidle')
    page.bring_to_front()
    assert page.locator('html').get_attribute('data-vool-layout')==('standard' if settings['layout_override']=='standard' else 'skin')
    return ids


@pytest.mark.parametrize('theme',['dark','light'])
def test_complete_trust_motion_and_stream_budget(served,theme):
    from core.toybox_registry_v2 import manifests
    from core.ui_palette import APPEARANCE_COOKIE

    manager,browser=launch_chromium()
    records=[]
    try:
        context=browser.new_context(viewport={'width':1280,'height':900})
        # A stable, supported companion setting isolates the skin's CPU increment.
        # Its normal 60 Hz loop is independently covered by the served matrices.
        context.add_init_script("localStorage.setItem('vool_ninja_pos_v1',JSON.stringify({v:2,mode:'docked',personality:'spark',pack:'default',motion:'reduced'}));")
        context.add_cookies([{'url':served,'name':APPEARANCE_COOKIE,'value':theme+'.100'}])
        page=context.new_page()
        guard=network_guard(page,served)
        page.goto(served+'/chat')
        page.wait_for_function('window.VoolCompanion && window.VoolStage')
        page.wait_for_load_state('networkidle')
        companion_style = """()=>[...document.querySelectorAll('#companionLayer,#companionLayer canvas,.vn-sprite')].map(el=>{const s=getComputedStyle(el);return [el.tagName,s.color,s.backgroundColor,s.borderColor,s.fontFamily,s.opacity,s.visibility,s.zIndex];})"""
        companions = page.evaluate(companion_style)
        baseline=_protected_probe(page)
        assert not any(p['covered'] for p in baseline)
        page.locator('#trust-fixtures').evaluate('el=>el.remove()')
        tabs=_tabs(page)
        page.bring_to_front()
        page.evaluate("VoolStage.mirror('idle',false)")
        base_idle=_measure(page)
        _assert_measurement_window(base_idle)
        for manifest,*_ in manifests():
            revision=page.request.get(served+'/api/toybox').json()['revision']
            response=page.request.post(served+'/api/toybox/apply',data={'manifest':json.dumps(manifest),'revision':revision,'confirmed':True})
            assert response.status==200
            page.reload()
            page.wait_for_function('window.VoolStage && window.VoolCompanion')
            page.wait_for_load_state('networkidle')
            assert _protected_probe(page)==baseline,manifest['id']
            assert page.evaluate(companion_style)==companions,manifest['id']
            assert page.locator('#main').evaluate("el=>getComputedStyle(el).getPropertyValue('--tb-stage-header-dark')") == ''
            page.locator('#trust-fixtures').evaluate('el=>el.remove()')
            assert _tabs(page)==tabs,manifest['id']
            page.bring_to_front()
            page.evaluate("VoolStage.mirror('idle',false)")
            idle=_measure(page)
            stream=_measure(page,stream=True,seconds=5)
            # Every running skin animation must freeze on approval and reduced motion.
            for preparation in ("VoolCompanion.consume('budget',{type:'permission.required'});VoolStage.mirror('budget',false)",
                                "VoolStage.selection({motion:'off'})"):
                page.evaluate(preparation)
                assert not page.evaluate("document.getAnimations().some(a=>String(a.animationName||'').startsWith('tb-')&&a.playState==='running')")
            page.emulate_media(reduced_motion='reduce')
            assert page.locator('html').get_attribute('data-vool-motion')=='off'
            page.emulate_media(reduced_motion='no-preference')
            # Surrounding default runs keep thermal/runtime drift out of the skin delta.
            rev=page.request.get(served+'/api/toybox').json()['revision']
            assert page.request.post(served+'/api/toybox/restore',data={'revision':rev,'confirmed':True}).status==200
            page.reload()
            page.wait_for_function('window.VoolStage && window.VoolCompanion')
            page.wait_for_load_state('networkidle')
            page.bring_to_front()
            page.evaluate("VoolStage.mirror('idle',false)")
            after_default=_measure(page)
            comparison=(base_idle['task_seconds']+after_default['task_seconds'])/2
            records.append({'id':manifest['id'],'theme':theme,'default_before':base_idle,'default_after':after_default,'idle':idle,'stream':stream,'delta_seconds':idle['task_seconds']-comparison})
            for sample in (idle,stream,after_default):
                _assert_measurement_window(sample)
            assert idle['task_seconds']<=comparison+.1,records[-1]
            assert stream['p95_ms']<=20,records[-1]
            base_idle=after_default
        assert not guard()
        context.close()
    finally:
        (Path(tempfile.gettempdir())/('stage-budget-'+theme+'.json')).write_text(json.dumps(records,indent=2))
        finish_browser(manager, browser)
