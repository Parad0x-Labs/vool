"""The forget list keeps keyed fingerprints of the forgotten words, never the words.

Measured on the Intel smoke of the v0.8 line (2026-11-24): after "Remember that my sister Ilona's favourite tree is
the silver birch." and "Forget what I told you about Ilona's favourite tree.", the check "forgotten 'silver birch' is
gone from every file under HOME" failed: the forget list itself (``.forget-authority/ledger.json``, the
``memory_revocations`` and ``memory_erasure_operations`` tables of ``vool_memory.db``) held the forgotten fact's words
in plain text, as every forget did (measured at function level on 3727b4e7: the ledger and the database held
"ilona's favourite tree"). The owner's law: forget covers every store, and NO RECORD = REFUSE after a forget.

Each entry is now a forget mark (``core.memory.forget_marks``): what each reader of the list compares, as keyed
fingerprints. The served checks below say the facts in new words (not the smoke's), forget them by describing them,
scan every file of the profile for the words as the smoke does, and ask about them again in other words, in the
forgetting chat and in another one. The function checks hold the fingerprints to the plain law on many texts: the
same matches, recall subjects, withdrawals and judged-recall words as the words themselves gave.

All names and values here are invented for this contract.
"""
from __future__ import annotations

import json
import random
import re
import sqlite3
from pathlib import Path

import pytest

FACT = "my uncle Tadas's favourite boat is the Blue Heron"
WORDS = ("Blue Heron", "blue heron", "Tadas", "favourite boat")


def _holding(root: Path, needle: str) -> list[str]:
    """`ops/macos_bundle_smoke.files_holding`: every file under ``root`` whose bytes hold ``needle``. The needle
    must stand on its own, so a short number is not found inside a longer one (a process id or a clock reading in
    the server's own preflight record)."""
    raw = re.compile(rb"(?<![0-9A-Za-z])" + re.escape(needle.encode()) + rb"(?![0-9A-Za-z])")
    return [str(p.relative_to(root)) for p in root.rglob("*")
            if p.is_file() and not p.is_symlink() and raw.search(p.read_bytes())]


def _judge(body: dict) -> str | None:
    if "well-known referents" in json.dumps(body.get("messages") or []):
        return '{"ambiguous": false, "referents": [], "clarification": ""}'
    return None


def _reply(payload: dict) -> str:
    message = payload.get("message") if isinstance(payload.get("message"), dict) else {}
    return str(message.get("content") or payload.get("response") or "")


@pytest.fixture()
def served(tmp_path):
    from tests._reader_served_rig import CapturingProvider, ServedDaemon, canonical_session

    with CapturingProvider(default="A kettle hums on the stove.", reply_fn=_judge) as provider, \
            ServedDaemon(tmp_path / "home", provider=provider) as daemon:
        assert daemon.certify().get("state") == "verified"

        def ask(text: str, chat: str) -> tuple[str, bool]:
            provider.reset()
            answer = _reply(daemon.chat(text, session_id=canonical_session(chat)))
            return answer, bool(provider.payloads())

        ask.home = tmp_path / "home"
        yield ask


def test_a_described_forget_leaves_no_file_holding_the_words_and_recall_still_refuses(served):
    assert "Locked in" in served(f"Remember that {FACT}.", "boat-chat")[0]
    assert any(_holding(served.home, word) for word in WORDS)
    forgot = served("Forget what I told you about Tadas's boat.", "boat-chat")[0]
    assert forgot.startswith("Forget applied"), forgot
    assert "fingerprint" in forgot and "forget list" in forgot, forgot

    held = {word: _holding(served.home, word) for word in WORDS}
    assert not any(held.values()), held
    ledger = (served.home / ".forget-authority" / "ledger.json").read_text()
    assert '"token": "fk1:' in ledger, ledger[:300]
    for chat in ("boat-chat", "another-chat"):
        answer, called = served("Do you remember the Blue Heron?", chat)
        assert answer.startswith(("I don't have that saved", "I have no record")), (chat, answer)
        assert "heron" not in answer.lower() and not called, (chat, answer)


def test_a_value_forgotten_in_one_chat_is_refused_in_another_without_a_model_call(served):
    from core.web.api.runtime import FORGOTTEN_IN_ANOTHER_CHAT_REPLY

    assert "Locked in" in served("Remember that the cellar lantern's serial is KX-5519", "lantern-chat")[0]
    assert served("Forget KX-5519", "lantern-chat")[0].startswith("Forget applied")
    assert not _holding(served.home, "KX-5519") and not _holding(served.home, "5519")
    answer, called = served("What do you remember about KX-5519?", "second-chat")
    assert answer == FORGOTTEN_IN_ANOTHER_CHAT_REPLY and not called, answer
    # A plain question is the other-chat search's: it finds nothing and says so, with no model call.
    answer, called = served("Do you know KX-5519?", "second-chat")
    assert answer.startswith("I don't have that saved") and "5519" not in answer and not called, answer
    answer, called = served("Do you recall KX-5519?", "lantern-chat")
    assert answer == "I have no record of that information in this chat; it was forgotten.", answer
    assert not called


