"""q90-lifecycle — the semantic occurrence leg honors grants/project scopes.

Measured gap on the integrated head: the recall lane's meaning-level
occurrence leg ran CURRENT-CHAT-ONLY, so a granted (or project-granted)
chat's decisive fact with zero lexical anchor to the question — exactly the
F13-04 shape, "broadcast signal cut back for overnight work" vs
"transmitter drops to half power at 23:00" — stayed unreachable even on a
real neural backend: the BM25 granted leg cannot bridge it, and the access
grant must not deliver less than the same fact would get in its own chat.

Wiring proof with a deterministic fake neural backend (real semantic
quality is the nomic lane's separately-labeled measurement, never claimed
here). Controls: no grant stays isolated on the semantic leg too; the hash
lane stays lexical-only (no silent fallback, unchanged behavior).
"""
from __future__ import annotations

import pytest

import core.context_retrieval as cr
import core.embedding_service as embedding_service
from core.context_namespace import (
    ensure_chat_namespace,
    grant_context_import,
)
from core.memory.entries import resolve_memory_access_policy
from core.runtime_paths import configure_runtime_home
from core.vool_memory import VoolMemory

try:
    from tests.test_fresh_acceptance_memrepair2b_20260927 import (
        fresh_profile,
    )
except ImportError:  # pragma: no cover
    fresh_profile = None

pytestmark = pytest.mark.usefixtures("fresh_profile")


BACKEND_LABEL = "ollama:fake-granted-test#v1"
# synonym-family dimensions: body words and question words that share a
# family land on the same axis (that is what a semantic model provides)
SYNONYM_DIMS = {
    "transmitter": 0, "broadcast": 0, "signal": 0, "radio": 0,
    "power": 1, "cut": 1, "back": 1, "halved": 1,
    "maintenance": 2, "work": 2, "works": 2,
    "overnight": 3, "night": 3, "nocturnal": 3,
    "rota": 4, "crew": 4, "shift": 4,
}


def _fake_vec(text: str) -> list[float]:
    v = [0.0] * len({*SYNONYM_DIMS.values()})
    lowered = str(text).lower()
    for word, dim in SYNONYM_DIMS.items():
        if word in lowered:
            v[dim] = 1.0
    return v


def _capsule(home: str, chat: str, query: str) -> str:
    out = cr.inject_retrieved(
        chat,
        query,
        [{"role": "user", "content": query}],
        access_policy=resolve_memory_access_policy(chat_id=chat),
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


def _seed(home: str):
    ensure_chat_namespace("gs-radio-ops", grant_current_receipts=False)
    ensure_chat_namespace("gs-radio-crew", grant_current_receipts=False)
    ops_policy = resolve_memory_access_policy(chat_id="gs-radio-ops")
    cr.store_turn(
        "gs-radio-ops",
        "Kestrel FM's transmitter drops to half power at 23:00 every night for maintenance.",
        "23:00 half-power maintenance window logged.",
        access_policy=ops_policy,
        source_context={"chat_id": "gs-radio-ops", "runtime_home": home},
    )
    crew_policy = resolve_memory_access_policy(chat_id="gs-radio-crew")
    cr.store_turn(
        "gs-radio-crew",
        "Crew rota for the night shift is nearly done.",
        "Good - the rota is nearly settled.",
        access_policy=crew_policy,
        source_context={"chat_id": "gs-radio-crew", "runtime_home": home},
    )


QUESTION = "When does the broadcast signal get cut back for overnight work?"


def test_granted_scope_semantic_leg_bridges_zero_lexical_overlap(
    fresh_profile, monkeypatch
):
    home = fresh_profile
    _seed(home)

    def fake_embed_stamped(text, **kw):
        return _fake_vec(text), BACKEND_LABEL

    monkeypatch.setattr(embedding_service, "_best_embed_model", lambda: BACKEND_LABEL)
    monkeypatch.setattr(cr, "embed_stamped", fake_embed_stamped)
    monkeypatch.setattr(
        VoolMemory, "semantic_floor", staticmethod(lambda backend: 0.10)
    )

    grant_context_import("gs-radio-crew", scope="chat", source_id="chat:gs-radio-ops")
    capsule = _capsule(home, "gs-radio-crew", QUESTION)
    assert "23:00" in capsule, {
        "capsule": capsule,
        "note": "the granted chat's decisive fact must be reachable at "
                "meaning level, not only by lexical luck",
    }
    telemetry = cr.get_last_retrieval_telemetry()
    assert int(telemetry.get("evidence_semantic_count") or 0) >= 1, {
        "telemetry": telemetry,
    }
    assert telemetry.get("granted_session_scopes"), {
        "note": "grant participation must stay observable",
    }


def test_no_grant_semantic_leg_stays_isolated(fresh_profile, monkeypatch):
    home = fresh_profile
    _seed(home)

    def fake_embed_stamped(text, **kw):
        return _fake_vec(text), BACKEND_LABEL

    monkeypatch.setattr(embedding_service, "_best_embed_model", lambda: BACKEND_LABEL)
    monkeypatch.setattr(cr, "embed_stamped", fake_embed_stamped)
    monkeypatch.setattr(
        VoolMemory, "semantic_floor", staticmethod(lambda backend: 0.10)
    )

    capsule = _capsule(home, "gs-radio-crew", QUESTION)
    assert "23:00" not in capsule and "transmitter" not in capsule, {
        "capsule": capsule,
        "note": "without a grant the semantic leg must not leak the foreign chat",
    }


def test_hash_lane_granted_scope_stays_lexical_only(fresh_profile, monkeypatch):
    home = fresh_profile
    _seed(home)
    monkeypatch.setattr(embedding_service, "_best_embed_model", lambda: None)

    grant_context_import("gs-radio-crew", scope="chat", source_id="chat:gs-radio-ops")
    capsule = _capsule(home, "gs-radio-crew", QUESTION)
    assert "23:00" not in capsule, {
        "capsule": capsule,
        "note": "hash-BoW degradation stays lexical-only: no silent fake "
                "semantic bridging on the granted leg",
    }
    # (telemetry counters are not asserted here: the no_hits early-bail merges
    # the previous test's telemetry in this shared process, so cross-test
    # count checks would read stale state rather than this capsule's legs)
