"""DISPATCHER phase 2: a chat keeps running while you work in another one.

Phase 1 built the per-chat ownership model behind four `if (busy) return` navigation guards and
proved it by slicing the state module out of the page. Phase 2 removes those guards, so the thing
being tested is no longer a seam in isolation -- it is the whole page: `openSession` really runs,
`renderChat` really rebuilds a chat's transcript, `attachRunDom` really re-hangs an in-flight run's
card, and a real `runTurn` is really mid-stream while all of that happens.

So these tests boot the entire inline script under node against an executable DOM, and replace only
the network: `/api/chat` returns a stream the harness holds open and feeds by hand. That is the one
substitution -- everything above it (the reader loop, event application, completion, history
append, composer state, navigation) is the page's own code doing its own work.

Why that matters here specifically: the failure mode this phase can introduce is not a wrong value,
it is a wrong LIFECYCLE -- a turn silently re-POSTed on re-open, a second run created for the same
question, a stream aborted because the user looked away, an answer painted into the chat that
happened to be on screen. None of those show up in a unit slice, and all of them show up here as a
counted request, a counted run, or an answer in the wrong transcript.

Every scenario asserts the negative as hard as the positive: `postCount` (no re-POST), `runsSeen`
(no duplicate run), `aborted` (no stream torn down by navigation), and the untouched chat's DOM.
"""

from __future__ import annotations

import re

from tests.chat_page_js_harness import DOM, run_node, script

A = "openclaw:" + "a" * 20
B = "openclaw:" + "b" * 20
C = "openclaw:" + "c" * 20

# Replaces ONLY the network. /api/chat hands back a stream this harness owns, so a turn can be held
# mid-flight across a real navigation and fed afterwards. Every POST is counted, so "did anything
# re-send this turn?" is a number rather than an inference.
NET = """
globalThis.__posts = [];
globalThis.__streams = {};      // chatId -> the open stream for that chat's live turn
globalThis.__aborted = [];      // chatIds whose fetch was aborted, in order
globalThis.__cancelPosts = [];  // every /api/chat/cancel body, in order

function makeStream(chatId) {
  const queue = [];
  let pending = null, closed = false, aborted = false;
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
    // A real aborted fetch REJECTS the pending read with AbortError. Closing cleanly instead would
    // let a cancelled turn arrive at runTurn's normal end and report itself completed.
    abort() {
      aborted = true;
      if (pending) { const p = pending; pending = null; const e = new Error('aborted'); e.name = 'AbortError'; p.reject(e); }
    },
    reader: {
      read() {
        if (queue.length) return Promise.resolve(queue.shift());
        if (aborted) { const e = new Error('aborted'); e.name = 'AbortError'; return Promise.reject(e); }
        if (closed) return Promise.resolve({ done: true, value: undefined });
        return new Promise((resolve, reject) => { pending = { resolve, reject }; });
      },
    },
  };
  __streams[chatId] = ctl;
  return ctl;
}

const API_RESP = {   // distinct from the DOM stub's own RESP
  ok: true, sessions: [], messages: [], queue: [], pins: [], projects: [], models: [], events: [],
  receipts: [], counts: {}, connections: [], data: [], next_after: 0,
  grant: { token: 't', expires_at: 0 }, state: { mode: 'manual' }, approval: { scope: 'once' },
  release_version: '0.0.0', build_id: 'b', commit: 'c',
};
globalThis.fetch = async (url, opts) => {
  const u = String(url), o = opts || {};
  if (u === '/api/chat' && String(o.method).toUpperCase() === 'POST') {
    const body = JSON.parse(o.body);
    __posts.push({ session_id: body.session_id, turn_id: body.turn_id, messages: body.messages,
                   approval_token: body.approval_token, bypass_token: body.bypass_token, mode: body.mode });
    const ctl = makeStream(body.session_id);
    if (o.signal) o.signal.__onabort = () => { __aborted.push(body.session_id); ctl.abort(); };
    return { ok: true, status: 200, body: { getReader: () => ctl.reader } };
  }
  if (u === '/api/chat/cancel' && String(o.method).toUpperCase() === 'POST') __cancelPosts.push(JSON.parse(o.body));
  return { ok: true, status: 200, headers: { get: () => null },
           json: async () => ({ ...API_RESP }), text: async () => '{}', blob: async () => ({}) };
};
globalThis.AbortController = class {
  constructor() { const self = this; this.signal = { aborted: false, __onabort: null, addEventListener() {}, removeEventListener() {} }; }
  abort() { this.signal.aborted = true; if (this.signal.__onabort) this.signal.__onabort(); }
};

// Let the page's promises run. The reader loop is async, so every push needs a turn of the loop
// before its effect is observable.
const _rawTimeout = globalThis.__rawSetTimeout;
function tick(n) {
  let p = Promise.resolve();
  for (let i = 0; i < (n || 4); i++) p = p.then(() => new Promise((r) => _rawTimeout(r, 0)));
  return p;
}
function chunk(text) { return { message: { content: text } }; }
function ev(e) { return { vool_event: e }; }
function logText() { return logEl.children.map((c) => c.textContent).join('|'); }
function snapshot(id) {
  const st = chatState(id);
  return {
    history: st.history.map((m) => [m.role, m.content]),
    run: st.run ? { turnId: st.run.turnId, status: st.run.status, ended: st.run.ended,
                    text: st.run.text, steps: st.run.steps.length, chatId: st.run.chatId,
                    hasDom: !!st.run.refs, resultParts: st.run.resultParts || null } : null,
    busy: isChatBusy(id),
    approvalToken: st.approvalToken,
    bypassToken: (st.bypassGrant && st.bypassGrant.token) || '',
    resumeApproved: !!st.resumeApprovedTurn,
  };
}
"""

