"""The Python Porter stemmer must match SQLite FTS5's ``porter unicode61``
tokenizer exactly — it restores the single stemming authority the scoped BM25
leg shares with the FTS5 index (core/vool_memory.py:_scoped_bm25_scores).

The full 234,454-word macOS system dictionary was compared with zero
mismatches at repair time (evidence:
porter-equivalence-fulldict.json in the repair job's artifacts). These tests
pin the behavior on a frozen curated set (variant-defining words) plus a
deterministic system-dictionary sample when the dictionary is present.
"""
from __future__ import annotations

import sqlite3

import pytest

from core.porter_stem import stem

# Every word that distinguishes porter1.c from other Porter variants:
# the two departures (bli->ble, logi->log), no dying/lying/tying special,
# skies->ski, news->new, the length<=2 exit, and the classic rule table.
_CURATED = {
    "parking": "park", "parked": "park", "guests": "guest", "guest": "guest",
    "embli": "embl", "emlogi": "emlog", "radalli": "radal",
    "conization": "coniz", "relational": "relat", "rational": "ration",
    "troubling": "troubl", "dying": "dy", "lying": "ly", "tying": "ty",
    "news": "new", "skies": "ski", "exceedingly": "exceedingli",
    "electricity": "electr", "running": "run", "hopping": "hop",
    "falling": "fall", "filing": "file", "sized": "size", "sing": "sing",
    "agreed": "agre", "feed": "feed", "plastered": "plaster",
    "motoring": "motor", "escape": "escap", "crying": "cry",
    "meeting": "meet", "veering": "veer", "compasses": "compass",
    "generalization": "gener", "oscillators": "oscil", "happy": "happi",
    "sky": "sky", "alumni": "alumni", "by": "by", "is": "is",
    "caresses": "caress", "ponies": "poni", "ties": "ti",
    "caress": "caress", "cats": "cat",
    "disabled": "disabl", "matting": "mat", "mating": "mate", "mill": "mill", "tanned": "tan",
    "hissing": "hiss", "fizzed": "fizz", "failing": "fail", "conditional": "condit", "valenci": "valenc", "hesitanci": "hesit",
    "digitizer": "digit", "conformabli": "conform", "radicalli": "radic",
    "differentli": "differ", "vileli": "vile", "analogousli": "analog",
    "vietnamization": "vietnam", "predication": "predic",
    "operator": "oper", "feudalism": "feudal", "decisiveness": "decis",
    "hopefulness": "hope", "callousness": "callous", "formaliti": "formal",
    "sensitiviti": "sensit", "sensibiliti": "sensibl",
    "triplicate": "triplic", "formative": "form", "formalize": "formal",
    "electriciti": "electr", "electrical": "electr", "hopeful": "hope",
    "goodness": "good", "revival": "reviv", "allowance": "allow",
    "inference": "infer", "airliner": "airlin", "gyroscopic": "gyroscop",
    "adjustable": "adjust", "defensible": "defens", "irritant": "irrit",
    "replacement": "replac", "adjustment": "adjust", "dependent": "depend",
    "adoption": "adopt", "homologou": "homolog", "communism": "commun",
    "activate": "activ", "angulariti": "angular", "homologous": "homolog",
    "effective": "effect", "bowdlerize": "bowdler",
    "probate": "probat", "rate": "rate", "cease": "ceas",
    "controll": "control", "roll": "roll",
}


def _fts5_stem(word: str) -> list[str]:
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute(
            "CREATE VIRTUAL TABLE t USING fts5(content, tokenize='porter unicode61')"
        )
        conn.execute("INSERT INTO t(content) VALUES (?)", (word,))
        conn.execute("CREATE VIRTUAL TABLE v USING fts5vocab('t','row')")
        return [row[0] for row in conn.execute("SELECT term FROM v")]
    finally:
        conn.close()


def test_curated_variant_defining_words_match_fts5() -> None:
    mismatches = [
        (word, expected, stem(word))
        for word, expected in _CURATED.items()
        if stem(word) != expected
    ]
    assert not mismatches, mismatches


def test_curated_words_match_fts5_index_exactly() -> None:
    for word in _CURATED:
        assert _fts5_stem(word) == [stem(word)], word


def test_short_and_non_alpha_tokens_pass_through() -> None:
    assert stem("ab") == "ab"
    assert stem("a") == "a"
    assert stem("sk-12345") == "sk-12345"  # non-alpha tokens are not stemmed
    assert stem("12north") == "12north"


def test_deterministic_dictionary_sample_matches_fts5() -> None:
    dict_path = None
    for candidate in ("/usr/share/dict/words", "/usr/dict/words"):
        from pathlib import Path

        if Path(candidate).exists():
            dict_path = candidate
            break
    if dict_path is None:
        pytest.skip("no system dictionary available")
    words = sorted(
        {
            line.strip().lower()
            for line in open(dict_path, encoding="utf-8", errors="ignore")
            if line.strip().isalpha() and line.strip().isascii()
        }
    )
    sample = words[::13]  # ~18k words, deterministic
    mismatches = [
        (word, _fts5_stem(word), stem(word))
        for word in sample
        if _fts5_stem(word) != [stem(word)]
    ]
    assert not mismatches, mismatches[:10]
