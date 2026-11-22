"""The pet's bubble says what VOOL is really doing, and only while there is something to say.

Owner report (2026-10-14): the bubble showed while the pet was doing nothing. Owner, 2026-11-22:
the popup must show the actual work, not fixed phrases -- it used to pick a random joke for the
kind of work ("Fetching unicorns...", "Your decision, my lord"). Now line 2 is the run card's own
words for the newest real step, the approval's own action while VOOL waits on the user, and the
real duration when a turn finishes. Quiet when idle; a finished line goes quiet after a moment.
"""

from __future__ import annotations

import pytest

from core.companion_world_fragment import normalise_companion_payload, render_desktop_companion_html
from tests.served_browser import launch_chromium
from tests.test_pet_ux_squad import _run

BUBBLE_DRIVER = r"""
const res = { errors: [] };
try {
  const VC = window.VoolCompanion;
  const find = (n, cls) => n.className === cls ? n : (n.children || []).reduce((f, c) => f || find(c, cls), null);
  const bubble = find(document.body, "vn-bubble"), line = find(bubble, "vn-bubble-activity");
  const look = (t) => { VC.paintNow(t); return { shown: bubble.style.display, line: line.textContent }; };
  // Events go through the page's own applyTaskEvent, exactly as the stream delivers them, so
  // the pet is fed by the real wiring and the card's words can be compared with the pet's.
  const runFor = (chat) => { const r = newRun(chat); r.chatId = chat; return r; };
  const t0 = Date.now() + 100000;
  displayedChat = "c1";
  const r1 = runFor("c1");
  res.idle = look(t0);
  applyTaskEvent(r1, { type: "task.submitted", ts: new Date(t0).toISOString(), userText: "how is the weather in Rome" });
  applyTaskEvent(r1, { type: "task.started", seq: 1 });
  applyTaskEvent(r1, { type: "tool.started", seq: 2, tool: "live_data.weather_lookup" });
  res.lookup = look(t0 + 1000); res.lookupCard = r1.action;
  applyTaskEvent(r1, { type: "tool.completed", seq: 3, tool: "live_data.weather_lookup" });
  applyTaskEvent(r1, { type: "tool.started", seq: 4, tool: "read_file", summary: "Reading notes.txt" });
  res.read = look(t0 + 1500); res.readCard = r1.action;
  res.readLater = look(t0 + 60000);
  // Engine bookkeeping the card has no plain words for: the pet keeps the step it was on.
  applyTaskEvent(r1, { type: "cloud.cost_updated", raw_type: "model_usage", seq: 5,
    summary: "ollama-local:qwen3:8b used 997 tokens." });
  res.usage = look(t0 + 60500);
  applyTaskEvent(r1, { type: "permission.required", seq: 6, summary: "long prose answer that is not the ask",
    approval: { approval_id: "a1", action_plain: "Send the email to Anna" } });
  res.ask = look(t0 + 61000);
  // The same ask again from the runtime, without the approval: the action stays named.
  applyTaskEvent(r1, { type: "permission.required", seq: 7,
    summary: "Awaiting approval for action response: I will send the email now." });
  res.askAgain = look(t0 + 61200);
  res.askLater = look(t0 + 600000);
  displayedChat = "c2";
  const r2 = runFor("c2");
  const sent = t0 + 700000;
  // The page's clock at both ends, like the card's total; the server's own end stamp is on
  // another clock (here 1.5 s early, as measured on the Mac) and does not set the duration.
  const realNow = Date.now;
  Date.now = () => sent;
  applyTaskEvent(r2, { type: "task.submitted", ts: new Date(sent).toISOString(), userText: "hi" });
  applyTaskEvent(r2, { type: "task.started", seq: 1 });
  Date.now = () => sent + 14000;
  applyTaskEvent(r2, { type: "task.completed", seq: 2, ts: new Date(sent + 12500).toISOString() });
  Date.now = realNow;
  res.done = look(sent + 15000);
  res.doneLater = look(sent + 15000 + 13000);
  displayedChat = "c1"; look(sent + 30000);
  displayedChat = "c2";
  res.doneReopened = look(sent + 31000);
  displayedChat = "c3";
  const r3 = runFor("c3");
  applyTaskEvent(r3, { type: "task.started", seq: 1 });
  applyTaskEvent(r3, { type: "task.failed", seq: 2 });
  res.failed = look(t0 + 800000);
  res.failedPhrase = VC.resolve(VC.view("c3")).phraseText;
  res.failedLater = look(t0 + 900000 - 1000);
} catch (e) { res.errors.push(String(e && e.stack || e)); }
out(res);
"""


def test_the_bubble_says_the_real_step_and_is_quiet_when_idle() -> None:
    out = _run(BUBBLE_DRIVER)
    assert out["idle"]["shown"] == "none", "a pet doing nothing says nothing"
    # Working: the same words the run card shows for the same event, never a line of its own.
    assert out["lookup"]["shown"] == "flex" and out["lookup"]["line"] == out["lookupCard"], out
    assert "weather" in out["lookup"]["line"].lower(), out["lookup"]
    assert out["read"]["line"] == out["readCard"] and out["read"]["line"] != out["lookup"]["line"], out
    assert out["readLater"]["line"] == out["read"]["line"], "lines change with the work, not on a timer"
    # Waiting on the user: what the approval is for, as the permission bar names it.
    assert out["usage"]["line"] == out["read"]["line"], "engine text never reaches the bubble"
    assert out["ask"]["line"] == "Send the email to Anna", out["ask"]
    assert out["askAgain"]["line"] == "Send the email to Anna", out["askAgain"]
    assert out["askLater"]["shown"] == "flex", "a waiting decision stays visible until it is answered"
    # Finished: done, with the turn's real duration on the page's clock (sent -> ended = 14 s).
    assert out["done"]["shown"] == "flex" and out["done"]["line"] == "Done · 14s", out["done"]
    assert out["doneLater"]["shown"] == "none", "after the moment passes the finished pet goes quiet"
    assert out["doneReopened"]["shown"] == "none", "opening a finished chat again does not replay its done line"
    assert out["failed"]["line"] == out["failedPhrase"] and out["failedPhrase"], "a failure says the plain phrase"
    assert out["failedLater"]["shown"] == "flex", "a failure stays up until something changes"


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
    done = [p for p in payloads if str(p.get("caption") or "").startswith("Done")]
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
