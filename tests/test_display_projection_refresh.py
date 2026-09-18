"""Laws for the two display-staleness defects the beta release recorded.

> a project breadcrumb showed General after restart until the chat was reselected,
> and the model menu's routing summary could reflect the prior profile while the
> per-chat model selector showed the actual selection.

Both live at the projection seam: the SERVER owns project binding and model routing;
the page must project the server's truth whenever the displayed chat changes or a
switch lands, not only once at boot. These laws boot the REAL page script in the
shipped node harness against a fetch override that answers with server truth, then
drive the exact user flows (restart into a project chat; switch chats; pin a model).

No law here touches routing or project permissions: only what the surfaces display.
"""
from __future__ import annotations

import functools
import re
import pytest

from core.vool_chat_page import render_vool_chat_html
from tests.chat_page_js_harness import DOM, run_node

HTML = render_vool_chat_html()

A = "openclaw:aaaaaaaaaaaaaaaaaaaa"   # project chat 'Depot counts' with a pinned free model
B = "openclaw:bbbbbbbbbbbbbbbbbbbb"   # general chat 'Amber violet' on Auto
PIN = "nvidia/nemotron-3.5-lightning:free"


def page_scripts() -> list[str]:
    found = re.findall(r"<script[^>]*>(.*?)</script>", HTML, re.DOTALL)
    wanted = [script for script in found if "renderContextBar" in script or "hydrateModelFromServer" in script]
    assert wanted, f"no inline script defines the context bar; {len(found)} scripts on the page"
    return wanted


PROGRAM_BODY = r"""
// Server truth for the probe, installed BEFORE the page script boots.
const SERVER = {
  projects: { ok: true, projects: [
    { id: 'p1', name: 'Depot robotics', root: '/w/depot', emoji: 'P', color: '#3bbfa9' },
  ]},
  sessions: { ok: true, counts: {}, sessions: [
    { session_id: 'A_ID', title: 'Depot counts', project_id: 'p1', updated_at: 2, emoji: '', color: '' },
    { session_id: 'B_ID', title: 'Amber violet', project_id: '', updated_at: 1, emoji: '', color: '' },
  ]},
  model_A: { ok: true, model: 'PIN_ID', provider: 'openrouter', cost_state: 'free', free_cloud_enabled: true, auto_free_model: 'PIN_ID' },
  model_B: { ok: true, model: '', provider: '', cost_state: '', free_cloud_enabled: true, auto_free_model: 'PIN_ID' },
  pin_verdict: { ok: true, model: 'PIN_ID', provider: 'openrouter', session_id: 'B_ID', cost_state: 'free', selection_source: 'server' },
};
const respond = (u, method) => {
  const url = decodeURIComponent(String(u));
  if (url.startsWith('/api/projects')) return SERVER.projects;
  if (url.startsWith('/api/chat/sessions')) return SERVER.sessions;
  if (url.startsWith('/api/cloud/model') && method === 'POST') {
    // The real endpoint persists the pin; later GETs must answer with it.
    SERVER.model_B = { ...SERVER.model_B, model: SERVER.pin_verdict.model, provider: SERVER.pin_verdict.provider, cost_state: SERVER.pin_verdict.cost_state };
    return SERVER.pin_verdict;
  }
  if (url.startsWith('/api/cloud/model') && url.includes('A_ID')) return SERVER.model_A;
  if (url.startsWith('/api/cloud/model') && url.includes('B_ID')) return SERVER.model_B;
  if (url.startsWith('/api/chat/history')) return { messages: [] };
  return { ok: true, sessions: [], messages: [], queue: [], pins: [], projects: [], models: [], events: [], counts: {}, data: [] };
};
globalThis.fetch = async (u, opts) => ({ ok: true, status: 200, headers: { get: () => null },
  json: async () => respond(u, opts && opts.method), text: async () => '{}', blob: async () => ({}) });
localStorage.setItem('vool.sessionId', 'BOOT_ID');
""".replace("A_ID", A).replace("B_ID", B).replace("PIN_ID", PIN)

