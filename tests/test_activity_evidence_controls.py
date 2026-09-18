"""Guard: Activity evidence can be opened, closed and copied in one action.

Two problems this pins, both about getting evidence OUT of the panel:

1. Reading it meant hand-opening the work log, then every category, then each of 30+ rows. There
   was no bulk control at all.
2. "Copy" read ``xpBodyEl.innerText``. In a browser, the contents of a collapsed ``<details>`` are
   not part of ``innerText`` -- so a copy taken from the panel in its DEFAULT (collapsed) state
   handed over a handful of category headlines and none of the evidence beneath them. The user
   could not tell: the clipboard had text in it, just not the text they were looking at.

Copy is now serialized from the tree MODEL, so it is complete regardless of what is expanded, and
Expand all / Collapse all move the whole tree at once.

These tests press the real buttons through the real handler rather than asserting the markup exists.
"""

from __future__ import annotations

import json

from core.vool_chat_page import render_vool_chat_html
from tests.chat_page_js_harness import DOM, run_node, script

HTML = render_vool_chat_html()

# A tree with real depth: two proven changes and two tool calls, so "expand everything" is a
# meaningfully different state from "expand the categories".
_EVENTS = [
    {
        "seq": 1, "event_type": "workspace_mutation_completed", "canonical_target": "src/alpha.py",
        "tool_intent": "workspace.write_file", "permission_decision": "allowed", "ok": True,
        "action": "created", "before_hash": "", "after_hash": "a" * 64,
        "diff_summary": "+alpha body", "error_class": "", "result_state": "executed",
    },
    {
        "seq": 2, "event_type": "workspace_mutation_completed", "canonical_target": "src/beta.py",
        "tool_intent": "workspace.replace_in_file", "permission_decision": "allowed", "ok": True,
        "action": "updated", "before_hash": "c" * 64, "after_hash": "d" * 64,
        "diff_summary": "-old beta\n+new beta", "error_class": "", "result_state": "executed",
    },
    {"seq": 3, "event_type": "tool_selected", "tool_name": "workspace.read_file", "tool_args": "path=src/alpha.py"},
    {"seq": 4, "event_type": "tool_executed", "tool_name": "workspace.read_file", "message": "read 40 lines", "status": "ok"},
]


def _drive_controls() -> dict:
    return run_node(
        DOM
        + script()
        + """
const EVENTS = """
        + json.dumps(_EVENTS)
        + """;
// No live run, so the recovered store is the open-state the controls operate on.
view.run = null;
view.recoveredActivityOpen = {};
const openState = view.recoveredActivityOpen;
panelTab = 'Activity';

const openDetails = (html) => (html.match(/<details[^>]* open>/g) || []).length;
const allDetails  = (html) => (html.match(/<details/g) || []).length;

// `ended: true` is the state a user actually finds the panel in after a turn finishes.
const initial = renderActivityTree(EVENTS, openState, { ended: true });

// Press the real controls through the real wiring. The harness DOM returns [] from
// querySelectorAll, so the buttons the renderer emits are handed to it here -- the HANDLER under
// test is the page's own, not a reimplementation.
function button(action) {
  const el = document.createElement('button');
  el.setAttribute('data-activity-action', action);
  return el;
}
const expandBtn = button('expand'), collapseBtn = button('collapse'), copyBtn = button('copy');
xpBodyEl.querySelectorAll = (sel) => (sel === '[data-activity-action]' ? [expandBtn, collapseBtn, copyBtn] : []);
wireActivityPanelActions();

expandBtn.__click();
const expanded = renderActivityTree(EVENTS, openState, { ended: true });
collapseBtn.__click();
const collapsed = renderActivityTree(EVENTS, openState, { ended: true });

// Copy while everything is CLOSED -- the exact state the old innerText copy lost evidence in.
let copied = '';
navigator.clipboard.writeText = async (text) => { copied = text; };
copyBtn.__click();

out({
  controlsInMarkup: (initial.match(/data-activity-action="([a-z]+)"/g) || []).map((m) => m.replace(/.*="/, '').replace(/"$/, '')),
  initialOpen: openDetails(initial),
  totalDetails: allDetails(initial),
  expandedOpen: openDetails(expanded),
  expandedTotal: allDetails(expanded),
  collapsedOpen: openDetails(collapsed),
  copiedWhileCollapsed: copied,
  headerCopyText: panelEvidenceText(),
});
"""
    )


