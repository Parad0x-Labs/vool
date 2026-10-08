"""A durable subject withdrawal reaches every memory reader family."""
import pytest

from tests.test_question_date_time_leg_20261002 import _capsule, _hash_backend, _ingest, _profile

pytestmark = pytest.mark.usefixtures("_hash_backend")

SUBJECT_READERS = (
    "structured_inventory", "command_recall", "source_occurrence", "capsule", "visible_transcript",
    "materialized_source", "admitted_telemetry", "exact_capsule", "hydrated_history",
)


@pytest.mark.parametrize("reader", SUBJECT_READERS)
def test_subject_withdrawal_reaches_every_registered_reader(tmp_path, reader):
    from core import context_retrieval as cr
    from core.agent_runtime.same_chat_recall import same_chat_transcript_recall_fast_path
    from core.memory.entries import add_memory_fact, list_memory_entries, resolve_memory_access_policy
    from core.persistent_memory import maybe_handle_memory_command
    from core.vool_memory import VoolMemory

    profile = _profile(tmp_path)
    chat = "subject-readers-" + reader
    subject = "My kiln locker code is 7428."
    _ingest(profile, chat, [(1760000000, subject)])
    policy = resolve_memory_access_policy(chat_id=chat)
    # An explicit application fact key avoids the correction lane's generic
    # "code" subject key. The named phrase is a withdrawal, while literal
    # erasure matches neither this authored row nor its source occurrence.
    assert add_memory_fact(subject, session_id=chat, access_policy=policy, fact_key="kiln-locker-combination")
    memory = VoolMemory(runtime_home=str(profile))
    occurrence = memory.occurrence_search("kiln locker code", chat_scope=chat)[0][0]
    context = {"chat_id": chat, "runtime_home": str(profile), "client_turn_id": "withdrawal-turn",
               "conversation_history": [{"role": "user", "content": "Give me a title."},
                                        {"role": "assistant", "content": "Title: " + subject}]}

    telemetry = {"capsule_mode": "distilled", "selected_facts": ["- exact code: 7428", "- relevant context: " + subject],
                 "source_session_ids": [cr._session_scope_key(chat)],
                 "evidence_refs": [{"line": subject, "delivered": True, "chat_scope": chat}]}

    def read():
        from core.persistent_memory import augment_history_from_session_log

        if reader == "materialized_source":
            return cr.materialize_source_evidence(occurrence.occurrence_id, runtime_home=str(profile))
        if reader == "admitted_telemetry":
            return cr.admitted_evidence_records(telemetry)
        if reader == "exact_capsule":
            return cr.capsule_exact_response("What is my kiln locker code?", telemetry, session_id=chat)
        if reader == "hydrated_history":
            return augment_history_from_session_log(
                [{"role": "user", "content": subject}, {"role": "assistant", "content": "Noted."}],
                session_id=chat, user_text="What is my kiln locker code?",
            )
        if reader == "structured_inventory":
            return list_memory_entries(access_policy=policy)
        if reader == "command_recall":
            return maybe_handle_memory_command("What facts do you remember from this chat?", session_id=chat,
                                               access_policy=policy, source_context=context)[1]
        if reader == "source_occurrence":
            return memory.occurrence_get(occurrence.occurrence_id)
        if reader == "capsule":
            return _capsule(profile, chat, "What is my kiln locker code?")[0]
        recalled = same_chat_transcript_recall_fast_path(
            "What title did you give me earlier in this chat?", source_surface="api", source_context=context)
        return recalled.response if recalled else ""

    try:
        assert "7428" in str(read()), reader
        handled, reply = maybe_handle_memory_command(
            "Forget the kiln locker code, it is wrong.", session_id=chat,
            access_policy=policy, source_context=context,
        )
        assert handled and reply.startswith("Nothing was removed"), reply
        retained = memory._conn.execute(
            "SELECT body FROM source_occurrences WHERE occurrence_id = ?", (occurrence.occurrence_id,),
        ).fetchone()
        assert retained is not None and retained[0] == subject
        from core.memory.files import memory_entries_path

        assert "7428" in memory_entries_path().read_text()
        assert "7428" not in str(read()), reader
        # No new source copy of the forget's revoked control text was written.
        _ingest(profile, chat, [(1760000010, "Forget the kiln locker code, it is wrong.")])
        assert "7428" not in str(read()), reader
    finally:
        memory.close()


def test_unrelated_source_survives_subject_withdrawal(tmp_path):
    from core.persistent_memory import maybe_handle_memory_command

    profile = _profile(tmp_path)
    chat = "unrelated-subject"
    _ingest(profile, chat, [(1760000000, "My pottery locker code is 7428."),
                            (1760000001, "The orchard key hangs on the silver hook.")])
    handled, reply = maybe_handle_memory_command(
        "Forget the pottery locker code I gave you, it is wrong.", session_id=chat,
        source_context={"chat_id": chat, "runtime_home": str(profile), "client_turn_id": "withdrawal-turn"},
    )
    assert handled and reply.startswith("Nothing was removed"), reply
    capsule, _ = _capsule(profile, chat, "Where is the orchard key?")
    assert "silver hook" in capsule
    assert "7428" not in capsule


