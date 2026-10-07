"""Lead-owned combined served journey (paired300-memory-repair-20260930).

One restarted-store journey through the real serving chain:

    real writer (store_turn) -> PROCESS RESTART (fresh interpreter reopens
    the store) -> real retrieval/capsule assembly -> the ACTUAL serialized
    provider request -> a SYNTHETIC scripted provider raw answer (no model is
    called; the reply text is authored to mimic a correct/incorrect reader)
    -> the normal response guards (past-time guard, final live-claim
    validator, orchestration sanitizer) -> final delivered text.

Assertions require complete substantive clauses, not single words. Controls:
later-entry assistant evidence, ordinal list binding, supported past
duration, recorded personal state, deletion/requery (two-layer forget),
foreign-scope exclusion, quote attribution, flag-off, genuine no-answer and
unsupported-live withdrawal.

World (deliberately unlike any captured case): a cider orchard chat plus a
foreign beekeeping chat; values 63/18 varieties, 4th of nine, 11 seasons,
96/104 kilograms, 41 kilograms honey.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

_WORKTREE = Path(__file__).resolve().parents[1]

ORCHARD = "journey-orchard"
APIARY = "journey-apiary"


def _epoch(raw: str) -> float:
    return int(
        datetime.strptime(raw, "%Y/%m/%d (%a) %H:%M")
        .replace(tzinfo=timezone.utc)
        .timestamp()
    )


ORCHARD_TURNS = [
    (
        "2024/03/02 (Sat) 09:15",
        "I keep hearing about heritage cider apple trials. Are there any real "
        "numbers comparing traditional orchards against mixed heritage blocks?",
        "Two worth knowing. 1. A trial summarized in the Orchard Ledger Bulletin "
        "followed 18 traditional orchards over two seasons and compared heritage "
        "varieties against conventional blocks, finding steadier yields. 2. A later "
        "multi-site trial reported in the Cider Growers' Register involved 63 "
        "heritage varieties across nine counties, the widest spread tested to date, "
        "with the mixed blocks out-yielding the conventional ones.",
    ),
    (
        "2024/03/09 (Sat) 10:40",
        "Spring is close. Can you give me a grafting supply checklist before I "
        "start bench grafting next weekend?",
        "Here is the grafting supply checklist:\n"
        "1. Bench grafting knife, honed\n"
        "2. Rootstock liners, chilled\n"
        "3. Scion wood, waxed and labeled\n"
        "4. Melt-grafting wax\n"
        "5. Parafilm bud tape\n"
        "6. Grafting rubbers\n"
        "7. Disinfectant spray for tools\n"
        "8. Label tags and grease pencil\n"
        "9. Callusing bin with damp sphagnum",
    ),
    (
        "2024/03/15 (Fri) 08:05",
        "By the way, I've been managing the north block of the orchard for 11 "
        "seasons now.",
        "Eleven seasons on the north block is real stewardship.",
    ),
    (
        "2024/03/17 (Sun) 11:20",
        "Also, by the way, my heaviest single-day press so far is 96 kilograms, "
        "set back in October.",
        "A 96-kilogram single-day press is a strong mark for one grower.",
    ),
    (
        "2024/03/21 (Thu) 19:30",
        "Quick update — I just got my heaviest single-day press yet, 104 "
        "kilograms in a single day this week!",
        "A 104-kilogram single-day press — that is a new personal best. "
        "Congratulations.",
    ),
]

APIARY_TURNS = [
    (
        "2024/03/18 (Mon) 17:00",
        "The apiary did well: my bees produced 41 kilograms of honey this past "
        "season.",
        "Forty-one kilograms from your own hives is an excellent season.",
    ),
]


def _seed(home: Path) -> None:
    from core.context_namespace import ensure_chat_namespace
    from core.context_retrieval import store_turn
    from core.memory.entries import resolve_memory_access_policy

    for chat, turns in ((ORCHARD, ORCHARD_TURNS), (APIARY, APIARY_TURNS)):
        ensure_chat_namespace(chat, grant_current_receipts=False)
        policy = resolve_memory_access_policy(chat_id=chat)
        for date, user_text, assistant_text in turns:
            result = store_turn(
                chat,
                user_text,
                assistant_text,
                access_policy=policy,
                source_context={
                    "chat_id": chat,
                    "runtime_home": str(home),
                    "statement_at": _epoch(date),
                },
            )
            assert result["status"] in {"stored", "retained"}, result


@pytest.fixture()
def journey_home(tmp_path):
    """Disposable runtime home, seeded FRESH per test.

    Function-scoped on purpose: the suite's autouse `runtime_storage_reset`
    wipes the memory files before every test, so a module-scoped seeded store
    would be erased (measured on the first journey attempt). Seeding inside
    the test boundary is the suite convention (see the lane-1 native tests).

    The real local embedding lane is used (the running local
    nomic-embed-text service, the same backend the captured run used; no
    answer model is called). Without it the deterministic hash fallback
    ranks some journey facts differently; QUICK-CHECK documents the
    service requirement for reruns.
    """
    home = tmp_path / f"journey-{uuid.uuid4().hex[:10]}"
    home.mkdir(parents=True, exist_ok=True)
    assert home.is_relative_to(tmp_path)
    os.environ["VOOL_HOME"] = str(home)
    os.environ["VOOL_HOME"] = str(home)
    os.environ["VOOL_CONTEXT_CAPSULE_V2"] = "1"
    from core.runtime_paths import configure_runtime_home
    from storage.db import configure_default_db_path

    configure_runtime_home(home)
    # The suite's autouse storage reset points the default db at the shared
    # pytest home; the namespace must live in THIS home so a fresh interpreter
    # (the process-restart step) resolves it from the journey home alone.
    configure_default_db_path(home / "data" / "vool_web0_v2.db")
    from storage.migrations import run_migrations

    run_migrations()
    _seed(home)
    yield home
    configure_default_db_path(None)
    configure_runtime_home(None)
    shutil.rmtree(home, ignore_errors=True)


class _StubContextResult:
    """The tiered-context shape the guard evidence builder reads."""

    def __init__(self, local_candidates: list | None = None):
        self.local_candidates = list(local_candidates or [])

    def assembled_context(self, *, prompt_profile: str = "default") -> str:
        return ""


def _serialized_request(
    home: Path, chat: str, question: str
) -> tuple[list[dict], dict, str]:
    """Reopen the store, assemble the real provider transcript, return
    (serialized messages, source_context carrying admitted evidence, capsule)."""
    import core.bootstrap_context as bc
    from core.context_retrieval import reset_retrieval_telemetry

    reset_retrieval_telemetry()
    source_context: dict = {
        "chat_id": chat,
        "runtime_home": str(home),
        "conversation_history": [{"role": "user", "content": question}],
    }
    transcript, _source = bc.canonical_runtime_transcript(
        session_id=chat,
        source_context=source_context,
        current_user_text=question,
    )
    capsule = "\n".join(
        str(m.get("content") or "")
        for m in transcript
        if "<retrieved_context>" in str(m.get("content") or "")
    )
    return transcript, source_context, capsule


def _deliver(question: str, raw_reply: str, source_context: dict, chat: str) -> str:
    """Scripted provider reply -> the normal response guard chain -> delivery."""
    from core.agent_runtime.response import decorate_chat_response
    from core.agent_runtime.turn_reasoning import _past_time_guard_evidence
    from core.model_output_guard import replace_unsupported_past_time_claims

    guarded = replace_unsupported_past_time_claims(
        raw_reply,
        question=question,
        evidence_texts=_past_time_guard_evidence(
            context_result=_StubContextResult(),
            source_context=source_context,
            web_notes=[],
            session_id=chat,
        ),
    )
    from apps.vool_agent import ChatTurnResult, ResponseClass, VoolAgent

    agent = VoolAgent(
        backend_name="journey-backend", device="p300-lead", persona_id="default"
    )
    surface = dict(source_context)
    surface.setdefault("surface", "openclaw")
    surface.setdefault("platform", "openclaw")
    return decorate_chat_response(
        agent,
        ChatTurnResult(
            text=guarded, response_class=ResponseClass.GENERIC_CONVERSATION
        ),
        session_id=chat,
        source_context=surface,
        include_hive_footer=False,
    )


# ── S1: later-entry assistant evidence, across a true process restart ─────────


def test_s1_later_entry_evidence_survives_a_process_restart(journey_home, tmp_path):
    question = (
        "Remind me: how many heritage varieties were in the trial reported in "
        "the Cider Growers' Register?"
    )
    script = f"""
