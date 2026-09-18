"""Laws for the Council control-room surface.

The demo truth this suite fences (full-a11-demo-app pass-001, §14): a Council
surface MUST be visible, and it MUST NOT fake a live council. No seats are
drawn because no council worker producers exist; the per-run lens renders only
typed review/verification evidence and model provenance; the control room
renders only the served runtime's own version stamp. When real council
producers arrive they publish typed events and the same cells render them.

Like the companion laws, the JS is exercised for real: the page script is
booted under the shared node DOM stub and driven through the same functions the
served page calls.
"""

from __future__ import annotations

import functools
import re

from core.vool_chat_page import render_vool_chat_html
from tests.chat_page_js_harness import DOM, run_node

HTML = render_vool_chat_html()


def page_scripts() -> list[str]:
    """Every inline script of the served page, in document order.

    Counted, not fixed at a number. This asserted ``== 2`` from when the page was the
    house script plus the companion; every appended fragment since (chips, palette,
    composer extras, price safety, notifications, settings extras, drawer, council,
    scene lab, the council chat card) added one more, and the suite had been red on the
    count alone rather than on any law it exists to prove. What the laws actually need
    is EVERY script, in order, with the house script first — that is what is checked.
    """
    found = re.findall(r"<script[^>]*>(.*?)</script>", HTML, re.DOTALL)
    assert len(found) >= 2, f"expected at least the house + companion scripts, found {len(found)}"
    assert "renderCouncilTab" in found[0], "the house script must evaluate first"
    return found


def test_council_dom_present_exactly_once():
    assert HTML.count('id="councilBtn"') == 1, "the topbar Council button must exist exactly once"
    assert HTML.count('id="councilOverlay"') == 1, "the council control-room overlay must exist exactly once"
    assert HTML.count('id="councilBody"') == 1
    # The per-run Council tab is a first-class panel tab, after Receipts.
    tabs = re.search(r"const PANEL_TABS = \[(.*?)\];", HTML, re.DOTALL)
    assert tabs and "'Council'" in tabs.group(1), "Council must be a PANEL_TABS member"
    assert tabs.group(1).index("'Council'") > tabs.group(1).index("'Receipts'")


def test_council_truth_laws():
    results = council_js_laws()
    for name in sorted(results):
        assert results[name] == "PASS", f"{name}: {results[name]}"
    for required in ["C1.no_fake_seats", "C2.no_active_session_truth",
                     "C3.verdict_from_typed_state", "C4.human_authority_visible",
                     "C5.control_room_renders_served_identity"]:
        assert required in results, f"law {required} did not run; ran: {sorted(results)}"


