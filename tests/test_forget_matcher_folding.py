"""The shared forget matcher folds the look-alike spellings of a forgotten word.

A forgotten "Hargreave-Smith" was still served when the text spelled it in fullwidth ASCII
("Ｈａｒｇｒｅａｖｅ‑Ｓｍｉｔｈ") or with a non-breaking hyphen (U+2011) in place of "-", and a
forget given in either spelling missed the plain one. The fold lives in the one matcher
(`core.memory.carriers.forget_needle_in` / `_forget_needle_spans`), so memory recall, the forget
refusal and the document serve screen all gain it. Folded, on both sides:

* fullwidth ASCII U+FF01..U+FF5E as its ASCII character (digits were already folded);
* U+2010 hyphen, U+2011 non-breaking hyphen, U+2012 figure dash, U+2013 en dash and U+2212 minus
  sign as "-";
* U+00AD soft hyphen (invisible) dropped.

Controls: plain-ASCII matching is exactly as before, a text that carries no forgotten word is served
byte for byte, and withheld line windows still name the original lines.
"""
from __future__ import annotations

import itertools
import random
import string

import pytest

from core import chat_attachments as ca
from core.context_retrieval import text_carries_revoked_token
from core.memory.carriers import (
    _SPELLING_FOLD,
    _SPELLING_FOLD_CHARS,
    carries,
    forget_needle_in,
)
from tests.test_question_date_time_leg_20261002 import _profile

FULLWIDTH = "Ｈａｒｇｒｅａｖｅ"
DASHES = {
    "hyphen U+2010": "‐",
    "non-breaking hyphen U+2011": "‑",
    "figure dash U+2012": "‒",
    "en dash U+2013": "–",
    "minus sign U+2212": "−",
    "fullwidth hyphen-minus U+FF0D": "－",
}


# --- the matcher --------------------------------------------------------------------------------

@pytest.mark.parametrize("hay, token", [
    (f"The locksmith is {FULLWIDTH}.", "Hargreave"),
    ("The locksmith is Hargreave.", FULLWIDTH),
    (f"THE LOCKSMITH IS {FULLWIDTH.upper()}.", "hargreave"),
    ("Call ＨＡＲＧＲＥＡＶＥ today", "Hargreave"),
    ("The gate code is ＺＸ－４４１０.", "ZX-4410"),
    ("The gate code is ZX-4410.", "ＺＸ－４４１０"),
])
def test_fullwidth_letters_are_their_ascii_letters(hay, token):
    assert carries(hay, token), (hay, token)
    assert text_carries_revoked_token(hay, (token,)), (hay, token)


@pytest.mark.parametrize("hay, token", [
    ("The pin is １２３４.", "1234"),
    ("The pin is 1234.", "１２３４"),
])
def test_fullwidth_digits_stay_their_ascii_digits(hay, token):
    assert carries(hay, token)


@pytest.mark.parametrize("name", sorted(DASHES))
def test_a_hyphen_or_dash_variant_is_a_hyphen_on_either_side(name):
    dash = DASHES[name]
    assert carries(f"The locksmith is Hargreave{dash}Smith.", "Hargreave-Smith"), name
    assert carries("The locksmith is Hargreave-Smith.", f"Hargreave{dash}Smith"), name
    assert carries("The locksmith is Hargreave Smith.", f"Hargreave{dash}Smith"), name
    assert carries(f"The locksmith is Hargreave{dash}Smith.", "Hargreave Smith"), name
    assert text_carries_revoked_token(f"Hargreave{dash}Smith", ("Hargreave-Smith",)), name


def test_the_non_breaking_hyphen_in_fullwidth_text():
    assert carries(f"The locksmith is {FULLWIDTH}‑Ｓｍｉｔｈ.", "Hargreave-Smith")
    assert carries("The locksmith is Hargreave-Smith.", f"{FULLWIDTH}‑Ｓｍｉｔｈ")


def test_a_soft_hyphen_inside_a_word_does_not_hide_it():
    assert carries("Call Harg­reave today.", "Hargreave")
    assert carries("Call Hargreave today.", "Harg­reave")


def test_a_folded_number_still_keeps_the_number_law():
    # "forget 12" never erases another number, whichever way its digits or dashes are written.
    assert not carries("Room ３１２ is upstairs.", "12")
    assert not carries("Room 312 is upstairs.", "１２")
    assert not carries("The pin is 1234.", "１２")
    assert carries("The code is １２.", "12")
    assert carries("Dial 555‒1234 now.", "555-1234")


# --- the fold only adds: every match of the text as written still stands ----------------------------