LAWS = r"""
const tp = await import('node:timers/promises');   // page timers are unref'd; keep the loop alive
const laws = {};
async function law(name, fn) {
  try { await fn(); laws[name] = "PASS"; }
  catch (e) { laws[name] = "FAIL: " + ((e && e.message) || e); }
}
function assert(cond, msg) { if (!cond) throw new Error(msg || "assertion failed"); }
const chipText = () => document.getElementById('ctxBar').textContent || '';
const provHtml = () => { const el = document.getElementById('modelProv'); return el ? el.innerHTML : ''; };
const settle = (ms) => tp.setTimeout(ms || 120);
const defs = {};

defs.D1_restart_shows_the_project_not_general = async () => {
  await settle(200);   // boot: loadSessions -> renderSessions -> renderContextBar
  const t = chipText();
  assert(t.indexOf('Depot robotics') !== -1, 'context bar shows the project after restart; got: ' + t);
  assert(t.indexOf('General') === -1, 'no stale General chip after restart; got: ' + t);
  assert((document.getElementById('ctxBar').title || '').indexOf('Depot robotics') === 0,
    'bar tooltip carries the project path; got: ' + document.getElementById('ctxBar').title);
};

defs.D2_general_chat_keeps_the_general_chip = async () => {
  await openSession('B_ID');
  await settle(200);
  const t = chipText();
  assert(t.indexOf('General') !== -1, 'general chat shows General; got: ' + t);
  assert(t.indexOf('Depot robotics') === -1, 'project chip gone after switch; got: ' + t);
};

defs.D3_switch_back_restores_the_project_chip = async () => {
  await openSession('A_ID');
  await settle(200);
  assert(chipText().indexOf('Depot robotics') !== -1, 'project chip restored; got: ' + chipText());
};

defs.D4_routing_summary_follows_the_chat_you_switch_into = async () => {
  await settle(200);           // boot hydrate painted chat A's pin
  assert(provHtml().indexOf('Pinned') !== -1, 'boot painted a verdict');
  await openSession('B_ID');   // B: server says "none — VOOL Auto routing"
  await settle(300);
  const h = provHtml();
  assert(h.indexOf('none \u2014 VOOL Auto routing') !== -1, 'strip follows chat B (Auto); got: ' + h);
  assert(h.indexOf('openrouter \u00b7') === -1, 'chat A pin no longer shown on B; got: ' + h);
};

defs.D5_routing_summary_follows_a_successful_model_switch = async () => {
  // Own boot on chat B (Auto): the strip must NOT already hold the pin, so the only way
  // the pin can appear is the switch path itself painting the server's POST verdict.
  await settle(200);
  const pre = provHtml();
  assert(pre.indexOf('none \u2014 VOOL Auto routing') !== -1, 'precondition: chat B boots Auto; got: ' + pre);
  const ok = await switchCloudModel('PIN_ID', 'Nemotron', null);
  assert(ok === true, 'the switch itself landed');
  await settle(120);
  const h = provHtml();
  assert(h.indexOf('openrouter \u00b7 PIN_ID') !== -1, 'strip Pinned row shows the just-pinned model; got: ' + h);
};

defs.D6_a_chat_with_no_session_record_is_left_alone = async () => {
  // newChat() points at a fresh id the server has never listed: nothing may adopt a
  // project for it, and the chip stays General (negative control for the adoption rule).
  await newChat();
  await settle(120);
  const t = chipText();
  assert(t.indexOf('General') !== -1, 'fresh chat shows General; got: ' + t);
  assert(view.projectId === '', 'view.projectId untouched for an unlisted chat; got: ' + view.projectId);
};

for (const name of RUN_LIST) await law(name, defs[name]);
out({ laws });
""".replace("A_ID", A).replace("B_ID", B).replace("PIN_ID", PIN)


