"""Recall supplement for multi-session dialogue (core/context_retrieval.py).

A question about a person's items spread over many sessions (what they planted, places they
visited, foods one person recommended to another) is answered by turns that each share at most one
word with the ask. The legs stop at the allowance item limit and the merge's coverage law drops
every further turn that repeats an already-covered query term, so the capsule carried one or two of
the items while most of the declared evidence allowance stayed unused (measured, c1-recall
zero-spend replay of 200 paid dev questions: 109 of 339 gold evidence turns absent, 64 of them below
rank 32 of both legs; median capsule 3.9k of 8.2k tokens).

Law: after every other lane, the unused allowance takes whole speaker-labelled turns ranked by
reciprocal-rank fusion of the lexical and semantic legs read deeper than the item limit, the named
person's own turns first. A turn qualifies by the semantic floor or by carrying two asked terms of
its own (all the ask's terms the chat uses, when fewer). The supplement never evicts a delivered
line, never passes the declared allowance or the packer's budget, never delivers a turn the temporal
contract excludes (in the pool's run or in its own run with the turns' chain mates), never
absent-facet noise, never a bare acknowledgment or a turn with no assertion. Totals, mention counts,
as-of asks, assistant-output asks and complete-collection requests keep their own selection;
unlabelled chats are untouched. A whole reported turn carries its own modality, so the user-hedge
law does not refuse it (a suggestion is what a suggestion question asks for).

All names, places and sentences are invented for this contract.
"""

from __future__ import annotations

import re

import pytest

import core.context_retrieval as cr
from tests.test_question_date_time_leg_20261002 import (  # noqa: F401
    _hash_backend,
    _ingest,
    _profile,
)
from tests.test_time_leg_follows_allowance_20261003 import _wide_capsule
from tests.test_whole_turn_packed_once_20261003 import (
    _FILLER_A,
    _FILLER_B,
    _home_with,
    _sessions,
)

_GARDEN_SHORT_OSKAR = ["The garden is soggy again.", "Garden gate squeaks, need oil.",
                       "Slugs everywhere in the garden.", "Garden hose split this morning.",
                       "The garden needs weeding badly.", "Garden bench finally painted.",
                       "Wind knocked over the garden chairs.", "The garden smells of rain."]
_GARDEN_SHORT_LENA = ["My garden is tiny compared to yours.",
                      "Garden centres are packed on Saturdays.", "Our garden has no sun at all.",
                      "The garden fence blew down.", "Garden birds love the feeder.",
                      "My garden gnome got stolen.", "The garden path is flooded.",
                      "Garden snails ate my lettuce."]
_PLANTINGS = {
    1: "Oskar: After a lot of digging this spring I planted three rows of purple carrots along the "
       "back of the garden near the old shed, and the soil there is surprisingly good.",
    4: "Oskar: Over the long weekend my sister and I planted a cherry tree in the far corner of "
       "the garden, right where the trampoline used to stand before the storm.",
    7: "Oskar: Last month I finally planted climbing beans against the south fence of the garden, "
       "they need a trellis but the neighbour lent me some poles.",
}
_ANSWERS = ("purple carrots", "cherry tree", "climbing beans")


def _garden_store(extra: dict[int, list[str]] | None = None, labelled: bool = True):
    sessions = []
    for i in range(8):
        turns = []
        for j in range(2):
            turns += [f"Lena: {_FILLER_A[(i + j) % 4]}", f"Oskar: {_FILLER_B[(i + j) % 4]}"]
        turns += [f"Oskar: {_GARDEN_SHORT_OSKAR[i]}", f"Lena: {_GARDEN_SHORT_LENA[i]}"]
        if i in _PLANTINGS:
            turns.insert(2, _PLANTINGS[i])
        for line in (extra or {}).get(i, []):
            turns.insert(1, line)
        if not labelled:
            turns = [re.sub(r"^(?:Lena|Oskar): ", "", turn) for turn in turns]
        sessions.append(turns)
    return _sessions(sessions)


_PLANTING_FAMILY = [
    # original reported shape (a person's items spread over several sessions)
    "What has Oskar planted in his garden?",
    # clean paraphrases
    "Which plants did Oskar put in his garden this year?",
    "What did Oskar plant in the garden over the year?",
    "Name everything Oskar has planted in the garden.",
    "In his garden, what has Oskar planted so far?",
    "List the things Oskar planted in his garden.",
    # sloppy / typed variants
    "what did oskar plant in the garden",
    "oskar planted what in garden",
    "wht did oskar plant in the garden",
    "oskars garden, what did he plant",
    "Oskar garden planted stuff?",
    "what has oskar planted in his garden lol",
    "Oskar's garden: which things did he plant",
]


def _capsule(tmp_path, store, question, target=2048, chat="garden"):
    profile = _home_with(tmp_path, chat, store)
    return _wide_capsule(profile, chat, question, target_tokens=target)