#: Texts the matcher caught as written, before the fold, where the folded spelling alone would not:
#: folded, the dash or the fullwidth sign between the digits becomes a number joint (". , : / - _"). A
#: forget that caught them must keep catching them; the fold never serves a word again.
RAW_MATCHES = [
    ("pages 10–12", "12", "12"),
    ("2026–10–12", "12", "12"),
    ("Pi is ３．１２ roughly.", "12", "１２"),
    ("We meet at １２：３０.", "12", "１２"),
    ("We sold １，２００ units.", "200", "２００"),
    ("5−12 is negative.", "12", "12"),
    ("Rows 10‑12 are blank.", "10", "10"),
    ("Dial 555‒1234 now.", "1234", "1234"),
    ("Steps 3‐7 done.", "7", "7"),
    ("Path ４／１２ here.", "12", "１２"),
]


@pytest.mark.parametrize("hay, token, word", RAW_MATCHES)
def test_a_text_that_matched_as_written_still_matches(hay, token, word):
    assert carries(hay, token), (hay, token)
    assert forget_needle_in(hay, token), (hay, token)
    assert text_carries_revoked_token(hay, (token,)), (hay, token)


# --- spans: edges, neighbours, soft hyphens, merged old and folded spans ----------------------------

# --- controls: plain ASCII and unrelated text are untouched ----------------------------------------

#: Plain-ASCII verdicts of the matcher before the fold, pinned.
ASCII_VERDICTS = [
    ("The locksmith is Hargreave.", "Hargreave", True),
    ("Hargreaves Ltd", "Hargreave", True),
    ("thermal-pool and more", "thermal pool", True),
    ("THERMAL_POOL", "thermal-pool", True),
    ("Hargrave visited.", "Hargreave", False),
    ("The gate code is 12.", "12", True),
    ("Room 312 is upstairs.", "12", False),
    ("Pi is 3.12 roughly.", "12", False),
    ("The report is dated 2026-10-12.", "12", False),
    ("We sold 1,200 units.", "200", False),
    ("We meet at 12:30.", "12", False),
    ("12kg of flour", "12", True),
    ("hex a12f9c", "12", False),
    ("alpha-beta", "alpha beta", True),
    ("alphabeta", "alpha beta", False),
]


@pytest.mark.parametrize("hay, token, expected", ASCII_VERDICTS)
def test_plain_ascii_matching_is_unchanged(hay, token, expected):
    assert carries(hay, token) is expected
    assert forget_needle_in(hay, token) is expected


def test_spellings_outside_the_fold_are_not_matched():
    # An em dash is sentence punctuation, not a hyphen; a decomposed letter is another entry (the
    # exactness law); a fullwidth word that is not the forgotten word is not matched.
    assert not carries("alpha—beta", "alpha beta")
    assert not carries("Café Central", "Café")
    assert not carries("Ｈａｒｇｒａｖｅ visited.", "Hargreave")


def _word(rng: random.Random, n: int = 7) -> str:
    return "".join(rng.choice(string.ascii_lowercase) for _ in range(n))


def _chat_id(rng: random.Random) -> str:
    return "openclaw:" + "".join(rng.choice("0123456789abcdef") for _ in range(20))


def _attach(chat: str, turn: str, text: str) -> dict:
    doc = ca.stage_document(session_id=chat, data=text.encode("utf-8"))
    ca.bind_to_turn(session_id=chat, turn_id=turn, attachment_ids=[doc["id"]])
    ca.release_turn(session_id=chat, turn_id=turn, outcomes=None)
    return doc


def _served(chat: str) -> str:
    return "\n".join(str(e.get("text") or "")
                     for e in ca.carried_documents_for_turn(session_id=chat, exclude_turn_id="turn-2"))


def _forgotten(monkeypatch, *tokens: str) -> None:
    import core.memory.revocation as revocation

    monkeypatch.setattr(revocation, "revoked_tokens", lambda *a, **k: tokens)


@pytest.mark.parametrize("spelling", [
    f"{FULLWIDTH}‑Ｓｍｉｔｈ",
    "Hargreave‑Smith",
    "Harg\u200breave-Smith",
])
def test_a_document_holding_a_folded_spelling_is_not_carried_after_the_forget(tmp_path, monkeypatch, spelling):
    import core.memory.revocation as revocation

    rng = random.Random(9103)
    _profile(tmp_path)
    chat = _chat_id(rng)
    kept = _attach(chat, "turn-1", f"Line 1: {_word(rng)} {_word(rng)}.\nThe gate code stays 7731.\n")
    _attach(chat, "turn-1b", f"Line 1: {_word(rng)}.\nThe locksmith is {spelling}.\n")
    monkeypatch.setattr(revocation, "memory_home_is_new", lambda *a, **k: False)
    _forgotten(monkeypatch, "Hargreave-Smith")
    carried = ca.carried_documents_for_turn(session_id=chat, exclude_turn_id="turn-2")
    assert [entry["attachment_id"] for entry in carried] == [kept["id"]], carried
    assert "locksmith" not in _served(chat)


