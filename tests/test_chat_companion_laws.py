"""Laws for the typed Vooling status + VOOL Ninja companion layer.

Obligations come from the demo-companion-vooling-ux pass-001 writer contract (doc 07 §7):
vocabulary purity, classifier precedence, replay determinism, override priority, stale-seq
and ghost-worker rejection, measurable-only progress, the FINISH gate (task.finalizing),
ended-run mutability, drag presentation purity, clamp law, critical-rect avoidance,
reduced-motion truth, persistence clamping and the sheet manifest.

The companion JS is exercised for real: both page scripts (house + fragment) are booted
under the shared node DOM stub and the laws are driven through ``window.VoolCompanion``,
the exact object the house ``applyTaskEvent`` consumer calls. Server-side truths (the
``task_finalizing`` allowlist row and the S1 pre-commit emission) are unit-driven here too,
so the FINISH gate is tested end to end at the seam level.
"""

from __future__ import annotations

import functools
import json
import re

import pytest

from core.companion_presentation_fragment import render_companion_fragment
from core.vool_chat_page import render_vool_chat_html
from tests.chat_page_js_harness import DOM, run_node

HTML = render_vool_chat_html()
FRAGMENT = render_companion_fragment()

# The three owned anchor additions. Each must be present exactly once -- a doubled
# dispatch would double-consume events, a moved one would silently break the lane.
ANCHOR_CONSUME = "if (typeof window !== 'undefined' && window.VoolCompanion) window.VoolCompanion.consume(run.chatId, ev);"
ANCHOR_BOOT = "if (window.VoolCompanionBoot) window.VoolCompanionBoot();"
SHEET_STATES = ["idle", "starting", "thinking", "tool", "waiting", "approval",
                "retry", "success", "failure", "cancelled", "unknown"]


def page_scripts() -> list[str]:
    """Every inline script of the served page, in document order.

    Since the U1 fragment mount (2026-08-28) the page carries the house script plus ONE script
    per appended fragment, so the count is no longer pinned at two — identity is. The house
    script is the first (it owns `logEl`); the companion script is the one that boots the
    sprite engine. Every script must still be identifiable — an anonymous stray script would
    mean an unreviewed injection point.
    """
    found = re.findall(r"<script[^>]*>(.*?)</script>", HTML, re.DOTALL)
    assert len(found) >= 2, f"expected at least house + companion scripts, found {len(found)}"
    # The i18n bootstrap (UI-locale wiring) legally precedes everything: it defines VOOLT
    # before the page's own script runs. The HOUSE script remains the first non-bootstrap one.
    house = next((s for s in found if "logEl" in s), None)
    assert house is not None, "the house page script must be present"
    assert found.index(house) < next(i for i, s in enumerate(found) if "vnBoot" in s), \
        "house script must precede the companion fragment"
    assert any("vnBoot" in script for script in found), "companion engine script missing"
    for script in found[1:]:
        assert re.search(r"window\.Vool[A-Za-z]+", script), (
            "every appended fragment must expose exactly a window.Vool* namespace"
        )
    return found


def test_companion_anchors_present_exactly_once():
    assert HTML.count(ANCHOR_CONSUME) == 1, "the typed-event dispatch anchor must be unique"
    assert HTML.count(ANCHOR_BOOT) == 1, "the companion boot anchor must be unique"
    assert HTML.count('id="companionLayer"') == 1 or FRAGMENT in HTML
    # The fragment must be concatenated BEFORE the body closes (valid document).
    assert HTML.index("companionLayer") < HTML.index("</body>")


def test_task_finalizing_allowlist_row():
    from core.task_event_model import build_task_event

    ev = build_task_event({"event_type": "task_finalizing", "seq": 41})
    assert ev is not None, "task_finalizing must map through the allowlist"
    assert ev["type"] == "task.finalizing"
    assert ev["stage"] is None, "finalizing is a delivery window, not an execution stage"
    assert ev["status"] == "running"
    # And nothing else may have leaked into the map: an unknown type stays dropped.
    assert build_task_event({"event_type": "task_finalizing_fake"}) is None


