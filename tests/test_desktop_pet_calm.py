"""Pet presentation is timed, quiet at rest, and independent of screen refresh."""

from core.companion_world_fragment import COMPANION_WORLD_JS, render_desktop_companion_html
from tests.chat_page_js_harness import DOM, run_node


def desktop_driver(driver, payload=None):
    html = render_desktop_companion_html(payload)
    script = html.split('<script>', 1)[1].split('</script>', 1)[0]
    return run_node(DOM + "\nlet callback; requestAnimationFrame=f=>{callback=f;return 1;};\n"
                    + script + '\nconst {VoolCompanionWorld,VoolDesktopCompanion}=window;\n' + driver.replace('console.log(JSON.stringify({frames}));','out({frames});').replace('console.log(JSON.stringify({seen}));','out({seen});'))


def test_idle_has_one_short_blink_and_no_refresh_rate_speedup():
    result = desktop_driver("""
const frames=[];VoolCompanionWorld.draw=(_g,_s,f)=>frames.push(f);
for(let t=0;t<=12000;t+=1000/120)callback(t);
console.log(JSON.stringify({frames}));
""")
    assert set(result['frames']) == {0, 2}
    assert len(result['frames']) <= 5, 'idle redraws only for its brief blink; never bounces per refresh'


def test_active_cadence_depends_on_time_and_terminal_pose_holds():
    result = run_node('globalThis.window=globalThis;\n' + COMPANION_WORLD_JS + """
const f=VoolCompanionWorld.frameAt;
console.log(JSON.stringify({tool:[f('spark','tool',109,false),f('spark','tool',110,false)],
 terminal:[f('spark','success',1000,false),f('spark','success',9000,false)],
 reduced:f('spark','tool',5000,true)}));
""")
    assert result == {'tool': [0, 1], 'terminal': [5, 5], 'reduced': 0}


def test_update_restarts_new_state_but_not_repeated_sync_and_respects_motion():
    result = desktop_driver("""
const frames=[];VoolCompanionWorld.draw=(_g,s,f)=>frames.push([s,f]);
callback(0);callback(220);
VoolDesktopCompanion.update({state:'tool'});callback(230);callback(450);
VoolDesktopCompanion.update({state:'tool'});callback(560);
VoolDesktopCompanion.update({motion:'reduced'});callback(570);callback(900);
console.log(JSON.stringify({frames}));
""")
    assert result['frames'] == [['idle', 0], ['tool', 0], ['tool', 2], ['tool', 3], ['tool', 0]]


def test_native_hover_exit_clears_controls_even_after_drag():
    result = desktop_driver("""
const shell=document.getElementById('shell'), seen=[];
for(const state of [[false,false],[true,false],[false,true],[false,false]]){
 VoolDesktopCompanion.interaction(...state);seen.push(shell.classList.contains('interacting'));
}
console.log(JSON.stringify({seen}));
""")
    assert result['seen'] == [False, True, True, False]
    html = render_desktop_companion_html()
    assert '#shell:hover #controls' not in html, 'stale web hover must not override native pointer truth'
    assert '#shell.interacting #controls' in html


def test_pet_renderer_idle_pose_does_not_move_the_body():
    result = run_node('globalThis.window=globalThis;\n'+COMPANION_WORLD_JS+"""
function pixels(frame){let out=[];const g={clearRect(){},fillRect(x,y,w,h){out.push([x,y,w,h,this.fillStyle])}};
VoolCompanionWorld.draw(g,'idle',frame,{character:'spark',ground:false});return out;}
const a=pixels(0),b=pixels(2);
console.log(JSON.stringify({bodyA:a.slice(0,23),bodyB:b.slice(0,23)}));
""")
    assert result['bodyA'] == result['bodyB']
