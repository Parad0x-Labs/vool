"""Startup accepts a locally proven successor of an expired shipped anchor."""
import sqlite3
import time

import pytest

from core.registry import client, native
from core.registry.codec import canonical
from core.registry.trust import TrustedCatalog
from tests.registry.helpers import signed, trust_fixture


@pytest.mark.parametrize("state_kind", ["one-rotation", "two-rotations", "legacy", "missing", "unrelated", "corrupt", "directory", "gap", "expired", "bad-envelope"])
def test_expired_anchor_uses_only_valid_persisted_rotation_without_writes(tmp_path, monkeypatch, state_kind):
    now = int(time.time())
    _, keys, root = trust_fixture()
    old = signed({**root["signed"], "expires": now - 1}, keys["root"])
    anchor = tmp_path / "anchor.json"
    anchor.write_bytes(canonical(old))
    home = tmp_path / "runtime"
    monkeypatch.setenv("VOOL_HOME", str(home))
    monkeypatch.setenv("VOOL_REGISTRY_ROOT_FILE", str(anchor))
    state = home / "config" / native.STATE_NAME
    if state_kind != "missing":
        catalog = TrustedCatalog(old, state, now=now - 100)
        _, next_keys, next_root = trust_fixture()
        second = signed({**next_root["signed"], "version": 2, "expires": now + 3600}, keys["root"] | next_keys["root"])
        catalog.rotate(second, now=now - 10)
        if state_kind in {"two-rotations", "gap"}:
            _, third_keys, third_root = trust_fixture()
            third = signed({**third_root["signed"], "version": 3, "expires": now + 7200}, next_keys["root"] | third_keys["root"])
            catalog.rotate(third, now=now - 5)
        if state_kind == "unrelated":
            unrelated = signed({**next_root["signed"], "version": 2, "expires": now + 3600}, next_keys["root"])
            catalog.db.execute("UPDATE trust SET root=?", (canonical(unrelated),))
        if state_kind == "expired":
            expired = signed({**next_root["signed"], "version": 2, "expires": now - 1}, keys["root"] | next_keys["root"])
            catalog.db.execute("UPDATE trust SET root=?", (canonical(expired),))
        if state_kind in {"legacy", "gap"}:
            catalog.db.execute("DROP TABLE root_history")
        if state_kind == "bad-envelope":
            catalog.db.execute("UPDATE trust SET root=?", (b"[]",))
        catalog.db.commit()
        catalog.close()
        if state_kind == "corrupt":
            state.write_bytes(b"invalid database")
        if state_kind == "directory":
            state.unlink()
            state.mkdir()
    before = {p.relative_to(tmp_path): (p.read_bytes(), p.stat().st_mtime_ns) for p in tmp_path.rglob("*") if p.is_file()}
    assert client.is_open() is (state_kind in {"one-rotation", "two-rotations", "legacy"})
    assert {p.relative_to(tmp_path): (p.read_bytes(), p.stat().st_mtime_ns) for p in tmp_path.rglob("*") if p.is_file()} == before
    if state_kind in {"one-rotation", "two-rotations", "legacy"}:
        assert client._read_anchor()["signed"]["version"] == (3 if state_kind == "two-rotations" else 2)
        db = sqlite3.connect(state.as_uri() + "?mode=ro", uri=True)
        db.close()
