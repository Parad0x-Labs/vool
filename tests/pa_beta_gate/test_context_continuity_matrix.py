"""pa_beta_gate — context-continuity matrix (deterministic, 100 cases).

Genuinely distinct assertions from a (value-kind x session-length x pattern) matrix
over the real corpus — not trivial variants. Sessions are 30/60/100 turns. Split:

  A  exact value survives a full session, any position     7 kinds x 3 len x 2 pos = 42
  B  needle-position independence                          1 kind  x 3 len x 4 pos = 12
  G  restart mid-session, char-exact                       7 kinds x 2 len         = 14
  C  dialogue topic anchor holds over neutral follow-ups   3 len x 3 patterns      =  9
  D  latest instruction overrides older (dialogue layer)   5 goal pairs x 2        = 10
  E  goal-normalizer corruption vs L3 char-exact boundary                          =  7
  F  store_turn -> inject_retrieved round-trip + no over-injection                 =  6

Exact values are recalled from the char-exact L3 layer; topic continuity from the
dialogue layer; the goal-normalizer corruption is pinned as the boundary between
the two. Everything is model-free (stable_text_embedding / hash-BoW).
"""
from __future__ import annotations

import tempfile
import uuid
from pathlib import Path

import pytest

from core.context_namespace import ensure_chat_namespace
from core.memory.entries import resolve_memory_access_policy
from core.vool_memory import VoolMemory
from tests.pa_beta_gate._pa_data import PLANTED_FACTS, VALUE_KINDS
from tests.pa_beta_gate._pa_session import build_dialogue_session, build_l3_session, recall_top

pytestmark = [pytest.mark.pa_beta]

LENGTHS = (30, 60, 100)


def _one_per_kind():
    seen = {}
    for f in PLANTED_FACTS:
        if f.kind in VALUE_KINDS and f.kind not in seen:
            seen[f.kind] = f
    return seen


KIND_FACT = _one_per_kind()


def _mem(tmp, agent="cont"):
    return VoolMemory(agent_id=agent, db_path=str(Path(tmp) / "m.db"))


# ---------------------------------------------------------------------------
# A. Exact value survives a full session, regardless of position  (42)
# ---------------------------------------------------------------------------

A_CASES = [(k, n, pos) for k in VALUE_KINDS for n in LENGTHS for pos in ("start", "end")]


@pytest.mark.parametrize("kind,length,position", A_CASES)
def test_exact_value_survives_session(kind, length, position):
    fact = KIND_FACT[kind]
    with tempfile.TemporaryDirectory() as tmp:
        mem = _mem(tmp)
        build_l3_session(mem, length, fact, needle_position=position)
        _top, contents = recall_top(mem, fact.query)
        mem.close()
    assert any(fact.needle in c for c in contents), (
        f"{kind} needle {fact.needle!r} lost in a {length}-turn session ({position})"
    )


# ---------------------------------------------------------------------------
# B. Needle-position independence — ranks top regardless of where it sits  (12)
# ---------------------------------------------------------------------------

B_CASES = [(n, pos) for n in LENGTHS for pos in ("start", "quarter", "mid", "end")]


@pytest.mark.parametrize("length,position", B_CASES)
def test_needle_position_independence(length, position):
    fact = KIND_FACT["decimal"]
    with tempfile.TemporaryDirectory() as tmp:
        mem = _mem(tmp)
        build_l3_session(mem, length, fact, needle_position=position)
        top, _contents = recall_top(mem, fact.query)
        mem.close()
    assert fact.needle in top, f"needle at {position} of a {length}-turn session did not rank top"


# ---------------------------------------------------------------------------
# G. Restart mid-session, char-exact  (14)
# ---------------------------------------------------------------------------

G_CASES = [(k, n) for k in VALUE_KINDS for n in (30, 60)]


