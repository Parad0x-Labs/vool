"""Guards for truthful, independent terminal/evidence labels on the chat status card."""

from __future__ import annotations

from tests.chat_page_js_harness import DOM, HTML, run_node, script


def _presentation_cases() -> dict:
    return run_node(
        DOM
        + script()
        + """
function cardRun(overrides) {
  const card = { classList: { add() {}, toggle() {} } };
  const refs = {
    card, stop: { style: {} }, title: { textContent: '' }, stage: { textContent: '' },
    elapsed: { innerHTML: '' }, summary: { textContent: '' }, last: { textContent: '' },
    sr: { textContent: '' }, view: { style: {} },
  };
  return Object.assign({
    status: 'completed', start: Date.now() - 10, endSummary: 'Done', steps: [], events: [],
    ledger: [], files: {}, tests: { done: 0, total: 0, failed: 0 }, model: null, cost: null,
    permission: false, verifierPassed: false, verifierFailed: false, reviewState: 'not_run', refs, card,
  }, overrides || {});
}
function activityPanel(overrides) {
  view.run = cardRun(overrides);
  document.body.classList.add('panel-open');
  panelTab = 'Activity';
  // renderPanelBody skips rebuilds with an unchanged signature; panels built in the same
  // millisecond (same chatId/start/counts) would all render the FIRST one's bytes. Reset
  // the skip guard so each case renders its own run — same law the live poll obeys by
  // only skipping when the signature is truly unchanged for the SAME run.
  lastActivityBodySig = '';
  if (xpBodyEl && xpBodyEl.dataset) xpBodyEl.dataset.activityRendered = '0';
  renderPanelBody();
  return document.getElementById('xpBody').innerHTML;
}
const reviewed = cardRun({
  reviewState: 'passed',
  tests: { done: 1, total: 1, failed: 0 },
  events: [{ type: 'verification.completed' }],
});
paintFinishedCard(reviewed);
function reducedReview(event) {
  const run = cardRun({
    ended: true, status: 'completed', refs: null, card: null, eventSeen: {}, eventSeq: 0,
    chatId: 'background-review', reviewState: 'not_run', verifierPassed: false, verifierFailed: false,
  });
  applyTaskEvent(run, Object.assign({ type: 'verification.completed' }, event));
  return {
    reviewState: run.reviewState, status: run.status, ended: run.ended, last: run.last,
    verifierPassed: run.verifierPassed, verifierFailed: run.verifierFailed,
    evidence: evidenceLabels(run),
  };
}
function savedActivityReview(state) {
  // The cross-chat history is a WIDER-scope view since QA-050-025 (chat scope deliberately renders
  // none of it), so the saved-card renderer is exercised in the scope that actually shows it.
  panelScope = 'all';
  const sessionId = 'saved-' + state;
  activityHistory = [{
    session_id: sessionId, status: 'completed', request_preview: 'Saved review probe',
    execution_history: {
      status: 'completed', request_preview: 'Saved review probe',
      bounded_execution: { model_review_state: state },
    },
  }];
  _lastSessions = [{ session_id: sessionId, title: 'Saved review probe' }];
  return renderActivityHistoryHtml();
}
out({
  completedTitle: reviewed.refs.title.textContent,
  completedEvidence: reviewed.refs.summary.textContent,
  failedActivity: hasMeaningfulActivity(cardRun({ status: 'failed' })),
  approvalActivity: hasMeaningfulActivity(cardRun({ status: 'awaiting_approval', permission: true })),
  reviewActivity: hasMeaningfulActivity(cardRun({ reviewState: 'passed' })),
  routingActivity: hasMeaningfulActivity(cardRun({ events: [{ type: 'model.changed' }] })),
  exactProvider: modelProviderPresentation({ provider_id: 'anthropic', model_id: 'claude-sonnet-4' }),
  providerOnly: modelProviderPresentation({ provider_id: 'provider-only' }),
  modelOnly: modelProviderPresentation({ model_id: 'model-only' }),
  unknownIdentity: modelProviderPresentation({ lane: 'cloud' }),
  requestedProvider: modelProviderPresentation({ requested_model_id: 'user/pinned-model' }),
  selectedProvider: modelProviderPresentation({ selected_provider_id: 'openrouter', selected_model_id: 'anthropic/claude-sonnet-4' }),
  actualProvider: modelProviderPresentation({
    actual_adapter_provider_id: 'ollama', provider_id: 'openrouter',
    actual_adapter_model_id: 'qwen', model_id: 'claude-sonnet-4'
  }),
  conflictingIdentity: modelProviderPresentation({
    provider_id: 'recorded-p', model_id: 'recorded-m',
    requested_provider_id: 'requested-p', requested_model_id: 'requested-m',
    selected_provider_id: 'selected-p', selected_model_id: 'selected-m',
    actual_adapter_provider_id: 'actual-p', actual_adapter_model_id: 'actual-m'
  }),
  conflictingDetails: modelIdentityDetails({
    provider_id: 'recorded-p', model_id: 'recorded-m',
    requested_provider_id: 'requested-p', requested_model_id: 'requested-m',
    selected_provider_id: 'selected-p', selected_model_id: 'selected-m',
    actual_adapter_provider_id: 'actual-p', actual_adapter_model_id: 'actual-m'
  }),
  cloudFallback: modelProviderPresentation({ lane: 'cloud', model_id: 'fallback-model' }),
  unknownLocality: modelProviderPresentation({ lane: 'local', model_id: 'qwen' }),
  activityStates: ['Planning', 'Searching', 'Reading', 'Editing', 'Running', 'Testing'].map(
    (stage) => humanActivityState({ stage, permission: false })
  ),
  unknownActivityState: humanActivityState({ stage: 'Understanding', permission: false }),
  approvalState: humanActivityState({ stage: 'Running', permission: true }),
  testStarted: ledgerRow({ event_type: 'tool_selected', tool_name: 'pytest_runner', tool_args: 'pytest tests/router' }),
  testFinished: ledgerRow({ event_type: 'tool_executed', tool_name: 'pytest_runner', message: 'Test command passed' }),
  rawToolDetails: activityItemDetailLines({
    startEvent: { event_type: 'tool_selected', tool_name: 'pytest_runner', tool_args: 'pytest tests/router' },
    endEvent: { event_type: 'tool_executed', tool_name: 'pytest_runner', message: 'Test command passed' }
  }, 'Test command finished'),
  failurePanel: activityPanel({ status: 'failed', endSummary: 'Provider unavailable' }),
  approvalPanel: activityPanel({ status: 'awaiting_approval', permission: true, action: 'Approve shell command' }),
  reviewPanel: activityPanel({ reviewState: 'passed' }),
  flaggedPanel: activityPanel({ reviewState: 'flagged' }),
  blockedPanel: activityPanel({ reviewState: 'blocked' }),
  degradedPanel: activityPanel({ reviewState: 'degraded' }),
  runtimeFailedPanel: activityPanel({ reviewState: 'runtime_failed' }),
  reviewReducers: {
    passed: reducedReview({ review_state: 'passed', status: 'completed' }),
    flagged: reducedReview({ review_state: 'flagged', status: 'completed' }),
    blocked: reducedReview({ review_state: 'blocked', status: 'blocked' }),
    degraded: reducedReview({ review_state: 'degraded', status: 'degraded' }),
    runtimeFailed: reducedReview({ review_state: 'runtime_failed', status: 'failed' }),
    ambiguousLegacyFailed: reducedReview({ status: 'failed' }),
  },
  savedReviewCards: {
    flagged: savedActivityReview('flagged'),
    blocked: savedActivityReview('blocked'),
    degraded: savedActivityReview('degraded'),
    runtimeFailed: savedActivityReview('runtime_failed'),
    legacyFailed: savedActivityReview('failed'),
  },
});
"""
    )


