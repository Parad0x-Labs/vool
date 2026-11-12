"""A failed load never renders as a fake "nothing here" in the settings and panel fragments.

Fragment half of the no-fake-empty contract, same law as tests/test_restart_shows_loading.py
pins for the chat sidebar and tests/test_no_fake_empty_phone.py pins for the phone surfaces:

* Settings -> Memory list (core/settings_extras_fragment.py renderMemory): a non-OK answer, an
  unreadable body or a fetch that throws paints the calm unreachable row and retries on a
  growing delay; "Nothing yet." shows only on a real OK empty answer, and after a first success
  a later failure keeps the last rendered rows.
* Model radar (core/model_radar_fragment.py refresh/paintList): a failed feed read never paints
  "Nothing qualified yet" (that text says quiet is the design); the calm row holds until the
  first real answer, then the last cards stay across later failures.
* Council past-runs list (core/council_fragment.py loadRuns): a failed read paints the calm line
  and retries; a real OK empty answer renders nothing, a real OK answer renders the run buttons.
* Goal progress card (core/goal_progress_fragment.py fetchState): a failed state read never
  hides the card as if no goal existed -- the last good view stands, or the collapsed card
  carries the calm line before any view exists; only a real OK answer without a goal hides it.
* Chat import sources (core/chat_import_settings_fragment.py loadSources): a failed read keeps
  the file-import option and never says "No other AI apps were found"; that note shows only on
  a real OK answer.

Every fragment boots standalone under the shared node harness with a stubbed fetch; the
helpers fall back to their inline English when no catalog is loaded, so the canonical
load.unreachable fallback is assertable verbatim.
"""
from __future__ import annotations

from core.chat_import_settings_fragment import render_chat_import_settings_fragment
from core.council_fragment import render_council_fragment
from core.goal_progress_fragment import render_goal_progress_fragment
from core.model_radar_fragment import render_model_radar_fragment
from core.settings_extras_fragment import render_settings_extras_fragment
from tests.chat_page_js_harness import DOM, run_node, script_blocks

CALM = "Can\u2019t reach VOOL right now, trying again\u2026"

_JSON = """
const __json = (status, payload) => ({ ok: status >= 200 && status < 300, status: status,
  headers: { get: () => 'application/json' }, json: async () => payload });
"""


# ---- Settings -> Memory list -----------------------------------------------------------------

_SETTINGS_SCRIPT = script_blocks(render_settings_extras_fragment())[0]


def _settings(body: str) -> dict:
    return run_node(DOM + _SETTINGS_SCRIPT + _JSON + body)


def test_memory_list_non_ok_is_the_calm_line_not_the_none_yet_state() -> None:
    result = _settings(
        """
let __calls = 0;
globalThis.fetch = async (url) => {
  const path = new URL(String(url), 'http://vool.test').pathname;
  if (path === '/api/memory/entries') { __calls += 1; return __json(503, { ok: false, state: 'starting' }); }
  return __json(200, {});
};
const host = document.getElementById('testHost');
window.VoolSettingsExtras.mountInto(host, 'memory');
await new Promise(setImmediate);
const list = host.querySelector('.vs-sec').querySelector('.vs-mem-list');
const html = String(list.innerHTML || '');
await new Promise((resolve) => _st(resolve, 1300));   // the growing-delay retry (1 s start) fires
out({ html: html, calls: __calls });
"""
    )
    assert result["errors"] == []
    assert CALM in result["html"]
    assert "Nothing yet" not in result["html"]
    assert result["calls"] >= 2, "a failed read must be retried, not left as a none-yet state"


def test_memory_list_fetch_throw_paints_the_calm_line() -> None:
    result = _settings(
        """
globalThis.fetch = async () => { throw new TypeError('Failed to fetch'); };
const host = document.getElementById('testHost');
window.VoolSettingsExtras.mountInto(host, 'memory');
await new Promise(setImmediate);
const list = host.querySelector('.vs-sec').querySelector('.vs-mem-list');
out({ html: String(list.innerHTML || '') });
"""
    )
    assert result["errors"] == []
    assert CALM in result["html"]
    assert "Nothing yet" not in result["html"]