def test_s1_emission_only_before_a_real_commit_frame():
    from core.web.api.service import _inject_task_finalizing_event

    content = b'{"message":{"content":"hi"}}\n'
    commit = b'{"done":true,"vool_response_commit":{"type":"response.commit"}}\n'
    stream = list(_inject_task_finalizing_event(iter([content, commit]), True))
    assert len(stream) == 3
    s1 = json.loads(stream[1])
    assert s1["vool_event"]["type"] == "task.finalizing"
    assert json.loads(stream[2])["done"] is True
    # Gate off: pure passthrough (the /v1/ shape).
    assert list(_inject_task_finalizing_event(iter([content, commit]), False)) == [content, commit]
    # Failure paths never yield a commit frame, so they can never claim finalization.
    assert list(_inject_task_finalizing_event(iter([content]), True)) == [content]
    # An answer that merely QUOTES the key name must not fire the event a frame early.
    tricky = b'{"message":{"content":"vool_response_commit"}}\n'
    out = list(_inject_task_finalizing_event(iter([tricky, commit]), True))
    assert json.loads(out[1])["vool_event"]["type"] == "task.finalizing"
    assert out[0] == tricky


# --------------------------------------------------------------------- JS laws

DRIVER = r"""
// -- harness glue -----------------------------------------------------------
// Capture window listeners (the stub's addEventListener is a no-op) so the drag
// pipeline can be driven for real.
globalThis.__winH = {};
window.addEventListener = (t, f) => { (__winH[t] = __winH[t] || []).push(f); };
document.addEventListener = (t, f) => { (__winH['doc:' + t] = __winH['doc:' + t] || []).push(f); };
"""

