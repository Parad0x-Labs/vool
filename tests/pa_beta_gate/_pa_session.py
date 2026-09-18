"""N-turn session builders for the pa_beta_gate continuity/memory matrix.

Not a test module. Two builders back the (value-kind x session-length x pattern)
matrix so 30/60/100-turn coverage is reached through genuinely distinct cases:

  - build_l3_session: node_stores N distractor turns plus one planted fact at a
    controlled position, into a real VoolMemory (char-exact L3). Deterministic —
    stable_text_embedding, no Ollama.
  - build_dialogue_session: drives the real dialogue pipeline (adapt_user_input +
    append_conversation_event) for the topic-continuity axis.
"""
from __future__ import annotations

from core.fact_extractor import stable_text_embedding
from tests.pa_beta_gate._pa_data import distractors

_OPENCLAW = {"surface": "openclaw", "platform": "openclaw"}

_POSITIONS = ("start", "quarter", "mid", "end")


def _position_index(n_turns: int, position: str) -> int:
    return {
        "start": 0,
        "quarter": n_turns // 4,
        "mid": n_turns // 2,
        "end": n_turns,
    }.get(position, 0)


def build_l3_session(mem, n_turns: int, plant, *, needle_position: str = "start") -> None:
    """Store n_turns distractor nodes plus `plant` (a PlantedFact) at needle_position.
    Deterministic increasing timestamps; distractors never share the needle vocabulary."""
    noise = distractors(n_turns)
    idx = _position_index(n_turns, needle_position)
    ts = 1_000_000.0

    def _store(content, keywords, tag, ctx):
        nonlocal ts
        mem.node_store(content=content, keywords=keywords, tags=[tag],
                       context_description=ctx, embedding=stable_text_embedding(content), timestamp=ts)
        ts += 1.0

    for i, text in enumerate(noise[:idx]):
        _store(text, text.lower().split(), "noise", f"noise-{i}")
    _store(plant.statement, [*plant.query.split(), plant.needle.lower()], "user", "planted")
    for i, text in enumerate(noise[idx:]):
        _store(text, text.lower().split(), "noise", f"noise-{idx + i}")


def recall_top(mem, query: str):
    """Return (top_content, [all_contents]) for a hybrid search of `query`."""
    hits = mem.node_search_hybrid(query, stable_text_embedding(query), top_k=3, min_score=0.0)
    return (hits[0][0].content if hits else ""), [h[0].content for h in hits]


def build_dialogue_session(session_id: str, *, goal: str, goal_reply: str,
                           n_followups: int, follow_up: str = "ok do that") -> None:
    """Drive the real dialogue pipeline: plant a goal turn, then n_followups neutral
    acknowledgements that should keep the topic anchor."""
    from core.human_input_adapter import adapt_user_input
    from core.persistent_memory import append_conversation_event

    adapt_user_input(goal, session_id=session_id)
    append_conversation_event(session_id=session_id, user_input=goal, assistant_output=goal_reply,
                              source_context=_OPENCLAW)
    for _ in range(n_followups):
        adapt_user_input(follow_up, session_id=session_id)
