"""Sidebar chat/project lifecycle indicators: WORKING / NEEDS_USER / COMPLETE_UNSEEN.

The mission: a chat with an active turn must be obviously "cooking" in the sidebar, a chat that
finished while the operator was elsewhere must show as done-but-unseen until they actually look at
it, and a chat stuck on an approval/permission decision must be unmistakably louder than either --
without opening every chat to find out.

Two tiers, mirroring how this file already tests itself elsewhere (see
tests/test_multichat_per_chat_state.py, tests/test_chat_page_boots_under_node.py):

* TIER A drives `chatLifecycleState()` alone -- the pure, DOM-free priority rule that decides
  needs_user > working > complete_unseen > idle. It is lifted out of the real page source the same
  way DISPATCHER is lifted in test_multichat_per_chat_state.py, and driven through a wide matrix of
  concrete scenarios: the states named in the brief, plus adversarial near-misses the brief does not
  spell out (a stale `running` row from a crashed worker, a mid-turn permission request that has not
  yet ended the run, an `interrupted` row that does NOT offer resume, a completed run whose
  `seenAt` predates it, failed/cancelled runs that must never read as an unseen completion).

* TIER B boots the whole inline script under node (same DOM stub as test_chat_page_boots_under_node)
  and drives the real rendering path -- `renderSessions`, `makeSessionItem`, `makeProjectHead`,
  `openSession`, `markChatSeen` -- so the contracts about SCOPING and AGGREGATION are proven against
  actual DOM nodes, not against a mocked copy of the render function.

Sabotage (`test_mutation_*`): each requires a one-line regression in the real source to make the
corresponding contract test fail, proving the assertion is load-bearing rather than vacuously true.
"""

from __future__ import annotations

from tests.chat_page_js_harness import DOM, run_node, script, slice_source

LIFECYCLE_BEGIN = "function chatLifecycleState(chatId, opts) {"
LIFECYCLE_END = "// ==================== LIFECYCLE: sidebar indicator priority — END ===================="


def _lifecycle_js() -> str:
    """The pure priority function, verbatim. Self-contained by construction (no DOM, no fetch)."""
    return slice_source(script(), LIFECYCLE_BEGIN, LIFECYCLE_END)


