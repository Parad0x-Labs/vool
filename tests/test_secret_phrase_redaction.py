"""Labelled multi-word phrase secrets (recovery/seed/mnemonic) must be masked at
admission — before embedding, persistence and re-injection.

Reproduced product failure (2026-09-27, 5b6fb6df): an explicitly
recovery-phrase-framed eight-word synthetic phrase was stored verbatim by
``store_turn`` and re-injected verbatim in ``<retrieved_context>``. The owning
boundary is ``core.secret_redaction`` (single-token labelled rule could not match a
multi-word value); fixing it protects the conversation log, derived summaries and
the provider boundary because they share this one redaction authority.

Precision controls: an unlabelled word run is never touched; benign discussion of
recovery phrases and prose tails after the label keep their prior behaviour.
"""
from __future__ import annotations

from types import SimpleNamespace

import core.context_retrieval as cr
from core.context_namespace import ensure_chat_namespace
from core.memory.entries import resolve_memory_access_policy
from core.secret_redaction import contains_secret, redact_secrets

_PHRASE = "ostrich blend canal marble seven thistle foyer pond"


# --- recall: labelled phrase secrets are masked -------------------------------

def test_labelled_recovery_phrase_masked_with_colon() -> None:
    out = redact_secrets("Please remember my recovery phrase: " + _PHRASE + ".")
    assert _PHRASE not in out
    assert "recovery phrase: [redacted]" in out
    assert contains_secret("Please remember my recovery phrase: " + _PHRASE + ".")


def test_labelled_recovery_phrase_masked_with_is_connector() -> None:
    out = redact_secrets("my recovery phrase is " + _PHRASE)
    assert _PHRASE not in out and "[redacted]" in out


def test_seed_words_backup_phrase_and_mnemonic_masked() -> None:
    for label in ("seed words", "backup phrase", "mnemonic", "seed phrase"):
        text = f"store the {label}: amber lodge quiet flute reed"
        out = redact_secrets(text)
        assert "amber lodge" not in out, label


def test_twelve_word_wordlist_masked() -> None:
    phrase = " ".join(["radius", "cabin", "stream", "pilot", "gently", "novel",
                       "harbor", "velvet", "ladder", "switch", "orbit", "maple"])
    out = redact_secrets("wallet seed phrase is " + phrase)
    assert phrase not in out and "[redacted]" in out


# --- formatting variants of the labelled value (review R2/R3) ------------------

_PHRASE_WORDS = ["velvet", "cabin", "orbit", "meadow", "maple", "ladder", "harbor", "quilt"]


def test_comma_separated_recovery_phrase_masked() -> None:
    text = "Remember my recovery phrase: " + ", ".join(_PHRASE_WORDS) + "."
    out = redact_secrets(text)
    for word in _PHRASE_WORDS:
        import re as _re

        assert not _re.search(rf"\b{word}\b", out), word
    assert "recovery phrase: [redacted]" in out
    assert contains_secret(text)


def test_curly_quoted_recovery_phrase_masked() -> None:
    for open_q, close_q in (("\u201c", "\u201d"), ("\u2018", "\u2019"), ('"', '"'), ("'", "'")):
        text = "Remember my recovery phrase: " + open_q + " ".join(_PHRASE_WORDS) + close_q + "."
        out = redact_secrets(text)
        assert _PHRASE_WORDS[0] not in out, (open_q, out)
        assert "[redacted]" in out, (open_q, out)


def test_mixed_comma_and_space_separators_masked() -> None:
    text = "backup words: velvet,cabin orbit, meadow maple ladder harbor quilt"
    out = redact_secrets(text)
    assert "velvet" not in out and "[redacted]" in out


def test_benign_comma_prose_without_secret_label_untouched() -> None:
    text = "we packed velvet, cabin, orbit, meadow, maple tags for the fair."
    assert redact_secrets(text) == text
    assert contains_secret(text) is False


def test_prose_tail_after_comma_list_still_masked_to_list_end() -> None:
    # The wordlist-shaped value ends at the last 3-8 char alpha token; prose that
    # cannot be a wordlist token (here "afterwards", 10 chars) survives untouched.
    out = redact_secrets("seed words: amber lodge quiet flute, afterwards we sailed home.")
    assert "amber" not in out and "[redacted]" in out
    assert "afterwards we sailed home" in out


# --- public-mnemonic declaration contract (review R4) --------------------------

