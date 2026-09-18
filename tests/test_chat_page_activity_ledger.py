"""Guard: the Activity panel is backed by the runtime LEDGER, scoped to the current turn.

The chat stream only delivers mapped tool events; the real timeline (scope, classification, model
routing, tools, timeouts, verification, terminal) lives in the runtime event ledger. The Activity tab
must poll /api/runtime/events, keep only rows tagged with THIS turn's client_turn_id, say plainly when
a turn ran no tool, and rebuild the last turn after a page refresh -- never a blank panel while the
card says "working".
"""

from __future__ import annotations

from core.vool_chat_page import render_vool_chat_html
from tests.chat_page_js_harness import DOM, run_node, script

HTML = render_vool_chat_html()


def test_activity_polls_the_runtime_ledger() -> None:
    assert "function pollLedger" in HTML
    assert "'/api/runtime/events?session='" in HTML
    # The per-run poll timer is started with the run and torn down when it ends.
    assert "run.ledgerTimer = setInterval" in HTML
    assert "if (run.ledgerTimer) { clearInterval(run.ledgerTimer); run.ledgerTimer = null; }" in HTML


def test_activity_is_scoped_to_this_turn() -> None:
    # A different turn's events (queued/overlapping) must not bleed into this run's timeline.
    assert "e.client_turn_id !== run.turnId" in HTML
    # Server-side, every emitted event is tagged from the per-turn cancel id.
    assert "client_turn_id" in HTML


def test_activity_renders_the_real_event_vocabulary() -> None:
    assert "function ledgerRow" in HTML
    assert "function renderLedgerRows" in HTML
    for label in ("Scope", "Classified", "Model selected", "Model call failed", "Independent review passed"):
        assert label in HTML, label
    # The noisy streaming-text event is never rendered as a row.
    assert "model_output_chunk: 1" in HTML


def test_activity_states_when_no_tool_ran() -> None:
    assert "function ledgerRanNoTool" in HTML
    assert "No tool ran" in HTML


def test_activity_shows_redacted_tool_args() -> None:
    # A tool row surfaces WHAT it ran with (the redacted arg summary), not just the tool name.
    assert "e.tool_args" in HTML


def test_activity_recovers_after_page_refresh() -> None:
    assert "function loadRecoveredLedger" in HTML
    assert "recoveredLedger" in HTML
    # The recovered view is the whole chat now, not a "Latest task activity" slice of it: the loader
    # keeps every event and the panel renders one section per turn, newest first. See
    # tests/test_activity_chat_scope.py -- rendering only the last turn discarded 95% of a real
    # 307-event chat under a heading that said "Current chat".
    assert "for (const event of more) appendToChatLedger(target, event);" in HTML
    assert "chatLedgerCursor" in HTML
    assert "function renderChatActivity" in HTML
    assert "Task interrupted by application restart" in HTML
    assert "data-recovery=\"resume\"" in HTML
    assert "data-recovery=\"cancel\"" in HTML
    # openPanel does an immediate read so the panel is current the instant it opens.
    # Same behaviour, per-chat state: the displayed chat's live run is `view.run` since
    # DISPATCHER phase 1 replaced the global `activeRun`.
    assert "if (view.run) pollLedger(view.run); else loadRecoveredLedger()" in HTML


def test_activity_contains_permanent_cross_chat_history() -> None:
    """Cross-chat history still exists — QA-050-025 moved it behind a scope, it did not remove it."""
    assert "function loadActivityHistory" in HTML
    # The read goes through ONE shared in-flight request (chat-startup repair, 2026-09-06): the
    # sidebar tick and the activity panel must never each start their own history traversal.
    assert "fetchJsonWithin('/api/runtime/sessions'" in HTML
    assert "const data = await fetchRuntimeActivity();" in HTML
    assert "data-history-session" in HTML
    assert "tool_receipt_count" in HTML
    assert "changed_paths" in HTML
    # The single global header was replaced by scope-named ones; the global view is still reachable.
    assert "Every chat in VOOL" in HTML
    assert "Chats in " in HTML


def test_cross_chat_history_is_not_mixed_into_a_single_chats_evidence() -> None:
    """QA-050-025: global activity was the DEFAULT, so one chat's evidence carried every project's.

    Measured on the running build before the fix: opening the panel on a fresh chat rendered 34
    cross-chat cards from /api/runtime/sessions under the current turn. The default is now the open
    chat, and the renderer returns nothing at all in that scope.
    """
    assert "let panelScope = localStorage.getItem('vool_panel_scope') || 'chat'" in HTML
    assert "if (panelScope === 'chat') return '';" in HTML, (
        "the cross-chat list must render nothing in chat scope, not merely be sorted differently"
    )
    # Scope reaches the other evidence surfaces, not only the Activity list.
    assert "if (panelScope !== 'chat') { renderScopedReceipts(body); return; }" in HTML
    assert "function renderEventLog" in HTML
    assert "emptyScopeText" in HTML


