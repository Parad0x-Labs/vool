"""A forget reaches the request as VOOL re-spaced it, and the page's old messages never write it back.

Real app, served build driven through the chat page (2026-11-12, branch of the v0.8 line at 247124ed):
"Remember that my boat is called Sea​Lark", then "Forget SeaLark" answered "Forget applied", and the
runtime database still held the name in two ways.

* The run history keeps the request as the input normalizer rewrote it. The normalizer rejoins a message
  with English spacing, so every punctuation mark or invisible character stands apart: the checkpoint's
  ``request_text`` and the "Received request: ..." event held "Sea ​ Lark", which the forget matcher did
  not read as "SeaLark". The same rewrite turns "pa$$word" into "pa $ $ word" and "R&D" into "R & D".
* The chat page sends every earlier message back with each turn. The next turn ("What do you remember?")
  wrote them, forgotten code included, into its checkpoint's source context and loop state; finalizing
  that checkpoint was then refused for carrying the erased answer, so it stayed "running" with the words.

Law:

* the forget matcher also compares both sides with the normalizer's re-spacing taken back
  (``core.memory.carriers._respaced_needle_found``), tried last, so it only adds matches and moving a space
  never joins two words across a visible mark;
* the chat door drops each re-sent message that carries a word this chat forgot, as the prompt already
  drops it (``core.web.api.runtime.history_without_forgotten_words``); the current message stays, and an
  unreadable forget list keeps only the current message.

All sentences, names and codes are invented for this contract.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any
from urllib.request import Request, urlopen

import pytest

from core.context_retrieval import text_carries_revoked_token
from core.input_normalizer import normalize_user_text
from core.memory.carriers import carries, forget_needle_in
from tests.test_forget_scrubs_run_history import (  # isolated_pages is a fixture
    _db_carriers,
    _remember,
    _session_words,
    _turn_history,
    command,
    isolated_pages,
)
from tests.test_memory_forget_served_p0 import _reply_text, served  # served is a fixture
from tests.test_question_date_time_leg_20261002 import _hash_backend  # fixture

pytestmark = pytest.mark.usefixtures("_hash_backend")

# --- the matcher reads the re-spaced request ------------------------------------------------------------

#: (as typed, as the person types the forget)
RESPACED = [
    ("Sea​Lark", "SeaLark"),        # zero-width space, forgotten as it looks
    ("Sea​Lark", "Sea​Lark"),  # ... and as it was pasted
    ("Sea⁠Lark", "SeaLark"),        # word joiner
    ("Sea﻿Lark", "SeaLark"),        # zero-width no-break space
    ("Harg­reave", "Hargreave"),    # soft hyphen
    ("pa$$word", "pa$$word"),
    ("R&D", "R&D"),
    ("#4410", "#4410"),
    ("J.R.R.", "J.R.R."),
    ("pass:word", "pass:word"),
    ("C++", "C++"),
    ("Sea·Lark", "Sea·Lark"),  # middle dot
    ("Sea|Lark", "Sea|Lark"),
]
FRAMES = ["Remember that my boat is called {}", "my secret: {}.", "im keeping {} safe"]


@pytest.mark.parametrize("frame", FRAMES)
@pytest.mark.parametrize("typed, token", RESPACED)
def test_a_forget_finds_the_word_in_the_request_as_the_normalizer_rewrote_it(typed, token, frame):
    request = frame.format(typed)
    rewritten = normalize_user_text(request).normalized_text
    # The rewrite really parts the word: it is not in the stored request as typed.
    assert typed not in rewritten, rewritten
    assert forget_needle_in(request, token), request
    assert forget_needle_in(rewritten, token), rewritten
    assert carries(rewritten, token), rewritten
    assert text_carries_revoked_token(rewritten, (token,)), rewritten


@pytest.mark.parametrize("frame", FRAMES)
def test_a_handle_is_kept_as_typed_and_the_forget_finds_it(frame):
    """Since the X merge (21856060) the normalizer keeps a handle whole, like an email address: "@sealark" is
    no longer re-spaced, so it left the list above; the forget still has to find it in the stored request."""
    request = frame.format("@sealark")
    rewritten = normalize_user_text(request).normalized_text
    assert "@sealark" in rewritten, rewritten
    assert forget_needle_in(rewritten, "@sealark"), rewritten
    assert carries(rewritten, "@sealark"), rewritten
    assert text_carries_revoked_token(rewritten, ("@sealark",)), rewritten


@pytest.mark.parametrize("hay, token", [
    # a visible mark between two words is a character, not spacing: no join across it
    ("The sea, larks and gulls.", "SeaLark"),
    ("Sea · Lark", "SeaLark"),
    ("Sea | Lark", "SeaLark"),
    ("alpha — beta", "alpha beta"),
    # the number law holds in the re-spaced text: "#12" is not in "#123", "3.1" is not in "3.12"
    ("room # 123", "#12"),
    ("version 3 . 12", "3.1"),
    ("pin : 44105", "pin:4410"),
    # a different word stays a different word
    ("pa $ $ wort", "pa$$word"),
    ("R & B", "R&D"),
])
def test_taking_the_spacing_back_never_joins_words_across_a_visible_mark(hay, token):
    assert not forget_needle_in(hay, token), (hay, token)
    assert not carries(hay, token), (hay, token)


@pytest.mark.parametrize("hay, token", [
    ("The locksmith is Hargreave.", "Hargreave"),
    ("thermal-pool and more", "thermal pool"),
    ("The gate code is 12.", "12"),
    ("pages 10–12", "12"),
    ("Sea  Lark", "Sea Lark"),
])
def test_what_matched_before_still_matches(hay, token):
    assert forget_needle_in(hay, token), (hay, token)


# --- the run history: the re-spaced request answers to the forget ---------------------------------------

def test_forget_erases_the_rewritten_request_from_the_run_history(isolated_pages):
    chat = "respaced-run-history"
    typed = "Remember that my boat is called Sea​Lark"
    rewritten = normalize_user_text(typed).normalized_text
    assert "Sea ​ Lark" in rewritten, rewritten
    _remember(isolated_pages, chat, "my boat is called Sea​Lark")
    _turn_history(chat, rewritten, "Locked in.")
    _turn_history(chat, "Remember that my harbor lantern is painted signal green.", "Locked in.")
    assert "lark" in _session_words(chat).casefold()

    handled, reply = command(isolated_pages, chat, "Forget SeaLark")

    assert handled and reply.startswith("Forget applied"), reply
    words = _session_words(chat)
    assert "lark" not in words.casefold(), words
    assert "Remember that my harbor lantern is painted signal green." in words


@pytest.mark.parametrize("typed, token", [("pa$$word", "pa$$word"), ("R&D", "R&D")])
def test_forget_erases_a_rewritten_punctuated_word_from_the_run_history(isolated_pages, typed, token):
    chat = "respaced-run-history-" + token.strip("$&").lower()
    _remember(isolated_pages, chat, f"my vault phrase is {typed}")
    _turn_history(chat, normalize_user_text(f"Remember that my vault phrase is {typed} ok").normalized_text,
                  "Locked in.")

    handled, reply = command(isolated_pages, chat, f"Forget {token}")

    assert handled and reply.startswith("Forget applied"), reply
    words = _session_words(chat)
    assert "vault phrase" not in words, words


# --- the chat door: the page's old messages never carry forgotten words into a turn -------------------

def _page(code: str) -> list[dict[str, str]]:
    """The chat page's messages after a remember, a list, a forget and a new question."""
    return [
        {"role": "user", "content": f"Remember that my storage locker code is {code}"},
        {"role": "assistant", "content": "Locked in. I’ll remember that."},
        {"role": "user", "content": "Remember that my harbor lantern is painted signal green."},
        {"role": "assistant", "content": "Locked in. I’ll remember that."},
        {"role": "user", "content": "What do you remember?"},
        {"role": "assistant", "content": f"What I remember:\n- my storage locker code is {code}\n"
                                         "- my harbor lantern is painted signal green"},
        {"role": "user", "content": "Forget KESTREL-4410"},
        {"role": "assistant", "content": "Forget applied. Removed 1 memory entry."},
        {"role": "user", "content": "What do you remember?"},
    ]


