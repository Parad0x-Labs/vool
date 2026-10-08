"""q90-lifecycle — aggregate-shaped questions keep BOTH cross-chat operands.

Measured on the integrated head (F13-13): "Combining both deliveries,
what's the tally ..." classified as shape=single because the aggregate
detector lacked tally/combining vocabulary — the temporal contract then ran
its ordinary supersession path, ruled the granted chat's May operand
superseded by the reading chat's July record, and the capsule packed only
one operand (no derived total). One detector class fixes the chain:
multi_record mode keeps both operands ("operand-kept"), the merge packs the
granted + own spans, composition mints the arithmetic over packed records
only.

Fresh domain (orchard cider press), distinct from the corpus bell-foundry:
positive tally-with-grant, positive combining-phrasing, and a negative
control that a current-intent single-value question still keeps the strict
law (the detector must not widen into recency-sensitive packing).
"""
from __future__ import annotations

import pytest

import core.context_retrieval as cr
from core.context_namespace import (
    ensure_chat_namespace,
    grant_context_import,
)
from core.memory.entries import resolve_memory_access_policy
from core.runtime_paths import configure_runtime_home

try:
    from tests.test_fresh_acceptance_memrepair2b_20260927 import (
        fresh_profile,
    )
except ImportError:  # pragma: no cover
    fresh_profile = None

pytestmark = pytest.mark.usefixtures("fresh_profile")


def _policy(chat: str):
    ensure_chat_namespace(chat, grant_current_receipts=False)
    return resolve_memory_access_policy(chat_id=chat)


def _store(home: str, chat: str, user_text: str, assistant_text: str):
    return cr.store_turn(
        chat,
        user_text,
        assistant_text,
        access_policy=_policy(chat),
        source_context={"chat_id": chat, "runtime_home": home},
    )


def _capsule(home: str, chat: str, query: str) -> str:
    out = cr.inject_retrieved(
        chat,
        query,
        [{"role": "user", "content": query}],
        access_policy=_policy(chat),
        source_context={"chat_id": chat, "runtime_home": home},
        env={"VOOL_CONTEXT_CAPSULE_V2": "1"},
    )
    return next(
        (
            str(m.get("content") or "")
            for m in out
            if "retrieved_context" in str(m.get("content") or "")
        ),
        "",
    )


@pytest.fixture()
def press_chats(fresh_profile):
    home = fresh_profile
    _store(
        home,
        "q90-press-mill",
        "We pressed 90 litres of russet cider at the old mill in October.",
        "90 litres of russet cider in October - logged.",
    )
    _store(
        home,
        "q90-press-yard",
        "The yard crew added 55 litres more of the russet cider in December.",
        "55 more litres of russet cider in December - noted.",
    )
    return home


def test_tally_with_grant_packs_both_operands_and_total(press_chats):
    home = press_chats
    grant_context_import("q90-press-yard", scope="chat", source_id="chat:q90-press-mill")
    capsule = _capsule(
        home,
        "q90-press-yard",
        "Combining both pressings, what is the tally of russet cider this season?",
    )
    assert "90" in capsule and "55" in capsule, {
        "capsule": capsule,
        "note": "both cross-chat operands must pack under the grant",
    }
    assert "145" in capsule, {
        "capsule": capsule,
        "note": "the derived total must be minted over the packed operands",
    }


def test_detector_shapes():
    assert cr._query_shape(
        "Combining both pressings, what is the tally of russet cider?"
    ) == "aggregate"
    assert cr._query_shape("what's the tally of new bells for the ring?") == "aggregate"
    assert cr._query_shape("how much cider in total did we press?") == "aggregate"
    # negative control: current-intent single-value questions stay single
    assert cr._query_shape("how much cider do we have now?") == "single"
    assert cr._query_shape("what is the pressing schedule?") == "single"


def test_current_intent_keeps_strict_law_with_grant(press_chats):
    """The widened aggregate words must not make a current-intent question
    pack superseded values: a plain 'how much lately' keeps the strict law
    (the December record answers; October rides only if the law admits it)."""
    home = press_chats
    grant_context_import("q90-press-yard", scope="chat", source_id="chat:q90-press-mill")
    capsule = _capsule(home, "q90-press-yard", "how much russet cider do we have lately?")
    assert capsule is not None  # strict law applies; no derived-total minting
    assert "145" not in capsule, {
        "capsule": capsule,
        "note": "no arithmetic may be minted for a non-aggregate question",
    }