def test_memory_list_real_ok_empty_is_the_only_none_yet_and_survives_a_later_failure() -> None:
    result = _settings(
        """
let __mode = 'empty';
globalThis.fetch = async (url) => {
  const path = new URL(String(url), 'http://vool.test').pathname;
  if (path !== '/api/memory/entries') return __json(200, {});
  if (__mode === 'empty') return __json(200, { ok: true, entries: [] });
  if (__mode === 'fail') return __json(503, { ok: false });
  return __json(200, { ok: true, entries: [{ record_id: 'mem_1', fact: 'Prefers dark mode', scope: 'preference', source: 'chat' }] });
};
const host = document.getElementById('testHost');
window.VoolSettingsExtras.mountInto(host, 'memory');
const list = host.querySelector('.vs-sec').querySelector('.vs-mem-list');
await new Promise(setImmediate);
const afterEmpty = String(list.innerHTML || '');
__mode = 'fail';
window.VoolSettingsExtras.refreshMemory();
await new Promise(setImmediate);
const afterFail = String(list.innerHTML || '');
__mode = 'rows';
await new Promise((resolve) => _st(resolve, 1300));   // the retry lands the real rows
const afterRetry = String(list.innerHTML || '');
out({ afterEmpty: afterEmpty, afterFail: afterFail, afterRetry: afterRetry });
"""
    )
    assert result["errors"] == []
    assert "Nothing yet" in result["afterEmpty"]
    assert CALM not in result["afterEmpty"]
    # A later failure keeps the last rendered state instead of repainting a none-yet over it.
    assert result["afterFail"] == result["afterEmpty"]
    assert "Prefers dark mode" in result["afterRetry"]
    assert CALM not in result["afterRetry"]


# ---- Model radar ------------------------------------------------------------------------------

_RADAR_SCRIPT = script_blocks(render_model_radar_fragment())[0]


def _radar(fetch_stub: str, body: str) -> dict:
    # The fragment refreshes its feed while the script evaluates, so the stub is installed first.
    return run_node(DOM + _JSON + fetch_stub + _RADAR_SCRIPT + body)


def test_radar_non_ok_first_load_never_says_quiet_is_the_design() -> None:
    result = _radar(
        """
let __calls = 0;
globalThis.fetch = async () => { __calls += 1; return __json(503, { ok: false, state: 'starting' }); };
""",
        """
window.VoolModelRadar.open();
await new Promise(setImmediate);
const html = String(document.body.querySelector('#mrPop').innerHTML || '');
await new Promise((resolve) => _st(resolve, 2200));   // eval load + open + one growing-delay retry
out({ html: html, calls: __calls });
""",
    )
    assert result["errors"] == []
    assert CALM in result["html"]
    assert "Nothing qualified yet" not in result["html"]
    assert result["calls"] >= 3, "the failed feed read must keep retrying"


def test_radar_real_ok_empty_is_the_only_quiet_and_a_later_failure_keeps_it() -> None:
    result = _radar(
        """
let __mode = 'ok';
globalThis.fetch = async () => {
  if (__mode === 'ok') return __json(200, { ok: true, findings: [], unread: 0, preferences: {}, open_conflicts: 0 });
  return __json(503, { ok: false });
};
""",
        """
window.VoolModelRadar.open();
await new Promise(setImmediate);
const pop = document.body.querySelector('#mrPop');
const afterOk = String(pop.innerHTML || '');
__mode = 'fail';
await window.VoolModelRadar.refresh();
await new Promise(setImmediate);
const afterFail = String(pop.innerHTML || '');
out({ afterOk: afterOk, afterFail: afterFail });
""",
    )
    assert result["errors"] == []
    assert "Nothing qualified yet" in result["afterOk"]
    assert CALM not in result["afterOk"]
    assert result["afterFail"] == result["afterOk"]


# ---- Council past-runs list --------------------------------------------------------------------

_COUNCIL_SCRIPT = script_blocks(render_council_fragment())[0]


