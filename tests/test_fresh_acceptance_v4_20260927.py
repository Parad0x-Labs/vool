"""FRESH ACCEPTANCE SET v4 — negative/uncertain publicity in any case or
typography, and genuine public-value preservation (review round 3 follow-up).

Frozen BEFORE first execution (followup3/FRESH-ACCEPTANCE-V4-FREEZE.json with
the candidate SHA). New wording and new expected contents — choir, rowing and
climbing-club domains, distinct from every earlier reviewer/acceptance fixture
(no replay or renaming of C1-C5/N1-N5/R1-R7 or v1-v3 cases).
"""
from __future__ import annotations

import re

import pytest

import core.context_retrieval as cr
from core.context_namespace import ensure_chat_namespace
from core.memory.entries import resolve_memory_access_policy
from core.runtime_paths import configure_runtime_home
from core.secret_redaction import contains_secret, redact_secrets

SEED = "cobalt drizzle pebble lattice thicket ember sonnet marlin"
SEED_WORDS = SEED.split()
PUBLIC_CHOIR = "sing low breathe slow land the tone"


@pytest.fixture()
def fresh_profile(tmp_path, monkeypatch):
    import core.embedding_service as embedding_service

    home = tmp_path / "profile"
    home.mkdir()
    monkeypatch.setenv("VOOL_HOME", str(home))
    monkeypatch.setenv("VOOL_HOME", str(home))
    configure_runtime_home(home)
    embedding_service._best_embed_model = lambda: None
    from storage.migrations import run_migrations

    run_migrations()
    yield str(home)
    configure_runtime_home(None)


def _store(home, chat, text):
    ensure_chat_namespace(chat, grant_current_receipts=False)
    policy = resolve_memory_access_policy(chat_id=chat)
    return cr.store_turn(
        chat, text, "Noted.", access_policy=policy,
        source_context={"chat_id": chat, "runtime_home": home},
    )


def _context(home, chat, query):
    ensure_chat_namespace(chat, grant_current_receipts=False)
    policy = resolve_memory_access_policy(chat_id=chat)
    out = cr.inject_retrieved(
        chat, query, [{"role": "user", "content": query}],
        access_policy=policy,
        source_context={"chat_id": chat, "runtime_home": home},
        env={"VOOL_CONTEXT_CAPSULE_V2": "1"},
    )
    return next(
        (str(m.get("content") or "") for m in out
         if m.get("role") == "system" and "<retrieved_context>" in str(m.get("content") or "")),
        "",
    )


def _no_seed_word(text):
    return not any(re.search(rf"\b{w}\b", text.lower()) for w in SEED_WORDS)


# ---- negative/uncertain publicity in untested case/typography shapes ---------

def test_v4_1_titlecase_hyphen_chain_masked_end_to_end(fresh_profile):
    record = "Please remember the choir\u2019s Not-For-Public mnemonic: " + SEED + "."
    status = _store(fresh_profile, "v4-1", record)
    assert status.get("secret_redacted") is True, status
    ctx = _context(fresh_profile, "v4-1", "What is the choir mnemonic?")
    assert _no_seed_word(ctx) and "[redacted]" in ctx, ctx


def test_v4_2_modifier_apostrophe_negation_masked(fresh_profile):
    # U+02BC MODIFIER LETTER APOSTROPHE — a third apostrophe family member
    record = "Remember this isn\u02bct public climbing mnemonic: " + SEED + "."
    status = _store(fresh_profile, "v4-2", record)
    assert status.get("secret_redacted") is True, status


def test_v4_3_uppercase_hedge_masked_end_to_end(fresh_profile):
    record = "Please remember our ARGUABLY Public rowing mnemonic: " + SEED + "."
    status = _store(fresh_profile, "v4-3", record)
    assert status.get("secret_redacted") is True, status
    ctx = _context(fresh_profile, "v4-3", "What is the rowing mnemonic?")
    assert _no_seed_word(ctx) and "[redacted]" in ctx, ctx


def test_v4_4_negation_before_caps_qualifier_masked(fresh_profile):
    record = "Please remember my never PUBLIC mnemonic: " + SEED + "."
    status = _store(fresh_profile, "v4-4", record)
    assert status.get("secret_redacted") is True, status


# ---- genuine public-value preservation ----------------------------------------

def test_v4_5_affirmative_public_choir_mnemonic_preserved_and_retrievable(fresh_profile):
    record = "Please remember our public choir mnemonic: " + PUBLIC_CHOIR + "."
    status = _store(fresh_profile, "v4-5", record)
    assert status["status"] == "stored", status
    assert "secret_redacted" not in status, status
    ctx = _context(fresh_profile, "v4-5", "What is the choir mnemonic?")
    assert PUBLIC_CHOIR in ctx, ctx


def test_v4_6_typographic_possessive_public_preserved(fresh_profile):
    # curly-apostrophe possessive in the surrounding prose must not disable the
    # affirmative exemption nor alter the stored bytes
    # AMENDED after the preserved first attempt: the original record lacked the
    # memory command, so admission correctly skipped it (known importance-gate
    # behavior) and the case measured admission, not preservation. Marker added;
    # declared outcome unchanged; disclosed in the freeze manifest.
    record = ("Please remember the club\u2019s public rowing mnemonic: "
              + PUBLIC_CHOIR + ".")
    status = _store(fresh_profile, "v4-6", record)
    assert status["status"] == "stored", status
    assert "secret_redacted" not in status, status
    ctx = _context(fresh_profile, "v4-6", "What is the rowing mnemonic?")
    assert PUBLIC_CHOIR in ctx, ctx


# ---- unit-level shape matrix ---------------------------------------------------

def test_v4_7_unit_matrix_case_and_typography():
    masked = [
        "our NON-public mnemonic: " + SEED,
        "this ISN\u2019T public mnemonic: " + SEED,
        "my Not-For-Public mnemonic: " + SEED,
        "our can\u02bct-be-public mnemonic: " + SEED,
        "the arguably Public mnemonic: " + SEED,
        "my NEVER-public Mnemonic: " + SEED,
    ]
    for text in masked:
        assert SEED not in redact_secrets(text), text
        assert contains_secret(text) is True, text
    preserved = [
        "our public choir mnemonic: " + PUBLIC_CHOIR,
        "the Well-Known rowing mnemonic: " + PUBLIC_CHOIR,
        "our PUBLIC climbing mnemonic: " + PUBLIC_CHOIR,
    ]
    for text in preserved:
        assert redact_secrets(text) == text, text
