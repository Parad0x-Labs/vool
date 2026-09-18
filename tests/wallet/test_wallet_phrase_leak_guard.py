"""The recovery-phrase leak detector: deterministic, and it bites on every real leak shape.

Background: the served wallet tests asserted that none of the first phrase words appeared as a bare token in the
model prompts. The prompt is English; 'page' and 'ask' are BIP-39 words; the check went red on the phrase draw
(observed 2026-09-07: three runs, one red on 'page', one on 'ask'). This file pins the replacement.
"""
from __future__ import annotations

import json
import random

from tests.wallet._rig import phrase_leaked

PHRASE = "legal winner thank year wave sausage worth useful legal winner thank yellow"
PROSE = ("You are Atlas, a knowledgeable AI assistant. Keep your tone warm and relaxed. Ask on the official help page "
         "you typed yourself; the state is confirmed once; the wallet is legal and useful.")


def test_verbatim_phrase_is_a_leak_through_quoting_newlines_and_json():
    assert phrase_leaked(PHRASE, "before " + PHRASE + " after") == "phrase verbatim"
    assert phrase_leaked(PHRASE, PHRASE.replace(" ", ",\n")) == "phrase verbatim"
    assert phrase_leaked(PHRASE, json.dumps({"rows": [{"note": PHRASE.upper()}]})) == "phrase verbatim"


def test_three_consecutive_words_in_phrase_order_are_a_leak():
    words = PHRASE.split()
    reason = phrase_leaked(PHRASE, "the model saw " + " ".join(words[4:7]) + " in a tool result")
    assert reason is not None and reason.startswith("consecutive phrase words 5-7"), reason


def test_half_the_phrase_scattered_through_the_text_is_a_leak():
    words = PHRASE.split()
    scattered = " lorem ".join(words[i] for i in (0, 2, 4, 6, 7, 9))
    reason = phrase_leaked(PHRASE, scattered)
    assert reason is not None and reason.startswith("6 distinct phrase words present"), reason


def test_one_common_english_word_is_not_a_leak_nor_are_two_apart_or_a_substring_in_an_id():
    assert phrase_leaked(PHRASE, PROSE) is None  # 'legal', 'useful', 'year'-less prose: three scattered words, no run
    assert phrase_leaked(PHRASE, "the page asks for winner status") is None
    assert phrase_leaked(PHRASE, json.dumps({"tx": "ab3fyearwave9k", "id": "w-thank-1"})) is None, "substrings inside identifiers are not tokens"
    assert phrase_leaked("", PROSE) is None


def test_two_adjacent_words_do_not_flag_but_the_run_threshold_is_the_knob():
    words = PHRASE.split()
    two = " ".join(words[1:3])
    assert phrase_leaked(PHRASE, f"noise {two} noise") is None
    assert phrase_leaked(PHRASE, f"noise {two} noise", run=2) is not None


def test_random_phrases_against_english_prose_never_flag_by_chance():
    from core.wallet import mnemonic

    wordlist = _english_wordlist(mnemonic)
    rng = random.Random(20260907)
    prose_words = rng.sample(wordlist, 120)  # worst case: a prompt built from 120 distinct BIP-39 words
    prose = " ".join(prose_words)
    flagged = []
    for _ in range(300):
        phrase = " ".join(rng.choice(wordlist) for _ in range(12))
        reason = phrase_leaked(phrase, prose)
        if reason is not None:
            flagged.append((phrase, reason))
    assert flagged == [], flagged


def _english_wordlist(mnemonic_module) -> list[str]:
    words = mnemonic_module.WORDLIST_PATH.read_text(encoding="utf-8").split()
    assert len(words) == 2048, len(words)
    return words
