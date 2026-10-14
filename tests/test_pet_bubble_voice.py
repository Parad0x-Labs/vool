"""The pet's bubble speaks only while there is something to say, in a playful but clear voice.

Owner report (2026-10-14): the bubble showed while the pet was doing nothing, and its words
were dry. Wanted: quiet when idle; playful lines while working ("fetching unicorns", "goblins
arrived"); unmistakable lines when VOOL waits on the user ("your decision, my lord"); and a
"done" line when a job finishes ("job's done", "done, going to sleep"), with many variations.
"""

from __future__ import annotations

import re

import pytest

from core.companion_quips import MAX_LINE, QUIPS
from core.companion_world_fragment import normalise_companion_payload, render_desktop_companion_html
from tests.served_browser import launch_chromium
from tests.test_pet_ux_squad import _run

ASK_WORDS = re.compile(r"decision|verdict|\bOK\b|approv|answer|decree", re.I)
OVERCLAIM = re.compile(r"correct|checked|verified|perfect|nailed|right|success", re.I)


def test_every_line_fits_says_its_state_and_has_no_emoji() -> None:
    seen = set()
    for pool, lines in QUIPS.items():
        assert len(lines) >= 6, f"{pool}: too few lines to feel varied"
        for line in lines:
            assert line not in seen, f"duplicate line {line!r}"
            seen.add(line)
            assert 0 < len(line) <= MAX_LINE, f"{line!r} does not fit the bubble"
            assert re.fullmatch(r"[A-Za-z0-9 ,.!?'’:;\-…]+", line), f"{line!r} has a symbol or emoji"
    for line in QUIPS["ask"]:
        assert ASK_WORDS.search(line), f"{line!r} does not plainly ask for the user's decision"
    for line in QUIPS["done"]:
        assert not OVERCLAIM.search(line), f"{line!r} claims more than a finished, unreviewed job"


BUBBLE_DRIVER = r"""
const res = { errors: [] };
try {
  const VC = window.VoolCompanion;
  const find = (n, cls) => n.className === cls ? n : (n.children || []).reduce((f, c) => f || find(c, cls), null);
  const bubble = find(document.body, "vn-bubble"), line = find(bubble, "vn-bubble-activity");
  const look = (t) => { VC.paintNow(t); return { shown: bubble.style.display, line: line.textContent }; };
  const t0 = Date.now() + 100000;
  displayedChat = "c1";
  res.idle = look(t0);
  VC.consume("c1", { type: "task.started", seq: 1 });
  VC.consume("c1", { type: "tool.started", seq: 2, tool: "web_search" });
  res.search = look(t0 + 1000);
  VC.consume("c1", { type: "tool.started", seq: 3, tool: "read_file" });
  res.burst = look(t0 + 1500);
  res.searchLater = look(t0 + 60000);
  VC.consume("c1", { type: "permission.required", seq: 4 });
  res.ask = look(t0 + 61000);
  res.askLater = look(t0 + 600000);
  displayedChat = "c2";
  VC.consume("c2", { type: "task.started", seq: 1 });
  VC.consume("c2", { type: "task.completed", seq: 2 });
  res.done = look(t0 + 700000);
  res.doneLater = look(t0 + 700000 + 13000);
  displayedChat = "c1"; look(t0 + 700000 + 14000);
  displayedChat = "c2";
  res.doneReopened = look(t0 + 700000 + 15000);
  displayedChat = "c3";
  VC.consume("c3", { type: "task.started", seq: 1 });
  VC.consume("c3", { type: "task.failed", seq: 2 });
  res.failed = look(t0 + 800000);
  res.failedPhrase = VC.resolve(VC.view("c3")).phraseText;
  res.failedLater = look(t0 + 900000 - 1000);
  document.documentElement.lang = "de";
  displayedChat = "c4";
  VC.consume("c4", { type: "task.started", seq: 1 });
  VC.consume("c4", { type: "tool.started", seq: 2, tool: "web_search" });
  res.german = look(t0 + 900000);
  res.germanPhrase = VC.resolve(VC.view("c4")).phraseText;
} catch (e) { res.errors.push(String(e && e.stack || e)); }
out(res);
"""


