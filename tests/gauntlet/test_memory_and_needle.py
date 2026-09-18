"""Gauntlet — category 3 + 4: L3 memory retrieval and needle-in-haystack.

All deterministic, no live model:

  - NEEDLE HARNESS: seed a large distractor corpus plus one needle into
    VoolMemory (explicit tmp db, `stable_text_embedding` vectors so there is no
    embedding-backend dependency), then assert `node_search_hybrid` ranks the
    needle in the top-k. Includes a BM25/exact-token needle and a position-sweep
    (retrieval, unlike in-context recall, must be position-independent).
  - LIVE RETRIEVAL ROUND-TRIP: `store_turn` a high-importance fact, then
    `inject_retrieved` a related query and assert the fact resurfaces as injected
    context — and that an unrelated query injects nothing (over-injection is a
    contamination vector). Runs in an isolated VOOL_HOME; `embed()` falls back to
    the deterministic hash-BoW because conftest blocks live Ollama.

Real `num_ctx` on this box is 4096 (not 32k), so these test *retrieval* recall,
which is model-free — not in-window long-context recall, which is not.
"""
from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path
from unittest import mock

import pytest

from core.fact_extractor import stable_text_embedding
from core.vool_memory import VoolMemory

pytestmark = [pytest.mark.gauntlet]


NEEDLE = (
    "The active Web0 launch wallet policy is: maximum spend 0.037 SOL, and never "
    "auto-register domains without explicit operator approval."
)
NEEDLE_QUERY = "what is the active Web0 launch wallet spend policy and auto-register rule"

# A spread of unrelated distractor facts — none share the needle's distinctive
# web0/launch/wallet/policy/auto-register vocabulary.
DISTRACTOR_TOPICS = [
    "The kitchen renovation used oak cabinets and a quartz countertop.",
    "Photosynthesis converts carbon dioxide and water into glucose and oxygen.",
    "The train from Riga to Vilnius departs at 07:40 on weekdays.",
    "A regular hexagon has interior angles of 120 degrees each.",
    "The recipe calls for two cups of flour and a teaspoon of baking soda.",
    "Mount Kilimanjaro is the highest peak on the African continent.",
    "The quarterly board meeting reviewed hiring plans for the design team.",
    "Sourdough bread relies on a wild-yeast starter fermented over several days.",
    "The museum's new exhibit features impressionist landscape paintings.",
    "A leap year occurs every four years except for most century years.",
]


def _seed_corpus(mem: VoolMemory, *, distractors: int, needle_position: int) -> None:
    """Store `distractors` distractor nodes plus the needle at `needle_position`."""
    entries = []
    for i in range(distractors):
        topic = DISTRACTOR_TOPICS[i % len(DISTRACTOR_TOPICS)]
        entries.append(f"Note {i}: {topic}")
    entries.insert(max(0, min(needle_position, len(entries))), NEEDLE)
    for i, text in enumerate(entries):
        mem.node_store(
            content=text,
            keywords=text.lower().replace(",", " ").replace(".", " ").split(),
            tags=["gauntlet-corpus"],
            context_description=f"corpus item {i}",
            embedding=stable_text_embedding(text),
            timestamp=1_000_000.0 + i,  # deterministic, not Date.now()
        )


def _rank_of_needle(mem: VoolMemory, query: str, *, top_k: int = 5):
    hits = mem.node_search_hybrid(query, stable_text_embedding(query), top_k=top_k, min_score=0.0)
    texts = [node.content for node, _score in hits]
    return (texts.index(NEEDLE) if NEEDLE in texts else None), texts


# ---------------------------------------------------------------------------
# 1. Needle ranked in the top-k among many distractors
# ---------------------------------------------------------------------------

def test_needle_ranked_top_of_haystack():
    with tempfile.TemporaryDirectory() as tmp:
        mem = VoolMemory(agent_id="needle", db_path=str(Path(tmp) / "m.db"))
        _seed_corpus(mem, distractors=60, needle_position=30)
        rank, texts = _rank_of_needle(mem, NEEDLE_QUERY, top_k=5)
        mem.close()
    assert rank is not None, f"needle not in top-5; got {texts[:3]}"
    assert rank == 0, f"needle should rank #1 for a direct query; ranked #{rank + 1}"


# ---------------------------------------------------------------------------
# 2. BM25/exact-token needle (a unique code) among semantic distractors
# ---------------------------------------------------------------------------