@pytest.mark.parametrize("kind,length", G_CASES)
def test_exact_value_survives_restart_mid_session(kind, length):
    fact = KIND_FACT[kind]
    with tempfile.TemporaryDirectory() as tmp:
        db = str(Path(tmp) / "m.db")
        mem = VoolMemory(agent_id="restart-cont", db_path=db)
        build_l3_session(mem, length, fact, needle_position="mid")
        mem.close()  # restart

        reopened = VoolMemory(agent_id="restart-cont", db_path=db)
        _top, contents = recall_top(reopened, fact.query)
        reopened.close()
    assert any(fact.needle in c for c in contents), (
        f"{kind} needle {fact.needle!r} lost across a restart in a {length}-turn session"
    )


# ---------------------------------------------------------------------------
# C. Dialogue topic anchor holds over neutral follow-ups  (9)
# ---------------------------------------------------------------------------

from storage.dialogue_memory import get_dialogue_session

C_CASES = [(n, fu) for n in LENGTHS for fu in ("ok do that", "go on", "keep going")]


def _sid(label):
    return f"openclaw:{label}:{uuid.uuid4().hex}"


@pytest.mark.parametrize("length,follow_up", C_CASES)
def test_topic_anchor_holds_over_neutral_followups(length, follow_up):
    sid = _sid("anchor")
    build_dialogue_session(
        sid, goal="Help me design the deployment pipeline for the Telegram bot.",
        goal_reply="I'll draft the pipeline stages next.", n_followups=length, follow_up=follow_up,
    )
    s = get_dialogue_session(sid)
    assert "telegram bot" in str(s.get("last_subject") or "").lower(), (
        f"topic anchor lost after {length} '{follow_up}' turns: {s.get('last_subject')!r}"
    )


# ---------------------------------------------------------------------------
# D. Latest instruction overrides older, at the dialogue layer  (10)
# ---------------------------------------------------------------------------

# (first goal, distinctive old word, second goal, distinctive new word)
D_PAIRS = [
    ("Help me choose a database for the app.", "database", "Now help me write the Dockerfile instead.", "dockerfile"),
    ("Let's design the caching layer.", "caching", "Actually, plan the launch marketing instead.", "marketing"),
    ("Help me set up CI for the repo.", "repo", "Switch to writing the API documentation now.", "documentation"),
    ("Plan the database schema for me.", "schema", "Forget that, help me debug the login flow instead.", "login"),
    ("Draft the onboarding email copy.", "onboarding", "Instead, outline the pricing page for me.", "pricing"),
]


def _drive_override(first_goal, second_goal):
    from core.human_input_adapter import adapt_user_input
    from core.persistent_memory import append_conversation_event

    sid = _sid("override")
    ctx = {"surface": "openclaw", "platform": "openclaw"}
    adapt_user_input(first_goal, session_id=sid)
    append_conversation_event(session_id=sid, user_input=first_goal, assistant_output="On it.", source_context=ctx)
    adapt_user_input(second_goal, session_id=sid)
    return str(get_dialogue_session(sid).get("current_user_goal") or "").lower()


@pytest.mark.parametrize("first_goal,old_word,second_goal,new_topic", D_PAIRS)
def test_latest_instruction_becomes_the_current_goal(first_goal, old_word, second_goal, new_topic):
    goal = _drive_override(first_goal, second_goal)
    assert new_topic in goal  # the new instruction is now the goal


@pytest.mark.parametrize("first_goal,old_word,second_goal,new_topic", D_PAIRS)
def test_superseded_instruction_is_dropped_from_the_goal(first_goal, old_word, second_goal, new_topic):
    goal = _drive_override(first_goal, second_goal)
    assert old_word not in goal  # the abandoned instruction no longer drives the turn


# ---------------------------------------------------------------------------
# E. Goal-normalizer corruption vs L3 char-exact — the boundary  (7)
# ---------------------------------------------------------------------------