_DRIVER = r"""
function priorityCase(chatId, bucket, serverRow, extra) {
  const cs = {};
  if (bucket) cs[String(chatId)] = bucket;
  const opts = Object.assign(
    { chatStates: cs, serverRow: serverRow || null, seenAt: '', displayedChat: '', hidden: false },
    extra || {}
  );
  return chatLifecycleState(chatId, opts);
}
console.log(JSON.stringify({
  // ---- named states from the brief ----
  idle_never_touched: priorityCase('c-idle', null, null),
  working_client_pumphold: priorityCase('c-w1', { pumpHold: true, run: null }, null),
  working_client_run_in_flight: priorityCase('c-w2', { run: { status: 'running', ended: false, permission: false } }, null),
  working_then_complete: (() => {
    // The exact same bucket, transitioned in place -- "working -> complete" as one continuous run.
    const bucket = { run: { status: 'running', ended: false, permission: false } };
    const mid = priorityCase('c-w3', bucket, null);
    bucket.run.ended = true; bucket.run.status = 'completed'; bucket.run.endedAt = '2026-08-12T09:00:00Z';
    const after = priorityCase('c-w3', bucket, null, { seenAt: '' });
    return { mid, after };
  })(),
  complete_unseen_client: priorityCase('c-u1', { run: { status: 'completed', ended: true, endedAt: '2026-08-12T09:00:00Z' } }, null, { seenAt: '' }),
  complete_unseen_server_row: priorityCase('c-u2', null, { status: 'completed', updated_at: '2026-08-12T09:00:00Z' }, { seenAt: '' }),
  complete_viewed_seen_after_done: priorityCase('c-v1', { run: { status: 'completed', ended: true, endedAt: '2026-08-12T09:00:00Z' } }, null, { seenAt: '2026-08-12T09:05:00Z' }),
  needs_approval_client_terminal: priorityCase('c-a1', { run: { status: 'awaiting_approval', ended: true, permission: true, endedAt: '2026-08-12T09:00:00Z' } }, null),
  needs_approval_server_pending: priorityCase('c-a2', null, { status: 'pending_approval' }),
  needs_confirmation_interrupted_resumable: priorityCase('c-a3', null, { status: 'interrupted', resume_available: true }),
  two_chats_different_states: {
    a: priorityCase('c-x', { run: { status: 'running', ended: false } }, null),
    b: priorityCase('c-y', { run: { status: 'awaiting_approval', ended: true, permission: true } }, null),
  },
  background_working_while_selected_idle: (() => {
    const cs = {
      'displayed': { run: null },
      'background': { run: { status: 'running', ended: false } },
    };
    return {
      displayed: chatLifecycleState('displayed', { chatStates: cs, displayedChat: 'displayed', hidden: false }),
      background: chatLifecycleState('background', { chatStates: cs, displayedChat: 'displayed', hidden: false }),
    };
  })(),
  rapid_transition_running_permission_then_resumed: (() => {
    // running -> permission mid-turn (NOT ended yet) -> resumed (tool starts again, permission clears)
    const bucket = { run: { status: 'running', ended: false, permission: false } };
    const step1 = priorityCase('c-r1', bucket, null);
    bucket.run.permission = true;                       // permission.required fires mid-stream
    const step2 = priorityCase('c-r1', bucket, null);
    bucket.run.permission = false;                       // tool.started clears it on resume
    const step3 = priorityCase('c-r1', bucket, null);
    bucket.run.ended = true; bucket.run.status = 'completed'; bucket.run.endedAt = '2026-08-12T09:00:00Z';
    const step4 = priorityCase('c-r1', bucket, null, { seenAt: '' });
    return { step1, step2, step3, step4 };
  })(),

  // ---- adversarial near-misses, not named in the brief ----
  stale_running_row_worker_dead: priorityCase('c-adv1', null, { status: 'running', worker_live: false }),
  running_row_worker_live_true: priorityCase('c-adv2', null, { status: 'running', worker_live: true }),
  interrupted_not_resumable_is_not_needs_user: priorityCase('c-adv3', null, { status: 'interrupted', resume_available: false }),
  mid_turn_permission_beats_working: priorityCase('c-adv4', { run: { status: 'running', ended: false, permission: true } }, null),
  needs_user_beats_working_when_both_present: priorityCase(
    'c-adv5',
    { run: { status: 'awaiting_approval', ended: true, permission: true }, pumpHold: true },
    { status: 'running', worker_live: true }
  ),
  failed_run_is_not_an_unseen_completion: priorityCase('c-adv6', { run: { status: 'failed', ended: true, endedAt: '2026-08-12T09:00:00Z' } }, null, { seenAt: '' }),
  cancelled_run_is_not_an_unseen_completion: priorityCase('c-adv7', { run: { status: 'cancelled', ended: true, endedAt: '2026-08-12T09:00:00Z' } }, null, { seenAt: '' }),
  server_failed_row_is_not_an_unseen_completion: priorityCase('c-adv8', null, { status: 'failed', updated_at: '2026-08-12T09:00:00Z' }, { seenAt: '' }),
  seen_before_done_is_still_unseen: priorityCase('c-adv9', { run: { status: 'completed', ended: true, endedAt: '2026-08-12T09:00:00Z' } }, null, { seenAt: '2026-08-12T08:00:00Z' }),
  displayed_but_tab_hidden_is_still_unseen: priorityCase('c-adv10', { run: { status: 'completed', ended: true, endedAt: '2026-08-12T09:00:00Z' } }, null, { seenAt: '', displayedChat: 'c-adv10', hidden: true }),
  displayed_and_visible_is_never_unseen: priorityCase('c-adv11', { run: { status: 'completed', ended: true, endedAt: '2026-08-12T09:00:00Z' } }, null, { seenAt: '', displayedChat: 'c-adv11', hidden: false }),
  current_client_turn_beats_stale_completed_server_row: priorityCase(
    'c-adv12',
    { run: { status: 'awaiting_approval', ended: true, permission: true } },
    { status: 'completed', updated_at: '2026-08-12T09:00:00Z' }
  ),
  empty_chat_id_is_idle: priorityCase('', { run: { status: 'running', ended: false } }, null),
  no_state_at_all_is_idle: priorityCase('c-nothing', null, null),
  numeric_chat_id_coerces_to_string_key: chatLifecycleState(4242, { chatStates: { '4242': { run: { status: 'running', ended: false } } } }),
}));
"""


