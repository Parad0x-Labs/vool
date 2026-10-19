"""An allowed approval resumes the turn that ASKED for it, and its grant rides on nothing else.

Reported on the desktop chat: the operator presses Allow on a permission request and, while that
answer is still on the wire (or while a message of theirs waits in the chat's queue), sends another
message. The resume then re-sent the NEWEST user message instead of the one whose turn asked, so the
approved action never ran; and a queued, unrelated message went out carrying the first action's
approval token.

These drive the real page under node (the whole inline script, booted against the shared DOM
harness) and replace only the network: `/api/chat` streams are held open and fed by hand, and the
`/api/mode` approval answer and the queue claim are released exactly when each scenario needs them.
Every assertion is on the request bodies the server would actually have received.
"""

from __future__ import annotations

import json

from tests.test_multichat_navigation_unlock import _drive

A = "openclaw:" + "e" * 20
ASK = "create a folder named vool_test on my desktop"
OTHER = "what is the capital of France?"
LATER = "and of Spain?"
APPROVAL = {
    "approval_id": "appr-1", "intent": "machine.ensure_directory", "action": "Create directory ~/Desktop/vool_test",
    "affected_resources": ["~/Desktop/vool_test"], "expected_side_effects": "one new empty directory",
    "reversible": True, "scope_options": ["once"],
}

# Gates the two answers whose timing the bug depends on, on top of the shared network double.
GATES = """
const __gate = { resolve: null, claim: null };
let __queued = [];
const __netFetch = globalThis.fetch;
globalThis.fetch = async (url, opts) => {
  const u = String(url), o = opts || {};
  const body = o.body ? JSON.parse(o.body) : {};
  if (u === '/api/mode' && body.op === 'resolve_approval') {
    if (__gate.resolve) await __gate.resolve.promise;
    return { ok: true, status: 200, json: async () => ({ ok: true, approval: { scope: body.scope || 'once' } }) };
  }
  if (u === '/api/chat/queue' && body.op === 'enqueue') {
    const item = { queue_item_id: 'q' + (__queued.length + 1), status: 'pending', session_id: body.session_id, payload: { text: body.text } };
    __queued.push(item);
    return { ok: true, status: 200, json: async () => ({ ok: true, item }) };
  }
  if (u === '/api/chat/queue' && body.op === 'claim') {
    if (__gate.claim) await __gate.claim.promise;
    const item = __queued.shift() || null;
    return { ok: true, status: 200, json: async () => ({ ok: true, item }) };
  }
  return __netFetch(url, opts);
};
function hold() { let release; const promise = new Promise((r) => { release = r; }); return { promise, release }; }
function lastUser(post) { const users = post.messages.filter((m) => m.role === 'user'); return users.length ? users[users.length - 1].content : null; }
function postView(p) { return { turn_id: p.turn_id, approval_token: p.approval_token, last_user: lastUser(p),
                                 users: p.messages.filter((m) => m.role === 'user').map((m) => m.content) }; }
"""


def _ask_first(close: bool) -> str:
    """Turn 1 in chat A asks for approval of one exact action; optionally its stream closes."""
    return GATES + f"""
setDisplayedChat('{A}');
runTurn({json.dumps(ASK)}, null, {{ chatId: '{A}' }});
await tick();
const askingTurn = chatState('{A}').run.turnId;
__streams['{A}'].push(ev({{ type: 'permission.required', summary: 'machine.ensure_directory needs your approval',
  approval: Object.assign({json.dumps(APPROVAL)}, {{ task_id: askingTurn }}) }}));
__streams['{A}'].push(chunk('`machine.ensure_directory` needs your approval before it runs.'));
{"__streams['" + A + "'].close();" if close else ""}
await tick(8);
"""


