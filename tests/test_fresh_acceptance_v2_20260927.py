"""FRESH ACCEPTANCE SET v2 — follow-up after the independent review of
native-memory-local-repair-20260927.

Frozen BEFORE first execution (sha256 in FRESH-ACCEPTANCE-V2-FREEZE.json with
the candidate SHA). New wording, structures and domains (print shop, marina,
ceramics studio) — distinct from the review's trolley/mnemonic fixtures and
from this job's first acceptance set (garden/harbor/inspector/bakery).

Contract A: labelled phrase-secret values must not persist or disclose
verbatim across ordinary formatting (comma lists, curly quotes) and an
explicit public declaration preserves a bare mnemonic while never overriding
recovery-class labels.

Contract P: morphological paraphrase recall through the native store →
close → fresh reopen → query → injected-context path, including the long-
sentence window selection, with negation, condition and no-answer controls.
"""
from __future__ import annotations

import re

import pytest

import core.context_retrieval as cr
from core.context_namespace import ensure_chat_namespace
from core.memory.entries import resolve_memory_access_policy
from core.runtime_paths import configure_runtime_home

SEED_LIST = "glaze kiln bisque flux ember quartz cobalt umber"
SEED_WORDS = SEED_LIST.split()

A1_RECORD = "Please remember: my seed phrase is " + ", ".join(SEED_WORDS) + "."
A2_RECORD = "Please remember my backup words: \u201cflux ember quartz cobalt umber glaze kiln bisque\u201d."
A3_RECORD = "Please remember the well-known glazing mnemonic: slow heat, even cool, no shock."
A4_RECORD = "Remember the public recovery seed: glaze kiln bisque flux ember quartz cobalt umber."
# AMENDED after the preserved first attempt: the original fixture omitted the
# memory command, so admission correctly skipped it (importance gate, the known
# L-1 behavior) and the case measured admission, not redaction precision. The
# marker restores the declared intent: benign comma prose IS stored, unredacted.
A5_RECORD = "Please remember for the open studio: we will fire mugs, bowls, plates, and tiles in one batch."

P1_RECORD = "Please remember: the life vest stows under the marina bench."
PRESS_FILLER = (
    "pallets of uncut cover stock line the corridor and the ventilation ducts hum "
    "above the guillotine benches while the apprentices tally the spoilage sheets "
    "at the counting desk "
) * 7
P2_RECORD = "Please remember: " + PRESS_FILLER + "the proof press jams near the feeder gate."
P3_RECORD = "Please remember: the studio kiln does not fire porcelain after sunset."
P4_RECORD = "Please remember: whenever the storm flag flies, the fuel dock must close."
P5_RECORD = "Please remember: the harbormaster keeps the spare winch key in the blue tin."


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


# ---- Contract A: phrase-secret formatting + public declaration ---------------

def test_a1_comma_list_seed_phrase_masked_end_to_end(fresh_profile):
    status = _store(fresh_profile, "fa2-seed", A1_RECORD)
    assert status["secret_redacted"] is True, status
    ctx = _context(fresh_profile, "fa2-seed", "What is my seed phrase?")
    assert "[redacted]" in ctx, ctx
    for word in SEED_WORDS:
        assert not re.search(rf"\b{word}\b", ctx.lower()), (word, ctx)


def test_a2_curly_quoted_backup_words_masked_end_to_end(fresh_profile):
    status = _store(fresh_profile, "fa2-backup", A2_RECORD)
    assert status["secret_redacted"] is True, status
    ctx = _context(fresh_profile, "fa2-backup", "What are my backup words?")
    assert "[redacted]" in ctx, ctx
    for word in SEED_WORDS:
        assert not re.search(rf"\b{word}\b", ctx.lower()), (word, ctx)


def test_a3_well_known_mnemonic_preserved_and_retrievable(fresh_profile):
    status = _store(fresh_profile, "fa2-mnemonic", A3_RECORD)
    assert status["status"] == "stored", status
    assert "secret_redacted" not in status, status
    ctx = _context(fresh_profile, "fa2-mnemonic", "What is the glazing mnemonic?")
    assert "slow heat, even cool" in ctx, ctx


def test_a4_public_qualifier_cannot_override_recovery_seed(fresh_profile):
    status = _store(fresh_profile, "fa2-publicseed", A4_RECORD)
    assert status["secret_redacted"] is True, status
    ctx = _context(fresh_profile, "fa2-publicseed", "What is the recovery seed?")
    assert "[redacted]" in ctx, ctx
    for word in SEED_WORDS:
        assert not re.search(rf"\b{word}\b", ctx.lower()), (word, ctx)


def test_a5_benign_comma_prose_untouched(fresh_profile):
    status = _store(fresh_profile, "fa2-benign", A5_RECORD)
    assert status["status"] == "stored", status
    assert "secret_redacted" not in status, status


# ---- Contract P: morphological recall incl. long-window selection ------------

def test_p1_plural_life_vests_recall_marina_bench(fresh_profile):
    chat = "fa2-marina"
    assert _store(fresh_profile, chat, P1_RECORD)["status"] == "stored"
    ctx = _context(fresh_profile, chat, "Where are the life vests?")
    assert "marina bench" in ctx, ctx


def test_p2_long_sentence_plural_presses_recall_feeder_gate(fresh_profile):
    chat = "fa2-press"
    assert _store(fresh_profile, chat, P2_RECORD)["status"] == "stored"
    ctx = _context(fresh_profile, chat, "Where do the proof presses jam?")
    assert "feeder gate" in ctx, ctx
    # bounded delivery: the 700+ char preamble must not ride along wholesale
    assert "pallets of uncut cover stock line the corridor and the ventilation ducts hum above" \
        not in ctx, ctx


def test_p3_negated_kiln_fact_survives_morphological_question(fresh_profile):
    chat = "fa2-kiln"
    assert _store(fresh_profile, chat, P3_RECORD)["status"] == "stored"
    ctx = _context(fresh_profile, chat, "Can the studio kilns fire porcelain after sunset?")
    assert "does not fire porcelain after sunset" in ctx.lower(), ctx


def test_p4_condition_and_obligation_with_plural_mismatch(fresh_profile):
    chat = "fa2-storm"
    assert _store(fresh_profile, chat, P4_RECORD)["status"] == "stored"
    ctx = _context(fresh_profile, chat, "What must happen when the storm flags fly?")
    assert "the fuel dock must close" in ctx, ctx


def test_p5_singular_key_recalled_through_rephrased_question(fresh_profile):
    chat = "fa2-key"
    assert _store(fresh_profile, chat, P5_RECORD)["status"] == "stored"
    ctx = _context(fresh_profile, chat, "Where are the harbormaster's spare winch keys kept?")
    assert "blue tin" in ctx, ctx


def test_n1_unrelated_question_gets_no_context(fresh_profile):
    chat = "fa2-marina"
    _store(fresh_profile, chat, P1_RECORD)
    ctx = _context(fresh_profile, chat, "Who tuned the piano?")
    assert ctx == "", ctx
