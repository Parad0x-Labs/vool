"""Lane 1 / Case B: the past-time guard consumes the admitted capsule evidence.

The measured failure (2026-09-30 LongMemEval case q3a7331b8fd7279ce): the capsule
carried the admitted, dated fact ("using my Fitbit Charge 3 for 9 months",
stated 2023-09-02), the reader answered from it, and the post-generation past-time
guard withdrew the answer as an invented time because its evidence list was
re-derived from tiered context, hydrated history and local candidates — channels
that never contained the capsule. The repair routes the guard to the SAME admitted
capsule the provider request carried: harvested at transcript assembly
(core.bootstrap_context), session-bound, fail-soft.

Regression rows restate the captured case (development/reproduction evidence).
Fresh rows (Garmin Vivosmart 4 tracker, 14 -> 17 months, dentist-domain absent
time) were frozen with hashed expectations in the mission freeze file before
their first execution.
"""
from __future__ import annotations

import os
import shutil
import uuid
from pathlib import Path

import pytest

from core.agent_runtime.turn_reasoning import (
    _admitted_capsule_evidence_text,
    _past_time_guard_evidence,
)
from core.bootstrap_context import admitted_capsule_evidence_text
from core.model_output_guard import replace_unsupported_past_time_claims

#: Disposable homes live under pytest's per-test tmp tree (the SSD launcher
#: redirects --basetemp), so the tests carry no machine-local paths.
def _profile_root(tmp_path: Path) -> Path:
    root = tmp_path / "p300-lane1-profiles"
    root.mkdir(exist_ok=True)
    return root


def _wrap(lines: list[str]) -> str:
    return (
        "<retrieved_context>\nDistilled local facts. Answer from these exact facts only.\n"
        + "\n".join(lines)
        + "\n</retrieved_context>"
    )


FITBIT_CAPSULE = _wrap(
    [
        "- user said: I've been watching TV shows before bed, and I think that might be "
        "affecting my sleep. By the way, speaking of tracking my health, I just realized "
        "I've been using my Fitbit Charge 3 for 9 months now - it's crazy how time flies! "
        "(stated: Session date: 2023/09/02; stated: 2023-09-02)",
        "- user said: Mentioned in a question: my Fitbit Charge 3. (stated: Session date: "
        "2023/06/18; stated: 2023-06-18)",
    ]
)
FITBIT_Q = "How long have I been using my Fitbit Charge 3?"
FITBIT_REPLY = (
    "According to what you mentioned on September 2nd, you've been using your Fitbit "
    "Charge 3 for 9 months now."
)


class _StubContextResult:
    """The two attributes the evidence builder reads off the tiered context."""

    def __init__(self, assembled: str = "", local_candidates: list | None = None):
        self._assembled = assembled
        self.local_candidates = list(local_candidates or [])

    def assembled_context(self, *, prompt_profile: str = "default") -> str:
        return self._assembled


def _guard(response: str, question: str, capsule: str | None) -> str:
    source_context: dict = {
        "chat_id": "chat-1",
        "conversation_history": [{"role": "user", "content": question}],
    }
    if capsule is not None:
        source_context["admitted_capsule_evidence"] = {
            "text": capsule,
            "chat_id": "chat-1",
            "source": "canonical_runtime_transcript",
        }
    return replace_unsupported_past_time_claims(
        response,
        question=question,
        evidence_texts=_past_time_guard_evidence(
            context_result=_StubContextResult(),
            source_context=source_context,
            web_notes=[],
            session_id="chat-1",
        ),
    )


# --------------------------------------------------------------------- regression


def test_captured_supported_duration_survives_with_capsule_evidence() -> None:
    delivered = _guard(FITBIT_REPLY, FITBIT_Q, FITBIT_CAPSULE)
    assert delivered == FITBIT_REPLY


def test_captured_case_still_withdraws_without_capsule_evidence() -> None:
    # The mechanism pin: the same answer is withdrawn exactly when the admitted
    # capsule is absent — the guard's law is unchanged, only its evidence is.
    delivered = _guard(FITBIT_REPLY, FITBIT_Q, None)
    assert "9 months" not in delivered
    assert "don't have that time" in delivered.lower()


