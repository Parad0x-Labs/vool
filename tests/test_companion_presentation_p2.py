"""P2 presentation laws: honest dwell queue, terminal truth, caption layout.

Continuation of the companion-event-truth line (P0 put agent-node frames on the channel;
this pack fixes what the caption does with them):

* A phrase deferred by the caption dwell is RETAINED (newest grounded deferred phrase) and
  painted at dwell expiry — but only while the run is still non-terminal.
* Any terminal event immediately cancels deferred work and paints terminal truth; a working
  phrase can never surface after completion merely to satisfy animation timing.
* Ordinary completion WITHOUT a verifier is its own truthful terminal — COMPLETED_UNREVIEWED,
  "Completed — not independently reviewed.", neutral tone — never UNKNOWN/"State unknown"
  and never PASS. Verified PASS/FLAGGED/FAILED/CANCELLED/approval keep precedence and wording.
* Caption geometry: column layout with a fixed canvas basis so canvas and caption stop
  squeezing each other; responsive and reduced-motion safe.
* No timer derives truth: with no events, time alone never rotates a phrase or fills the queue.

The fragment JS is executed for real under the shared node DOM stub (same harness family as
``tests/test_chat_companion_laws.py``); time is driven explicitly through ``VoolCompanion.paintNow``.
"""

from __future__ import annotations

import re

from core.companion_presentation_fragment import render_companion_fragment
from core.vool_chat_page import render_vool_chat_html
from tests.chat_page_js_harness import DOM, run_node

FRAGMENT = render_companion_fragment()
HTML = render_vool_chat_html()

UNREVIEWED_TEXT = "Completed — not independently reviewed."


def page_scripts() -> list[str]:
    """Every inline script of the served page, in document order (house first)."""
    found = re.findall(r"<script[^>]*>(.*?)</script>", HTML, re.DOTALL)
    assert len(found) >= 2, "expected house + companion scripts"
    # The i18n bootstrap (when the UI locale is wired) legally precedes everything: it must
    # define VOOLT before the page's own script runs. The HOUSE script is the first script
    # that is neither the bootstrap nor a fragment — it must precede the companion fragment.
    house = next((s for s in found if "logEl" in s), None)
    assert house is not None, "the house page script must be present"
    assert found.index(house) < next(
        i for i, s in enumerate(found) if "vnBoot" in s
    ), "house script must precede the companion fragment"
    return found


def run_presentation(driver: str) -> dict:
    """Boot the real page + fragment under node once per program and run one driver."""
    program = (
        DOM
        + "\nglobalThis.__winH = {};\n"
        + "window.addEventListener = (t, f) => { (__winH[t] = __winH[t] || []).push(f); };\n"
        + "\n;(function(){\n"
        + "\n;\n".join(page_scripts())
        + "\n"
        + driver
        + "\n})();\n"
    )
    result = run_node(program, timeout=120)
    assert not result.get("errors"), f"page booted with errors: {result['errors']}"
    return result




# --------------------------------------------------------------------- drivers

DRIVER_RETENTION = r"""
const out = {};
try {
  const VC = window.VoolCompanion;
  if (typeof displayedChat !== "undefined") displayedChat = "";
  const cap = () => VC.caption();
  const T = 99000000000000;
  VC.consume("c1", { type: "task.started", seq: 1 });
  VC.paintNow(T);
  out.startPhrase = VC.resolve(VC.view("c1")).phraseText;
  out.captionAtStart = cap();
  VC.consume("c1", { type: "tool.started", seq: 2, tool: "web_search" });
  VC.paintNow(T + 100);                       // inside the dwell: deferred
  out.midPhrase = VC.resolve(VC.view("c1")).phraseText;
  const q1 = VC.captionQueue();
  out.queued = q1 ? (q1.phrase || null) : "NO QUEUE API";
  out.captionDuringHold = cap();
  VC.consume("c1", { type: "tool.started", seq: 3, tool: "web_fetch" });
  VC.paintNow(T + 200);                       // newer deferred phrase replaces the retained one
  out.newestPhrase = VC.resolve(VC.view("c1")).phraseText;
  out.queuedNewest = VC.captionQueue().phrase;
  VC.paintNow(T + 950);                       // dwell expired, run still non-terminal
  out.captionAfterExpiry = cap();
} catch (e) { out.error = String((e && e.message) || e); }
globalThis.__out = out;
"""

