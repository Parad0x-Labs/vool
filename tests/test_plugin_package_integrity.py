"""Approved package bytes must be the bytes a cached tool actually executes."""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest

from core import plugin_lifecycle as lifecycle
from core import plugin_tools, tool_registry
from core.plugin_package import PackageError, capture_package, execution_directory
from tests._toolchain_fixtures import admit_plugin, make_plugin, reset_toolchain_state


@pytest.fixture
def inventory(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_PLUGIN_LIFECYCLE_PATH", str(tmp_path / "lifecycle.json"))
    monkeypatch.setenv("VOOL_PLUGINS_DIR", str(tmp_path))
    monkeypatch.setenv("VOOL_HOME", str(tmp_path / "home"))
    reset_toolchain_state()
    yield tmp_path
    reset_toolchain_state()


def _cached(root: Path):
    plugin_tools.load_all(root)
    contract = tool_registry.tool_for_intent("pack.echo")
    assert contract is not None and contract.supported
    return contract


def test_changed_executable_cannot_run_through_a_cached_contract(inventory):
    pack = make_plugin(inventory)
    contract = _cached(inventory)
    handler = pack / "bin/run"
    handler.write_text(handler.read_text().replace('"echo:"', '"changed:"'))
    result = plugin_tools.execute_plugin_contract(contract, {"text": "hello"})
    assert not result.ok
    assert lifecycle.record_for("pack").invocations == 0


def test_vendored_dependency_changes_invalidate_approval(inventory):
    pack = make_plugin(inventory, admit=False)
    dependency = pack / "vendor/helper.py"
    dependency.parent.mkdir()
    dependency.write_text("VALUE = 1\n")
    lifecycle.install("pack", root=pack)
    lifecycle.verify("pack")
    lifecycle.enable("pack")
    assert lifecycle.is_available("pack")
    dependency.write_text("VALUE = 2\n")
    assert not lifecycle.is_available("pack")


def test_added_and_removed_dependency_files_change_identity(inventory):
    pack = make_plugin(inventory)
    original = lifecycle.manifest_digest(pack)
    lock = pack / "dependencies.lock"
    lock.write_text(json.dumps({"helper": "1.0"}))
    assert lifecycle.manifest_digest(pack) != original
    lock.unlink()
    assert lifecycle.manifest_digest(pack) == original


@pytest.mark.parametrize("name", ["LICENSE", "vendor/native.so", "references/hidden.bin", ".hidden"])
def test_all_package_files_are_approval_material(inventory, name):
    pack = make_plugin(inventory, admit=False)
    extra = pack / name
    extra.parent.mkdir(parents=True, exist_ok=True)
    extra.write_bytes(b"first")
    admit_plugin("pack", pack)
    assert lifecycle.is_available("pack")
    extra.write_bytes(b"second")
    assert not lifecycle.is_available("pack")


def test_identity_covers_paths_empty_directories_and_executable_bits(inventory):
    pack = make_plugin(inventory)
    original = capture_package(pack).digest
    directory = pack / "empty"
    directory.mkdir()
    assert capture_package(pack).digest != original
    directory.rmdir()
    handler = pack / "bin/run"
    handler.chmod(0o644)
    assert capture_package(pack).digest != original
    handler.chmod(0o755)
    assert capture_package(pack).digest == original
    handler.rename(pack / "bin/renamed")
    assert capture_package(pack).digest != original


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "fifo"])
def test_links_and_special_files_fail_closed(inventory, kind):
    pack = make_plugin(inventory)
    added = pack / "unsafe"
    if kind == "symlink":
        added.symlink_to(pack / "bin/run")
    elif kind == "hardlink":
        os.link(pack / "bin/run", added)
    else:
        os.mkfifo(added)
    assert not lifecycle.is_available("pack")
    with pytest.raises(PackageError):
        capture_package(pack)


def test_bounded_capture_refuses_oversized_files(inventory):
    from core.plugin_package import MAX_FILE_BYTES
    pack = make_plugin(inventory)
    with (pack / "too-large").open("wb") as output:
        output.truncate(MAX_FILE_BYTES + 1)
    assert not lifecycle.is_available("pack")


def test_execution_copy_has_same_identity_and_is_removed(inventory):
    pack = make_plugin(inventory)
    identity = capture_package(pack)
    with execution_directory(inventory / "copies") as owned:
        target = owned / "package"
        captured = capture_package(pack, destination=target)
        assert captured.digest == identity.digest == capture_package(target).digest
        assert not (target.stat().st_mode & 0o222)
        assert not ((target / "bin/run").stat().st_mode & 0o222)
    assert not owned.exists()
    assert (pack / "bin/run").exists()


