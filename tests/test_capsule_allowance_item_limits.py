"""Retrieved evidence reaches the capsule in proportion to the declared allowance.

Measured defect (answer-path audit, 2026-10-02): the capsule's layer-1 legs
(BM25 occurrence leg, semantic occurrence leg), the node selection fold and the
distiller chunk ceiling were each fixed at 8 whatever evidence allowance the
caller declared. Under an 8192-token allowance capsules used ~630-1800 tokens
while in-scope evidence ranked 9th or lower was never offered to selection.
Three contracts, each checked end to end through the real capsule path:

* item limits follow the resolved allowance: clamp(target_tokens // 256, 8, 32),
  so the default 420-token allowance keeps the historical 8;
* adjacency belongs to the hit, not to the leg that found it: a semantic
  (cosine) hit and a node hit seed the same bounded neighbor rides a BM25 hit
  does;
* the strongest lexical hit delivers whole sentences, not clause fragments,
  unless widening would add a co-tenant value.

These are synthetic fixtures (no benchmark text, no benchmark literals).
"""
from __future__ import annotations

import math
import re
import zlib
from types import SimpleNamespace

import pytest

from core import context_retrieval as cr
from tests.test_p300_pref_retrieval_repair import _epoch

FIXTURE_BACKEND = "ollama:allowance-fixture-ax"


_DIMS = 64


def _axis(i: int) -> list[float]:
    vec = [0.0] * _DIMS
    vec[i] = 1.0
    return vec


def _make_embed(question: str, angles: dict[str, float]):
    """Deterministic geometry: the question is axis 0; a body carrying the
    k-th keyed word sits at that word's angle from axis 0, leaning into its
    OWN perpendicular axis (keyed bodies are mutually dissimilar, so result
    diversification cannot reorder them); any other text gets its own
    content-derived axis, orthogonal to the question."""
    keyed = list(angles.items())

    def embed_stamped(text, *_args, **_kwargs):
        raw = str(text or "")
        if raw.strip() == question.strip():
            return _axis(0), FIXTURE_BACKEND
        low = raw.lower()
        for k, (key, angle) in enumerate(keyed):
            if re.search(r"\b" + re.escape(key.lower()) + r"\b", low):
                vec = [0.0] * _DIMS
                vec[0] = math.cos(angle)
                vec[1 + k] = math.sin(angle)
                return vec, FIXTURE_BACKEND
        return _axis(32 + zlib.crc32(raw.encode("utf-8")) % 32), FIXTURE_BACKEND

    return embed_stamped


@pytest.fixture()
def capsule_env(tmp_path, monkeypatch):
    import core.embedding_service as embedding_service
    from core.runtime_paths import configure_runtime_home
    from storage.migrations import run_migrations

    home = tmp_path / "allowance-profile"
    home.mkdir()
    monkeypatch.setenv("VOOL_HOME", str(home))
    monkeypatch.setenv("VOOL_HOME", str(home))
    monkeypatch.setenv("VOOL_CONTEXT_CAPSULE_V2", "1")
    configure_runtime_home(home)
    monkeypatch.setattr(embedding_service, "_best_embed_model",
                        lambda: "allowance-fixture-model")
    run_migrations()

    def install(question, angles):
        monkeypatch.setattr(cr, "embed_stamped", _make_embed(question, angles))

    return home, install


@pytest.fixture()
def no_node_leg(monkeypatch):
    """Isolate the layer-1 OCCURRENCE lane: the node (semantic-index) leg
    returns nothing, so whatever reaches the capsule came through the
    occurrence legs and the evidence merge this file is about. Store
    admission decides which records also become nodes; that decision is
    not under test here and would otherwise mask the occurrence lane."""
    from core.vool_memory import VoolMemory

    monkeypatch.setattr(VoolMemory, "node_search_hybrid",
                        lambda self, *a, **k: [])


def _ingest(home, chat, turns):
    from core.context_namespace import ensure_chat_namespace
    from core.memory.entries import resolve_memory_access_policy

    ensure_chat_namespace(chat, grant_current_receipts=False)
    policy = resolve_memory_access_policy(chat_id=chat)
    for date, user_text, assistant_text in turns:
        result = cr.store_turn(
            chat, user_text, assistant_text, access_policy=policy,
            source_context={"chat_id": chat, "runtime_home": str(home),
                            "statement_at": _epoch(date)},
        )
        assert result["status"] in {"stored", "retained"}, result


def _quality_budget(question: str, target: int = 8192):
    from dataclasses import replace

    from core import context_capsule_v2 as capsule

    budget = capsule.resolve_budget(
        bucket="D", role="heavy_reasoning", kv_quant="fp16", gpu_served=True,
        output_reserve_tokens=2048,
        transcript_tokens=capsule.estimate_tokens(question),
        retrieval_ceiling_tokens=target, evidence_target_tokens=target,
    )
    return replace(budget, min_score=0.25)