# Per-kind: does the exact needle survive verbatim in the normalized dialogue goal?
# decimals and dotted names are split by the normalizer; hyphenated/alphanumeric/
# spaced tokens survive. Whatever the goal does, L3 is always char-exact.
# After the Codex Phase 2 F1 fix, the input normalizer protects exact-literal spans,
# so EVERY value kind now survives verbatim in the goal snapshot (decimals and dotted
# names used to be split; they are now preserved char-for-char).
E_EXPECT_IN_GOAL = {kind: True for kind in VALUE_KINDS}


@pytest.mark.parametrize("kind", VALUE_KINDS)
def test_goal_normalizer_preserves_exact_values(kind):
    from core.human_input_adapter import adapt_user_input

    fact = KIND_FACT[kind]
    sid = _sid("boundary")
    adapt_user_input(fact.statement, session_id=sid)
    goal = str(get_dialogue_session(sid).get("current_user_goal") or "")
    in_goal = fact.needle in goal

    assert in_goal is E_EXPECT_IN_GOAL[kind], (
        f"{kind} needle {fact.needle!r} goal-survival was {in_goal}, expected "
        f"{E_EXPECT_IN_GOAL[kind]}; goal={goal!r}"
    )

    # regardless of the goal path, L3 recalls the value char-exact
    with tempfile.TemporaryDirectory() as tmp:
        mem = _mem(tmp, agent="boundary")
        build_l3_session(mem, 10, fact, needle_position="start")
        _top, contents = recall_top(mem, fact.query)
        mem.close()
    assert any(fact.needle in c for c in contents)


# ---------------------------------------------------------------------------
# F. store_turn -> inject_retrieved round-trip + no over-injection  (6)
# ---------------------------------------------------------------------------

@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    # Capsule V2 = hybrid recall (semantic + BM25 + recency): exact tokens (ids,
    # dates, caps) survive retrieval far better than the legacy semantic-only path.
    monkeypatch.setenv("VOOL_CONTEXT_CAPSULE_V2", "1")
    from core import runtime_paths
    monkeypatch.setattr(runtime_paths, "_VOOL_HOME_OVERRIDE", None, raising=False)
    return tmp_path


F_FACTS = [KIND_FACT["date"], KIND_FACT["cap"], KIND_FACT["wallet_prefix"]]


@pytest.mark.parametrize("fact", F_FACTS, ids=lambda f: f.kind)
def test_store_turn_round_trip_resurfaces_fact(isolated_home, fact):
    from core.context_retrieval import inject_retrieved, store_turn

    sid = _sid("rt")
    ensure_chat_namespace(sid, grant_current_receipts=False)
    policy = resolve_memory_access_policy(chat_id=sid)
    store_turn(
        sid,
        f"Please remember for later: {fact.statement}",
        "Noted.",
        access_policy=policy,
    )
    # neutral transcript (no keyword overlap) so the fact is not deduped against it
    transcript = [{"role": "user", "content": "can you remind me of the important details?"}]
    out = inject_retrieved(
        sid,
        fact.query,
        transcript,
        access_policy=policy,
    )
    blob = " ".join(m.get("content", "") for m in out)
    assert fact.needle in blob, f"{fact.kind} fact did not resurface via inject_retrieved"


@pytest.mark.parametrize("fact", F_FACTS, ids=lambda f: f.kind)
def test_unrelated_query_does_not_over_inject(isolated_home, fact):
    from core.context_retrieval import inject_retrieved, store_turn

    sid = _sid("rt-neg")
    ensure_chat_namespace(sid, grant_current_receipts=False)
    policy = resolve_memory_access_policy(chat_id=sid)
    store_turn(
        sid,
        f"Please remember for later: {fact.statement}",
        "Noted.",
        access_policy=policy,
    )
    transcript = [{"role": "user", "content": "what is the boiling point of water?"}]
    out = inject_retrieved(
        sid,
        "boiling point of water in celsius",
        transcript,
        access_policy=policy,
    )
    blob = " ".join(m.get("content", "") for m in out)
    assert fact.needle not in blob, f"{fact.kind} fact over-injected on an unrelated query"
