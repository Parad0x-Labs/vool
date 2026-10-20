"""A queued message claimed by a page that is gone must not read "Running" for ever.

Measured on a served page: three quick sends, then a reload while the first ran. The page that
was unloading claimed message 2 (pending -> in_flight) and died; after the reload the strip said
"Running: second ..." with no control, nothing ever sent it, and message 3 sat behind it unsent.

Every claim now carries the claiming page's lease, and a page keeps a heartbeat for its lease.
These tests drive the real `renderQueue()` under node: a claim whose lease has no live page is
shown as interrupted with Send again / Remove; a claim by this page, by a page with a fresh
heartbeat, or with no lease at all (an older page; unknown) keeps the old "Running" reading.
"""

from __future__ import annotations

import json

from tests.chat_page_js_harness import DOM, run_node, script


def _item(qid: str, text: str, status: str = "pending", lease: str = "") -> dict:
    return {"status": status, "queue_item_id": qid, "lease_owner": lease, "session_id": "", "payload": {"text": text}}


def _run(items_js: str, extra_js: str = "", setup_js: str = "") -> dict:
    program = (
        DOM
        + script()
        + """
const __calls = [];
const __origFetch = globalThis.fetch;
globalThis.fetch = async (url, opts) => {
  __calls.push({ url: String(url), body: (opts && opts.body) ? JSON.parse(opts.body) : null });
  return __origFetch(url, opts);
};
function __snap() {
  return queueEl.children.map(function (c) {
    return { cls: c.className, text: c.textContent,
             buttons: c.children.filter(function (cc) { return cc.tagName === 'BUTTON' || /qchip-(x|act)/.test(cc.className || ''); })
                                .map(function (cc) { return cc.textContent; }) };
  });
}
_st(async function () {
"""
        + setup_js
        + "  renderQueue("
        + items_js
        + ");\n"
        + extra_js
        + """
  await new Promise(function (r) { _st(r, 20); });
  out({ snap: __snap(), calls: __calls });
  process.exit(0);
}, 60);
"""
    )
    return run_node(program)


def test_a_claim_from_a_page_that_is_gone_is_shown_as_interrupted_with_controls() -> None:
    items = json.dumps([_item("q1", "second: what about cats", "in_flight", "tab-gone"), _item("q2", "third: and dogs")])
    data = _run(items)
    assert data["errors"] == []
    first, second = data["snap"][0], data["snap"][1]
    assert first["text"].startswith("Interrupted, not sent: second: what about cats")
    assert "Running" not in first["text"]
    assert "Send again" in first["buttons"] and "×" in first["buttons"]
    assert "stale" in first["cls"] and "running" not in first["cls"]
    assert second["text"].startswith("Queued 1: third: and dogs")


def test_removing_an_interrupted_claim_closes_it_as_cancelled() -> None:
    items = json.dumps([_item("q1", "second", "in_flight", "tab-gone")])
    click = (
        "  queueEl.children[0].children.find(function (cc) { return cc.className === 'qchip-x'; }).__click();\n"
    )
    data = _run(items, click)
    ops = [c["body"] for c in data["calls"] if c["url"] == "/api/chat/queue" and c["body"]]
    assert len(ops) == 1
    assert {"op": "complete", "queue_item_id": "q1", "status": "cancelled"}.items() <= ops[0].items()


def test_a_claim_from_a_page_with_a_live_heartbeat_still_reads_running() -> None:
    items = json.dumps([_item("q1", "in progress", "in_flight", "tab-other")])
    setup = "  localStorage.setItem('vool.queue.lease.tab-other', String(Date.now()));\n"
    data = _run(items, setup_js=setup)
    assert data["snap"][0]["text"].startswith("Running: in progress")
    assert data["snap"][0]["buttons"] == []


def test_this_pages_own_claim_and_a_claim_without_a_lease_read_running() -> None:
    mine = "[{status: 'in_flight', queue_item_id: 'q1', lease_owner: PAGE_LEASE, session_id: '', payload: {text: 'mine'}}]"
    data = _run(mine)
    assert data["snap"][0]["text"].startswith("Running: mine")
    legacy = _run(json.dumps([_item("q1", "older page", "in_flight", "")]))
    assert legacy["snap"][0]["text"].startswith("Running: older page")


def test_every_claim_carries_this_pages_lease() -> None:
    assert "queueOp('claim', chatId, { lease_owner: PAGE_LEASE })" in script()
