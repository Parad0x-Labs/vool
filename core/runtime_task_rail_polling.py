RUNTIME_TASK_RAIL_POLLING_SCRIPT = r"""
async function fetchSessions() {
  pollStatusEl.textContent = 'polling';
  const response = await fetch('/api/runtime/sessions');
  if (!response.ok) {
    pollStatusEl.textContent = 'error';
    return;
  }
  const payload = await response.json();
  sessions = Array.isArray(payload.sessions) ? payload.sessions : [];
  sessions.sort((a, b) => {
    const aOpen = String(a.session_id || '').startsWith('openclaw:') ? 1 : 0;
    const bOpen = String(b.session_id || '').startsWith('openclaw:') ? 1 : 0;
    if (aOpen !== bOpen) return bOpen - aOpen;
    return String(b.updated_at || '').localeCompare(String(a.updated_at || ''));
  });
  if (!selectedSessionId && sessions.length) {
    selectedSessionId = pickDefaultSession(sessions);
    setQuerySession(selectedSessionId);
  }
  if (selectedSessionId && !sessions.some((row) => row.session_id === selectedSessionId)) {
    selectedSessionId = pickDefaultSession(sessions);
    lastSeq = 0;
    knownEvents = [];
    setQuerySession(selectedSessionId);
  }
  renderSessions();
  renderSessionDetail();
  renderSummary();
}

async function fetchEvents(reset = false) {
  if (!selectedSessionId) {
    renderEvents();
    return;
  }
  const after = reset ? 0 : lastSeq;
  const response = await fetch(`/api/runtime/events?session=${encodeURIComponent(selectedSessionId)}&after=${after}&limit=120`);
  if (!response.ok) {
    pollStatusEl.textContent = 'error';
    return;
  }
  const payload = await response.json();
  const incoming = Array.isArray(payload.events) ? payload.events : [];
  if (reset) {
    knownEvents = incoming;
  } else if (incoming.length) {
    knownEvents = knownEvents.concat(incoming);
  }
  if (knownEvents.length) {
    const selectedStillExists = knownEvents.some((event) => Number(event.seq || 0) === Number(selectedEventSeq || 0));
    if (!selectedStillExists) {
      selectedEventSeq = Number(knownEvents[knownEvents.length - 1].seq || 0) || 0;
    }
  } else {
    selectedEventSeq = 0;
  }
  if (payload.next_after != null) {
    lastSeq = Number(payload.next_after) || lastSeq;
  } else if (knownEvents.length) {
    lastSeq = Number(knownEvents[knownEvents.length - 1].seq || 0) || lastSeq;
  }
  renderSessionDetail();
  renderSummary();
  renderEvents();
  renderSelectedStep();
  pollStatusEl.textContent = 'live';
}

async function tick() {
  try {
    await fetchSessions();
    await fetchEvents();
  } catch (err) {
    pollStatusEl.textContent = 'error';
  }
}

tick();
setInterval(tick, 1200);
"""