def test_model_review_is_not_presented_as_generic_verification() -> None:
    assert "function evidenceLabels" in HTML
    assert "Model-reviewed — passed" in HTML
    assert "Review flagged" in HTML
    assert "return 'Verified'" not in HTML
    assert "'Needs review'" not in HTML


def test_live_review_reducer_preserves_verdict_vs_execution_health() -> None:
    cases = _presentation_cases()
    review = cases["reviewReducers"]
    assert review["passed"]["reviewState"] == "passed"
    assert review["passed"]["verifierPassed"] is True
    assert review["flagged"]["reviewState"] == "flagged"
    assert review["flagged"]["verifierFailed"] is True
    assert review["flagged"]["last"] == "Review flagged"
    assert review["blocked"]["reviewState"] == "blocked"
    assert review["degraded"]["reviewState"] == "degraded"
    assert review["runtimeFailed"]["reviewState"] == "runtime_failed"
    for key in ("blocked", "degraded", "runtimeFailed"):
        assert review[key]["verifierFailed"] is False
        assert "Review flagged" not in review[key]["evidence"]
        assert review[key]["status"] == "completed"
        assert review[key]["ended"] is True
    assert review["ambiguousLegacyFailed"]["reviewState"] == "unavailable"
    assert review["ambiguousLegacyFailed"]["verifierFailed"] is False
    assert "Review flagged" not in review["ambiguousLegacyFailed"]["evidence"]