# __rawSetTimeout must survive the DOM stub's unref-wrapping, or tick() would never fire.
RAW_TIMER = "globalThis.__rawSetTimeout = globalThis.setTimeout;\n"


def test_background_reminder_appears_once_without_replacing_cached_or_running_chat():
    data = _drive(f"""
    await tick();
    setDisplayedChat('{A}');
    chatState('{A}').history.push({{role:'user',content:'my existing question'}},
      {{role:'assistant',content:'my existing answer'}});
    renderChat('{A}');
    const originalFetch = fetch;
    let resolveHistory;
    globalThis.fetch = async (url, opts) => String(url).startsWith('/api/chat/history?session=')
      ? {{ok:true,json:async()=>({{messages:[{{role:'assistant',content:'⏰ Reminder: saffron inventory',
          ts:'2026-09-12T12:00:00Z',artifact:{{kind:'reminder_delivery',reminder_id:'rem-A',status:'delivered'}}}}]}})}}
      : originalFetch(url,opts);
    await refreshSidebarActivity();
    await refreshSidebarActivity();
    const first = snapshot('{A}');
    const firstLog = logText();
    globalThis.fetch = async (url,opts) => String(url).startsWith('/api/chat/history?session=')
      ? {{ok:true,json:()=>new Promise(r=>{{resolveHistory=r;}})}} : originalFetch(url,opts);
    const pending = refreshSidebarActivity();
    await tick();
    setDisplayedChat('{B}'); renderChat('{B}');
    resolveHistory({{messages:[{{role:'assistant',content:'⏰ Reminder: delayed A',
       ts:'2026-09-12T12:01:00Z',artifact:{{kind:'reminder_delivery',reminder_id:'rem-A2',status:'delivered'}}}}]}});
    await pending;
    out({{first,firstLog,a:snapshot('{A}'),b:snapshot('{B}'),bLog:logText(),posts:__posts.length}});
    """)
    assert len(data["first"]["history"]) == 3
    assert data["firstLog"].count("⏰ Reminder: saffron inventory") == 1
    assert data["a"]["history"][:2] == [["user", "my existing question"], ["assistant", "my existing answer"]]
    assert "delayed A" not in data["bLog"] and data["b"]["history"] == []
    assert data["posts"] == 0


def test_late_reminder_refresh_cannot_rewrite_a_turn_started_during_fetch():
    data = _drive(f"""
    await tick();
    setDisplayedChat('{A}');
    chatState('{A}').history.push({{role:'assistant',content:'existing answer'}});
    const originalFetch = fetch;
    let resolveHistory;
    globalThis.fetch = async (url,opts) => String(url).startsWith('/api/chat/history?session=')
      ? {{ok:true,json:()=>new Promise(r=>{{resolveHistory=r;}})}} : originalFetch(url,opts);
    const pending = refreshSidebarActivity();
    await tick();
    runTurn('keep this live question', null, {{chatId:'{A}'}});
    await tick();
    __streams['{A}'].push(chunk('live response'));
    await tick();
    resolveHistory({{messages:[{{role:'assistant',content:'late reminder',
      artifact:{{kind:'reminder_delivery',reminder_id:'late',status:'delivered'}}}}]}});
    await pending;
    await refreshSidebarActivity();
    out({{a:snapshot('{A}'),posts:__posts.length,aborted:__aborted}});
    """)
    assert data["a"]["history"] == [["assistant", "existing answer"], ["user", "keep this live question"]]
    assert data["a"]["run"]["text"] == "live response" and not data["a"]["run"]["ended"]
    assert data["posts"] == 1 and data["aborted"] == []