def _council(runs_answer: str, body: str) -> dict:
    # The fragment auto-mounts while the script evaluates (its overlay stub is not hidden), so
    # the stub is installed before the script.
    stub = (
        """
let __calls = 0;
globalThis.fetch = async (url) => {
  const path = new URL(String(url), 'http://vool.test').pathname;
  if (path === '/api/council/runs') { __calls += 1; return """ + runs_answer + """; }
  return __json(200, { models: [] });
};
"""
    )
    return run_node(DOM + _JSON + stub + _COUNCIL_SCRIPT + body)


def _runs_text(body_extra: str = "") -> str:
    return (
        """
// The stub DOM answers every id with a live element, so the fragment's own "already mounted"
// check would always pass; let this one id read as absent so mount() really renders the form.
const __origById = document.getElementById;
document.getElementById = function(id){ return id === 'vccRoot' ? null : __origById(id); };
window.VoolCouncilConvene.mount();
document.getElementById = __origById;
const runs = document.getElementById('councilBody').querySelector('#vccRoot').querySelector('#vccRuns');
"""
        + body_extra
    )


def test_council_runs_non_ok_paints_the_calm_line_and_retries() -> None:
    result = _council(
        "__json(503, { ok: false, state: 'starting' })",
        _runs_text(
            """
await new Promise(setImmediate);
const text = String(runs.textContent || '');
await new Promise((resolve) => _st(resolve, 1300));   // the growing-delay retry fires
out({ text: text, calls: __calls });
"""
        ),
    )
    assert result["errors"] == []
    assert CALM in result["text"]
    assert result["calls"] >= 2, "a failed runs read must be retried, not left blank"


def test_council_runs_real_ok_empty_renders_nothing_and_not_the_calm_line() -> None:
    result = _council(
        "__json(200, { ok: true, runs: [] })",
        _runs_text(
            """
await new Promise(setImmediate);
out({ text: String(runs.textContent || '') });
"""
        ),
    )
    assert result["errors"] == []
    assert result["text"] == ""
    assert CALM not in result["text"]


def test_council_runs_real_ok_answer_renders_the_run_buttons() -> None:
    result = _council(
        "__json(200, { ok: true, runs: [{ run_id: 'run_keep_me', state: 'converged' }] })",
        _runs_text(
            """
await new Promise(setImmediate);
out({ html: String(runs.innerHTML || '') });
"""
        ),
    )
    assert result["errors"] == []
    assert "PAST RUNS" in result["html"]
    assert "run_keep_me" in result["html"]
    assert CALM not in result["html"]


# ---- Goal progress card ------------------------------------------------------------------------

_GOAL_SCRIPT = script_blocks(render_goal_progress_fragment())[0]


def _goal(body: str) -> dict:
    return run_node(DOM + _GOAL_SCRIPT + _JSON + body)


_GOAL_VIEW = (
    "{ session_id: 'chat:goal-1', goal_id: 'g1', revision: 1, state: 'running',"
    " objective: 'Ship the release notes', elapsed_seconds: 65, paused: false,"
    " has_plan: true, total: 3, completed: 1, current_step_id: 's2',"
    " steps: [{ id: 's1', state: 'completed', title: 'Draft' }, { id: 's2', state: 'running', title: 'Review' }] }"
)


def test_goal_card_non_ok_keeps_the_running_goal_visible() -> None:
    result = _goal(
        """
window.VoolPageActions = { displayedChat: function(){ return 'chat:goal-1'; } };
let __mode = 'ok';
globalThis.fetch = async () => __mode === 'ok'
  ? __json(200, { goal: """ + _GOAL_VIEW + """ })
  : __json(503, { ok: false, state: 'starting' });
window.VoolGoalCard.refresh();
await new Promise(setImmediate);
const card = document.getElementById('voolGoalCard');
const okObjective = String(document.getElementById('vgcObjective').textContent || '');
__mode = 'fail';
window.VoolGoalCard.refresh();
await new Promise(setImmediate);
out({
  okObjective: okObjective,
  okVisible: !card.classList.contains('vgc-hidden'),
  failObjective: String(document.getElementById('vgcObjective').textContent || ''),
  failVisible: !card.classList.contains('vgc-hidden'),
});
"""
    )
    assert result["errors"] == []
    assert result["okObjective"] == "Ship the release notes"
    assert result["okVisible"] is True
    # A failed state read is not a "no goal" answer: the card and its view stay.
    assert result["failObjective"] == "Ship the release notes"
    assert result["failVisible"] is True