def _capsule(home, chat, question, *, budget=None):
    from core.memory.entries import resolve_memory_access_policy

    policy = resolve_memory_access_policy(chat_id=chat)
    cr.reset_retrieval_telemetry()
    kwargs = {"budget": budget} if budget is not None else {}
    out = cr.inject_retrieved(
        chat, question, [{"role": "user", "content": question}],
        access_policy=policy,
        source_context={"chat_id": chat, "runtime_home": str(home),
                        "surface": "channel", "platform": "api"},
        **kwargs,
    )
    block = "\n".join(str(m.get("content") or "") for m in out
                      if "<retrieved_context>" in str(m.get("content") or ""))
    return block, dict(cr.get_last_retrieval_telemetry())


def _dates(n: int, start_day: int = 1):
    return [f"2025/03/{start_day + i:02d} (Sat) 10:00" for i in range(n)]


# ── item limits follow the allowance ─────────────────────────────────────────

@pytest.mark.parametrize("target, expected", [
    (0, 8), (420, 8), (2048, 8), (2303, 8), (2560, 10), (4096, 16),
    (8192, 32), (16384, 32), (100000, 32),
])
def test_item_limit_is_a_clamped_function_of_the_allowance(target, expected):
    assert cr._allowance_item_limit(target) == expected


def test_default_allowance_keeps_the_historical_depth():
    assert cr._allowance_item_limit(cr._CAPSULE_TARGET_TOKENS) == cr._V2_MAX_INJECT_ITEMS == 8


# Twelve same-chat statements the meaning leg ranks above the answer; none
# shares a word with the question, so only the semantic and node legs can
# reach any of them. The answer sits at cosine rank 13.
_ERRANDS = [
    ("violin", "The violin recital for the choir moved to the east hall."),
    ("lantern", "We hung a paper lantern over the porch for the street party."),
    ("harbour", "The harbour walk was windy, so we turned back near the pier."),
    ("quilt", "My aunt finished the patchwork quilt before the frost arrived."),
    ("bakery", "The bakery on Elm Road now opens at seven on weekdays."),
    ("telescope", "Our telescope club met on the hill to watch the meteor shower."),
    ("orchard", "The orchard picking day got rained out and moved a week."),
    ("canoe", "We repainted the old canoe green before the lake trip."),
    ("pottery", "The pottery class fired its first batch of mugs on Monday."),
    ("chess", "The chess evening at the library drew twelve new players."),
    ("garden", "The community garden plots were reassigned for spring."),
    ("museum", "The museum tour guide spoke about the old clock tower."),
]
_KITE = ("kite", "We bought the red box kite with the long tail for Lena.")
_GIFT_QUESTION = "Which present was chosen for the youngest cousin?"


def _gift_store(capsule_env, chat):
    home, install = capsule_env
    angles = {key: 0.10 + 0.03 * i for i, (key, _body) in enumerate(_ERRANDS)}
    angles[_KITE[0]] = 0.10 + 0.03 * len(_ERRANDS) + 0.05
    install(_GIFT_QUESTION, angles)
    # the answer is the OLDEST record: recency cannot lift it either
    turns = [("2025/02/20 (Thu) 10:00", _KITE[1], "Okay.")]
    turns += [(date, body, "Okay.") for date, (_k, body) in zip(_dates(12), _ERRANDS)]
    _ingest(home, chat, turns)
    return home


def test_declared_allowance_reaches_evidence_ranked_past_eight(capsule_env, no_node_leg):
    """Fails on the fixed-8 legs/fold: the thirteenth meaning-ranked record
    never entered any pool although the declared allowance had room."""
    home = _gift_store(capsule_env, "allowance-depth")
    budget = _quality_budget(_GIFT_QUESTION)
    block, telemetry = _capsule(home, "allowance-depth", _GIFT_QUESTION, budget=budget)
    assert "red box kite" in block, (block, telemetry.get("capsule_mode"))
    resolved = telemetry["evidence_budget"]["resolved_target_tokens"]
    assert resolved == 8192
    # the capsule stays inside the allowance it was given
    assert len(block) <= resolved * 4


def test_default_allowance_keeps_the_eight_item_depth(capsule_env, no_node_leg):
    """The allowance tie is not a blanket raise: with no declared budget the
    product default (420 tokens -> depth 8) still cannot reach rank 13."""
    home = _gift_store(capsule_env, "allowance-default")
    block, _telemetry = _capsule(home, "allowance-default", _GIFT_QUESTION)
    assert "red box kite" not in block