@pytest.mark.parametrize("module_name,readers,other_apis", [
    ("core.context_retrieval", {
        "admitted_evidence_records", "capsule_exact_response", "materialize_source_evidence", "inject_retrieved",
    }, {
        "get_last_retrieval_telemetry", "reset_retrieval_telemetry", "update_retrieval_telemetry",
        "resolve_semantic_access_policy", "exact_recall_requires_current_session", "validate_capsule_exact_response",
        "is_capsule_exact_recall_query", "query_requests_complete_collection", "temporal_speaker",
        "search_expansion_wanted", "store_turn", "revoked_tokens_for_chat", "text_carries_revoked_token",
        "forget_session_memory", "import_conversation_history", "revoke_imported_history",
    }),
    ("core.persistent_memory", {
        "augment_history_from_session_log", "memory_lifecycle_snapshot", "maybe_handle_memory_command",
    }, {
        "has_relevant_memory_candidate", "ensure_memory_files", "append_conversation_event",
        "open_transcript_commit_boundary", "transcript_commit_boundary_open", "has_staged_conversation_event",
        "persist_staged_conversation_event", "flush_staged_conversation_events", "append_assistant_artifact_event",
    }),
    ("core.agent_runtime.same_chat_recall", {
        "gate_transcript_recall", "same_chat_transcript_recall_fast_path", "same_chat_math_recall_fast_path",
    }, set()),
])
def test_new_public_serving_api_requires_reader_classification(module_name, readers, other_apis):
    import ast
    import importlib
    from pathlib import Path

    module = importlib.import_module(module_name)
    tree = ast.parse(Path(module.__file__).read_text())
    public = {node.name for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
              and not node.name.startswith("_")}
    assert public == readers | other_apis


class _BoundedOwnerLock:
    """An inversion fails in bounded time instead of stranding a test thread."""
    def __init__(self):
        import threading

        self.lock = threading.RLock()

    def __enter__(self):
        if not self.lock.acquire(timeout=2):
            raise TimeoutError("owner lock held while waiting for authority")
        return self

    def __exit__(self, *_exc):
        self.lock.release()


def test_block_reader_does_not_hold_owner_lock_while_waiting_for_authority(tmp_path, monkeypatch):
    import contextlib
    import threading

    from core.memory import revocation
    from core.vool_memory import VoolMemory

    profile = _profile(tmp_path)
    memory = VoolMemory(runtime_home=str(profile))
    memory.block_write("notes", "Earlier marker.")
    reader_at_gate = threading.Event()
    writer_has_authority = threading.Event()
    errors = []
    read_values = []
    original_gate = memory.payload_is_current
    original_writing = revocation.writing

    def gate_after_writer_starts(*args, **kwargs):
        reader_at_gate.set()
        if not writer_has_authority.wait(timeout=2):
            raise TimeoutError("writer did not acquire authority")
        return original_gate(*args, **kwargs)

    @contextlib.contextmanager
    def observe_writing(*args, **kwargs):
        with original_writing(*args, **kwargs):
            writer_has_authority.set()
            yield

    def read():
        try:
            read_values.append(memory.block_read("notes"))
        except BaseException as exc:
            errors.append(exc)

    def write():
        try:
            memory.block_write("notes", "Updated marker.")
        except BaseException as exc:
            errors.append(exc)

    monkeypatch.setattr(memory, "_lock", _BoundedOwnerLock())
    monkeypatch.setattr(memory, "payload_is_current", gate_after_writer_starts)
    monkeypatch.setattr(revocation, "writing", observe_writing)
    reader = threading.Thread(target=read, daemon=True)
    writer = threading.Thread(target=write, daemon=True)
    try:
        reader.start()
        assert reader_at_gate.wait(timeout=2)
        writer.start()
        reader.join(timeout=5)
        writer.join(timeout=5)
        assert not reader.is_alive() and not writer.is_alive()
        assert not errors, errors
        assert read_values == ["Earlier marker."]
    finally:
        reader.join(timeout=3)
        if writer.ident is not None:
            writer.join(timeout=3)
        memory.close()


def test_new_public_memory_owner_api_requires_reader_classification():
    import ast
    from pathlib import Path

    from core import vool_memory

    readers = {
        "block_read", "blocks_for_prompt", "node_get", "node_search", "node_search_hybrid", "session_nodes",
        "recent_nodes", "occurrence_get", "occurrence_search", "occurrence_embeddings_missing",
        "occurrence_stated_between", "occurrence_search_semantic", "occurrence_neighbors",
    }
    other_apis = {
        "semantic_floor", "db_path", "agent_id", "block_write", "block_append", "block_replace", "block_delete",
        "all_block_names", "node_store", "node_update_links", "node_invalidate", "node_invalidate_matching",
        "node_delete_by_lineage", "node_delete_for_session", "occurrence_delete_for_chat",
        "node_delete_unlineaged_by_content", "purge_governed_lines", "prune", "node_count", "occurrence_store",
        "occurrence_embedding_upsert", "occurrence_active_count", "occurrence_delete", "occurrence_invalidate_matching",
        "begin_erasure", "complete_erasure", "erasure_incomplete", "revocation_generation", "payload_is_current",
        "verify_forget_blocks", "record_revocation", "revoked_tokens", "occurrence_delete_by_lineage",
        "occurrence_ids_for_batch", "node_invalidate_by_occurrence", "occurrence_count", "close",
    }
    tree = ast.parse(Path(vool_memory.__file__).read_text())
    owner = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "VoolMemory")
    public = {node.name for node in owner.body if isinstance(node, ast.FunctionDef) and not node.name.startswith("_")}
    assert public == readers | other_apis