def test_dependency_reapproval_requires_parent_reapproval(inventory):
    child = make_plugin(inventory, plugin_id="child")
    parent = make_plugin(inventory, manifest_extra={"requires": ["child"]})
    assert lifecycle.record_for("pack").verified_dependencies == {"child": lifecycle.record_for("child").approval_digest}
    assert lifecycle.is_available("pack")
    (child / "dependency.lock").write_text("v2")
    assert not lifecycle.is_available("pack")
    lifecycle.verify("child")
    lifecycle.enable("child")
    assert lifecycle.is_available("child")
    assert not lifecycle.is_available("pack")
    previous = lifecycle.record_for("pack").approval_digest
    lifecycle.verify("pack")
    lifecycle.enable("pack")
    assert lifecycle.is_available("pack")
    assert lifecycle.record_for("pack").approval_digest != previous
    lifecycle.disable("child")
    assert not lifecycle.is_available("pack")
    assert lifecycle.manifest_digest(parent)


def test_transitive_dependency_revocation_refuses_cached_execution(inventory):
    make_plugin(inventory, plugin_id="leaf")
    make_plugin(inventory, plugin_id="child", manifest_extra={"requires": ["leaf"]})
    make_plugin(inventory, manifest_extra={"requires": ["child"]})
    contract = _cached(inventory)
    lifecycle.revoke("leaf", reason="withdrawn")
    assert not lifecycle.is_available("pack")
    assert not plugin_tools.execute_plugin_contract(contract, {"text": "hello"}).ok


def test_reapproving_transitive_dependency_does_not_silently_reapprove_parent(inventory):
    leaf = make_plugin(inventory, plugin_id="leaf")
    make_plugin(inventory, plugin_id="child", manifest_extra={"requires": ["leaf"]})
    make_plugin(inventory, manifest_extra={"requires": ["child"]})
    assert lifecycle.is_available("pack")
    (leaf / "new-library.py").write_text("VALUE = 2\n")
    lifecycle.verify("leaf")
    lifecycle.enable("leaf")
    lifecycle.verify("child")
    lifecycle.enable("child")
    assert lifecycle.is_available("child")
    assert not lifecycle.is_available("pack")

    previous = lifecycle.record_for("pack").approval_digest
    lifecycle.verify("pack")
    lifecycle.enable("pack")
    assert lifecycle.is_available("pack")
    assert lifecycle.record_for("pack").approval_digest != previous


def test_missing_dependency_cannot_be_verified(inventory):
    child = make_plugin(inventory, plugin_id="missing", admit=False)
    lifecycle.install("missing", root=child)
    pack = make_plugin(inventory, manifest_extra={"requires": ["missing"]}, admit=False)
    lifecycle.install("pack", root=pack)
    with pytest.raises(lifecycle.LifecycleError, match="verified and enabled"):
        lifecycle.verify("pack")


def test_legacy_approval_is_preserved_but_requires_explicit_verification(inventory):
    make_plugin(inventory)
    state = json.loads(lifecycle.store_path().read_text())
    # Simulate a persisted v1 approval, preserving its evidence rather than deleting it.
    record = next(row for row in state["plugins"].values() if row["plugin_id"] == "pack")
    record.pop("digest_schema")
    lifecycle.store_path().write_text(json.dumps(state))
    assert not lifecycle.is_available("pack")
    with pytest.raises(lifecycle.LifecycleError, match="complete package verification"):
        lifecycle.enable("pack")
    lifecycle.verify("pack")
    lifecycle.enable("pack")
    assert lifecycle.is_available("pack")
    assert any(row["event"] == "installed" for row in lifecycle.evidence("pack"))


def test_manifest_reapproval_refreshes_cached_contracts(inventory):
    pack = make_plugin(inventory)
    old = _cached(inventory)
    manifest = pack / ".codex-plugin/plugin.json"
    payload = json.loads(manifest.read_text())
    payload["tools"][0]["description"] = "Changed reviewed contract"
    manifest.write_text(json.dumps(payload))
    lifecycle.verify("pack")
    lifecycle.enable("pack")
    fresh = _cached(inventory)
    assert fresh != old
    assert not plugin_tools.execute_plugin_contract(old, {"text": "hello"}).ok
    result = plugin_tools.execute_plugin_contract(fresh, {"text": "hello"})
    assert result.ok and result.text == "echo:hello"
    assert result.observation["package_sha256"] == lifecycle.record_for("pack").verified_digest
    assert result.observation["approval_sha256"] == lifecycle.record_for("pack").approval_digest
    assert result.confinement.startswith("kernel:")
    assert not list((inventory / "scratch/.vool-packages").glob("invocation-*"))


