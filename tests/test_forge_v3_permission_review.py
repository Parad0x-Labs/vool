"""A read/hash is not operator approval of a replacement outside a code task."""
import hashlib

import pytest

from core.mode_permission_policy import (
    PermissionEffect, decide_tool_call, reset_mode_permission_state,
)


@pytest.mark.parametrize("surface", ["workspace", "machine"])
def test_hash_alone_does_not_approve_unrelated_existing_file_replacement(tmp_path, monkeypatch, surface):
    reset_mode_permission_state()
    home = tmp_path / "disposable_home"
    desktop = home / "Desktop"
    desktop.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    root = tmp_path / "workspace"
    root.mkdir()
    target = (root if surface == "workspace" else desktop) / "existing.txt"
    target.write_text("Existing work must retain overwrite approval.\n")
    path = "existing.txt" if surface == "workspace" else "Desktop/existing.txt"
    args = {"path": path, "content": "Entirely different replacement\n"}
    ctx = {"operating_mode": "auto", "runtime_session_id": f"unrelated-{surface}",
           "workspace_root": str(root)}
    try:
        blind = decide_tool_call(intent=f"{surface}.write_file", arguments=args,
                                 task_id="no-approved-code-task", source_context=ctx)
        assert blind.effect is PermissionEffect.REQUIRE_APPROVAL
        args["expected_hash"] = hashlib.sha256(target.read_bytes()).hexdigest()
        hashed = decide_tool_call(intent=f"{surface}.write_file", arguments=args,
                                  task_id="no-approved-code-task", source_context=ctx)
        assert hashed.effect is PermissionEffect.REQUIRE_APPROVAL, hashed
    finally:
        reset_mode_permission_state()