def test_visible_review_copy_matches_the_recorded_cause() -> None:
    cases = _presentation_cases()
    assert "Review flagged" in cases["flaggedPanel"]
    assert "Model review unavailable" in cases["blockedPanel"]
    assert "Review flagged" not in cases["blockedPanel"]
    assert "Model review degraded" in cases["degradedPanel"]
    assert "Review flagged" not in cases["degradedPanel"]
    assert "Model review failed to run" in cases["runtimeFailedPanel"]
    assert "Review flagged" not in cases["runtimeFailedPanel"]


def test_saved_activity_copy_preserves_review_execution_state() -> None:
    saved = _presentation_cases()["savedReviewCards"]
    assert "review flagged" in saved["flagged"]
    assert "model review blocked" in saved["blocked"]
    assert "review flagged" not in saved["blocked"]
    assert "model review degraded" in saved["degraded"]
    assert "review flagged" not in saved["degraded"]
    assert "model review failed to run" in saved["runtimeFailed"]
    assert "review flagged" not in saved["runtimeFailed"]
    assert "model review unavailable" in saved["legacyFailed"]
    assert "review flagged" not in saved["legacyFailed"]


def test_completed_is_independent_of_test_and_review_evidence() -> None:
    cases = _presentation_cases()
    assert cases["errors"] == []
    assert cases["completedTitle"] == "Completed"
    assert "Tested — passed" in cases["completedEvidence"]
    assert "Model-reviewed — passed" in cases["completedEvidence"]


def test_activity_is_visible_without_a_successful_tool_execution() -> None:
    cases = _presentation_cases()
    assert cases["failedActivity"] is True
    assert cases["approvalActivity"] is True
    assert cases["reviewActivity"] is True
    assert cases["routingActivity"] is True
    assert "r.view.style.display = hasActivity" in HTML
    assert "Failed safely" in cases["failurePanel"]
    assert "Waiting for approval" in cases["approvalPanel"]
    assert "Independent review passed" in cases["reviewPanel"]


def test_provider_and_model_use_recorded_truth_with_a_safe_fallback() -> None:
    cases = _presentation_cases()
    assert cases["exactProvider"] == "Recorded · anthropic · claude-sonnet-4"
    assert cases["providerOnly"] == "Recorded · provider-only"
    assert cases["modelOnly"] == "Recorded · model-only"
    assert cases["unknownIdentity"] == "Unknown model identity"
    assert cases["requestedProvider"] == "Requested · user/pinned-model"
    assert cases["selectedProvider"] == "Selected manifest · openrouter · anthropic/claude-sonnet-4"
    assert cases["actualProvider"] == "Actual adapter · ollama · qwen"
    assert cases["conflictingIdentity"] == "Actual adapter · actual-p · actual-m"
    assert cases["conflictingDetails"] == [
        "Requested · requested-p · requested-m",
        "Selected manifest · selected-p · selected-m",
        "Actual adapter · actual-p · actual-m",
    ]
    assert cases["cloudFallback"] == "Recorded · fallback-model"
    # A local-sounding lane alone is not rendered as proof that execution was local.
    assert cases["unknownLocality"] == "Recorded · qwen"
    render_card = HTML[HTML.index("function renderCard(run)") : HTML.index("function evidenceLabels(run)")]
    assert "run.model.lane" not in render_card
    assert "String(run.model.locality || '').toLowerCase() === 'cloud'" in render_card
    # The run card must not paint a "Cloud model" LABEL from lane inference. The ban is
    # scoped to the rendering slice: honest settings/drawer prose elsewhere on the page
    # (e.g. the companion drawer's "Cloud model calls happen only on the cloud lane..."
    # disclosure) is not a run-card label and must stay legal (broken page-wide since
    # the drawer landed 2026-08-28).
    assert "Cloud model" not in render_card
    assert "OpenRouter · ' +" not in HTML
    assert "'openrouter · ' +" not in HTML
    assert "['selected_provider_id', 'selected provider']" in HTML
    assert "['selected_model', 'selected model']" in HTML
    assert "['selected_provider_id', 'requested provider']" not in HTML
    assert "['selected_model', 'requested model']" not in HTML