def test_distiller_ceiling_follows_the_allowance():
    """Fails on the fixed ceiling of 8: twelve paraphrase records with room
    for all of them delivered only eight."""
    bodies = [
        f"The archive keeps a {word} ledger from the old {place} depot."
        for word, place in zip(
            ["blue", "green", "amber", "violet", "grey", "copper",
             "ivory", "scarlet", "olive", "silver", "ochre", "teal"],
            ["north", "south", "east", "west", "harbour", "canal",
             "hill", "market", "river", "forest", "quarry", "bridge"])
    ]
    sources = [
        SimpleNamespace(body=body, occurrence_id=str(i), role="user",
                        authority="observed-user-statement", chat_scope="ceiling-fixture",
                        body_integrity="verified", status="active")
        for i, body in enumerate(bodies)
    ]
    kwargs = dict(record_sources=sources, record_roles=[("user", "")] * len(sources),
                  semantic_record_indices=set(range(len(sources))))
    query = "Which colour records survive from each depot?"
    _distilled, high = cr._distill_retrieved_hits(
        query, [(body, 1.0) for body in bodies], target_tokens=8192, **kwargs)
    _distilled, low = cr._distill_retrieved_hits(
        query, [(body, 1.0) for body in bodies], target_tokens=420, **kwargs)
    high_text = "\n".join(high["selected_facts"])
    assert all(body in high_text for body in bodies), high["selected_facts"]
    assert len(low["selected_facts"]) <= 8


# ── adjacency belongs to the hit, not to the leg ─────────────────────────────

_HUT_QUESTION = "When do the climbers have to be asleep up there?"
_HUT_TURNS = [
    ("2025/04/01 (Tue) 10:00", "Lights-out at the mountain refuge is 22:00 sharp.", "Okay."),
    ("2025/04/01 (Tue) 10:05", "Correction: make it 21:30.", "Okay."),
    ("2025/04/02 (Wed) 10:00", "The cable car closes for maintenance in May.", "Okay."),
    # the question's own words live in the chat too (so the ask is not an
    # absent-facet ask), two turns away from the correction
    ("2025/04/03 (Thu) 10:00", "The climbers were asleep before the storm rolled in.", "Okay."),
]


def test_semantic_hit_seeds_its_correction_neighbor(capsule_env, no_node_leg):
    """Fails when only BM25 hits gather neighbors: the statement is found by
    meaning alone (no shared word), and the terse correction that follows it
    shares neither words nor meaning with the question. The lexical hit is
    not adjacent to the correction."""
    home, install = capsule_env
    install(_HUT_QUESTION, {"lights-out": 0.2})
    _ingest(home, "hut-semantic", _HUT_TURNS)
    block, _telemetry = _capsule(home, "hut-semantic", _HUT_QUESTION,
                                 budget=_quality_budget(_HUT_QUESTION))
    assert "22:00" in block, block
    assert "21:30" in block, block


def test_node_hit_seeds_its_correction_neighbor(capsule_env, monkeypatch):
    """Same contract through the node leg alone: the occurrence semantic leg
    is emptied, so the statement reaches the capsule only as a node hit."""
    from core.vool_memory import VoolMemory

    monkeypatch.setattr(VoolMemory, "occurrence_search_semantic",
                        lambda self, *a, **k: [])
    home, install = capsule_env
    install(_HUT_QUESTION, {"lights-out": 0.2})
    _ingest(home, "hut-node", _HUT_TURNS)
    block, _telemetry = _capsule(home, "hut-node", _HUT_QUESTION,
                                 budget=_quality_budget(_HUT_QUESTION))
    assert "22:00" in block, block
    assert "21:30" in block, block


def test_unrelated_neighbor_never_rides_a_semantic_hit(capsule_env, no_node_leg):
    """Control: adjacency admits a correction or an answer, not any turn
    that happens to sit next to a semantic hit."""
    home, install = capsule_env
    install(_HUT_QUESTION, {"lights-out": 0.2})
    _ingest(home, "hut-control", [
        ("2025/04/01 (Tue) 10:00", "Lights-out at the mountain refuge is 22:00 sharp.", "Okay."),
        ("2025/04/01 (Tue) 10:05", "My cousin sold her bike for 140 euros.", "Okay."),
        ("2025/04/03 (Thu) 10:00", "The climbers were asleep before the storm rolled in.", "Okay."),
    ])
    block, _telemetry = _capsule(home, "hut-control", _HUT_QUESTION,
                                 budget=_quality_budget(_HUT_QUESTION))
    assert "22:00" in block, block
    assert "140 euros" not in block, block


# ── the strongest lexical hit delivers whole sentences ───────────────────────

