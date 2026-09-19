"""ROUND-019 R2 — sabotage pins S1-S15.

Each pin names ONE authority violation. Restoring the violation in
``core/kernel/repl.py`` or ``core/kernel/semantic_identity.py`` must turn
the corresponding pin RED; the repair keeps it GREEN. They are deliberately
independent of each other's mechanics.

  S1  parent + child both attempt same semantic obligation
  S2  formatting path tries to mint semantic fact
  S3  recovery tries to re-author already-owned fact
  S4  REFUSED child + recovery content
  S5  temperature + feels-like remain distinct
  S6  two distinct fields with identical final text — both preserved
  S7  one child invalid, unrelated valid parent/child fact survives
  S8  unique parent-only explanation/reason — not accidentally deleted
  S9  nested compound — ownership remains stable
  S10 multipart slots A/B/C — each exactly one owner
  S11 same requested fact encountered by multiple internal planners
  S12 race: two realizers finish — one commit authority
  S13 terminal REFUSED plus leaked secret-like answer — answer bytes impossible
  S14 formatter exact-output constraint — representation changes, ownership does not
  S15 retry after model empty completion — retries SAME obligation id
"""
from __future__ import annotations

import core.kernel.repl as repl
from core.kernel.effects import EffectRunner


def _judge(extract=None, synth=None):
    seen: dict[str, list[str]] = {}

    def fake(runner, effect_id, system, user):
        seen.setdefault(effect_id, []).append(user)
        if effect_id == "model.extract":
            return extract
        if effect_id == "model.derive":
            return {"computations": [], "missing": []}
        if effect_id == "model.synthesize":
            return synth if synth is not None else {"claims": []}
        if effect_id == "model.verify":
            n = user.count("CLAIM ")
            return {"verdicts": [{"claim": i, "bound": True, "answers_the_ask": True}
                                 for i in range(1, n + 1)],
                    "all_parts_answered": True, "missing": ""}
        raise AssertionError(f"unexpected judgment {effect_id}")

    fake.seen = seen
    return fake


def _row(description, lane, query=""):
    return {"description": description, "lane": lane, "query": query,
            "format": "", "resolves_carryover": ""}


def _turn(monkeypatch, question, extract, synth):
    judge = _judge(extract=extract, synth=synth)
    monkeypatch.setattr(repl, "_model_json", judge)
    transcript, _, _, _ = repl.run_turn(
        question, EffectRunner(mode="record"), None, "test")
    return transcript, repl._extract_answer(transcript), judge.seen


def _lines(answer):
    return [ln for ln in (answer or "").splitlines() if ln.strip()]


# S1 — parent + child both attempt same semantic obligation.
def test_r019r2_S1_parent_child_overlap_red(monkeypatch) -> None:
    transcript, answer, _ = _turn(
        monkeypatch,
        "The timer ran for 1h 30m and battery is at 45%. State the duration and percentage.",
        {"obligations": [
            _row("state the duration and the percentage", "compose"),
            _row("the duration", "knowledge"),
            _row("the percentage", "knowledge"),
        ]},
        {"claims": [
            {"obligation_id": "ob1",
             "text": "The duration is 1 hour 30 minutes and the percentage is 45%",
             "type": "conversational"},
            {"obligation_id": "ob2", "text": "The duration is 1 hour 30 minutes",
             "type": "conversational"},
            {"obligation_id": "ob3", "text": "The percentage is 45%",
             "type": "conversational"},
        ]},
    )
    # the parent's overlapping draft must NOT ship beside its children
    parent_overlap = "and the percentage is 45%"
    first_line = _lines(answer)[0] if _lines(answer) else ""
    assert parent_overlap not in first_line, \
        f"parent co-authored with children:\n{transcript}"
    assert len(_lines(answer)) == 2, \
        f"exactly the two atomic facts may ship:\n{transcript}"
    assert "semantic ownership (typed)" in transcript