LAWS = r"""
const laws = {};
function law(name, fn) {
  try { fn(); laws[name] = "PASS"; }
  catch (e) { laws[name] = "FAIL: " + ((e && e.message) || e); }
}
function assert(cond, msg) { if (!cond) throw new Error(msg || "assertion failed"); }
function eq(a, b, msg) { if (a !== b) throw new Error((msg || "eq") + ": " + JSON.stringify(a) + " !== " + JSON.stringify(b)); }

const VC = window.VoolCompanion;
assert(VC, "companion API must exist after boot");
// House boot may auto-select a session under the stub; the laws pin the displayed lane
// empty so the companion's most-recently-fed lane is the one that paints.
if (typeof displayedChat !== "undefined") displayedChat = "";

// -- T1 vocabulary purity ----------------------------------------------------
law("T1.vocabulary_purity", () => {
  const lib = VC.library;
  const cats = Object.keys(lib);
  eq(cats.length, 15, "15 rotating categories");
  const ids = new Set(), texts = new Set();
  for (const cat of cats) {
    assert(lib[cat].length >= 3, cat + " needs >=3 rotating entries");
    for (const [id, text] of lib[cat]) {
      assert(!ids.has(id), "duplicate id " + id);
      assert(!texts.has(text), "duplicate text " + text);
      ids.add(id); texts.add(text);
      assert(text.endsWith("…"), "rotating text must end with ellipsis: " + text);
      for (const ch of text) assert(ch.codePointAt(0) <= 0x2500, "non-terminal codepoint in " + text);
      assert(!/[\u{1F000}-\u{1FAFF}\u{2600}-\u{27BF}]/u.test(text), "no emoji in library");
    }
  }
  eq(ids.size, 117, "117 rotating phrases");
});

// -- T2 stage/category coverage matrix ---------------------------------------
law("T2.stage_coverage_matrix", () => {
  const need = {
    Understanding: "UNDERSTANDING", Planning: "PLANNING", Searching: "WEB_RESEARCH",
    Reading: "READING_FILES", Editing: "APPLYING_CHANGES", Running: "RUNNING_TOOLS",
    Testing: "TESTING_CI", Verifying: "VERIFYING", Repairing: "RETRYING",
    Queued: "WAITING_QUEUED", Synthesis: "SYNTHESIZING", Finalizing: "FINALIZING",
    Routing: "ROUTING_PROVENANCE", Streaming: "MODEL_GENERATION",
  };
  for (const [stage, cat] of Object.entries(need)) {
    assert(VC.library[cat] && VC.library[cat].length, stage + " has no reachable category " + cat);
  }
  for (const key of ["APPROVAL_REQUIRED", "FAILED", "CANCELLED", "UNKNOWN", "FLAGGED", "VERIFIER_INCOMPLETE", "PASS", "COMPLETED_UNREVIEWED"]) {
    assert(VC.resolve({}).phraseText !== undefined, "fixed copy present");
  }
});

// -- T3 classifier precedence -------------------------------------------------
law("T3.classifier_precedence", () => {
  eq(VC.classify({ type: "task.finalizing", stage: "Verifying" }), "FINALIZING", "S1 beats every stage");
  eq(VC.classify({ type: "task.stage_changed", raw_type: "tool_synthesizing", stage: "Verifying" }), "SYNTHESIZING", "synthesis beats its stage");
  eq(VC.classify({ type: "task.stage_changed", stage: "Repairing", diagnostics: { retryable: true } }), "RETRYING", "retry needs real evidence");
  eq(VC.classify({ type: "task.stage_changed", stage: "Repairing" }), null, "stage alone never fires RETRYING");
  eq(VC.classify({ type: "task.stage_changed", raw_type: "tool_repeat_blocked", stage: "Repairing" }), "RETRYING", "recover-lane shape is retry evidence");
  eq(VC.classify({ type: "permission.required" }), "APPROVAL_REQUIRED");
  eq(VC.classify({ type: "verification.started" }), "VERIFYING");
  eq(VC.classify({ type: "task.started" }), "UNDERSTANDING");
  eq(VC.classify({ type: "model.call_started" }), "MODEL_GENERATION", "model calls are generation, not routing");
  eq(VC.classify({ type: "model.changed" }), "ROUTING_PROVENANCE");
  // Needle collisions both ways: registry order is law (doc 03 §3 rows 4-9).
  eq(VC.classify({ type: "tool.started", tool: "read_git_status" }), "READING_FILES", "read beats git");
  eq(VC.classify({ type: "tool.started", tool: "git_show_patch" }), "APPLYING_CHANGES", "apply beats git");
  eq(VC.classify({ type: "tool.started", tool: "check_git_status_workflow" }), "GIT_INSPECTION", "git beats test");
  eq(VC.classify({ type: "tool.started", tool: "run_tests" }), "TESTING_CI", "test beats run");
  eq(VC.classify({ type: "tool.started", tool: "web_search_fast" }), "WEB_RESEARCH");
  eq(VC.classify({ type: "tool.started", tool: "unknown_probe_xyz" }), "RUNNING_TOOLS", "default family");
});

// -- T4 replay determinism -----------------------------------------------------
function runTape(chatId, tape) {
  const phrases = [];
  for (const ev of tape) { VC.consume(chatId, ev); phrases.push(VC.resolve(VC.view(chatId)).phraseId); }
  return phrases;
}
const DETERMINISTIC_TAPE = [
  { type: "task.started", seq: 1 },
  { type: "tool.started", seq: 2, tool: "git_status" },
  { type: "tool.completed", seq: 3, tool: "git_status" },
  { type: "task.stage_changed", raw_type: "tool_synthesizing", stage: "Verifying", seq: 4 },
  { type: "verification.started", seq: 5 },
  { type: "verification.completed", seq: 6, review_state: "passed" },
  { type: "task.finalizing" },
  { type: "task.completed", seq: 7 },
];
law("T4.replay_determinism", () => {
  const a = runTape("det-a", DETERMINISTIC_TAPE);
  const b = runTape("det-b", DETERMINISTIC_TAPE);
  eq(JSON.stringify(a), JSON.stringify(b), "same tape must yield the same phrase sequence");
});

// -- T5 override priority -------------------------------------------------------
law("T5.override_priority_stack", () => {
  // FAILED beats a late DONE: terminal truth is immutable.
  VC.consume("p1", { type: "task.failed", seq: 1 });
  eq(VC.resolve(VC.view("p1")).state, "FAILED");
  VC.consume("p1", { type: "task.completed", seq: 2, review_state: "passed" });
  eq(VC.resolve(VC.view("p1")).state, "FAILED", "late DONE must not repaint FAILED");
  // Ordinary completion WITHOUT a verifier is COMPLETED_UNREVIEWED: explicit and neutral —
  // never PASS, and never the old "State unknown" that read as a soft failure (P2, 2026-08-31).
  VC.consume("p2", { type: "task.started", seq: 1 });
  VC.consume("p2", { type: "task.completed", seq: 2 });
  const unk = VC.resolve(VC.view("p2"));
  eq(unk.state, "COMPLETED_UNREVIEWED");
  eq(unk.tone, "completed");
  eq(unk.phraseText, "Completed — not independently reviewed.");
  assert(VC.tones.completed !== VC.tones.success, "unreviewed completion must not borrow PASS green");
  assert(VC.tones.completed !== VC.tones.unknown, "unreviewed completion is not UNKNOWN either");
  assert(unk.phraseText.indexOf("green") === -1);
  // A passed verdict completes as PASS.
  VC.consume("p3", { type: "task.started", seq: 1 });
  VC.consume("p3", { type: "verification.completed", seq: 2, review_state: "passed" });
  VC.consume("p3", { type: "task.completed", seq: 3 });
  eq(VC.resolve(VC.view("p3")).state, "PASS");
  // APPROVAL locks out categories until resolved.
  VC.consume("p4", { type: "task.started", seq: 1 });
  VC.consume("p4", { type: "tool.started", seq: 2, tool: "shell_run" });
  VC.consume("p4", { type: "permission.required", seq: 3 });
  eq(VC.resolve(VC.view("p4")).state, "APPROVAL_REQUIRED");
  VC.consume("p4", { type: "tool.started", seq: 4, tool: "web_search" });
  eq(VC.resolve(VC.view("p4")).state, "APPROVAL_REQUIRED", "approval persists");
  VC.consume("p4", { type: "permission.resolved", seq: 5 });
  eq(VC.resolve(VC.view("p4")).category, "WEB_RESEARCH", "resolved unlock returns to category");
});

// -- T6 stale seq + ghost workers ------------------------------------------------
law("T6.stale_seq_rejected_no_ghosts", () => {
  VC.consume("g1", { type: "task.started", seq: 5 });
  const before = VC.resolve(VC.view("g1")).phraseId;
  VC.consume("g1", { type: "task.stage_changed", stage: "Planning", seq: 3 });
  eq(VC.view("g1").seq, 5, "stale seq must not move the high-water mark");
  eq(VC.view("g1").category, "UNDERSTANDING", "stale event must not change the category");
  // Synthetic agent events: the channel has no AGENT_* producer, and the companion
  // must not spawn anything for one even if hand-fed.
  const presBefore = JSON.stringify(VC.resolve(VC.view("g1")));
  VC.consume("g1", { type: "agent.started", raw_type: "agent_started", agent_id: "ghost-1" });
  VC.consume("g1", { type: "agent.finished", raw_type: "agent_finished", agent_id: "ghost-1" });
  eq(JSON.stringify(VC.resolve(VC.view("g1"))), presBefore, "AGENT_* events must be inert");
  eq(VC.spriteCount(), 1, "exactly one companion sprite while one real worker exists");
});

// -- T7 progress only from measurable ---------------------------------------------
function progressValue(html) {
  const m = html.match(/measurable<\/b><span>([^<]*)</);
  assert(m, "popover must render a measurable row");
  return m[1];
}
law("T7.progress_only_from_measurable", () => {
  VC.consume("m1", { type: "task.started", seq: 1 });
  eq(progressValue(VC.popoverHtml("m1")), "unknown", "absent numbers must render unknown");
  VC.consume("m1", { type: "tool.completed", seq: 2, measurable: { current: 2, total: 4 } });
  eq(progressValue(VC.popoverHtml("m1")), "50%", "measurable current/total is the only percent source");
});

// -- T8 FINISH gate ------------------------------------------------------------------
law("T8.finish_unreachable_without_task_finalizing", () => {
  // Tape WITHOUT producer S1: no FINALIZING phrase may ever appear.
  const noS1 = DETERMINISTIC_TAPE.filter((e) => e.type !== "task.finalizing");
  const phrases = runTape("f1", noS1);
  assert(phrases.indexOf("assembling_answer") === -1, "FINISH leaked without task.finalizing");
  assert(phrases.indexOf("assembling_result") === -1 && phrases.indexOf("finalizing") === -1,
    "no FINALIZING-family phrase without the real event");
  // The gate is the STATE, not one phrase: no evidence short of task.finalizing may
  // resolve the presentation into FINISH/FINALIZING.
  VC.consume("f1b", { type: "task.started", seq: 1 });
  VC.consume("f1b", { type: "task.stage_changed", raw_type: "tool_synthesizing", stage: "Verifying", seq: 2 });
  // Assert AT the synthesizing event: a mis-classifier that maps synthesis to
  // FINALIZING must be caught in the exact moment it lies, before any later event.
  assert(VC.resolve(VC.view("f1b")).state !== "FINISH", "state must never be FINISH without S1");
  assert(VC.resolve(VC.view("f1b")).category !== "FINALIZING", "category must never be FINALIZING without S1");
  assert(VC.resolve(VC.view("f1b")).tone !== "final", "final tone is unreachable without S1");
  VC.consume("f1b", { type: "verification.started", seq: 3 });
  // With S1, FINISH appears exactly at that event and is cleared by the terminal.
  let sawFinish = false;
  VC.consume("f2", { type: "task.started", seq: 1 });
  VC.consume("f2", { type: "tool.started", seq: 2, tool: "git_status" });
  VC.consume("f2", { type: "task.finalizing" });
  let pres = VC.resolve(VC.view("f2"));
  eq(pres.state, "FINISH", "task.finalizing is the only door to FINISH");
  eq(pres.tone, "final", "FINISH tone is the finalization teal");
  eq(VC.view("f2").category, "FINALIZING");
  VC.consume("f2", { type: "verification.completed", seq: 6, review_state: "passed" });
  VC.consume("f2", { type: "task.completed", seq: 7 });
  pres = VC.resolve(VC.view("f2"));
  eq(pres.stage, "TERMINAL", "commit/terminal clears FINISH");
  eq(pres.state, "PASS");
});

// -- T9 ended runs accept only verification mutations ---------------------------------
law("T9.ended_run_only_verification_mutates", () => {
  VC.consume("e1", { type: "task.started", seq: 1 });
  VC.consume("e1", { type: "verification.completed", seq: 2, review_state: "passed" });
  VC.consume("e1", { type: "task.completed", seq: 3 });
  const before = JSON.stringify(VC.resolve(VC.view("e1")));
  VC.consume("e1", { type: "tool.started", seq: 4, tool: "shell_run" });
  VC.consume("e1", { type: "task.stage_changed", stage: "Running", seq: 5 });
  eq(JSON.stringify(VC.resolve(VC.view("e1"))), before, "post-terminal activity must not move the view");
});

// -- T10 drag is presentation only ------------------------------------------------------
function viewsSnapshot() {
  const out = {};
  for (const [k, v] of Object.entries({})) {} // views map is internal; snapshot via API below
  return out;
}
law("T10.drag_presentation_only", () => {
  VC.consume("d1", { type: "task.started", seq: 1 });
  VC.consume("d1", { type: "tool.started", seq: 2, tool: "web_search" });
  const layer = document.body.children.find((c) => c.id === "companionLayer");
  assert(layer, "companion layer must be booted");
  const sprite = layer.children.find((c) => c.attrs && c.attrs.role === "button");
  assert(sprite, "sprite element must exist");
  const before = JSON.stringify({ v: VC.view("d1"), p: VC.resolve(VC.view("d1")) });
  const dockedBefore = VC.pos().mode;
  sprite.__on.pointerdown({ clientX: 300, clientY: 300, pointerId: 3 });
  (__winH.pointermove || []).forEach((f) => f({ clientX: 420, clientY: 180 }));
  const liveLeft = sprite.style.left, liveTop = sprite.style.top;
  VC.paintNow(99000000000000);
  eq(sprite.style.left, liveLeft, "paint loop must not snap an active drag back to its dock");
  eq(sprite.style.top, liveTop, "paint loop must preserve the live pointer position");
  (__winH.pointerup || []).forEach((f) => f({ clientX: 420, clientY: 180 }));
  const after = JSON.stringify({ v: VC.view("d1"), p: VC.resolve(VC.view("d1")) });
  eq(before, after, "dragging must produce ZERO diffs in run/event/store state");
  eq(VC.pos().mode, "free", "drag detaches the sprite from the dock");
  assert(dockedBefore === "docked", "the boot default is docked");
  assert(VC.pos().x > 0 && VC.pos().y > 0, "placement moved with the drag");
  const stored = JSON.parse(localStorage.getItem("vool_ninja_pos_v1"));
  eq(stored.mode, "free", "position persists on settle");
});

// -- T11 clamp law -----------------------------------------------------------------------
law("T11.clamp_law_offscreen_impossible", () => {
  const vw = window.innerWidth, vh = window.innerHeight;
  for (const x of [-1e9, -500, 0, 1, vw / 2, vw, vw + 500, 1e9]) {
    for (const y of [-1e9, -500, 0, 1, vh / 2, vh, vh + 500, 1e9]) {
      const c = VC.clamp(x, y);
      assert(c[0] >= 24 && c[0] <= vw - 112 - 24, "x out of legal range for " + x + " -> " + c[0]);
      assert(c[1] >= 24 && c[1] <= vh - 112 - 24, "y out of legal range for " + y + " -> " + c[1]);
    }
  }
  // A stored off-screen blob is clamped on read: nothing off-screen can ever restore.
  localStorage.setItem("vool_ninja_pos_v1", JSON.stringify({ v: 1, mode: "free", x: 99999, y: -99999, hidden: false, personality: "prism", motion: "system", override: false }));
  VC.loadPos();
  const p = VC.pos();
  assert(p.x >= 24 && p.x <= vw - 136, "stored x must clamp on read");
  assert(p.y >= 24 && p.y <= vh - 136, "stored y must clamp on read");
});

// -- T12 one drag, one final position (product/desktop-usability-20260917) -----------------
// The old contract slid the FIRST drop off a critical control and honoured only a second
// attempt at the same spot -- the live-reported "one drag often fails to hold the pet; a
// second drag is required". The drop now lands where it was released (clamped to the window
// only), on the first attempt and every attempt.
law("T12.critical_rect_drop_is_final", () => {
  const first = VC.resolveDrop(30, 30);   // #input stub rect 0,0,100,100 -- covered, not slid
  eq(first[0], 30, "first drop keeps its x exactly (clamped only)");
  eq(first[1], 30, "first drop keeps its y exactly (clamped only)");
  const second = VC.resolveDrop(30, 30);
  eq(second[0], 30, "the same release point is stable: no second-attempt mechanic");
  eq(second[1], 30, "the same release point is stable: no second-attempt mechanic");
  const free = VC.resolveDrop(400, 300);  // away from every stub rect
  eq(free[0], 400, "an uncovered drop keeps its x");
  eq(free[1], 300, "an uncovered drop keeps its y");
});

// -- T13 reduced motion keeps state truth -----------------------------------------------------
law("T13.reduced_motion_preserves_state_truth", () => {
  VC.consume("r1", { type: "task.started", seq: 1 });
  VC.consume("r1", { type: "tool.started", seq: 2, tool: "web_search" });
  VC.pos().motion = "reduced";
  VC.paintNow();
  const f1 = VC.paintState().frame;
  VC.paintNow(99000000009000);
  VC.paintNow(99000000090000);
  eq(VC.paintState().frame, f1, "reduced motion must hold the static frame");
  const layer = document.body.children.find((c) => c.id === "companionLayer");
  const sr = layer.children[0];
  assert(sr.textContent.indexOf("Companion") !== -1, "aria mirror must announce the state");
  assert(VC.pos().motion === "reduced");
  VC.pos().motion = "system";
});

// -- T14 persistence restores a legal placement ------------------------------------------------
law("T14.persistence_reload_restores_legal_placement", () => {
  const vw = window.innerWidth, vh = window.innerHeight;
  localStorage.setItem("vool_ninja_pos_v1", JSON.stringify({ v: 2, mode: "free", x: 200, y: 260, hidden: false, personality: "rascal", pack: "ninja", motion: "system", override: false }));
  VC.loadPos();
  eq(VC.pos().mode, "free");
  eq(VC.pos().x, 200, "legal x restores exactly");
  eq(VC.pos().y, 260, "legal y restores exactly");
  eq(VC.pos().personality, "rascal", "approved character choice persists");
  eq(VC.pos().pack, "ninja", "pixel-world cosmetic pack persists");
  // Reload across a viewport size change clamps rather than leaks off-screen.
  window.innerWidth = 320; window.innerHeight = 480;
  VC.loadPos();
  assert(VC.pos().x <= 320 - 136, "x must clamp to the new viewport");
  window.innerWidth = vw; window.innerHeight = vh;
  VC.loadPos();
});

// -- T15 live-update coupling (M1 inverse) -----------------------------------------------------
law("T15.consume_drives_presentation", () => {
  VC.consume("t15", { type: "task.started", seq: 1 });
  const a = VC.resolve(VC.view("t15")).category;
  VC.consume("t15", { type: "tool.started", seq: 2, tool: "git_diff" });
  const b = VC.resolve(VC.view("t15")).category;
  assert(a !== b, "a consumed event must move the presentation");
  VC.paintNow();
  assert(VC.paintState().state === "tool", "paint consumes the reducer state");
});

// -- M2 timer-driven advancement is impossible ---------------------------------------------------
law("M2.no_timer_advancement", () => {
  VC.consume("m2", { type: "task.started", seq: 1 });
  VC.consume("m2", { type: "tool.started", seq: 2, tool: "git_status" });
  const snap = JSON.stringify({ v: VC.view("m2"), p: VC.resolve(VC.view("m2")) });
  for (let i = 0; i < 50; i++) VC.paintNow(99000000000000 + i * 1000);
  eq(JSON.stringify({ v: VC.view("m2"), p: VC.resolve(VC.view("m2")) }), snap,
    "time alone can never advance status");
});

// -- sheet manifest (doc 10 §1/§4) ------------------------------------------------------------------
law("SHEET.manifest", () => {
  const sheets = VC.sheets;
  eq(Object.keys(sheets).sort().join(","), "ember,prism,veil", "the TOP-3 family ships");
  for (const [name, concept] of Object.entries(sheets)) {
    for (const st of ["idle", "starting", "thinking", "tool", "waiting", "approval", "retry", "success", "failure", "cancelled", "unknown"]) {
      assert(concept.states[st], name + " missing state " + st);
    }
    let total = 0;
    for (const [st, frames] of Object.entries(concept.states)) {
      total += frames.length;
      for (const f of frames) {
        const rows = f.split("|");
        eq(rows.length, 24, name + "/" + st + " needs 24 rows");
        for (const row of rows) {
          eq(row.length, 24, name + "/" + st + " rows are 24 wide");
          for (const ch of row) {
            assert(ch === "." || concept.palette[ch] !== undefined, name + "/" + st + " unknown pixel '" + ch + "'");
          }
        }
      }
    }
    assert(total <= 70, name + " exceeds the 70-frame budget: " + total);
    for (const [ch, hex] of Object.entries(concept.palette)) {
      assert(/^#[0-9a-f]{6}$/.test(hex), name + " palette entry " + ch + " malformed");
    }
  }
});

law("WORLD.authority", () => {
  // The approved family is the three voxel heroes PLUS the recovered hand-drawn pixel trio
  // (Prism Shifter / Veil Shadowstep / Ember Signal) — the 126-frame sheets shipped as dead
  // payload from b7d045fe until the 2026-08-28 recovery re-bound their rasteriser.
  eq(Object.keys(VC.characters).sort().join(","), "ember,prime,prism,rascal,spark,veil", "approved Character Lab family ships");
  eq(VC.characters.prism.renderer, "sheet", "PRISM renders from its hand-drawn sheet");
  eq(VC.characters.veil.renderer, "sheet", "VEIL renders from its hand-drawn sheet");
  eq(VC.characters.ember.renderer, "sheet", "EMBER renders from its hand-drawn sheet");
  const layer = document.body.children.find((c) => c.id === "companionLayer");
  const sprite = layer.children.find((c) => c.attrs && c.attrs.role === "button");
  const canvas = sprite.children[0];
  eq(canvas.width, 48, "approved world canvas is 48 pixels wide");
  eq(canvas.height, 48, "approved world canvas is 48 pixels high");
  eq(VC.pos().v, 2, "placement schema records the BigHead-family generation");
});

law("WORLD.character_lab_selects_one_cosmetic_identity", () => {
  VC.chooseCharacter("prime");
  eq(VC.pos().personality, "prime", "Character Lab selects PRIME");
  VC.choosePack("cyber");
  eq(VC.pos().pack, "cyber", "Character Lab selects the cosmetic Cyber palette");
  VC.chooseCharacter("not-real"); VC.choosePack("not-real");
  eq(VC.pos().personality, "prime", "unknown characters are rejected");
  eq(VC.pos().pack, "cyber", "unknown packs are rejected");
  VC.openCharacterLab();
  const layer = document.body.children.find((c) => c.id === "companionLayer");
  const lab = layer.children[layer.children.length - 1];
  eq(lab.style.display, "flex", "Character Lab opens as a real chooser");
  const modal = lab.children[0], candidateGrid = modal.children[1], packGrid = modal.children[3];
  eq(candidateGrid.children.length, 6, "SPARK/RASCAL/PRIME + PRISM/VEIL/EMBER are selectable");
  eq(packGrid.children.length, 4, "the four authoritative cosmetic packs are selectable");
  VC.chooseCharacter("spark"); VC.choosePack("default");
});
"""

