"""The launch-blocking repairs from the owner's final beta pass (2026-09-18).

1. DESKTOP BY DEFAULT: an unbound chat's create requests landed in the hidden internal
   workspace (`~/.vool_runtime/workspace`) across the whole beta -- finalbot, pocketbot,
   stashbot. The packaged app's unbound default workspace is now the real `~/Desktop`;
   explicit overrides, bound projects and dev runs keep their existing roots.
2. SPEC OVER TEMPLATE: every scaffold request that stated its own commands
   ("/start -> `Ready`", "/save <word>") was answered with the generic template bot
   implementing none of them. A stated command spec now routes to the model-build lane,
   which generates from the request; the template keeps requests that name no commands.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch, tmp_path):
    monkeypatch.delenv("VOOL_PROJECT_ROOT", raising=False)
    monkeypatch.delenv("VOOL_WORKSPACE_ROOT", raising=False)
    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    yield


# ============================================================================================
# 1 -- Desktop by default for unbound chats in the packaged app
# ============================================================================================

def test_packaged_unbound_default_workspace_is_the_real_desktop(tmp_path, monkeypatch):
    from core.web.api.runtime import default_workspace_root

    monkeypatch.setenv("VOOL_PROJECT_ROOT", "/Users/owner/g2-native-build/app")
    assert default_workspace_root() == str(Path.home() / "Desktop")


def test_workspace_override_still_wins_for_isolated_rigs(tmp_path, monkeypatch):
    from core.web.api.runtime import default_workspace_root

    monkeypatch.setenv("VOOL_PROJECT_ROOT", "/Users/owner/g2-native-build/app")
    monkeypatch.setenv("VOOL_WORKSPACE_ROOT", str(tmp_path / "rig-ws"))
    assert default_workspace_root() == str(tmp_path / "rig-ws")


def test_dev_runs_keep_the_working_directory_workspace(tmp_path, monkeypatch):
    from core.web.api.runtime import default_workspace_root

    monkeypatch.chdir(tmp_path)
    assert default_workspace_root() == str(tmp_path.resolve())


def test_the_server_wires_the_desktop_default(tmp_path, monkeypatch):
    """The served app's provider chain resolves the Desktop default end to end."""
    from core.web.api.runtime import default_workspace_root as authority

    monkeypatch.setenv("VOOL_PROJECT_ROOT", "/Users/owner/g2-native-build/app")
    import apps.vool_api_server as server

    assert server._default_workspace_root() == str(Path.home() / "Desktop")
    assert server._default_workspace_root() == authority()


# ============================================================================================
# 2 -- a stated command spec outranks the platform template
# ============================================================================================

def _profile(prompt: str) -> dict:
    from core.agent_runtime.builder.support import controller_profile

    gap = {"support_level": "unsupported", "reason": "gap"}
    agent = SimpleNamespace(
        _should_run_builder_controller=lambda **_kw: True,
        _workspace_build_target=lambda **_kw: {
            "platform": "telegram",
            "language": "python",
            "root_dir": "generated/workspace-starter",
        },
        _supports_bounded_builder_workflow_request=lambda **_kw: False,
        _looks_like_explicit_workspace_file_request=lambda _t: False,
        _looks_like_generic_workspace_bootstrap_request=lambda _t: False,
        _builder_support_gap_report=lambda **_kw: gap,
    )
    return controller_profile(
        agent,
        effective_input=prompt,
        classification={"task_class": "unknown"},
        interpretation=SimpleNamespace(topic_hints=[]),
        source_context={"workspace": "/tmp/ws", "workspace_root": "/tmp/ws"},
        plan_tool_workflow_fn=lambda **_kw: SimpleNamespace(handled=False, next_payload=None),
        looks_like_workspace_bootstrap_request_fn=lambda _t: False,
    )


def test_a_stated_command_spec_routes_to_model_build_not_the_template():
    """The measured beta defect: this exact request shape shipped the generic template."""
    profile = _profile(
        "Create a folder named `stashbot`. Inside it create a minimal Python Telegram bot "
        "with: - `/start` -> `Stash ready` - `/save <word>` - `/show` Store one saved word "
        "per Telegram user in memory. Add tests for the save/show logic."
    )
    assert profile["should_handle"] is True
    assert profile["mode"] == "model_build", profile


def test_an_arrow_variant_spec_also_routes_to_model_build():
    profile = _profile(
        "make a telegram bot with /start - > Online and /add <positive integer> and /balance"
    )
    assert profile["mode"] == "model_build", profile


def test_a_plain_platform_request_keeps_the_template_lane():
    """No stated commands: the template scaffold keeps the request exactly as before."""
    profile = _profile("create a minimal telegram bot scaffold for reminders")
    assert profile["mode"] == "scaffold", profile


def test_the_command_spec_detector_shapes():
    from core.agent_runtime.build_request_intent import request_specifies_command_behavior

    assert request_specifies_command_behavior("/start -> `Ready`") is True
    assert request_specifies_command_behavior("/save <word>") is True
    assert request_specifies_command_behavior("/add <positive integer>") is True
    assert request_specifies_command_behavior("a telegram bot for reminders") is False
    assert request_specifies_command_behavior("") is False
