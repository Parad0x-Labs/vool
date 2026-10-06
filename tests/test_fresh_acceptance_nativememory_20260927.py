"""FRESH ACCEPTANCE SET — native-memory-local-repair-20260927.

Frozen BEFORE first acceptance execution (sha256 recorded in
FRESH-ACCEPTANCE-FREEZE.json together with the candidate SHA). New wording,
new domains, new structures — not renamings of the original failures. Expected
outcomes are declared per case in the freeze manifest, independently of
implementation.

Contract P: a stored everyday fact must survive the native
store -> close -> fresh reopen -> query -> provider-boundary context path when
the question differs morphologically from the stored words (the repaired
lexical/stemming contract), with no-answer and negative-clause controls.

Contract S: an explicitly phrase-secret-labelled wordlist must never persist or
be disclosed verbatim (the repaired admission contract), while benign
discussion and public mnemonics keep their stored text.
"""
from __future__ import annotations

import pytest

import core.context_retrieval as cr
from core.context_namespace import ensure_chat_namespace
from core.memory.entries import resolve_memory_access_policy
from core.runtime_paths import configure_runtime_home

GARDEN_RECORD = (
    "Please remember the community garden rules:\n"
    "- Plot rentals: annual renewal in April.\n"
    "- Tool storage: the blue container.\n"
    "- Composting: only raw greens."
)
HARBOR_RECORD = "Please remember: baggage drop closes at six in the evening."
INSPECTOR_RECORD = (
    "Please remember that new inspectors must sign the ledger at the gatehouse."
)
BAKERY_RECORD = (
    "Please remember: the bakery delivered the cake order to the harbor office."
)
POOL_RECORD = (
    "Please remember: the pool does not admit guests without keycards."
)
S1_PHRASE = "larch juniper moss slate cobalt ember drift"
S1_RECORD = "Remember my wallet backup phrase: " + S1_PHRASE + "."
S2_PHRASE = "violet copper meadow thistle amber quarry ridge"
S2_RECORD = (
    "Please remember: the account recovery seed is " + S2_PHRASE + "."
)
S3_RECORD = (
    "Please remember: recovery phrases should never be typed into a web form."
)
S4_RECORD = "Please remember our astronomy mnemonic: Oh Be A Fine Girl Kiss Me."
S5_TOKEN = "sk-ANTH-NEWCANARY-x7q9Lm2Tq4Wz"
S5_RECORD = "Remember: the deploy token is " + S5_TOKEN + "."


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


def _store(home: str, chat: str, text: str):
    ensure_chat_namespace(chat, grant_current_receipts=False)
    policy = resolve_memory_access_policy(chat_id=chat)
    return cr.store_turn(
        chat, text, "Noted.", access_policy=policy,
        source_context={"chat_id": chat, "runtime_home": home},
    )


def _context(home: str, chat: str, query: str) -> str:
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


# ---- Contract P: morphological paraphrase recall -----------------------------

def test_p1_garden_tools_plural_question_delivers_storage_fact(fresh_profile):
    chat = "fa-garden"
    status = _store(fresh_profile, chat, GARDEN_RECORD)
    assert status["status"] == "stored", status
    ctx = _context(fresh_profile, chat, "Where are the garden tools stored?")
    assert "Tool storage: the blue container." in ctx, ctx
    assert "blue container" in ctx


def test_p2_baggage_drop_gerund_question_delivers_time(fresh_profile):
    chat = "fa-harbor"
    _store(fresh_profile, chat, HARBOR_RECORD)
    ctx = _context(fresh_profile, chat, "What time does baggage dropping close?")
    assert "six in the evening" in ctx, ctx


def test_p3_inspectors_question_delivers_full_obligation(fresh_profile):
    chat = "fa-inspectors"
    _store(fresh_profile, chat, INSPECTOR_RECORD)
    ctx = _context(fresh_profile, chat, "Where do inspectors sign in?")
    assert "ledger" in ctx and "gatehouse" in ctx, ctx
    assert "must sign the ledger at the gatehouse" in ctx, ctx