def test_pending_forget_content_is_not_returned_to_embedding_backfill(tmp_path, monkeypatch):
    from core import context_retrieval as cr
    from core.vool_memory import VoolMemory

    profile = _profile(tmp_path)
    chat = "withheld-backfill"
    memory = VoolMemory(runtime_home=str(profile))
    subject = "My kiln locker code is MINT-84."
    occurrence = memory.occurrence_store(
        chat_scope=chat, role="user", body=subject, authority="observed-user-statement",
    )
    backend = "ollama:backfill-boundary"
    sent_to_embedding = []
    original_embed = cr.embed_stamped

    def observe_embedding(text, *args, **kwargs):
        sent_to_embedding.append(text)
        vector, _backend = original_embed(text, *args, **kwargs)
        return vector, backend

    try:
        assert (occurrence.occurrence_id, subject) in memory.occurrence_embeddings_missing(
            chat_scope=chat, backend=backend,
        )
        memory.begin_erasure(chat, "MINT-84")
        assert memory._conn.execute(
            "SELECT body FROM source_occurrences WHERE occurrence_id = ?", (occurrence.occurrence_id,),
        ).fetchone()[0] == subject
        assert memory.occurrence_embeddings_missing(chat_scope=chat, backend=backend) == []
        # The embedding transport uses deterministic vectors; this checks the
        # bytes admitted to backfill, not a model's retrieval quality.
        monkeypatch.setattr(cr, "embed_stamped", observe_embedding)
        _capsule(profile, chat, "What is my kiln locker code?")
        assert sent_to_embedding
        assert not any("MINT-84" in text for text in sent_to_embedding)
        assert memory._conn.execute(
            "SELECT COUNT(*) FROM source_embeddings WHERE occurrence_id = ?", (occurrence.occurrence_id,),
        ).fetchone()[0] == 0
    finally:
        memory.close()


def test_direct_erasure_takes_authority_before_owner_lock(tmp_path, monkeypatch):
    import contextlib
    import threading

    from core.memory import revocation
    from core.memory_receipts import write_receipt
    from core.vool_memory import VoolMemory

    monkeypatch.setenv("VOOL_MEMORY_RECEIPTS", "1")
    profile = _profile(tmp_path)
    chat = "direct-erasure-order"
    memory = VoolMemory(runtime_home=str(profile))
    occurrence = memory.occurrence_store(
        chat_scope=chat, role="user", authority="observed-user-statement",
        body="My locker code is MINT-84. My garden gate is blue.",
    )
    assert write_receipt(memory, occurrence, chat_scope=chat).status == "written"
    writer_has_authority = threading.Event()
    erasure_at_authority = threading.Event()
    errors = []
    erased = []
    original_serialized = revocation.serialized

    @contextlib.contextmanager
    def observe_authority(*args, **kwargs):
        if threading.current_thread() is eraser:
            erasure_at_authority.set()
        with original_serialized(*args, **kwargs):
            yield

    def write():
        try:
            with original_serialized(profile):
                writer_has_authority.set()
                if not erasure_at_authority.wait(timeout=2):
                    raise TimeoutError("erasure did not reach authority")
                memory.block_write("notes", "Unrelated marker.")
        except BaseException as exc:
            errors.append(exc)

    def erase():
        try:
            erased.append(memory.occurrence_invalidate_matching("MINT-84", chat_scope=chat))
        except BaseException as exc:
            errors.append(exc)

    writer = threading.Thread(target=write, daemon=True)
    eraser = threading.Thread(target=erase, daemon=True)
    monkeypatch.setattr(memory, "_lock", _BoundedOwnerLock())
    monkeypatch.setattr(revocation, "serialized", observe_authority)
    try:
        writer.start()
        assert writer_has_authority.wait(timeout=2)
        eraser.start()
        writer.join(timeout=5)
        eraser.join(timeout=5)
        assert not writer.is_alive() and not eraser.is_alive()
        assert not errors, errors
        assert erased == [1]
        retained = memory.occurrence_get(occurrence.occurrence_id)
        assert retained is not None and "MINT-84" not in retained.body
        assert "garden gate is blue" in retained.body
    finally:
        writer.join(timeout=3)
        if eraser.ident is not None:
            eraser.join(timeout=3)
        memory.close()