def test_a_forget_by_value_leaves_no_file_holding_it_and_unrelated_memory_stays(served):
    assert "Locked in" in served("Remember that my allotment shed padlock is 6093.", "shed-chat")[0]
    assert "Locked in" in served("Remember that my allotment is plot 12 on Ferrier Lane.", "shed-chat")[0]
    assert served("Forget 6093.", "shed-chat")[0].startswith("Forget applied")
    assert not _holding(served.home, "padlock is 6093") and not _holding(served.home, "6093")
    assert _holding(served.home, "Ferrier Lane")  # the other fact is kept


# ── an older build's forget list ────────────────────────────────────────────


def test_words_an_older_build_kept_on_the_forget_list_become_fingerprints(tmp_path):
    from core.memory import revocation
    from core.memory.carriers import text_carries
    from core.runtime_paths import configure_runtime_home
    from core.vool_memory import VoolMemory

    home = tmp_path / "home"
    configure_runtime_home(home)
    try:
        VoolMemory(runtime_home=str(home)).close()
        authority = home / ".forget-authority"
        # What a build before the fingerprints wrote: the words in the ledger and in both tables.
        authority.joinpath("ledger.json").write_text(json.dumps({"version": 2, "operations": {
            "a" * 32: {"chat": "chat-one", "token": "the quince jam recipe", "status": "complete"}}}))
        db = home / "data" / "memory" / "vool_memory.db"
        with sqlite3.connect(db) as conn:
            conn.execute("INSERT INTO memory_revocations (token, chat_id, revoked_at) VALUES (?, ?, ?)",
                         ("the quince jam recipe", "chat-one", 1.0))
            conn.execute("INSERT INTO memory_erasure_operations (operation_id, chat_id, token, started_at, status) "
                         "VALUES (?, ?, ?, ?, 'complete')", ("b" * 32, "chat-one", "the quince jam recipe", 1.0))
        memory = VoolMemory(runtime_home=str(home))
        try:
            assert not _holding(home, "quince"), _holding(home, "quince")
            tokens = revocation.revoked_tokens("chat-one", str(home), owner_memory=memory)
            assert tokens and all(token.startswith("fk1:") for token in tokens)
            assert any(text_carries("Here is the Quince-Jam recipe you asked for.", token) for token in tokens)
            assert not any(text_carries("A quince tree grows by the gate.", token) for token in tokens)
            assert not memory.payload_is_current("The quince jam recipe needs lemon.", chat_id="chat-one")
        finally:
            memory.close()
    finally:
        configure_runtime_home(None)


def test_a_fingerprint_whose_key_is_gone_is_unreadable_never_empty(tmp_path, monkeypatch):
    from core.memory import forget_marks
    from core.memory.carriers import text_carries

    home = tmp_path / "home"
    mark = forget_marks.forget_mark("the quince jam recipe", home)
    assert (home / ".forget-authority" / "fingerprint.key").stat().st_mode & 0o077 == 0
    assert text_carries("the quince jam recipe", mark)
    (home / ".forget-authority" / "fingerprint.key").unlink()
    monkeypatch.setattr(forget_marks, "_KEYS", {})
    monkeypatch.setattr(forget_marks, "_HOME_KEYS", {})
    forget_marks._read_mark.cache_clear()
    try:
        with pytest.raises(forget_marks.ForgetMarkKeyError):
            text_carries("the quince jam recipe", mark)
    finally:
        forget_marks._read_mark.cache_clear()


# ── the fingerprints hold the plain law ─────────────────────────────────────

#: Words, numbers and spellings the forget law treats specially (word starts and endings, numbers as their own,
#: number joints, spelling folds, invisible characters, the input normalizer's re-spacing).
_PARTS = ["hut", "huts", "dog", "dogma", "pen", "happen", "copper", "kettle", "Marit's", "spare", "garage", "door",
          "37", "537", "4.37", "2,370", "37:15", "K37", "b37e", "37mm", "gate", "is", "the", "my", "cold-frame",
          "cold_frame", "ＱＲ－７７０２", "Brann​igan", "Brannigan", "ke$$el", "B&B", "Moss​Wren", "MossWren",
          "３７", "٣٧", "Marit’s", "ąžuolas", "—", "-", ",", ".", "(", ")", "37﹣02", "4​37", "y-ra", "y-rat",
          "def-ghi", "def-gh", "8826", "shed", "sheds", "Crème", "­", "ß", "SS"]