def test_public_mnemonic_value_preserved() -> None:
    text = "Please remember our public mnemonic: every good bird dances softly."
    assert redact_secrets(text) == text
    assert contains_secret(text) is False


def test_well_known_mnemonic_preserved() -> None:
    text = "Remember the well-known mnemonic: every good bird dances softly."
    assert redact_secrets(text) == text


def test_public_qualifier_cannot_override_recovery_class_labels() -> None:
    phrase = " ".join(_PHRASE_WORDS)
    for label in ("recovery phrase", "seed words", "backup phrase"):
        text = f"Remember my public {label}: {phrase}."
        out = redact_secrets(text)
        assert phrase not in out, label
        assert contains_secret(text) is True, label


def test_undeclared_mnemonic_still_masked() -> None:
    phrase = " ".join(_PHRASE_WORDS)
    out = redact_secrets("Remember my mnemonic: " + phrase + ".")
    assert phrase not in out and "[redacted]" in out


def test_distant_public_word_does_not_exempt_mnemonic_label() -> None:
    # "public" governs "phrase" here only via the recovery-phrase label, which is
    # not overridable anyway; and a public word far from a bare mnemonic label
    # (label not directly followed by the value in the first place) masks nothing.
    phrase = " ".join(_PHRASE_WORDS)
    assert "velvet" not in redact_secrets("Remember my mnemonic: " + phrase + ", shared publicly.")


def test_public_mnemonic_single_token_value_preserved() -> None:
    assert redact_secrets("our public mnemonic: eagle.") == "our public mnemonic: eagle."
    assert "redacted" not in redact_secrets("our public mnemonic: eagle.")


# --- affirmative-declaration semantics (review N1/N2 regression) --------------

def test_negated_public_mnemonic_still_masked() -> None:
    text = "Please remember my not public mnemonic: " + " ".join(_PHRASE_WORDS) + "."
    out = redact_secrets(text)
    assert _PHRASE_WORDS[0] not in out and "[redacted]" in out
    assert contains_secret(text) is True


def test_hyphenated_non_public_mnemonic_still_masked() -> None:
    text = "Please remember my non-public mnemonic: " + " ".join(_PHRASE_WORDS) + "."
    out = redact_secrets(text)
    assert _PHRASE_WORDS[0] not in out and "[redacted]" in out


def test_hedged_public_mnemonic_still_masked() -> None:
    for prefix in ("maybe public", "probably well-known", "not really famous",
                   "never-public", "not so public"):
        text = f"Remember my {prefix} mnemonic: " + " ".join(_PHRASE_WORDS) + "."
        out = redact_secrets(text)
        assert _PHRASE_WORDS[0] not in out, (prefix, out)


def test_contradicted_public_mnemonic_still_masked() -> None:
    for between in ("but private", "but sensitive", "but secret"):
        text = f"Remember our public {between} mnemonic: " + " ".join(_PHRASE_WORDS) + "."
        out = redact_secrets(text)
        assert _PHRASE_WORDS[0] not in out, (between, out)


def test_negation_in_earlier_clause_does_not_block_public_mnemonic() -> None:
    text = ("It is not listed in the handbook. Please remember our public music "
            "mnemonic: every good bird dances softly.")
    assert redact_secrets(text) == text


def test_governing_negation_in_same_clause_blocks_exemption() -> None:
    text = "I do not think this is a public mnemonic: " + " ".join(_PHRASE_WORDS) + "."
    out = redact_secrets(text)
    assert _PHRASE_WORDS[0] not in out and "[redacted]" in out


# --- case and typographic apostrophes must not flip negative declarations ---
# (review C2/C3: 'NON-public' passed the case-sensitive hyphen guard and
#  "isn\u2019t public" missed the ASCII-only contraction negation)

def test_uppercase_non_public_still_masked() -> None:
    text = "Please remember my NON-public mnemonic: " + " ".join(_PHRASE_WORDS) + "."
    out = redact_secrets(text)
    assert _PHRASE_WORDS[0] not in out and "[redacted]" in out
    assert contains_secret(text) is True


def test_typographic_apostrophe_negation_still_masked() -> None:
    for contraction in ("isn\u2019t", "don\u2019t", "can\u2019t", "couldn\u2019t", "ISN\u2019T"):
        text = (f"Please remember this {contraction} public mnemonic: "
                + " ".join(_PHRASE_WORDS) + ".")
        out = redact_secrets(text)
        assert _PHRASE_WORDS[0] not in out, (contraction, out)
        assert contains_secret(text) is True, contraction


