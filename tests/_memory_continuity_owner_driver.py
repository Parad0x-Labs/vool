"""Offline writer/grant-owner driver for separate native continuity controls.

This calls finalized-turn retention and persisted grant owners directly in
fresh processes. No model or network execution is permitted. Serving is then
performed by the independently frozen evidence-contract reader driver.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sqlite3
import sys
import urllib.error
import urllib.request
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--home", required=True)
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--input", required=True)
    parser.add_argument("--operation", choices=["image-finalization", "grant-seed", "revoke"], required=True)
    args = parser.parse_args()
    home = Path(args.home).resolve()
    home.mkdir(parents=True, exist_ok=True)
    os.environ["VOOL_HOME"] = str(home)
    os.environ["VOOL_HOME"] = str(home)
    os.environ["VOOL_CONTEXT_CAPSULE_V2"] = "1"
    os.environ["VOOL_REGISTER_INSTALLED_OLLAMA_MODELS"] = "0"
    sys.path.insert(0, str(Path(args.repo_root).resolve()))
    blocked = []

    def deny_urlopen(request, *a, **kw):
        blocked.append(str(getattr(request, "full_url", request)))
        raise urllib.error.URLError("Offline continuity acceptance: network disabled")

    urllib.request.urlopen = deny_urlopen
    import requests

    def deny_requests(*a, **kw):
        raise requests.ConnectionError("Offline continuity acceptance: network disabled")

    requests.get = deny_requests
    requests.post = deny_requests
    requests.sessions.Session.request = deny_requests

    from core.runtime_paths import configure_runtime_home
    from storage.db import configure_default_db_path
    from storage.migrations import run_migrations

    configure_runtime_home(home)
    configure_default_db_path(home / "data" / "vool_web0_v2.db")
    run_migrations()
    from core.context_namespace import (
        ensure_chat_namespace,
        grant_context_import,
        list_context_imports,
        revoke_context_import,
    )
    from core.memory.entries import resolve_memory_access_policy

    doc = json.loads(Path(args.input).read_text())
    main_chat, foreign_chat = doc["main_chat"], doc["foreign_chat"]
    for chat in [main_chat, foreign_chat]:
        ensure_chat_namespace(chat, grant_current_receipts=False)
    receipts = []
    revoked = None
    if args.operation == "image-finalization":
        from core.execution.constants import image_generation_intent
        from core.persistent_memory import append_conversation_event

        assert image_generation_intent(doc["turns"][0]["user"]), "Brief must hit the real image finalization gate"
        for turn in doc["turns"]:
            append_conversation_event(
                session_id=main_chat,
                user_input=turn["user"],
                assistant_output=turn["assistant"],
                source_context={"chat_id": main_chat, "runtime_home": str(home), "surface": "local", "statement_at": dt.datetime.fromisoformat(turn["stated"]).replace(tzinfo=dt.timezone.utc).timestamp()},
                access_policy=resolve_memory_access_policy(chat_id=main_chat),
            )
        receipts = [{"operation": "append_conversation_event", "turns": len(doc["turns"])}]
    elif args.operation == "grant-seed":
        from core.context_retrieval import store_turn

        for turn in doc["turns"]:
            result = store_turn(
                turn["chat"], turn["user"], turn["assistant"],
                access_policy=resolve_memory_access_policy(chat_id=turn["chat"]),
                source_context={"chat_id": turn["chat"], "runtime_home": str(home)},
            )
            receipts.append(result)
        grant_context_import(main_chat, scope="chat", source_id="chat:" + foreign_chat)
    else:
        revoked = revoke_context_import(main_chat, scope="chat", source_id="chat:" + foreign_chat)

    sources = []
    assertion_nodes = []
    memory_db = home / "data" / "memory" / "vool_memory.db"
    if memory_db.exists():
        con = sqlite3.connect("file:" + str(memory_db) + "?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        try:
            sources = [dict(row) for row in con.execute("SELECT chat_scope,role,authority,body,status FROM source_occurrences ORDER BY rowid")]
            assertion_nodes = [dict(row) for row in con.execute(
                "SELECT n.content, n.source_occurrence_id, o.body AS source_body, o.role AS source_role FROM memory_nodes n LEFT JOIN source_occurrences o ON o.occurrence_id=n.source_occurrence_id"
            )]
        finally:
            con.close()
    from core.persistent_memory import (
        conversation_log_path,
        memory_entries_path,
        operator_dense_profile_path,
        user_heuristics_path,
    )

    output = {"operation": args.operation, "network_blocked": True, "blocked_attempts": blocked, "receipts": receipts, "revoked": revoked, "active_grants": [g.source_id for g in list_context_imports(main_chat)], "retained_sources": sources, "assertion_nodes": assertion_nodes, "conversation_log": conversation_log_path().read_text() if conversation_log_path().exists() else "", "profile_facts": {"memory_entries": memory_entries_path().read_text() if memory_entries_path().exists() else "", "heuristics": user_heuristics_path().read_text() if user_heuristics_path().exists() else "", "operator_profile": operator_dense_profile_path().read_text() if operator_dense_profile_path().exists() else ""}}
    Path(args.out).write_text(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