def test_the_bubble_is_quiet_when_idle_playful_at_work_and_clear_when_it_needs_you() -> None:
    out = _run(BUBBLE_DRIVER)
    assert out["idle"]["shown"] == "none", "a pet doing nothing says nothing"
    assert out["search"]["shown"] == "flex" and out["search"]["line"] in QUIPS["search"], out["search"]
    assert out["burst"]["line"] == out["search"]["line"], "a burst of quick steps does not flicker the line"
    assert out["searchLater"]["line"] == out["search"]["line"], "lines change with the work, not on a timer"
    assert out["ask"]["line"] in QUIPS["ask"], out["ask"]
    assert out["askLater"]["shown"] == "flex", "a waiting decision stays visible until it is answered"
    assert out["done"]["shown"] == "flex" and out["done"]["line"] in QUIPS["done"], out["done"]
    assert out["doneLater"]["shown"] == "none", "after the moment passes the finished pet goes quiet"
    assert out["doneReopened"]["shown"] == "none", "opening a finished chat again does not replay its done line"
    assert out["failed"]["line"] == out["failedPhrase"] and out["failedPhrase"], "no jokes about a failure"
    assert out["failedLater"]["shown"] == "flex", "a failure stays up until something changes"
    assert out["german"]["line"] == out["germanPhrase"], "other languages keep the translated plain phrase"


DESKTOP_DRIVER = r"""
const res = { errors: [], payloads: [] };
try {
  const VC = window.VoolCompanion;
  window.pywebview = { api: {
    detach_companion: () => Promise.resolve({ ok: true }),
    sync_companion: (p) => { res.payloads.push(p); return Promise.resolve({ ok: true }); } } };
  displayedChat = "d1";
  const t0 = Date.now() + 100000;
  VC.paintNow(t0);
  await VC.detachDesktop(true);
  VC.consume("d1", { type: "task.started", seq: 1 });
  VC.consume("d1", { type: "task.completed", seq: 2 });
  VC.paintNow(t0 + 1000);
  VC.paintNow(t0 + 20000);
} catch (e) { res.errors.push(String(e && e.stack || e)); }
out(res);
"""


def test_the_desktop_pet_says_the_same_line_and_goes_quiet_with_it() -> None:
    payloads = _run(DESKTOP_DRIVER)["payloads"]
    done = [p for p in payloads if p.get("caption") in QUIPS["done"]]
    assert done and not done[0].get("quiet"), payloads
    assert payloads[-1].get("quiet") is True, "the desktop bubble goes quiet when the in-app one does"
    assert normalise_companion_payload({"state": "unknown", "quiet": True})["quiet"] is True
    assert "quiet" not in normalise_companion_payload({"state": "unknown", "quiet": "yes"})


@pytest.fixture(scope="module")
def browser():
    manager, instance = launch_chromium()
    yield instance
    instance.close()
    manager.stop()


def test_the_desktop_bubble_hides_when_quiet_and_speaks_again_after(browser) -> None:
    page = browser.new_page(viewport={"width": 208, "height": 236})
    try:
        page.set_content(render_desktop_companion_html({"state": "idle", "chatTitle": "Chat A"}))
        page.wait_for_function("window.VoolDesktopCompanion")
        assert page.locator("#bubble").is_hidden(), "an idle desktop pet says nothing"
        page.evaluate("VoolDesktopCompanion.update({state:'unknown',chatTitle:'Chat A',caption:'Donzo.'})")
        assert page.locator("#bubble").is_visible()
        page.evaluate("VoolDesktopCompanion.update({state:'unknown',chatTitle:'Chat A',caption:'Donzo.',quiet:true})")
        assert page.locator("#bubble").is_hidden()
        page.evaluate("VoolDesktopCompanion.update({state:'tool',chatTitle:'Chat A',caption:'Digging…'})")
        assert page.locator("#bubble").is_visible(), "a new snapshot without the flag speaks again"
    finally:
        page.close()