def test_a_message_sent_while_the_allow_is_in_flight_does_not_take_the_resume() -> None:
    data = _drive(_ask_first(close=True) + f"""
__gate.resolve = hold();
document.getElementById('permOnce').__click();          // Allow, answer still on the wire
await tick();
inputEl.value = {json.dumps(OTHER)};
send();                                                  // chat is idle: runs at once
await tick(6);
__gate.resolve.release();                                // the Allow answer lands mid-turn
await tick(6);
__streams['{A}'].push(chunk('Paris.'));
__streams['{A}'].close();
await tick(12);
const afterResume = {{ posts: __posts.map(postView), history: snapshot('{A}').history,
                       token: approvalTokenFor('{A}') }};
__streams['{A}'].push(chunk('Created ~/Desktop/vool_test.'));
__streams['{A}'].close();
await tick(12);
out({{ askingTurn, afterResume, posts: __posts.length, tokenAfter: approvalTokenFor('{A}'),
       history: snapshot('{A}').history }});
""")
    posts = data["afterResume"]["posts"]
    assert len(posts) == 3, posts
    sent_other, resumed = posts[1], posts[2]
    assert sent_other["last_user"] == OTHER and sent_other["approval_token"] == ""
    # The resume is the ASKING turn: its message, its turn id, its grant -- and nothing said after it.
    assert resumed["last_user"] == ASK, f"the resume re-sent the wrong message: {resumed}"
    assert resumed["turn_id"] == data["askingTurn"], resumed
    assert resumed["approval_token"] == "appr-1", resumed
    assert OTHER not in resumed["users"], resumed
    # The other message's answer is part of the transcript; resuming must not delete it.
    assert ["assistant", "Paris."] in data["afterResume"]["history"], data["afterResume"]["history"]
    # Held while the resumed turn runs, released once it ended without asking again.
    assert data["afterResume"]["token"] == "appr-1"
    assert data["tokenAfter"] == "" and data["posts"] == 3
    assert data["history"][-1] == ["assistant", "Created ~/Desktop/vool_test."]


def test_a_queued_message_claimed_before_the_resume_never_carries_the_grant() -> None:
    data = _drive(_ask_first(close=False) + f"""
inputEl.value = {json.dumps(OTHER)};
await send();                                            // turn 1 still streaming: queued
__gate.claim = hold();
__streams['{A}'].close();                                // turn 1 ends; the pump starts its claim
await tick(8);
document.getElementById('permOnce').__click();           // Allow while the claim is on the wire
await tick(8);
__gate.claim.release();                                  // the queued message is claimed first
await tick(8);
const queuedPost = postView(__posts[__posts.length - 1]);
__streams['{A}'].push(chunk('Paris.'));
__streams['{A}'].close();
await tick(12);
out({{ askingTurn, queuedPost, posts: __posts.map(postView) }});
""")
    posts = data["posts"]
    assert len(posts) == 3, posts
    queued = data["queuedPost"]
    assert queued["last_user"] == OTHER
    assert queued["approval_token"] == "", f"an unrelated queued turn carried the grant: {queued}"
    assert queued["turn_id"] != data["askingTurn"]
    resumed = posts[2]
    assert resumed["last_user"] == ASK, f"the resume re-sent the wrong message: {resumed}"
    assert resumed["turn_id"] == data["askingTurn"] and resumed["approval_token"] == "appr-1", resumed
    assert OTHER not in resumed["users"], resumed


def test_a_refused_resume_does_not_leave_its_grant_for_the_next_message() -> None:
    """The resumed turn is refused by the server (HTTP 409); the next new message is not it."""
    data = _drive(_ask_first(close=True) + f"""
const __priorFetch = globalThis.fetch;
let refuseNext = true;
globalThis.fetch = async (url, opts) => {{
  if (String(url) === '/api/chat' && refuseNext) {{
    refuseNext = false;
    const body = JSON.parse(opts.body);
    __posts.push({{ session_id: body.session_id, turn_id: body.turn_id, messages: body.messages,
                    approval_token: body.approval_token }});
    return {{ ok: false, status: 409, clone() {{ return this; }},
              json: async () => ({{ error: 'session_archived', detail: 'This chat is archived.' }}) }};
  }}
  return __priorFetch(url, opts);
}};
document.getElementById('permOnce').__click();
await tick(12);
inputEl.value = {json.dumps(LATER)};
send();
await tick(8);
out({{ askingTurn, posts: __posts.map(postView) }});
""")
    posts = data["posts"]
    assert len(posts) == 3, posts
    assert posts[1]["turn_id"] == data["askingTurn"] and posts[1]["approval_token"] == "appr-1"
    later = posts[2]
    assert later["last_user"] == LATER
    assert later["approval_token"] == "", f"a new message carried a grant for another turn: {later}"