def test_goal_card_first_load_failure_shows_the_calm_line_until_a_real_answer() -> None:
    result = _goal(
        """
window.VoolPageActions = { displayedChat: function(){ return 'chat:goal-1'; } };
let __mode = 'fail';
globalThis.fetch = async () => __mode === 'fail'
  ? __json(503, { ok: false, state: 'starting' })
  : __json(200, { goal: null });
window.VoolGoalCard.refresh();
await new Promise(setImmediate);
const card = document.getElementById('voolGoalCard');
const line = String(document.getElementById('vgcState').textContent || '');
const failVisible = !card.classList.contains('vgc-hidden');
__mode = 'ok-empty';
window.VoolGoalCard.refresh();
await new Promise(setImmediate);
out({ line: line, failVisible: failVisible,
      hiddenAfterOk: card.classList.contains('vgc-hidden') });
"""
    )
    assert result["errors"] == []
    assert result["line"] == CALM
    assert result["failVisible"] is True
    # Only a real OK answer without a goal may put the card away.
    assert result["hiddenAfterOk"] is True


def test_goal_card_fetch_throw_shows_the_calm_line_and_retries() -> None:
    result = _goal(
        """
window.VoolPageActions = { displayedChat: function(){ return 'chat:goal-1'; } };
let __calls = 0;
globalThis.fetch = async () => { __calls += 1; throw new TypeError('Failed to fetch'); };
window.VoolGoalCard.refresh();
await new Promise(setImmediate);
const line = String(document.getElementById('vgcState').textContent || '');
await new Promise((resolve) => _st(resolve, 1300));   // the growing-delay retry fires
out({ line: line, calls: __calls, visible: !document.getElementById('voolGoalCard').classList.contains('vgc-hidden') });
"""
    )
    assert result["errors"] == []
    assert result["line"] == CALM
    assert result["visible"] is True
    assert result["calls"] >= 2


# ---- Chat import sources ------------------------------------------------------------------------

_IMPORT_SCRIPT = script_blocks(render_chat_import_settings_fragment())[0]


def _import(body: str) -> dict:
    return run_node(DOM + _IMPORT_SCRIPT + _JSON + body)


def test_import_sources_non_ok_keeps_the_file_option_and_never_says_none_found() -> None:
    result = _import(
        """
let __calls = 0;
globalThis.fetch = async (url) => {
  const path = new URL(String(url), 'http://vool.test').pathname;
  if (path === '/api/chat/import/sources') { __calls += 1; return __json(403, { ok: false, error: 'owner_local_required' }); }
  return __json(200, {});
};
const host = document.getElementById('ciHost');
window.VoolChatImport.mountInto(host);
await new Promise(setImmediate);
const area = String(host.textContent || '');
await new Promise((resolve) => _st(resolve, 1300));   // the growing-delay retry fires
out({ area: area, calls: __calls });
"""
    )
    assert result["errors"] == []
    assert CALM in result["area"]
    assert "No other AI apps were found" not in result["area"]
    assert "A data export file" in result["area"], "the file-import option stays usable during the outage"
    assert result["calls"] >= 2, "a failed sources read must be retried"


def test_import_sources_real_ok_empty_says_none_found() -> None:
    result = _import(
        """
globalThis.fetch = async (url) => {
  const path = new URL(String(url), 'http://vool.test').pathname;
  if (path === '/api/chat/import/sources') return __json(200, { ok: true, local: [] });
  return __json(200, {});
};
const host = document.getElementById('ciHost');
window.VoolChatImport.mountInto(host);
await new Promise(setImmediate);
out({ area: String(host.textContent || '') });
"""
    )
    assert result["errors"] == []
    assert "No other AI apps were found" in result["area"]
    assert CALM not in result["area"]
    assert "A data export file" in result["area"]