def _priority_cases(source: str | None = None) -> dict:
    js = source if source is not None else _lifecycle_js()
    result = run_node(js + _DRIVER)
    # run_node parses the LAST json line of stdout; this driver's only output IS that line, via a
    # bare console.log rather than the DOM harness's out()/errors machinery (there is no DOM here).
    return result


# ============================================================== TIER A: pure priority rule


def test_named_states_from_the_brief() -> None:
    c = _priority_cases()
    assert c["idle_never_touched"] == ""
    assert c["working_client_pumphold"] == "working"
    assert c["working_client_run_in_flight"] == "working"
    assert c["working_then_complete"]["mid"] == "working"
    assert c["working_then_complete"]["after"] == "complete_unseen"
    assert c["complete_unseen_client"] == "complete_unseen"
    assert c["complete_unseen_server_row"] == "complete_unseen"
    assert c["complete_viewed_seen_after_done"] == ""
    assert c["needs_approval_client_terminal"] == "needs_user"
    assert c["needs_approval_server_pending"] == "needs_user"
    assert c["needs_confirmation_interrupted_resumable"] == "needs_user"


def test_two_chats_carry_independent_states() -> None:
    c = _priority_cases()
    assert c["two_chats_different_states"]["a"] == "working"
    assert c["two_chats_different_states"]["b"] == "needs_user"


def test_a_background_chat_can_be_busy_while_the_selected_one_is_idle() -> None:
    c = _priority_cases()
    both = c["background_working_while_selected_idle"]
    assert both["displayed"] == "", "the idle, on-screen chat must not inherit the other one's state"
    assert both["background"] == "working"


def test_rapid_transitions_resolve_to_the_correct_state_at_every_step() -> None:
    c = _priority_cases()["rapid_transition_running_permission_then_resumed"]
    assert c["step1"] == "working"
    assert c["step2"] == "needs_user", "a mid-turn permission request must flip the badge immediately"
    assert c["step3"] == "working", "resuming after approval must drop back to working"
    assert c["step4"] == "complete_unseen"


def test_adversarial_near_misses_are_not_mistaken_for_the_real_thing() -> None:
    c = _priority_cases()
    assert c["stale_running_row_worker_dead"] == "", "a crashed worker's stale 'running' row must not read as active"
    assert c["running_row_worker_live_true"] == "working"
    assert c["interrupted_not_resumable_is_not_needs_user"] == "", "an interrupted row with no resume offer is not a pending decision"
    assert c["mid_turn_permission_beats_working"] == "needs_user"
    assert c["needs_user_beats_working_when_both_present"] == "needs_user"
    assert c["failed_run_is_not_an_unseen_completion"] == "", "a failed run is not a completion"
    assert c["cancelled_run_is_not_an_unseen_completion"] == ""
    assert c["server_failed_row_is_not_an_unseen_completion"] == ""
    assert c["seen_before_done_is_still_unseen"] == "complete_unseen", "seenAt must be compared, not just checked for presence"
    assert c["displayed_but_tab_hidden_is_still_unseen"] == "complete_unseen"
    assert c["displayed_and_visible_is_never_unseen"] == ""
    assert c["current_client_turn_beats_stale_completed_server_row"] == "needs_user"
    assert c["empty_chat_id_is_idle"] == ""
    assert c["no_state_at_all_is_idle"] == ""
    assert c["numeric_chat_id_coerces_to_string_key"] == "working"