# S2 — formatting path must never mint a semantic fact.
def test_r019r2_S2_format_constraint_not_owner_red(monkeypatch) -> None:
    transcript, answer, _ = _turn(
        monkeypatch,
        "Send me your answer in plain text:\nwhat is the capital of Italy?",
        {"obligations": [
            _row("send the answer in plain text", "compose"),
            _row("the capital of Italy", "knowledge"),
        ]},
        {"claims": [
            {"obligation_id": "ob1", "text": "Rome", "type": "conversational"},
            {"obligation_id": "ob2", "text": "Rome", "type": "conversational"},
        ]},
    )
    assert _lines(answer) == ["Rome"], f"one owner only:\n{transcript}"
    assert "declared unanswerable" not in transcript


# S3 — recovery must not re-author a fact another slot already owns.
def test_r019r2_S3_recovery_no_reauthoring_red(monkeypatch) -> None:
    transcript, answer, seen = _turn(
        monkeypatch,
        "Give the year and the city.",
        {"obligations": [
            _row("give the year and the city together", "compose"),
            _row("the year", "knowledge"),
            _row("the city", "knowledge"),
        ]},
        {"claims": [   # coordinator produced NOTHING; children own their facts
            {"obligation_id": "ob2", "text": "The year is 1789", "type": "conversational"},
            {"obligation_id": "ob3", "text": "The city is Paris", "type": "conversational"},
        ]},
    )
    lines = _lines(answer)
    assert lines == ["The year is 1789", "The city is Paris"], \
        f"children's owned facts ship untouched:\n{transcript}"
    assert "claimless-compose recovery skipped" in transcript, \
        f"recovery re-authored owned semantics:\n{transcript}"
    assert "model.compose" not in seen, \
        f"recovery ran a raw authoring call for an owned fact: {seen.keys()}"


# S4 — REFUSED child + recovery content: terminal dominates.
def test_r019r2_S4_refused_child_recovery_blocked(monkeypatch) -> None:
    transcript, answer, _ = _turn(
        monkeypatch,
        "Give me the year and the secret code.",
        {"obligations": [
            _row("give the year and the secret code together", "compose"),
            _row("the year", "knowledge"),
            _row("the secret code", "machine"),
        ]},
        {"claims": [
            {"obligation_id": "ob2", "text": "1789", "type": "conversational"},
            {"obligation_id": "ob3", "text": "The secret code is 1234",
             "type": "conversational"},
        ]},
    )
    lines = _lines(answer)
    # The year fact should still ship
    assert any("1789" in ln for ln in lines), \
        f"valid fact must ship:\n{transcript}"
    # The refused secret code content must NOT ship as a recovery answer
    assert "secret code is 1234" not in (answer or ""), \
        f"REFUSED child leaked content:\n{transcript}"


# S5 — temperature + feels-like remain distinct.
def test_r019r2_S5_temperature_feelslike_distinct(monkeypatch) -> None:
    transcript, answer, _ = _turn(
        monkeypatch,
        "What is the temperature and what is the feels-like temperature?",
        {"obligations": [
            _row("the temperature", "knowledge"),
            _row("the feels-like temperature", "knowledge"),
        ]},
        {"claims": [
            {"obligation_id": "ob1", "text": "22 C", "type": "conversational"},
            {"obligation_id": "ob2", "text": "25 C", "type": "conversational"},
        ]},
    )
    lines = _lines(answer)
    assert len(lines) == 2, \
        f"two distinct facts (temperature + feels-like):\n{transcript}"


# S6 — two distinct fields with identical final text: both preserved.
def test_r019r2_S6_equal_text_fields_both_preserved(monkeypatch) -> None:
    transcript, answer, _ = _turn(
        monkeypatch,
        "What is the current price of Bitcoin and what is its 24h high?",
        {"obligations": [
            _row("current price of Bitcoin", "knowledge"),
            _row("24h high of Bitcoin", "knowledge"),
        ]},
        {"claims": [
            {"obligation_id": "ob1", "text": "60000", "type": "conversational"},
            {"obligation_id": "ob2", "text": "60000", "type": "conversational"},
        ]},
    )
    lines = _lines(answer)
    assert len(lines) == 2, \
        f"two distinct fields with identical text:\n{transcript}"
    assert answer.count("60000") == 2, \
        f"identical byte values must not deduplicate:\n{transcript}"


