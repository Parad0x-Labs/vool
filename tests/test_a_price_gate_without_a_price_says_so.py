"""The price dialog's buttons never say "Accept price" when no price is known.

Tracker row 132 (TDL-L30, seen in the v0.7 demo video): picking a cloud model whose cost the
server could not verify opened a dialog titled "MODEL COST UNKNOWN" whose main button read
"Accept price and select". There was no price to accept. The buttons now say that the price is
unknown ("Select without a known price", "Allow for this chat without a known price (24 hours)",
"Send without a known price"); a model with published rates keeps "Accept price and ...".

These cases drive the real chat page script and the real price-gate fragment under node, against
a server stand-in whose answers have the live daemon's shapes (409 with the server's own code on
the first pin, the catalog read, the acceptances read). Only the wording changes: every button
still resolves the same chat-scoped decision, and declining still pins nothing.
"""
from __future__ import annotations

import json

import pytest

from core.price_safety_fragment import _GATE_JS
from tests.chat_page_js_harness import DOM, run_node, script

A = "openclaw:" + "b" * 20
MODEL = "acme/unpriced-model-1"

NET = """
const _rawTimeout = globalThis.__rawSetTimeout;
function tick(n) {
  let p = Promise.resolve();
  for (let i = 0; i < (n || 4); i++) p = p.then(() => new Promise((r) => _rawTimeout(r, 0)));
  return p;
}
const CODE = %(code)s;
const ROWS = %(rows)s;
const PIN = { model: '', provider: '', cost_state: 'free' };
globalThis.__pinPosts = [];
const realFetch = globalThis.fetch;
globalThis.fetch = async (url, opts) => {
  const u = String(url), o = opts || {};
  if (u === '/api/cloud/model' || u.startsWith('/api/cloud/model?')) {
    if (String(o.method).toUpperCase() === 'POST') {
      const body = JSON.parse(o.body || '{}');
      __pinPosts.push(!!body.confirm_paid);
      if (body.confirm_paid) {
        PIN.model = body.model; PIN.provider = 'openrouter'; PIN.cost_state = 'unknown';
        return { ok: true, status: 200, json: async () => ({ ok: true, model: PIN.model, provider: 'openrouter', cost_state: 'unknown', session_id: body.session_id, selection_source: 'server' }) };
      }
      return { ok: false, status: 409, json: async () => ({ ok: false, code: CODE, model: body.model, cost_state: 'unknown' }) };
    }
    return { ok: true, status: 200, json: async () => ({ ok: true, model: PIN.model, provider: PIN.provider, free_cloud_enabled: true, auto_free_model: 'z-ai/glm-5.2:free', source: 'server', cost_state: PIN.cost_state, session_id: '%(A)s' }) };
  }
  if (u.startsWith('/api/cloud/models')) {
    return { ok: true, status: 200, json: async () => ({ ok: true, provider: 'openrouter', age_seconds: 30, models: ROWS }) };
  }
  if (u.startsWith('/api/cloud/acceptances')) {
    return { ok: true, status: 200, json: async () => ({ ok: true, acceptances: [] }) };
  }
  return realFetch(url, opts);
};
"""

HELPERS = """
function gateOverlay() { return document.body.children.find((n) => n && n.id === 'vgOverlay'); }
function gateButtons() { return gateOverlay().querySelector('#vgActions'); }
function gateWords() {
  const buttons = gateButtons().children;
  return { confirm: buttons[0].textContent, chat: buttons[1].textContent, title: gateOverlay().querySelector('#vgHead').textContent };
}
"""


def _program(body: str, *, code: str, rows: list[dict]) -> str:
    return (
        "globalThis.__rawSetTimeout = globalThis.setTimeout;\n"
        + DOM
        + (NET % {"A": A, "code": json.dumps(code), "rows": json.dumps(rows)})
        + script()
        + "\n" + _GATE_JS + "\n"
        + HELPERS
        + "\n;(async () => {\n" + body + "\n})()"
        + ".catch((e) => { errors.push('drive: ' + (e && e.stack || e)); out({ errors }); });"
    )