# --- end to end: the serve screen, the forget in documents, memory forget then recall ---------------

@pytest.fixture
def memory_home(tmp_path):
    from core.memory.files import memory_path
    from core.runtime_paths import configure_runtime_home

    home = tmp_path / "runtime-home"
    configure_runtime_home(home)
    memory_path().parent.mkdir(parents=True, exist_ok=True)
    yield home
    configure_runtime_home(None)


@pytest.mark.parametrize("stored, command, plain", [
    ("my locksmith is Hargreave‑Smith.", "Forget Hargreave-Smith", "Hargreave-Smith"),
    (f"my locksmith is {FULLWIDTH}.", "Forget Hargreave", "Hargreave"),
    ("my locksmith is Hargreave.", f"Forget {FULLWIDTH}", "Hargreave"),
])
def test_memory_forget_then_recall_catches_a_folded_spelling(memory_home, stored, command, plain):
    from core.context_namespace import ensure_chat_namespace
    from core.context_retrieval import revoked_tokens_for_chat
    from core.memory.entries import (
        add_memory_fact,
        list_memory_entries,
        resolve_memory_access_policy,
        search_relevant_memory,
    )
    from core.persistent_memory import maybe_handle_memory_command

    def recalled(query: str) -> list[str]:
        return [str(r.get("text") or "") for r in search_relevant_memory(query, access_policy=policy, limit=10)]

    chat = "forget-fold-chat"
    ensure_chat_namespace(chat, grant_confirmed_profile=True)
    policy = resolve_memory_access_policy(chat_id=chat)
    assert add_memory_fact(stored, session_id=chat, access_policy=policy)
    assert add_memory_fact("my plumber is Okafor.", session_id=chat, access_policy=policy)
    assert any("locksmith" in t for t in recalled("who is my locksmith")), "recall finds the fact before"
    handled, reply = maybe_handle_memory_command(command, session_id=chat, access_policy=policy)
    assert handled and reply.startswith("Forget applied"), reply
    texts = [str(r.get("text") or "") for r in list_memory_entries(access_policy=policy, limit=20)]
    assert not any("locksmith" in t for t in texts), (reply, texts)
    assert any(t == "my plumber is Okafor." or "Okafor" in t for t in texts), texts
    # Recall after the forget: the fact is not recalled, the other one still is, and the chat's revoked
    # words stop the stored spelling and the plain one from being served again.
    assert not any("locksmith" in t for t in recalled("who is my locksmith")), recalled("who is my locksmith")
    assert any("Okafor" in t for t in recalled("who is my plumber")), recalled("who is my plumber")
    revoked = revoked_tokens_for_chat(chat)
    assert revoked, "the forget left no revoked words for the chat"
    assert text_carries_revoked_token(stored, revoked), (stored, revoked)
    assert text_carries_revoked_token(f"Your locksmith is {plain}.", revoked), (plain, revoked)
    assert not text_carries_revoked_token("Your plumber is Okafor.", revoked), revoked


# --- invisible characters and the small hyphen-minus -------------------------------------------------
#
# A zero-width space (U+200B), word joiner (U+2060), zero-width joiner (U+200D) or zero-width no-break
# space / byte order mark (U+FEFF) hides a word exactly as the soft hyphen does: "Harg​reave" shows
# as "Hargreave". They are removed like the soft hyphen, and the small hyphen-minus (U+FE63) is "-".

INVISIBLE = {
    "soft hyphen U+00AD": "­",
    "zero-width space U+200B": "​",
    "word joiner U+2060": "⁠",
    "zero-width joiner U+200D": "‍",
    "zero-width no-break space U+FEFF": "﻿",
}
NEW_INVISIBLE = [name for name in sorted(INVISIBLE) if "U+00AD" not in name]


@pytest.mark.parametrize("name", NEW_INVISIBLE)
def test_an_invisible_character_inside_a_word_does_not_hide_it(name):
    mark = INVISIBLE[name]
    assert carries(f"Call Harg{mark}reave today.", "Hargreave"), name
    assert carries("Call Hargreave today.", f"Harg{mark}reave"), name
    assert carries(f"The gate code is ZX-44{mark}10.", "ZX-4410"), name
    assert carries(f"The pin is 12{mark}34.", "1234"), name
    assert carries(f"{mark}Hargreave{mark}", "hargreave"), name
    assert text_carries_revoked_token(f"Harg{mark}reave", ("Hargreave",)), name
    assert forget_needle_in(f"Harg{mark}{mark}reave{mark}-Smith", "hargreave smith"), name


