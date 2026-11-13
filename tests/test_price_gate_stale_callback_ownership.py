"""The price gate's async price reads belong to the review that started them.

The gate is one long-lived overlay; the price and acceptance reads behind a review are
asynchronous; the operator can decline, or a newer review can replace the open one, before a reply
lands. Measured broken in the browser 2026-09-16: a superseded callback approved and closed
whatever NEWER review was open, and a superseded FAILURE painted its error into the newer dialog.

The UsePod limits save that first showed this left VOOL in 0.8. The reads that remain carry the
same contract, and these cases drive the REAL gate fragment under node to prove it:

* every async hop re-checks that its review is still the open one (``activeResolve === resolve``)
  and, if not, returns before any UI effect: an obsolete reply never paints, never shows an error,
  never shows Retry, never enables, approves or closes another review;
* an escaped review (``activeResolve`` is null) is exactly as superseded as a replaced one;
* a live failure keeps the review open: the error and Retry show, the accept buttons stay
  disabled, nothing is approved, and Retry can still complete the review;
* the chat page carries no price-review action button and no JSON packed into an attribute (the
  wedge the browser drive found on the old Review price limits button).
"""
from __future__ import annotations

from core.price_safety_fragment import _GATE_JS
from tests.chat_page_js_harness import DOM, run_node

NET = """
const _rawTimeout = globalThis.__rawSetTimeout;
function tick(n) {
  let p = Promise.resolve();
  for (let i = 0; i < (n || 6); i++) p = p.then(() => new Promise((r) => _rawTimeout(r, 0)));
  return p;
}
// Catalog reads are HELD per provider until the drive releases them, so a reply can be made to
// land after its review was replaced or declined.
const held = {};
// The acceptances read answers at once unless the drive holds it (globalThis.__holdAcceptances).
const heldAcceptances = [];
globalThis.__holdAcceptances = false;
globalThis.fetch = (url) => {
  const u = String(url);
  if (u.startsWith('/api/cloud/acceptances')) {
    if (globalThis.__holdAcceptances) return new Promise((resolve) => { heldAcceptances.push(resolve); });
    return Promise.resolve({ ok: true, status: 200, json: async () => ({ ok: true, acceptances: [] }) });
  }
  if (u.startsWith('/api/cloud/models')) {
    const provider = new URL(u, 'http://127.0.0.1').searchParams.get('provider');
    return new Promise((resolve) => { (held[provider] = held[provider] || []).push(resolve); });
  }
  return Promise.resolve({ ok: true, status: 200, json: async () => ({}) });
};
function releaseAcceptances(ok) {
  const resolve = heldAcceptances.shift();
  resolve(ok
    ? { ok: true, status: 200, json: async () => ({ ok: true, acceptances: [] }) }
    : { ok: false, status: 503, json: async () => ({}) });
}
function release(provider, ok, id) {
  const resolve = held[provider].shift();
  resolve(ok
    ? { ok: true, status: 200, json: async () => ({ ok: true, provider, age_seconds: 30,
        models: [{ id, prompt_usd_per_m: 9.5, completion_usd_per_m: 19.5, free: false }] }) }
    : { ok: false, status: 503, json: async () => ({}) });
}
"""

HELPERS = """
function overlay() { return document.body.children.find((n) => n && n.id === 'vgOverlay'); }
function actions() { return overlay().querySelector('#vgActions').children; }
function retryShown() { return actions().filter((b) => b.textContent === 'Retry price check' && !b.hidden).length; }
function view() {
  return {
    open: !overlay().hidden,
    head: overlay().querySelector('#vgHead').textContent,
    body: overlay().querySelector('#vgBody').innerHTML,
    error: document.getElementById('vgError').textContent,
    retry: retryShown(),
    acceptDisabled: [actions()[0].disabled, actions()[1].disabled],
  };
}
function track(promise) { const box = { value: 'pending' }; promise.then((v) => { box.value = v; }); return box; }
const A = { kind: 'pin', code: 'paid_model_confirm_required', provider: 'openrouter', id: 'acme/model-a', label: 'Model A' };
const B = { kind: 'pin', code: 'paid_model_confirm_required', provider: 'groq', id: 'llama-b', label: 'Model B' };
"""


def _drive(body: str, *, prelude: str = "") -> dict:
    program = (
        "globalThis.__rawSetTimeout = globalThis.setTimeout;\n"
        + DOM
        + NET
        + prelude
        + "\n" + _GATE_JS + "\n"
        + HELPERS
        + "\n;(async () => {\nawait tick();\n" + body + "\n})()"
        + ".catch((e) => { errors.push('drive: ' + (e && e.stack || e)); out({ errors }); });"
    )
    result = run_node(program, timeout=120)
    assert result.get("errors") == [], "the gate threw while being driven:\n" + "\n".join(result["errors"])
    return result


def test_a_replaced_reviews_late_failure_never_paints_into_the_newer_review() -> None:
    data = _drive("""
    const a = track(window.VoolPriceGate.review(A));
    await tick();
    const b = track(window.VoolPriceGate.review(B));
    await tick();
    release('openrouter', false);          // A's catalog read fails AFTER B replaced it
    await tick(10);
    const afterStale = view();
    release('groq', true, 'llama-b');      // B's own read lands
    await tick(10);
    out({ afterStale, afterLive: view(), a: a.value, b: b.value });
    """)
    stale = data["afterStale"]
    assert data["a"] is False, "the replaced review resolves as declined, never approved"
    assert stale["open"] is True
    assert stale["error"] == "", "a superseded failure painted its error into the newer review"
    assert stale["retry"] == 0, "a superseded failure offered Retry on the newer review"
    assert stale["acceptDisabled"] == [True, True]
    assert "llama-b" in stale["body"] and "acme/model-a" not in stale["body"]
    live = data["afterLive"]
    assert live["acceptDisabled"] == [False, False]
    assert "llama-b" in live["body"] and "acme/model-a" not in live["body"]
    assert data["b"] == "pending", "nothing approved or closed the newer review"


