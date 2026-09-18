"""Activity bulk disclosure must repaint even when the panel's content is unchanged.

The 1.4s ledger poll used to rebuild the Activity body on every tick, which cancelled clicks
mid-press; the repair skips the rebuild when the panel's CONTENT signature is unchanged. But the
signature tracks only content (ledger length, steps, model) -- not disclosure state -- so the
deliberate re-render Expand all / Collapse all request was swallowed by the same skip whenever no
new events had arrived: the buttons wrote the open store and nothing repainted, which is the
owner-visible "Expand all / Collapse all frequently do nothing", worst exactly when the chat is
idle. The toggle now invalidates the signature before its own render; the poll's protective skip
is untouched.

Driven through the page's real render pipeline (renderPanel, not renderActivityTree) and the real
capture-phase pointerdown wiring, with a multi-turn chat ledger -- pointer presses are the
acceptance evidence, DOM click injection alone is not.
"""
from __future__ import annotations

import json

from tests.chat_page_js_harness import DOM, run_node, script

A = "openclaw:" + "a" * 20


def _turn_events() -> list[dict]:
    events = []
    seq = 0
    for turn in ("turn-1", "turn-2"):
        for event_type in ("task_received", "tool_selected", "tool_executed"):
            seq += 1
            row = {"seq": seq, "event_type": event_type, "client_turn_id": turn}
            if event_type == "task_received":
                row["message"] = "Received request: do the work of " + turn
            if event_type == "tool_selected":
                row["tool_name"] = "workspace.write_file"
            if event_type == "tool_executed":
                row["tool_name"] = "workspace.write_file"
                row["message"] = "wrote src/" + turn + ".py"
                row["status"] = "ok"
            events.append(row)
    return events


def _drive(body: str) -> dict:
    program = (
        "globalThis.__rawSetTimeout = globalThis.setTimeout;\n"
        + DOM
        + script()
        + "\nconst EVENTS = " + json.dumps(_turn_events()) + ";\n"
        + """
function openDetails(html) { return (String(html).match(/<details[^>]* open/g) || []).length; }
function allDetails(html) { return (String(html).match(/<details/g) || []).length; }
function press(action) {
  const btn = document.createElement('button');
  btn.setAttribute('data-activity-action', action);
  const target = { closest: (sel) => (sel === '[data-activity-action]' ? btn : null) };
  // The harness element has no DOM-tree contains(); the handler's containment guard is satisfied
  // for a press that arrived on this container exactly as it is in a real browser.
  const savedContains = xpBodyEl.contains;
  xpBodyEl.contains = () => true;
  try { xpBodyEl.__on.pointerdown({ button: 0, target: target, preventDefault() {} }); }
  finally { xpBodyEl.contains = savedContains; }
}
"""
        + "\n;(async () => {\n" + body + "\n})()"
        + ".catch((e) => { errors.push('drive: ' + (e && e.stack || e)); out({ errors }); });"
    )
    result = run_node(program, timeout=120)
    assert result.get("errors") == [], "the page threw while being driven:\n" + "\n".join(result["errors"])
    return result


def test_bulk_disclosure_repaints_an_idle_panel_and_survives_the_poll() -> None:
    data = _drive(f"""
    setDisplayedChat('{A}');
    view.run = null;
    view.chatLedger = EVENTS.slice();
    view.chatActivityOpen = {{}};
    document.body.classList.add('panel-open');
    panelTab = 'Activity';
    renderPanel();
    const first = xpBodyEl.innerHTML;
    const rendered = xpBodyEl.dataset.activityRendered;
    // The idle poll: same ledger, nothing moved -- the protective skip must hold.
    renderPanel();
    const idleSkipHeld = xpBodyEl.innerHTML === first;
    const initiallyOpen = openDetails(first);
    const totalNodes = allDetails(first);
    // A POINTER press on Expand all, through the real delegated capture-phase handler.
    press('expand');
    const afterExpand = xpBodyEl.innerHTML;
    const openAfterExpand = openDetails(afterExpand);
    // The poll again, still idle: the expanded state must survive (and the skip hold on it).
    renderPanel();
    const survivedPoll = openDetails(xpBodyEl.innerHTML) === openAfterExpand;
    // Collapse all through the same pointer path.
    press('collapse');
    const openAfterCollapse = openDetails(xpBodyEl.innerHTML);
    out({{
      rendered, idleSkipHeld, initiallyOpen, totalNodes,
      openAfterExpand, survivedPoll, openAfterCollapse,
      turnSections: (first.match(/data-node-id="turn:/g) || []).length,
    }});
    """)
    assert data["rendered"] == "1", "the Activity body never rendered"
    assert data["turnSections"] == 2, "expected both turns rendered as sections"
    assert data["idleSkipHeld"] is True, "the poll's no-change skip regressed (clicks would die again)"
    assert data["initiallyOpen"] < data["totalNodes"], "the tree was already fully open; the case proves nothing"
    assert data["openAfterExpand"] == data["totalNodes"], (
        f"Expand all left {data['totalNodes'] - data['openAfterExpand']} node(s) closed on an idle panel"
    )
    assert data["survivedPoll"] is True, "the expanded state did not survive the idle poll"
    assert data["openAfterCollapse"] == 0, f"Collapse all left {data['openAfterCollapse']} node(s) open"


def test_bulk_disclosure_works_while_events_keep_arriving() -> None:
    """The active case: new events land between presses, so each poll rebuild IS taken -- the
    press must still open everything currently rendered, including the turn that just grew."""
    data = _drive(f"""
    setDisplayedChat('{A}');
    view.run = null;
    view.chatLedger = EVENTS.slice(0, 3);
    view.chatActivityOpen = {{}};
    document.body.classList.add('panel-open');
    panelTab = 'Activity';
    renderPanel();
    press('expand');
    const openWhenIdle = openDetails(xpBodyEl.innerHTML);
    // Events for the second turn arrive: the poll rebuilds with new content.
    view.chatLedger = EVENTS.slice();
    renderPanel();
    const totalAfterArrival = allDetails(xpBodyEl.innerHTML);
    const openAfterArrival = openDetails(xpBodyEl.innerHTML);
    press('expand');
    const openAfterSecondPress = openDetails(xpBodyEl.innerHTML);
    out({{ openWhenIdle, totalAfterArrival, openAfterArrival, openAfterSecondPress }});
    """)
    assert data["openWhenIdle"] > 0
    assert data["openAfterArrival"] < data["totalAfterArrival"], (
        "the newly arrived turn should render under the chat's default disclosure (latest open, older closed)"
    )
    assert data["openAfterSecondPress"] == data["totalAfterArrival"], (
        "Expand all did not reach every node of the turn that arrived after the previous press"
    )
