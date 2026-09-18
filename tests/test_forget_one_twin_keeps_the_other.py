"""Forgetting one record by id leaves a twin with the same text in place, and forgetting a unique record erases its text.

Measured 2026-09-07 (`test_forget_memory_record_is_id_scoped`, red): the success-after-verification law
re-read every model-visible path for the erased TEXT and raised because the twin still carried it. The
forget is id-scoped by design; the verifier must not treat a surviving row's own text as a leak.
"""
from __future__ import annotations

import json


def _rows(store):
    return [json.loads(line) for line in store.read_text().splitlines() if line.strip()]


def test_forgetting_the_middle_of_three_twins_keeps_the_other_two(tmp_path, monkeypatch):
    import core.memory.entries as entries

    store = tmp_path / "memory_entries.jsonl"
    rows = [
        {"record_id": "t1", "text": "prefers metric units"},
        {"record_id": "t2", "text": "prefers metric units"},
        {"record_id": "t3", "text": "prefers metric units"},
        {"record_id": "u1", "text": "commutes by bicycle on weekdays"},
    ]
    store.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    monkeypatch.setattr(entries, "memory_entries_path", lambda: store)
    monkeypatch.setattr(entries, "_ensure_memory_files", lambda: None)
    assert entries.forget_memory_record("t2") is True
    live = [r["record_id"] for r in _rows(store) if r.get("status") != "erased"]
    assert live == ["t1", "t3", "u1"], live


def test_forgetting_a_unique_record_erases_its_text_everywhere(tmp_path, monkeypatch):
    import core.memory.entries as entries

    store = tmp_path / "memory_entries.jsonl"
    rows = [
        {"record_id": "t1", "text": "prefers metric units"},
        {"record_id": "u1", "text": "commutes by bicycle on weekdays"},
    ]
    store.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    monkeypatch.setattr(entries, "memory_entries_path", lambda: store)
    monkeypatch.setattr(entries, "_ensure_memory_files", lambda: None)
    assert entries.forget_memory_record("u1") is True
    texts = [r["text"] for r in _rows(store) if r.get("status") != "erased"]
    assert "commutes by bicycle on weekdays" not in texts
    assert entries.forget_memory_record("u1") is False