def test_exact_token_needle_recalled_via_bm25_leg():
    unique_needle = "The one-time bootstrap code for the launch node is ZQ7X9K2M."
    with tempfile.TemporaryDirectory() as tmp:
        mem = VoolMemory(agent_id="bm25", db_path=str(Path(tmp) / "m.db"))
        for i, topic in enumerate(DISTRACTOR_TOPICS * 5):
            mem.node_store(
                content=f"Note {i}: {topic}",
                keywords=topic.lower().split(),
                tags=["c"],
                context_description=f"d{i}",
                embedding=stable_text_embedding(topic),
                timestamp=1_000_000.0 + i,
            )
        mem.node_store(
            content=unique_needle,
            keywords=["bootstrap", "code", "launch", "node", "zq7x9k2m"],
            tags=["c"],
            context_description="needle",
            embedding=stable_text_embedding(unique_needle),
            timestamp=1_000_500.0,
        )
        # Query by the exact rare token — the FTS5/BM25 leg should surface it.
        hits = mem.node_search_hybrid("ZQ7X9K2M bootstrap code", stable_text_embedding("ZQ7X9K2M bootstrap code"), top_k=3, min_score=0.0)
        mem.close()
    texts = [n.content for n, _ in hits]
    assert unique_needle in texts, f"exact-token needle not recalled; got {texts}"
    assert texts[0] == unique_needle


def test_exact_bm25_hit_survives_weak_semantic_similarity():
    with tempfile.TemporaryDirectory() as tmp:
        mem = VoolMemory(agent_id="bm25-threshold", db_path=str(Path(tmp) / "m.db"))
        mem.node_store(
            content="The operator node ID is 8829145.",
            keywords=["operator", "node", "id", "8829145"],
            tags=["user"],
            context_description="exact identifier",
            embedding=[1.0, 0.0, 0.0],
        )
        hits = mem.node_search_hybrid(
            "What operator node ID should I remember?",
            [0.0, 1.0, 0.0],
            top_k=3,
            min_score=0.42,
        )
        mem.close()

    assert hits
    assert hits[0][0].content == "The operator node ID is 8829145."
    assert hits[0][1] >= 0.42


# ---------------------------------------------------------------------------
# 3. Retrieval recall is position-independent (unlike in-context recall)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("position", [0, 30, 60])
def test_needle_recall_is_position_independent(position):
    with tempfile.TemporaryDirectory() as tmp:
        mem = VoolMemory(agent_id=f"pos{position}", db_path=str(Path(tmp) / "m.db"))
        _seed_corpus(mem, distractors=60, needle_position=position)
        rank, texts = _rank_of_needle(mem, NEEDLE_QUERY, top_k=5)
        mem.close()
    assert rank == 0, f"needle at position {position} did not rank #1 (got #{rank}); {texts[:2]}"


# ---------------------------------------------------------------------------
# 4. Live retrieval round-trip: store_turn -> inject_retrieved resurfaces the fact
# ---------------------------------------------------------------------------

@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    from core import runtime_paths

    monkeypatch.setattr(runtime_paths, "_VOOL_HOME_OVERRIDE", None, raising=False)
    return tmp_path


def _active_semantic_policy(session_id: str):
    from core.context_namespace import ensure_chat_namespace
    from core.memory.entries import resolve_memory_access_policy

    ensure_chat_namespace(
        session_id,
        grant_current_receipts=False,
    )
    return resolve_memory_access_policy(chat_id=session_id)


def test_store_turn_then_inject_retrieved_resurfaces_fact(isolated_home):
    from core.context_retrieval import inject_retrieved, store_turn

    policy = _active_semantic_policy("gsess-1")
    # High-importance fact ("remember ..." + a long code both push importance over
    # the store threshold) so store_turn actually persists it.
    fact = "Please remember: my Web0 launch wallet index is 8829145 and the cap is 0.037 SOL."
    store_turn(
        "gsess-1",
        fact,
        "Noted — I've stored your launch wallet index and cap.",
        access_policy=policy,
    )

    transcript = [{"role": "user", "content": "what is my Web0 launch wallet index?"}]
    out = inject_retrieved(
        "gsess-1",
        "Web0 launch wallet index",
        transcript,
        access_policy=policy,
    )

    blob = " ".join(m.get("content", "") for m in out)
    assert "8829145" in blob, f"planted fact did not resurface in retrieved context: {out}"
    # The injection sits ahead of the (still-present) last user turn.
    assert out[-1]["content"] == "what is my Web0 launch wallet index?"