def test_the_three_controls_are_offered_on_the_activity_tree() -> None:
    result = _drive_controls()
    assert result["errors"] == []
    assert result["controlsInMarkup"] == ["expand", "collapse", "copy"]
    for label in ("Expand all", "Collapse all", "Copy all"):
        assert label in HTML, label


def test_expand_all_opens_every_node_and_collapse_all_closes_every_node() -> None:
    result = _drive_controls()
    assert result["errors"] == []
    # The default a user lands on: not everything is open, which is the whole complaint.
    assert result["totalDetails"] > result["initialOpen"], (
        "the tree was already fully expanded, so this test proves nothing about Expand all"
    )
    assert result["expandedOpen"] == result["expandedTotal"], (
        f"Expand all left {result['expandedTotal'] - result['expandedOpen']} node(s) closed"
    )
    assert result["collapsedOpen"] == 0, (
        f"Collapse all left {result['collapsedOpen']} node(s) open"
    )


def test_copy_all_carries_the_evidence_while_the_tree_is_collapsed() -> None:
    """The regression that mattered: a copy taken from a collapsed panel must be complete."""
    result = _drive_controls()
    assert result["errors"] == []
    copied = result["copiedWhileCollapsed"]
    assert copied, "Copy all put nothing on the clipboard"

    # Scope is stated, so pasted evidence cannot be read as covering more than it does.
    assert copied.startswith("VOOL Activity — this chat")
    # Category headlines...
    assert "Files changed (2)" in copied
    # ...and the per-row evidence that lives inside a closed <details>.
    for evidence in (
        "src/alpha.py", "src/beta.py",
        "a" * 64, "d" * 64,          # after-hashes
        "+alpha body", "+new beta",  # diffs
        "workspace.replace_in_file",
        "workspace_mutation_completed",
    ):
        assert evidence in copied, f"collapsed copy dropped its evidence: {evidence!r} missing"


def test_the_header_copy_button_uses_the_same_complete_serializer() -> None:
    """The header Copy and the in-body Copy all must never disagree about what the evidence is."""
    result = _drive_controls()
    assert result["errors"] == []
    # copiedWhileCollapsed carries the scope heading the header copy prepends; the body is identical.
    assert result["headerCopyText"] in result["copiedWhileCollapsed"]
    assert "src/beta.py" in result["headerCopyText"]
    # The defect was reading rendered text instead of the model.
    assert "const text = ((xpBodyEl && xpBodyEl.innerText) || '').trim();\n  if (!text) { toast('Nothing to copy in '" not in HTML


def test_copy_never_serializes_a_tree_the_panel_is_no_longer_showing() -> None:
    """Scope truthfulness. The copy carries a "this chat" heading, so the tree it serializes must be
    the one on screen -- switching to a chat with no activity must not copy the previous chat's."""
    result = run_node(
        DOM
        + script()
        + """
view.run = null;
view.recoveredActivityOpen = {};
panelTab = 'Activity';
renderActivityTree("""
        + json.dumps(_EVENTS)
        + """, view.recoveredActivityOpen, { ended: true });
const afterRealChat = panelEvidenceText();
// Now a chat with nothing recorded: the renderer returns '' and draws no tree at all.
renderActivityTree([], {}, { ended: true });
out({ afterRealChat: afterRealChat, afterEmptyChat: panelEvidenceText() });
"""
    )
    assert result["errors"] == []
    assert "src/beta.py" in result["afterRealChat"]
    assert "src/beta.py" not in result["afterEmptyChat"], (
        "an empty chat copied the previous chat's evidence under its own scope heading: "
        f"{result['afterEmptyChat']!r}"
    )


def _drive_event_log() -> dict:
    return run_node(
        DOM
        + script()
        + """
view.run = null;
panelScope = 'chat';
panelTab = 'Event log';
view.recoveredLedger = [
  { seq: 1, event_type: 'scope_resolved', message: '/tmp/ws' },
  { seq: 2, event_type: 'tool_selected', message: 'Running workspace.write_file.' },
  { seq: 3, event_type: 'workspace_mutation_completed', message: 'wrote src/alpha.py' },
];
const sections = eventLogSections();
renderEventLog(xpBodyEl);
let copied = '';
navigator.clipboard.writeText = async (text) => { copied = text; };
const copyBtn = document.createElement('button');
copyBtn.setAttribute('data-eventlog-action', 'copy');
xpBodyEl.querySelectorAll = (sel) => (sel === '[data-eventlog-action]' ? [copyBtn] : []);
wireEventLogActions();
copyBtn.__click();
out({
  rowCount: sections.reduce((n, s) => n + s.rows.length, 0),
  renderedRows: (xpBodyEl.innerHTML.match(/class="xp-row"/g) || []).length,
  hasCopyControl: xpBodyEl.innerHTML.indexOf('data-eventlog-action="copy"') !== -1,
  copied: copied,
});
"""
    )


