"""The date-anchored time leg follows the evidence allowance.

A question naming a calendar period ("Where was the apiary in July 2024?") reads the records stated
inside that period. The leg read at most 8 records whatever the caller's evidence allowance, while
the lexical, semantic and node legs follow the allowance-tied item limit (one item per 256 tokens,
floored at 8, capped at 32): a month-grain question saw 8 records of a whole month (measured on
the fork-v5 wiring audit: every date question hit the cap). Law: the leg reads up to the same
allowance-tied item limit; the historical default allowance still reads 8.

All records are synthetic.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tests.test_question_date_time_leg_20261002 import (
    _hash_backend,
    _ingest,
    _profile,
)

UTC = timezone.utc


def _ts(text: str) -> float:
    return datetime.fromisoformat(text).replace(tzinfo=UTC).timestamp()


def _wide_capsule(profile: Path, chat: str, question: str, target_tokens: int = 420):
    from core.context_capsule_v2 import resolve_budget
    from core.context_namespace import ensure_chat_namespace
    from core.context_retrieval import get_last_retrieval_telemetry, inject_retrieved
    from core.memory.entries import resolve_memory_access_policy

    ensure_chat_namespace(chat, grant_current_receipts=False)
    policy = resolve_memory_access_policy(chat_id=chat)
    budget = replace(
        resolve_budget(bucket="D", role="heavy_reasoning", output_reserve_tokens=2048,
                       evidence_target_tokens=target_tokens, retrieval_ceiling_tokens=8192),
        min_score=0.25)
    messages = inject_retrieved(
        chat, question, [{"role": "user", "content": question}],
        access_policy=policy,
        source_context={"chat_id": chat, "runtime_home": str(profile)},
        env={"VOOL_CONTEXT_CAPSULE_V2": "1"},
        budget=budget,
    )
    capsule = next((str(m.get("content") or "") for m in messages
                    if "<retrieved_context>" in str(m.get("content") or "")), "")
    return capsule, get_last_retrieval_telemetry()


# ───────────────────────── time leg follows the allowance ─────────────────

def _month_log() -> list[tuple[float, str]]:
    # eleven same-month records that share no word with the question, then
    # the answer record (also no shared word): all sit inside the named
    # month, so the leg orders them by capture order and the answer is 12th
    turns = [(_ts(f"2024-07-{day:02d}T09:00:00"), f"Checked the hive frames and wrote up note number {day}.")
             for day in range(1, 12)]
    turns.append((_ts("2024-07-20T09:00:00"), "Moved the bee yard to the clover field by the old mill."))
    turns.append((_ts("2024-02-01T09:00:00"), "Where should the apiary go for the season? Still deciding."))
    return turns


@pytest.mark.usefixtures("_hash_backend")
def test_month_question_time_leg_reads_past_eight_records(tmp_path):
    profile = _profile(tmp_path)
    _ingest(profile, "apiary", _month_log())
    capsule, telemetry = _wide_capsule(
        profile, "apiary", "Where was the apiary in July 2024?", target_tokens=8192)
    leg = telemetry.get("evidence_time_leg")
    assert leg and leg["grain"] == "month", leg
    assert leg["records"] > 8, leg
    assert "clover field" in capsule, capsule


@pytest.mark.usefixtures("_hash_backend")
def test_default_allowance_time_leg_keeps_its_eight(tmp_path):
    # control: the historical default allowance still reads 8
    profile = _profile(tmp_path)
    _ingest(profile, "apiary", _month_log())
    _capsule_text, telemetry = _wide_capsule(
        profile, "apiary", "Where was the apiary in July 2024?", target_tokens=420)
    leg = telemetry.get("evidence_time_leg")
    assert leg and leg["records"] == 8, leg