def test_real_capsule_path_returns_same_session_operator_node_id(isolated_home, monkeypatch):
    from core import context_retrieval as retrieval
    from core.web.api.runtime import stable_openclaw_session_id

    monkeypatch.setattr(retrieval, "embed", lambda _text: [0.1, 0.2, 0.3])
    client_session = "operator-memory-client-session"
    history = [{"role": "user", "content": "Earlier local context is available in memory."}]
    session_id = stable_openclaw_session_id(
        body={"session_id": client_session, "model": "vool"},
        history=history,
        headers={},
    )
    policy = _active_semantic_policy(session_id)
    retrieval.store_turn(
        session_id,
        "Please remember this exact local fact: the operator node ID is 8829145.",
        "Noted.",
        access_policy=policy,
    )

    transcript = retrieval.inject_retrieved(
        session_id,
        "What operator node ID should I remember?",
        history,
        access_policy=policy,
        env={"VOOL_CONTEXT_CAPSULE_V2": "1"},
    )
    telemetry = retrieval.get_last_retrieval_telemetry()

    assert "8829145" in " ".join(item["content"] for item in transcript), telemetry
    assert telemetry["capsule_mode"] == "distilled"
    assert retrieval.capsule_exact_response(
        "What operator node ID should I remember?",
        telemetry,
        session_id=session_id,
    ) == "8829145"


def test_run_agent_one_shot_exact_memory_recall_is_pre_model(isolated_home, make_agent, monkeypatch):
    from core.context_retrieval import get_last_retrieval_telemetry, store_turn
    from core.web.api.runtime import RuntimeServices, run_agent

    monkeypatch.setenv("VOOL_CONTEXT_CAPSULE_V2", "1")
    monkeypatch.setattr("core.web.api.runtime.schedule_memory_extraction", lambda *args, **kwargs: None)
    session_id = "public:memory:operator-node"
    policy = _active_semantic_policy(session_id)
    store_turn(
        session_id,
        "Please remember this exact local fact: operator node ID is 8829145.",
        "Noted.",
        access_policy=policy,
    )

    agent = make_agent()
    agent.memory_router.resolve = mock.Mock(
        side_effect=AssertionError("exact same-session memory recall should not invoke the model"),
    )
    runtime = RuntimeServices(agent=agent, runtime_home=str(isolated_home), runtime_model_tag="qwen2.5:7b")

    result = run_agent(
        runtime,
        "What operator node ID should I remember?",
        session_id=session_id,
        source_context={
            "surface": "api",
            "platform": "api",
            "allow_remote_fetch": False,
            "runtime_home": str(isolated_home),
            "conversation_history": [{"role": "user", "content": "What operator node ID should I remember?"}],
            "client_conversation_history": [{"role": "user", "content": "What operator node ID should I remember?"}],
        },
        workspace_root_provider=lambda: str(isolated_home),
    )

    # The answer's BODY, plus the provenance line every response now carries
    # (`core/response_provenance.py`). Stripping it here is not a loosening: the footer states this
    # test's own subject in words -- a pre-model recall says `no model` -- so it is asserted too.
    from core.response_provenance import strip_provenance_footer

    assert strip_provenance_footer(result["response"]) == "8829145"
    assert "no model" in result["response"]
    assert result["route"] == "capsule_exact_pre_model"
    assert result["model_calls"] == 0
    assert result["web_calls"] == 0
    assert get_last_retrieval_telemetry()["capsule_mode"] == "distilled"
    agent.memory_router.resolve.assert_not_called()