# ============================================================== TIER A sabotage


def test_mutation_priority_order_is_load_bearing() -> None:
    """Swap the two priority blocks whole (const declarations travel with their `if`, or the
    swap would reference a not-yet-initialized const); a mid-turn permission request must then
    wrongly read as working."""
    source = _lifecycle_js()
    nu_block = (
        "  // NEEDS_USER -- checked first: a run stuck on 'awaiting_approval' may already have released its\n"
        "  // busy slot (the composer is meant to stay usable while a permission request is pending), so it\n"
        "  // must never fall through to WORKING, and it is never \"done\" until the operator actually answers.\n"
        "  const clientNeedsUser = !!(run && (run.status === 'awaiting_approval' || (run.permission && !run.ended)));\n"
        "  const serverNeedsUser = !!row && (row.status === 'pending_approval'\n"
        "    || (row.status === 'interrupted' && row.resume_available === true));\n"
        "  if (clientNeedsUser || serverNeedsUser) return 'needs_user';"
    )
    working_block = (
        "  // WORKING -- a turn is genuinely executing right now. A server row can read `status: 'running'`\n"
        "  // for a worker that has since crashed (list_runtime_sessions() in runtime_continuity.py calls\n"
        "  // this out explicitly); `worker_live` is what turns that row into real evidence instead of a\n"
        "  // stale flag left over from a dead process.\n"
        "  const clientWorking = !!(st && (st.pumpHold || (run && !run.ended)));\n"
        "  const serverWorking = !!row && row.status === 'running' && row.worker_live === true;\n"
        "  if (clientWorking || serverWorking) return 'working';"
    )
    assert nu_block in source and working_block in source, "chatLifecycleState no longer has the shape this sabotage targets"
    sabotaged = source.replace(nu_block, "@@NU_PLACEHOLDER@@").replace(working_block, nu_block)
    sabotaged = sabotaged.replace("@@NU_PLACEHOLDER@@", working_block)
    cases = _priority_cases(sabotaged)
    assert cases["mid_turn_permission_beats_working"] == "working", (
        "SABOTAGE DID NOT BITE: reordering the priority checks must flip this case from "
        "'needs_user' to 'working', or the priority-order test below is not actually load-bearing"
    )


def test_mutation_worker_live_guard_is_load_bearing() -> None:
    """Drop the worker_live guard; a crashed worker's stale 'running' row must then read as active."""
    source = _lifecycle_js()
    marker = "row.status === 'running' && row.worker_live === true"
    assert marker in source
    sabotaged = source.replace(marker, "row.status === 'running'")
    cases = _priority_cases(sabotaged)
    assert cases["stale_running_row_worker_dead"] == "working", (
        "SABOTAGE DID NOT BITE: removing the worker_live guard must make a dead process's stale "
        "'running' row read as active, or that guard is not actually load-bearing"
    )


# ============================================================== TIER B: real render path