@pytest.mark.usefixtures("_hash_backend")
@pytest.mark.parametrize("question", _PLANTING_FAMILY)
def test_items_spread_over_sessions_all_reach_the_capsule(tmp_path, question):
    capsule, telemetry = _capsule(tmp_path, _garden_store(), question)
    missing = [answer for answer in _ANSWERS if answer not in capsule]
    assert not missing, (missing, telemetry.get("recall_supplement"), capsule)


def _records(capsule: str) -> list[str]:
    """Capsule records: a line opening with "- " and its continuation lines."""
    records: list[str] = []
    for line in capsule.splitlines():
        if line.startswith("- "):
            records.append(line)
        elif records and not line.startswith("</retrieved_context>"):
            records[-1] += "\n" + line
    return records


def _supplement_disabled(monkeypatch):
    monkeypatch.setattr(cr, "_recall_supplement_applies", lambda _query: False)


@pytest.mark.usefixtures("_hash_backend")
@pytest.mark.xfail(strict=True, reason=(
    "known boundary of the lexical-only (hash) lane: a misspelt CONTENT word ('plantd') matches "
    "no record, and the absent-facet gate reads it as an asked attribute no record states, "
    "suppressing the speaker's records; on neural backends the semantic leg is exempt from that "
    "gate in the merge and in the supplement"))
def test_a_misspelt_content_word_on_the_lexical_lane(tmp_path):
    capsule, _telemetry = _capsule(tmp_path, _garden_store(), "wat has Oskar plantd in his garden")
    assert all(answer in capsule for answer in _ANSWERS)


@pytest.mark.usefixtures("_hash_backend")
@pytest.mark.parametrize("question", [
    "What did Oskar plant in the garden over the year?",
    "what did oskar plant in the garden",
    "Oskar's garden: which things did he plant",
])
def test_the_supplement_is_what_delivers_the_missed_items(tmp_path, monkeypatch, question):
    """The items the pool's own lanes leave out are delivered by the supplement, as attributed
    supplement receipts (the seam), and without it they are missing."""
    store = _garden_store()
    profile = _home_with(tmp_path, "garden", store)
    with monkeypatch.context() as patch:
        _supplement_disabled(patch)
        without, _ = _wide_capsule(profile, "garden", question, target_tokens=2048)
    assert not all(answer in without for answer in _ANSWERS), without
    capsule, telemetry = _wide_capsule(profile, "garden", question, target_tokens=2048)
    assert all(answer in capsule for answer in _ANSWERS), capsule
    supplement_lines = [str(ref.get("line")) for ref in telemetry.get("evidence_refs") or []
                        if ref.get("delivered") and ref.get("recall_supplement")]
    assert any(answer in line for line in supplement_lines for answer in _ANSWERS), (
        telemetry.get("recall_supplement"), supplement_lines)
    assert all("Oskar:" in line or "Lena:" in line for line in supplement_lines), supplement_lines


@pytest.mark.usefixtures("_hash_backend")
@pytest.mark.parametrize("target", [600, 1200, 2048, 4096])
@pytest.mark.parametrize("question", [
    "What has Oskar planted in his garden?",
    "what did oskar plant in the garden",
    "What did Lena say about her garden gnome?",
])
def test_the_supplement_never_displaces_a_delivered_line(tmp_path, monkeypatch, question, target):
    store = _garden_store()
    profile = _home_with(tmp_path, "garden", store)
    with monkeypatch.context() as patch:
        _supplement_disabled(patch)
        without, telemetry_without = _wide_capsule(profile, "garden", question,
                                                   target_tokens=target)
    with_supplement, telemetry = _wide_capsule(profile, "garden", question, target_tokens=target)
    kept = set(_records(with_supplement))
    lost = [record for record in _records(without) if record not in kept]
    assert not lost, (lost, telemetry.get("recall_supplement"))
    packed = telemetry.get("packed") or {}
    assert packed.get("dropped_budget", 0) == 0, packed
    tokens = int(telemetry.get("estimated_distilled_tokens") or 0)
    tokens_without = int(telemetry_without.get("estimated_distilled_tokens") or 0)
    assert tokens <= max(target, tokens_without), (tokens, tokens_without, target)


@pytest.mark.usefixtures("_hash_backend")
@pytest.mark.parametrize("question", [
    "What did Lena say about her garden gnome?",
    "lena garden gnome what happened",
    "Which garden ornament of Lena's got stolen?",
])
def test_a_single_fact_ask_gets_no_one_word_garden_chatter(tmp_path, question):
    # negative control: one shared word ("garden") is not relevance when the ask names more;
    # the supplement adds no turn that lacks the ask's other term
    capsule, telemetry = _capsule(tmp_path, _garden_store(), question)
    assert "gnome got stolen" in capsule, capsule
    supplement_lines = [str(ref.get("line")) for ref in telemetry.get("evidence_refs") or []
                        if ref.get("delivered") and ref.get("recall_supplement")]
    assert all("gnome" in line.lower() for line in supplement_lines), supplement_lines