@pytest.mark.parametrize("name", NEW_INVISIBLE)
def test_an_invisible_character_keeps_the_number_law(name):
    # Removed, it joins the digits around it into one number, read by the number law: "312" is
    # caught, "31" is not part of it. The digits as written still match as before ("12" stands alone
    # after the invisible character), so no earlier match is lost.
    mark = INVISIBLE[name]
    assert carries(f"Room 3{mark}12 is upstairs.", "312"), name
    assert not carries(f"Room 3{mark}12 is upstairs.", "31"), name
    assert not carries(f"Room 3{mark}12{mark}4 is upstairs.", "312"), name
    assert carries(f"Room 3{mark}12 is upstairs.", "12"), name


def test_the_small_hyphen_minus_is_a_hyphen_on_either_side():
    dash = "﹣"
    assert carries(f"The locksmith is Hargreave{dash}Smith.", "Hargreave-Smith")
    assert carries("The locksmith is Hargreave-Smith.", f"Hargreave{dash}Smith")
    assert carries("The locksmith is Hargreave Smith.", f"Hargreave{dash}Smith")
    assert carries(f"The locksmith is Hargreave{dash}Smith.", "Hargreave Smith")
    assert carries(f"The gate code is ＺＸ{dash}４４１０.", "ZX-4410")
    assert text_carries_revoked_token(f"Hargreave{dash}Smith", ("Hargreave-Smith",))


def test_every_removed_kind_mixed_in_one_word():
    hay = "Call Ha­r​g⁠r‍e﻿ave﹣Ｓｍｉｔｈ today."
    assert carries(hay, "Hargreave-Smith")
    assert carries(hay, "hargreave smith")
    assert carries("Hargreave-Smith", "Ha​­rgreave﹣Smith")


#: Every code point the fold changes: fullwidth ASCII, the hyphen and dash spellings, and the removed
#: invisible characters.
FOLDED_CODE_POINTS = frozenset({
    *range(0xFF01, 0xFF5F),
    0x2010, 0x2011, 0x2012, 0x2013, 0x2212, 0xFE63,
    0x00AD, 0x200B, 0x200D, 0x2060, 0xFEFF,
})


def test_the_pre_check_class_is_exactly_what_the_fold_changes():
    changed = {code for code in range(0x110000) if chr(code).translate(_SPELLING_FOLD) != chr(code)}
    caught = {code for code in range(0x110000) if _SPELLING_FOLD_CHARS.fullmatch(chr(code))}
    assert changed == caught, sorted(changed ^ caught)
    assert changed == FOLDED_CODE_POINTS, sorted(changed ^ FOLDED_CODE_POINTS)
    removed = {code for code, to in _SPELLING_FOLD.items() if to is None}
    assert removed == {0x00AD, 0x200B, 0x200D, 0x2060, 0xFEFF}
    assert all(to is None or isinstance(to, int) for to in _SPELLING_FOLD.values())  # one for one, or none


# --- spans with every removed kind: mixed runs, edges, neighbours, several in a row -------------------

# --- the new fold only adds: every match and span of the earlier fold still stands -------------------

#: Texts the matcher caught before the invisible characters and U+FE63 were folded, where the new fold
#: alone would not: removed, the invisible character joins two digit runs into one number, or the
#: small hyphen-minus becomes a number joint. Caught as written, or by the earlier fold (U+2011 as "-",
#: fullwidth "ａ" as "a"), they must stay caught, with the same span.
EARLIER_MATCHES = [
    ("Room 3​12 here", "12", ["12"]),
    ("Weight 12⁠.5 kg", "12", ["12"]),
    ("x 12​-​3", "12", ["12"]),
    ("１­2​3", "12", ["１­2"]),
    ("Code a‑12​3", "a-12", ["a‑12"]),
    ("Code ａ-12﹣3", "a-12", ["ａ-12"]),
    ("Code ａ‑12⁠.5", "a-12", ["ａ‑12"]),
    ("Pages 10﹣12", "12", ["12"]),
    ("Pages 10﹣12", "10", ["10"]),
]


@pytest.mark.parametrize("hay, token, words", EARLIER_MATCHES)
def test_a_match_of_the_earlier_fold_still_matches(hay, token, words):
    assert carries(hay, token), (hay, token)
    assert forget_needle_in(hay, token), (hay, token)
    assert text_carries_revoked_token(hay, (token,)), (hay, token)


@pytest.mark.parametrize("stored, command, plain", [
    ("my locksmith is Harg​reave.", "Forget Hargreave", "Hargreave"),
    ("my locksmith is Hargreave﹣Smith.", "Forget Hargreave-Smith", "Hargreave-Smith"),
    ("my locksmith is Hargreave.", "Forget Harg⁠reave", "Hargreave"),
])
def test_memory_forget_then_recall_catches_an_invisible_spelling(memory_home, stored, command, plain):
    # The same forget-then-recall flow as the folded spellings above, with the new characters.
    test_memory_forget_then_recall_catches_a_folded_spelling(memory_home, stored, command, plain)
