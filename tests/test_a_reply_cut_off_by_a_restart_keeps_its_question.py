"""INTERRUPT-01 (0.7.8 sweep block 3): VOOL stopped mid-reply and the chat came back empty.

The exchange is written to the transcript only when its reply finalizes, so a restart mid-reply
left nothing: GET /api/chat/history returned no rows and the person's question was gone. The
startup sweep does record the cut-off turn (an `interrupted` runtime session with its question),
so the history now returns that question, marked `interrupted`, and the page shows it with an
"Ask again" note. The marked row is never sent to a model as context.

The served test kills a real daemon mid-reply (its own recorded process) and starts a new one on
the same home, the way a quit or crash and a relaunch happen.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from urllib.request import Request, urlopen

import pytest

import tests._reader_served_rig as rig
from tests.test_multichat_navigation_unlock import A, _drive

HELD = "what were the three biggest causes of the 1929 stock market crash"
LATER = "whats the capital of mongolia"


class _HoldOne:
    def __init__(self) -> None:
        self.release = threading.Event()

    def __call__(self, body: dict) -> str | None:
        messages = [m for m in (body.get("messages") or []) if isinstance(m, dict)]
        last_user = next((str(m.get("content") or "") for m in reversed(messages) if m.get("role") == "user"), "")
        if HELD in last_user:
            self.release.wait(120)
            return "too late"
        if "mongolia" in last_user:
            return "Ulaanbaatar is the capital of Mongolia."
        return None


def _history(daemon, session: str) -> list[dict]:
    with urlopen(f"{daemon.base_url}/api/chat/history?session={session}", timeout=60) as response:
        return json.loads(response.read().decode()).get("messages", [])


def _ask(daemon, session: str, text: str) -> None:
    body = {"messages": [{"role": "user", "content": text}], "stream": True, "session_id": session,
            "turn_id": uuid.uuid4().hex, "model": daemon.model, "mode": "ask"}
    request = Request(f"{daemon.base_url}/api/chat", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(request, timeout=300) as response:
        for _ in response:
            pass


def test_a_restart_mid_reply_keeps_the_question_until_a_later_turn_finishes(tmp_path) -> None:
    held = _HoldOne()
    with rig.CapturingProvider(default="Hello there.", reply_fn=held) as provider:
        home = tmp_path / "home"
        daemon = rig.ServedDaemon(home, provider=provider)
        daemon.start()
        session = rig.canonical_session("cutoff-" + uuid.uuid4().hex[:8])
        try:
            assert daemon.certify(timeout=180).get("state") == "verified"
            reader = threading.Thread(target=lambda: _swallow(_ask, daemon, session, HELD), daemon=True)
            reader.start()
            deadline = time.time() + 120
            while time.time() < deadline and not any(HELD in c["text"] for c in provider.calls):
                time.sleep(0.1)
            assert any(HELD in c["text"] for c in provider.calls), "the turn never reached the model"
            assert _history(daemon, session) == []      # nothing is written before the reply finalizes
            # The crash: this test's own daemon process, killed without a chance to finish.
            daemon.process.kill()
            daemon.process.wait(timeout=30)
        finally:
            held.release.set()
        held.release.clear()

        again = rig.ServedDaemon(home, provider=provider)
        again.start()
        try:
            rows = _history(again, session)
            assert rows == [{"role": "user", "content": HELD, "interrupted": True}], rows

            # The person moves on in the same chat: the cut-off question is not repeated after it.
            _ask(again, session, LATER)
            after = _history(again, session)
            assert [(m["role"], m["content"]) for m in after][:1] == [("user", LATER)], after
            assert not any(m.get("interrupted") for m in after), after
            assert HELD not in json.dumps(after)
        finally:
            again.stop()


def _swallow(fn, *args) -> None:
    try:
        fn(*args)
    except Exception:
        pass


# The page: the interrupted row shows with Ask again, is never model input, and Ask again sends it
# once as a fresh turn.
_PAGE = f"""
await tick();
setDisplayedChat('{A}');
const realQ = logEl.querySelector.bind(logEl);
logEl.querySelector = (s) => {{
  if (s !== '.recovery-note') return realQ(s);
  const stack = logEl.children.slice();
  while (stack.length) {{ const n = stack.shift(); if (String(n.className || '').split(' ').includes('recovery-note')) return n; (n.children || []).forEach((k) => stack.push(k)); }}
  return null;
}};
const originalFetch = fetch;
globalThis.fetch = async (url, opts) => String(url).startsWith('/api/chat/history?session=')
  ? {{ok:true,status:200,json:async()=>({{messages:[
      {{role:'user',content:'whos the mayor of riga'}},
      {{role:'assistant',content:'I could not check that just now.',ts:'2026-11-12T09:00:00Z'}},
      {{role:'user',content:'{HELD}',interrupted:true}}]}})}}
  : originalFetch(url, opts);
await restoreCurrent();
await tick();
const note = logEl.children.find((c) => String(c.className || '').includes('recovery-note'));
const buttons = note ? note.children.filter((c) => c.tagName === 'BUTTON').map((c) => c.textContent) : [];
const modelInput = canonicalMessagesForModel(chatState('{A}').history);
const shownBefore = logEl.children.map((c) => c.textContent).join('|');
const again = note && note.children.find((c) => c.tagName === 'BUTTON' && c.textContent === 'Ask again');
if (again) again.__click();
await tick(8);
out({{noteText: note ? note.textContent : '', buttons, modelInput, shownBefore,
      posts: __posts.map((p) => p.messages),
      history: chatState('{A}').history.map((m) => [m.role, m.content, !!m.interrupted])}});
"""


def test_the_page_shows_the_cut_off_question_with_ask_again_and_never_feeds_it_to_a_model() -> None:
    data = _drive(_PAGE)
    assert "Reply interrupted" in data["noteText"], data
    assert data["buttons"] == ["Ask again"], data          # nothing to resume: no Resume/cancel button
    assert HELD in data["shownBefore"]
    assert data["modelInput"] == [
        {"role": "user", "content": "whos the mayor of riga"},
        {"role": "assistant", "content": "I could not check that just now."},
    ]
    assert len(data["posts"]) == 1, data["posts"]
    sent = data["posts"][0]
    assert [m["content"] for m in sent if m["role"] == "user"] == ["whos the mayor of riga", HELD]
    assert [row for row in data["history"] if row[1] == HELD] == [["user", HELD, False]]