def test_p4_bakery_base_form_question_delivers_destination(fresh_profile):
    chat = "fa-bakery"
    _store(fresh_profile, chat, BAKERY_RECORD)
    ctx = _context(fresh_profile, chat, "Where did the bakery deliver the cake order?")
    assert "harbor office" in ctx, ctx


def test_p5_negative_clause_survives_paraphrased_question(fresh_profile):
    chat = "fa-pool"
    _store(fresh_profile, chat, POOL_RECORD)
    ctx = _context(fresh_profile, chat, "Can visitors enter without keycards?")
    assert "does not admit guests without keycards" in ctx.lower(), ctx


def test_p6_no_answer_question_yields_empty_context(fresh_profile):
    chat = "fa-garden"
    _store(fresh_profile, chat, GARDEN_RECORD)
    ctx = _context(fresh_profile, chat, "Who waters the orchids?")
    assert ctx == "", ctx


# ---- Contract S: labelled phrase secrets never persist or disclose ----------

def test_s1_wallet_backup_phrase_masked_end_to_end(fresh_profile):
    chat = "fa-wallet"
    status = _store(fresh_profile, chat, S1_RECORD)
    assert status["secret_redacted"] is True, status
    assert status["reason"] == "eligible_user_content_secret_redacted", status
    ctx = _context(fresh_profile, chat, "What is my wallet backup phrase?")
    assert S1_PHRASE not in ctx, ctx
    assert "[redacted]" in ctx, ctx
    import re as _re

    # AMENDED (disclosed in FRESH-ACCEPTANCE-FREEZE.json): first attempt used a
    # substring check and 'ember' matched inside the word "remember". The frozen
    # declaration is "no phrase WORD in context" — word-boundary check.
    for word in S1_PHRASE.split():
        assert not _re.search(rf"\b{word}\b", ctx.lower()), (word, ctx)


def test_s2_recovery_seed_with_is_connector_masked(fresh_profile):
    chat = "fa-account"
    status = _store(fresh_profile, chat, S2_RECORD)
    assert status.get("secret_redacted") is True, status
    ctx = _context(fresh_profile, chat, "What is the account recovery seed?")
    assert S2_PHRASE not in ctx, ctx
    assert "[redacted]" in ctx, ctx


def test_s3_benign_recovery_phrase_discussion_stored_verbatim(fresh_profile):
    chat = "fa-benign"
    status = _store(fresh_profile, chat, S3_RECORD)
    assert status["status"] == "stored", status
    assert "secret_redacted" not in status, status
    ctx = _context(fresh_profile, chat, "Where should recovery phrases be typed?")
    assert "never be typed into a web form" in ctx, ctx


def test_s4_public_astronomy_mnemonic_preserved(fresh_profile):
    chat = "fa-mnemonic"
    status = _store(fresh_profile, chat, S4_RECORD)
    assert status["status"] == "stored", status
    assert "secret_redacted" not in status, status
    ctx = _context(fresh_profile, chat, "What is our astronomy mnemonic?")
    assert "Oh Be A Fine Girl Kiss Me" in ctx, ctx


def test_s5_api_key_canary_new_value_still_masked(fresh_profile):
    chat = "fa-canary"
    status = _store(fresh_profile, chat, S5_RECORD)
    ctx = _context(fresh_profile, chat, "What is the deploy token?")
    assert S5_TOKEN not in ctx, ctx
    # AMENDED (disclosed in FRESH-ACCEPTANCE-FREEZE.json): the vendor-shaped
    # canary is masked by the api-key rule as "[redacted-api-key]", which the
    # first attempt's literal "[redacted]" check did not anticipate. The frozen
    # declaration is "masked end to end" — any redaction marker satisfies it.
    assert "[redacted" in ctx, ctx
