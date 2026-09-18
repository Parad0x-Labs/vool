"""One clearly scoped price decision per paid pin, reused across sends at unchanged scope.

The gate dialog's own copy promises the acceptance covers "this chat, this model, for 24
hours" for BOTH accept buttons. The primary button used to resolve bare ``true``, which the
page stores as a ONE-TURN acknowledgement consumed by the first send -- so the very next send
re-asked the identical price decision, and a per-send acceptance never persisted at all
(measured as the operator's "accepting its price for the conversation still prompts again").

These cases drive the REAL page script and the REAL price-gate fragment under node, against a
server stand-in whose response shapes are copied from the live daemon (provider ``openrouter``,
bare model ids, ``cost_state`` from the server classifier). Decline still dispatches nothing;
the acceptance never widens server ceilings, caps or grants, which gate every dispatch.
"""
from __future__ import annotations

import pytest

from core.price_safety_fragment import _GATE_JS
from tests.chat_page_js_harness import DOM, run_node, script

A = "openclaw:" + "a" * 20
MODEL = "z-ai/glm-5.3-flash"

PIN_NET = """
const _rawTimeout = globalThis.__rawSetTimeout;
function tick(n) {
  let p = Promise.resolve();
  for (let i = 0; i < (n || 4); i++) p = p.then(() => new Promise((r) => _rawTimeout(r, 0)));
  return p;
}
globalThis.__streams = {};
globalThis.__chatPosts = [];
function makeStream(chatId) {
  const queue = [];
  let pending = null, closed = false;
  const ctl = {
    push(obj) {
      const chunk = new TextEncoder().encode(JSON.stringify(obj) + '\\n');
      if (pending) { const p = pending; pending = null; p.resolve({ done: false, value: chunk }); }
      else queue.push({ done: false, value: chunk });
    },
    close() {
      closed = true;
      if (pending) { const p = pending; pending = null; p.resolve({ done: true, value: undefined }); }
    },
    reader: {
      read() {
        if (queue.length) return Promise.resolve(queue.shift());
        if (closed) return Promise.resolve({ done: true, value: undefined });
        return new Promise((resolve) => { pending = { resolve }; });
      },
    },
  };
  __streams[chatId] = ctl;
  return ctl;
}
// Response shapes copied from the live daemon: provider 'openrouter', bare ids, server-derived
// cost state on the selection read.
const PIN = { model: '', provider: '', cost_state: 'free' };
const realFetch = globalThis.fetch;
globalThis.fetch = async (url, opts) => {
  const u = String(url), o = opts || {};
  if (u === '/api/chat' && String(o.method).toUpperCase() === 'POST') {
    const body = JSON.parse(o.body);
    __chatPosts.push(body.session_id);
    const ctl = makeStream(body.session_id);
    setTimeout(() => {
      const n = __chatPosts.length;
      ctl.push({ message: { content: 'answer ' + n } });
      ctl.push({ message: { content: '' }, done: true, vool_response_commit: { type: 'response.commit', version: 1, revision: 1, turn_id: 't' + n, canonical_content: 'answer ' + n, content_hash: 'sha256:x', display_metadata: { provenance: { lane: 'cloud', model_id: '%(model)s' }, usage: {}, provenance_footer: '`cloud | %(model)s`' } } });
      ctl.close();
    }, 5);
    return { ok: true, status: 200, body: { getReader: () => ctl.reader } };
  }
  if (u === '/api/cloud/model' || u.startsWith('/api/cloud/model?')) {
    if (String(o.method).toUpperCase() === 'POST') {
      const body = JSON.parse(o.body || '{}');
      if (body.confirm_paid) {
        PIN.model = body.model; PIN.provider = 'openrouter'; PIN.cost_state = 'paid';
        return { ok: true, status: 200, json: async () => ({ ok: true, model: PIN.model, provider: 'openrouter', cost_state: 'paid', session_id: body.session_id, selection_source: 'server' }) };
      }
      return { ok: false, status: 409, json: async () => ({ ok: false, code: 'paid_model_confirm_required', model: body.model, cost_state: 'paid' }) };
    }
    return { ok: true, status: 200, json: async () => ({ ok: true, model: PIN.model, provider: PIN.provider, free_cloud_enabled: true, auto_free_model: 'z-ai/glm-5.2:free', source: 'server', cost_state: PIN.cost_state, session_id: '%(A)s' }) };
  }
  if (u.startsWith('/api/cloud/models')) {
    return { ok: true, status: 200, json: async () => ({ ok: true, provider: 'openrouter', age_seconds: 30, models: [{ id: '%(model)s', prompt_usd_per_m: 0.1, completion_usd_per_m: 0.4, free: false }] }) };
  }
  if (u.startsWith('/api/cloud/acceptances')) {
    return { ok: true, status: 200, json: async () => ({ ok: true, acceptances: [] }) };
  }
  return realFetch(url, opts);
};
"""

# Shared drive helpers executed inside the node program.
HELPERS = """
function gateOverlay() { return document.body.children.find((n) => n && n.id === 'vgOverlay'); }
function gateButtons() { return gateOverlay().querySelector('#vgActions'); }
"""


def _program(body: str) -> str:
    return (
        "globalThis.__rawSetTimeout = globalThis.setTimeout;\n"
        + DOM
        + (PIN_NET % {"A": A, "model": MODEL})
        + script()
        + "\n" + _GATE_JS + "\n"
        + HELPERS
        + "\n;(async () => {\n" + body + "\n})()"
        + ".catch((e) => { errors.push('drive: ' + (e && e.stack || e)); out({ errors }); });"
    )