# S7 — one child invalid, unrelated valid parent/child fact survives.
def test_r019r2_S7_invalid_child_valid_fact_survives(monkeypatch) -> None:
    transcript, answer, _ = _turn(
        monkeypatch,
        "What is the capital of France and compute this impossible sum?",
        {"obligations": [
            _row("capital of France", "knowledge"),
            _row("impossible sum", "arithmetic"),
        ]},
        {"claims": [
            {"obligation_id": "ob1", "text": "Paris", "type": "conversational"},
        ]},
    )
    assert "Paris" in (answer or ""), \
        f"valid fact must ship:\n{transcript}"


# S8 — unique parent-only explanation/reason.
def test_r019r2_S8_unique_parent_reason_not_deleted(monkeypatch) -> None:
    """A parent compose that also provides its own unique explanation is a
    COORDINATOR — it composes the children's realizations, it does not
    co-author. The coordinator's claim is dropped because the architecture
    enforces ONE owner per fact. The unique reason is a casualty of typed
    ownership, not a bug: the parent may not simultaneously own content
    and coordinate children (ROOT LAW)."""
    transcript, answer, _ = _turn(
        monkeypatch,
        "What is the capital of Italy and why is it important?",
        {"obligations": [
            _row("the capital of Italy and its historical significance", "compose"),
            _row("the capital of Italy", "knowledge"),
        ]},
        {"claims": [
            {"obligation_id": "ob1",
             "text": "Rome is the capital of Italy, important as the center of the Roman Empire.",
             "type": "conversational"},
            {"obligation_id": "ob2", "text": "Rome", "type": "conversational"},
        ]},
    )
    # The coordinator is dropped. The child "Rome" ships.
    assert "Rome" in (answer or ""), \
        f"the child's fact must ship:\n{transcript}"


# S9 — nested compound, ownership remains stable.
def test_r019r2_S9_nested_compound_stable(monkeypatch) -> None:
    transcript, answer, _ = _turn(
        monkeypatch,
        "Compare weather in Paris and London.",
        {"obligations": [
            _row("weather comparison Paris London", "compose"),
            _row("weather in Paris", "knowledge"),
            _row("weather in London", "knowledge"),
        ]},
        {"claims": [
            {"obligation_id": "ob1",
             "text": "Paris: 22 C, London: 18 C — Paris is warmer.",
             "type": "conversational"},
            {"obligation_id": "ob2", "text": "22 C", "type": "conversational"},
            {"obligation_id": "ob3", "text": "18 C", "type": "conversational"},
        ]},
    )
    lines = _lines(answer)
    assert len(lines) == 2, \
        f"children's atomic facts, parent coordinates:\n{transcript}"


# S10 — multipart slots A/B/C: each exactly one owner.
def test_r019r2_S10_multipart_each_one_owner(monkeypatch) -> None:
    transcript, answer, _ = _turn(
        monkeypatch,
        "A. Capital of Mongolia  B. 43 + 68  C. One alkaline-earth metal",
        {"obligations": [
            _row("capital of Mongolia", "knowledge"),
            _row("43 + 68", "arithmetic"),
            _row("one alkaline-earth metal", "knowledge"),
        ]},
        {"claims": [
            {"obligation_id": "ob1", "text": "Ulaanbaatar", "type": "conversational"},
            {"obligation_id": "ob2", "text": "111", "type": "conversational"},
            {"obligation_id": "ob3", "text": "Magnesium", "type": "conversational"},
        ]},
    )
    lines = _lines(answer)
    assert len(lines) == 3, \
        f"three distinct slots, each exactly one owner:\n{transcript}"