def _run(boot: str, run_list: list[str]) -> dict:
    body = PROGRAM_BODY.replace("BOOT_ID", boot)
    laws = "const RUN_LIST = " + str(run_list).replace("'", '"') + ";\n" + LAWS
    program = DOM + "\n;(async function(){\n" + body + "\n;\n".join(page_scripts()) + "\n" + laws + "\n})();\n"
    result = run_node(program, timeout=120)
    assert not result.get("errors"), f"page booted with errors: {result['errors']}"
    return result["laws"]


@functools.lru_cache(maxsize=1)
def _laws_project_boot() -> dict:
    return _run(A, [name for name in
                    ("D1_restart_shows_the_project_not_general", "D2_general_chat_keeps_the_general_chip",
                     "D3_switch_back_restores_the_project_chip",
                     "D4_routing_summary_follows_the_chat_you_switch_into",
                     "D6_a_chat_with_no_session_record_is_left_alone")])


@functools.lru_cache(maxsize=1)
def _laws_auto_boot() -> dict:
    return _run(B, ["D5_routing_summary_follows_a_successful_model_switch"])


def test_restart_into_a_project_chat_shows_the_project_not_general():
    results = _laws_project_boot()
    assert results["D1_restart_shows_the_project_not_general"] == "PASS", results


def test_switching_chats_swaps_the_project_chip_correctly():
    results = _laws_project_boot()
    assert results["D2_general_chat_keeps_the_general_chip"] == "PASS", results
    assert results["D3_switch_back_restores_the_project_chip"] == "PASS", results


def test_routing_summary_follows_a_chat_switch():
    results = _laws_project_boot()
    assert results["D4_routing_summary_follows_the_chat_you_switch_into"] == "PASS", results


def test_routing_summary_follows_a_model_switch():
    results = _laws_auto_boot()
    assert results["D5_routing_summary_follows_a_successful_model_switch"] == "PASS", results


def test_unlisted_chat_is_never_given_a_project():
    results = _laws_project_boot()
    assert results["D6_a_chat_with_no_session_record_is_left_alone"] == "PASS", results


@pytest.mark.parametrize('stale_fails', [False, True])
@pytest.mark.parametrize('change_chat', [False, True])
def test_late_provenance_response_cannot_replace_or_clear_current_selection(stale_fails, change_chat):
    probe = r"""
const tp = await import('node:timers/promises');
await tp.setTimeout(250);
const pending = [];
const originalFetch = globalThis.fetch;
globalThis.fetch = (u, opts) => String(u).startsWith('/api/cloud/model')
  ? new Promise((resolve, reject) => pending.push({resolve, reject}))
  : originalFetch(u, opts);
const old = refreshSelectionProvenance();
if (CHANGE_CHAT) await openSession('B_ID');
const fresh = refreshSelectionProvenance();
const reply = value => ({ok: true, json: async () => value});
pending[pending.length - 1].resolve(reply({...SERVER.model_A, model: 'current-provider/current-model'}));
await fresh;
const before = document.getElementById('modelProv').innerHTML;
if (STALE_FAILS) pending[0].reject(new Error('old request disconnected'));
else pending[0].resolve(reply({...SERVER.model_A, model: 'stale-provider/stale-model'}));
await old;
const after = document.getElementById('modelProv').innerHTML;
out({before, after});
""".replace('B_ID', B).replace('CHANGE_CHAT', str(change_chat).lower()).replace('STALE_FAILS', str(stale_fails).lower())
    program = DOM + '\n;(async function(){\n' + PROGRAM_BODY.replace('BOOT_ID', A) + '\n;\n'.join(page_scripts()) + probe + '\n})();\n'
    result = run_node(program, timeout=120)
    assert not result.get('errors'), result
    assert 'current-provider/current-model' in result['before'], result
    assert result['after'] == result['before'], result