def test_human_activity_states_are_event_grounded_and_fall_back_safely() -> None:
    cases = _presentation_cases()
    assert cases["activityStates"] == ["Planning", "Searching", "Reading", "Editing", "Running", "Testing"]
    assert cases["unknownActivityState"] == "Working"
    assert cases["approvalState"] == "Waiting for approval"


def test_tool_cards_separate_action_from_recorded_result() -> None:
    cases = _presentation_cases()
    assert cases["testStarted"]["title"] == "Running tests"
    assert "pytest tests/router" in cases["testStarted"]["sub"]
    assert cases["testFinished"]["title"] == "Test command finished"
    assert "Test command passed" in cases["testFinished"]["sub"]
    detail_values = [line[1] for line in cases["rawToolDetails"]]
    assert "pytest tests/router" in detail_values
    assert "Test command passed" in detail_values


def test_tabs_name_the_surfaces_they_actually_show() -> None:
    assert "['Steps', 'Activity', 'Agents', 'Changes', 'Files', 'Event log'" in HTML
    assert "panelTab === 'Steps'" in HTML
    assert "panelTab === 'Event log'" in HTML
    assert "panelTab === 'Terminal'" not in HTML


def test_every_declared_tab_has_a_surface_behind_it() -> None:
    """The invariant the literal above is really defending, asserted directly.

    The list check catches a tab being renamed; it cannot catch one being ADDED with nothing
    rendering it. A tab that appears and shows an empty body is worse than no tab, so every name in
    PANEL_TABS must be reachable in the render dispatch -- either by its own branch or through the
    shared `!run` path that Activity, Steps, Changes, Files and Tests share.
    """

    import re

    declared = re.search(r"const PANEL_TABS = \[(.*?)\];", HTML)
    assert declared
    names = [n.strip().strip("'") for n in declared.group(1).split(",")]

    # These render through the shared body path rather than an early-return branch.
    shared = {"Steps", "Activity", "Changes", "Files", "Tests", "Preview"}
    for name in names:
        if name in shared:
            continue
        assert f"panelTab === '{name}'" in HTML, f"tab {name!r} is offered but nothing renders it"


def test_receipt_copy_limits_the_integrity_claim() -> None:
    """The copy must claim CONSISTENCY, never completeness.

    It used to say "recorded action and evidence history is intact". The verifier
    walks the receipts it is given, so it catches mutation, forgery and a middle
    deletion — and cannot catch a truncated tail, because the surviving prefix is
    a valid chain. "Intact" was a completeness claim the check does not make.
    """
    assert "Receipts consistent" in HTML
    # The old completeness claim is gone. The chain-INVALID message still names the
    # evidence history — saying integrity could NOT be confirmed, the honest inverse —
    # so the ban targets the exact old claim, not any mention of the history.
    assert "recorded action and evidence history is intact" not in HTML
    assert "integrity could not be confirmed" in HTML
    assert "does not prove the history is complete" in HTML
    assert "This does not prove the answer is correct" in HTML
    assert "Chain verified ✓" not in HTML


def test_composer_frees_on_answer_ready_not_stream_close() -> None:
    assert "function releaseComposer" in HTML
    assert "Answer ready · model review running" in HTML
    assert "const owner = run ? ownerOf(run) : view;" in HTML
    assert "run !== owner.run" in HTML
    # `sendEl.disabled` is banned in the RUN lifecycle: a turn in flight leaves Send usable
    # ("Queue"), which is the whole point of this law. It became legal afterwards in exactly one
    # place -- the council lock, which closes the composer while a council owns the machine's
    # global model pin, a different fact about a different owner, restored on every terminal
    # path. So the ban is scoped to a pinned writer list instead of a whole-document string ban:
    # a run-lifecycle disable still cannot slip in beside it.
    disables = [line.strip() for line in HTML.splitlines() if "sendEl.disabled" in line]
    assert disables == ["sendEl.disabled = locked;"], (
        f"something other than the council lock disables Send: {disables}"
    )