_RENDER_DRIVER = r"""
const drove = globalThis.__drove;
function drive(name, fn) { try { fn(); drove.push(name); } catch (e) { errors.push(name + ': ' + ((e && e.message) || e)); } }
const RESULTS = {};

// The harness's El stub does not implement Node.contains() (nothing exercised it before now); the
// real script already relies on it elsewhere (e.g. xpBodyEl.contains(details)), so this is a gap in
// the test double, not a reason to avoid a standard DOM method in production code. Polyfilled here,
// scoped to this file's own driver, rather than widening the shared harness for every other suite.
if (typeof sessionsEl.contains !== 'function') {
  sessionsEl.contains = function (node) {
    let found = false;
    (function walk(n) { if (!n || found) return; if (n === node) { found = true; return; } (n.children || []).forEach(walk); })(sessionsEl);
    return found;
  };
}

function badgeOf(row) {
  if (!row || !row.children) return null;
  const el = row.children.find((c) => c && c.className && String(c.className).indexOf('lc-badge') !== -1);
  return el ? { cls: el.className, text: el.textContent } : null;
}
function rowsByClass(cls) {
  return (sessionsEl.children || []).filter((c) => c && c.className && String(c.className).split(' ').indexOf(cls) !== -1);
}
// Archived rows nest one level down (inside the collapsed archive box), so lookups need a real tree
// walk rather than a scan of #sessions' direct children.
function findInTree(root, pred) {
  let found = null;
  (function walk(n) { if (found || !n) return; if (pred(n)) { found = n; return; } (n.children || []).forEach(walk); })(root);
  return found;
}
// makeSessionItem never stamps the id onto its row, so tag it here for lookup rather than
// re-deriving from the title text (titles can collide).
const origMakeSessionItem = makeSessionItem;
makeSessionItem = function (s, archived, nested) {
  const el = origMakeSessionItem(s, archived, nested);
  el.__sid = s.session_id;
  return el;
};
function sessionRow(sid) { return findInTree(sessionsEl, (c) => c.__sid === sid); }

const A = 'openclaw:' + 'a'.repeat(20);
const B = 'openclaw:' + 'b'.repeat(20);
const PROJ_BUSY = 'proj_busy';
const PROJ_IDLE = 'proj_idle';

drive('scoping: correct chat only', () => {
  chatStates[A] = { run: { status: 'running', ended: false } };
  chatStates[B] = { run: null };
  renderSessions([
    { session_id: A, title: 'A', project_id: '', archived: false },
    { session_id: B, title: 'B', project_id: '', archived: false },
  ]);
  RESULTS.scoping = { a: badgeOf(sessionRow(A)), b: badgeOf(sessionRow(B)) };
});

drive('archived rows carry no badge', () => {
  chatStates[A] = { run: { status: 'running', ended: false } };
  renderSessions([{ session_id: A, title: 'A', project_id: '', archived: true }]);
  const archRow = sessionRow(A);
  RESULTS.archivedBadge = archRow ? badgeOf(archRow) : 'no-row-found';
});

drive('project aggregation from member state, not a global flag', () => {
  const busyChat = 'openclaw:' + 'c'.repeat(20);
  const idleChat1 = 'openclaw:' + 'd'.repeat(20);
  const idleChat2 = 'openclaw:' + 'e'.repeat(20);
  chatStates[busyChat] = { run: { status: 'running', ended: false } };
  chatStates[idleChat1] = { run: null };
  chatStates[idleChat2] = { run: null };
  _serverProjects = {
    [PROJ_BUSY]: { id: PROJ_BUSY, name: 'Busy Project' },
    [PROJ_IDLE]: { id: PROJ_IDLE, name: 'Idle Project' },
  };
  renderSessions([
    { session_id: busyChat, title: 'busy', project_id: PROJ_BUSY, archived: false },
    { session_id: idleChat1, title: 'idle1', project_id: PROJ_IDLE, archived: false },
    { session_id: idleChat2, title: 'idle2', project_id: PROJ_IDLE, archived: false },
  ]);
  const heads = rowsByClass('proj-head');
  const busyHead = heads.find((h) => h.children.some((k) => k.className === 'proj-name' && k.textContent === 'Busy Project'));
  const idleHead = heads.find((h) => h.children.some((k) => k.className === 'proj-name' && k.textContent === 'Idle Project'));
  RESULTS.projectAggregation = { busy: badgeOf(busyHead), idle: badgeOf(idleHead) };
  _serverProjects = {};
});

drive('General aggregation reflects only unassigned chats', () => {
  chatStates[A] = { run: { status: 'awaiting_approval', ended: true, permission: true } };
  renderSessions([{ session_id: A, title: 'A', project_id: '', archived: false }]);
  const gen = (sessionsEl.children || []).find((c) => c.className && c.className.indexOf('general') !== -1);
  RESULTS.generalAggregation = badgeOf(gen);
});

drive('completed must not appear while still running', () => {
  chatStates[A] = { run: { status: 'running', ended: false } };
  renderSessions([{ session_id: A, title: 'A', project_id: '', archived: false }]);
  RESULTS.runningNotUnseen = badgeOf(sessionRow(A));
});

drive('needs_user survives an unrelated background completion', () => {
  chatStates[A] = { run: { status: 'awaiting_approval', ended: true, permission: true } };
  chatStates[B] = { run: { status: 'completed', ended: true, endedAt: '2026-08-12T09:00:00Z' } };
  renderSessions([
    { session_id: A, title: 'A', project_id: '', archived: false },
    { session_id: B, title: 'B', project_id: '', archived: false },
  ]);
  const before = badgeOf(sessionRow(A));
  // An unrelated background event: B's completion is already reflected above; re-render again as if
  // a timer tick fired after B finished, and A must be untouched.
  renderSessions([
    { session_id: A, title: 'A', project_id: '', archived: false },
    { session_id: B, title: 'B', project_id: '', archived: false },
  ]);
  RESULTS.needsUserSurvives = { before: before, after: badgeOf(sessionRow(A)) };
});

drive('opening a chat clears its own unseen badge, not others', () => {
  chatStates[A] = { run: { status: 'completed', ended: true, endedAt: '2026-08-12T09:00:00Z' } };
  chatStates[B] = { run: { status: 'completed', ended: true, endedAt: '2026-08-12T09:00:00Z' } };
  renderSessions([
    { session_id: A, title: 'A', project_id: '', archived: false },
    { session_id: B, title: 'B', project_id: '', archived: false },
  ]);
  const beforeA = badgeOf(sessionRow(A));
  const beforeB = badgeOf(sessionRow(B));
  markChatSeen(A);
  renderSessions([
    { session_id: A, title: 'A', project_id: '', archived: false },
    { session_id: B, title: 'B', project_id: '', archived: false },
  ]);
  RESULTS.seenOnOpen = { beforeA: beforeA, beforeB: beforeB, afterA: badgeOf(sessionRow(A)), afterB: badgeOf(sessionRow(B)) };
});

drive('maybeRepaintSidebarLifecycle never blows away an in-progress rename', () => {
  chatStates[A] = { run: null };
  renderSessions([{ session_id: A, title: 'A', project_id: '', archived: false }]);
  const before = sessionsEl.children.length;
  const fakeInput = document.createElement('input');
  document.activeElement = fakeInput;
  sessionsEl.appendChild(fakeInput);   // simulate startRename() having swapped a title for an input
  const beforeChildren = sessionsEl.children.slice();
  chatStates[A] = { run: { status: 'running', ended: false } };   // real state changed mid-rename
  maybeRepaintSidebarLifecycle();
  RESULTS.renameGuard = { untouched: sessionsEl.children === beforeChildren || sessionsEl.children.length === beforeChildren.length };
  document.activeElement = null;
});

drive('maybeRepaintSidebarLifecycle repaints once state actually changes', () => {
  chatStates[A] = { run: null };
  _lastSessions = [{ session_id: A, title: 'A', project_id: '', archived: false }];
  renderSessions(_lastSessions);
  _lifecycleSnapshot = '';   // force the next tick to compute fresh rather than reuse a cached key
  maybeRepaintSidebarLifecycle();
  const idleBadge = badgeOf(sessionRow(A));
  chatStates[A] = { run: { status: 'running', ended: false } };
  maybeRepaintSidebarLifecycle();
  RESULTS.repaintOnRealChange = { idle: idleBadge, afterChange: badgeOf(sessionRow(A)) };
});

out(RESULTS);
"""