PROGRAM_TAIL = "\nout({ laws: laws });\n"


@functools.lru_cache(maxsize=1)
def js_law_results() -> dict:
    """Boot the real page + fragment under node once and run every JS law."""
    window_patch = (
        "\nglobalThis.__winH = {};\n"
        "window.addEventListener = (t, f) => { (__winH[t] = __winH[t] || []).push(f); };\n"
    )
    # The laws run INSIDE the page script scope so the driver sees the same
    # `displayedChat` binding the companion paint path reads.
    program = (DOM + window_patch + "\n;(function(){\n" + "\n;\n".join(page_scripts())
               + "\n" + DRIVER + LAWS + PROGRAM_TAIL + "\n})();\n")
    result = run_node(program, timeout=120)
    assert not result.get("errors"), f"page booted with errors: {result['errors']}"
    return result["laws"]


@pytest.mark.parametrize("law_name", [
    "T1.vocabulary_purity",
    "T2.stage_coverage_matrix",
    "T3.classifier_precedence",
    "T4.replay_determinism",
    "T5.override_priority_stack",
    "T6.stale_seq_rejected_no_ghosts",
    "T7.progress_only_from_measurable",
    "T8.finish_unreachable_without_task_finalizing",
    "T9.ended_run_only_verification_mutates",
    "T10.drag_presentation_only",
    "T11.clamp_law_offscreen_impossible",
    "T12.critical_rect_drop_is_final",
    "T13.reduced_motion_preserves_state_truth",
    "T14.persistence_reload_restores_legal_placement",
    "T15.consume_drives_presentation",
    "M2.no_timer_advancement",
    "SHEET.manifest",
    "WORLD.authority",
    "WORLD.character_lab_selects_one_cosmetic_identity",
])
def test_law(law_name: str):
    results = js_law_results()
    assert law_name in results, f"law {law_name} did not run; ran: {sorted(results)}"
    assert results[law_name] == "PASS", f"{law_name}: {results[law_name]}"