def test_run_agent_one_shot_absent_memory_recall_is_pre_model(isolated_home, make_agent, monkeypatch):
    from core.context_retrieval import get_last_retrieval_telemetry, store_turn
    from core.web.api.runtime import RuntimeServices, run_agent

    monkeypatch.setenv("VOOL_CONTEXT_CAPSULE_V2", "1")
    monkeypatch.setattr("core.web.api.runtime.schedule_memory_extraction", lambda *args, **kwargs: None)
    other_policy = _active_semantic_policy("other-session")
    store_turn(
        "other-session",
        "Please remember this exact local fact: operator node ID is 8829145.",
        "Noted.",
        access_policy=other_policy,
    )
    session_id = "public:memory:absent-color"
    policy = _active_semantic_policy(session_id)
    store_turn(
        session_id,
        "Please remember this exact local fact: No launch gate color is defined.",
        "Noted.",
        access_policy=policy,
    )

    agent = make_agent()
    agent.memory_router.resolve = mock.Mock(
        side_effect=AssertionError("same-session absent exact recall should not invoke the model"),
    )
    runtime = RuntimeServices(agent=agent, runtime_home=str(isolated_home), runtime_model_tag="qwen2.5:7b")

    result = run_agent(
        runtime,
        "What is the launch gate color?",
        session_id=session_id,
        source_context={
            "surface": "api",
            "platform": "api",
            "allow_remote_fetch": False,
            "runtime_home": str(isolated_home),
            "conversation_history": [{"role": "user", "content": "What is the launch gate color?"}],
            "client_conversation_history": [{"role": "user", "content": "What is the launch gate color?"}],
        },
        workspace_root_provider=lambda: str(isolated_home),
    )

    response = result["response"].lower()
    assert "launch gate color" in response
    assert "not present" in response
    assert result["route"] == "capsule_exact_pre_model"
    assert result["model_calls"] == 0
    assert result["web_calls"] == 0
    assert get_last_retrieval_telemetry()["capsule_mode"] == "distilled"
    agent.memory_router.resolve.assert_not_called()


def test_irrelevant_query_injects_nothing(isolated_home):
    from core.context_retrieval import inject_retrieved, store_turn

    store_turn("gsess-2", "Please remember: my Web0 launch wallet index is 8829145.", "Noted.")

    transcript = [{"role": "user", "content": "what is the boiling point of water?"}]
    out = inject_retrieved("gsess-2", "boiling point of water in celsius", transcript)

    blob = " ".join(m.get("content", "") for m in out)
    assert "8829145" not in blob  # unrelated query must not drag in the wallet fact


def test_inject_retrieved_respects_token_budget(isolated_home):
    from core.context_retrieval import _MAX_INJECT_TOKENS, inject_retrieved, store_turn

    # Store several high-importance facts so retrieval has plenty to inject.
    for i in range(8):
        store_turn(
            f"gsess-b{i}",
            f"Please remember: config value number {i} is 900000{i} and must be kept for later reference.",
            "Noted.",
        )

    transcript = [{"role": "user", "content": "what are my stored config values?"}]
    out = inject_retrieved("gsess-b0", "stored config values", transcript)

    injected = " ".join(
        m.get("content", "") for m in out if m.get("content") != transcript[-1]["content"]
    )
    # Legacy injection cap is 350 tokens (~4 chars/token). Allow generous slack for
    # wrapper markup but assert it is bounded, not unbounded.
    assert len(injected) <= _MAX_INJECT_TOKENS * 4 + 400


# ---------------------------------------------------------------------------
# 5. Exact-value survival: L3 is char-exact where the dialogue goal is not
# ---------------------------------------------------------------------------

# Values the dialogue-goal normalizer corrupts (decimals, dotted names — see
# test_context_continuity's goal-normalizer canary) but the L3 store keeps verbatim.
_EXACT_FACT = (
    "Launch cap is exactly 0.037 SOL for alice.null; wallet prefix F6Fr2; deadline 2026-07-15."
)


def test_exact_values_survive_verbatim_through_l3_retrieval():
    with tempfile.TemporaryDirectory() as tmp:
        mem = VoolMemory(agent_id="exact", db_path=str(Path(tmp) / "m.db"))
        mem.node_store(
            content=_EXACT_FACT,
            keywords=["launch", "cap", "alice.null", "wallet", "deadline"],
            tags=["user"],
            context_description="planted exact values",
            embedding=stable_text_embedding(_EXACT_FACT),
            timestamp=1_000_000.0,
        )
        hits = mem.node_search_hybrid(
            "launch cap alice.null wallet deadline",
            stable_text_embedding("launch cap alice.null wallet deadline"),
            top_k=3,
            min_score=0.0,
        )
        mem.close()

    top = hits[0][0].content
    # Every exact token is preserved char-for-char — the opposite of the goal snapshot.
    assert "0.037 SOL" in top
    assert "alice.null" in top
    assert "F6Fr2" in top
    assert "2026-07-15" in top


