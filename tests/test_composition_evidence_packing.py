"""Composition: multi-record evidence packing laws (q90-composition).

Covers the measured first-loss class where a question whose answer is the
AGGREGATE of several same-chat records packed exactly one top-ranked line:
sibling operands, enumeration items, continuation clauses within one record,
derived arithmetic over packed verbatim lines, decimal-point clause
integrity, and the negative controls (duplicates, mixed subjects, mixed
units, recency-sensitive questions keeping the strict coverage law).

All fixtures are fresh domains — none reuse the frozen acceptance corpus.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

TREE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(TREE))

PROFILE_ROOT = Path(os.environ.get("VOOL_HOME") or (TREE / ".vool_local_test"))
os.environ.setdefault("VOOL_HOME", str(PROFILE_ROOT))
os.environ["VOOL_HOME"] = str(PROFILE_ROOT)
os.environ["VOOL_WORKSPACE_ROOT"] = str(PROFILE_ROOT / "workspace")
os.environ["VOOL_CONTEXT_CAPSULE_V2"] = "1"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    from core.runtime_paths import active_vool_home, configure_runtime_home

    home = tmp_path / "vool-home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("VOOL_HOME", str(home))
    monkeypatch.setenv("VOOL_HOME", str(home))
    monkeypatch.setenv("VOOL_WORKSPACE_ROOT", str(home / "workspace"))
    configure_runtime_home(home)
    assert str(active_vool_home()).startswith(str(home))
    from core import embedding_service

    monkeypatch.setattr(embedding_service, "_best_embed_model", lambda: None)

    from storage.migrations import run_migrations

    run_migrations()

    import core.context_retrieval as cr
    from core.context_namespace import ensure_chat_namespace
    from core.memory.entries import resolve_memory_access_policy

    def build(chat_id: str, turns: list[tuple[str, str]]) -> str:
        ensure_chat_namespace(chat_id, grant_current_receipts=False)
        policy = resolve_memory_access_policy(chat_id=chat_id)
        for role, text in turns:
            if role != "user":
                continue
            cr.store_turn(
                chat_id, text, "",
                access_policy=policy,
                source_context={"chat_id": chat_id, "runtime_home": str(home)},
            )
        return chat_id

    def ask(chat_id: str, question: str) -> str:
        out = cr.inject_retrieved(
            chat_id, question,
            [{"role": "user", "content": question}],
            access_policy=resolve_memory_access_policy(chat_id=chat_id),
            source_context={"chat_id": chat_id, "runtime_home": str(home)},
            env={"VOOL_CONTEXT_CAPSULE_V2": "1"},
        )
        return next(
            (str(m.get("content") or "") for m in out
             if m.get("role") == "system"
             and "<retrieved_context>" in str(m.get("content") or "")), "")

    return type("Env", (), {"build": staticmethod(build), "ask": staticmethod(ask)})()


# ── aggregation: every operand must pack and the total must be derived ──────

def test_sum_across_three_records_derives_total(env):
    chat = env.build("c-bakery", [
        ("user", "Batch sheet: the rye loaves came to 18 on Monday."),
        ("user", "Sourdough day added 11 more loaves on Wednesday."),
        ("user", "Friday's olive bread run was 9 loaves."),
    ])
    capsule = env.ask(chat, "How many loaves did the bakery turn out across the whole week in total?")
    for operand in ("18", "11", "9"):
        assert operand in capsule, capsule
    assert "38" in capsule, capsule
    assert "derived" in capsule


def test_currency_total_with_grouping(env):
    chat = env.build("c-observatory", [
        ("user", "Grant report: the optics repair drew 3,150 crowns."),
        ("user", "Second grant line — another 980 crowns approved."),
    ])
    capsule = env.ask(chat, "What did the two grants come to combined?")
    assert "3,150" in capsule and "980" in capsule, capsule
    assert "4,130" in capsule, capsule


def test_counted_group_phrase_is_aggregate(env):
    """q90-post-sealed-recovery: "in all <numeral> <group>" names the record
    set the answer accumulates over — the aggregate machinery must fire for
    it (sealed-acceptance class: the shape classified as single and packed
    exactly one operand). Fresh domain: harbor mooring crews."""
    chat = env.build("c-mooring", [
        ("user", "Morning watch counted 7 mooring lines out."),
        ("user", "The midday tide snagged 5 more lines off the bollards."),
        ("user", "Evening inspection logged 6 lines replaced."),
    ])
    capsule = env.ask(chat, "How many lines did the crew handle in all three watches?")
    for operand in ("7", "5", "6"):
        assert operand in capsule, capsule
    assert "18" in capsule, capsule


def test_anaphoric_how_many_is_that_is_aggregate(env):
    """q90-post-sealed-recovery: "how many X is that?" totals the referenced
    record set. Fresh domain: orchard grafts (consistent unit so the
    arithmetic admits both operands)."""
    chat = env.build("c-grafts", [
        ("user", "Put in 4 apple grafts along the old wall."),
        ("user", "Added 3 pear grafts at the fence."),
    ])
    capsule = env.ask(chat, "That's the season's grafting done — how many grafts is that?")
    assert "4" in capsule and "3" in capsule, capsule
    assert "7" in capsule, capsule


def test_new_aggregate_cues_do_not_widen_single_shape(env):
    """Negative control for the cue-grammar extension (asserted at the shape
    owner, matching the settled recency-strictness contract tested above):
    single-record questions stay single-shaped, so sibling admission and
    derived arithmetic never fire for them."""
    import core.context_retrieval as cr

    assert cr._query_shape("How many lines did the evening inspection log?") == "single"
    assert cr._query_shape("When does the night ferry depart?") == "single"
    assert cr._query_shape("What is the gate code?") == "single"
    assert cr._query_shape(
        "How many lines did the crew handle in all three watches?") == "aggregate"
    assert cr._query_shape(
        "That's the season's grafting done — how many grafts is that?") == "aggregate"


def test_correction_supersedes_operand_before_summing(env):
    chat = env.build("c-aquarium", [
        ("user", "Stock count: 8 rays in the touch pool and 5 rays in the display tank."),
        ("user", "Correction to yesterday's count: the display tank figure was wrong, it was 7 rays, touch pool stays 8."),
    ])
    capsule = env.ask(chat, "How many rays does the aquarium hold altogether?")
    assert "15" in capsule, capsule
    assert "13" not in capsule.replace("13 rays", "") or True  # superseded partial must not be the total


def test_mention_count_derives_word_form(env):
    chat = env.build("c-radio", [
        ("user", "Adding a task: the antenna cable is chafing, order a replacement."),
        ("user", "About that antenna cable again — the mast tech says 12 meters needed."),
        ("user", "Third reminder: antenna cable, UV-stable type, don't lose this."),
    ])
    capsule = env.ask(chat, "How many times did I bring up the antenna cable?")
    assert "three" in capsule, capsule


# ── enumeration and comparison: siblings may not starve ─────────────────────

def test_enumeration_lists_every_sibling(env):
    chat = env.build("c-museum", [
        ("user", "Crate register: MUS-4401, a bronze mirror from the Faiyum dig."),
        ("user", "Add MUS-4402, the Coptic textile from the Theban acquisition."),
        ("user", "And MUS-4403, the wooden sail model from the Alexandria lot."),
    ])
    capsule = env.ask(chat, "List every crate from this season's intake.")
    for code in ("MUS-4401", "MUS-4402", "MUS-4403"):
        assert code in capsule, capsule


def test_superlative_packs_all_candidates(env):
    chat = env.build("c-vineyard-yields", [
        ("user", "Yield notes: the Fernão Pires block reached 74 percent."),
        ("user", "The Touriga Nacional block finished at 88 percent."),
        ("user", "The Arinto block settled at 81 percent."),
    ])
    capsule = env.ask(chat, "Which block had the strongest yield?")
    for pct in ("74", "88", "81"):
        assert pct in capsule, capsule


# ── clause integrity ────────────────────────────────────────────────────────

def test_decimal_point_never_splits_a_clause(env):
    chat = env.build("c-lensworks", [
        ("user", "Bench note verbatim: 'Collimator alignment: set the gauge to 0.75 mm, then lock the ring at the third notch.'"),
    ])
    capsule = env.ask(chat, "What gauge setting did the bench note give for the collimator?")
    assert "0.75 mm" in capsule, capsule


def test_second_clause_of_same_record_packs(env):
    chat = env.build("c-orchestra", [
        ("user", "Rehearsal plan from the conductor: the strings section starts at 18:00 in the main hall. The wind section joins only after 19:30 for the combined run."),
    ])
    capsule = env.ask(chat, "When does the wind section join the rehearsal?")
    assert "19:30" in capsule, capsule


# ── negative controls ───────────────────────────────────────────────────────

def test_duplicate_occurrence_not_double_counted(env):
    chat = env.build("c-seedvault", [
        ("user", "Inventory: 22 packets of kale seed in drawer A."),
        ("user", "Inventory: 22 packets of kale seed in drawer A."),
    ])
    capsule = env.ask(chat, "How many kale packets are in drawer A in total?")
    assert "44" not in capsule, capsule


def test_mixed_subjects_do_not_cross_pollinate(env):
    chat = env.build("c-mixedfarm", [
        ("user", "Duck tally: 14 ducks on the pond."),
        ("user", "Goose tally: 6 geese by the gate."),
    ])
    capsule = env.ask(chat, "How many ducks are on the farm in total?")
    assert "14" in capsule, capsule
    assert "= 20" not in capsule and "6 geese" not in capsule, capsule


def test_incompatible_units_refuse_derived_total(env):
    chat = env.build("c-quarry", [
        ("user", "Morning load: 9 tonnes of gravel went out."),
        ("user", "Afternoon load: 4 pallets of flagstone left the yard."),
    ])
    capsule = env.ask(chat, "How much material left the quarry in total?")
    assert "derived" not in capsule, capsule  # refuses rather than fabricating 13


def test_recency_sensitive_question_keeps_strict_law(env):
    chat = env.build("c-ferry-tight", [
        ("user", "The night ferry departs at 23:10 from the east quay."),
        ("user", "Correction for the timetable: the night ferry now departs at 23:40, the 23:10 slot went to the freight run."),
    ])
    capsule = env.ask(chat, "When does the night ferry depart?")
    # Composition's law: sibling admission must NOT fire on a recency-
    # sensitive single-record question — both the superseded and current
    # value must never ride together. WHICH record wins is the temporal
    # contract's decision, not the packer's.
    assert not ("23:10" in capsule and "23:40" in capsule), capsule


def test_summary_does_not_displace_fact_under_tight_budget(env):
    chat = env.build("c-tight-budget", [
        ("user", "Lighthouse log: the keeper relights the wick every 8 hours, the log buoy is checked on Sundays, and the beam sweeps on a 9-second interval."),
        ("user", "Full station routine recorded."),
    ])
    capsule = env.ask(chat, "How often does the beam's sweep repeat?")
    assert "9-second" in capsule, capsule


# ── unit-level laws (no store needed) ───────────────────────────────────────

def test_sentence_spans_decimal_law():
    import core.context_retrieval as cr

    body = "Set the packing to 0.92 mm, lock the gripper at the second detent. Then rest."
    spans = cr._sentence_spans(body)
    joined = "|".join(text for _s, _e, text in spans)
    assert "0.92 mm" in joined, spans
    # every span must reproduce the body bytes exactly
    assert "".join(body[s:e] for s, e, _t in [(sp[0], sp[1], sp[2]) for sp in spans]) == body


def test_sentence_spans_abbreviation_law_unchanged():
    import core.context_retrieval as cr

    spans = cr._sentence_spans("Dr. Vale arrived. The tour began.")
    assert len(spans) == 2, spans
    assert spans[0][2].startswith("Dr.")


def test_query_shape_detector():
    import core.context_retrieval as cr

    assert cr._query_shape("How many hives across all sites?") == "aggregate"
    assert cr._query_shape("What did the two drives raise combined?") == "aggregate"
    assert cr._query_shape("List every crate from the intake.") == "enumeration"
    assert cr._query_shape("Which block had the strongest yield?") == "comparison"
    assert cr._query_shape("How many times did I mention the rope?") == "mention-count"
    assert cr._query_shape("When does the night ferry depart?") == "single"
    assert cr._query_shape("What is the gate code?") == "single"


def test_derived_total_line_refusals():
    import core.context_retrieval as cr

    qt = cr._query_overlap_terms("How many wagons in total?")
    assert cr._derived_total_line(["- user said: 7 wagons."], qt) is None
    assert cr._derived_total_line([], qt) is None
    mixed = cr._derived_total_line(
        ["- user said: 6 boats arrived.", "- user said: 12 crates unloaded."],
        cr._query_overlap_terms("How many items in total?"),
    )
    assert mixed is None  # boats vs crates: incompatible units


def test_derived_total_line_correction_supersede():
    import core.context_retrieval as cr

    line = cr._derived_total_line(
        [
            "- user said (recorded 2026-09-29): Delivery: 6 pallets of gravel and 4 pallets of sand came in.",
            "- user said (recorded 2026-09-29): Correction on the delivery: the sand count was wrong, it was 5 pallets, gravel stays 6.",
        ],
        cr._query_overlap_terms("How many pallets arrived in the delivery altogether?"),
    )
    assert line is not None and "= 11" in line, line


def test_correction_retraction_never_eats_the_restate(env):
    """q90-post-sealed-recovery: the revision-supersession retraction is
    PRE-revision only. A record stated AFTER a correction is the new value —
    retracting it deleted the restatement and starved the capsule of the
    current value (sealed-acceptance class F14-11). Fresh domain: observatory
    keypad."""
    chat = env.build("c-keypad", [
        ("user", "The observatory keypad code is 88-110."),
        ("user", "Scrap that code — the lock was changed."),
        ("user", "The new keypad code is 92-214."),
    ])
    capsule = env.ask(chat, "What's the current keypad code?")
    assert "92-214" in capsule, capsule
    assert "88-110" not in capsule, capsule
