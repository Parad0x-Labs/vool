"""pa_beta_gate — exact-value memory (deterministic).

The 98% exact-value gate: decimals, .null names, wallet prefixes, dates, paths,
caps must recall char-for-char. They survive via the char-exact L3 layer
(core.vool_memory node_store / node_search_hybrid), NOT the lossy dialogue goal
snapshot. Each planted fact in _pa_data.PLANTED_FACTS is a distinct assertion; the
suite also covers restart survival, model-switch (agent-id) survival + isolation,
forgetting, and latest-instruction-overrides-older at the recall layer.
"""
from __future__ import annotations

import math
import tempfile
import time
from pathlib import Path

import pytest

from core.fact_extractor import stable_text_embedding
from core.vool_memory import VoolMemory
from tests.pa_beta_gate._pa_data import PLANTED_FACTS, VALUE_KINDS

pytestmark = [pytest.mark.pa_beta]


def _store(mem, statement, needle, query, *, ts=1_000_000.0):
    mem.node_store(
        content=statement,
        keywords=[*query.split(), needle.lower()],
        tags=["user"],
        context_description="planted",
        embedding=stable_text_embedding(statement),
        timestamp=ts,
    )


def _recall(mem, query):
    hits = mem.node_search_hybrid(query, stable_text_embedding(query), top_k=3, min_score=0.0)
    return hits[0][0].content if hits else ""


# ---------------------------------------------------------------------------
# 1. Every planted exact value recalls char-for-char (the 98% gate)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("fact", PLANTED_FACTS, ids=lambda f: f"{f.kind}-{f.needle}")
def test_planted_value_recalls_char_exact(fact):
    with tempfile.TemporaryDirectory() as tmp:
        mem = VoolMemory(agent_id="exact", db_path=str(Path(tmp) / "m.db"))
        _store(mem, fact.statement, fact.needle, fact.query)
        got = _recall(mem, fact.query)
        mem.close()
    assert fact.needle in got, f"{fact.kind} value {fact.needle!r} lost — recalled {got!r}"


def test_all_value_kinds_are_covered():
    covered = {f.kind for f in PLANTED_FACTS}
    missing = [k for k in VALUE_KINDS if k not in covered]
    assert not missing, f"exact-value corpus is missing kinds: {missing}"


# ---------------------------------------------------------------------------
# 2. Exact values survive a restart (reopen the same store)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("fact", PLANTED_FACTS, ids=lambda f: f"{f.kind}-{f.needle}")
def test_planted_value_survives_restart(fact):
    with tempfile.TemporaryDirectory() as tmp:
        db = str(Path(tmp) / "m.db")
        mem = VoolMemory(agent_id="restart", db_path=db)
        _store(mem, fact.statement, fact.needle, fact.query)
        mem.close()  # simulate process shutdown

        reopened = VoolMemory(agent_id="restart", db_path=db)
        got = _recall(reopened, fact.query)
        reopened.close()
    assert fact.needle in got, f"{fact.kind} value {fact.needle!r} lost across restart — recalled {got!r}"


# ---------------------------------------------------------------------------
# 3. Latest instruction overrides older — the two mechanisms and their boundary
# ---------------------------------------------------------------------------

def _unit(*xs):
    mag = math.sqrt(sum(x * x for x in xs)) or 1.0
    return [x / mag for x in xs]


def test_latest_value_wins_via_temporal_collapse():
    """When a later statement is about the same slot (near-identical embedding) with
    a real time gap, temporal collapse surfaces the NEW value and drops the old —
    the mechanism behind 'latest instruction overrides older'. Recency decay is
    relative to real time, so use time.time()-anchored timestamps."""
    with tempfile.TemporaryDirectory() as tmp:
        mem = VoolMemory(agent_id="collapse", db_path=str(Path(tmp) / "m.db"))
        now = time.time()
        mem.node_store(content="My spend cap is 0.037 SOL.", keywords=["spend", "cap"],
                       tags=["user"], context_description="p", embedding=_unit(1.0, 0.0, 0.0),
                       timestamp=now - 21 * 86400)
        mem.node_store(content="Update: my spend cap is now 0.088 SOL.", keywords=["spend", "cap", "update"],
                       tags=["user"], context_description="p", embedding=_unit(0.97, 0.24, 0.0),
                       timestamp=now - 3 * 86400)
        hits = mem.node_search(_unit(0.99, 0.1, 0.0), top_k=3, min_score=0.0)
        mem.close()
    top = hits[0][0].content
    assert "0.088" in top and "0.037" not in top


def test_dissimilar_slot_values_are_both_kept_field_override_not_modeled():
    """Boundary / canary. Two DIFFERENT exact values that are not near-identical
    embeddings are both retained by L3 — there is no field-level 'newer cap wins'
    override at the value layer (audit missing-item). Topic-level override lives at
    the dialogue goal; exact-value slot override needs an impl (typed slots)."""
    with tempfile.TemporaryDirectory() as tmp:
        mem = VoolMemory(agent_id="both-kept", db_path=str(Path(tmp) / "m.db"))
        _store(mem, "Register alice.null for the project.", "alice.null", "project domain", ts=1_000.0)
        _store(mem, "Change of plan, register beta7.null instead.", "beta7.null", "project domain", ts=2_000.0)
        n = mem.node_count()
        mem.close()
    assert n == 2  # both kept; no automatic supersession of the old value


# ---------------------------------------------------------------------------
# 4. Model-switch survival + per-agent isolation
# ---------------------------------------------------------------------------

def test_exact_values_survive_a_model_switch_and_are_agent_isolated():
    with tempfile.TemporaryDirectory() as tmp:
        db = str(Path(tmp) / "m.db")
        first = VoolMemory(agent_id="vool_chat", db_path=db)
        _store(first, "My launch cap is 0.037 SOL for alice.null.", "0.037", "launch cap")
        first.close()

        # a model switch is a new session over the SAME agent memory -> still there, still exact
        after_switch = VoolMemory(agent_id="vool_chat", db_path=db)
        got = _recall(after_switch, "launch cap")
        assert "0.037" in got and "alice.null" in got
        after_switch.close()

        # a different agent id is isolated
        other = VoolMemory(agent_id="other_agent", db_path=db)
        assert other.node_count() == 0
        other.close()


# ---------------------------------------------------------------------------
# 5. Forgetting: a deleted value is not recalled, even after restart
# ---------------------------------------------------------------------------

def test_forgotten_value_is_not_recalled_after_restart():
    with tempfile.TemporaryDirectory() as tmp:
        db = str(Path(tmp) / "m.db")
        mem = VoolMemory(agent_id="forget", db_path=db)
        node = mem.node_store(
            content="The old treasury prefix was 28hxX, but forget it.",
            keywords=["treasury", "prefix", "28hxx"],
            tags=["user"], context_description="p",
            embedding=stable_text_embedding("old treasury prefix 28hxX"), timestamp=1_000.0,
        )
        mem.node_invalidate(node.node_id)
        mem.close()

        reopened = VoolMemory(agent_id="forget", db_path=db)
        hits = reopened.node_search_hybrid(
            "treasury prefix", stable_text_embedding("treasury prefix"), top_k=3, min_score=0.0
        )
        reopened.close()
    assert all("28hxX" not in n.content for n, _ in hits)  # forgotten value gone
