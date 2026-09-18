"""Persisted updater state: directory layout + the per-channel high-water store.

Everything lives under `<data>/update_v2/` (user data, never the app bundle) so it
survives updates. The high-water store is the durable half of the replay defense.
"""
from __future__ import annotations

from core.updater.state import HighWaterStore, UpdaterPaths


class TestUpdaterPaths:
    def test_layout_under_user_data(self, tmp_path):
        paths = UpdaterPaths.for_data_dir(tmp_path)
        assert paths.root == tmp_path / "update_v2"
        assert paths.root.is_dir()
        for sub in (paths.staging, paths.transactions, paths.receipts, paths.snapshots):
            assert sub.is_dir()
        assert paths.status_file == tmp_path / "update_v2" / "status.json"
        assert paths.high_water_file == tmp_path / "update_v2" / "high_water.json"

    def test_second_init_is_idempotent(self, tmp_path):
        UpdaterPaths.for_data_dir(tmp_path)
        again = UpdaterPaths.for_data_dir(tmp_path)
        assert again.root == tmp_path / "update_v2"


class TestHighWaterStore:
    def test_absent_channel_is_none(self, tmp_path):
        store = HighWaterStore(tmp_path / "high_water.json")
        assert store.load("stable") is None

    def test_record_then_load_roundtrip(self, tmp_path):
        store = HighWaterStore(tmp_path / "high_water.json")
        store.record("stable", sequence=47, version="0.6.0", manifest_sha256="ab" * 32, now=123.0)
        loaded = store.load("stable")
        assert loaded == {
            "sequence": 47,
            "version": "0.6.0",
            "manifest_sha256": "ab" * 32,
            "recorded_at": 123.0,
        }

    def test_channels_are_independent(self, tmp_path):
        store = HighWaterStore(tmp_path / "high_water.json")
        store.record("stable", sequence=10, version="0.5.0", manifest_sha256="a" * 64, now=1.0)
        store.record("beta", sequence=3, version="0.6.0-beta.1", manifest_sha256="b" * 64, now=2.0)
        assert store.load("stable")["sequence"] == 10
        assert store.load("beta")["sequence"] == 3

    def test_sequence_never_moves_backwards(self, tmp_path):
        store = HighWaterStore(tmp_path / "high_water.json")
        store.record("stable", sequence=47, version="0.6.0", manifest_sha256="a" * 64, now=1.0)
        store.record("stable", sequence=30, version="0.4.0", manifest_sha256="b" * 64, now=2.0)
        assert store.load("stable")["sequence"] == 47

    def test_corrupt_file_reads_as_empty(self, tmp_path):
        marker = tmp_path / "high_water.json"
        marker.write_text("{corrupt")
        store = HighWaterStore(marker)
        assert store.load("stable") is None

    def test_writes_are_atomic(self, tmp_path):
        store = HighWaterStore(tmp_path / "high_water.json")
        store.record("stable", sequence=1, version="0.5.0", manifest_sha256="a" * 64, now=1.0)
        assert not list(tmp_path.glob("*.tmp"))
