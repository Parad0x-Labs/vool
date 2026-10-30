"""A long job shows how much time is left, by default.

The estimate is the goal's own pace: working time per completed step times the steps still to go
(``goal_eta_seconds``, served as ``eta_seconds`` on the public view). It is unknown until a step
has finished and whenever the goal is not running. The goal card shows "About 25 min left" once
the job looks like 10 minutes or more in all, and nothing for a short or unestimated job.
"""

from __future__ import annotations

import json
import re

from core.goal_progress_fragment import render_goal_progress_fragment
import time

from core.goal_workflow import goal_eta_seconds, goal_public_view
from tests.chat_page_js_harness import run_node


def test_the_estimate_is_the_goals_own_pace_and_unknown_without_one() -> None:
    assert goal_eta_seconds("running", 600.0, 2, 5) == 900.0
    assert goal_eta_seconds("running", 600.0, 0, 5) is None, "no finished step: no pace yet"
    assert goal_eta_seconds("running", 600.0, 5, 5) is None, "nothing left to estimate"
    for state in ("paused", "waiting_approval", "blocked", "interrupted", "planning", "queued",
                  "completed", "failed", "cancelled"):
        assert goal_eta_seconds(state, 600.0, 2, 5) is None, state


def test_the_served_goal_view_carries_the_estimate() -> None:
    steps = [{"id": f"s{i}", "state": "completed" if i < 2 else "pending"} for i in range(5)]
    goal = {"goal_id": "g1", "state": "running", "started_at": time.time() - 600, "plan": {"steps": steps}}
    view = goal_public_view(goal)
    assert 880 <= view["eta_seconds"] <= 920, view["eta_seconds"]
    paused = goal_public_view({**goal, "state": "paused", "paused_at": time.time()})
    assert paused["eta_seconds"] is None


CARD = r"""
const ids = {};
class El {
  constructor() { this.textContent = ''; this.innerHTML = ''; this.title = ''; this.className = '';
    this.style = {}; this.attrs = {}; this.children = [];
    this.classList = { _s: new Set(), add: (c) => this.classList._s.add(c), remove: (c) => this.classList._s.delete(c),
      toggle: (c) => this.classList._s.has(c) ? this.classList._s.delete(c) : this.classList._s.add(c), contains: (c) => this.classList._s.has(c) }; }
  get lastChild() { return new El(); }
  querySelector() { return new El(); }
  querySelectorAll() { return []; }
  setAttribute(k, v) { this.attrs[k] = v; } getAttribute(k) { return this.attrs[k] || null; }
  addEventListener() {} appendChild(c) { this.children.push(c); return c; } insertAdjacentHTML() {}
  getBoundingClientRect() { return { top: 0 }; }
}
globalThis.window = globalThis;
globalThis.innerHeight = 800;
globalThis.addEventListener = () => {};
globalThis.document = {
  readyState: 'complete',
  getElementById: (id) => ids[id] || (ids[id] = new El()),
  createElement: () => new El(),
  body: new El(),
  addEventListener() {},
};
globalThis.fetch = () => new Promise(() => {});
const _si = globalThis.setInterval, _st = globalThis.setTimeout;
globalThis.setInterval = (f, ms) => { const t = _si(f, ms); t.unref && t.unref(); return t; };
globalThis.setTimeout = (f, ms) => { const t = _st(f, ms); t.unref && t.unref(); return t; };
window.VoolPageActions = { displayedChat: () => 'chat-a' };
__CARD_JS__
const base = { session_id: 'chat-a', goal_id: 'g1', state: 'running', has_plan: true, steps: [], paused: false, ended_at: 0 };
const seen = {};
for (const [name, extra] of Object.entries(__CASES__)) {
  window.VoolGoalCard.consume(Object.assign({}, base, extra));
  seen[name] = ids.vgcEta ? ids.vgcEta.textContent : null;
}
console.log(JSON.stringify(seen));
process.exit(0);
"""

CASES = {
    "long": {"completed": 2, "total": 5, "elapsed_seconds": 600, "eta_seconds": 900},
    "hours": {"completed": 1, "total": 4, "elapsed_seconds": 1800, "eta_seconds": 5400},
    "short": {"completed": 2, "total": 4, "elapsed_seconds": 120, "eta_seconds": 120},
    "unknown": {"completed": 0, "total": 5, "elapsed_seconds": 900, "eta_seconds": None},
    "paused": {"completed": 2, "total": 5, "elapsed_seconds": 600, "eta_seconds": 900,
               "state": "paused", "paused": True},
}


def _card() -> dict:
    html = render_goal_progress_fragment()
    js = "\n".join(re.findall(r"<script>(.*?)</script>", html, re.DOTALL))
    program = CARD.replace("__CARD_JS__", js).replace("__CASES__", json.dumps(CASES))
    out = run_node(program)
    return out


def test_the_card_shows_time_left_for_a_long_job_and_nothing_otherwise() -> None:
    seen = _card()
    assert seen["long"] == "About 15 min left"
    assert seen["hours"] == "About 1.5 hr left"
    assert seen["short"] == "", "a job under 10 minutes in all shows no ETA"
    assert seen["unknown"] == "", "no finished step means no estimate, not a guess"
    assert seen["paused"] == "", "a paused job has no pace to project"