def _pin_and_read(*, code: str, rows: list[dict], press: str = "") -> dict:
    """Pin MODEL, read the dialog's words once the catalog settled, then press a button if asked."""
    press_js = {
        "": "",
        "confirm": "gateButtons().children[0].__click();",
        "chat": "gateButtons().children[1].__click();",
        "decline": "gateButtons().children.find((b) => b.className.includes('vg-decline')).__click();",
    }[press]
    body = f"""
    await tick();
    setDisplayedChat('{A}');
    await tick();
    const pinPromise = performCloudModelSwitch('{MODEL}', 'Unpriced model', null, 'openrouter');
    await tick(8);
    const open = !gateOverlay().hidden;
    const words = gateWords();
    let switched = null;
    if ({json.dumps(bool(press))}) {{
      {press_js}
      switched = await pinPromise;
      await tick();
    }}
    const ack = paidPinAcknowledgements.get(paidAckKey('{A}', modelForChat('{A}')));
    out({{ open, words, switched, pinned: modelForChat('{A}'), scope: ack && ack.scope, pinPosts: __pinPosts }});
    """
    result = run_node(_program(body, code=code, rows=rows), timeout=120)
    assert result.get("errors") == [], "the page threw while being driven:\n" + "\n".join(result["errors"])
    return result


PRICED_ROW = {"id": MODEL, "prompt_usd_per_m": 0.5, "completion_usd_per_m": 2.0, "free": False}


@pytest.mark.parametrize("code", ["MODEL_COST_UNKNOWN", "PAID_STATUS_UNKNOWN"])
def test_a_model_whose_cost_the_server_cannot_verify_is_not_offered_as_accept_price(code: str) -> None:
    data = _pin_and_read(code=code, rows=[])

    assert data["open"] is True
    words = data["words"]
    assert "Accept price" not in words["confirm"] and "Accept price" not in words["chat"], words
    assert words["confirm"] == "Select without a known price", words
    assert words["chat"] == "Allow for this chat without a known price (24 hours)", words


def test_a_paid_model_with_no_published_rates_says_the_price_is_unknown() -> None:
    """The server says paid, but the catalog lists no rate for it: there is still no price."""
    data = _pin_and_read(code="paid_model_confirm_required", rows=[{"id": MODEL, "free": False}])

    assert data["words"]["confirm"] == "Select without a known price", data["words"]
    assert data["words"]["chat"] == "Allow for this chat without a known price (24 hours)", data["words"]


def test_a_model_with_published_rates_keeps_accept_price() -> None:
    data = _pin_and_read(code="paid_model_confirm_required", rows=[PRICED_ROW])

    assert data["words"]["confirm"] == "Accept price and select", data["words"]
    assert data["words"]["chat"] == "Accept price for this chat (24 hours)", data["words"]


@pytest.mark.parametrize("press", ["confirm", "chat"])
def test_the_unknown_price_buttons_still_resolve_the_chat_scoped_decision(press: str) -> None:
    """Only the words changed: pressing either button pins the model for this chat, with the
    second, confirmed POST, exactly as the priced buttons do."""
    data = _pin_and_read(code="MODEL_COST_UNKNOWN", rows=[], press=press)

    assert data["switched"] is True
    assert data["pinned"] == MODEL
    assert data["scope"] == "conversation"
    assert data["pinPosts"] == [False, True], "one unconfirmed pin, then the confirmed one"


def test_declining_an_unknown_price_still_pins_nothing() -> None:
    data = _pin_and_read(code="MODEL_COST_UNKNOWN", rows=[], press="decline")

    assert data["switched"] is False
    assert data["pinned"] == "vool"
    assert data["pinPosts"] == [False], "no confirmed pin is ever sent after a decline"


def test_a_per_send_decision_without_a_price_says_send_without_a_known_price() -> None:
    """A restored pin with no stored acceptance asks once before the send; with no rate known the
    button names that, not "Accept price and send"."""
    body = f"""
    await tick();
    setDisplayedChat('{A}');
    await tick();
    const decision = window.VoolPriceGate.review({{ kind: 'per-send', id: '{MODEL}', provider: 'openrouter', label: 'Unpriced model', code: 'paid_model_confirm_required' }});
    await tick(8);
    const words = gateWords();
    gateButtons().children.find((b) => b.className.includes('vg-decline')).__click();
    const resolved = await decision;
    out({{ words, resolved }});
    """
    result = run_node(_program(body, code="paid_model_confirm_required", rows=[]), timeout=120)
    assert result.get("errors") == [], "\n".join(result["errors"])
    assert result["words"]["confirm"] == "Send without a known price", result["words"]
    assert result["resolved"] is False


def test_every_shipped_language_has_the_unknown_price_words() -> None:
    import pathlib

    catalogs = pathlib.Path(__file__).resolve().parents[1] / "core" / "i18n" / "catalogs"
    keys = ("pay.btn_select_price_unknown", "pay.btn_send_price_unknown", "pay.btn_chat_price_unknown")
    for path in sorted(catalogs.glob("*.json")):
        messages = json.loads(path.read_text(encoding="utf-8"))["messages"]
        for key in keys:
            assert messages.get(key), f"{path.name} lacks {key}"
            if path.stem != "en":
                assert "Accept" not in messages[key], f"{path.name} {key} still reads as English"
