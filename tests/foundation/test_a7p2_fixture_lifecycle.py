"""Fixture authority restoration under setup, suspension and cleanup failures."""

import pytest

from core import runtime_continuity as rc
from core.conductor import obligation_ledger as ol
from storage import db as sdb
from storage import migrations
from tests.foundation import test_a7p2_concurrency_crash as owner


@pytest.mark.parametrize("exit_path", ["setup_failure", "generator_close", "body_exception", "cleanup_exception"])
def test_fresh_store_restores_its_real_authorities_on_every_exit(tmp_path, monkeypatch, exit_path):
    before_default = sdb._DEFAULT_DB_PATH_OVERRIDE
    before_continuity = rc._DB_PATH_OVERRIDE
    before_binding = ol.active_set()
    outer_db = tmp_path / "outer.db"
    sdb.configure_default_db_path(outer_db)
    rc.configure_runtime_continuity_db_path(str(outer_db))
    ol.bind_active_set("outer-fixture-scope", "v1:outer-fixture-scope")
    expected = (sdb._DEFAULT_DB_PATH_OVERRIDE, rc._DB_PATH_OVERRIDE, ol.active_set())

    def fault(*args, **kwargs):
        raise RuntimeError("fixture injected failure")

    cleanup_probe = {"armed": False, "calls": 0}
    original_clear = owner.clear_execution_context

    def cleanup_fault():
        if cleanup_probe["armed"]:
            cleanup_probe["calls"] += 1
            fault()
        original_clear()

    if exit_path == "cleanup_exception":
        # Install before the fixture captures its cleanup callback; arm only
        # after setup so the failure actually occurs in teardown.
        monkeypatch.setattr(owner, "clear_execution_context", cleanup_fault)
    fn = owner.fresh_store._fixture_function
    gen = fn(tmp_path / "inner")
    try:
        if exit_path == "setup_failure":
            monkeypatch.setattr(migrations, "run_migrations", fault)
            with pytest.raises(RuntimeError, match="fixture injected failure"):
                next(gen)
        else:
            next(gen)
            if exit_path == "cleanup_exception":
                cleanup_probe["armed"] = True
                with pytest.raises(RuntimeError, match="fixture injected failure"):
                    next(gen)
                assert cleanup_probe["calls"] == 1
            elif exit_path == "generator_close":
                gen.close()
            else:
                with pytest.raises(RuntimeError, match="fixture body failure"):
                    gen.throw(RuntimeError("fixture body failure"))
        actual = (sdb._DEFAULT_DB_PATH_OVERRIDE, rc._DB_PATH_OVERRIDE, ol.active_set())
        assert actual == expected, (exit_path, expected, actual)
    finally:
        gen.close()
        sdb.configure_default_db_path(before_default)
        rc.configure_runtime_continuity_db_path(before_continuity)
        if before_binding is None:
            ol.clear_active_set()
        else:
            ol.bind_active_set(*before_binding)