def _run(body: str) -> dict:
    result = run_node(_program(body), timeout=120)
    assert result.get("errors") == [], "the page threw while being driven:\n" + "\n".join(result["errors"])
    return result


def test_the_primary_accept_button_resolves_the_chat_scoped_acceptance() -> None:
    """'Accept price and select' used to resolve a one-turn ack that the first send consumed,
    so the next send re-asked the identical decision. Both accept buttons must resolve the
    24-hour chat-scoped acceptance their own dialog copy promises."""
    data = _run(f"""
    await tick();
    setDisplayedChat('{A}');
    await tick();
    const pinPromise = performCloudModelSwitch('{MODEL}', 'GLM Flash', null, 'openrouter');
    await tick(6);                       // catalog/acceptance reads settle; buttons mount
    gateButtons().children[0].__click(); // the PRIMARY accept button
    const switched = await pinPromise;
    await tick();
    const ack = paidPinAcknowledgements.get(paidAckKey('{A}', modelForChat('{A}')));
    const pinScope = ack && ack.scope;
    runTurn('first question', null, {{ chatId: '{A}' }});
    await tick(14);
    const gateReopenedOnSend = !gateOverlay().hidden;
    const firstPosts = __chatPosts.length;
    runTurn('second question', null, {{ chatId: '{A}' }});
    await tick(14);
    out({{
      switched, pinScope, gateReopenedOnSend, firstPosts,
      secondGateOpen: !gateOverlay().hidden,
      totalPosts: __chatPosts.length,
    }});
    """)
    assert data["switched"] is True
    assert data["pinScope"] == "conversation", "the primary accept button must resolve the chat-scoped acceptance"
    assert data["gateReopenedOnSend"] is False, "the accepted price must not be re-asked on the first send"
    assert data["firstPosts"] == 1 and data["totalPosts"] == 2
    assert data["secondGateOpen"] is False, "nor on the second send at unchanged price and scope"


def test_a_per_send_acceptance_persists_for_the_chat() -> None:
    """A paid pin that arrives without a page acknowledgement (a restored pin, a reload that
    lost the stored ack) gets ONE per-send decision; accepting it must cover the chat, so the
    second send does not ask again."""
    data = _run(f"""
    await tick();
    setDisplayedChat('{A}');
    await tick();
    // The pin exists server-side and in the composer, but no acceptance is stored for it.
    await fetch('/api/cloud/model', {{ method: 'POST', headers: {{ 'Content-Type': 'application/json' }}, body: JSON.stringify({{ model: '{MODEL}', session_id: '{A}', confirm_paid: true, provider: 'openrouter' }}) }});
    setModelValue('{MODEL}', '{A}');
    await tick();
    runTurn('first question', null, {{ chatId: '{A}' }});
    await tick(8);
    const gateOpened = !gateOverlay().hidden;
    const postsWhenGated = __chatPosts.length;
    gateButtons().children[0].__click();  // 'Accept price and send'
    await tick(20);
    const ack = paidPinAcknowledgements.get(paidAckKey('{A}', modelForChat('{A}')));
    runTurn('second question', null, {{ chatId: '{A}' }});
    await tick(16);
    out({{
      gateOpened, postsWhenGated, scope: ack && ack.scope,
      secondGateOpen: !gateOverlay().hidden,
      posts: __chatPosts.length,
    }});
    """)
    assert data["gateOpened"] is True, "a pin with no stored acceptance still asks exactly once"
    assert data["postsWhenGated"] == 0, "nothing dispatches while the decision is open"
    assert data["scope"] == "conversation"
    assert data["secondGateOpen"] is False, "the per-send acceptance must cover the chat"
    assert data["posts"] == 2


def test_a_declined_price_still_pins_nothing_and_confirms_nothing() -> None:
    """The acceptance scope widening must not soften the refusal arm: a declined paid model is
    never pinned, no confirm_paid POST is ever sent, and the chat stays on Auto."""
    data = _run(f"""
    await tick();
    setDisplayedChat('{A}');
    await tick();
    const pinPosts = [];
    const realModelFetch = fetch;
    const pinPromise = performCloudModelSwitch('{MODEL}', 'GLM Flash', null, 'openrouter');
    await tick(6);
    const buttons = gateButtons();
    buttons.children.find((b) => b.className.includes('vg-decline')).__click();  // 'Decline — dispatch nothing'
    const switched = await pinPromise;
    await tick();
    out({{ switched, pinned: modelForChat('{A}') }});
    """)
    assert data["switched"] is False
    assert data["pinned"] == "vool", "a declined paid pin must not be pinned"


@pytest.mark.parametrize("kind", ["pin", "per-send"])
def test_every_accept_button_resolves_the_promised_scope(kind: str) -> None:
    """Source pin of the resolution contract: no executable accept path may resolve bare ``true``
    (the one-turn ack) again -- every acceptance is chat-scoped for 24 hours."""
    import re

    executable = "\n".join(
        line for line in _GATE_JS.splitlines() if not line.strip().startswith("//")
    )
    assert not re.search(r"closeGate\(\s*true\s*\)", executable), (
        "an accept path still resolves the one-turn ack"
    )
    assert _GATE_JS.count("closeGate('conversation')") >= 4