import json, os, sys
sys.path.insert(0, {str(_WORKTREE)!r})
os.environ["VOOL_HOME"] = {str(journey_home)!r}
os.environ["VOOL_HOME"] = {str(journey_home)!r}
os.environ["VOOL_CONTEXT_CAPSULE_V2"] = "1"
from core.runtime_paths import configure_runtime_home
configure_runtime_home({str(journey_home)!r})
import core.bootstrap_context as bc
from core.context_retrieval import reset_retrieval_telemetry
reset_retrieval_telemetry()
source_context = {{"chat_id": {ORCHARD!r}, "runtime_home": {str(journey_home)!r}}}
transcript, _ = bc.canonical_runtime_transcript(
    session_id={ORCHARD!r},
    source_context=source_context,
    current_user_text={question!r},
)
json.dump(transcript, open({str(tmp_path / "request.json")!r}, "w"))
"""
    env = dict(os.environ)
    env.pop("PYTHONDONTWRITEBYTECODE", None)
    # the interpreter running this suite is the job's venv under the launcher
    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(_WORKTREE),
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert proc.returncode == 0, proc.stderr[-2000:] + "|STDOUT|" + proc.stdout[-2000:]
    # the store reopened in a FRESH interpreter and still served the evidence
    request = json.loads((tmp_path / "request.json").read_text())
    request_text = "\n".join(str(m.get("content") or "") for m in request)
    assert "63 heritage varieties" in request_text
    assert "Cider Growers' Register" in request_text
    assert "- assistant said" in request_text  # attribution preserved

    _, source_context, capsule = _serialized_request(journey_home, ORCHARD, question)
    assert "63 heritage varieties" in capsule
    raw_reply = (
        "The Cider Growers' Register trial involved 63 heritage varieties across "
        "nine counties — the later, wider of the two trials we discussed. For "
        "comparison, the Orchard Ledger Bulletin trial followed only 18 traditional "
        "orchards."
    )
    delivered = _deliver(question, raw_reply, source_context, ORCHARD)
    assert "63 heritage varieties" in delivered
    assert "the later, wider of the two trials we discussed" in delivered
    assert "18 traditional orchards" in delivered


# ── S2: ordinal list binding after restart ────────────────────────────────────


def test_s2_fourth_checklist_item_reaches_delivery(journey_home):
    question = "What was the fourth item on the grafting supply checklist you gave me?"
    _, source_context, capsule = _serialized_request(journey_home, ORCHARD, question)
    assert "Melt-grafting wax" in capsule, capsule
    raw_reply = (
        "The fourth item on your grafting supply checklist was melt-grafting wax — "
        "you will want it warm enough to brush over the bench grafts."
    )
    delivered = _deliver(question, raw_reply, source_context, ORCHARD)
    assert "fourth item on your grafting supply checklist was melt-grafting wax" in (
        delivered
    )


# ── S3: supported past duration survives the time guard ──────────────────────


def test_s3_supported_past_duration_survives(journey_home):
    question = "How long have I been managing the north block of the orchard?"
    _, source_context, capsule = _serialized_request(journey_home, ORCHARD, question)
    assert "11 seasons" in capsule, capsule
    raw_reply = (
        "According to what you noted on March 15th, you've been managing the "
        "north block of the orchard for 11 seasons now."
    )
    delivered = _deliver(question, raw_reply, source_context, ORCHARD)
    assert "managing the north block of the orchard for 11 seasons" in delivered
    assert "don't have that time" not in delivered.lower()
    assert "not going to state" not in delivered.lower()


# ── S4: latest recorded personal state survives; supersession preserved ───────


def test_s4_latest_recorded_state_survives(journey_home):
    question = "What is my current heaviest single-day press?"
    _, source_context, capsule = _serialized_request(journey_home, ORCHARD, question)
    # the current record rides as a full exact-fact line; the superseded mark is
    # evidenced by the attributed assistant echo that rides with it
    assert "104 kilograms" in capsule, capsule
    assert "96-kilogram" in capsule, capsule
    raw_reply = (
        "Your current heaviest single-day press is 104 kilograms, which you just "
        "set this week — a new personal best, up from the 96-kilogram mark you "
        "mentioned in March."
    )
    delivered = _deliver(question, raw_reply, source_context, ORCHARD)
    assert "heaviest single-day press is 104 kilograms" in delivered
    assert "up from the 96-kilogram mark you mentioned in March" in delivered
    assert "can't verify current measured-quantity" not in delivered


# ── S5: genuine no-answer keeps its full explanatory abstention ───────────────


def test_s5_genuine_no_answer_abstention_survives_sanitizer(journey_home):
    question = (
        "Do my notes say anything about autographed prints from the print shop?"
    )
    _, source_context, _capsule = _serialized_request(journey_home, ORCHARD, question)
    raw_reply = (
        "I've got no record of autographed prints from the print shop in your "
        "notes. They only track the orchard press records — 96 kilograms in "
        "October, then 104 kilograms in March. If you've started collecting "
        "prints, tell me and I'll note it."
    )
    delivered = _deliver(question, raw_reply, source_context, ORCHARD)
    assert "I've got no record of autographed prints" in delivered
    assert "They only track the orchard press records" in delivered
    assert "If you've started collecting prints" in delivered


# ── S6: unsupported live observation still withdrawn ──────────────────────────


def test_s6_unsupported_live_observation_still_refused(journey_home):
    question = "What's the current temperature in the cider barn right now?"
    _, source_context, _capsule = _serialized_request(journey_home, ORCHARD, question)
    raw_reply = "It's 19 degrees in the cider barn right now."
    delivered = _deliver(question, raw_reply, source_context, ORCHARD)
    assert "19 degrees" not in delivered
    assert "can't verify" in delivered.lower() or "not going to state" in delivered.lower()


# ── S7: foreign-scope exclusion ────────────────────────────────────────────────


def test_s7_foreign_chat_excluded_and_its_own_fact_scoped(journey_home):
    question = (
        "Remind me: how many heritage varieties were in the trial reported in "
        "the Cider Growers' Register?"
    )
    request, source_context, capsule = _serialized_request(
        journey_home, APIARY, question
    )
    request_text = "\n".join(str(m.get("content") or "") for m in request)
    assert "63 heritage varieties" not in request_text
    assert "Cider Growers' Register" not in request_text
    raw_reply = (
        "I don't have any record of a Cider Growers' Register trial in our "
        "beekeeping conversations — nothing here mentions heritage varieties. The "
        "only number on file from the apiary is your 41-kilogram honey harvest "
        "this past season. If you meant a different conversation, point me at it."
    )
    delivered = _deliver(question, raw_reply, source_context, APIARY)
    assert (
        "I don't have any record of a Cider Growers' Register trial" in delivered
    )
    assert "41-kilogram honey harvest" in delivered


# ── S8: deletion/requery — two-layer forget removes carriers, keeps siblings ──


def test_s8_deletion_requery_removes_carriers_and_keeps_siblings(journey_home):
    from core.context_retrieval import _AGENT_ID, admitted_evidence_records
    from core.vool_memory import VoolMemory

    # the real two-layer forget: nodes + source occurrences, token-scoped,
    # through the SAME store construction the serving path uses
    mem = VoolMemory(runtime_home=str(journey_home), agent_id=_AGENT_ID)
    try:
        node_hits = mem.node_invalidate_matching("104", session_id=ORCHARD)
        occ_hits = mem.occurrence_invalidate_matching("104", chat_scope=ORCHARD)
        assert node_hits >= 1 and occ_hits >= 1, (node_hits, occ_hits)
    finally:
        mem.close()

    question = "What is my current heaviest single-day press?"
    request, source_context, capsule = _serialized_request(
        journey_home, ORCHARD, question
    )
    request_text = "\n".join(str(m.get("content") or "") for m in request)
    assert "104 kilograms" not in request_text
    assert "104" not in capsule
    # sibling facts survive the token-scoped forget (the duration fact rides
    # untouched; the superseded 96 record's subject line still serves its
    # subject phrase)
    assert "11 seasons" in capsule, capsule
    assert "heaviest single-day press so far" in capsule, capsule
    records = admitted_evidence_records()
    assert not any("104" in r.line for r in records if r.role == "user"), [
        r.line for r in records
    ]
    # a stale scripted provider still claiming the forgotten value is refused
    stale_reply = (
        "Your current heaviest single-day press is 104 kilograms, which you set "
        "the week of March 21st."
    )
    delivered = _deliver(question, stale_reply, source_context, ORCHARD)
    assert "104 kilograms" not in delivered


# ── S9: flag-off — no capsule, no admitted evidence, behavior degrades safely ─


def test_s9_flag_off_no_capsule_and_safe_delivery(journey_home):
    question = "How long have I been managing the north block of the orchard?"
    saved = os.environ.get("VOOL_CONTEXT_CAPSULE_V2")
    os.environ.pop("VOOL_CONTEXT_CAPSULE_V2", None)
    try:
        request, source_context, capsule = _serialized_request(
            journey_home, ORCHARD, question
        )
        assert capsule == ""
        record = source_context.get("admitted_capsule_evidence") or {}
        assert not record.get("text")
        raw_reply = (
            "According to what you noted on March 15th, you've been managing the "
            "north block of the orchard for 11 seasons now."
        )
        delivered = _deliver(question, raw_reply, source_context, ORCHARD)
        # without admitted evidence the specific time is honestly refused
        assert "11 seasons" not in delivered or "don't have that time" in delivered.lower()
    finally:
        if saved is not None:
            os.environ["VOOL_CONTEXT_CAPSULE_V2"] = saved