@pytest.mark.usefixtures("_hash_backend")
@pytest.mark.parametrize("question", [
    "How many times did Oskar talk about planting in the garden?",
    "What is the total number of rows Oskar planted in the garden?",
    "As of May, what had Oskar planted in the garden?",
])
def test_totals_counts_and_as_of_asks_keep_their_own_selection(tmp_path, question):
    # negative control: derived values are computed from the exact operands the lanes packed
    _capsule_text, telemetry = _capsule(tmp_path, _garden_store(), question)
    supplement = telemetry.get("recall_supplement") or {}
    assert not supplement.get("delivered"), supplement
    assert not any(ref.get("recall_supplement") for ref in telemetry.get("evidence_refs") or [])


@pytest.mark.usefixtures("_hash_backend")
def test_an_unlabelled_chat_is_untouched(tmp_path):
    # negative control: the supplement reads speaker-labelled transcripts only
    _capsule_text, telemetry = _capsule(
        tmp_path, _garden_store(labelled=False), "What did I plant in the garden?")
    supplement = telemetry.get("recall_supplement") or {}
    assert not supplement.get("candidates"), supplement
    assert not supplement.get("delivered"), supplement


@pytest.mark.usefixtures("_hash_backend")
@pytest.mark.parametrize("question", [
    "What did Lena suggest Oskar plant in his garden?",
    "what did lena tell oskar to plant in his garden",
    "Lena's planting suggestion for Oskar's garden?",
])
def test_a_suggestion_in_a_whole_reported_turn_is_not_a_hedge(tmp_path, question):
    store = _garden_store(extra={3: ["Lena: Perhaps plant tomatoes along the south fence of "
                                     "your garden, they love the sun."]})
    capsule, telemetry = _capsule(tmp_path, store, question)
    assert "plant tomatoes along the south fence" in capsule, (
        telemetry.get("recall_supplement"), capsule)


@pytest.mark.usefixtures("_hash_backend")
def test_the_named_person_is_the_subject_not_the_most_frequent_speaker(tmp_path):
    # adversarial near-miss: same words, the OTHER speaker asked about
    store = _garden_store(extra={6: ["Lena: I planted mint in a window box since my garden gets "
                                     "no sun at all."]})
    capsule, telemetry = _capsule(tmp_path, store, "What has Lena planted in her garden?")
    supplement = telemetry.get("recall_supplement") or {}
    assert supplement.get("subject_speaker") == "Lena", supplement
    assert "planted mint in a window box" in capsule, capsule


@pytest.mark.usefixtures("_hash_backend")
def test_a_turn_the_temporal_contract_excludes_is_never_supplemented(tmp_path, monkeypatch):
    """The supplement obeys the contract's verdict on every turn it would add: rule the cherry
    tree turn out and it must stay out, whichever run (pool or supplement) excluded it."""
    import core.temporal_selection as temporal

    original = temporal.apply_temporal_selection

    def excluding(candidates, **kwargs):
        verdicts = original(candidates, **kwargs)
        for candidate in candidates:
            body = str(getattr(candidate, "body", "") or "")
            if "cherry tree" in body:
                verdicts[candidate.key] = temporal.EligibilityVerdict(
                    key=candidate.key, eligible=False, reason="superseded",
                    slot=getattr(verdicts.get(candidate.key), "slot", None))
        return verdicts

    monkeypatch.setattr(temporal, "apply_temporal_selection", excluding)
    capsule, telemetry = _capsule(tmp_path, _garden_store(), "what did oskar plant in the garden")
    supplement_lines = [str(ref.get("line")) for ref in telemetry.get("evidence_refs") or []
                        if ref.get("delivered") and ref.get("recall_supplement")]
    assert not any("cherry tree" in line for line in supplement_lines), supplement_lines
    assert "cherry tree" not in capsule, capsule


@pytest.mark.parametrize("query, speakers, subject", [
    ("What has Oskar planted in his garden?", {"Oskar", "Lena"}, "Oskar"),
    ("what did oskar plant", {"Oskar", "Lena"}, "Oskar"),
    ("oskars garden, what did he plant", {"Oskar", "Lena"}, "Oskar"),
    ("Oskar's garden?", {"Oskar", "Lena"}, "Oskar"),
    ("What did Lena suggest Oskar plant?", {"Oskar", "Lena"}, "Lena"),
    ("How do Oskar and Lena spend weekends?", {"Oskar", "Lena"}, ""),
    ("Where did Lena & Oskar meet?", {"Oskar", "Lena"}, ""),
    ("What did the neighbour plant?", {"Oskar", "Lena"}, ""),
    ("What did Oskarsson plant?", {"Oskar", "Lena"}, ""),
])
def test_named_subject_speaker(query, speakers, subject):
    assert cr._named_subject_speaker(query, speakers) == subject


# ── gate, chain mates, the two budget guards and repeated words ───────────────