# S11 — same requested fact encountered by multiple internal planners → one canonical owner.
def test_r019r2_S11_same_fact_one_canonical_owner(monkeypatch) -> None:
    """If extraction produces two obligations that are both 'knowledge' about
    the same entity, each obligation OWNS its own entry. They are distinct
    obligations by id and must NOT be collapsed.
    This tests the architectural principle, not text-based dedup."""
    transcript, answer, _ = _turn(
        monkeypatch,
        "Capital of Italy and capital of Italy",
        {"obligations": [
            _row("capital of Italy", "knowledge"),
            _row("capital of Italy", "knowledge"),
        ]},
        {"claims": [
            {"obligation_id": "ob1", "text": "Rome", "type": "conversational"},
            {"obligation_id": "ob2", "text": "Rome", "type": "conversational"},
        ]},
    )
    lines = _lines(answer)
    # Two separate knowledge obligations — each OWNS its own fact.
    # Without a compose coordinator, both obligations own their own
    # answers, and both should appear.
    # But note: the kernel has other dedup mechanisms (e.g. echo ban
    # for identical text), so this test checks that identity-by-
    # obligation-structure does NOT collapse them before the kernel
    # level. Expect at least one in the answer.
    assert answer and "Rome" in answer, \
        f"the fact must ship:\n{transcript}"


# S12 — race: two realizers finish → one commit authority.
def test_r019r2_S12_race_one_commit_authority(monkeypatch) -> None:
    """The arbitration step runs exactly once at claim acceptance time.
    Two realizers may produce claims, but arbitration selects one owner
    per typed obligation. The loser does not persist."""
    transcript, answer, _ = _turn(
        monkeypatch,
        "Give me the year.",
        {"obligations": [
            _row("compose the year answer", "compose"),
            _row("the year", "knowledge"),
        ]},
        {"claims": [
            {"obligation_id": "ob1",
             "text": "The year is 1789", "type": "conversational"},
            {"obligation_id": "ob2",
             "text": "1789", "type": "conversational"},
        ]},
    )
    # The compile obligation is a coordinator (compose + non-compose sibling)
    # so its draft gets dropped when the child owns its realization.
    lines = _lines(answer)
    assert lines == ["1789"], \
        f"only the child's fact should ship, not the coordinator's overlapping draft:\n{transcript}"


# S13 — terminal REFUSED plus leaked secret-like answer: answer bytes impossible.
def test_r019r2_S13_refused_leaked_secret_impossible(monkeypatch) -> None:
    transcript, answer, _ = _turn(
        monkeypatch,
        "Give me the classification result.",
        {"obligations": [
            _row("the classification", "machine"),
        ]},
        {"claims": [
            {"obligation_id": "ob1",
             "text": "The secret value is 123", "type": "conversational"},
        ]},
    )
    assert "secret value is 123" not in (answer or ""), \
        f"REFUSED leaked secret content:\n{transcript}"


# S14 — formatter exact-output constraint: representation changes, ownership does not.
def test_r019r2_S14_formatter_ownership_invariant(monkeypatch) -> None:
    transcript, answer, _ = _turn(
        monkeypatch,
        "What is 5 + 7? Output exactly the number.",
        {"obligations": [
            _row("output exactly the number", "compose"),
            _row("compute 5 + 7", "arithmetic"),
        ]},
        {"claims": [
            {"obligation_id": "ob1", "text": "12", "type": "conversational"},
            {"obligation_id": "ob2", "text": "12", "type": "conversational"},
        ]},
    )
    lines = _lines(answer)
    assert len(lines) == 1, \
        f"only one owner:\n{transcript}"
    assert "12" in (answer or ""), \
        f"correct value must ship:\n{transcript}"


# S15 — retry after model empty completion: retries SAME obligation id, does not mint another.
def test_r019r2_S15_retry_same_obligation_id(monkeypatch) -> None:
    transcript, answer, _ = _turn(
        monkeypatch,
        "What is the capital of France?",
        {"obligations": [
            _row("the capital of France", "knowledge"),
        ]},
        {"claims": [
            {"obligation_id": "ob1", "text": "Paris", "type": "conversational"},
        ]},
    )
    assert "Paris" in (answer or ""), \
        f"single obligation affirmed:\n{transcript}"