def _drive(body: str, *, mutate: str = "") -> dict:
    source = script()
    if mutate:
        source = MUTATIONS[mutate](source)
    program = (
        RAW_TIMER
        + DOM
        + NET
        + source
        + "\n;(async () => {\n"
        + body
        + "\n})().then(() => { __report(); process.exit(0); })"
        + ".catch((e) => { errors.push('harness: ' + (e && e.stack || e)); __report(); process.exit(0); });\n"
    )
    data = run_node(program)
    assert data.get("errors") == [], "the page threw while being driven:\n" + "\n".join(data["errors"])
    return data


# Start a turn in chat A, get it genuinely mid-stream, then hand control back.
START_A = f"""
setDisplayedChat('{A}');
runTurn('long task A', null, {{ chatId: '{A}' }});
await tick();
__streams['{A}'].push(ev({{ type: 'tool.started', tool: 'read_file', summary: 'Reading', stage: 'Reading' }}));
__streams['{A}'].push(chunk('partial A'));
await tick();
"""


# --------------------------------------------------------------------------------------------- #
# A -- navigating away from a running chat
# --------------------------------------------------------------------------------------------- #
def test_a_running_chat_survives_navigating_away_from_it() -> None:
    data = _drive(START_A + f"""
    const beforeSwitch = snapshot('{A}');
    await openSession('{B}');
    await tick();
    // A keeps streaming with nobody watching.
    __streams['{A}'].push(chunk(' more A'));
    await tick();
    out({{
      beforeSwitch, a: snapshot('{A}'), b: snapshot('{B}'),
      displayed: displayedChat, posts: __posts.length, aborted: __aborted,
      bLog: logText(), sendLabel: sendEl.textContent,
    }});
    """)
    assert data["beforeSwitch"]["run"]["ended"] is False
    # The navigation itself happened -- asserted first, so restoring a `busy` guard fails HERE and
    # names the defect, rather than surfacing later as a confusing DOM-ownership mismatch.
    assert data["displayed"] == B, "navigation must not be refused because another chat is running"
    # The turn is still running, still owned by A, and still accumulating -- with A off screen.
    assert data["a"]["run"]["ended"] is False
    assert data["a"]["run"]["chatId"] == A
    assert data["a"]["run"]["text"] == "partial A more A", "a background turn must keep accruing"
    assert data["a"]["busy"] is True
    assert data["a"]["run"]["hasDom"] is False, "a background run must hold no DOM"
    # Navigation did not tear the stream down, and did not re-send anything.
    assert data["aborted"] == [], "navigating away must not abort the running turn's stream"
    assert data["posts"] == 1, "navigation must not re-POST the turn"
    # B is untouched and usable.
    assert data["b"] == {"history": [], "run": None, "busy": False, "approvalToken": "",
                         "bypassToken": "", "resumeApproved": False}
    assert "partial A" not in data["bLog"], "A's answer must not appear in B's log"
    assert data["sendLabel"] == "Send", "B's composer must be free while A runs"


# --------------------------------------------------------------------------------------------- #
# B -- two chats running at once
# --------------------------------------------------------------------------------------------- #
def test_two_chats_run_at_the_same_time_without_sharing_anything() -> None:
    data = _drive(START_A + f"""
    await openSession('{B}');
    await tick();
    runTurn('task B', null, {{ chatId: '{B}' }});
    await tick();
    __streams['{B}'].push(chunk('partial B'));
    await tick();
    const a = chatState('{A}').run, b = chatState('{B}').run;
    out({{
      a: snapshot('{A}'), b: snapshot('{B}'),
      busyIds: busyChatIds().sort(),
      shared: {{
        run: a === b, steps: a.steps === b.steps, events: a.events === b.events,
        files: a.files === b.files, tests: a.tests === b.tests,
        history: chatState('{A}').history === chatState('{B}').history,
        aborter: a.aborter === b.aborter, turnId: a.turnId === b.turnId,
      }},
      posts: __posts.map((p) => p.session_id), aborted: __aborted,
    }});
    """)
    assert data["a"]["run"]["ended"] is False and data["b"]["run"]["ended"] is False
    assert data["a"]["run"]["text"] == "partial A" and data["b"]["run"]["text"] == "partial B"
    assert sorted(data["busyIds"]) == sorted([A, B]), "both chats must be busy at once"
    assert not any(data["shared"].values()), f"runs share state: {[k for k, v in data['shared'].items() if v]}"
    assert data["posts"] == [A, B], "exactly one POST per chat"
    assert data["aborted"] == []
    # Only the displayed chat holds DOM.
    assert data["b"]["run"]["hasDom"] is True and data["a"]["run"]["hasDom"] is False