@pytest.mark.parametrize("query, applies", [
    # ordinary asks the supplement serves
    ("What has Oskar planted in his garden?", True),
    ("which places did mira visit in the old town", True),
    ("What foods has Tamsin recommended to Ilya?", True),
    ("When did Corvin first go sailing?", True),
    # derived values and bound units keep their own selection
    ("How many times did Oskar talk about planting?", False),
    ("How often does Mira go to the market?", False),
    ("What is the total number of rows Oskar planted?", False),
    ("As of May, what had Oskar planted?", False),
    ("What did you tell me about the garden last time?", False),
    # exact recall of one stored value: other values beside it are noise
    ("Which code did Lena give Oskar for the garden gate?", False),
    ("what code opens the shed", False),
    ("What is the exact locker id Oskar stored?", False),
    ("What is my spend cap?", False),
    # adversarial near-miss: the same nouns, not an exact-value recall
    ("What did Oskar say about painting the shed?", True),
])
def test_the_supplement_gate_by_ask_shape(query, applies):
    assert cr._recall_supplement_applies(query) is applies, query


class _Occ:
    def __init__(self, occurrence_id: str, body: str):
        self.occurrence_id = occurrence_id
        self.body = body


class _ProbeMem:
    """Records each probe and answers it from a fixed chat by shared words."""

    def __init__(self, records: list[_Occ]):
        self.records = records
        self.queries: list[str] = []

    def occurrence_search(self, query, *, chat_scope, limit):
        self.queries.append(query)
        words = set(query.lower().split())
        hits = [(r, 1.0) for r in self.records
                if words & set(re.findall(r"[a-z]+", r.body.lower()))]
        return hits[:limit]


def test_chain_mates_are_probed_by_each_turns_own_subject():
    turn = _Occ("t1", "Oskar: I planted a walnut sapling by the gate.")
    retraction = _Occ("r1", "Oskar: Scratch that about the walnut sapling, it went to my sister.")
    known = _Occ("k1", "Oskar: The walnut sapling cost twelve euros.")
    mem = _ProbeMem([turn, retraction, known])
    mates = cr._recall_supplement_chain_mates(mem, [turn], known_ids={"k1"}, session_id="chat")
    assert [m.occurrence_id for m in mates] == ["r1"], [m.occurrence_id for m in mates]
    assert mem.queries and {"walnut", "sapling"} <= set(mem.queries[0].split()), mem.queries
    assert cr._recall_supplement_chain_mates(mem, [turn], known_ids=set(), session_id="") == []


def _tight_capsule(profile, chat, question, target):
    """The harness shape: the declared evidence target IS the packer's free budget."""
    from dataclasses import replace

    from core.context_capsule_v2 import resolve_budget
    from core.context_namespace import ensure_chat_namespace
    from core.context_retrieval import get_last_retrieval_telemetry, inject_retrieved
    from core.memory.entries import resolve_memory_access_policy

    ensure_chat_namespace(chat, grant_current_receipts=False)
    policy = resolve_memory_access_policy(chat_id=chat)
    budget = replace(
        resolve_budget(bucket="D", role="heavy_reasoning", output_reserve_tokens=2048,
                       evidence_target_tokens=target, retrieval_ceiling_tokens=target),
        min_score=0.25)
    messages = inject_retrieved(
        chat, question, [{"role": "user", "content": question}], access_policy=policy,
        source_context={"chat_id": chat, "runtime_home": str(profile)},
        env={"VOOL_CONTEXT_CAPSULE_V2": "1"}, budget=budget)
    capsule = next((str(m.get("content") or "") for m in messages
                    if "<retrieved_context>" in str(m.get("content") or "")), "")
    return capsule, get_last_retrieval_telemetry()


def _compare_without_supplement(tmp_path, monkeypatch, question, target, capsule_fn):
    """(base records the supplement lost, packer drops without, telemetry with)."""
    profile = _home_with(tmp_path, "garden", _garden_store())
    with monkeypatch.context() as patch:
        _supplement_disabled(patch)
        without, telemetry_without = capsule_fn(profile, "garden", question, target)
    with_supplement, telemetry = capsule_fn(profile, "garden", question, target)
    kept = set(_records(with_supplement))
    lost = [record for record in _records(without) if record not in kept]
    dropped_without = int((telemetry_without.get("packed") or {}).get("dropped_budget", 0) or 0)
    return lost, dropped_without, telemetry


@pytest.mark.usefixtures("_hash_backend")
@pytest.mark.parametrize("target", [400, 600, 900])
@pytest.mark.parametrize("question", [
    "Tell me about Oskar's garden",
    "oskar garden news?",
    "What has Lena said about her garden?",
])
def test_the_supplement_stops_before_the_packer_would_drop_a_line(tmp_path, monkeypatch,
                                                                   question, target):
    # the harness shape (declared target == packer budget): per-line token rounding and the
    # header would make the final packer drop lines if the supplement filled the char budget
    lost, dropped_without, telemetry = _compare_without_supplement(
        tmp_path, monkeypatch, question, target,
        lambda profile, chat, q, t: _tight_capsule(profile, chat, q, t))
    assert not lost, (lost, telemetry.get("recall_supplement"))
    dropped = int((telemetry.get("packed") or {}).get("dropped_budget", 0) or 0)
    assert dropped <= dropped_without, (dropped, dropped_without, telemetry.get("recall_supplement"))


