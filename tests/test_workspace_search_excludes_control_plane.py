"""The runtime's own telemetry must not be evidence for the user's code search.

`core/control_plane_workspace.py` writes `<workspace_root>/control/` -- run ledgers, metrics, and the
verbatim `final_response` of every past turn. When the fallback workspace is also the search root,
that directory sits inside the corpus `workspace.search_text` / `workspace.symbol_search` grep.

Measured 2026-07-31 on the live daemon: a turn answered "which file defines handle_inspect_processes"
with `workspace/process_inspector.py` -- a path that exists nowhere. The answer was persisted to
`control/runs/`, and a later search for that symbol returned the runtime's own earlier fabrication as
a match. 122 copies of the invented path had accumulated across `control/runs/` and
`control/metrics/`, growing with every probe. Each fabrication became evidence for the next.

The exclusion is keyed on the control-plane MARKER file, never on the directory name, so a user
project with a genuine `control/` source directory stays fully searchable. Both halves are asserted
here: the marker present means excluded, the marker absent means searchable.
"""
from __future__ import annotations

import json
from pathlib import Path

from core.execution.workspace_tools import _workspace_file_is_ignored


def _control_plane_workspace(root: Path) -> Path:
    """A workspace root shaped like the runtime's own fallback: control plane + real user code."""
    marker = root / "control" / "metrics" / "runtime_truth.json"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps({"runtime": "vool"}))

    checkpoints = root / "control" / "runs" / "runtime_checkpoints.json"
    checkpoints.parent.mkdir(parents=True, exist_ok=True)
    checkpoints.write_text(json.dumps([{
        "final_response": "The function `handle_inspect_processes` is defined in the file "
                          "`workspace/process_inspector.py`.",
    }]))

    (root / "control" / "metrics" / "overview.json").write_text(
        json.dumps({"title": "which file defines handle_inspect_processes"})
    )

    user_code = root / "src" / "real_module.py"
    user_code.parent.mkdir(parents=True, exist_ok=True)
    user_code.write_text("def handle_inspect_processes():\n    return []\n")
    return root


def test_the_runtimes_own_run_ledger_is_not_searchable(tmp_path) -> None:
    root = _control_plane_workspace(tmp_path)
    ledger = root / "control" / "runs" / "runtime_checkpoints.json"

    # The file really does carry the fabricated path -- the fixture is not vacuous.
    assert "process_inspector.py" in ledger.read_text()

    assert _workspace_file_is_ignored(ledger, workspace_root=root) is True
    assert _workspace_file_is_ignored(
        root / "control" / "metrics" / "overview.json", workspace_root=root
    ) is True
    assert _workspace_file_is_ignored(
        root / "control" / "metrics" / "runtime_truth.json", workspace_root=root
    ) is True


def test_the_users_own_code_beside_it_is_still_searchable(tmp_path) -> None:
    """The exclusion must not become a blanket hole in the search corpus."""
    root = _control_plane_workspace(tmp_path)
    assert _workspace_file_is_ignored(root / "src" / "real_module.py", workspace_root=root) is False


def test_a_project_with_a_genuine_control_directory_is_untouched(tmp_path) -> None:
    """No marker means it is the user's own `control/` package, not the runtime's telemetry.

    This is the reason the rule keys on the marker file and not on the directory name: `control/` is
    an ordinary name for a robotics, PID, or access-control package, and excluding it by name would
    silently hide a real source tree from the user's own search.
    """
    root = tmp_path
    source = root / "control" / "pid_loop.py"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("def handle_inspect_processes():\n    return []\n")

    assert not (root / "control" / "metrics" / "runtime_truth.json").exists()
    assert _workspace_file_is_ignored(source, workspace_root=root) is False


def test_a_nested_control_directory_is_not_mistaken_for_the_control_plane(tmp_path) -> None:
    """Only a DEPTH-1 `control/` is the runtime's; `src/control/` is the user's code."""
    root = _control_plane_workspace(tmp_path)
    nested = root / "src" / "control" / "loop.py"
    nested.parent.mkdir(parents=True, exist_ok=True)
    nested.write_text("x = 1\n")

    assert _workspace_file_is_ignored(nested, workspace_root=root) is False