def test_memory_survives_a_restart_char_exact():
    with tempfile.TemporaryDirectory() as tmp:
        db = str(Path(tmp) / "m.db")
        mem = VoolMemory(agent_id="restart", db_path=db)
        mem.node_store(
            content=_EXACT_FACT,
            keywords=["launch", "cap", "alice.null"],
            tags=["user"],
            context_description="planted",
            embedding=stable_text_embedding(_EXACT_FACT),
            timestamp=1_000_000.0,
        )
        mem.close()  # simulate a process shutdown

        reopened = VoolMemory(agent_id="restart", db_path=db)  # a restart on the same store
        count = reopened.node_count()
        hits = reopened.node_search_hybrid(
            "launch cap alice.null", stable_text_embedding("launch cap alice.null"), top_k=3, min_score=0.0
        )
        reopened.close()

    assert count == 1
    assert "0.037 SOL" in hits[0][0].content and "alice.null" in hits[0][0].content


def test_memory_is_keyed_by_agent_not_model_and_is_isolated():
    """A model switch does not lose memory: the store is keyed by agent_id, not by
    the active model. The same agent id still sees its nodes after a reopen; a
    different agent id sees nothing (per-agent isolation)."""
    with tempfile.TemporaryDirectory() as tmp:
        db = str(Path(tmp) / "m.db")
        first = VoolMemory(agent_id="vool_chat", db_path=db)
        first.node_store(
            content="Owner cap is 0.037 SOL.",
            keywords=["cap"],
            tags=["user"],
            context_description="p",
            embedding=stable_text_embedding("Owner cap is 0.037 SOL."),
            timestamp=1_000.0,
        )
        first.close()

        # A "model swap" is just a new session over the SAME agent memory -> still there.
        same_agent = VoolMemory(agent_id="vool_chat", db_path=db)
        assert same_agent.node_count() == 1
        same_agent.close()

        # A different agent id is isolated and sees nothing.
        other_agent = VoolMemory(agent_id="other_agent", db_path=db)
        assert other_agent.node_count() == 0
        other_agent.close()


def test_forgotten_memory_stays_gone_after_restart():
    with tempfile.TemporaryDirectory() as tmp:
        db = str(Path(tmp) / "m.db")
        mem = VoolMemory(agent_id="forget", db_path=db)
        node = mem.node_store(
            content="The abandoned plan was to ship on Monday.",
            keywords=["abandoned", "plan", "monday"],
            tags=["user"],
            context_description="p",
            embedding=stable_text_embedding("abandoned plan monday"),
            timestamp=1_000.0,
        )
        mem.node_invalidate(node.node_id)  # user forgets it
        mem.close()

        reopened = VoolMemory(agent_id="forget", db_path=db)
        hits = reopened.node_search_hybrid(
            "abandoned plan monday", stable_text_embedding("abandoned plan monday"), top_k=3, min_score=0.0
        )
        reopened.close()

    assert hits == []  # a forgotten memory is not recalled, even across a restart


# ---------------------------------------------------------------------------
# 6. Value-aware injection dedup: a needle whose VALUE is missing from the
#    lossy L2 summary survives, even when its keywords are already present.
# ---------------------------------------------------------------------------

class _StubNode:
    def __init__(self, content: str, *, session_id: str) -> None:
        scope = f"v2:{hashlib.sha256(session_id.encode('utf-8')).hexdigest()}"
        self.content = content
        self.tags = [f"session:{scope}"]
        self.context_description = f"session={scope} role=user scope=chat status=active"


class _StubMemory:
    """Returns one hit only through the requested chat's scoped query."""

    def __init__(self, content: str, *, session_id: str) -> None:
        self._session_id = session_id
        self._hit = (_StubNode(content, session_id=session_id), 0.9)

    def node_search(self, _q_vec, *, top_k=0, min_score=0.0, session_id=None):
        del top_k, min_score
        return [self._hit] if session_id == self._session_id else []

    def node_search_hybrid(
        self,
        _query,
        _q_vec,
        *,
        top_k=0,
        min_score=0.0,
        session_id=None,
    ):
        del top_k, min_score
        return [self._hit] if session_id == self._session_id else []

    def close(self) -> None:
        pass