# --------------------------------------------------------------------------------------------- #
# C -- A completes while B is displayed
# --------------------------------------------------------------------------------------------- #
def test_a_completing_in_the_background_never_reaches_the_displayed_chat() -> None:
    data = _drive(START_A + f"""
    await openSession('{B}');
    runTurn('task B', null, {{ chatId: '{B}' }});
    await tick();
    __streams['{B}'].push(chunk('partial B'));
    await tick();
    const bLogBefore = logText();
    const bRunBefore = chatState('{B}').run;
    // A finishes, entirely off screen.
    __streams['{A}'].push(chunk(' done A'));
    __streams['{A}'].push(ev({{ type: 'task.completed', summary: 'Complete', ts: '2026-08-07T10:00:00Z' }}));
    __streams['{A}'].close();
    await tick(8);
    out({{
      a: snapshot('{A}'), b: snapshot('{B}'),
      bLogBefore, bLogAfter: logText(),
      bRunUnchanged: chatState('{B}').run === bRunBefore,
      displayed: displayedChat, sendLabel: sendEl.textContent,
      posts: __posts.length,
    }});
    """)
    assert data["a"]["run"]["status"] == "completed" and data["a"]["run"]["ended"] is True
    assert data["a"]["history"] == [["user", "long task A"], ["assistant", "partial A done A"]]
    # B: transcript, run and DOM all untouched.
    assert data["b"]["history"] == [["user", "task B"]]
    assert data["b"]["run"]["ended"] is False and data["b"]["run"]["text"] == "partial B"
    assert data["bRunUnchanged"] is True
    assert data["bLogAfter"] == data["bLogBefore"], "A's completion must not repaint B's log"
    assert "done A" not in data["bLogAfter"]
    assert data["sendLabel"] == "Queue", "B is still running, so B's composer still queues"
    assert data["posts"] == 2


# --------------------------------------------------------------------------------------------- #
# D -- out-of-order completion: B first, then A
# --------------------------------------------------------------------------------------------- #
def test_each_answer_lands_exactly_once_when_b_finishes_before_a() -> None:
    data = _drive(START_A + f"""
    await openSession('{B}');
    runTurn('task B', null, {{ chatId: '{B}' }});
    await tick();
    // Same answer text on purpose: attribution cannot be passing by content.
    __streams['{B}'].push(chunk('the answer'));
    __streams['{B}'].push(ev({{ type: 'task.completed', summary: 'Complete' }}));
    __streams['{B}'].close();
    await tick(8);
    const afterB = {{ a: snapshot('{A}'), b: snapshot('{B}'), sendLabel: sendEl.textContent }};
    __streams['{A}'].push(chunk('the answer'));
    __streams['{A}'].push(ev({{ type: 'task.completed', summary: 'Complete' }}));
    __streams['{A}'].close();
    await tick(8);
    out({{ afterB, a: snapshot('{A}'), b: snapshot('{B}'), posts: __posts.length, aborted: __aborted }});
    """)
    # B finished first; A was still running and untouched.
    assert data["afterB"]["b"]["run"]["status"] == "completed"
    assert data["afterB"]["a"]["run"]["ended"] is False
    assert data["afterB"]["sendLabel"] == "Send", "B's composer frees when B finishes"
    # Each answer landed exactly once, in its own chat.
    assert data["a"]["history"] == [["user", "long task A"], ["assistant", "partial Athe answer"]]
    assert data["b"]["history"] == [["user", "task B"], ["assistant", "the answer"]]
    assert [m for m in data["a"]["history"] if m[0] == "assistant"] == [["assistant", "partial Athe answer"]]
    assert [m for m in data["b"]["history"] if m[0] == "assistant"] == [["assistant", "the answer"]]
    assert data["posts"] == 2 and data["aborted"] == []