def _render_cases() -> dict:
    return run_node(DOM + script() + _RENDER_DRIVER)


def test_indicator_is_scoped_to_the_correct_chat() -> None:
    cases = _render_cases()
    assert cases["errors"] == [], cases["errors"]
    scoping = cases["scoping"]
    assert scoping["a"] and "lc-working" in scoping["a"]["cls"]
    assert scoping["b"] is None, f"chat B must carry no badge while idle, got {scoping['b']}"


def test_archived_rows_never_carry_a_lifecycle_badge() -> None:
    cases = _render_cases()
    assert cases["archivedBadge"] is None, cases["archivedBadge"]


def test_project_aggregation_is_derived_from_member_chats_not_a_global_flag() -> None:
    cases = _render_cases()
    agg = cases["projectAggregation"]
    assert agg["busy"] and "lc-working" in agg["busy"]["cls"]
    assert agg["idle"] is None, (
        f"the idle project must show nothing even though another project is busy, got {agg['idle']}"
    )


def test_general_group_aggregates_only_its_own_unassigned_chats() -> None:
    cases = _render_cases()
    assert cases["generalAggregation"] and "lc-needs" in cases["generalAggregation"]["cls"]


def test_completed_badge_never_appears_while_the_run_is_still_going() -> None:
    cases = _render_cases()
    assert cases["runningNotUnseen"] and "lc-working" in cases["runningNotUnseen"]["cls"]