def test_the_door_drops_each_re_sent_message_that_carries_a_forgotten_word(isolated_pages):
    from core.web.api.runtime import history_without_forgotten_words

    chat = "page-history-forget"
    _remember(isolated_pages, chat, "my storage locker code is KESTREL‐4410")
    _remember(isolated_pages, chat, "my harbor lantern is painted signal green.")
    handled, reply = command(isolated_pages, chat, "Forget KESTREL-4410")
    assert handled and reply.startswith("Forget applied"), reply
    page = _page("KESTREL‐4410")

    kept = history_without_forgotten_words(page, chat_id=chat, current_user_text="What do you remember?")

    assert "kestrel" not in json.dumps(kept).casefold(), kept
    assert kept == [page[1], page[2], page[3], page[4], page[7], page[8]], kept
    # The current message is the person's new words: it stays even when it names the forgotten code.
    asked = [*page[:-1], {"role": "user", "content": "Is KESTREL-4410 still my code?"}]
    kept = history_without_forgotten_words(asked, chat_id=chat, current_user_text="Is KESTREL-4410 still my code?")
    assert kept[-1] == asked[-1], kept
    assert "kestrel" not in json.dumps(kept[:-1]).casefold(), kept


def test_a_chat_that_forgot_nothing_keeps_its_page_history(isolated_pages):
    from core.web.api.runtime import history_without_forgotten_words

    chat = "page-history-nothing-forgotten"
    _remember(isolated_pages, chat, "my storage locker code is KESTREL‐4410")
    page = _page("KESTREL‐4410")

    assert history_without_forgotten_words(page, chat_id=chat, current_user_text="What do you remember?") == page


