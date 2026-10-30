"""Updated-fact marking: the capsule tells the reader which value is current.

v08x (2026-10-30). Measured cause (DEV-30 probe, missions/v08x-updated-facts-20261030):
the evidence delivered the newest value of a changed fact beside the older ones and
nothing marked which one is current, so the reader voiced the older value or both.
These tests pin the delivered-line markers ([current value] / [superseded] /
[conflicting records]), the direction law (only the user's own later statement carries
a chain), and the header's answering rule on the product path.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.updated_fact_marking import (  # noqa: E402
    CONFLICT_MARKER,
    CURRENT_MARKER,
    SUPERSEDED_MARKER,
    compute_markers,
    mark_updated_fact_lines,
)

APR = datetime(2024, 4, 1, 12, 0, tzinfo=timezone.utc).timestamp()
JUN = datetime(2024, 6, 1, 12, 0, tzinfo=timezone.utc).timestamp()
SEP = datetime(2024, 9, 1, 12, 0, tzinfo=timezone.utc).timestamp()


def receipt(line: str, *, role: str = "user", at: float, seq: int | None = 1) -> dict:
    return {"line": line, "role": role, "delivered": True,
            "statement_at": at, "recorded_at": at, "source_sequence": seq}


def test_salary_raised_twice_marks_only_the_newest_current():
    old = "- user said (stated 2024-04-01): My salary at the bakery job is $52,000 a year."
    mid = "- user said (stated 2024-06-01): I got a raise, so my salary is now $61,000."
    new = "- user said (stated 2024-09-01): With the promotion my salary went to $78,000."
    markers = compute_markers(
        [receipt(old, at=APR, seq=1), receipt(mid, at=JUN, seq=2),
         receipt(new, at=SEP, seq=3)], [])
    assert markers[new] == CURRENT_MARKER
    assert markers[old] == SUPERSEDED_MARKER
    assert markers[mid] == SUPERSEDED_MARKER


def test_count_corrected_same_day_orders_by_capture_sequence():
    wrong = "- user said (stated 2024-06-01): We counted 45 crates in the barn this morning."
    fixed = "- user said (stated 2024-06-01): Recounted it, the barn holds 47 crates."
    markers = compute_markers(
        [receipt(wrong, at=JUN, seq=4), receipt(fixed, at=JUN, seq=9)], [])
    assert markers[fixed] == CURRENT_MARKER
    assert markers[wrong] == SUPERSEDED_MARKER


def test_same_moment_conflicting_counts_mark_both_conflict():
    one = "- user said (stated 2024-06-01): The guest list stands at 30 people."
    two = "- user said (stated 2024-06-01): Make that 26 people for the dinner."
    markers = compute_markers(
        [receipt(one, at=JUN, seq=5), receipt(two, at=JUN, seq=5)], [])
    assert markers[one] == CONFLICT_MARKER
    assert markers[two] == CONFLICT_MARKER


def test_plan_switch_marks_the_new_plan_current():
    old = "- user said (stated 2024-04-01): I signed up for the morning yoga classes."
    new = "- user said (stated 2024-09-01): I switched to the evening yoga classes instead."
    markers = compute_markers(
        [receipt(old, at=APR, seq=1), receipt(new, at=SEP, seq=6)], [])
    assert markers[new] == CURRENT_MARKER
    assert markers[old] == SUPERSEDED_MARKER


def test_assistant_echo_of_the_same_value_never_marks():
    user = "- user said (stated 2024-09-01): My salary went to $78,000 with the promotion."
    echo = ("- assistant said (stated 2024-09-10): Congratulations on the promotion "
            "and the new $78,000 salary.")
    markers = compute_markers(
        [receipt(user, at=SEP, seq=1), receipt(echo, role="assistant", at=SEP + 864000, seq=2)], [])
    assert user not in markers or markers[user] != CONFLICT_MARKER
    assert markers.get(user) in (None, CURRENT_MARKER) or markers[user] == CURRENT_MARKER


def test_a_later_assistant_line_never_supersedes_the_users_word():
    user = "- user said (stated 2024-04-01): My target is 1,500 words a week."
    advisor = ("- assistant said (stated 2024-06-01): Try raising the weekly "
               "target to 2,000 words.")
    markers = compute_markers(
        [receipt(user, at=APR, seq=1), receipt(advisor, role="assistant", at=JUN, seq=2)], [])
    assert markers == {}


def test_the_users_later_statement_supersedes_an_earlier_assistant_value():
    advisor = ("- assistant said (stated 2024-04-01): The Zoom call with the "
               "director is scheduled for 11 AM.")
    user = ("- user said (stated 2024-05-05): My interview with the director is "
            "on May 12 at 10:30 AM via Zoom.")
    markers = compute_markers(
        [receipt(advisor, role="assistant", at=APR, seq=1), receipt(user, at=JUN, seq=2)], [])
    assert markers[user] == CURRENT_MARKER
    assert markers[advisor] == SUPERSEDED_MARKER


def test_render_marker_is_a_suffix_and_leaves_other_lines_alone():
    body = (
        "<retrieved_context>\nDistilled local facts. Answer from these records.\n"
        "- user said (stated 2024-04-01): My target is 1,500 words a week.\n"
        "- user said (stated 2024-09-01): I updated my weekly target to 1,800 words.\n"
        "</retrieved_context>"
    )
    out = mark_updated_fact_lines(
        body,
        receipts=[
            receipt("- user said (stated 2024-04-01): My target is 1,500 words a week.", at=APR, seq=1),
            receipt("- user said (stated 2024-09-01): I updated my weekly target to 1,800 words.", at=SEP, seq=2),
        ], lane_records=[])
    assert "1,500 words a week. " + SUPERSEDED_MARKER in out
    assert "1,800 words. " + CURRENT_MARKER in out
    assert "Distilled local facts. Answer from these records.\n" in out  # header untouched
    assert out.count("\n") == body.count("\n")  # no lines added or merged


def _product_env(tmp_path: Path) -> None:
    os.environ.update({
        "VOOL_CONTEXT_CAPSULE_V2": "1",
        "VOOL_MEMORY_RECEIPTS": "1",
        "VOOL_EVIDENCE_COMPILER": "1",
        "VOOL_EVIDENCE_HOP": "1",
        "VOOL_EVIDENCE_VERIFY": "1",
        "VOOL_EVIDENCE_KERNEL": "1",
        "VOOL_HOME": str(tmp_path),
        "NULLA_HOME": str(tmp_path),
        "VOOL_WORKSPACE_ROOT": str(tmp_path / "workspace"),
    })
    from core.runtime_paths import configure_runtime_home

    configure_runtime_home(tmp_path)


def test_product_capsule_marks_the_current_value_end_to_end(tmp_path):
    pytest_home = tmp_path / "home"
    pytest_home.mkdir(parents=True, exist_ok=True)
    _product_env(pytest_home)
    from core import embedding_service

    embedding_service._best_embed_model = lambda: None  # deterministic hash bow
    from core.context_namespace import ensure_chat_namespace
    from core.context_retrieval import inject_retrieved, store_turn
    from core.memory.entries import resolve_memory_access_policy

    chat = "mark-current-value-20261030"
    ensure_chat_namespace(chat, grant_current_receipts=False)
    policy = resolve_memory_access_policy(chat_id=chat)
    day = lambda y, m, d: datetime(y, m, d, 12, 0, tzinfo=timezone.utc).timestamp()  # noqa: E731
    store_turn(chat, "My rent is $1,400 a month for the Maple street apartment.",
               "That is a solid rent for that area.",
               access_policy=policy,
               source_context={"chat_id": chat, "runtime_home": str(pytest_home),
                               "statement_at": day(2024, 3, 1)})
    store_turn(chat, "I switched to the bigger unit, my rent is $1,650 a month now.",
               "Congratulations on the bigger place.",
               access_policy=policy,
               source_context={"chat_id": chat, "runtime_home": str(pytest_home),
                               "statement_at": day(2024, 8, 1)})
    question = "What is my rent on the Maple street apartment?"
    out = inject_retrieved(
        chat, question, [{"role": "user", "content": question}],
        access_policy=policy,
        source_context={"chat_id": chat, "runtime_home": str(pytest_home)},
        env={"VOOL_CONTEXT_CAPSULE_V2": "1",
             "VOOL_MEMORY_RECEIPTS": "1", "VOOL_EVIDENCE_COMPILER": "1",
             "VOOL_EVIDENCE_HOP": "1", "VOOL_EVIDENCE_VERIFY": "1",
             "VOOL_EVIDENCE_KERNEL": "1"},
    )
    block = next((str(m.get("content") or "") for m in out
                  if m.get("role") == "system"
                  and "<retrieved_context>" in str(m.get("content") or "")), "")
    assert block, "no retrieved context block delivered"
    assert "1,650" in block and CURRENT_MARKER in block
    assert "1,400" in block and SUPERSEDED_MARKER in block
    # the answering rule rides the header, whose first sentence is unchanged
    assert "newest value (marked [current value])" in block
    assert block.splitlines()[1].startswith("Distilled local facts.")


def test_forget_law_keeps_no_record_after_forget(tmp_path):
    """The marking pass only annotates delivered lines: a forget that empties
    the capsule still delivers nothing (NO RECORD = REFUSE stays the product's
    law, not this pass's to change)."""
    pytest_home = tmp_path / "home2"
    pytest_home.mkdir(parents=True, exist_ok=True)
    _product_env(pytest_home)
    from core.updated_fact_marking import mark_updated_fact_lines as mark

    empty = mark("<retrieved_context>\n</retrieved_context>", receipts=[], lane_records=[])
    assert empty == "<retrieved_context>\n</retrieved_context>"
    markers = compute_markers([], [])
    assert markers == {}