def test_evidence_builder_puts_capsule_before_history_channels() -> None:
    source_context = {
        "chat_id": "chat-1",
        "admitted_capsule_evidence": {
            "text": FITBIT_CAPSULE,
            "chat_id": "chat-1",
            "source": "canonical_runtime_transcript",
        },
        "conversation_history": [{"role": "user", "content": FITBIT_Q}],
    }
    evidence = _past_time_guard_evidence(
        context_result=_StubContextResult(assembled="Bootstrap Context\n- static"),
        source_context=source_context,
        web_notes=[],
        session_id="chat-1",
    )
    assert evidence[0] == "Bootstrap Context\n- static"
    assert evidence[1] == FITBIT_CAPSULE


def test_local_candidate_dict_summaries_reach_the_guard() -> None:
    # Legacy, explicitly unsealed assembly still reads dictionary summaries.
    # A final request seal must instead restrict support to serialized messages.
    # Declare the unsealed carrier so telemetry left by another test cannot
    # turn this legacy-interface fixture into an invalid sealed request.
    source_context = {"chat_id": "chat-1", "admitted_capsule_evidence": {
        "text": "", "chat_id": "chat-1", "source": "canonical_runtime_transcript",
    }}
    candidates = [
        {
            "shard_id": "s1",
            "summary": "user noted: the ferry crossing took 11 months to complete",
        }
    ]
    evidence = _past_time_guard_evidence(
        context_result=_StubContextResult(local_candidates=candidates),
        source_context=source_context,
        web_notes=[],
        session_id="chat-1",
    )
    assert any("11 months" in text for text in evidence)


# ------------------------------------------------- admitted-evidence authority rules


def test_reader_refuses_foreign_session_evidence() -> None:
    source_context = {
        "chat_id": "chat-1",
        "admitted_capsule_evidence": {
            "text": FITBIT_CAPSULE,
            "chat_id": "some-other-chat",
            "source": "canonical_runtime_transcript",
        },
    }
    assert admitted_capsule_evidence_text(source_context, "chat-1") == ""


def test_reader_is_fail_soft_without_evidence() -> None:
    assert admitted_capsule_evidence_text({}, "chat-1") == ""
    assert admitted_capsule_evidence_text(None, "chat-1") == ""
    assert admitted_capsule_evidence_text({"admitted_capsule_evidence": None}, "chat-1") == ""


def test_cleared_record_is_not_stale_evidence() -> None:
    # An assembly pass that injected nothing clears the record; a later consumer
    # in the same session must see "", never the previous turn's capsule.
    source_context = {"admitted_capsule_evidence": None}
    assert admitted_capsule_evidence_text(source_context, "chat-1") == ""


def test_turn_reasoning_helper_delegates_to_single_reader() -> None:
    source_context = {
        "chat_id": "chat-1",
        "admitted_capsule_evidence": {
            "text": FITBIT_CAPSULE,
            "chat_id": "chat-1",
            "source": "canonical_runtime_transcript",
        },
    }
    assert (
        _admitted_capsule_evidence_text(source_context, "chat-1")
        == admitted_capsule_evidence_text(source_context, "chat-1")
        == FITBIT_CAPSULE
    )


# ---------------------------------------------------------------------- fresh rows
# Frozen as B-F1..B-F4 in FRESH-CASES-FREEZE.json before first execution.

GARMIN_CAPSULE = _wrap(
    [
        "- user said: I've been using my Garmin Vivosmart 4 for 14 months now and the "
        "band finally broke in. (stated: Session date: 2024/03/11; stated: 2024-03-11)",
        "- user said: Quick update — I've now been using the Garmin for 17 months and "
        "the battery still holds. (stated: Session date: 2024/06/02; stated: 2024-06-02)",
    ]
)


def test_fresh_latest_duration_supported_by_admitted_capsule() -> None:
    question = "How long have I been using my Garmin Vivosmart 4?"
    reply = "By your June 2nd note, you'd been using your Garmin Vivosmart 4 for 17 months."
    assert _guard(reply, question, GARMIN_CAPSULE) == reply


def test_fresh_historical_duration_supported_by_admitted_capsule() -> None:
    question = "How long did I say I'd been using my Garmin back in March?"
    reply = "In your March 11 note you said 14 months with the Garmin Vivosmart 4."
    assert _guard(reply, question, GARMIN_CAPSULE) == reply


def test_fresh_absent_time_still_abstains_beside_admitted_capsule() -> None:
    question = "What time was my dentist appointment on Wednesday?"
    reply = "Your dentist appointment on Wednesday was at 10:15 AM."
    delivered = _guard(reply, question, GARMIN_CAPSULE)
    assert "10:15" not in delivered
    assert "don't have that time" in delivered.lower()