def test_an_unreadable_forget_list_keeps_only_the_current_message(isolated_pages, monkeypatch):
    import core.context_retrieval as retrieval
    from core.web.api.runtime import history_without_forgotten_words

    def unreadable(_chat_id: str) -> tuple[str, ...]:
        raise RuntimeError("forget ledger unreadable")

    monkeypatch.setattr(retrieval, "revoked_tokens_for_chat", unreadable)
    page = _page("KESTREL‐4410")

    kept = history_without_forgotten_words(page, chat_id="page-history-unreadable",
                                           current_user_text="What do you remember?")

    assert kept == [page[-1]], kept


def _page_turn(daemon: Any, session: str, page: list[dict[str, str]]) -> str:
    """One turn as the chat page sends it: every message on screen, the new one last."""
    payload = {"messages": page, "stream": False, "session_id": session}
    request = Request(f"{daemon.base_url}/api/chat", data=json.dumps(payload).encode("utf-8"),
                      headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(request, timeout=180) as response:
        return _reply_text(json.loads(response.read().decode("utf-8"))).strip()


def test_served_turn_after_a_forget_writes_no_page_copy_of_the_words_back(served):
    """The real daemon and the real /api/chat door, sent what the chat page sends: the turn after the
    forget leaves no byte of the forgotten code in the runtime database, and its checkpoint finishes."""
    daemon, home = served["daemon"], served["home"]
    session = "page-history-served"
    page: list[dict[str, str]] = []

    def say(text: str) -> str:
        page.append({"role": "user", "content": text})
        reply = _page_turn(daemon, session, page)
        page.append({"role": "assistant", "content": reply})
        return reply

    assert "Locked in" in say("Remember that my storage locker code is KESTREL-4410")
    assert "Locked in" in say("Remember that my harbor lantern is painted signal green.")
    assert "KESTREL" in say("What do you remember?")
    assert say("Forget KESTREL-4410").startswith("Forget applied")
    db_path = home / "data" / "vool_web0_v2.db"
    assert _db_carriers(db_path, "kestrel") == []

    listed = say("What do you remember?")

    assert "KESTREL" not in listed and "signal green" in listed, listed
    assert _db_carriers(db_path, "kestrel") == []
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        statuses = [row[0] for row in conn.execute("SELECT status FROM runtime_checkpoints")]
    finally:
        conn.close()
    assert statuses and "running" not in statuses, statuses
    # The words the person did not forget are still recorded.
    assert "runtime_checkpoints.source_context_json" in _db_carriers(db_path, "signal green")
