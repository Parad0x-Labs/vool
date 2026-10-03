"""Real canvas checks: companions have no added floor or external cast shadow."""

from __future__ import annotations

import pytest

from core.companion_art_fragment import COMPANION_ROSTER
from core.companion_world_fragment import render_desktop_companion_html
from tests.test_companion_visible_layout import browser as browser
from tests.test_companion_visible_layout import inline, save_evidence

SPY = r"""canvas => {
  const g=canvas.getContext('2d');
  if(g.groundMarks)return;
  g.groundMarks=[];
  const fill=g.fillRect.bind(g),clear=g.clearRect.bind(g);
  g.fillRect=function(x,y,w,h){this.groundMarks.push([x,y,w,h,this.fillStyle]);fill(x,y,w,h);};
  g.clearRect=function(...args){this.groundMarks=[];clear(...args);};
}"""

CHECK = r"""canvas => {
  const g=canvas.getContext('2d'), swatch=document.createElement('canvas').getContext('2d');
  const colors=['rgba(23,38,43,.18)','rgba(17,29,38,.2)','rgba(32,43,43,.2)',
    'rgba(18,44,67,.2)','rgba(36,26,16,.2)','rgba(31,24,17,.2)','rgba(21,23,31,.2)',
    'rgba(26,31,48,.18)','rgba(23,19,25,.2)','rgba(35,16,9,.2)']
    .map(color=>{swatch.fillStyle=color;return swatch.fillStyle;});
  swatch.fillStyle='rgba(0,0,0,.35)';colors.push(swatch.fillStyle);
  return g.groundMarks.filter(([x,y,w,h,color])=>
    (x===0 && y===42 && w===48 && h===6) ||
    ((y===44 || y===45 || y===42) && colors.includes(color)));
}"""


@pytest.mark.parametrize("host", ["inline", "native"])
def test_all_ten_pets_have_no_ground_in_the_actual_host(browser, host):
    page = browser.new_page(viewport={"width": 900, "height": 650})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    try:
        if host == "inline":
            inline(page)
            selector = "#companionLayer canvas"
        else:
            page.set_content(render_desktop_companion_html({"motion": "reduced"}))
            page.wait_for_function("window.VoolDesktopCompanion")
            selector = "#pet"
        page.locator(selector).evaluate(SPY)
        failures = {}
        # Copies of actual host canvases form the evidence sheet, rather than a separate renderer.
        page.evaluate("""() => {
          const sheet=document.createElement('div');sheet.id='groundless-roster';
          sheet.style='position:fixed;inset:0;z-index:99999;padding:24px;background:#141416;'
            +'color:#e6e2dc;font:14px system-ui;display:grid;grid-template-columns:repeat(5,160px);'
            +'grid-template-rows:repeat(2,220px);gap:10px;align-content:center;';
          document.body.appendChild(sheet);
        }""")
        for pet in COMPANION_ROSTER:
            if host == "inline":
                page.evaluate("id=>{VoolCompanion.chooseCharacter(id);VoolCompanion.paintNow();}", pet)
            else:
                page.evaluate("id=>VoolDesktopCompanion.update({character:id,state:'idle',motion:'reduced'})", pet)
                page.evaluate("() => new Promise(requestAnimationFrame)")
            marks = page.locator(selector).evaluate(CHECK)
            if marks:
                failures[pet] = marks
            assert page.locator(selector).evaluate("el=>el.getContext('2d').getImageData(0,0,48,48).data.some(v=>v)")
            page.locator(selector).evaluate(
                """(source,name)=>{
              const cell=document.createElement('div'),cv=document.createElement('canvas'),label=document.createElement('div');
              cv.width=cv.height=48;cv.style='width:144px;height:144px;image-rendering:pixelated;';
              cv.getContext('2d').putImageData(source.getContext('2d').getImageData(0,0,48,48),0,0);
              label.textContent=name;cell.append(cv,label);document.getElementById('groundless-roster').append(cell);
            }""",
                pet,
            )
        save_evidence(page, f"groundless-{host}-roster.png")
        assert not failures, failures
        assert page.locator(selector).evaluate("el=>getComputedStyle(el).filter") == "none"
        assert not errors
    finally:
        page.close()


def test_all_states_actions_and_worker_scenes_keep_props_but_no_floor(browser):
    page = browser.new_page()
    try:
        inline(page)
        page.evaluate(
            "() => {const cv=document.createElement('canvas');cv.id='probe';cv.width=cv.height=48;document.body.appendChild(cv);}"
        )
        canvas = page.locator("#probe")
        canvas.evaluate(SPY)
        failures = {}
        states = [
            "idle",
            "starting",
            "thinking",
            "tool",
            "waiting",
            "approval",
            "retry",
            "success",
            "failure",
            "cancelled",
            "unknown",
        ]
        actions = ["read", "search", "dig", "code", "exec", "test", "watch"]
        for pet in COMPANION_ROSTER:
            for state, activity in [(state, "") for state in states] + [("tool", a) for a in actions]:
                canvas.evaluate(
                    "(cv,args)=>VoolCompanionWorld.draw(cv.getContext('2d'),args[1],2,{character:args[0],activity:args[2],elapsed:1400})",
                    [pet, state, activity],
                )
                marks = canvas.evaluate(CHECK)
                if marks:
                    failures[f"{pet}/{state}/{activity}"] = marks
        # Retain real desks/screens in worker scenes; remove only the decorative floor/shadow.
        for scene in ["pair", "huddle"]:
            assert canvas.evaluate(
                "(cv,scene)=>VoolCompanionWorld.scene(cv.getContext('2d'),scene,6,{character:'beetle'})", scene
            )
            marks = canvas.evaluate(CHECK)
            if marks:
                failures[scene] = marks
            prop = [18, 25, 23, 3, "#343a46"] if scene == "pair" else [18, 16, 12, 9, "#e8eaf0"]
            assert canvas.evaluate(
                "(cv,prop)=>cv.getContext('2d').groundMarks.some(mark=>JSON.stringify(mark)===JSON.stringify(prop))",
                prop,
            )
        canvas.evaluate("cv=>VoolCompanionWorld.draw(cv.getContext('2d'),'idle',0,{character:'unrecognised-old-id'})")
        marks = canvas.evaluate(CHECK)
        if marks:
            failures["legacy-fallback"] = marks
        assert not failures, failures
    finally:
        page.close()