def test_uppercase_affirmative_public_still_preserved() -> None:
    for qualifier in ("Public", "Well-Known", "FAMOUS"):
        text = f"Remember our {qualifier} music mnemonic: every good bird dances softly."
        assert redact_secrets(text) == text, qualifier


def test_uppercase_contradiction_still_masked() -> None:
    text = "Remember our public but PRIVATE mnemonic: " + " ".join(_PHRASE_WORDS) + "."
    out = redact_secrets(text)
    assert _PHRASE_WORDS[0] not in out and "[redacted]" in out


def test_benign_text_with_typographic_apostrophes_untouched() -> None:
    # analysis normalization never rewrites stored text: benign curly-apostrophe
    # prose passes through byte-identical
    text = "Our brass band\u2019s march is the guild\u2019s favorite."
    assert redact_secrets(text) == text


# --- precision: benign text is left alone ------------------------------------

def test_unlabelled_word_run_untouched() -> None:
    text = "we saw heron elk deer roam past the cabin porch at dawn"
    assert redact_secrets(text) == text
    assert contains_secret(text) is False


def test_benign_recovery_phrase_discussion_untouched() -> None:
    text = "Never share your recovery phrase with anyone, and never store it in plain text."
    assert redact_secrets(text) == text


def test_prose_tail_after_label_untouched() -> None:
    # short function word / long word / too few tokens all keep prior behaviour
    assert redact_secrets("my recovery phrase is stored in the vault") == \
        "my recovery phrase is stored in the vault"
    assert redact_secrets("my recovery phrase is written on the whiteboard") == \
        "my recovery phrase is written on the whiteboard"
    assert redact_secrets("my passphrase is fine") == "my passphrase is fine"


# --- the native store path: admission + honest write status -------------------

class _FakeMemory:
    """The memory store as `store_turn` uses it. Since 0446c715 (2026-09-29, "retain both roles as source
    evidence") every turn is first written as a source occurrence, then indexed; the fake records both, so
    the redaction is checked on the source evidence as well as on the semantic derivative."""

    def __init__(self):
        self.stored: list[str] = []
        self.occurrences: list[str] = []

    def occurrence_store(self, *, body, **_kwargs):
        self.occurrences.append(body)
        return SimpleNamespace(occurrence_id=f"occ-{len(self.occurrences)}")

    def node_store(self, *, content, keywords, tags, context_description, embedding,
                   embedding_backend="", lineage_request_id="", **_kwargs):
        self.stored.append(content)

    def close(self):
        pass


def test_store_turn_masks_phrase_before_persist_and_reports_redaction(monkeypatch):
    fake = _FakeMemory()
    monkeypatch.setattr(cr, "_open_memory", lambda: fake)
    monkeypatch.setattr(cr, "embed", lambda text: [0.0])
    monkeypatch.setattr(cr, "_session_scope_key", lambda sid: "sess")
    ensure_chat_namespace("sess", grant_current_receipts=False)
    policy = resolve_memory_access_policy(chat_id="sess")

    result = cr.store_turn(
        "sess",
        "Please remember my recovery phrase: " + _PHRASE + ".",
        "noted",
        access_policy=policy,
    )

    assert fake.stored, "the masked record should still be persisted"
    assert fake.occurrences, "the turn should still be retained as source evidence"
    for content in fake.stored:
        assert _PHRASE not in content, "phrase must not reach the semantic store"
        assert "[redacted]" in content
    for body in fake.occurrences:
        assert _PHRASE not in body, "phrase must not reach the source evidence"
    assert any("[redacted]" in body for body in fake.occurrences), fake.occurrences
    # A redacted write must not read as the sensitive request being saved as stated.
    assert result["secret_redacted"] is True
    assert result["reason"] == "eligible_user_content_secret_redacted"


def test_store_turn_plain_record_still_reports_eligible(monkeypatch):
    fake = _FakeMemory()
    monkeypatch.setattr(cr, "_open_memory", lambda: fake)
    monkeypatch.setattr(cr, "embed", lambda text: [0.0])
    monkeypatch.setattr(cr, "_session_scope_key", lambda sid: "sess")
    ensure_chat_namespace("sess2", grant_current_receipts=False)
    policy = resolve_memory_access_policy(chat_id="sess2")

    result = cr.store_turn(
        "sess2",
        "Please remember: the observatory parking is the north lot.",
        "noted",
        access_policy=policy,
    )
    assert result["reason"] == "eligible_user_content"
    assert "secret_redacted" not in result