DRIVER_TERMINAL = r"""
const out = {};
try {
  const VC = window.VoolCompanion;
  if (typeof displayedChat !== "undefined") displayedChat = "";
  const cap = () => VC.caption();
  const T = 99000000000000;
  VC.consume("c2", { type: "task.started", seq: 1 });
  VC.paintNow(T);
  VC.consume("c2", { type: "tool.started", seq: 2, tool: "web_search" });
  VC.paintNow(T + 100);                       // deferred by the dwell
  out.queuedBeforeTerminal = VC.captionQueue().phrase;
  VC.consume("c2", { type: "task.completed", seq: 3 });   // ordinary completion, no verifier
  VC.paintNow(T + 200);                       // terminal must paint IMMEDIATELY
  out.captionAtTerminal = cap();
  out.queueAfterTerminal = VC.captionQueue().phrase;
  VC.paintNow(T + 2500);                      // long past any hold
  out.captionLongAfter = cap();
} catch (e) { out.error = String((e && e.message) || e); }
globalThis.__out = out;
"""

DRIVER_UNREVIEWED = r"""
const out = {};
try {
  const VC = window.VoolCompanion;
  if (typeof displayedChat !== "undefined") displayedChat = "";
  VC.consume("c3", { type: "task.started", seq: 1 });
  VC.consume("c3", { type: "task.completed", seq: 2 });   // no verifier ever ran
  const r = VC.resolve(VC.view("c3"));
  out.state = r.state;
  out.tone = r.tone;
  out.phraseText = r.phraseText;
  out.completedToneDistinctFromSuccess = VC.tones.completed !== VC.tones.success;
  out.completedToneDistinctFromUnknown = VC.tones.completed !== VC.tones.unknown;
} catch (e) { out.error = String((e && e.message) || e); }
globalThis.__out = out;
"""

DRIVER_PRECEDENCE = r"""
const out = {};
try {
  const VC = window.VoolCompanion;
  if (typeof displayedChat !== "undefined") displayedChat = "";
  const feed = (id, events) => { for (const [i, ev] of events.entries()) VC.consume(id, Object.assign({ seq: i + 1 }, ev)); };
  feed("p1", [{ type: "task.started" }, { type: "verification.completed", review_state: "passed" }, { type: "task.completed" }]);
  const pass = VC.resolve(VC.view("p1"));
  out.pass = [pass.state, pass.phraseText];
  feed("p2", [{ type: "task.started" }, { type: "verification.completed", review_state: "flagged" }, { type: "task.completed" }]);
  const flagged = VC.resolve(VC.view("p2"));
  out.flagged = [flagged.state, flagged.phraseText];
  feed("p3", [{ type: "task.started" }, { type: "task.failed" }]);
  out.failed = VC.resolve(VC.view("p3")).phraseText;
  feed("p4", [{ type: "task.started" }, { type: "task.cancelled" }]);
  out.cancelled = VC.resolve(VC.view("p4")).phraseText;
  feed("p5", [{ type: "task.started" }, { type: "permission.required" }]);
  out.approval = VC.resolve(VC.view("p5")).phraseText;
  feed("p6", [{ type: "task.started" }, { type: "verification.completed", review_state: "blocked" }, { type: "task.completed" }]);
  out.verifierIncomplete = VC.resolve(VC.view("p6")).state;
} catch (e) { out.error = String((e && e.message) || e); }
globalThis.__out = out;
"""

DRIVER_DOM_SMOKE = r"""
const out = {};
try {
  const VC = window.VoolCompanion;
  if (typeof displayedChat !== "undefined") displayedChat = "";
  VC.consume("smoke", { type: "task.started", seq: 1 });
  VC.paintNow(99000000000000);
  out.spriteCount = VC.spriteCount();
  out.captionReadable = typeof VC.caption() === "string" && VC.caption().length > 0;
  out.captionHoldsGroundedPhrase = VC.caption() === VC.resolve(VC.view("smoke")).phraseText;
  out.spriteStatePainted = VC.paintState().state === "thinking";
} catch (e) { out.error = String((e && e.message) || e); }
globalThis.__out = out;
"""

