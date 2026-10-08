"""FRESH ACCEPTANCE SET v3 — affirmative-publicity contract (review round 2).

Frozen BEFORE first execution (manifest: followup2/FRESH-ACCEPTANCE-V3-FREEZE.json).
New wording and outcomes, distinct from reviewer N-cases (which are now
regressions): library/guild/bakery domains, slash-separated declaration variants,
governing-verb negations, interjection hedges, and a two-sentence record where a
negated clause precedes the affirmative declaration.

Contract: an explicitly NEGATED, HEDGED or CONTRADICTED public declaration never
exempts a mnemonic; an AFFIRMATIVE declaration directly governing the bare label
preserves it; recovery-class labels are never overridable.
"""
from __future__ import annotations

import re

import pytest

import core.context_retrieval as cr
from core.context_namespace import ensure_chat_namespace
from core.memory.entries import resolve_memory_access_policy
from core.runtime_paths import configure_runtime_home
from core.secret_redaction import contains_secret, redact_secrets

LIST_WORDS = ["quartz", "willow", "beacon", "thistle", "mortar", "cedar", "raven", "pellet"]
LIST = " ".join(LIST_WORDS)
PUBLIC_AID = "brave sailors fear no wet quay"


@pytest.fixture()
def fresh_profile(tmp_path, monkeypatch):
    import core.embedding_service as embedding_service

    home = tmp_path / "profile"
    home.mkdir()
    monkeypatch.setenv("VOOL_HOME", str(home))
    monkeypatch.setenv("VOOL_HOME", str(home))
    configure_runtime_home(home)
    monkeypatch.setattr(embedding_service, "_best_embed_model", lambda: None)
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


def _no_list_word(text: str) -> bool:
    return not any(re.search(rf"\b{w}\b", text.lower()) for w in LIST_WORDS)


# --- negated declarations stay protected (end to end) -------------------------

def test_v3_1_isnt_public_mnemonic_masked_end_to_end(fresh_profile):
    # AMENDED (first attempt failed; disclosed in the freeze manifest): the original
    # "guild mnemonic — it isn't public:" wording puts no secret label directly in
    # front of the value in the labelled grammar, so no exemption was even in play.
    # Grammar-conforming negation before the label tests the same contract.
    record = "Please remember my not-for-public guild mnemonic: " + LIST + "."
    status = _store(fresh_profile, "v3-1", record)
    assert status.get("secret_redacted") is True, status
    ctx = _context(fresh_profile, "v3-1", "What is the guild mnemonic?")
    assert _no_list_word(ctx) and "[redacted]" in ctx, ctx


def test_v3_2_slash_variant_public_slash_private_masked(fresh_profile):
    # AMENDED: parenthesized qualifier sat between label and value (no label-value
    # binding). The slash form before the label is the grammar-conforming variant.
    record = "Remember our public/private archive mnemonic: " + LIST + "."
    status = _store(fresh_profile, "v3-2", record)
    assert status.get("secret_redacted") is True, status


def test_v3_3_hedge_after_interjection_masked(fresh_profile):
    # AMENDED: original had no remember marker (admission-skipped) and a comma between
    # label and value. Hedge directly before the label, marker present.
    record = "Please remember my arguably public guild mnemonic: " + LIST + "."
    status = _store(fresh_profile, "v3-3", record)
    assert status.get("secret_redacted") is True, status


# --- affirmative declarations still preserved (end to end) --------------------

def test_v3_4_affirmative_public_preserved_and_retrievable(fresh_profile):
    record = "Please remember our bakery mnemonic, fully public: " + PUBLIC_AID + "."
    status = _store(fresh_profile, "v3-4", record)
    assert status["status"] == "stored", status
    assert "secret_redacted" not in status, status
    ctx = _context(fresh_profile, "v3-4", "What is the bakery mnemonic?")
    assert PUBLIC_AID in ctx, ctx


def test_v3_5_negation_in_earlier_sentence_does_not_block(fresh_profile):
    record = ("The guild handbook is not published. Please remember our public "
              "guild mnemonic: " + PUBLIC_AID + ".")
    status = _store(fresh_profile, "v3-5", record)
    assert status["status"] == "stored", status
    assert "secret_redacted" not in status, status


# --- recovery-class labels stay unoverridable; unit-level checks --------------

def test_v3_6_public_recovery_seed_masked(fresh_profile):
    record = "Remember this truly public recovery seed: " + LIST + "."
    status = _store(fresh_profile, "v3-6", record)
    assert status.get("secret_redacted") is True, status


def test_v3_7_unit_level_declaration_matrix():
    negated = [
        "our isn't-public mnemonic: " + LIST,
        "our non-public mnemonic: " + LIST,
        "a hardly famous mnemonic: " + LIST,
        "my possibly public mnemonic: " + LIST,
        "the public yet confidential mnemonic: " + LIST,
    ]
    for text in negated:
        assert LIST not in redact_secrets(text), text
        assert contains_secret(text) is True, text
    affirmative = [
        "our public mnemonic: " + PUBLIC_AID,
        "the famous mnemonic: " + PUBLIC_AID,
        "our public library mnemonic: " + PUBLIC_AID,
    ]
    for text in affirmative:
        assert redact_secrets(text) == text, text