# --------------------------------------------------------------------------------------------- #
# E -- failure isolation
# --------------------------------------------------------------------------------------------- #
def test_a_failing_in_the_background_leaves_a_running_b_untouched() -> None:
    data = _drive(START_A + f"""
    await openSession('{B}');
    runTurn('task B', null, {{ chatId: '{B}' }});
    await tick();
    __streams['{B}'].push(chunk('partial B'));
    await tick();
    __streams['{A}'].push(ev({{ type: 'task.failed', summary: 'Model lane failed' }}));
    __streams['{A}'].close();
    await tick(8);
    out({{ a: snapshot('{A}'), b: snapshot('{B}'), bLog: logText(), sendLabel: sendEl.textContent }});
    """)
    assert data["a"]["run"]["status"] == "failed" and data["a"]["run"]["ended"] is True
    assert data["a"]["busy"] is False
    assert data["b"]["run"]["ended"] is False and data["b"]["run"]["status"] == "running"
    assert data["b"]["run"]["text"] == "partial B"
    assert data["b"]["busy"] is True
    assert "Model lane failed" not in data["bLog"]
    assert data["sendLabel"] == "Queue"


# --------------------------------------------------------------------------------------------- #
# F -- approval isolation
# --------------------------------------------------------------------------------------------- #
def test_an_approval_raised_in_the_background_does_not_reach_the_displayed_chat() -> None:
    data = _drive(f"""
    setDisplayedChat('{A}');
    chatState('{A}').bypassGrant = {{ token: 'bypass-A' }};
    runTurn('needs approval', null, {{ chatId: '{A}' }});
    await tick();
    await openSession('{B}');
    await tick();
    // A hits a permission gate with B on screen.
    __streams['{A}'].push(ev({{ type: 'permission.required', summary: 'Approve one write',
      approval: {{ approval_id: 'approval-A', scope_options: ['once'], action: 'Write a file' }} }}));
    __streams['{A}'].close();
    await tick(8);
    setApprovalToken('{A}', 'approval-A');
    chatState('{A}').resumeApprovedTurn = true;
    const whileAway = {{
      a: snapshot('{A}'), b: snapshot('{B}'),
      permBarHidden: document.getElementById('permBar').hidden,
      permMsg: document.getElementById('permMsg').textContent,
    }};
    // Anything B sends must carry none of A's grants.
    runTurn('plain B', null, {{ chatId: '{B}' }});
    await tick();
    const bPost = __posts[__posts.length - 1];
    // Coming back to A shows A's approval state again.
    await openSession('{A}');
    await tick();
    out({{ whileAway, bPost,
      backOnA: {{ permBarHidden: document.getElementById('permBar').hidden,
                 permMsg: document.getElementById('permMsg').textContent,
                 status: chatState('{A}').run.status, approvalToken: approvalTokenFor('{A}') }} }});
    """)
    assert data["whileAway"]["a"]["run"]["status"] == "awaiting_approval"
    # B inherits nothing: no token, no bypass, no resume flag, no permission UI.
    assert data["whileAway"]["b"]["approvalToken"] == ""
    assert data["whileAway"]["b"]["bypassToken"] == ""
    assert data["whileAway"]["b"]["resumeApproved"] is False
    assert data["whileAway"]["permBarHidden"] is True, "a background approval must not show over B"
    assert data["bPost"]["session_id"] == B
    assert data["bPost"]["approval_token"] == "" and data["bPost"]["bypass_token"] == ""
    # Returning to A restores A's own approval state and its grant is intact.
    assert data["backOnA"]["status"] == "awaiting_approval"
    assert data["backOnA"]["permBarHidden"] is False, "returning to A must show A's approval again"
    assert data["backOnA"]["permMsg"] == "Write a file"
    assert data["backOnA"]["approvalToken"] == "approval-A"