def test_a_replaced_reviews_late_success_never_repaints_enables_or_approves_the_newer_review() -> None:
    data = _drive("""
    const a = track(window.VoolPriceGate.review(A));
    await tick();
    const b = track(window.VoolPriceGate.review(B));
    await tick();
    release('openrouter', true, 'acme/model-a');   // A's read SUCCEEDS after B replaced it
    await tick(10);
    const afterStale = view();
    release('groq', true, 'llama-b');
    await tick(10);
    const beforeClick = b.value;
    actions()[1].__click();                         // the operator accepts B for this chat
    await tick();
    out({ afterStale, beforeClick, a: a.value, b: b.value });
    """)
    stale = data["afterStale"]
    assert stale["error"] == "" and stale["retry"] == 0
    assert stale["acceptDisabled"] == [True, True], "a superseded reply enabled the newer review's buttons"
    assert "acme/model-a" not in stale["body"], "a superseded reply repainted the newer review"
    assert data["beforeClick"] == "pending"
    assert data["a"] is False
    assert data["b"] == "conversation", "only the operator's own click on B resolves B"


def test_a_replaced_reviews_late_acceptances_read_never_paints_into_the_newer_review() -> None:
    """The second async read: the saved-acceptance read. Held for A until B has replaced A, it
    then fails; B must show nothing of it. A's catalog read lands first, so only the acceptances
    read stands between A and B's dialog."""
    data = _drive("""
    releaseAcceptances(false);   // the gate's own boot read fails, so each review reads again
    await tick();
    const a = track(window.VoolPriceGate.review(A));
    await tick();
    release('openrouter', true, 'acme/model-a');   // A's catalog read lands; its acceptances read is held
    await tick();
    const b = track(window.VoolPriceGate.review(B));
    await tick();
    const queued = heldAcceptances.length;
    releaseAcceptances(false);   // A's acceptances read fails AFTER B replaced A
    await tick(10);
    const afterStale = view();
    releaseAcceptances(true);    // B's own reads
    release('groq', true, 'llama-b');
    await tick(10);
    out({ afterStale, afterLive: view(), a: a.value, b: b.value, queued });
    """, prelude="globalThis.__holdAcceptances = true;\n")
    assert data["queued"] == 2, "both reviews must have their own acceptances read in flight"
    stale = data["afterStale"]
    assert data["a"] is False
    assert stale["open"] is True
    assert stale["error"] == "", "a superseded acceptances failure painted into the newer review"
    assert stale["retry"] == 0
    assert stale["acceptDisabled"] == [True, True]
    assert "acme/model-a" not in stale["body"]
    live = data["afterLive"]
    assert live["acceptDisabled"] == [False, False] and live["error"] == ""
    assert "llama-b" in live["body"]
    assert data["b"] == "pending"


def test_an_escaped_review_is_as_superseded_as_a_replaced_one() -> None:
    data = _drive("""
    const a = track(window.VoolPriceGate.review(A));
    await tick();
    actions().find((x) => x.className.includes('vg-decline')).__click();   // no review is open now
    await tick();
    release('openrouter', false);
    await tick(10);
    const failed = view();
    out({ failed, a: a.value });
    """)
    assert data["a"] is False
    assert data["failed"]["open"] is False, "a late reply reopened a review the operator declined"
    assert data["failed"]["error"] == ""
    assert data["failed"]["retry"] == 0


def test_a_live_failure_keeps_the_review_open_with_retry_and_nothing_approved() -> None:
    data = _drive("""
    const a = track(window.VoolPriceGate.review(A));
    await tick();
    release('openrouter', false);
    await tick(10);
    const failed = view();
    actions().find((x) => x.textContent === 'Retry price check').__click();
    await tick();
    release('openrouter', true, 'acme/model-a');
    await tick(10);
    out({ failed, retried: view(), a: a.value });
    """)
    failed = data["failed"]
    assert failed["open"] is True
    assert failed["error"] == "Could not read price information. Retry or cancel."
    assert failed["retry"] == 1
    assert failed["acceptDisabled"] == [True, True], "a failed price read must not be approvable"
    retried = data["retried"]
    assert retried["error"] == "" and retried["retry"] == 0
    assert retried["acceptDisabled"] == [False, False]
    assert data["a"] == "pending", "a failure never approves or closes the review"


def test_every_async_hop_rechecks_that_its_review_is_still_open() -> None:
    # Source pin beside the drives above: both reads' callbacks guard before any UI effect, and
    # the guard never special-cases null (an escaped review is superseded too).
    assert _GATE_JS.count("if (activeResolve !== resolve) return") >= 2
    assert "activeResolve !== null && activeResolve !== resolve" not in _GATE_JS


def test_the_chat_page_carries_no_price_review_action_or_json_in_an_attribute() -> None:
    """The wedge the browser drive found: the Review price limits button packed its target as
    JSON inside a quoted attribute; the panel's re-serialization unescaped it, the attribute
    truncated at the first inner quote, JSON.parse threw, and the click silently did nothing.
    That button belonged to the UsePod lane and left with it; nothing may bring the JSON-in-an-
    attribute shape back."""
    from core.vool_chat_page import render_vool_chat_html

    html = render_vool_chat_html()
    assert "data-vool-price-review" not in html
    assert "JSON.parse(button.getAttribute(" not in html