def test_needs_user_is_not_cleared_by_an_unrelated_background_completion() -> None:
    cases = _render_cases()
    surv = cases["needsUserSurvives"]
    assert surv["before"] and "lc-needs" in surv["before"]["cls"]
    assert surv["after"] and "lc-needs" in surv["after"]["cls"], (
        "chat B finishing in the background must not clear chat A's needs_user badge"
    )


def test_opening_a_chat_clears_only_its_own_unseen_badge() -> None:
    cases = _render_cases()
    seen = cases["seenOnOpen"]
    assert seen["beforeA"] and "lc-unseen" in seen["beforeA"]["cls"]
    assert seen["beforeB"] and "lc-unseen" in seen["beforeB"]["cls"]
    assert seen["afterA"] is None, "opening A must clear A's own unseen badge"
    assert seen["afterB"] and "lc-unseen" in seen["afterB"]["cls"], "opening A must not clear B's unseen badge"


def test_repaint_never_destroys_an_in_progress_rename() -> None:
    cases = _render_cases()
    assert cases["renameGuard"]["untouched"] is True


def test_repaint_reflects_a_real_state_change() -> None:
    cases = _render_cases()
    r = cases["repaintOnRealChange"]
    assert r["idle"] is None
    assert r["afterChange"] and "lc-working" in r["afterChange"]["cls"]


# ============================================================== TIER B sabotage


def test_mutation_cross_chat_scoping_is_load_bearing() -> None:
    """Point the per-chat lookup at whatever chat is displayed instead of the chat being asked
    about (displayedChat set to a THIRD, untouched chat, so the sabotaged lookup can never
    coincidentally land on the right bucket); a background chat's real activity must then go
    invisible, proving the scoping assertions above are load-bearing rather than vacuous."""
    source = script()
    marker = "const st = o.chatStates ? o.chatStates[sid] : null;"
    assert marker in source
    sabotaged = source.replace(marker, "const st = o.chatStates ? o.chatStates[o.displayedChat] : null;", 1)
    driver = r"""
const cs = { 'displayed-elsewhere': { run: null }, 'background': { run: { status: 'running', ended: false } } };
console.log(JSON.stringify({
  background: chatLifecycleState('background', { chatStates: cs, displayedChat: 'displayed-elsewhere', hidden: false }),
}));
"""
    lifecycle_only = slice_source(sabotaged, LIFECYCLE_BEGIN, LIFECYCLE_END)
    result = run_node(lifecycle_only + driver)
    assert result["background"] != "working", (
        "SABOTAGE DID NOT BITE: keying the per-chat lookup off displayedChat instead of the row's "
        "own id must make a background chat's real activity invisible, or cross-chat scoping is "
        "not actually load-bearing"
    )