DRIVER_NO_TIMER = r"""
const out = {};
try {
  const VC = window.VoolCompanion;
  if (typeof displayedChat !== "undefined") displayedChat = "";
  VC.consume("c9", { type: "task.started", seq: 1 });
  VC.consume("c9", { type: "tool.started", seq: 2, tool: "web_search" });
  const before = JSON.stringify({ v: VC.view("c9"), p: VC.resolve(VC.view("c9")), q: VC.captionQueue() });
  for (let i = 0; i < 60; i++) VC.paintNow(99000000000000 + i * 1000);
  const after = JSON.stringify({ v: VC.view("c9"), p: VC.resolve(VC.view("c9")), q: VC.captionQueue() });
  out.stable = before === after;
} catch (e) { out.error = String((e && e.message) || e); }
globalThis.__out = out;
"""


# --------------------------------------------------------------------- tests

def test_deferred_phrase_is_retained_and_paints_at_expiry() -> None:
    """Req 1+2: the dwell's deferral must RETAIN the newest grounded deferred phrase (the old
    code just re-scheduled and dropped it), and at expiry the newest one paints — grounded,
    reducer-produced, never invented."""
    out = run_presentation(DRIVER_RETENTION)
    assert not out.get("error"), f"driver failed: {out['error']}"
    assert out["queued"] != "NO QUEUE API", "the dwell must expose its retained deferred phrase"
    assert out["queued"] == out["midPhrase"], "deferred phrase must be retained while held"
    assert out["captionDuringHold"] == out["startPhrase"], "the hold must keep the current phrase readable"
    assert out["queuedNewest"] == out["newestPhrase"], "a newer deferred phrase replaces the retained one"
    assert out["captionAfterExpiry"] == out["newestPhrase"], (
        "at dwell expiry (run still non-terminal) the newest retained phrase paints"
    )


def test_terminal_cancels_queue_and_never_shows_stale_working_phrase() -> None:
    """Req 3+4: terminal during the dwell paints immediately, empties the deferred work, and a
    working phrase can never resurface after completion. Fast-turn activity stays on the
    task-card line (proven in P0); the caption does not owe impossible visibility."""
    out = run_presentation(DRIVER_TERMINAL)
    assert not out.get("error"), f"driver failed: {out['error']}"
    assert out["queuedBeforeTerminal"], "the burst phrase must be pending before the terminal lands"
    assert out["captionAtTerminal"] == UNREVIEWED_TEXT, "terminal truth paints immediately"
    assert out["queueAfterTerminal"] is None, "terminal truth cancels deferred work"
    assert out["captionLongAfter"] == out["captionAtTerminal"], (
        "no stale working phrase may appear after completion"
    )


def test_unreviewed_completion_is_completed_unreviewed_not_unknown() -> None:
    """Req 5: ordinary completion WITHOUT a verifier is its own truthful terminal — distinct
    name, explicit wording, neutral tone; it never borrows UNKNOWN's 'State unknown' and never
    implies PASS."""
    out = run_presentation(DRIVER_UNREVIEWED)
    assert not out.get("error"), f"driver failed: {out['error']}"
    assert out["state"] == "COMPLETED_UNREVIEWED", f"got {out['state']}"
    assert out["phraseText"] == UNREVIEWED_TEXT
    assert out["tone"] == "completed"
    assert out["completedToneDistinctFromSuccess"], "neutral completion must never borrow PASS green"
    assert out["completedToneDistinctFromUnknown"], "unreviewed completion is not UNKNOWN either"


def test_terminal_precedence_and_wording_retained() -> None:
    """Req 6: verified PASS, FLAGGED, FAILED, CANCELLED, approval and verifier-incomplete keep
    their precedence and existing wording."""
    out = run_presentation(DRIVER_PRECEDENCE)
    assert not out.get("error"), f"driver failed: {out['error']}"
    assert out["pass"] == ["PASS", "Complete."], out["pass"]
    assert out["flagged"] == ["FLAGGED", "Review flagged the result."], out["flagged"]
    assert out["failed"] == "Stopped safely."
    assert out["cancelled"] == "Cancelled."
    assert out["approval"] == "Waiting for your approval…"
    assert out["verifierIncomplete"] == "VERIFIER_INCOMPLETE"