# ------------------------------------------------------- native store-to-transcript


@pytest.fixture()
def native_profile(monkeypatch, tmp_path):
    home = _profile_root(tmp_path) / f"bf4-{uuid.uuid4().hex[:10]}"
    home.mkdir(parents=True, exist_ok=True)
    assert home.is_relative_to(tmp_path)
    monkeypatch.setenv("VOOL_HOME", str(home))
    monkeypatch.setenv("VOOL_HOME", str(home))
    monkeypatch.setenv("VOOL_CONTEXT_CAPSULE_V2", "1")
    import core.embedding_service as embedding_service

    monkeypatch.setattr(embedding_service, "_best_embed_model", lambda: None)
    from core.runtime_paths import configure_runtime_home

    configure_runtime_home(home)
    yield home
    configure_runtime_home(None)
    shutil.rmtree(home, ignore_errors=True)


def _seed_store(home: Path) -> None:
    from storage.migrations import run_migrations

    run_migrations()
    from core.context_namespace import ensure_chat_namespace
    from core.context_retrieval import store_turn
    from core.memory.entries import resolve_memory_access_policy

    ensure_chat_namespace("own-chat", grant_current_receipts=False)
    ensure_chat_namespace("foreign-chat", grant_current_receipts=False)
    own = resolve_memory_access_policy(chat_id="own-chat")
    ctx = {"chat_id": "own-chat", "runtime_home": str(home)}
    assert (
        store_turn(
            "own-chat",
            "Please remember: Quick update — I've now been using the Garmin for "
            "17 months and the battery still holds.",
            "Noted.",
            access_policy=own,
            source_context=ctx,
        )["status"]
        == "stored"
    )
    foreign = resolve_memory_access_policy(chat_id="foreign-chat")
    assert (
        store_turn(
            "foreign-chat",
            "Please remember: my record saddle height is 78 centimetres.",
            "Noted.",
            access_policy=foreign,
            source_context={"chat_id": "foreign-chat", "runtime_home": str(home)},
        )["status"]
        == "stored"
    )


def test_native_transcript_records_session_bound_admitted_evidence(native_profile) -> None:
    _seed_store(native_profile)
    import core.bootstrap_context as bc
    from core.context_retrieval import reset_retrieval_telemetry

    reset_retrieval_telemetry()
    source_context: dict = {"chat_id": "own-chat", "runtime_home": str(native_profile)}
    transcript, _source = bc.canonical_runtime_transcript(
        session_id="own-chat",
        source_context=source_context,
        current_user_text="How long have I been using my Garmin Vivosmart 4?",
    )
    record = source_context.get("admitted_capsule_evidence")
    assert isinstance(record, dict)
    assert record["chat_id"] == "own-chat"
    assert "17 months" in str(record.get("text") or "")
    # Reader and guard saw the same material: the transcript carried the block the
    # record holds.
    assert any("<retrieved_context>" in str(m.get("content") or "") for m in transcript)


def test_native_foreign_chat_never_records_other_chat_facts(native_profile) -> None:
    _seed_store(native_profile)
    import core.bootstrap_context as bc
    from core.context_retrieval import reset_retrieval_telemetry

    reset_retrieval_telemetry()
    source_context: dict = {"chat_id": "foreign-chat", "runtime_home": str(native_profile)}
    bc.canonical_runtime_transcript(
        session_id="foreign-chat",
        source_context=source_context,
        current_user_text="How long have I been using my Garmin Vivosmart 4?",
    )
    record = source_context.get("admitted_capsule_evidence")
    record_text = str((record or {}).get("text") or "")
    assert "Garmin" not in record_text


def test_native_flag_off_records_no_evidence(native_profile, monkeypatch) -> None:
    _seed_store(native_profile)
    import core.bootstrap_context as bc
    from core.context_retrieval import reset_retrieval_telemetry

    reset_retrieval_telemetry()
    monkeypatch.delenv("VOOL_CONTEXT_CAPSULE_V2", raising=False)
    source_context: dict = {"chat_id": "own-chat", "runtime_home": str(native_profile)}
    bc.canonical_runtime_transcript(
        session_id="own-chat",
        source_context=source_context,
        current_user_text="How long have I been using my Garmin Vivosmart 4?",
    )
    record = source_context.get("admitted_capsule_evidence")
    # Flag-off turns record the cleared state — never a capsule, never stale text.
    assert not (record and record.get("text"))