# --------------------------------------------------------------------------------------------- #
# G -- returning to a chat whose turn is still in flight
# --------------------------------------------------------------------------------------------- #
def test_returning_to_a_running_chat_redraws_it_without_restarting_it() -> None:
    data = _drive(START_A + f"""
    const runBefore = chatState('{A}').run;
    await openSession('{B}');
    await tick();
    // A accumulates while off screen.
    __streams['{A}'].push(ev({{ type: 'tool.completed', tool: 'read_file', summary: 'Read', stage: 'Reading', path: '/x' }}));
    __streams['{A}'].push(ev({{ type: 'tool.started', tool: 'run_tests', summary: 'Testing', stage: 'Testing' }}));
    __streams['{A}'].push(chunk(' offscreen'));
    await tick();
    const postsBeforeReturn = __posts.length;
    await openSession('{A}');
    await tick();
    const runAfter = chatState('{A}').run;
    const painted = {{
      log: logText(), hasDom: !!runAfter.refs, hasCard: !!runAfter.card,
      textEl: runAfter.textEl ? runAfter.textEl.textContent : null,
      stepsRendered: runAfter.refs ? runAfter.refs.recent.innerHTML : '',
    }};
    // ...and it is STILL live: a later chunk paints straight into the reattached node.
    __streams['{A}'].push(chunk(' after return'));
    await tick();
    out({{
      sameRun: runAfter === runBefore, painted,
      textAfterReturn: runAfter.textEl.textContent,
      state: snapshot('{A}'),
      postsBeforeReturn, posts: __posts.length, aborted: __aborted,
    }});
    """)
    # The SAME run object, redrawn -- not a new one, and nothing re-sent.
    assert data["sameRun"] is True, "returning must reattach the existing run, not create another"
    assert data["postsBeforeReturn"] == 1 and data["posts"] == 1, "returning must not re-POST the turn"
    assert data["aborted"] == []
    # Everything accumulated off screen is on screen now.
    assert data["painted"]["hasDom"] is True and data["painted"]["hasCard"] is True
    assert data["painted"]["textEl"] == "partial A offscreen"
    assert "Read" in data["painted"]["stepsRendered"], "the Activity accumulated off screen must paint"
    assert data["state"]["run"]["steps"] == 2
    # And the stream is still feeding the reattached DOM.
    assert data["textAfterReturn"] == "partial A offscreen after return"
    assert data["state"]["run"]["ended"] is False


def test_returning_to_a_chat_that_finished_while_away_shows_its_answer_and_card() -> None:
    data = _drive(START_A + f"""
    await openSession('{B}');
    __streams['{A}'].push(chunk(' finished'));
    __streams['{A}'].push(ev({{ type: 'tool.completed', tool: 'read_file', summary: 'Read', stage: 'Reading' }}));
    __streams['{A}'].push(ev({{ type: 'task.completed', summary: 'Complete', ts: '2026-08-07T12:00:00Z' }}));
    __streams['{A}'].close();
    await tick(8);
    await openSession('{A}');
    await tick(4);
    const run = chatState('{A}').run;
    out({{
      log: logText(), state: snapshot('{A}'),
      cardClasses: run.card ? [...run.card.classList._s].sort() : null,
      title: run.refs ? run.refs.title.textContent : null,
      summary: run.refs ? run.refs.summary.textContent : null,
      stopHidden: run.refs ? run.refs.stop.style.display : null,
      // The real setMsgTime writes an HH:mm .msg-time span -- assert the rendered value, and the
      // expected one derived by the page's own formatter, so this cannot drift on a timezone.
      bubbleTime: run.assistantMsgEl ? run.assistantMsgEl.querySelector('.msg-time').textContent : null,
      expectedTime: formatMsgTime('2026-08-07T12:00:00Z'),
      posts: __posts.length,
    }});
    """)
    assert data["posts"] == 1, "a chat that finished while away must not be re-run on return"
    assert "partial A finished" in data["log"], "the completed answer must be on screen"
    assert data["state"]["run"]["status"] == "completed"
    # The terminal card is rebuilt, not lost.
    assert data["cardClasses"] is not None and "done" in data["cardClasses"]
    assert data["title"] == "Completed"
    assert data["summary"] == "Tool action completed · 1 action"
    assert data["stopHidden"] == "none"
    assert data["expectedTime"], "the page's own formatter produced nothing for the server ts"
    assert data["bubbleTime"] == data["expectedTime"], (
        "the real completion time must survive the re-render, not be re-guessed as 'now'"
    )


