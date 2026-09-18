RUNTIME_TASK_RAIL_SUMMARY_CLIENT_SCRIPT = r"""
const MODEL_REVIEW_EVENT_STATES = Object.freeze({
  model_lane_verifier_started: 'running',
  model_lane_verifier_completed: 'passed',
  model_lane_verifier_flagged: 'flagged',
  model_lane_verifier_failed: 'runtime_failed',
  model_lane_verifier_blocked: 'blocked',
  model_lane_verifier_degraded: 'degraded',
});
const MODEL_REVIEW_STATES = new Set(['not_run', 'running', 'passed', 'flagged', 'blocked', 'degraded', 'runtime_failed', 'unavailable']);
const FAILED_EVIDENCE_EVENT_TYPES = new Set(['task_envelope_failed', 'task_envelope_step_failed', 'tool_failed']);
const PASSED_EVIDENCE_EVENT_TYPES = new Set(['task_envelope_completed', 'task_envelope_step_completed', 'tool_executed']);
function typedEvidenceObservation(event) {
  const eventType = String(event.event_type || '').toLowerCase();
  const status = String(event.status || '').toLowerCase();
  if (FAILED_EVIDENCE_EVENT_TYPES.has(eventType) || status === 'failed') return 'failed';
  if (PASSED_EVIDENCE_EVENT_TYPES.has(eventType) || ['completed', 'executed', 'passed'].includes(status)) return 'passed';
  return 'running';
}
function advanceEvidenceState(current, observation) {
  if (current === 'failed' || observation === 'failed') return 'failed';
  if (observation === 'passed') return 'passed';
  return current === 'not_run' ? 'running' : current;
}
function normalizeReviewState(value) {
  const state = String(value || 'not_run').toLowerCase();
  // Older saved sessions collapsed verdict and infrastructure outcomes into failed. Without the
  // original typed event there is no authority to reconstruct a flagged reviewer verdict.
  if (state === 'failed') return 'unavailable';
  return MODEL_REVIEW_STATES.has(state) ? state : 'unavailable';
}
function advanceReviewState(current, observation) {
  // Review events are ordered execution states, so a later retry may replace an earlier blocked
  // or degraded outcome instead of being merged into a severity bucket.
  return normalizeReviewState(observation || current);
}
function buildSummary(session, events) {
  const serverSummary = session && session.execution_history ? session.execution_history : {};
  const serverBounded = serverSummary && serverSummary.bounded_execution ? serverSummary.bounded_execution : {};
  const serverStages = serverSummary && serverSummary.stages ? serverSummary.stages : {};
  const artifactIds = new Set();
  const packetArtifactIds = new Set();
  const bundleArtifactIds = new Set();
  const candidateIds = new Set();
  const queryRuns = Array.isArray(serverSummary.query_runs)
    ? serverSummary.query_runs.map((item) => ({
        label: String(item.label || ''),
        index: Number(item.index || 0),
        total: Number(item.total || 0),
        state: String(item.state || 'running'),
      }))
    : [];
  const startedQueries = new Set();
  const completedQueries = new Set();
  for (const item of Array.isArray(serverSummary.artifact_ids) ? serverSummary.artifact_ids : []) artifactIds.add(String(item));
  for (const item of Array.isArray(serverSummary.packet_artifact_ids) ? serverSummary.packet_artifact_ids : []) packetArtifactIds.add(String(item));
  for (const item of Array.isArray(serverSummary.bundle_artifact_ids) ? serverSummary.bundle_artifact_ids : []) bundleArtifactIds.add(String(item));
  for (const item of Array.isArray(serverSummary.candidate_ids) ? serverSummary.candidate_ids : []) candidateIds.add(String(item));
  let topicId = String(serverSummary.topic_id || '');
  let topicTitle = String(serverSummary.title || '');
  let claimId = String(serverSummary.claim_id || '');
  let resultStatus = String(serverSummary.topic_status || serverSummary.result_status || '');
  let activeStatus = String(serverSummary.request_status || session?.status || 'running');
  let lastMessage = String(serverSummary.last_message || session?.last_message || '');
  let latestTool = String(serverSummary.latest_tool || '');
  let postId = String(serverSummary.post_id || '');
  let queryCount = Number(serverSummary.query_started_count || 0);
  let artifactCount = Number(serverSummary.artifact_count || 0);
  let candidateCount = Number(serverSummary.candidate_count || 0);
  let stopReason = String(serverSummary.stop_reason || '');
  let approvalState = String(serverBounded.approval_state || 'not_required');
  let rollbackState = String(serverBounded.rollback_state || 'not_triggered');
  let restoreState = String(serverBounded.restore_state || 'not_triggered');
  let verifierState = String(serverBounded.verifier_state || 'not_run');
  let reviewState = normalizeReviewState(serverBounded.model_review_state || 'not_run');
  let validationState = String(serverBounded.validation_state || 'not_run');
  let toolAttemptCount = Number(serverBounded.tool_attempt_count || 0);
  let toolReceiptCount = Number(serverBounded.tool_receipt_count || 0);
  let mutatingToolCount = Number(serverBounded.mutating_tool_count || 0);
  const changedPaths = Array.isArray(serverSummary.changed_paths) ? serverSummary.changed_paths.map((item) => String(item)) : [];
  const failureItems = Array.isArray(serverSummary.failure_items)
    ? serverSummary.failure_items.map((item) => ({
        type: String(item.type || 'failed'),
        tool: String(item.tool || ''),
        message: String(item.message || ''),
      }))
    : [];
  const artifactRows = Array.isArray(serverSummary.artifact_rows)
    ? serverSummary.artifact_rows.map((item) => ({
        artifactId: String(item.artifact_id || item.artifactId || ''),
        role: String(item.role || 'artifact'),
        path: String(item.path || ''),
        toolName: String(item.tool_name || item.toolName || ''),
      }))
    : [];
  const retryHistory = Array.isArray(serverSummary.retry_history)
    ? serverSummary.retry_history.map((item) => ({
        tool: String(item.tool || 'runtime.step'),
        retryCount: Number(item.retry_count || item.retryCount || 0),
        reason: String(item.reason || ''),
      }))
    : [];
  const toolAttempts = new Map();
  const stages = {
    received: Boolean(serverStages.received),
    claimed: Boolean(serverStages.claimed),
    packet: Boolean(serverStages.packet),
    queries: Boolean(serverStages.queries),
    bundle: Boolean(serverStages.bundle),
    result: Boolean(serverStages.result),
  };

  let laneCostClass = '';
  let promptTokens = 0;
  let outputTokens = 0;
  let usdActual = 0;
  for (const event of events) {
    if (event.topic_id && !topicId) topicId = String(event.topic_id);
    if (event.topic_title) topicTitle = String(event.topic_title);
    if (event.claim_id) claimId = String(event.claim_id);
    if (event.result_status) resultStatus = String(event.result_status);
    if (event.post_id) postId = String(event.post_id);
    if (event.tool_name || event.intent) latestTool = String(event.tool_name || event.intent);
    if (event.message) lastMessage = String(event.message);
    if (event.status) activeStatus = String(event.status);
    if (event.cost_class) laneCostClass = String(event.cost_class);
    if (event.event_type === 'model_usage') {
      promptTokens += Number(event.prompt_tokens || 0);
      outputTokens += Number(event.output_tokens || 0);
      usdActual += Number(event.usd_actual || 0);
    }
    if (!stopReason && event.stop_reason) stopReason = String(event.stop_reason);
    if (!stopReason && event.loop_stop_reason) stopReason = String(event.loop_stop_reason);
    if (!stopReason && event.final_stop_reason) stopReason = String(event.final_stop_reason);
    if (event.event_type === 'tool_preview' || event.event_type === 'task_pending_approval' || String(event.status || '').toLowerCase() === 'pending_approval') approvalState = 'pending';
    if (event.event_type === 'task_envelope_rollback_completed') rollbackState = 'completed';
    if (event.event_type === 'task_envelope_rollback_failed') rollbackState = 'failed';
    if (event.event_type === 'task_envelope_restore_completed') restoreState = 'completed';
    if (event.event_type === 'task_envelope_restore_failed') restoreState = 'failed';
    const eventTypeForEvidence = String(event.event_type || '').toLowerCase();
    const toolForEvidence = String(event.tool_name || event.intent || '');
    const isValidation = ['workspace.run_tests', 'workspace.run_lint', 'workspace.run_formatter'].includes(toolForEvidence)
      || (Array.isArray(event.receipt_types) && event.receipt_types.includes('validation_result'));
    const reviewObservation = MODEL_REVIEW_EVENT_STATES[eventTypeForEvidence];
    if (reviewObservation) reviewState = advanceReviewState(reviewState, reviewObservation);
    else if (isValidation) validationState = advanceEvidenceState(validationState, typedEvidenceObservation(event));

    if (event.event_type === 'task_received' || event.event_type === 'task_envelope_started') stages.received = true;
    if (event.claim_id || event.tool_name === 'hive.claim_task') stages.claimed = true;
    if (event.artifact_id) {
      artifactIds.add(String(event.artifact_id));
      artifactRows.push({
        artifactId: String(event.artifact_id),
        role: String(event.artifact_role || event.artifact_kind || 'artifact'),
        path: String(event.path || event.file_path || event.target_path || ''),
        toolName: String(event.tool_name || ''),
      });
      if (String(event.artifact_role || '') === 'packet' || String(event.tool_name || '') === 'liquefy.pack_research_packet') {
        packetArtifactIds.add(String(event.artifact_id));
      }
      if (String(event.artifact_role || '') === 'bundle' || String(event.tool_name || '') === 'liquefy.pack_research_bundle') {
        bundleArtifactIds.add(String(event.artifact_id));
      }
    }
    if (event.tool_name === 'liquefy.pack_research_packet') stages.packet = true;
    if (event.tool_name === 'liquefy.pack_research_bundle') stages.bundle = true;
    if (event.tool_name === 'hive.submit_result' || event.event_type === 'task_completed' || event.event_type === 'task_envelope_completed' || event.event_type === 'task_envelope_merge_completed') stages.result = true;
    if (event.candidate_id) candidateIds.add(String(event.candidate_id));
    if (event.candidate_count != null) candidateCount = Math.max(candidateCount, Number(event.candidate_count) || 0);
    if (event.query_count != null) queryCount = Math.max(queryCount, Number(event.query_count) || 0);
    if (event.artifact_count != null) artifactCount = Math.max(artifactCount, Number(event.artifact_count) || 0);
    const changedPath = String(event.path || event.file_path || event.target_path || '').trim();
    if (changedPath && !changedPaths.includes(changedPath)) changedPaths.push(changedPath);
    const eventStatus = String(event.status || '').toLowerCase();
    const eventType = String(event.event_type || '').toLowerCase();
    if (eventType.includes('failed') || eventStatus === 'failed' || String(event.result_status || '').toLowerCase() === 'failed') {
      failureItems.push({
        type: String(event.event_type || 'failed'),
        tool: String(event.tool_name || ''),
        message: String(event.message || ''),
      });
    }
    if (event.retry_count != null) {
      retryHistory.push({
        tool: String(event.tool_name || 'runtime.step'),
        retryCount: Number(event.retry_count) || 0,
        reason: String(event.retry_reason || event.message || ''),
      });
    }
    if (event.tool_name) {
      toolAttempts.set(event.tool_name, Number(toolAttempts.get(event.tool_name) || 0) + 1);
    }

    if (event.tool_name === 'curiosity.run_external_topic') {
      const qIndex = Number(event.query_index || 0);
      const qTotal = Number(event.query_total || 0);
      const label = String(event.query || event.message || '').trim();
      const key = label || `${qIndex}/${qTotal}`;
      if (event.event_type === 'tool_started') startedQueries.add(key);
      if (event.event_type === 'tool_executed') completedQueries.add(key);
      if (label && !queryRuns.some((item) => item.label === label)) {
        queryRuns.push({
          label,
          index: qIndex,
          total: qTotal,
          state: event.event_type === 'tool_executed' ? 'completed' : 'running',
        });
      }
    }
  }

  const queryCompletedCount = completedQueries.size || queryCount;
  const queryStartedCount = Math.max(startedQueries.size, queryRuns.length, queryCompletedCount);
  if (queryStartedCount > 0 || queryCompletedCount > 0) stages.queries = true;
  artifactCount = Math.max(artifactCount, artifactIds.size);
  candidateCount = Math.max(candidateCount, candidateIds.size);
  for (const [toolName, attempts] of toolAttempts.entries()) {
    if (attempts > 1 && !retryHistory.some((item) => item.tool === toolName)) {
      retryHistory.push({
        tool: toolName,
        retryCount: attempts - 1,
        reason: 'repeated execution in the same session',
      });
    }
  }
  toolAttemptCount = Math.max(toolAttemptCount, Array.from(toolAttempts.values()).reduce((total, item) => total + Number(item || 0), 0));
  if (approvalState !== 'pending' && String(serverBounded.approval_state || '') === 'cleared') approvalState = 'cleared';
  if (verifierState === 'not_run' && String(serverBounded.verifier_state || '')) verifierState = String(serverBounded.verifier_state || 'not_run');
  if (rollbackState === 'not_triggered' && String(serverBounded.rollback_state || '')) rollbackState = String(serverBounded.rollback_state || 'not_triggered');
  if (restoreState === 'not_triggered' && String(serverBounded.restore_state || '')) restoreState = String(serverBounded.restore_state || 'not_triggered');

  const title = topicTitle || String(serverSummary.title || '') || session?.request_preview || session?.session_id || 'Recent runtime session';
  const requestStatus = String(session?.status || activeStatus || 'running').toLowerCase();
  const topicStatus = String(resultStatus || '').toLowerCase();
  const displayStatus = topicStatus || (String(serverSummary.task_class || session?.task_class || '').toLowerCase() === 'autonomous_research' && requestStatus === 'completed'
    ? 'request_done'
    : requestStatus || 'running');
  const requestStateLabel = String(serverSummary.request_state_label || '') || (requestStatus === 'completed' && topicStatus && topicStatus !== 'solved' && topicStatus !== 'completed'
    ? 'request finished; topic still active'
    : requestStatus === 'completed' && String(serverSummary.task_class || session?.task_class || '').toLowerCase() === 'autonomous_research'
      ? 'request finished after the first bounded pass'
      : requestStatus);
  return {
    sessionId: session?.session_id || '',
    title,
    requestPreview: String(session?.request_preview || ''),
    taskClass: String(session?.task_class || 'unknown'),
    status: displayStatus,
    requestStatus,
    requestStateLabel,
    topicStatus,
    lastMessage,
    updatedAt: String(session?.updated_at || ''),
    topicId,
    claimId,
    resultStatus: topicStatus || String(session?.status || activeStatus || ''),
    postId,
    latestTool,
    lane: laneCostClass === 'paid_cloud' ? 'cloud' : (laneCostClass === 'free_local' ? 'local' : ''),
    promptTokens,
    outputTokens,
    usdActual,
    artifactIds: Array.from(artifactIds),
    packetArtifactIds: Array.from(packetArtifactIds),
    bundleArtifactIds: Array.from(bundleArtifactIds),
    candidateIds: Array.from(candidateIds),
    artifactRows,
    changedPaths,
    failureItems,
    retryHistory,
    stopReason: stopReason || (requestStatus === 'completed' ? 'bounded loop finished' : ''),
    queryRuns,
    queryStartedCount,
    queryCompletedCount,
    artifactCount,
    candidateCount,
    stages,
    approvalState,
    rollbackState,
    restoreState,
    verifierState,
    reviewState,
    validationState,
    toolAttemptCount,
    toolReceiptCount,
    mutatingToolCount,
  };
}
"""