def _scope_cases() -> dict:
    """EXECUTE the renderer in each scope, rather than asserting on its source text."""
    return run_node(
        DOM
        + script()
        + """
// Two chats in two different projects, plus the one on screen. Only the open chat's evidence may
// appear in chat scope; project scope must admit its own project and nothing else.
const OPEN = 'openclaw:aaaa', SAME_PROJECT = 'openclaw:bbbb', OTHER_PROJECT = 'openclaw:cccc';
_serverProjects = { p1: { name: 'Lumen' }, p2: { name: 'Other' } };
_lastSessions = [
  { session_id: OPEN, title: 'Open chat', project_id: 'p1' },
  { session_id: SAME_PROJECT, title: 'Sibling chat', project_id: 'p1' },
  { session_id: OTHER_PROJECT, title: 'Unrelated chat', project_id: 'p2' },
];
activityHistory = _lastSessions.map((s) => ({
  session_id: s.session_id, status: 'completed', request_preview: 'probe ' + s.title,
  execution_history: { status: 'completed', request_preview: 'probe ' + s.title, bounded_execution: {} },
}));
setDisplayedChat(OPEN);
view.projectId = 'p1';

function cardsFor(scope) {
  panelScope = scope;
  const html = renderActivityHistoryHtml();
  return (html.match(/data-history-session="([^"]*)"/g) || [])
    .map((m) => m.replace(/.*="/, '').replace(/"$/, ''));
}
function emptyFor(scope) { panelScope = scope; return emptyScopeText('activity'); }

out({
  chatScopeCards: cardsFor('chat'),
  projectScopeCards: cardsFor('project'),
  allScopeCards: cardsFor('all'),
  emptyChat: emptyFor('chat'),
  emptyProject: emptyFor('project'),
  emptyAll: emptyFor('all'),
});
"""
    )


def test_chat_scope_renders_no_other_chats_evidence_when_executed() -> None:
    """The behavioural half: run the renderer, do not merely grep for the guard."""
    cases = _scope_cases()
    assert cases["errors"] == []
    assert cases["chatScopeCards"] == [], (
        "chat scope rendered another chat's activity card: "
        f"{cases['chatScopeCards']}"
    )


def test_project_scope_admits_its_own_project_and_excludes_the_others() -> None:
    cases = _scope_cases()
    assert cases["errors"] == []
    assert sorted(cases["projectScopeCards"]) == ["openclaw:aaaa", "openclaw:bbbb"]
    assert "openclaw:cccc" not in cases["projectScopeCards"], "another project's chat leaked in"
    # The global view is still reachable and still global.
    assert sorted(cases["allScopeCards"]) == ["openclaw:aaaa", "openclaw:bbbb", "openclaw:cccc"]


def test_the_empty_state_names_the_scope_it_is_empty_for() -> None:
    cases = _scope_cases()
    assert cases["emptyChat"] == "No activity for this chat yet."
    assert cases["emptyProject"] == "No activity for project Lumen yet."
    assert cases["emptyAll"] == "No activity recorded anywhere in VOOL yet."


def test_inline_progress_is_event_grounded_not_rotating_conversational_filler() -> None:
    for fake in ("Thinking…", "Working through it…", "Composing the answer…", "Almost there…", "const phrases"):
        assert fake not in HTML
    assert "run.steps.slice(-3)" in HTML
    assert 'class="tc-mode"' in HTML
    assert "case 'mode.changed'" in HTML


def test_duplicate_and_out_of_order_stream_events_are_ignored() -> None:
    assert "eventSeq: 0, eventSeen: {}" in HTML
    assert "run.eventSeen[eventKey]" in HTML
    assert "seq && seq < run.eventSeq" in HTML


def test_activity_badge_still_counts_tool_steps() -> None:
    # The tab badge counts real tool actions (not raw events) -- unchanged contract.
    assert "Activity: run ? run.steps.length" in HTML


def test_plugin_catalog_cannot_override_the_activity_renderer() -> None:
    # Both helpers once used `renderPanel`; JavaScript function hoisting made Activity silently call
    # the plugin renderer and left its tabpanel blank without a useful browser error.
    assert HTML.count("function renderPanel()") == 1
    assert "function renderPluginPanel(d, query)" in HTML
    assert "renderPanel(_pluginCatalog" not in HTML