def test_inject_relevant_keeps_value_needle_when_only_keywords_in_summary(monkeypatch):
    """The deepest live-memory fix: word-overlap dedup would drop 'database port 5433'
    because the L2 summary kept the words database+port, but the value 5433 (the whole
    point of the recall) is absent from context — so the node must still be injected."""
    from core import context_retrieval as retrieval

    monkeypatch.setattr(
        retrieval,
        "_open_memory",
        lambda: _StubMemory("database port 5433", session_id="s-live"),
    )
    transcript = [
        {"role": "system", "content": "Summary: discussed the staging postgres database port setup."},
        {"role": "user", "content": "what is the database port?"},
    ]

    out = retrieval._legacy_inject_retrieved("s-live", "database port", transcript)

    blob = " ".join(message["content"] for message in out)
    assert "5433" in blob, "value needle was deduped away despite its value being unseen"


def test_legacy_inject_retrieved_keeps_value_needle_absent_from_context(monkeypatch):
    """Mirror of the value-aware dedup in the live retrieval path (v2 off)."""
    from core import context_retrieval as retrieval

    monkeypatch.setattr(
        retrieval,
        "_open_memory",
        lambda: _StubMemory("database port 5433", session_id="s-legacy"),
    )
    transcript = [
        {"role": "user", "content": "earlier we set up the staging postgres database port"},
        {"role": "user", "content": "what is the database port again?"},
    ]

    out = retrieval._legacy_inject_retrieved("s-legacy", "database port", transcript)

    blob = " ".join(m.get("content", "") for m in out)
    assert "5433" in blob, "retrieval mirror deduped the value needle away"


def test_plain_fact_without_value_still_deduped_when_covered(monkeypatch):
    """Guard the guard: a node with NO distinctive value token is still deduped when its
    words are already covered — the fix must not disable dedup wholesale."""
    from core import context_retrieval as retrieval

    monkeypatch.setattr(
        retrieval,
        "_open_memory",
        lambda: _StubMemory("kitchen renovation oak cabinets", session_id="s-plain"),
    )
    transcript = [
        {"role": "user", "content": "we discussed the kitchen renovation with oak cabinets already"},
    ]

    out = retrieval._legacy_inject_retrieved("s-plain", "kitchen renovation", transcript)

    # No value token -> normal word-overlap dedup applies -> nothing new injected.
    assert all("<retrieved" not in m.get("content", "") for m in out)


# ---------------------------------------------------------------------------
# 7. Memory prompts are not hijacked by the machine-specs / profile-template routes
# ---------------------------------------------------------------------------

def test_memory_prompts_not_claimed_by_machine_spec_fast_path():
    from core.agent_runtime.fast_paths_machine import looks_like_supported_machine_read_request

    memory_prompts = [
        "what do you remember about our launch wallet?",
        "recall the database port i gave you earlier",
        "did you forget the operator node id?",
        "check your memory for the cap value",
        "what is in your memory about the deadline?",
    ]
    for prompt in memory_prompts:
        assert not looks_like_supported_machine_read_request(prompt), prompt

    # Genuine hardware reads still route to the machine fast path.
    assert looks_like_supported_machine_read_request("what are the system specs of this machine?")
    assert looks_like_supported_machine_read_request("how much ram does this machine have?")


def test_private_memory_recall_trigger_is_narrow():
    from core.web.api.runtime import _looks_like_private_memory_recall

    # Explicit profile recall still triggers.
    assert _looks_like_private_memory_recall("what is my name?")
    assert _looks_like_private_memory_recall("what do you remember about me?")
    assert _looks_like_private_memory_recall("what is my project codename?")

    # Bare field words inside an unrelated instruction/statement must NOT trigger.
    assert not _looks_like_private_memory_recall("set your response style to terse")
    assert not _looks_like_private_memory_recall("the project codename is Orion")
    assert not _looks_like_private_memory_recall("i have a preference for dark mode in the ui")


def test_private_memory_recall_answers_the_asked_field():
    from core.web.api.runtime import _format_private_memory_recall

    values = {"name": "Sam", "answer_style": "terse", "project_codename": "Orion", "constraints": ""}

    # Single-field questions get a scoped answer, not the whole-profile dump.
    assert _format_private_memory_recall(values, user_text="what is my name?") == "Your name is Sam."
    assert (
        _format_private_memory_recall(values, user_text="what's my project codename?")
        == "Your active project codename is Orion."
    )

    # Broad recall summarises everything without the old canned template prefix.
    broad = _format_private_memory_recall(values, user_text="what do you remember about me?")
    assert "Sam" in broad and "Orion" in broad
    assert not broad.startswith("Stored local profile:")
