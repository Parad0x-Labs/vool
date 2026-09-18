"""The chat page must actually RUN -- not merely contain the right substrings.

The page is ~3,500 lines of inline JavaScript with no module system, and every other test of it
either asserts on source text or lifts one function out for a unit drive. Neither notices an
identifier that was renamed in one place and left dangling in another: `node --check` only parses,
and pytest never evaluates the script, so a `ReferenceError` on a path no unit test slices reaches
the user instead of CI.

That is not hypothetical. DISPATCHER phase 1 moved `_sessionStickyModel` into the per-chat bucket
and left two reads of the old name inside `reflectModel()` -- the model pill's "Auto is sticking to
this chat's cloud model" branch. Every suite stayed green; the composer would have thrown the first
time Auto landed on a free cloud model. This test is that gap closed: it boots the whole script
under node against a DOM complete enough to execute it, then drives the render/lifecycle entry
points that unit slices do not reach, and fails on any error including an unhandled rejection.

It is a smoke test and says so: passing means the code runs and its identifiers resolve, not that
any behaviour is correct. Behaviour is asserted in the focused suites.
"""

from __future__ import annotations

from tests.chat_page_js_harness import DOM, HTML, run_node, script

__all__ = ["HTML"]


# Entry points a unit slice never reaches. Each is driven for real; a dangling identifier anywhere
# in them surfaces here as a named failure rather than as a blank pill in front of the user.
DRIVE = """
const drove = globalThis.__drove;
function drive(name, fn) { try { fn(); drove.push(name); } catch (e) { errors.push(name + ': ' + ((e && e.message) || e)); } }

drive('reflectMode', () => reflectMode());
drive('reflectModel', () => reflectModel());
drive('renderContextBar', () => renderContextBar());
drive('updatePinCount', () => updatePinCount());
drive('renderPinPanel', () => renderPinPanel());
drive('showEmpty', () => showEmpty());
drive('addMsg', () => addMsg('assistant', 'hello **world** `x` https://example.com', new Date().toISOString()));
drive('renderSessions', () => renderSessions([{ session_id: displayedChat, title: 'T' }]));
drive('renderQueue', () => renderQueue([{ status: 'pending', queue_item_id: 'q1', payload: { text: 'x' } }]));
drive('newChat', () => newChat());
drive('buildTabs', () => buildTabs());
drive('openPanel', () => openPanel());
drive('renderPanel', () => renderPanel());
drive('renderPanelBody', () => renderPanelBody());
drive('visibleTabs', () => visibleTabs());
drive('updateTabCounts', () => updateTabCounts());
drive('currentActivityOpenStore', () => currentActivityOpenStore());
drive('closePanel', () => closePanel());

// The model pill's Auto-stickiness branch -- the exact path that was left dangling.
drive('rememberStickyModel', () => rememberStickyModel(displayedChat, { lane: 'cloud', model_id: 'a/b:free', paid: false }));
drive('effectiveModel', () => effectiveModel(displayedChat, 'a real question about something'));
drive('reflectModel:sticky', () => reflectModel());

// A full run lifecycle on the displayed chat, then on a background chat.
drive('adoptRun', () => adoptRun(displayedChat, newRun(displayedChat)));
drive('buildCard', () => buildCard(view.run, null));
drive('applyTaskEvent', () => {
  applyTaskEvent(view.run, { type: 'tool.started', tool: 'read_file', summary: 'Reading', stage: 'Reading' });
  applyTaskEvent(view.run, { type: 'tool.completed', tool: 'read_file', summary: 'Read', stage: 'Reading', path: '/x' });
  applyTaskEvent(view.run, { type: 'permission.required', summary: 'Approve', approval: { approval_id: 'a1', scope_options: ['once', 'task'] } });
  applyTaskEvent(view.run, { type: 'verification.completed', status: 'passed' });
});
drive('buildTurnRequestBody', () => buildTurnRequestBody(view.run, 'vool'));
drive('finishRun', () => finishRun(view.run, 'completed', 'Complete', new Date().toISOString()));
drive('releaseComposer', () => releaseComposer(view.run));
drive('showPermBar', () => showPermBar(displayedChat, { approval: { approval_id: 'a2', scope_options: ['once'] }, summary: 's' }));
drive('hidePermBar', () => hidePermBar());
drive('resumeApprovedTurn', () => resumeApprovedTurn(displayedChat));
drive('background run', () => {
  const other = 'openclaw:' + 'b'.repeat(20);
  const run = adoptRun(other, newRun(other));
  buildCard(run, null);
  applyTaskEvent(run, { type: 'tool.started', tool: 'edit_file', summary: 'Editing', stage: 'Editing' });
  finishRun(run, 'failed', 'Connection error', new Date().toISOString());
  releaseComposer(run);
});
drive('renderActivityTree', () => renderActivityTree(
  [{ seq: 1, event_type: 'tool_selected', message: 'x', tool_name: 'read_file' },
   { seq: 2, event_type: 'tool_executed', message: 'y', tool_name: 'read_file' }], {}, { ended: true }));
drive('setDisplayedChat', () => setDisplayedChat('openclaw:' + 'c'.repeat(20)));
drive('renderRichText', () => renderRichText(document.createElement('div'), '# H\\n\\n- a\\n- b\\n\\n```js\\nx\\n```'));

// Let the boot-time fetches settle so an unhandled rejection is attributed here, not swallowed.
// `_st` is the un-unref'd original: this timer is the one thing that must keep node alive.
_st(() => { __report(); process.exit(0); }, 400);
"""


def _boot() -> dict:
    return run_node(DOM + script() + DRIVE)


def test_the_chat_page_boots_and_every_driven_path_resolves_its_identifiers() -> None:
    data = _boot()
    assert data["errors"] == [], "the chat page threw while booting or being driven:\n" + "\n".join(data["errors"])
    # A harness that silently drove nothing would report zero errors too.
    assert len(data["drove"]) >= 25, f"only drove {len(data['drove'])} entry points: {data['drove']}"


def test_the_boot_smoke_actually_fails_on_a_dangling_identifier() -> None:
    """Anti-vacuity: reintroduce the exact defect this test exists for and require it to be caught.

    `reflectModel()` reads the chat's sticky model out of its bucket. Point that read back at the
    global name the state used to live under -- the mistake that shipped green through every other
    suite -- and this harness must name reflectModel as throwing.
    """
    source = script()
    marker = "if (modelValue === 'vool' && view.stickyModel) {"
    assert marker in source, "reflectModel no longer has the shape this sabotage targets"
    sabotaged = source.replace(marker, "if (modelValue === 'vool' && _sessionStickyModel) {", 1)
    errors = run_node(DOM + sabotaged + DRIVE)["errors"]
    assert any("reflectModel" in e for e in errors), (
        "SABOTAGE DID NOT BITE: a dangling identifier in reflectModel must be caught by this "
        f"harness. Errors seen: {errors}"
    )