def test_the_event_log_offers_copy_all_and_copies_every_row_it_shows() -> None:
    """The event log is a flat list -- there is nothing to disclose, so Copy all is the control that
    applies to it. It is built from the same data the rows are, so it can neither miss nor invent one."""
    result = _drive_event_log()
    assert result["errors"] == []
    assert result["hasCopyControl"]
    assert result["rowCount"] == 3
    assert result["renderedRows"] == result["rowCount"], (
        "the rendered rows and the copied rows come from different places"
    )
    for line in ("[scope_resolved] /tmp/ws", "[tool_selected] Running workspace.write_file.", "[workspace_mutation_completed] wrote src/alpha.py"):
        assert line in result["copied"], f"Copy all dropped a row: {line!r}"
    assert result["copied"].startswith("VOOL Event log — this chat")


# --------------------------------------------------------------------------------------------
# Scope selector position (TASK C): preserved, default 'Current chat', directly under the header.
# --------------------------------------------------------------------------------------------


def test_the_scope_selector_sits_directly_beneath_the_activity_header() -> None:
    head = HTML.index('<div class="xp-head">')
    scope_row = HTML.index('<div class="xp-scope-row">', head)
    scope_note = HTML.index('<div class="xp-scope-note"', scope_row)
    tabs = HTML.index('<div class="xp-tabs"', scope_note)
    body = HTML.index('<div class="xp-body"', tabs)
    # Nothing may be inserted between the header and the scope row -- the panel title, the scope it
    # applies to, then the tabs and the evidence.
    between = HTML[HTML.index("</div>", head) + len("</div>") : scope_row].strip()
    assert between == "", f"something drifted between the Activity header and the scope row: {between!r}"
    assert head < scope_row < scope_note < tabs < body

    # The bulk controls live INSIDE the tab body, never between the header and the scope row.
    assert '<div class="xp-tree-actions">' not in HTML[head:tabs]


def test_the_three_scopes_are_preserved_and_current_chat_is_the_default() -> None:
    assert "const PANEL_SCOPES = [['chat', 'Current chat'], ['project', 'Current project'], ['all', 'All activity']];" in HTML
    assert "let panelScope = localStorage.getItem('vool_panel_scope') || 'chat'" in HTML
    assert "if (!PANEL_SCOPES.some((s) => s[0] === panelScope)) panelScope = 'chat';" in HTML


def test_the_scope_row_stays_a_single_compact_row_at_every_width() -> None:
    """The panel is a fixed-width column at every breakpoint, and the scope row is a flex row inside
    it -- so the selector cannot spread into a responsive dead-space layout."""
    assert ".xp-scope-row { display:flex; align-items:center; gap:6px; padding:0 12px 8px; }" in HTML
    # The selector is sized by its longest option, not by the row: `flex:1` gave it 271px around a
    # 150px option at the default panel width and 611px around it at a 680px one. `max-width:100%`
    # is what still lets it fill the row on a panel too narrow to hold that option.
    assert ".xp-scope { flex:0 1 auto; width:auto; max-width:100%; min-width:0;" in HTML
    assert "#xpanel { flex:0 0 340px; width:340px;" in HTML
    # The only breakpoint that touches the panel repositions the whole column; it does not restyle
    # or reorder the scope row.
    assert "#xpanel { position:fixed; right:0; top:var(--app-header-h,54px); bottom:0; z-index:25; box-shadow:-2px 0 12px #0008; }" in HTML
    # The panel is offset below the header rather than starting at the top of the window, because a
    # `top:0` overlay covered the header's own Activity toggle -- the control that closes it.
    assert "#xpanel { position:fixed; right:0; top:0;" not in HTML
    assert ".xp-scope-row" not in HTML[HTML.index("@media (max-width: 980px)") :]