@pytest.mark.usefixtures("_hash_backend")
@pytest.mark.parametrize("target", [150, 180, 200, 230, 260])
@pytest.mark.parametrize("question", [
    "what did oskar plant in the garden",
    "Oskar's garden: which things did he plant",
])
def test_a_supplement_turn_never_evicts_a_delivered_line(tmp_path, monkeypatch, question, target):
    # the room estimate is relaxed so the delivery path's own budget refusal is what binds:
    # a decisive supplement turn must still never demote a line another lane delivered
    monkeypatch.setattr(cr, "_RECALL_SUPPLEMENT_PACK_MARGIN_TOKENS", -10_000)
    lost, _dropped_without, telemetry = _compare_without_supplement(
        tmp_path, monkeypatch, question, target,
        lambda profile, chat, q, t: _wide_capsule(profile, chat, q, target_tokens=t))
    assert not lost, (lost, telemetry.get("recall_supplement"))


@pytest.mark.usefixtures("_hash_backend")
def test_the_same_words_said_again_are_not_supplemented_twice(tmp_path):
    store = _garden_store(extra={6: [_PLANTINGS[1]]})  # the carrots turn, said again months later
    capsule, telemetry = _capsule(tmp_path, store, "what did oskar plant in the garden")
    assert capsule.count("three rows of purple carrots") == 1, (
        telemetry.get("recall_supplement"), capsule)


@pytest.mark.usefixtures("_hash_backend")
def test_unlabelled_records_in_a_labelled_chat_are_never_supplemented(tmp_path):
    # negative control: a plain note mixed into a transcript stays with the general lanes
    store = [*_garden_store(), (_sessions([["x"]])[0][0] + 86_400.0,
                                "I planted garlic cloves in the garden beside the shed.")]
    _capsule_text, telemetry = _capsule(tmp_path, store, "what did oskar plant in the garden")
    supplement_lines = [str(ref.get("line")) for ref in telemetry.get("evidence_refs") or []
                        if ref.get("delivered") and ref.get("recall_supplement")]
    assert supplement_lines, telemetry.get("recall_supplement")
    assert not any("garlic" in line for line in supplement_lines), supplement_lines


class _Mate:
    """A stored-shape record the chain probe can hand to the contract."""

    def __init__(self, occurrence_id: str, body: str, stated: float):
        self.occurrence_id = occurrence_id
        self.body = body
        self.role = "user"
        self.authority = "observed-user-statement"
        self.statement_at = stated
        self.event_at = None
        self.recorded_at = stated
        self.source_kind = "live-turn"
        self.source_sequence = None
        self.speaker = ""
        self.body_integrity = "verified"
        self.chat_scope = "garden"


@pytest.mark.usefixtures("_hash_backend")
def test_chain_mates_reach_the_contract_that_rules_the_supplement(tmp_path, monkeypatch):
    """A retraction found only by the supplement's chain probe withdraws the turn it names."""
    stated = _sessions([[]] * 8 + [["x"]])[0][0]  # a ninth session, after every planting
    retraction = _Mate(
        "mate-1", "Session date: 10:00 am on 18 September, 2026\n"
                  "Oskar: Scratch that about the purple carrots, they went to my sister's "
                  "allotment instead.", stated)
    monkeypatch.setattr(cr, "_recall_supplement_chain_mates",
                        lambda *args, **kwargs: [retraction])
    capsule, telemetry = _capsule(tmp_path, _garden_store(), "what did oskar plant in the garden")
    supplement_lines = [str(ref.get("line")) for ref in telemetry.get("evidence_refs") or []
                        if ref.get("delivered") and ref.get("recall_supplement")]
    assert not any("purple carrots" in line for line in supplement_lines), (
        telemetry.get("recall_supplement"), supplement_lines)
    assert "purple carrots" not in capsule, capsule
    skipped = (telemetry.get("recall_supplement") or {}).get("skipped") or {}
    assert any(reason.startswith("temporal:") for reason in skipped), skipped


# ── a short turn delivered in part is completed whole ─────────────────────────

_SLEEP_TURNS = {4: ["Lena: A white-noise machine might help your sleep, mine drowns out the "
                    "night trains. Good sleep fixes everything, Oskar.",
                    "Oskar: I keep waking up at three in the morning, my sleep is a mess."]}
_SLEEP_ANSWER = "A white-noise machine might help your sleep"


