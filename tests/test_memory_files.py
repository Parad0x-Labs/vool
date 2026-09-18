from __future__ import annotations

import json

from core.memory.files import (
    MAX_MEMORY_INDEX_BYTES,
    append_jsonl,
    conversation_log_path,
    ensure_memory_files,
    load_jsonl,
    memory_entries_path,
    memory_path,
    operator_dense_profile_path,
    rewrite_jsonl,
    session_summaries_path,
    trim_jsonl_file,
    user_heuristics_path,
)


def setup_function() -> None:
    for path in (
        memory_path(),
        conversation_log_path(),
        memory_entries_path(),
        session_summaries_path(),
        user_heuristics_path(),
        operator_dense_profile_path(),
    ):
        if path.exists():
            path.unlink()


def test_ensure_memory_files_creates_expected_surface() -> None:
    calls: list[str] = []

    ensure_memory_files(ensure_policy_table=lambda: calls.append("policy"))

    assert calls == ["policy"]
    assert memory_path().exists()
    assert conversation_log_path().exists()
    assert memory_entries_path().exists()
    assert session_summaries_path().exists()
    assert user_heuristics_path().exists()
    assert operator_dense_profile_path().exists()
    assert "## Learned Knowledge" in memory_path().read_text(encoding="utf-8")
    assert operator_dense_profile_path().read_text(encoding="utf-8") == "{}"


def test_jsonl_helpers_roundtrip_rows() -> None:
    rows = [
        {"created_at": "2026-03-23T00:00:00+00:00", "text": "alpha"},
        {"created_at": "2026-03-23T01:00:00+00:00", "text": "beta"},
    ]
    for row in rows:
        append_jsonl(memory_entries_path(), row)

    assert load_jsonl(memory_entries_path()) == rows

    rewritten = [rows[1]]
    rewrite_jsonl(memory_entries_path(), rewritten)
    assert load_jsonl(memory_entries_path()) == rewritten


def test_trim_jsonl_file_keeps_newer_half_when_over_budget() -> None:
    rows = [{"index": index, "text": f"row-{index}"} for index in range(12)]
    rewrite_jsonl(memory_entries_path(), rows)

    trim_jsonl_file(memory_entries_path(), max_bytes=max(32, MAX_MEMORY_INDEX_BYTES // 65536))

    kept = load_jsonl(memory_entries_path())
    assert kept
    assert kept != rows
    assert kept[0]["index"] >= rows[len(rows) // 2]["index"]
    assert json.loads(memory_entries_path().read_text(encoding="utf-8").splitlines()[0])["index"] == kept[0]["index"]
