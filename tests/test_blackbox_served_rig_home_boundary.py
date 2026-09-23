"""The served rig's runtime-home boundary, as a law the rig itself must keep.

Every subprocess this rig assigns its own ``VOOL_HOME`` — the daemon behind
``ServedDaemon`` and each ``run_in_home`` snippet — owns that home's state. The test
session pins ``VOOL_PLUGIN_LIFECYCLE_PATH`` (tests/conftest.py) for THIS process, and
``core.plugin_lifecycle.store_path()`` resolves that pin BEFORE the ``VOOL_HOME``-derived
path, so an inherited pin silently redirected every real install/verify/enable the rig's
subprocesses performed into the SESSION's store. A pack activated under one home then
flipped availability for every later test in the process — the shard-9 projection-parity
failures of PR #35 (runs 35849609728 / 35858135891): green in the baseline composition,
red in both re-partitioned runs, and reproducible on macOS.

The rig therefore does not inherit the session pin across the home boundary. A caller
that DELIBERATELY pins a subprocess's lifecycle store still can — through ``env_extra``,
which both env constructions apply AFTER the drop, so the deliberate pin wins.
"""
import json
from pathlib import Path

from tests._blackbox_served_rig import REPO_ROOT, ServedDaemon, run_in_home


def _activation_script(pack: Path, plugin_id: str) -> str:
    return (
        "import sys\n"
        f"sys.path.insert(0, {str(REPO_ROOT)!r})\n"
        "from pathlib import Path\n"
        "from core.plugin_lifecycle import enable, install, store_path, verify\n"
        "print('store:', store_path())\n"
        f"pack = Path({str(pack)!r})\n"
        f"install({plugin_id!r}, root=pack, source='test-isolated')\n"
        f"verify({plugin_id!r}, root=pack)\n"
        f"enable({plugin_id!r})\n"
        "print('lifecycle: installed+verified+enabled')\n"
    )


def test_a_real_subprocess_activation_records_under_its_own_home_and_leaves_the_session_store_intact(tmp_path):
    from core import plugin_lifecycle
    from tests._toolchain_fixtures import make_plugin

    pack = make_plugin(tmp_path / "proot", plugin_id="rig-boundary-pack", admit=False)
    home = tmp_path / "home"
    home.mkdir()

    before = plugin_lifecycle.lifecycle_snapshot()["plugins"]
    out = run_in_home(home, _activation_script(pack, "rig-boundary-pack"))
    assert "lifecycle: installed+verified+enabled" in out, out

    # The activation is recorded under the isolated home, through its own store.
    home_store = home / "data" / "plugin_lifecycle.json"
    assert home_store.is_file(), "the subprocess store never landed under its VOOL_HOME"
    recorded = json.loads(home_store.read_text(encoding="utf-8"))
    row = recorded.get("plugins", {}).get("rig-boundary-pack")
    assert row and row.get("stage") == "enabled" and row.get("enabled") is True, recorded

    # The parent/session store is exactly what it was — the boundary held.
    assert plugin_lifecycle.lifecycle_snapshot()["plugins"] == before


def test_the_daemon_env_resolves_lifecycle_under_its_home_without_the_session_pin(tmp_path):
    daemon = ServedDaemon(tmp_path / "daemon-home")
    env = daemon.env()
    assert env["VOOL_HOME"] == str(tmp_path / "daemon-home")
    assert env.get("VOOL_PLUGIN_LIFECYCLE_PATH") is None, "the session pin leaked into the daemon env"


def test_a_deliberate_lifecycle_pin_supplied_by_the_caller_still_wins(tmp_path):
    from core import plugin_lifecycle
    from tests._toolchain_fixtures import make_plugin

    deliberate = tmp_path / "deliberate-lifecycle.json"
    pack = make_plugin(tmp_path / "proot", plugin_id="rig-deliberate-pin-pack", admit=False)
    home = tmp_path / "home"
    home.mkdir()

    before = plugin_lifecycle.lifecycle_snapshot()["plugins"]
    out = run_in_home(
        home,
        _activation_script(pack, "rig-deliberate-pin-pack"),
        env_extra={"VOOL_PLUGIN_LIFECYCLE_PATH": str(deliberate)},
    )
    assert "lifecycle: installed+verified+enabled" in out, out
    store_line = next(line for line in out.splitlines() if line.startswith("store:"))
    assert store_line == f"store: {deliberate}", store_line
    assert deliberate.is_file(), "the deliberate pin was not honored"
    assert not (home / "data" / "plugin_lifecycle.json").exists(), (
        "a deliberate pin must redirect the store, not stack a second one under the home"
    )

    # The explicit override redirects ONLY the subprocess: the session store is untouched.
    assert plugin_lifecycle.lifecycle_snapshot()["plugins"] == before

    # The same contract on the daemon side: env_extra is applied after the drop, so it wins.
    daemon = ServedDaemon(tmp_path / "daemon-home", env_extra={"VOOL_PLUGIN_LIFECYCLE_PATH": str(deliberate)})
    assert daemon.env()["VOOL_PLUGIN_LIFECYCLE_PATH"] == str(deliberate)
