"""Real served conversation writer with canonical A7 finalization receipts.

Reuses the native driver's parent-blind transport. It adds only observation and
calls the real transport finalizer after each real run_agent result. No source
rows, semantic ids, grants, answer content, or finalization identities are forged.
Contributor: sls_0x.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
from pathlib import Path


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _source_snapshot(home: Path, session_id: str) -> list[dict]:
    path = home / "data" / "memory" / "vool_memory.db"
    if not path.exists():
        return []
    conn = sqlite3.connect("file:" + str(path) + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        return [
            {
                "occurrence_id": row["occurrence_id"],
                "chat_scope": row["chat_scope"],
                "role": row["role"],
                "authority": row["authority"],
                "status": row["status"],
                "body_sha256": _digest(row["body"]),
                "body_characters": len(row["body"]),
                "statement_at": row["statement_at"],
            }
            for row in conn.execute(
                "SELECT occurrence_id, chat_scope, role, authority, status, body, statement_at "
                "FROM source_occurrences WHERE chat_scope=? ORDER BY rowid", (session_id,)
            )
        ]
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--home", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--reference-date", default="")
    args, _ = parser.parse_known_args()
    home = Path(args.home).resolve()
    repo = Path(args.repo_root).resolve()
    # Runtime storage must be bound before ANY project import. The ordinary
    # native driver resolves startup flags and installs its scripted transport.
    os.environ["VOOL_HOME"] = str(home)
    os.environ["VOOL_HOME"] = str(home)
    sys.path.insert(0, str(repo))
    sys.path.insert(0, str(repo / "tests"))
    import _memory_evidence_contract_driver as native_driver
    native_driver.install_fixture_reference_clock(args.reference_date)
    from core.runtime_paths import configure_runtime_home
    from storage.db import configure_default_db_path, get_connection
    configure_runtime_home(home)
    configure_default_db_path(home / "data" / "vool_web0_v2.db")
    from storage.migrations import run_migrations
    run_migrations()
    import core.web.api.runtime as runtime_api
    from core.persistent_memory import recent_conversation_events
    from storage.dialogue_memory import recent_dialogue_turns
    original = runtime_api.run_agent
    receipts: list[dict] = []

    def finalized_run_agent(runtime, user_text, **kwargs):
        session_id = str(kwargs.get("session_id") or "")
        result = original(runtime, user_text, **kwargs)
        # This is the normal API transport authority, not a test-created commit.
        # Any rejection propagates and remains a failed first attempt.
        commit = runtime_api._response_commit(
            result,
            source_context=result.get("source_context") or kwargs.get("source_context"),
        )
        canonical = str(commit.get("canonical_content") or "")
        assert canonical.strip(), "A7 returned no finalized answer content"
        assert commit.get("content_hash") == "sha256:" + _digest(canonical)
        conn = get_connection()
        try:
            durable = conn.execute(
                "SELECT content_hash, canonical_content, semantic_result_id "
                "FROM a7_finalizations WHERE finalization_id=?", (commit["finalization_id"],)
            ).fetchone()
            assert durable is not None, "A7 finalization was not durable"
            assert durable["canonical_content"] == canonical
            assert durable["content_hash"] == commit["content_hash"]
        finally:
            conn.close()
        rows = recent_dialogue_turns(session_id, limit=500, speaker_roles=("user", "assistant"))
        events = recent_conversation_events(session_id, limit=500)
        receipts.append({
            "sequence": len(receipts) + 1,
            "session_id": session_id,
            "user_text_sha256": _digest(user_text),
            "user_text_characters": len(user_text),
            "finalization_id": commit["finalization_id"],
            "semantic_result_id": commit.get("semantic_result_id") or "not_captured",
            "request_id": commit.get("request_id") or "not_captured",
            "final_content_sha256": _digest(canonical),
            "final_content_characters": len(canonical),
            "closure_verdict": commit.get("closure_verdict"),
            "binding_outcome": commit.get("binding_outcome"),
            "durable_finalization_verified": True,
            "dialogue_rows": [
                {"turn_id": row["turn_id"], "request_id": row.get("request_id") or "not_captured",
                 "role": row["speaker_role"], "raw_sha256": _digest(str(row["raw_input"])),
                 "created_at": row["created_at"]}
                for row in reversed(rows)
            ],
            "conversation_events_count": len(events),
            "retained_sources": _source_snapshot(home, session_id),
        })
        # The real API's served content is the canonical finalized content.
        return {**result, "response": canonical, "vool_response_commit": commit}

    runtime_api.run_agent = finalized_run_agent
    outcome = "not_completed"
    error = None
    try:
        code = native_driver.main()
        outcome = "completed" if code == 0 else "driver_failed"
        output = Path(args.out)
        data = json.loads(output.read_text())
        data["finalized_workflow"] = {
            "schema": "vool.finalized_conversation_workflow.v1",
            "pid": os.getpid(),
            "owner_path": "core/web/api/runtime.py:_response_commit -> core/finalization.py:finalize_answer",
            "ingestion_path": "run_agent -> adapt_user_input -> record_dialogue_turn; append_conversation_event -> store_turn",
            "finalized_turns": len(receipts),
            "unique_finalization_ids": len({r["finalization_id"] for r in receipts}),
            "receipts": receipts,
            "provider_spending_usd": 0,
            "live_model_quality": "not_measured",
        }
        output.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        return code
    except BaseException as exc:
        outcome = "failed"
        error = type(exc).__name__ + ": " + str(exc)
        raise
    finally:
        runtime_api.run_agent = original
        diagnostic = Path(args.out).with_suffix(".workflow.json")
        assert not diagnostic.exists(), "workflow diagnostic path must preserve earlier attempts"
        diagnostic.write_text(json.dumps({"outcome": outcome, "error": error,
            "pid": os.getpid(), "finalized_turns": len(receipts), "receipts": receipts},
            ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