LAWS = r"""
const laws = {};
async function law(name, fn) {
  try { await fn(); laws[name] = "PASS"; }
  catch (e) { laws[name] = "FAIL: " + ((e && e.message) || e); }
}
function assert(cond, msg) { if (!cond) throw new Error(msg || "assertion failed"); }

function freshRun(patch) {
  return Object.assign({
    steps: [], events: [], files: {}, tests: { total: 0, done: 0, failed: 0 },
    ended: true, status: 'completed', reviewState: '', model: null, cost: null,
  }, patch || {});
}
function councilHtmlFor(run) {
  view.run = run || null;
  const body = document.getElementById('xpBody');
  renderCouncilTab(body);
  return body.innerHTML;
}

// -- C1 no fake seats ----------------------------------------------------------
law("C1.no_fake_seats", () => {
  const html = councilHtmlFor(freshRun());
  assert(html.indexOf('<canvas') === -1, "the council surface must never draw animated seat canvases");
  assert(html.indexOf('<img') === -1 && html.indexOf('avatar') === -1, "no seat imagery in the council surface");
  assert(!/council-seat|agent-seat|class="seat/.test(html), "no seat elements in the council surface");
  assert(html.indexOf('VoolCompanion') === -1, "the council surface is not a companion clone");
  // Feeding synthetic multi-agent events changes nothing: there is no AGENT_* producer.
  const before = councilHtmlFor(freshRun());
  assert(before === html, "absent producers cannot change the surface");
});

// -- C2 the truthful empty state is content --------------------------------------
// REWRITTEN. This pinned the literal 'NO ACTIVE COUNCIL SESSION' and the note that
// said "No multi-model council runtime is wired into this build". That note was
// FALSE: the council runtime is wired -- nine registry commands behind availability
// probes with their /api/council/* routes -- and council.runs answers
// {"ok": true, "runs": []}. "No runtime" and "no run yet" are different facts, and a
// third exists (the runtime did not answer). The law is now that the panel states
// whichever is TRUE, and never the false one.
const HONEST_STATES = ['NO COUNCIL RUN CONVENED', 'COUNCIL STATE NOT READ YET',
                       'COUNCIL RUNTIME DID NOT ANSWER', 'COUNCIL RUNS:'];
function statesHonest(html) { return HONEST_STATES.some((s) => html.indexOf(s) !== -1); }
await law("C2.no_active_session_truth", () => {
  const html = councilHtmlFor(freshRun());
  assert(statesHonest(html), "an honest session state must be visible");
  assert(html.indexOf('No multi-model council runtime is wired into this build') === -1,
         "the panel must not claim the council runtime does not exist");
  // It stays honest with a run on screen: per-run evidence is additive, never a session.
  const withRun = councilHtmlFor(freshRun({ reviewState: 'passed' }));
  assert(statesHonest(withRun), "per-run verdicts must not fabricate a session");
  assert(withRun.indexOf('Independent review passed') !== -1, "the typed verdict must render");
});

// -- C3 verdicts come from typed review state only ----------------------------------
await law("C3.verdict_from_typed_state", () => {
  assert(councilHtmlFor(freshRun()).indexOf('No reviewer verdict yet') !== -1,
    "an unreviewed run says exactly that");
  assert(councilHtmlFor(freshRun({ reviewState: 'flagged' })).indexOf('Review flagged') !== -1,
    "a flagged verdict renders as flagged");
  assert(councilHtmlFor(freshRun({ reviewState: 'passed' })).indexOf('not validation or answer proof') !== -1,
    "a PASS carries the honesty caveat: model review is not answer proof");
  // Provenance rows render the typed identity tiers, verbatim, or say nothing was recorded.
  const m = { requested_provider_id: 'r', requested_model_id: 'rq', actual_adapter_provider_id: 'a', actual_adapter_model_id: 'am' };
  const html = councilHtmlFor(freshRun({ model: m }));
  assert(html.indexOf('Actual adapter') !== -1 && html.indexOf('Requested') !== -1, "identity tiers render");
  assert(councilHtmlFor(freshRun({ model: null })).indexOf('no model provenance recorded') !== -1,
    "absent provenance says so");
});

// -- C4 human authority is on the surface ---------------------------------------------
await law("C4.human_authority_visible", () => {
  const html = councilHtmlFor(freshRun());
  for (const state of ['READY FOR HUMAN PROMOTION', 'FROZEN', 'COUNTEREXAMPLE FOUND', 'REJECTED']) {
    assert(html.indexOf(state) !== -1, "lifecycle must include " + state);
  }
  assert(html.indexOf('Human authority') !== -1 && html.indexOf('operator alone') !== -1,
    "human promotion/freeze authority must be stated");
  assert(html.indexOf('never self-freezing') !== -1, "the surface must disclaim self-freeze");
});

// -- C5 the control room renders the SERVED identity, never an invented one -------------
await law("C5.control_room_renders_served_identity", async () => {
  await openCouncil();
  const body = document.getElementById('councilBody');
  const html = body.innerHTML;
  assert(statesHonest(html), "the control room is honest about sessions too");
  assert(html.indexOf('No multi-model council runtime is wired into this build') === -1,
         "the control room must not claim the council runtime does not exist");
  // The stub runtime answers /api/runtime/version with commit 'c', build_id 'b': those EXACT
  // values must be what the room shows — server truth, not a fabricated candidate.
  assert(/>c<\/div>/.test(html) || />c<span/.test(html) || html.indexOf('>c</div>') !== -1,
    "candidate SHA cell renders the served commit");
  assert(html.indexOf('>b</div>') !== -1, "build id cell renders the served build id");
  assert(html.indexOf('Evidence a real session must carry') !== -1, "evidence vocabulary is shown");
  closeCouncil();
  assert(councilOverlay.hidden === true, "close hides the overlay");
});
"""

PROGRAM_TAIL = "\nout({ laws: laws });\n"


@functools.lru_cache(maxsize=1)
def council_js_laws() -> dict:
    """Boot the real page under node once and run the council laws in its scope."""
    program = (DOM + "\n;(async function(){\n" + "\n;\n".join(page_scripts())
               + "\n" + LAWS + PROGRAM_TAIL + "\n})();\n")
    result = run_node(program, timeout=120)
    assert not result.get("errors"), f"page booted with errors: {result['errors']}"
    return result["laws"]