def test_handler_runs_with_captured_dependency_bytes(inventory):
    child = make_plugin(inventory, plugin_id="child")
    (child / "value.txt").write_text("approved dependency")
    lifecycle.verify("child")
    lifecycle.enable("child")
    pack = make_plugin(inventory, manifest_extra={"requires": ["child"]}, admit=False)
    handler = pack / "bin/run"
    handler.write_text(f"#!{sys.executable}\n" + '''import json, os
from pathlib import Path
deps = json.loads(os.environ["VOOL_PLUGIN_DEPENDENCIES"])
value = (Path(deps["child"]) / "value.txt").read_text()
print(json.dumps({"ok": True, "text": value}))
''')
    admit_plugin("pack", pack)
    result = plugin_tools.execute_plugin_contract(_cached(inventory), {"text": "hello"})
    assert result.ok and result.text == "approved dependency"
    assert result.observation["dependency_sha256"] == {"child": lifecycle.manifest_digest(child)}
    assert result.observation["dependency_approval_sha256"] == {"child": lifecycle.record_for("child").approval_digest}


def test_mutating_handler_can_write_scratch_but_cannot_modify_approved_code(inventory):
    pack = make_plugin(inventory, admit=False)
    handler = pack / "bin/run"
    handler.write_text(f"#!{sys.executable}\n" + '''import json, os
from pathlib import Path
blocked = False
try:
    Path(__file__).chmod(0o755)
    Path(__file__).write_text("replaced")
except OSError:
    blocked = True
output = Path(os.environ["PLUGIN_SCRATCH"]) / "written"
output.write_text("allowed")
print(json.dumps({"ok": blocked, "text": str(output)}))
''')
    admit_plugin("pack", pack)
    plugin_tools.load_all(inventory)
    contract = tool_registry.tool_for_intent("pack.touch")
    result = plugin_tools.execute_plugin_contract(contract, {"path": "unused"})
    assert result.ok
    assert Path(result.text).read_text() == "allowed"
    assert lifecycle.is_available("pack")


def test_source_changed_during_capture_is_refused_before_launch(inventory, monkeypatch):
    from core import plugin_package
    pack = make_plugin(inventory)
    contract = _cached(inventory)
    capture = plugin_package.capture_package

    def sabotage(root, *, destination=None):
        identity = capture(root, destination=destination)
        if destination is not None and Path(root) == pack:
            (pack / "bin/run").write_text("unapproved executable")
        return identity

    # Inject a filesystem edit after a REAL capture; neither capture nor execution is mocked.
    monkeypatch.setattr(plugin_package, "capture_package", sabotage)
    result = plugin_tools.execute_plugin_contract(contract, {"text": "hello"})
    assert not result.ok and result.status == "package_integrity_failed"
    assert lifecycle.record_for("pack").invocations == 0
    assert not list((inventory / "scratch/.vool-packages").glob("invocation-*"))


def test_timeout_stops_owned_descendants_before_copy_cleanup(inventory):
    from tests._toolchain_fixtures import default_tools
    tools = default_tools()
    tools[1]["handler"]["timeout_seconds"] = 1
    pack = make_plugin(inventory, tools=tools, admit=False)
    handler = pack / "bin/run"
    handler.write_text(f"#!{sys.executable}\n" + '''import os, time
from pathlib import Path
root = Path(os.environ["PLUGIN_SCRATCH"])
if os.fork() == 0:
    time.sleep(2)
    (root / "escaped-timeout").write_text("child survived")
    os._exit(0)
(root / "started").write_text("parent and child started")
time.sleep(10)
''')
    admit_plugin("pack", pack)
    plugin_tools.load_all(inventory)
    result = plugin_tools.execute_plugin_contract(tool_registry.tool_for_intent("pack.touch"), {"path": "unused"})
    scratch = plugin_tools.plugin_scratch_dir("pack")
    assert (scratch / "started").exists()
    assert not result.ok and result.status == "timeout"
    assert not list((inventory / "scratch/.vool-packages").glob("invocation-*"))
    time.sleep(2)
    assert not (scratch / "escaped-timeout").exists()
    assert lifecycle.is_available("pack")