# Four plain assertion sentences and a question ahead of the answer sentence:
# the question keeps the record from riding as one complete source unit, and
# the answer sentence sits past the sibling-sentence ride's first four.
_LEAD_IN = ("Thanks for the soup recipe. The kids loved it. We cooked it twice. "
            "It will stay on the menu. Guess what? ")

def test_top_lexical_hit_is_not_cut_into_clause_fragments(capsule_env, no_node_leg):
    """Fails on clause narrowing: the parenthetical adverb split the answer
    sentence into 'went to the market again and' / 'had trouble with the
    parking meter.' and the asked-about trouble lost its subject."""
    home, install = capsule_env
    question = "What trouble does the market give me?"
    install(question, {})
    sentence = ("I went to the market again and, unsurprisingly, had trouble "
                "with the parking meter.")
    # The embedded question ("Guess what?") keeps the whole record from
    # riding as one complete source unit (a question asserts nothing), so
    # the record is delivered through its windows.
    _ingest(home, "market-group", [
        ("2025/05/01 (Thu) 09:00", _LEAD_IN + sentence + " It keeps happening.", "Okay."),
        ("2025/05/02 (Fri) 09:00", "The weather turned cold this week.", "Okay."),
    ])
    block, _telemetry = _capsule(home, "market-group", question,
                                 budget=_quality_budget(question))
    assert sentence in block, block
    lines = [line.split("): ", 1)[-1].strip() for line in block.splitlines()]
    assert "I went to the market again and" not in lines, block
    assert "had trouble with the parking meter." not in lines, block


def test_sentence_group_never_adds_a_co_tenant_value(capsule_env, no_node_leg):
    """Guard: widening is refused when the rest of the sentence carries a
    value the narrowed clause did not (the compound-binding law)."""
    home, install = capsule_env
    question = "When does the ferry to Brin depart?"
    install(question, {})
    _ingest(home, "ferry-group", [
        ("2025/06/01 (Sun) 09:00",
         _LEAD_IN + "The ferry to Brin departs at 09:15, the bus goes at 10:40.",
         "Okay."),
    ])
    block, _telemetry = _capsule(home, "ferry-group", question,
                                 budget=_quality_budget(question))
    assert "09:15" in block, block
    assert "10:40" not in block, block


def test_sentence_group_windows_unit_contract():
    body = ("Thanks for the soup recipe! Guess what? I went to the market again and, "
            "unsurprisingly, had trouble with the parking meter. It keeps happening.")
    occ = SimpleNamespace(body=body, occurrence_id="m1", role="user",
                          authority="observed-user-statement", status="active",
                          body_integrity="verified")
    first = body.index("I went to the market again and")
    second = body.index("had trouble with the parking meter.")
    windows = [
        {"start": first, "end": first + len("I went to the market again and"),
         "text": "I went to the market again and"},
        {"start": second, "end": second + len("had trouble with the parking meter."),
         "text": "had trouble with the parking meter."},
    ]
    grouped = cr._sentence_group_windows(occ, windows)
    assert [w["text"] for w in grouped] == [
        "I went to the market again and, unsurprisingly, had trouble with the parking meter."]
    assert body[grouped[0]["start"]:grouped[0]["end"]] == grouped[0]["text"]
    # exact units are never widened
    exact = [{**windows[0], "ordinal_bound": True}, windows[1]]
    assert cr._sentence_group_windows(occ, exact) == exact


def test_sentence_group_windows_refuses_a_co_tenant_value():
    body = "Guess what? The ferry to Brin departs at 09:15, the bus goes at 10:40."
    occ = SimpleNamespace(body=body, occurrence_id="f1", role="user",
                          authority="observed-user-statement", status="active",
                          body_integrity="verified")
    clause = "The ferry to Brin departs at 09:15"
    start = body.index(clause)
    windows = [{"start": start, "end": start + len(clause), "text": clause}]
    assert cr._sentence_group_windows(occ, windows) == windows


def test_sentence_group_windows_refuses_a_hedged_or_revised_group():
    for sentence in ("I went to the market and, maybe, had trouble with the meter.",
                     "I went to the market and, correction, had trouble with the meter."):
        body = "Guess what? " + sentence
        occ = SimpleNamespace(body=body, occurrence_id="h1", role="user",
                              authority="observed-user-statement", status="active",
                              body_integrity="verified")
        first = body.index("I went to the market and")
        second = body.index("had trouble with the meter.")
        windows = [
            {"start": first, "end": first + len("I went to the market and"),
             "text": "I went to the market and"},
            {"start": second, "end": second + len("had trouble with the meter."),
             "text": "had trouble with the meter."},
        ]
        assert cr._sentence_group_windows(occ, windows) == windows, sentence