@pytest.mark.usefixtures("_hash_backend")
@pytest.mark.parametrize("question", [
    # original shape (a hedged suggestion asked about; the hedge law kept only the rest)
    "What did Lena suggest for Oskar's sleep?",
    # clean paraphrases
    "What could help Oskar sleep, according to Lena?",
    "Which machine did Lena think would help Oskar's sleep?",
    "What was Lena's advice for Oskar's sleep?",
    "For Oskar's sleep at night, what did Lena recommend?",
    "What might fix Oskar's sleep in Lena's view?",
    # sloppy / typed variants
    "lena sleep advice oskar?",
    "oskar sleep - lena suggested what",
    "wat helps oskar sleep per lena",
    "Lena tip oskar sleeping",
    "lena machine for oskar sleep??",
])
def test_a_partly_delivered_turn_is_completed_whole(tmp_path, question):
    store = _garden_store(extra=_SLEEP_TURNS)
    capsule, telemetry = _capsule(tmp_path, store, question)
    assert _SLEEP_ANSWER in capsule, (telemetry.get("recall_supplement"), capsule)


@pytest.mark.usefixtures("_hash_backend")
def test_a_turn_already_whole_in_the_capsule_is_not_repeated(tmp_path):
    # negative control: completion is for turns delivered in part, never a second copy
    store = _garden_store(extra=_SLEEP_TURNS)
    capsule, _telemetry = _capsule(tmp_path, store, "Why does Oskar keep waking up at three?")
    assert capsule.count("I keep waking up at three in the morning") <= 1, capsule


# ── every guard of the supplement pass, whatever the candidate leg ranked first ─

def _prepend_candidates(monkeypatch, *needles: str):
    """The candidate leg returns the stored turn carrying each needle first - a ranking the
    semantic leg can produce - and keeps its own ranking after them. Every law of the delivery
    pass still applies to every candidate."""
    original = cr._recall_supplement_candidates

    def ranked_first(mem, query, q_vec, q_backend, *, session_id, limit, **kwargs):
        ranked, subject, semantic = original(mem, query, q_vec, q_backend,
                                             session_id=session_id, limit=limit, **kwargs)
        head = []
        for needle in needles:
            for occurrence, _score in mem.occurrence_search(needle, chat_scope=session_id,
                                                            limit=64):
                if needle.lower() in str(getattr(occurrence, "body", "") or "").lower():
                    head.append((occurrence, 1.0))
                    break
        assert len(head) == len(needles), (needles, head)
        head_ids = {str(getattr(occurrence, "occurrence_id", "")) for occurrence, _ in head}
        return head + [(occurrence, fused) for occurrence, fused in ranked
                       if str(getattr(occurrence, "occurrence_id", "")) not in head_ids], (
            subject), semantic

    monkeypatch.setattr(cr, "_recall_supplement_candidates", ranked_first)


def _supplement_lines(telemetry) -> list[str]:
    return [str(ref.get("line")) for ref in telemetry.get("evidence_refs") or []
            if ref.get("delivered") and ref.get("recall_supplement")]


def _skips(telemetry) -> dict:
    return (telemetry.get("recall_supplement") or {}).get("skipped") or {}


_LONG_TRIP = ("Oskar: The coastal bike trip took four days in the end. The first day we rode into a "
              "headwind past the salt marshes and stopped at a tiny cafe that only sold rye bread and "
              "fish soup. On the second day the chain snapped twice and a farmer lent us pliers and "
              "a spare link. The third day was all hills and we pushed the bikes up the last two. On "
              "the fourth day the sun finally came out and we coasted down to the ferry with an hour "
              "to spare, sunburnt and starving, and swore we would do it again next summer with "
              "proper panniers.")
_LONG_PLANTING = ("Oskar: Big weekend in the garden. I planted a row of gooseberry bushes along the "
                  "north wall, moved the rhubarb crowns to the shady bed, and finally cleared the "
                  "brambles that had taken over the compost corner. My neighbour came by with a "
                  "wheelbarrow of horse manure and we dug it into the vegetable beds before the rain "
                  "started. By Sunday evening my back was aching, the shed was a mess of muddy tools, "
                  "and I still had to fix the gate hinge, but the beds look ready for spring at last "
                  "and I have a plan for the potatoes.")


@pytest.mark.usefixtures("_hash_backend")
@pytest.mark.parametrize("needle, long_turn", [
    ("coastal bike trip", _LONG_TRIP),  # a long turn no other lane delivered
    ("gooseberry bushes", _LONG_PLANTING),  # a long turn the pool delivered in part
])
def test_a_long_turn_is_never_supplemented(tmp_path, monkeypatch, needle, long_turn):
    """Whole short turns only: a record longer than one complete source unit is the pool's
    to window, never delivered whole or completed by the supplement."""
    assert len(long_turn) > cr._COMPLETE_SOURCE_MAX_CHARS
    _prepend_candidates(monkeypatch, needle)
    store = _garden_store(extra={5: [long_turn]})
    capsule, telemetry = _capsule(tmp_path, store, "what did oskar plant in the garden")
    assert not any(needle in line for line in _supplement_lines(telemetry)), (
        _supplement_lines(telemetry))
    assert _skips(telemetry).get("long-turn", 0) >= 1, telemetry.get("recall_supplement")
    assert capsule.count("Big weekend in the garden") <= 1, capsule


