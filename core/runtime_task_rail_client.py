from core.runtime_task_rail_event_render import RUNTIME_TASK_RAIL_EVENT_RENDER_SCRIPT
from core.runtime_task_rail_polling import RUNTIME_TASK_RAIL_POLLING_SCRIPT
from core.runtime_task_rail_summary_client import RUNTIME_TASK_RAIL_SUMMARY_CLIENT_SCRIPT

RUNTIME_TASK_RAIL_CLIENT_SCRIPT = (
    r"""
const sessionListEl = document.getElementById('sessionList');
const sessionDetailEl = document.getElementById('sessionDetail');
const summaryGridEl = document.getElementById('summaryGrid');
const opsGridEl = document.getElementById('opsGrid');
const traceStripEl = document.getElementById('traceStrip');
const metaRowEl = document.getElementById('metaRow');
const eventFeedEl = document.getElementById('eventFeed');
const pollStatusEl = document.getElementById('pollStatus');
const focusListEl = document.getElementById('focusList');
const artifactListEl = document.getElementById('artifactList');
const queryListEl = document.getElementById('queryList');
const selectedStepTitleEl = document.getElementById('selectedStepTitle');
const selectedStepBodyEl = document.getElementById('selectedStepBody');
const selectedStepMetaEl = document.getElementById('selectedStepMeta');
const traceRawPanelEl = document.getElementById('traceRawPanel');
const query = new URLSearchParams(window.location.search);
let selectedSessionId = query.get('session') || '';
let selectedEventSeq = 0;
let lastSeq = 0;
let knownEvents = [];
let sessions = [];

const statusClass = (value) => {
  const raw = String(value || 'running').toLowerCase();
  return ['running', 'completed', 'failed', 'pending_approval', 'interrupted', 'request_done', 'researching', 'solved'].includes(raw) ? raw : 'running';
};

const shortTime = (value) => {
  if (!value) return 'unknown';
  try {
    return new Date(value).toLocaleString();
  } catch (err) {
    return String(value);
  }
};

const formatNumber = (value) => {
  const num = Number(value || 0);
  if (!Number.isFinite(num)) return '0';
  return Math.abs(num) >= 100 ? String(Math.round(num)) : num.toFixed(1).replace(/\.0$/, '');
};

const escapeHtml = (value) => String(value || '')
  .replaceAll('&', '&amp;')
  .replaceAll('<', '&lt;')
  .replaceAll('>', '&gt;')
  .replaceAll('"', '&quot;');

function setQuerySession(sessionId) {
  const url = new URL(window.location.href);
  if (sessionId) url.searchParams.set('session', sessionId);
  else url.searchParams.delete('session');
  window.history.replaceState({}, '', url.toString());
}

function pickDefaultSession(items) {
  if (!items.length) return '';
  const runningOpenClaw = items.find((session) => String(session.session_id || '').startsWith('openclaw:') && String(session.status || '').toLowerCase() === 'running');
  if (runningOpenClaw) return runningOpenClaw.session_id || '';
  const anyOpenClaw = items.find((session) => String(session.session_id || '').startsWith('openclaw:'));
  if (anyOpenClaw) return anyOpenClaw.session_id || '';
  return items[0].session_id || '';
}
"""
    + RUNTIME_TASK_RAIL_SUMMARY_CLIENT_SCRIPT
    + RUNTIME_TASK_RAIL_EVENT_RENDER_SCRIPT
    + RUNTIME_TASK_RAIL_POLLING_SCRIPT
)