# --------------------------------------------------------------------------------------------- #
# H / I -- starting a new chat, and a new project chat, while something runs
# --------------------------------------------------------------------------------------------- #
def test_a_new_chat_opens_and_is_usable_while_another_chat_runs() -> None:
    data = _drive(START_A + f"""
    newChat();
    await tick();
    const fresh = displayedChat;
    runTurn('task in the new chat', null, {{ chatId: fresh }});
    await tick();
    __streams[fresh].push(chunk('fresh answer'));
    await tick();
    out({{
      fresh, isNew: fresh !== '{A}', a: snapshot('{A}'), n: snapshot(fresh),
      busyIds: busyChatIds().length, aborted: __aborted, posts: __posts.length,
    }});
    """)
    assert data["isNew"] is True
    assert data["a"]["run"]["ended"] is False, "the previous chat keeps running"
    assert data["n"]["run"]["text"] == "fresh answer"
    assert data["n"]["history"] == [["user", "task in the new chat"]]
    assert data["busyIds"] == 2
    assert data["aborted"] == [] and data["posts"] == 2


def test_a_new_project_chat_opens_and_is_usable_while_another_chat_runs() -> None:
    data = _drive(START_A + f"""
    await newChatInProject('proj-1');
    await tick();
    const fresh = displayedChat;
    runTurn('task in the project chat', null, {{ chatId: fresh }});
    await tick();
    __streams[fresh].push(chunk('project answer'));
    await tick();
    out({{ fresh, projectId: chatState(fresh).projectId, a: snapshot('{A}'), n: snapshot(fresh),
          busyIds: busyChatIds().length, aborted: __aborted, posts: __posts.length }});
    """)
    assert data["projectId"] == "proj-1"
    assert data["a"]["run"]["ended"] is False
    assert data["n"]["run"]["text"] == "project answer"
    assert data["busyIds"] == 2
    assert data["aborted"] == [] and data["posts"] == 2


# --------------------------------------------------------------------------------------------- #
# Cancel isolation
# --------------------------------------------------------------------------------------------- #
def test_cancelling_the_returned_to_chat_leaves_the_other_running() -> None:
    data = _drive(START_A + f"""
    await openSession('{B}');
    runTurn('task B', null, {{ chatId: '{B}' }});
    await tick();
    __streams['{B}'].push(chunk('partial B'));
    await tick();
    await openSession('{A}');
    await tick();
    // Press Stop on A's rebuilt card -- the same button a user would click.
    chatState('{A}').run.refs.stop.__click();
    await tick(8);
    out({{
      a: snapshot('{A}'), b: snapshot('{B}'), aborted: __aborted,
      cancelPosts: __cancelPosts,
    }});
    """)
    assert data["a"]["run"]["status"] == "cancelled" and data["a"]["run"]["ended"] is True
    # Cancel was addressed to A's chat and turn, and only A's stream was aborted.
    assert data["cancelPosts"] == [{"session_id": A, "turn_id": data["a"]["run"]["turnId"]}]
    assert data["aborted"] == [A]
    # B is entirely unaffected.
    assert data["b"]["run"]["ended"] is False and data["b"]["run"]["text"] == "partial B"
    assert data["b"]["busy"] is True


# --------------------------------------------------------------------------------------------- #
# Sabotage -- each mutation must fail its NAMED test
# --------------------------------------------------------------------------------------------- #
def _mutate_restore_open_session_guard(source: str) -> str:
    """Put phase 1's navigation lock back on openSession."""
    old = "async function openSession(id) {"
    assert old in source
    return source.replace(old, old + "\n  if (isChatBusy(displayedChat)) return;", 1)


def _mutate_global_history(source: str) -> str:
    """One transcript for the whole app again: append to whatever chat is displayed."""
    old = "function recordAssistantMessage(run, text, metadata) {\n  const owner = ownerOf(run);"
    assert old in source, "recordAssistantMessage no longer has the shape this mutation targets"
    return source.replace(old, "function recordAssistantMessage(run, text, metadata) {\n  const owner = view;", 1)


def _mutate_completion_uses_display(source: str) -> str:
    """Resolve completion ownership from the screen instead of the run."""
    old = "function ownerOf(run) { return chatState(run && run.chatId); }"
    assert old in source
    return source.replace(old, "function ownerOf(run) { return view; }", 1)


def _mutate_abort_on_losing_display(source: str) -> str:
    """Tear the stream down when a chat leaves the screen -- the 'the stream belongs to the page'
    mistake this phase exists to avoid."""
    old = "  const leaving = view.run;\n  if (leaving && !leaving.ended) detachRunDom(leaving);"
    assert old in source, "openSession no longer has the shape this mutation targets"
    return source.replace(
        old,
        "  const leaving = view.run;\n  if (leaving && !leaving.ended) { detachRunDom(leaving); if (leaving.aborter) leaving.aborter.abort(); }",
        1,
    )