def test_every_registered_law_ran():
    results = js_law_results()
    assert len(results) >= 17, f"expected >=17 laws, got {sorted(results)}"


def test_lane_node_events_classify_through_the_tool_rules() -> None:
    """Live finding 2026-08-29: a turn that really fetched weather/market data reached the
    client as task.started -> task.completed with nothing between, so the 117-phrase library
    had no events to narrate. Lane node events are real execution rows and must classify."""
    from core.companion_presentation_fragment import render_companion_fragment

    fragment = render_companion_fragment()
    assert 'ev.type === "agent_node_started" || ev.type === "agent_node_completed"' in fragment
    assert 'String(ev.operation || ev.tool || "").toLowerCase()' in fragment, (
        "a node classifies by its declared operation, read through the SAME tool-rule table"
    )
    assert '"quote"], "WEB_RESEARCH"' in fragment, "market_quote is a remote retrieval"


def test_node_emitter_streams_instead_of_ledger_only() -> None:
    """The producer half: node events must go through the STREAMING emitter (which also
    writes durably), not the ledger-only append that never reached the live client."""
    import pathlib

    src = pathlib.Path("core/agent_runtime/agent.py").read_text(encoding="utf-8")
    emitter = src[src.index("def _agent_node_emitter"):]
    emitter = emitter[: emitter.index("def _record_live_data_plan_subtasks")]
    assert "self._emit_runtime_event(" in emitter, "node events must use the streaming emitter"
    assert "append_runtime_event(" not in emitter, "ledger-only append was the delivery gap"