_JOINS = [" ", "  ", "-", "_", " - ", ", ", ". ", " ", "", "\t", " $ ", " & "]


def _phrase(rng: random.Random, n: int) -> str:
    out: list[str] = []
    for _ in range(n):
        out += [rng.choice(_PARTS), rng.choice(_JOINS)]
    return "".join(out[:-1])


def test_the_fingerprint_matcher_finds_exactly_what_the_words_find():
    from core.memory.carriers import forget_needle_in, normalized_forget_text
    from core.memory.forget_marks import forget_mark, mark_in_text, text_in_mark

    rng = random.Random(20261124)
    found = [0, 0]
    for _ in range(600):
        token = _phrase(rng, rng.randint(1, 3))
        needle = normalized_forget_text(token)
        if not needle:
            continue
        mark = forget_mark(token)
        for _ in range(3):
            hay = _phrase(rng, rng.randint(1, 6))
            if rng.random() < 0.4:
                hay = hay + rng.choice(_JOINS) + token + rng.choice(_JOINS) + _phrase(rng, rng.randint(0, 2))
            expected = forget_needle_in(hay, needle)
            assert mark_in_text(hay, mark) == expected, (token, hay)
            found[0] += expected
            part = normalized_forget_text(_phrase(rng, rng.randint(1, 2)) if rng.random() < 0.6
                                          else token[:rng.randint(1, len(token))])
            if part:
                expected = forget_needle_in(needle, part)
                assert text_in_mark(mark, part) == expected, (token, part)
                found[1] += expected
    assert min(found) > 100, found  # the draw holds matches both ways, not only misses


_FACTS = [
    "My spare garage door code is 8826.", "My garage door code is 4471.", "My sister Marit's garage code is 3390.",
    "The allotment shed key hangs by the cold frame.", "My kettle is copper and it whistles.",
    "Our back gate code is 1945.", "My work locker is number 37.", "The B&B in Crème Street costs 90 a night.",
    "Marit's dog is called Brannigan.", "I keep the spare key under the blue pot.",
]
_TARGETS = [
    "the spare garage door code", "my garage door code", "the garage code", "Marit's garage code",
    "the allotment shed key, it is wrong", "my kettle", "the back gate code I gave you", "my locker",
    "Marit's dog", "the spare key", "who Marit's dog is", "my sister Marit", "the B&B", "the gate",
]
_QUESTIONS = [
    "What is my garage door code?", "Where is the allotment shed key?", "What do you remember about Marit's dog?",
    "Do you remember my kettle?", "What's the spare key?", "Who is Marit's dog?", "Remind me of my locker",
    "What is the back gate code?", "Do you know the B&B?", "Can you recall my sister Marit?",
]


def test_the_fingerprint_readers_decide_exactly_as_the_words_decide():
    from core.chat_housekeeping import query_terms
    from core.fact_extractor import _folded_words, _same_word
    from core.memory.carriers import normalized_forget_text
    from core.memory.forget_marks import forget_mark, mark_words_named, terms_of_mark_in
    from core.memory.withdrawals import payload_is_withdrawn
    from core.memory_recall_intent import revoked_memory_recall_clauses

    hits = [0, 0, 0, 0]
    for target in _TARGETS + [fact.rstrip(".") for fact in _FACTS]:
        token = normalized_forget_text(target)
        mark = forget_mark(target)
        for fact in _FACTS:
            expected = payload_is_withdrawn(fact, (token,))
            assert payload_is_withdrawn(fact, (mark,)) == expected, (target, fact)
            hits[0] += expected
            stated = set(query_terms(fact))
            subject = {term for term in query_terms(token) if term not in {"the", "my", "a", "an"}}
            expected = bool(subject and subject <= stated)
            assert terms_of_mark_in(mark, stated) == expected, (target, fact)
            hits[1] += expected
            held = _folded_words(fact)
            for question in _QUESTIONS:
                asked = _folded_words(question)
                own = [w for w in _folded_words(token) if len(w) >= 4 and not any(_same_word(w, o) for o in held)]
                expected = any(_same_word(w, o) for w in own for o in asked)
                assert mark_words_named(mark, held, asked) == expected, (target, fact, question)
                hits[2] += expected
        for question in [*_QUESTIONS, f"What is {target}?"]:
            expected = bool(revoked_memory_recall_clauses(question, revoked_tokens=(token,)))
            assert bool(revoked_memory_recall_clauses(question, revoked_tokens=(mark,))) == expected, (target, question)
            hits[3] += expected
    assert all(hits), hits