@pytest.mark.usefixtures("_hash_backend")
@pytest.mark.parametrize("ack", ["Lena: Woohoo, yay, cheers!", "Lena: Haha wow, thanks!!",
                                 "Lena: Aww, okay, sure."])
def test_a_bare_acknowledgment_is_never_supplemented(tmp_path, monkeypatch, ack):
    needle = ack.split(": ", 1)[1]
    _prepend_candidates(monkeypatch, needle)
    store = _garden_store(extra={3: [ack]})
    capsule, telemetry = _capsule(tmp_path, store, "what did oskar plant in the garden")
    assert not any(needle in line for line in _supplement_lines(telemetry)), (
        _supplement_lines(telemetry))
    assert _skips(telemetry).get("no-assertion", 0) >= 1, telemetry.get("recall_supplement")
    assert all(answer in capsule for answer in _ANSWERS), capsule


_GLASSHOUSE = {2: ["Lena: Your glasshouse looks lovely from the lane."],
               6: ["Oskar: The thermometer read 31 degrees by lunchtime yesterday."]}


@pytest.mark.usefixtures("_hash_backend")
@pytest.mark.parametrize("question", [
    "How hot is the glasshouse right now?",
    "glasshouse temperature at the moment?",
    "What is the glasshouse like at the moment, warm?",
])
def test_a_past_reading_never_answers_a_right_now_ask_through_the_supplement(
        tmp_path, monkeypatch, question):
    """The current-observation contract rules supplement turns the pool never fetched: a past
    report of a measured value is history, not a live reading."""
    _prepend_candidates(monkeypatch, "thermometer read 31")
    capsule, telemetry = _capsule(tmp_path, _garden_store(extra=_GLASSHOUSE), question)
    assert "31 degrees" not in capsule, (telemetry.get("recall_supplement"), capsule)
    assert _skips(telemetry).get("temporal:current-observation-contract", 0) >= 1, (
        telemetry.get("recall_supplement"))


@pytest.mark.usefixtures("_hash_backend")
def test_a_past_reading_is_supplemented_for_a_history_ask(tmp_path, monkeypatch):
    # negative control: the same turn answers an ask about what the reading WAS
    _prepend_candidates(monkeypatch, "thermometer read 31")
    capsule, telemetry = _capsule(tmp_path, _garden_store(extra=_GLASSHOUSE),
                                  "What did the glasshouse thermometer show?")
    assert "31 degrees" in capsule, (telemetry.get("recall_supplement"), capsule)


@pytest.mark.usefixtures("_hash_backend")
@pytest.mark.parametrize("question", [
    "Which code did Lena give Oskar for the garden gate?",
    "what code opens the garden gate",
])
def test_an_exact_code_ask_keeps_its_own_selection(tmp_path, question):
    store = _garden_store(extra={2: ["Lena: The garden gate code is 7342, Oskar."]})
    capsule, telemetry = _capsule(tmp_path, store, question)
    assert "7342" in capsule, capsule
    supplement = telemetry.get("recall_supplement") or {}
    assert not supplement.get("candidates"), supplement
    assert not _supplement_lines(telemetry), _supplement_lines(telemetry)


class _Turn:
    """A stored-shape speaker-labelled turn for the candidate ranking."""

    def __init__(self, occurrence_id: str, body: str):
        self.occurrence_id = occurrence_id
        self.body = body
        self.role = "user"
        self.body_integrity = "verified"


class _RankedMem:
    """A lexical leg with a fixed ranking (no semantic leg on this backend)."""

    def __init__(self, turns: list[_Turn]):
        self.turns = turns

    def occurrence_search(self, query, *, chat_scope, limit):
        return [(turn, 1.0) for turn in self.turns[:limit]]


def test_a_speakers_name_is_never_an_asked_term():
    """A name matches every turn its owner said and every turn that addresses them: a turn
    carrying the asked person's name and ONE asked term is one-word chatter, not relevance."""
    mem = _RankedMem([
        _Turn("vocative", "Lena: Oskar, the garden centre rang about your order."),
        _Turn("mention", "Lena: I told Oskar the garden fence is leaning."),
        _Turn("answer", "Oskar: I planted chard in the garden this week."),
        _Turn("other", "Lena: The tram was late again."),
    ])
    ranked, subject, semantic = cr._recall_supplement_candidates(
        mem, "What did Oskar plant in the garden?", None, "hash", session_id="garden", limit=10)
    assert subject == "Oskar"
    assert not semantic  # no semantic leg on this backend
    assert [turn.occurrence_id for turn, _fused in ranked] == ["answer"], ranked


def test_the_named_persons_turns_rank_first():
    """Turns spoken by the person the question names come first by a bounded factor: the
    other speaker's turn ranks first on the lexical leg alone."""
    mem = _RankedMem([
        _Turn("lena", "Lena: I planted basil in my garden boxes."),
        _Turn("oskar", "Oskar: I planted leeks in the garden."),
    ])
    ranked, subject, _semantic = cr._recall_supplement_candidates(
        mem, "What did Oskar plant in the garden?", None, "hash", session_id="garden", limit=10)
    assert subject == "Oskar"
    assert [turn.occurrence_id for turn, _fused in ranked] == ["oskar", "lena"], ranked
    ranked, subject, _semantic = cr._recall_supplement_candidates(
        mem, "What did Lena plant in her garden?", None, "hash", session_id="garden", limit=10)
    assert subject == "Lena"
    assert [turn.occurrence_id for turn, _fused in ranked] == ["lena", "oskar"], ranked