def test_caption_carries_the_phrase_with_a_readable_dwell() -> None:
    """Live finding 2026-08-29: the pill printed the sprite STATE while the 117-phrase library
    resolved into a surface the operator never read, and real turns finished in ~1s so any
    phrase flashed for a few frames. The pill now carries the phrase and holds it long enough
    to read — cadence only; terminal truth never waits behind it."""
    from core.companion_presentation_fragment import render_companion_fragment

    fragment = render_companion_fragment()
    assert "captionText = String(pres.phraseText)" in fragment, "the pill must carry the phrase"
    assert "VN_CAPTION_MIN_MS" in fragment and "vnCaptionHold" in fragment
    assert "const terminalNow = VN_TERMINAL_STATES[nextState] === true;" in fragment, (
        "the hold must judge the CURRENT state — reading the view's stale terminal disabled it"
    )
    assert "if (held && !terminalNow)" in fragment, (
        "a failure/cancellation/verdict must bypass the hold — truth never queues behind decoration"
    )
    assert "vnSchedulePaint();  // come back when the hold expires" in fragment, (
        "a held phrase must schedule its own repaint or the newer phrase would be dropped"
    )


def test_local_models_disabled_marker_stops_the_intent_arbiter(tmp_path, monkeypatch) -> None:
    """Operator hard stop: with the marker present NOTHING loads a local model, including the
    on-demand arbiter (the boot-prewarm gate alone did not cover it — measured live 2026-08-29
    with qwen3:4b cold-loading mid-turn on a memory-tight host)."""
    from core import intent_arbiter

    monkeypatch.setattr(intent_arbiter, "_MODEL_CACHE", {}, raising=False)
    monkeypatch.setattr("core.runtime_paths.active_data_dir", lambda: tmp_path)
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    (tmp_path / "config" / "local_models_disabled").touch()
    monkeypatch.delenv("VOOL_INTENT_ARBITER_MODEL", raising=False)
    assert intent_arbiter._resolve_model() == "", "the marker must yield no local model"

    # Control: without the marker the resolver is free to pick one again.
    (tmp_path / "config" / "local_models_disabled").unlink()
    monkeypatch.setattr(intent_arbiter, "_MODEL_CACHE", {}, raising=False)
    called = {}
    def _fake_get(url, timeout=None):
        called["hit"] = True
        class R:
            @staticmethod
            def json(): return {"models": [{"name": "qwen3:0.6b"}]}
        return R()
    import requests
    monkeypatch.setattr(requests, "get", _fake_get)
    assert intent_arbiter._resolve_model() == "qwen3:0.6b"
    assert called.get("hit") is True
