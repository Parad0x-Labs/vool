"""The Agents tab answers "is an agent at work" while the agent is at work.

These tests EXECUTE the shipped `agentRowsFrom` / `agentSummaryOf` / `agentEvidenceText` under node,
rather than asserting on the page source. A panel test that greps for a class name proves the string
is in the file; it cannot tell a working derivation from one that never runs -- and this repo has
been bitten repeatedly by exactly that (a reachable path nobody executed).

The derivation reads the CHAT LEDGER, whose rows come from `/api/runtime/events` with `details`
FLATTENED onto the row (measured, and pinned in
`tests/test_node_events_reach_the_pollable_stream.py`). A reader expecting `row["details"]["node_id"]`
finds nothing and renders an empty panel while work is happening.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

PAGE = Path(__file__).resolve().parents[1] / "core" / "vool_chat_page.py"
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")


def _extract_js() -> str:
    """The three pure functions, lifted verbatim from the shipped page."""

    source = PAGE.read_text(encoding="utf-8")
    start = source.index("function fmtElapsed(ms) {")
    end = source.index("function esc(t)", start)
    body = source[start:end]
    assert "function agentRowsFrom(" in body, "agentRowsFrom is not where the test expects it"
    assert "function agentEvidenceText(" in body, "agentEvidenceText missing"
    return body


@pytest.fixture(scope="module")
def js() -> str:
    return _extract_js()


def _run(js: str, expr: str, events, now_ms: int = 1_000_000):
    script = (
        js
        + f"\nconst EVENTS = {json.dumps(events)};\nconst NOW = {now_ms};\n"
        + f"process.stdout.write(JSON.stringify({expr}));\n"
    )
    proc = subprocess.run(
        [NODE, "--input-type=module", "-e", script], capture_output=True, text=True, timeout=30
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def _started(node_id: str, *, op: str = "fresh_data.weather", iso: str = "2026-08-14T20:00:00.000Z", **extra):
    row = {
        "event_type": "agent_node_started",
        "seq": 1,
        "node_id": node_id,
        "operation": op,
        "plan_id": "plan-1",
        "started_at_iso": iso,
    }
    row.update(extra)
    return row


def _completed(node_id: str, *, state="succeeded", ok=True, duration=1.5, rendered="did the thing", **extra):
    row = {
        "event_type": "agent_node_completed",
        "seq": 2,
        "node_id": node_id,
        "operation": "fresh_data.weather",
        "state": state,
        "ok": ok,
        "duration_s": duration,
        "rendered": rendered,
        "rendered_truncated": False,
    }
    row.update(extra)
    return row


# ---------------------------------------------------------------------------------------------
# G1 -- LIVENESS: a started node with no completion is RUNNING, with a live elapsed
# ---------------------------------------------------------------------------------------------


def test_a_started_node_with_no_completion_is_running(js) -> None:
    rows = _run(js, "agentRowsFrom(EVENTS, NOW)", [_started("weather")])

    assert len(rows) == 1
    assert rows[0]["running"] is True
    assert rows[0]["state"] == "running"


def test_a_running_node_reports_elapsed_from_its_own_start(js) -> None:
    """Not from the poll time, and not from when the turn began."""

    started_ms = 1_000_000 - 12_000  # 12s ago
    iso = "2026-08-14T20:00:00.000Z"
    rows = _run(
        js,
        "agentRowsFrom(EVENTS, NOW)",
        [_started("weather", iso=iso)],
        now_ms=int(__import__("datetime").datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp() * 1000) + 12_000,
    )

    assert rows[0]["elapsedMs"] == 12_000
    assert started_ms  # keeps the intent of the arithmetic visible


def test_a_completed_node_reports_its_measured_duration(js) -> None:
    rows = _run(js, "agentRowsFrom(EVENTS, NOW)", [_started("a"), _completed("a", duration=2.25)])

    assert rows[0]["running"] is False
    assert rows[0]["elapsedMs"] == 2250
    assert rows[0]["state"] == "succeeded"


# ---------------------------------------------------------------------------------------------
# CLEAN -- several agents, dependencies, failure, mixed states
# ---------------------------------------------------------------------------------------------


def test_several_agents_in_one_session_stay_separate_rows(js) -> None:
    """The axis the existing task rail lacks entirely: it keys on session alone."""

    events = [_started("a"), _started("b"), _started("c"), _completed("b")]
    rows = _run(js, "agentRowsFrom(EVENTS, NOW)", events)

    assert [r["nodeId"] for r in rows] == ["a", "b", "c"]
    assert [r["running"] for r in rows] == [True, False, True]


def test_the_summary_counts_running_done_and_failed(js) -> None:
    events = [
        _started("a"), _completed("a"),
        _started("b"), _completed("b", state="failed", ok=False, rendered=""),
        _started("c"),
    ]
    summary = _run(js, "agentSummaryOf(agentRowsFrom(EVENTS, NOW))", events)

    assert summary == {"total": 3, "running": 1, "failed": 1, "done": 2}


def test_a_failed_agent_says_why(js) -> None:
    events = [_started("a"), _completed("a", state="failed", ok=False, rendered="", failure_reason="RuntimeError: boom")]
    rows = _run(js, "agentRowsFrom(EVENTS, NOW)", events)

    assert rows[0]["ok"] is False
    assert "boom" in rows[0]["failureReason"]


def test_dependencies_are_carried_for_display(js) -> None:
    rows = _run(js, "agentRowsFrom(EVENTS, NOW)", [_started("b", depends_on=["a"])])

    assert rows[0]["dependsOn"] == ["a"]


# ---------------------------------------------------------------------------------------------
# COPY -- "copy all agent work" must contain the work
# ---------------------------------------------------------------------------------------------


def test_copy_contains_the_actual_work_not_just_headings(js) -> None:
    """The header Copy previously fell back to innerText, and every agent's work sits inside a
    collapsed <details> -- so the fallback would hand over headings and no work at all."""

    events = [_started("weather"), _completed("weather", rendered="It is 14C in Vilnius.")]
    text = _run(js, "agentEvidenceText(agentRowsFrom(EVENTS, NOW), NOW)", events)

    assert "It is 14C in Vilnius." in text
    assert "weather" in text
    assert "succeeded" in text


def test_copy_marks_a_truncated_result_rather_than_pretending(js) -> None:
    events = [_started("a"), _completed("a", rendered="x" * 50, rendered_truncated=True)]
    text = _run(js, "agentEvidenceText(agentRowsFrom(EVENTS, NOW), NOW)", events)

    assert "[truncated]" in text


def test_copy_of_an_empty_roster_says_so(js) -> None:
    assert _run(js, "agentEvidenceText(agentRowsFrom(EVENTS, NOW), NOW)", []) == "No agent activity recorded."


def test_copy_includes_a_still_running_agent(js) -> None:
    """A copy taken mid-run must not silently omit the agent that is still working."""

    text = _run(js, "agentEvidenceText(agentRowsFrom(EVENTS, NOW), NOW)", [_started("slow")])

    assert "slow" in text and "running" in text


# ---------------------------------------------------------------------------------------------
# NEGATIVE CONTROLS
# ---------------------------------------------------------------------------------------------


def test_unrelated_events_produce_no_agents(js) -> None:
    """The ledger carries every event this chat ever recorded. Only conductor node rows are agents."""

    noise = [
        {"event_type": "tool.started", "seq": 1, "tool": "workspace.read_file"},
        {"event_type": "model.call_completed", "seq": 2, "model": "qwen2.5:7b"},
        {"event_type": "status", "seq": 3, "message": "thinking"},
    ]
    assert _run(js, "agentRowsFrom(EVENTS, NOW)", noise) == []


def test_a_non_conductor_event_carrying_a_node_id_is_still_not_an_agent(js) -> None:
    """The EVENT-TYPE filter, exercised on its own.

    Written after a sabotage run: deleting the event-type check left the family green, because
    every noise row above happens to lack `node_id` and was rejected by the id guard instead. The
    kind filter was doing nothing that any test could see. `node_id` is a plain detail key and the
    store flattens arbitrary details onto the row, so another subsystem is free to use it.
    """

    noise = [
        {"event_type": "tool.started", "seq": 1, "node_id": "not-an-agent", "tool": "read_file"},
        {"event_type": "hive.task_assigned", "seq": 2, "node_id": "peer-7"},
        {"type": "verification.completed", "seq": 3, "node_id": "reviewer"},
    ]
    assert _run(js, "agentRowsFrom(EVENTS, NOW)", noise) == []


def test_a_node_event_without_an_id_is_ignored(js) -> None:
    assert _run(js, "agentRowsFrom(EVENTS, NOW)", [{"event_type": "agent_node_started", "seq": 1}]) == []


def test_empty_and_missing_input_are_safe(js) -> None:
    assert _run(js, "agentRowsFrom(EVENTS, NOW)", []) == []
    assert _run(js, "agentRowsFrom(null, NOW)", []) == []


# ---------------------------------------------------------------------------------------------
# ADVERSARIAL
# ---------------------------------------------------------------------------------------------


def test_a_late_duplicate_started_cannot_resurrect_a_finished_agent(js) -> None:
    """Events are cursor-paged and can be re-delivered. A finished agent flipping back to
    "running" would show the operator work that is not happening."""

    events = [_started("a"), _completed("a"), _started("a")]
    rows = _run(js, "agentRowsFrom(EVENTS, NOW)", events)

    assert len(rows) == 1
    assert rows[0]["running"] is False
    assert rows[0]["state"] == "succeeded"


def test_a_completion_with_no_started_is_still_shown(js) -> None:
    """Paging can drop the operator into the middle of a session. An agent whose start scrolled
    past must still appear, not vanish."""

    rows = _run(js, "agentRowsFrom(EVENTS, NOW)", [_completed("orphan")])

    assert len(rows) == 1
    assert rows[0]["running"] is False


def test_an_unparseable_start_stamp_does_not_invent_an_elapsed(js) -> None:
    """A wrong number here would be read as fact. No stamp must mean no claim."""

    rows = _run(js, "agentRowsFrom(EVENTS, NOW)", [_started("a", iso="not-a-date")])

    assert rows[0]["running"] is True
    assert rows[0]["elapsedMs"] is None


def test_the_task_stream_key_is_also_accepted(js) -> None:
    """The same record can arrive as `type` (task stream) or `event_type` (runtime events).
    Reading only one is how a panel shows an empty list while the work is happening."""

    rows = _run(
        js,
        "agentRowsFrom(EVENTS, NOW)",
        [{"type": "agent_node_started", "node_id": "a", "operation": "op", "started_at_iso": "2026-08-14T20:00:00.000Z"}],
    )

    assert len(rows) == 1 and rows[0]["running"] is True


def test_the_tab_is_registered_in_the_shipped_page() -> None:
    """The derivation being right is not the same as the tab existing."""

    source = PAGE.read_text(encoding="utf-8")
    tabs = re.search(r"const PANEL_TABS = \[(.*?)\];", source)
    assert tabs and "'Agents'" in tabs.group(1)
    assert "if (panelTab === 'Agents') { renderAgents(body); return; }" in source
    assert "function renderAgents(body)" in source


# ---------------------------------------------------------------------------------------------
# ESCAPING -- `rendered` is model and tool output, so it is untrusted by construction
# ---------------------------------------------------------------------------------------------


_ESC_SHIM = """
class El {
  constructor() { this._text = ''; this._html = ''; }
  set textContent(v) {
    this._text = String(v == null ? '' : v);
    this._html = this._text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }
  get textContent() { return this._text; }
  set innerHTML(v) { this._html = String(v); }
  get innerHTML() { return this._html; }
  querySelector() { return null; }
}
const document = { createElement: () => new El() };
"""


def _render_html(page_source: str, events) -> str:
    """Execute the SHIPPED renderAgents against a minimal DOM."""

    start = page_source.index("function fmtAgentDuration(")
    end = page_source.index("function esc(t)", start)
    pure = page_source[start:end]
    esc_src = page_source[page_source.index("function esc(t)") : page_source.index("function safeUrl(u)")]
    render_start = page_source.index("function renderAgents(body) {")
    render_end = page_source.index("function renderEventLog(body) {", render_start)
    renderer = page_source[render_start:render_end]

    script = (
        _ESC_SHIM
        + "\nfunction fmtElapsed(ms){const s=Math.max(0,Math.floor(ms/1000));return s+'s';}\n"
        + "let view = { chatLedger: " + json.dumps(events) + " };\n"
        + "function scopeWhere(){ return 'this chat'; }\n"
        + "function copyText(t){}\n"
        + esc_src + "\n" + pure + "\n" + renderer + "\n"
        + "const body = new El();\nrenderAgents(body);\nprocess.stdout.write(body.innerHTML);\n"
    )
    proc = subprocess.run(
        [NODE, "--input-type=module", "-e", script], capture_output=True, text=True, timeout=30
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def test_untrusted_agent_output_is_escaped_before_it_reaches_the_panel() -> None:
    """`rendered` is whatever a tool or a model produced. It is interpolated into innerHTML."""

    events = [
        _started("<img src=x onerror=alert(1)>", op="<script>bad()</script>"),
        _completed(
            "<img src=x onerror=alert(1)>",
            rendered="<script>steal()</script>",
            failure_reason="<b>boom</b>",
            state="failed",
            ok=False,
        ),
    ]
    html = _render_html(PAGE.read_text(encoding="utf-8"), events)

    # The property is that no `<` from untrusted data survives as markup -- NOT that the payload's
    # letters are absent. `&lt;img src=x onerror=alert(1)&gt;` is inert text and contains
    # "onerror=alert(1)" quite legitimately; asserting on that substring tests the wrong thing.
    assert "<script" not in html
    assert "<img" not in html
    assert "&lt;script&gt;steal()&lt;/script&gt;" in html
    assert "&lt;img src=x onerror=alert(1)&gt;" in html


def test_ordinary_output_still_renders_readably() -> None:
    """Negative control: escaping must not mangle a normal answer."""

    events = [_started("weather"), _completed("weather", rendered="It is 14C in Vilnius.")]
    html = _render_html(PAGE.read_text(encoding="utf-8"), events)

    assert "It is 14C in Vilnius." in html
    assert "weather" in html


def test_a_sub_second_agent_does_not_read_as_zero(js) -> None:
    """Found by the live product proof, not by a unit test.

    A real live-data fetch measured 491ms on the served surface and the panel said "succeeded in
    0s", because `fmtElapsed` floors to whole seconds. Agent durations are routinely sub-second, so
    the shared formatter reports every fast agent as not having run.
    """

    events = [_started("fast"), _completed("fast", duration=0.491)]
    text = _run(js, "agentEvidenceText(agentRowsFrom(EVENTS, NOW), NOW)", events)

    assert "0s" not in text
    assert "491ms" in text


def test_a_slow_agent_still_uses_the_shared_turn_formatter(js) -> None:
    """Negative control: above a second, an agent and its turn must not disagree about format."""

    events = [_started("slow"), _completed("slow", duration=125.0)]
    text = _run(js, "agentEvidenceText(agentRowsFrom(EVENTS, NOW), NOW)", events)

    assert "2m 05s" in text
    assert "125000ms" not in text


def test_the_lane_is_carried_so_the_panel_can_tell_the_two_apart(js) -> None:
    """Two different runners emit these events -- the conductor and the live-data lane.

    Both are "agents" to the operator, but they have different capabilities and different budgets,
    and a row that cannot say which one it came from cannot be diagnosed from the panel. Added
    after a sabotage run: dropping the lane assignment left the whole family green.
    """

    rows = _run(
        js,
        "agentRowsFrom(EVENTS, NOW)",
        [
            _started("a", lane="live_data"),
            _started("b", lane="conductor"),
            _completed("b", lane="conductor"),
        ],
    )

    lanes = {r["nodeId"]: r["lane"] for r in rows}
    assert lanes == {"a": "live_data", "b": "conductor"}


def test_a_row_without_a_lane_is_still_rendered(js) -> None:
    """Negative control: lane is descriptive, not required. An event missing it must not vanish."""

    rows = _run(js, "agentRowsFrom(EVENTS, NOW)", [_started("a")])

    assert len(rows) == 1
    assert rows[0]["lane"] == ""