def test_caption_geometry_column_layout_not_squeeze() -> None:
    """Req 7: the sprite lays canvas and caption out as a column with a fixed canvas basis;
    the caption is bounded by the sprite box (no fixed 200px squeeze against a 100%-width
    canvas) and stays reduced-motion safe."""
    css = FRAGMENT
    assert "#companionLayer .vool-ninja {" in css
    ninja_rule = css[css.index("#companionLayer .vool-ninja {"):]
    ninja_rule = ninja_rule[: ninja_rule.index("}")]
    assert "flex-direction:column" in ninja_rule, "sprite must lay canvas+caption out as a column"
    canvas_rule = css[css.index("#companionLayer .vool-ninja canvas {"):]
    canvas_rule = canvas_rule[: canvas_rule.index("}")]
    assert "flex:" in canvas_rule and "0 0 auto" in canvas_rule, "canvas needs a fixed basis so it cannot squeeze the caption"
    assert "width:100%" not in canvas_rule, "a full-width canvas is what squeezed the caption row"
    caption_rule = css[css.index(".vn-caption{"):]
    caption_rule = caption_rule[: caption_rule.index("}")]
    assert "max-width:100%" in caption_rule, "caption is bounded by the sprite box, not a hard 200px"
    assert "max-width:200px" not in caption_rule, "the old fixed 200px squeeze must be gone"
    assert ".vn-caption:empty" in css, "empty captions still hide"
    assert "body.vn-motion-reduced" in css and "@media (prefers-reduced-motion: reduce)" in css, (
        "reduced-motion rules must survive the geometry change"
    )


def test_caption_layout_dom_smoke() -> None:
    """Executed smoke: with the new geometry the layer still boots, the real caption carries
    the grounded phrase, and the sprite structure keeps canvas→caption→dot order."""
    out = run_presentation(DRIVER_DOM_SMOKE)
    assert not out.get("error"), f"driver failed: {out['error']}"
    assert out["spriteCount"] == 1, "exactly one sprite"
    assert out["captionReadable"], "the real caption element must carry text"
    assert out["captionHoldsGroundedPhrase"], "the caption must show the reducer's phrase"
    assert out["spriteStatePainted"], "the sprite paint path consumes the reducer state"
    # Structure pin for the column order the CSS lays out (canvas row above caption row).
    boot = FRAGMENT[FRAGMENT.index("function vnBoot"):]
    boot = boot[: boot.index("window.VoolCompanionBoot")]
    canvas_at = boot.index("vnSprite.appendChild(vnCanvas);")
    caption_at = boot.index("vnSprite.appendChild(vnCaption);")
    assert canvas_at < caption_at, "canvas and caption must mount in that order"
    # No status dot on the sprite (product/desktop-usability-20260917): the 9px tone dot sat at
    # the CORNER of the 112px drag box -- roughly a centimetre from the character's drawn feet --
    # and was reported as a stray floating mark. Tone is already carried by the caption word,
    # which the pack's colour law requires anyway. The RESTORE chip keeps its own inline dot:
    # that one is part of a real control.
    assert "vnSprite.appendChild(vnDot);" not in boot, "the sprite must not mount a corner dot"
    assert "vnDot" not in boot, "no dot element is created for the sprite at all"
    assert boot.index("vnRestore.innerHTML") > 0, "the restore control still mounts"


def test_timers_only_present_reducer_truth() -> None:
    """Req 8: with no events, time alone must not rotate the phrase, move the reducer or fill
    the deferred queue."""
    out = run_presentation(DRIVER_NO_TIMER)
    assert not out.get("error"), f"driver failed: {out['error']}"
    assert out["stable"], "time alone must never advance status or queue work"
    # Source pin: the reducer/resolver path derives nothing from the clock or chance.
    from core.companion_presentation_fragment import _VN_LIBRARY_JS

    for forbidden in ("Date.now(", "Math.random("):
        assert forbidden not in _VN_LIBRARY_JS, f"{forbidden} is banned from the selection path"


def test_served_page_still_mounts_the_fragment_once() -> None:
    """The geometry/dwell changes ride the same single mount: one style, one script, one boot."""
    assert HTML.count("id=\"companionLayer\"") == 1 or FRAGMENT in HTML
    scripts = re.findall(r"<script[^>]*>(.*?)</script>", HTML, re.DOTALL)
    assert sum("vnBoot" in s for s in scripts) == 1, "exactly one companion engine script"
