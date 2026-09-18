"""machine.display_inspect (Checkpoint 3): real display resolution, never CPU specs.

Regression for "what is my screen resolution?" answering with Intel CPU/OS specs.
"""

from __future__ import annotations

from types import SimpleNamespace

import core.machine_diagnostics as md
from core.execution.constants import machine_display_intent
from core.runtime_execution_tools import execute_runtime_tool


def test_display_intent_routes_only_this_host_questions() -> None:
    assert machine_display_intent("what is my screen resolution?") == "machine.display_inspect"
    assert machine_display_intent("what's my display resolution") == "machine.display_inspect"
    assert machine_display_intent("what monitor am i using?") == "machine.display_inspect"
    # General knowledge / not this host -> not routed to the inspection tool.
    assert machine_display_intent("what is the resolution of a 4k monitor?") is None
    assert machine_display_intent("what's a good monitor to buy?") is None
    # Mutation / non-question.
    assert machine_display_intent("change my screen resolution") is None
    assert machine_display_intent("my screen is nice") is None


def _fake_runner(resolution_lines: str, edid_lines: str = ""):
    calls = {"n": 0}

    def runner(cmd, **kwargs):
        calls["n"] += 1
        joined = " ".join(cmd)
        if "WmiMonitorBasicDisplayParams" in joined:
            return SimpleNamespace(stdout=edid_lines, stderr="", returncode=0)
        return SimpleNamespace(stdout=resolution_lines, stderr="", returncode=0)

    return runner


def test_display_info_parses_resolution_and_refresh(monkeypatch) -> None:
    monkeypatch.setattr(md.sys, "platform", "win32")
    runner = _fake_runner("Intel UHD Graphics|1920|1080|60\n")
    info = md.display_info(runner=runner)
    assert info["supported"] is True
    assert info["displays"] == [{"name": "Intel UHD Graphics", "width": 1920, "height": 1080, "refresh_hz": 60}]
    assert info["physical"]["verified"] is False


def test_display_info_reports_verified_physical_size_from_edid(monkeypatch) -> None:
    monkeypatch.setattr(md.sys, "platform", "win32")
    runner = _fake_runner("Display|2560|1440|144\n", edid_lines="60|34\n")
    info = md.display_info(runner=runner)
    assert info["displays"][0]["width"] == 2560
    assert info["physical"]["verified"] is True
    assert info["physical"]["diagonal_in"] > 0


def test_display_tool_renders_resolution_not_specs(monkeypatch) -> None:
    monkeypatch.setattr(md, "display_info", lambda: {
        "supported": True, "platform": "win32",
        "displays": [{"name": "Intel UHD Graphics", "width": 1920, "height": 1080, "refresh_hz": 60}],
        "physical": {"verified": False, "diagonal_in": None},
    })
    result = execute_runtime_tool("machine.display_inspect", {})
    assert result is not None and result.ok and result.status == "executed"
    assert "1920 x 1080" in result.response_text
    assert "60 Hz" in result.response_text
    assert "could not be verified" in result.response_text
    # Must not be a CPU/OS spec dump.
    assert "Chip:" not in result.response_text


def test_display_tool_refuses_when_nothing_can_read_the_display(monkeypatch) -> None:
    """No display data anywhere means a refusal -- never an invented resolution.

    This test previously asserted the refusal from `display_info` reporting unsupported ALONE, on
    the assumption that "not Windows" means "unreadable". That inference was wrong and was costing
    real answers: measured live 2026-07-31 on macOS, all eight display phrasings were told "Display
    inspection is only available on Windows in this build" while `machine.inspect_specs`, in the
    same runtime, reported the resolution `system_profiler` confirms (4480 x 2520, 2240 x 1260 @
    60.00Hz). `machine.display_inspect` now consults the platform reader before refusing.

    The guard this test exists for is unchanged and still pinned: when NO reader has anything, the
    tool refuses rather than guessing. So both readers are emptied here, which is the condition the
    refusal is actually about.
    """
    import core.runtime_execution_tools as ret

    monkeypatch.setattr(md, "display_info", lambda: {"supported": False, "platform": "linux", "displays": [], "physical": {"verified": False}})
    monkeypatch.setattr(ret, "_machine_display_details", dict)
    result = execute_runtime_tool("machine.display_inspect", {})
    assert result is not None and result.ok is False and result.status == "unsupported_platform"
    assert "won't guess" in result.response_text


def test_a_platform_reader_with_data_is_used_instead_of_refusing(monkeypatch) -> None:
    """The other half of the same contract: data that exists must be reported, not refused."""
    import core.runtime_execution_tools as ret

    monkeypatch.setattr(md, "display_info", lambda: {"supported": False, "platform": "darwin", "displays": [], "physical": {"verified": False}})
    monkeypatch.setattr(
        ret,
        "_machine_display_details",
        lambda: {"name": "iMac", "native_resolution": "4480 x 2520", "current_resolution": "2240 x 1260 @ 60.00Hz", "screen_size": ""},
    )
    result = execute_runtime_tool("machine.display_inspect", {})
    assert result is not None and result.ok is True and result.status == "executed"
    assert "4480 x 2520" in result.response_text
    assert "only available on Windows" not in result.response_text
