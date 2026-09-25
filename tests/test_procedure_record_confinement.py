"""Procedure identifiers must not escape their persistent record store."""
from __future__ import annotations

from dataclasses import replace

import pytest

from core.learning import procedure_shards as store


def record(key="procedure-normal"):
    return store.ProcedureShardV1.create(procedure_id=key, task_class="fixture", title="fixture",
        preconditions=[], steps=["step"], tool_receipts=[], validation={}, rollback={},
        privacy_class="local_private", shareability="local_only", success_signal="fixture")


@pytest.fixture
def root(tmp_path, monkeypatch):
    path = tmp_path / "procedures"
    path.mkdir()
    monkeypatch.setattr(store, "data_path", lambda *args: path)
    return path


def test_forget_cannot_delete_a_json_outside_the_procedure_store(root):
    outside = root.parent / "outside.json"
    outside.write_text("preserved")
    assert store.delete_procedure("../outside") is False
    assert outside.read_text() == "preserved"


def test_save_cannot_publish_a_path_as_a_procedure_id(root):
    with pytest.raises(ValueError):
        store.save_procedure_shard(record("../outside"))
    assert not (root.parent / "outside.json").exists()


def test_update_cannot_read_or_modify_an_external_record(root):
    outside = root.parent / "outside.json"
    import json
    original = json.dumps(record("../outside").to_dict())
    outside.write_text(original)
    assert store.update_procedure_shard("../outside", lambda item: replace(item, title="changed")) is None
    assert outside.read_text() == original


def test_valid_record_create_update_and_forget_remain_functional(root):
    store.save_procedure_shard(record())
    updated = store.update_procedure_shard("procedure-normal", lambda item: replace(item, title="changed"))
    assert updated.title == "changed"
    assert store.load_procedure_shards()[0].title == "changed"
    assert store.delete_procedure("procedure-normal") is True
    assert store.load_procedure_shards() == []


@pytest.mark.parametrize("suffix", [".json", ".json.lock"])
def test_record_operations_refuse_symlinks_and_preserve_the_target(root, suffix):
    outside = root.parent / "outside.json"
    outside.write_text("preserved")
    (root / ("procedure-normal" + suffix)).symlink_to(outside)
    assert store.delete_procedure("procedure-normal") is False
    assert store.update_procedure_shard("procedure-normal", lambda item: item) is None
    with pytest.raises(ValueError):
        store.save_procedure_shard(record())
    assert outside.read_text() == "preserved"


def test_find_or_create_rejects_path_syntax_before_calling_the_builder(root):
    def unexpected():
        pytest.fail("invalid record ID reached the builder")
    with pytest.raises(ValueError):
        store.find_or_create_procedure_shard("../outside", unexpected, lambda item: item)
    assert not (root.parent / "outside.json.lock").exists()


def test_mutation_cannot_change_the_record_identity(root):
    store.save_procedure_shard(record())
    with pytest.raises(ValueError):
        store.update_procedure_shard("procedure-normal", lambda item: replace(item, procedure_id="other"))
    assert store.load_procedure_shards()[0].procedure_id == "procedure-normal"
