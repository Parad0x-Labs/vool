"""Pet presentation is timed, quiet at rest, and independent of screen refresh."""

from core.companion_world_fragment import COMPANION_WORLD_JS, render_desktop_companion_html
from tests.chat_page_js_harness import DOM, run_node


def desktop_driver(driver, payload=None):
    html = render_desktop_companion_html(payload)
    script = html.split('<script>', 1)[1].split('</script>', 1)[0]
    return run_node(DOM + "\nlet callback; requestAnimationFrame=f=>{callback=f;return 1;};\n"
                    + script + '\nconst {VoolCompanionWorld,VoolDesktopCompanion}=window;\n' + driver.replace('console.log(JSON.stringify({frames}));','out({frames});').replace('console.log(JSON.stringify({seen}));','out({seen});'))


def test_idle_breathes_on_time_never_on_the_refresh_rate():
    """Owner 2026-11-22: the painted pets breathe at rest. That motion is timed (20 frames a
    second) so a 120 Hz screen draws exactly as often as a 60 Hz one."""
    result = desktop_driver("""
const frames=[];VoolCompanionWorld.draw=(_g,_s,f)=>frames.push(f);
for(let t=0;t<=12000;t+=1000/120)callback(t);
console.log(JSON.stringify({frames}));
""")
    frames = result['frames']
    assert frames == sorted(set(frames)), 'each frame is drawn once, in order'
    assert 12000 // 50 - 1 <= frames[-1] <= 12000 // 50 and len(frames) <= 12000 // 50 + 1, 'idle is 20 frames a second'


def test_active_cadence_depends_on_time_and_terminal_pose_holds():
    result = run_node('globalThis.window=globalThis;\n' + COMPANION_WORLD_JS + """
const f=VoolCompanionWorld.frameAt;
console.log(JSON.stringify({tool:[f('spark','tool',32,false),f('spark','tool',33,false)],
 terminal:[f('spark','success',2000,false),f('spark','success',9000,false)],
 reduced:f('spark','tool',5000,true)}));
""")
    assert result['tool'] == [0, 1], 'work is 30 frames a second, on time'
    assert result['terminal'][0] == result['terminal'][1] > 0, 'a finished pose plays once and holds'
    assert result['reduced'] == 0


def test_update_restarts_new_state_but_not_repeated_sync_and_respects_motion():
    result = desktop_driver("""
const frames=[];VoolCompanionWorld.draw=(_g,s,f)=>frames.push([s,f]);
callback(0);callback(220);
VoolDesktopCompanion.update({state:'tool'});callback(230);callback(450);
VoolDesktopCompanion.update({state:'tool'});callback(560);
VoolDesktopCompanion.update({motion:'reduced'});callback(570);callback(900);
console.log(JSON.stringify({frames}));
""")
    # A new state starts its animation over; the same state sent again does not; reduced motion
    # draws one still picture and then nothing more.
    assert result['frames'] == [['idle', 0], ['idle', 4], ['tool', 0], ['tool', 6], ['tool', 10], ['tool', 0]]


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