def _mutate_composer_gated_globally(source: str) -> str:
    """Make the Send button reflect ANY running chat rather than the displayed one."""
    old = "  if (sendEl) sendEl.textContent = isChatBusy(displayedChat) ? 'Queue' : 'Send';"
    assert old in source
    return source.replace(old, "  if (sendEl) sendEl.textContent = busyChatIds().length ? 'Queue' : 'Send';", 1)


def _mutate_clear_run_on_open(source: str) -> str:
    """Clear the target chat's run while opening it -- phase 1's old switch behaviour."""
    old = "  renderChat(id);"
    assert old in source
    return source.replace(old, "  view.run = null;\n  renderChat(id);", 1)


MUTATIONS = {
    "open_session_guard": _mutate_restore_open_session_guard,
    "global_history": _mutate_global_history,
    "completion_uses_display": _mutate_completion_uses_display,
    "abort_on_losing_display": _mutate_abort_on_losing_display,
    "composer_gated_globally": _mutate_composer_gated_globally,
    "clear_run_on_open": _mutate_clear_run_on_open,
}

# Each mutation, and the test whose property it removes. The anti-vacuity gate below re-runs every
# named guard against its mutation and reports any that stays green.
MUTATION_GUARDS: dict[str, tuple[str, ...]] = {
    "open_session_guard": ("test_a_running_chat_survives_navigating_away_from_it",),
    "global_history": ("test_a_completing_in_the_background_never_reaches_the_displayed_chat",
                       "test_each_answer_lands_exactly_once_when_b_finishes_before_a"),
    "completion_uses_display": ("test_a_completing_in_the_background_never_reaches_the_displayed_chat",),
    "abort_on_losing_display": ("test_returning_to_a_running_chat_redraws_it_without_restarting_it",
                                "test_a_running_chat_survives_navigating_away_from_it"),
    "composer_gated_globally": ("test_a_running_chat_survives_navigating_away_from_it",),
    "clear_run_on_open": ("test_returning_to_a_running_chat_redraws_it_without_restarting_it",),
}


def test_every_mutation_turns_its_named_guard_red() -> None:
    """The anti-vacuity gate: a mutation that changes no test result proves nothing.

    Each mutation is applied and every guard named for it is re-run against the mutated page. A
    guard that stays green did not reach the property it claims to protect, and is reported by name
    rather than left looking like coverage.
    """
    import sys

    module = sys.modules[__name__]
    original = module._drive
    survived: list[str] = []
    try:
        for mutation, guards in MUTATION_GUARDS.items():
            module._drive = (lambda m: (lambda body, mutate="": original(body, mutate=m)))(mutation)
            for guard in guards:
                try:
                    getattr(module, guard)()
                except AssertionError:
                    continue
                survived.append(f"{mutation} -> {guard}")
    finally:
        module._drive = original
    assert survived == [], (
        "these guards stayed GREEN under a mutation that removes the property they claim to "
        f"protect, so they never reached it: {survived}"
    )


# --------------------------------------------------------------------------------------------- #
# The guards really are gone
# --------------------------------------------------------------------------------------------- #
def test_no_navigation_path_consults_a_running_turn() -> None:
    source = script()
    for fn in ("async function openSession(id) {", "function newChat() {", "async function newChatInProject(pid) {"):
        body = source[source.index(fn) + len(fn):]
        head = body[: body.index("\n}\n")] if "\n}\n" in body else body[:3000]
        assert "if (busy) return;" not in head, f"{fn} still refuses to navigate while something runs"
        assert "isChatBusy" not in head.split("\n")[0], f"{fn} gates navigation on a run"
    assert "if (sid && sid !== displayedChat) openSession(sid);" in source
    # And no process-global busy flag survives anywhere. `aria-busy` is excluded: it is the
    # a11y attribute the model picker sets on ONE button while that row's switch is in flight
    # -- per-element UI state that gates nothing, not a busy flag the page consults.
    offenders = [
        line.strip() for line in source.splitlines()
        if re.search(r"(?<![.\w])busy(?![\w])", line)
        and not line.strip().startswith("//")
        and "isChatBusy" not in line and "busyChatIds" not in line and "ledgerBusy" not in line
        and "aria-busy" not in line
    ]
    assert offenders == [], f"a global busy flag is still in play: {offenders}"
