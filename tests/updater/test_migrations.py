"""Transactional migrations: forward, mid-failure rollback, too-new data, chaining."""
from __future__ import annotations

import json

from core.updater.migrations import (
    MigrationCatalog,
    MigrationReason,
    MigrationStep,
    read_recorded_schema,
    run_migrations,
    write_recorded_schema,
)


def _write_config(home, *, setting: str) -> None:
    config = home / "config"
    config.mkdir(parents=True, exist_ok=True)
    (config / "settings.json").write_text(json.dumps({"setting": setting}), encoding="utf-8")


def _read_config(home) -> dict:
    return json.loads((home / "config" / "settings.json").read_text(encoding="utf-8"))


def _step(name: str, frm: str, to: str, *, forward, backward=None, paths=("config",)) -> MigrationStep:
    return MigrationStep(name=name, from_version=frm, to_version=to, affected_paths=paths, forward=forward, backward=backward)


class TestHappyPath:
    def test_single_hop_migration(self, tmp_path):
        home = tmp_path / "home"
        _write_config(home, setting="old")

        def forward(ctx):
            data = _read_config(ctx.user_home)
            data["setting"] = "new"
            (ctx.user_home / "config" / "settings.json").write_text(json.dumps(data), encoding="utf-8")

        catalog = MigrationCatalog()
        catalog.register(_step("bump", "0.5.0", "0.6.0", forward=forward))
        result = run_migrations(
            catalog, from_version="0.5.0", to_version="0.6.0", user_home=home, snapshot_root=tmp_path / "snaps"
        )
        assert result.ok, result.detail
        assert result.applied == ["bump"]
        assert _read_config(home)["setting"] == "new"
        assert read_recorded_schema(home) == "0.6.0"

    def test_same_version_is_a_no_op(self, tmp_path):
        catalog = MigrationCatalog()
        result = run_migrations(
            catalog, from_version="0.6.0", to_version="0.6.0", user_home=tmp_path, snapshot_root=tmp_path / "snaps"
        )
        assert result.ok

    def test_hops_chain_in_order(self, tmp_path):
        order: list[str] = []
        catalog = MigrationCatalog()
        catalog.register(_step("a", "0.4.0", "0.5.0", forward=lambda ctx: order.append("a")))
        catalog.register(_step("b", "0.5.0", "0.6.0", forward=lambda ctx: order.append("b")))
        catalog.register(_step("c", "0.6.0", "0.7.0", forward=lambda ctx: order.append("c")))
        result = run_migrations(
            catalog, from_version="0.4.0", to_version="0.6.0", user_home=tmp_path, snapshot_root=tmp_path / "snaps"
        )
        assert result.ok
        assert order == ["a", "b"]

    def test_missing_path_is_refused_not_skipped(self, tmp_path):
        catalog = MigrationCatalog()
        result = run_migrations(
            catalog, from_version="0.5.0", to_version="0.9.0", user_home=tmp_path, snapshot_root=tmp_path / "snaps"
        )
        assert not result.ok
        assert result.reason is MigrationReason.NO_PATH


class TestFailureRollback:
    def test_mid_sequence_failure_restores_everything(self, tmp_path):
        home = tmp_path / "home"
        _write_config(home, setting="original")

        def good_forward(ctx):
            data = _read_config(ctx.user_home)
            data["setting"] = "migrated-once"
            (ctx.user_home / "config" / "settings.json").write_text(json.dumps(data), encoding="utf-8")

        def bad_forward(ctx):
            raise RuntimeError("migration bug")

        undo: list[str] = []

        def good_backward(ctx):
            undo.append(ctx.step_name)

        catalog = MigrationCatalog()
        catalog.register(_step("first", "0.4.0", "0.5.0", forward=good_forward, backward=good_backward))
        catalog.register(_step("second", "0.5.0", "0.6.0", forward=bad_forward, backward=good_backward))

        result = run_migrations(
            catalog, from_version="0.4.0", to_version="0.6.0", user_home=home, snapshot_root=tmp_path / "snaps"
        )
        assert not result.ok
        assert result.reason is MigrationReason.STEP_FAILED
        # the first hop's backward hook ran…
        assert undo == ["first"]
        # …AND the snapshot restore put the original bytes back
        assert _read_config(home)["setting"] == "original"
        assert result.rolled_back == ["first"]

    def test_files_created_by_a_failed_step_are_removed(self, tmp_path):
        home = tmp_path / "home"
        home.mkdir(parents=True)

        def creates_then_fails(ctx):
            (ctx.user_home / "config").mkdir(parents=True, exist_ok=True)
            (ctx.user_home / "config" / "new-file.json").write_text("{}", encoding="utf-8")
            raise RuntimeError("bug after creating a file")

        catalog = MigrationCatalog()
        catalog.register(_step("creates", "0.5.0", "0.6.0", forward=creates_then_fails))
        result = run_migrations(
            catalog, from_version="0.5.0", to_version="0.6.0", user_home=home, snapshot_root=tmp_path / "snaps"
        )
        assert not result.ok
        assert not (home / "config" / "new-file.json").exists()

    def test_recorded_schema_not_advanced_on_failure(self, tmp_path):
        home = tmp_path / "home"
        write_recorded_schema(home, "0.5.0")

        def bad(ctx):
            raise RuntimeError("nope")

        catalog = MigrationCatalog()
        catalog.register(_step("bad", "0.5.0", "0.6.0", forward=bad))
        run_migrations(catalog, from_version="0.5.0", to_version="0.6.0", user_home=home, snapshot_root=tmp_path / "snaps")
        assert read_recorded_schema(home) == "0.5.0"


class TestDataTooNew:
    def test_schema_newer_than_catalog_is_refused(self, tmp_path):
        home = tmp_path / "home"
        home.mkdir(parents=True)
        write_recorded_schema(home, "9.9.9")
        catalog = MigrationCatalog()
        catalog.register(_step("tiny", "0.5.0", "0.6.0", forward=lambda ctx: None))
        result = run_migrations(
            catalog, from_version="0.5.0", to_version="0.6.0", user_home=home, snapshot_root=tmp_path / "snaps"
        )
        assert not result.ok
        assert result.reason is MigrationReason.DATA_TOO_NEW
        assert "newer" in result.plain_message

    def test_schema_within_catalog_proceeds(self, tmp_path):
        home = tmp_path / "home"
        home.mkdir(parents=True)
        write_recorded_schema(home, "0.5.0")
        catalog = MigrationCatalog()
        catalog.register(_step("tiny", "0.5.0", "0.6.0", forward=lambda ctx: None))
        result = run_migrations(
            catalog, from_version="0.5.0", to_version="0.6.0", user_home=home, snapshot_root=tmp_path / "snaps"
        )
        assert result.ok