# ── the semantic leg is the paraphrase path: exempt from the absent-facet gate ─

class _SemanticMem(_RankedMem):
    """A lexical leg and a semantic leg with fixed rankings."""

    def __init__(self, lexical: list[_Turn], semantic: list[_Turn]):
        super().__init__(lexical)
        self.semantic = semantic
        self.floors: list[float] = []

    def occurrence_search_semantic(self, q_vec, *, chat_scope, backend, floor, limit):
        self.floors.append(floor)
        return [(turn, 0.61) for turn in self.semantic[:limit]]


def test_the_candidates_name_the_turns_the_semantic_leg_read():
    fencing = _Turn("fencing", "Oskar: I signed up for evening fencing lessons.")
    chatter = _Turn("chatter", "Lena: Pastimes are taken too seriously round here.")
    mem = _SemanticMem([chatter], [fencing])
    ranked, subject, semantic = cr._recall_supplement_candidates(
        mem, "Which pastimes has Oskar taken up?", [0.1, 0.2], "ollama:nomic-embed-text",
        session_id="garden", limit=10)
    assert subject == "Oskar"
    assert semantic == frozenset({"fencing"}), semantic
    assert {turn.occurrence_id for turn, _fused in ranked} == {"fencing", "chatter"}, ranked
    assert mem.floors, "the semantic leg is read with its backend floor"
    # a lexical-only backend has no semantic leg and names no turn
    _ranked, _subject, semantic = cr._recall_supplement_candidates(
        mem, "Which pastimes has Oskar taken up?", [0.1, 0.2], "hash", session_id="garden",
        limit=10)
    assert semantic == frozenset(), semantic


_PASTIMES = {2: ["Oskar: I signed up for evening fencing lessons at the sports hall."],
             5: ["Oskar: The compost bin lid cracked in the frost."]}


def _rank_semantic_first(monkeypatch, semantic_needle: str, lexical_needle: str):
    """The candidate leg returns the turn carrying *semantic_needle* as read by the semantic
    leg above its floor and the one carrying *lexical_needle* as a lexical-only candidate,
    ahead of its own ranking."""
    original = cr._recall_supplement_candidates

    def ranked_first(mem, query, q_vec, q_backend, *, session_id, limit, **kwargs):
        ranked, subject, _semantic = original(mem, query, q_vec, q_backend,
                                              session_id=session_id, limit=limit, **kwargs)
        found = {}
        for needle in (semantic_needle, lexical_needle):
            for occurrence, _score in mem.occurrence_search(needle, chat_scope=session_id,
                                                            limit=64):
                if needle.lower() in str(getattr(occurrence, "body", "") or "").lower():
                    found[needle] = occurrence
                    break
        assert len(found) == 2, found
        head_ids = {str(getattr(o, "occurrence_id", "")) for o in found.values()}
        head = [(found[semantic_needle], 1.0), (found[lexical_needle], 0.9)]
        return head + [(o, f) for o, f in ranked
                       if str(getattr(o, "occurrence_id", "")) not in head_ids], subject, (
            frozenset({str(getattr(found[semantic_needle], "occurrence_id", ""))}))

    monkeypatch.setattr(cr, "_recall_supplement_candidates", ranked_first)


@pytest.mark.usefixtures("_hash_backend")
@pytest.mark.parametrize("question", [
    # each ask names two or more words no turn of the chat uses: the absent-facet gate is active
    "Which pastimes has Oskar taken up?",
    "What hobbies has Oskar picked up lately?",
    "wat does oskar do 4 fun",
    "hobbies oskar picked up, all of them",
    "Oskar's new leisure pursuits?",
])
def test_the_semantic_leg_is_exempt_from_the_absent_facet_gate(tmp_path, monkeypatch, question):
    """A category-word or sloppy ask names words no turn uses; the gate then holds back every
    lexical-only turn, while a turn the semantic leg read above its floor is the paraphrase
    class the merge's semantic arm exempts too."""
    _rank_semantic_first(monkeypatch, "evening fencing lessons", "compost bin lid")
    capsule, telemetry = _capsule(tmp_path, _garden_store(extra=_PASTIMES), question)
    lines = _supplement_lines(telemetry)
    assert any("evening fencing lessons" in line for line in lines), (
        telemetry.get("recall_supplement"), lines)
    # the gate is active and still binds the lexical-only candidate
    assert not any("compost bin lid" in line for line in lines), lines
    assert _skips(telemetry).get("facet-noise", 0) >= 1, telemetry.get("recall_supplement")
    assert "compost bin lid" not in capsule, capsule
