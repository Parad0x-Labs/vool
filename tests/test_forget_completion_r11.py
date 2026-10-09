"""Forget completion on upgraded homes and healthy prompt-block storage."""
import json

import pytest

from tests.test_question_date_time_leg_20261002 import _hash_backend, _profile

pytestmark = pytest.mark.usefixtures("_hash_backend")


def test_upgraded_summary_home_forgets_through_normal_command(tmp_path, monkeypatch):
    from core import runtime_paths
    from core.context_namespace import ensure_chat_namespace
    from core.memory.files import session_summaries_path
    from core.persistent_memory import maybe_handle_memory_command

    home = _profile(tmp_path)
    monkeypatch.setattr(runtime_paths, "_VOOL_HOME_OVERRIDE", home)
    ensure_chat_namespace("chat-a", grant_current_receipts=False)
    path = session_summaries_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        '{"session_id":"chat-a","future_note":"MINT-84","summary":"neutral"}\n'
        '{"session_id":"chat-b","future_note":"MINT-84","summary":"foreign"}\n',
        encoding="utf-8",
    )
    assert not (home / ".forget-authority/ledger.json").exists()
    handled, reply = maybe_handle_memory_command(
        "forget MINT-84", session_id="chat-a",
        source_context={"chat_id": "chat-a", "runtime_home": str(home), "client_turn_id": "upgrade-turn"},
    )
    print("UPGRADED_HOME_REPLY", reply)
    assert handled and "Forget applied" in reply, reply
    assert [json.loads(line) for line in path.read_text().splitlines()] == [
        {"session_id": "chat-b", "future_note": "MINT-84", "summary": "foreign"},
    ]
    ledger = json.loads((home / ".forget-authority/ledger.json").read_text())
    assert ledger["operations"] and all(row["status"] == "complete" for row in ledger["operations"].values())


def test_healthy_prompt_block_is_erased_and_completion_is_reported(tmp_path, monkeypatch):
    from core import runtime_paths
    from core.vool_memory import VoolMemory
    from tests.test_forget_holder_coverage_r6 import _seed, command

    home = _profile(tmp_path)
    monkeypatch.setattr(runtime_paths, "_VOOL_HOME_OVERRIDE", home)
    chat = "healthy-block"
    _seed(tmp_path, chat)
    with VoolMemory(runtime_home=home) as memory:
        memory.block_write("user_profile", "The locker code is MINT-84.")
        assert "MINT-84" in memory.block_read("user_profile")
        handled, reply = command(home, chat, "forget MINT-84")
        assert handled and "Forget applied" in reply, reply
        assert memory._conn.execute("SELECT * FROM memory_blocks").fetchone() is None
        assert memory.block_read("user_profile") is None
        assert memory.blocks_for_prompt(["user_profile"]) == ""


def test_withheld_telemetry_keeps_refusal_diagnostics_without_text(tmp_path, monkeypatch):
    from core import context_retrieval, runtime_paths
    from core.memory import revocation
    from core.vool_memory import VoolMemory

    home = _profile(tmp_path)
    monkeypatch.setattr(runtime_paths, "_VOOL_HOME_OVERRIDE", home)
    with VoolMemory(runtime_home=home) as memory:
        occurrence = memory.occurrence_store(chat_scope="diagnostics", role="user", body="Locker code MINT-84.",
                                             authority="observed-user-statement")
        context_retrieval.reset_retrieval_telemetry()
        context_retrieval.update_retrieval_telemetry(
            selected_facts=["Locker code MINT-84."], memory_store_count=1,
            evidence_compiler={"receipts_in_chat": 1, "matched_facts": 1, "missing": ["event_date"],
                               "coverage": {"unseen_receipts": ["receipt-id"], "exhaustive": False},
                               "hop_query": "MINT-84"},
            recall_supplement={"skipped": {"delivery-refused": 1}, "subject_speaker": "MINT-84"},
            whole_turn_lane_refused=[{"occurrence_id": occurrence.occurrence_id,
                                     "reason": "revalidation:missing", "body": "Locker code MINT-84."}],
        )
        revocation.begin("diagnostics", "MINT-84", home)
        telemetry = context_retrieval.get_last_retrieval_telemetry()
        assert "MINT-84" not in str(telemetry)
        assert telemetry["memory_store_count"] == 1
        assert telemetry["evidence_compiler"] == {
            "receipts_in_chat": 1, "matched_facts": 1, "missing": ["event_date"],
            "coverage": {"unseen_receipts": ["receipt-id"], "exhaustive": False},
        }
        assert telemetry["recall_supplement"] == {"skipped": {"delivery-refused": 1}}
        assert telemetry["whole_turn_lane_refused"] == [
            {"occurrence_id": occurrence.occurrence_id, "reason": "revalidation:missing"},
        ]
