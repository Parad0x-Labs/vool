"""q90-lifecycle — token forget matches separator/case variants of the token.

Measured on the integrated head with the real neural backend (F14-07,
full208-nomic-492d7412): forgetting "thermal pool" left the assistant's
hyphenated copy ("Every third Monday thermal-pool drain") serving in the
capsule — every forget-law layer (occurrence/node invalidation, log scrub,
derivative sweep, revocation-ledger gates) matched by plain substring, so a
hyphen/underscore/spacing variant of the same phrase survived all of them;
BM25 never surfaced it in the hash era, the semantic leg does.

The law: separators (whitespace, hyphen, underscore) and case are not
meaning — a forget must retire every variant of its token. Fresh domain
(lighthouse maintenance), distinct from the corpus's bathhouse case.
"""
from __future__ import annotations

import pytest

import core.context_retrieval as cr
from core.context_namespace import ensure_chat_namespace
from core.memory.entries import resolve_memory_access_policy
from core.runtime_paths import configure_runtime_home

try:
    from tests.test_fresh_acceptance_memrepair2b_20260927 import (  # noqa: F401
        fresh_profile,
    )
except ImportError:  # pragma: no cover
    fresh_profile = None

pytestmark = pytest.mark.usefixtures("fresh_profile")


TOKEN = "lamp room"
USER_TEXT = "The lighthouse lamp room gets repainted before every equinox inspection."
ASSISTANT_HYPHEN = "Equinox repaint of the lamp-room - noted."  # hyphen variant
ASSISTANT_PLAIN = "The lamp room repaint is on the winter list."


def _policy(chat: str):
    ensure_chat_namespace(chat, grant_current_receipts=False)
    return resolve_memory_access_policy(chat_id=chat)


def _capsule(home: str, chat: str, query: str) -> str:
    out = cr.inject_retrieved(
        chat,
        query,
        [{"role": "user", "content": query}],
        access_policy=_policy(chat),
        source_context={"chat_id": chat, "runtime_home": home},
        env={"VOOL_CONTEXT_CAPSULE_V2": "1"},
    )
    return "\n".join(
        str(m.get("content") or "")
        for m in out
        if "retrieved_context" in str(m.get("content") or "")
    )


def _seed(home: str, chat: str) -> None:
    policy = _policy(chat)
    cr.store_turn(
        chat,
        USER_TEXT,
        ASSISTANT_HYPHEN,
        access_policy=policy,
        source_context={"chat_id": chat, "runtime_home": home},
    )
    cr.store_turn(
        chat,
        "The keeper logs the paint stock separately.",
        ASSISTANT_PLAIN,
        access_policy=policy,
        source_context={"chat_id": chat, "runtime_home": home},
    )


def test_forget_matches_hyphenated_assistant_copy(fresh_profile):
    """The exact F14-07 shape in a fresh domain: after forgetting the token,
    no variant of it (and not its decisive value) may serve in the capsule."""
    home = fresh_profile
    chat = "q90-vt-light"
    _seed(home, chat)

    # sanity: before forget, a zero-lexical-anchor query with a neural-less
    # backend cannot bridge, so probe with a lexically anchored query instead
    pre = _capsule(home, chat, "When is the lamp area repainted?")
    assert "equinox" in pre or "repaint" in pre, {"pre": pre}

    cr.forget_session_memory(
        chat,
        TOKEN,
        access_policy=_policy(chat),
        source_context={"chat_id": chat, "runtime_home": home},
    )
    post = _capsule(home, chat, "What does the keeper log, and when is the lamp area repainted?")
    assert "lamp-room" not in post and "lamp room" not in post.lower(), {
        "post": post,
        "note": "hyphenated variant of the forgotten token must not serve",
    }
    assert "equinox" not in post, {
        "post": post,
        "note": "the deleted statement's decisive value must not ride a surviving variant",
    }
    # the same-chat unrelated fact keeps serving (preservation control,
    # anchored to the query so its absence can never be a ranking artifact)
    assert "paint stock" in post, {
        "post": post,
        "note": "unrelated same-chat content must survive the forget",
    }


def test_occurrence_invalidation_matches_separator_variants(fresh_profile):
    home = fresh_profile
    chat = "q90-vt-occ"
    _seed(home, chat)
    from core.vool_memory import VoolMemory

    mem = VoolMemory(runtime_home=home, agent_id="vool_chat")
    try:
        removed = mem.occurrence_invalidate_matching(TOKEN, chat_scope=chat)
        assert removed >= 2, {
            "removed": removed,
            "note": "plain + hyphenated variants must both be invalidated",
        }
        survivors = [
            str(row["body"])
            for row in mem._conn.execute(
                "SELECT body FROM source_occurrences WHERE chat_scope=? "
                "AND status='active' AND body != ''",
                (chat,),
            ).fetchall()
        ]
        assert not any("lamp" in b.lower() and "room" in b.lower() for b in survivors), {
            "survivors": survivors,
        }
        assert any("paint stock" in b.lower() for b in survivors), {
            "survivors": survivors,
            "note": "unrelated occurrence preserved",
        }
    finally:
        mem.close()


def test_revocation_gate_matches_separator_variants():
    tokens = (cr._normalize_forget_token("thermal pool"),)
    assert cr.text_carries_revoked_token("Every third Monday thermal-pool drain", tokens)
    assert cr.text_carries_revoked_token("the thermal_pool drain note", tokens)
    assert cr.text_carries_revoked_token("the  THERMAL   POOL  drains", tokens)
    assert not cr.text_carries_revoked_token("the thermal baths drain", tokens), {
        "note": "variant matching must not widen to different words",
    }


def test_foreign_chat_hyphenated_variant_preserved(fresh_profile):
    home = fresh_profile
    mine, foreign = "q90-vt-mine", "q90-vt-foreign"
    _seed(home, foreign)
    cr.forget_session_memory(
        mine,
        TOKEN,
        access_policy=_policy(mine),
        source_context={"chat_id": mine, "runtime_home": home},
    )
    from core.vool_memory import VoolMemory

    mem = VoolMemory(runtime_home=home, agent_id="vool_chat")
    try:
        rows = mem._conn.execute(
            "SELECT body FROM source_occurrences WHERE chat_scope=? AND status='active'",
            (foreign,),
        ).fetchall()
        assert any("lamp-room" in str(r["body"]) for r in rows), {
            "rows": [str(r["body"]) for r in rows],
            "note": "a foreign chat's hyphenated copy must never be touched",
        }
    finally:
        mem.close()
