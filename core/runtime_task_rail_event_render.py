RUNTIME_TASK_RAIL_EVENT_RENDER_SCRIPT = r"""
// --- Inference value estimate (tok/s -> $/min) ------------------------------------------------
// Local inference really costs ~electricity, so a raw "cost" is ~$0. Instead show what the SAME
// throughput is WORTH at two realistic rates: (a) a blended average cloud LLM API price, and (b)
// VOOL's own compute-market price. Both are estimates (labelled "~est"); tune the constants here.
const CLOUD_USD_PER_TOKEN = 0.0000006;  // ~$0.60 / 1M tokens: blended avg of common mid-tier cloud LLM output pricing
const MESH_USDC_PER_TOKEN = 0.000001;   // ~$1.00 / 1M tokens: VOOL null_protocol default price_per_token_usdc
function fmtUsdPerMin(v) {
  if (!(v > 0)) return "0";
  return v >= 0.001 ? v.toFixed(5) : v.toFixed(6);
}
function modelReviewTone(state) {
  if (state === 'passed') return 'good';
  if (state === 'flagged' || state === 'runtime_failed') return 'bad';
  if (state === 'running' || state === 'blocked' || state === 'degraded' || state === 'unavailable') return 'warn';
  return '';
}
function modelReviewDetail(state) {
  if (state === 'passed') return 'Review passed; this is model-review evidence, not validation or answer proof.';
  if (state === 'flagged') return 'Review flagged by the reviewer model; this is a reviewer verdict.';
  if (state === 'running') return 'Model review is still in flight; no verdict is recorded yet.';
  if (state === 'blocked') return 'Model review was blocked before a reviewer verdict was produced.';
  if (state === 'degraded') return 'Model review was degraded or unavailable; no reviewer verdict was produced.';
  if (state === 'runtime_failed') return 'Model review failed to run to a valid verdict.';
  if (state === 'unavailable') return 'Model-review outcome is unavailable; no reviewer verdict can be inferred.';
  return 'No model-review event recorded for this session.';
}

function renderSessions() {
  if (!sessions.length) {
    sessionListEl.innerHTML = '<div class="empty-state">No recent runtime sessions yet.</div>';
    return;
  }
  sessionListEl.innerHTML = sessions.map((session) => {
    const active = session.session_id === selectedSessionId ? 'active' : '';
    const sessionSummary = buildSummary(session, session.session_id === selectedSessionId ? knownEvents : []);
    const badgeLabel = sessionSummary.status === 'request_done' ? 'request done' : (sessionSummary.status || 'running');
    return `
      <button class="session-card ${active}" data-session-id="${escapeHtml(session.session_id)}">
        <div class="session-top">
          <div class="session-id">${escapeHtml(session.session_id)}</div>
          <span class="badge ${statusClass(sessionSummary.status)}">${escapeHtml(badgeLabel)}</span>
        </div>
        <div class="session-preview">${escapeHtml(session.request_preview || session.last_message || 'Recent OpenClaw task')}</div>
        <div class="session-meta">
          <span>${escapeHtml(session.task_class || 'unknown')}</span>
          <span>${escapeHtml(String(session.event_count || 0))} events</span>
          ${session.resume_available ? `<span>resume ready from ${escapeHtml(String(session.checkpoint_step_count || 0))} step(s)</span>` : ''}
          ${sessionSummary.approvalState === 'pending' ? '<span>approval pending</span>' : ''}
          ${sessionSummary.reviewState && sessionSummary.reviewState !== 'not_run' ? `<span>model review ${escapeHtml(sessionSummary.reviewState)}</span>` : ''}
          ${sessionSummary.validationState && sessionSummary.validationState !== 'not_run' ? `<span>validation ${escapeHtml(sessionSummary.validationState)}</span>` : ''}
          <span>${escapeHtml(shortTime(session.updated_at))}</span>
        </div>
      </button>
    `;
  }).join('');
  sessionListEl.querySelectorAll('[data-session-id]').forEach((node) => {
    node.addEventListener('click', () => {
      const sessionId = node.getAttribute('data-session-id') || '';
      if (!sessionId || sessionId === selectedSessionId) return;
      selectedSessionId = sessionId;
      selectedEventSeq = 0;
      lastSeq = 0;
      knownEvents = [];
      setQuerySession(sessionId);
      renderSessions();
      renderSessionDetail();
      renderSummary();
      renderEvents();
      fetchEvents(true);
    });
  });
}

function renderSessionDetail() {
  const session = sessions.find((row) => row.session_id === selectedSessionId);
  if (!session) {
    sessionDetailEl.innerHTML = `
      <h2>No session selected</h2>
      <p>Pick a recent session from the left to inspect its runtime event trail.</p>
    `;
    return;
  }
  const summary = buildSummary(session, knownEvents);
  const detailLine = summary.status === 'researching'
    ? 'The chat request finished quickly because it only launched the first bounded research pass. The Hive topic is still researching in the background.'
    : 'This trace comes from real runtime session events, not fabricated chain-of-thought.';
  sessionDetailEl.innerHTML = `
    <h2>${escapeHtml(summary.title)}</h2>
    <p>${escapeHtml(summary.lastMessage || 'No event message yet.')} ${escapeHtml(detailLine)}</p>
  `;
}

function selectedEvent() {
  if (!knownEvents.length) return null;
  if (!selectedEventSeq) return knownEvents[knownEvents.length - 1] || null;
  return knownEvents.find((event) => Number(event.seq || 0) === Number(selectedEventSeq || 0)) || knownEvents[knownEvents.length - 1] || null;
}

function renderSelectedStep() {
  const session = sessions.find((row) => row.session_id === selectedSessionId);
  const event = selectedEvent();
  if (!session || !event) {
    selectedStepTitleEl.textContent = 'No step selected';
    selectedStepBodyEl.textContent = 'Pick a runtime event or let the latest step stay in focus.';
    selectedStepMetaEl.innerHTML = '';
    traceRawPanelEl.textContent = '';
    return;
  }
  const summary = buildSummary(session, knownEvents);
  const title = String(event.tool_name || event.event_type || 'runtime_step');
  const body = String(
    event.message
    || event.retry_reason
    || event.stop_reason
    || event.final_stop_reason
    || event.query
    || 'No step detail captured.',
  );
  const meta = [];
  meta.push(`<span class="meta-chip">seq ${escapeHtml(String(event.seq || '?'))}</span>`);
  meta.push(`<span class="meta-chip">type ${escapeHtml(String(event.event_type || 'status'))}</span>`);
  if (event.tool_name) meta.push(`<span class="meta-chip">tool ${escapeHtml(String(event.tool_name))}</span>`);
  if (event.status) meta.push(`<span class="meta-chip">status ${escapeHtml(String(event.status))}</span>`);
  if (event.path || event.file_path || event.target_path) {
    meta.push(`<span class="meta-chip">changed ${escapeHtml(String(event.path || event.file_path || event.target_path))}</span>`);
  }
  if (event.artifact_id) meta.push(`<span class="meta-chip">artifact ${escapeHtml(String(event.artifact_id))}</span>`);
  if (event.stop_reason || event.final_stop_reason || summary.stopReason) {
    meta.push(`<span class="meta-chip">stop ${escapeHtml(String(event.stop_reason || event.final_stop_reason || summary.stopReason))}</span>`);
  }
  if (event.retry_count != null) meta.push(`<span class="meta-chip">retry ${escapeHtml(String(event.retry_count))}</span>`);
  selectedStepTitleEl.textContent = title;
  selectedStepBodyEl.textContent = body;
  selectedStepMetaEl.innerHTML = meta.join('');
  traceRawPanelEl.textContent = JSON.stringify(
    {
      session: {
        session_id: session.session_id,
        request_preview: session.request_preview,
        status: session.status,
        task_class: session.task_class,
      },
      summary,
      selected_event: event,
    },
    null,
    2,
  );
}

function renderSummary() {
  const session = sessions.find((row) => row.session_id === selectedSessionId);
  if (!session) {
    summaryGridEl.innerHTML = '<div class="empty-state">No session summary yet.</div>';
    traceStripEl.innerHTML = '<div class="empty-state">No process rail yet.</div>';
    metaRowEl.innerHTML = '';
    focusListEl.innerHTML = '<div class="inspector-item">No topic or claim selected yet.</div>';
    artifactListEl.innerHTML = '<div class="inspector-item">No packed artifacts yet.</div>';
    queryListEl.innerHTML = '<div class="inspector-item">No query runs yet.</div>';
    opsGridEl.innerHTML = '<div class="empty-state">No stop, failure, or retry state yet.</div>';
    traceRawPanelEl.textContent = '';
    return;
  }
  const summary = buildSummary(session, knownEvents);
  const laneLabel = summary.lane === 'cloud' ? 'Cloud' : (summary.lane === 'local' ? 'Local' : '—');
  const totalTokens = Number(summary.promptTokens || 0) + Number(summary.outputTokens || 0);
  const stats = [
    { label: 'lane', value: laneLabel },
    { label: 'tokens', value: totalTokens.toLocaleString() },
    { label: 'cost', value: Number(summary.usdActual || 0) > 0 ? ('$' + Number(summary.usdActual).toFixed(4)) : '$0' },
    { label: 'request state', value: summary.requestStatus || 'running' },
    { label: 'topic', value: summary.topicId ? `#${summary.topicId.slice(0, 8)}` : 'none yet' },
    { label: 'claim', value: summary.claimId ? summary.claimId.slice(0, 8) : 'none yet' },
    { label: 'queries', value: `${summary.queryCompletedCount}/${Math.max(summary.queryStartedCount, summary.queryCompletedCount)}` },
    { label: 'artifacts', value: String(summary.artifactCount || summary.artifactIds.length || 0) },
    { label: 'topic state', value: summary.resultStatus || summary.status || 'running' },
  ];
  summaryGridEl.innerHTML = stats.map((item) => `
    <div class="stat-card">
      <span>${escapeHtml(item.label)}</span>
      <strong>${escapeHtml(item.value)}</strong>
    </div>
  `).join('');

  const stageRows = [
    { key: 'received', label: 'Request', value: summary.stages.received ? 'accepted' : 'waiting', detail: session.request_preview || summary.title },
    { key: 'claimed', label: 'Claim', value: summary.stages.claimed ? (summary.claimId ? summary.claimId.slice(0, 8) : 'active') : 'not claimed', detail: summary.topicId ? `topic #${summary.topicId.slice(0, 8)}` : 'no live topic yet' },
    { key: 'packet', label: 'Packet', value: summary.stages.packet ? 'packed' : 'pending', detail: summary.packetArtifactIds.length ? summary.packetArtifactIds.map((item) => item.slice(0, 8)).join(', ') : 'machine-readable packet not packed yet' },
    { key: 'queries', label: 'Queries', value: `${summary.queryCompletedCount}`, detail: summary.queryStartedCount ? `${summary.queryCompletedCount}/${summary.queryStartedCount} bounded runs finished` : 'no bounded research runs yet' },
    { key: 'bundle', label: 'Artifacts', value: `${summary.artifactCount || summary.artifactIds.length}`, detail: summary.bundleArtifactIds.length ? `bundle ${summary.bundleArtifactIds.map((item) => item.slice(0, 8)).join(', ')}` : 'no research bundle yet' },
    { key: 'result', label: 'Topic', value: summary.resultStatus || summary.status || 'running', detail: summary.postId ? `post ${summary.postId.slice(0, 8)}` : (summary.lastMessage || 'no result post yet') },
  ];
  traceStripEl.innerHTML = stageRows.map((stage) => {
    const stateClass = summary.status === 'failed' && stage.key === 'result'
      ? 'failed'
      : summary.stages[stage.key]
        ? 'done'
        : stage.key === 'queries' && summary.queryStartedCount > 0
          ? 'active'
          : '';
    return `
      <article class="trace-stage ${stateClass}">
        <div class="stage-label">${escapeHtml(stage.label)}</div>
        <div class="stage-value">${escapeHtml(stage.value)}</div>
        <div class="stage-detail">${escapeHtml(stage.detail)}</div>
      </article>
    `;
  }).join('');

  const meta = [];
  meta.push(`<span class="meta-chip">request ${escapeHtml(summary.requestStateLabel || summary.requestStatus || 'running')}</span>`);
  meta.push(`<span class="meta-chip">topic ${escapeHtml(summary.resultStatus || summary.status || 'running')}</span>`);
  meta.push(`<span class="meta-chip">class ${escapeHtml(session.task_class || 'unknown')}</span>`);
  meta.push(`<span class="meta-chip">events ${escapeHtml(String(session.event_count || knownEvents.length || 0))}</span>`);
  meta.push(`<span class="meta-chip">updated ${escapeHtml(shortTime(session.updated_at))}</span>`);
  if (summary.latestTool) meta.push(`<span class="meta-chip">tool ${escapeHtml(summary.latestTool)}</span>`);
  if (summary.topicId) meta.push(`<span class="meta-chip">topic ${escapeHtml(summary.topicId)}</span>`);
  if (summary.stopReason) meta.push(`<span class="meta-chip">stop ${escapeHtml(summary.stopReason)}</span>`);
  if (summary.approvalState && summary.approvalState !== 'not_required') meta.push(`<span class="meta-chip">approval ${escapeHtml(summary.approvalState)}</span>`);
  if (summary.reviewState && summary.reviewState !== 'not_run') meta.push(`<span class="meta-chip">model review ${escapeHtml(summary.reviewState)}</span>`);
  if (summary.validationState && summary.validationState !== 'not_run') meta.push(`<span class="meta-chip">validation ${escapeHtml(summary.validationState)}</span>`);
  if ((summary.rollbackState && summary.rollbackState !== 'not_triggered') || (summary.restoreState && summary.restoreState !== 'not_triggered')) {
    meta.push(`<span class="meta-chip">recovery ${escapeHtml(summary.rollbackState !== 'not_triggered' ? summary.rollbackState : summary.restoreState)}</span>`);
  }
  metaRowEl.innerHTML = meta.join('');

  const focusItems = [];
  if (summary.topicId) focusItems.push(`<div class="inspector-item">Topic <code>${escapeHtml(summary.topicId)}</code></div>`);
  if (summary.claimId) focusItems.push(`<div class="inspector-item">Claim <code>${escapeHtml(summary.claimId)}</code></div>`);
  focusItems.push(`<div class="inspector-item">Request state: <code>${escapeHtml(summary.requestStatus || 'running')}</code></div>`);
  focusItems.push(`<div class="inspector-item">Topic state: <code>${escapeHtml(summary.resultStatus || summary.status || 'running')}</code></div>`);
  focusItems.push(`<div class="inspector-item">Approval: <code>${escapeHtml(summary.approvalState || 'not_required')}</code></div>`);
  focusItems.push(`<div class="inspector-item">Model review: <code>${escapeHtml(summary.reviewState || 'not_run')}</code></div>`);
  focusItems.push(`<div class="inspector-item">Validation: <code>${escapeHtml(summary.validationState || 'not_run')}</code></div>`);
  focusItems.push(`<div class="inspector-item">Rollback: <code>${escapeHtml(summary.rollbackState || 'not_triggered')}</code></div>`);
  focusItems.push(`<div class="inspector-item">Restore: <code>${escapeHtml(summary.restoreState || 'not_triggered')}</code></div>`);
  if (summary.stopReason) focusItems.push(`<div class="inspector-item">Stop reason: <code>${escapeHtml(summary.stopReason)}</code></div>`);
  if (summary.changedPaths.length) {
    summary.changedPaths.slice(0, 6).forEach((path) => {
      focusItems.push(`<div class="inspector-item">Changed <code>${escapeHtml(path)}</code></div>`);
    });
  }
  if (summary.postId) focusItems.push(`<div class="inspector-item">Last result post <code>${escapeHtml(summary.postId)}</code></div>`);
  if (!focusItems.length) focusItems.push('<div class="inspector-item">No topic or claim selected yet.</div>');
  focusListEl.innerHTML = focusItems.join('');

  const artifactItems = summary.artifactRows.map((item) => {
    const role = summary.packetArtifactIds.includes(item.artifactId) ? 'packet' : summary.bundleArtifactIds.includes(item.artifactId) ? 'bundle' : item.role || 'artifact';
    const tail = item.path ? ` · <code>${escapeHtml(item.path)}</code>` : item.toolName ? ` · ${escapeHtml(item.toolName)}` : '';
    return `<div class="inspector-item">${escapeHtml(role)} <code>${escapeHtml(item.artifactId)}</code>${tail}</div>`;
  });
  artifactListEl.innerHTML = artifactItems.length ? artifactItems.join('') : '<div class="inspector-item">No packed artifacts yet.</div>';

  const queryItems = [];
  summary.retryHistory.forEach((item) => {
    queryItems.push(`<div class="inspector-item">retry <code>${escapeHtml(item.tool)}</code> × ${escapeHtml(String(item.retryCount || 0))}${item.reason ? ` · ${escapeHtml(item.reason)}` : ''}</div>`);
  });
  summary.queryRuns.forEach((item) => {
    const prefix = item.total ? `${item.index}/${item.total}` : 'query';
    queryItems.push(`<div class="inspector-item"><code>${escapeHtml(prefix)}</code> ${escapeHtml(item.label)}</div>`);
  });
  queryListEl.innerHTML = queryItems.length ? queryItems.join('') : '<div class="inspector-item">No query runs yet.</div>';

  const failureCount = summary.failureItems.length;
  const retryCount = summary.retryHistory.reduce((total, item) => total + Number(item.retryCount || 0), 0);
  const changedCount = summary.changedPaths.length;
  const opsCards = [
    {
      tone: summary.stopReason ? 'good' : '',
      label: 'Stop reason',
      value: summary.stopReason || 'still running',
      detail: 'Bounded execution stops explicitly instead of pretending work is still in flight.',
    },
    {
      tone: failureCount ? 'bad' : 'good',
      label: 'Failures',
      value: String(failureCount),
      detail: failureCount
        ? summary.failureItems.slice(0, 2).map((item) => item.message || item.type).join(' | ')
        : 'No failed runtime steps in this session.',
    },
    {
      tone: summary.approvalState === 'pending' ? 'warn' : summary.approvalState === 'cleared' ? 'good' : '',
      label: 'Approval',
      value: summary.approvalState || 'not_required',
      detail: summary.approvalState === 'pending'
        ? 'A bounded action is waiting for operator confirmation.'
        : 'No active approval gate is blocking this session.',
    },
    {
      tone: modelReviewTone(summary.reviewState),
      label: 'Model review',
      value: summary.reviewState || 'not_run',
      detail: modelReviewDetail(summary.reviewState),
    },
    {
      tone: summary.validationState === 'failed' ? 'bad' : summary.validationState === 'passed' ? 'good' : summary.validationState === 'running' ? 'warn' : '',
      label: 'Validation',
      value: summary.validationState || 'not_run',
      detail: summary.validationState === 'failed'
        ? 'A recorded test, lint, or format check failed.'
        : summary.validationState === 'passed'
          ? 'Recorded validation checks completed successfully.'
          : summary.validationState === 'running'
            ? 'A test, lint, or format check is still in flight.'
            : 'No validation check recorded for this session.',
    },
    {
      tone: summary.rollbackState === 'failed' || summary.restoreState === 'failed' ? 'bad' : summary.rollbackState === 'completed' || summary.restoreState === 'completed' ? 'good' : '',
      label: 'Recovery',
      value: summary.rollbackState !== 'not_triggered' ? summary.rollbackState : (summary.restoreState || 'not_triggered'),
      detail: summary.rollbackState !== 'not_triggered'
        ? `Rollback ${summary.rollbackState}`
        : summary.restoreState !== 'not_triggered'
          ? `Restore ${summary.restoreState}`
          : 'No rollback or restore path was triggered.',
    },
    {
      tone: retryCount ? 'warn' : '',
      label: 'Retries',
      value: String(retryCount),
      detail: retryCount
        ? summary.retryHistory.slice(0, 2).map((item) => `${item.tool} x${item.retryCount}`).join(' | ')
        : 'No repeated runtime retries.',
    },
    {
      tone: changedCount ? 'good' : summary.toolReceiptCount ? 'warn' : '',
      label: 'Changed',
      value: String(changedCount),
      detail: changedCount
        ? summary.changedPaths.slice(0, 2).join(' | ')
        : summary.toolReceiptCount
          ? `No path mutation recorded across ${summary.toolReceiptCount} tool receipt(s).`
          : 'No file or target path changes recorded.',
    },
  ];
  opsGridEl.innerHTML = opsCards.map((item) => `
    <article class="ops-card ${escapeHtml(item.tone)}">
      <span>${escapeHtml(item.label)}</span>
      <strong>${escapeHtml(item.value)}</strong>
      <p>${escapeHtml(item.detail)}</p>
    </article>
  `).join('');
}

function renderEvents() {
  if (!knownEvents.length) {
    eventFeedEl.innerHTML = '<div class="empty-state">No runtime events for this session yet.</div>';
    return;
  }
  const activeSeq = Number((selectedEvent() || {}).seq || 0);
  eventFeedEl.innerHTML = knownEvents.map((event) => {
    const chips = [];
    if (event.seq != null) chips.push(`<span class="meta-chip">seq ${escapeHtml(String(event.seq))}</span>`);
    // Keep orchestration, requested policy, selected manifest, and actual adapter
    // identity separate. An actual identity is shown only when runtime evidence records it.
    const actualModel = event.actual_adapter_model_id || '';
    const actualProvider = event.actual_adapter_provider_id || '';
    const selectedModel = event.selected_model || '';
    const selectedProvider = event.selected_provider_id || '';
    const requestedModel = event.requested_model || event.requested_model_id || '';
    const requestedProvider = event.requested_provider_id || '';
    const policyModel = event.planned_model_id || '';
    const policyProvider = event.planned_provider_id || '';
    const recordedModel = event.model_id || event.model_name || '';
    const recordedProvider = event.provider_id || '';
    if (event.lane) chips.push(`<span class="meta-chip route">orchestration lane ${escapeHtml(String(event.lane))}</span>`);
    if (event.complexity) chips.push(`<span class="meta-chip route">complexity ${escapeHtml(String(event.complexity))}</span>`);
    if (requestedProvider) chips.push(`<span class="meta-chip route">requested provider ${escapeHtml(String(requestedProvider))}</span>`);
    if (requestedModel) chips.push(`<span class="meta-chip route">requested model ${escapeHtml(String(requestedModel))}</span>`);
    if (policyProvider) chips.push(`<span class="meta-chip route">policy provider ${escapeHtml(String(policyProvider))}</span>`);
    if (policyModel) chips.push(`<span class="meta-chip route">policy model ${escapeHtml(String(policyModel))}</span>`);
    if (selectedProvider) chips.push(`<span class="meta-chip route">selected provider ${escapeHtml(String(selectedProvider))}</span>`);
    if (selectedModel) chips.push(`<span class="meta-chip route">selected model ${escapeHtml(String(selectedModel))}</span>`);
    if (actualProvider) chips.push(`<span class="meta-chip route">actual provider ${escapeHtml(String(actualProvider))}</span>`);
    if (actualModel) chips.push(`<span class="meta-chip route">actual model ${escapeHtml(String(actualModel))}</span>`);
    if (!actualModel && !selectedModel && recordedModel) chips.push(`<span class="meta-chip route">recorded model ${escapeHtml(String(recordedModel))}</span>`);
    else if (!actualProvider && !selectedProvider && recordedProvider && String(recordedProvider).indexOf('fast-path') !== -1) chips.push(`<span class="meta-chip route">model none · fast-path</span>`);
    else if (!actualProvider && !selectedProvider && recordedProvider) chips.push(`<span class="meta-chip route">recorded provider ${escapeHtml(String(recordedProvider))}</span>`);
    if (event.tokens_per_second) {
      const tps = Number(event.tokens_per_second);
      chips.push(`<span class="meta-chip route">${escapeHtml(String(Math.round(tps)))} tok/s</span>`);
      // Value of this throughput at cloud-equivalent and VOOL-mesh rates (estimates).
      const tokensPerMin = tps * 60;
      const cloudPerMin = fmtUsdPerMin(tokensPerMin * CLOUD_USD_PER_TOKEN);
      const meshPerMin = fmtUsdPerMin(tokensPerMin * MESH_USDC_PER_TOKEN);
      chips.push(`<span class="meta-chip route" title="Estimated value at this throughput. cloud ≈ blended average cloud LLM API price (~$0.60/1M tok); mesh ≈ VOOL compute-market price (~$1/1M tok). Running locally, you pay ~electricity.">~$${cloudPerMin}/min cloud · ~$${meshPerMin}/min mesh</span>`);
    }
    if (event.topic_id) chips.push(`<span class="meta-chip">topic ${escapeHtml(String(event.topic_id).slice(0, 8))}</span>`);
    if (event.claim_id) chips.push(`<span class="meta-chip">claim ${escapeHtml(String(event.claim_id).slice(0, 8))}</span>`);
    if (event.artifact_id) chips.push(`<span class="meta-chip">artifact ${escapeHtml(String(event.artifact_id).slice(0, 8))}</span>`);
    if (event.query_index || event.query_total) chips.push(`<span class="meta-chip">query ${escapeHtml(String(event.query_index || 0))}/${escapeHtml(String(event.query_total || 0))}</span>`);
    if (event.candidate_id) chips.push(`<span class="meta-chip">candidate ${escapeHtml(String(event.candidate_id).slice(0, 8))}</span>`);
    if (event.result_status) chips.push(`<span class="meta-chip">result ${escapeHtml(String(event.result_status))}</span>`);
    if (event.tool_name) chips.push(`<span class="meta-chip">tool ${escapeHtml(String(event.tool_name))}</span>`);
    if (event.status) chips.push(`<span class="meta-chip">status ${escapeHtml(String(event.status))}</span>`);
    if (event.stop_reason) chips.push(`<span class="meta-chip">stop ${escapeHtml(String(event.stop_reason))}</span>`);
    if (event.retry_count != null) chips.push(`<span class="meta-chip">retry ${escapeHtml(String(event.retry_count))}</span>`);
    const activeClass = Number(event.seq || 0) === activeSeq ? 'is-active' : '';
    return `
      <article class="event-card ${escapeHtml(String(event.event_type || 'status'))} ${activeClass}" data-event-seq="${escapeHtml(String(event.seq || '0'))}">
        <div class="event-head">
          <div class="event-type">${escapeHtml(event.event_type || 'status')}</div>
          <div class="event-time">${escapeHtml(shortTime(event.created_at))}</div>
        </div>
        <div class="event-message">${escapeHtml(event.message || '')}</div>
        <div class="event-meta">${chips.join('')}</div>
      </article>
    `;
  }).join('');
  eventFeedEl.querySelectorAll('[data-event-seq]').forEach((node) => {
    node.addEventListener('click', () => {
      selectedEventSeq = Number(node.getAttribute('data-event-seq') || '0') || 0;
      renderEvents();
      renderSelectedStep();
    });
  });
}
"""
