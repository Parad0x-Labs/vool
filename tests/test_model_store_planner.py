from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.model_store_planner import DEFAULT_OPENCLAW_MEMORY_MODEL, build_model_store_drive_plan

# The drive planner is Windows-only logic (drive letters, per-volume Ollama stores, setx).
# These tests mock platform.system()=="Windows", but pathlib cannot emulate Windows drive
# parsing or "\\" separators on a POSIX runner: PosixPath("D:\\").drive is "" and
# PosixPath("D:\\") / "Ollama" yields forward slashes. So the assertions are only meaningful
# on a real Windows host (dev machines + Windows CI), which is exactly where the planner runs.
pytestmark = pytest.mark.skipif(
    sys.platform != "win32",
    reason="Windows-only drive planning; pathlib cannot emulate Windows drive letters on POSIX",
)


def test_model_store_drive_plan_keeps_current_drive_when_it_still_has_enough_room(monkeypatch) -> None:
    # Regression coverage: a machine that already has models downloaded on D: must NOT get
    # "recommended" onto G: just because G: happens to have more free space right now. Doing
    # so (and this used to) would setx-persist OLLAMA_MODELS to a drive with no models on it,
    # silently orphaning the already-downloaded models and triggering a full re-download on
    # the next process that reads OLLAMA_MODELS - exactly the failure a live installer re-run
    # on this GTX 1080 dev machine hit before this test was added.
    gib = 1024**3
    free_by_drive = {
        "C:": 20.0,
        "D:": 120.0,
        "G:": 500.0,
    }

    def fake_disk_usage(path: Path) -> SimpleNamespace:
        drive = str(path.drive or path.anchor).upper()
        free_gb = free_by_drive[drive]
        return SimpleNamespace(total=int(1000 * gib), used=int((1000 - free_gb) * gib), free=int(free_gb * gib))

    monkeypatch.setattr("core.model_store_planner.platform.system", lambda: "Windows")
    monkeypatch.setattr(
        "core.model_store_planner.default_ollama_models_path",
        lambda env=None: Path("D:\\Ollama\\models"),
    )
    monkeypatch.setattr(
        "core.model_store_planner._mounted_windows_drive_roots",
        lambda: (Path("C:\\"), Path("D:\\"), Path("G:\\")),
    )
    monkeypatch.setattr("core.model_store_planner.shutil.disk_usage", fake_disk_usage)
    # Simulates a real, already-populated store (not just an unpopulated default path).
    monkeypatch.setattr("core.model_store_planner._path_exists", lambda path: True)

    plan = build_model_store_drive_plan(
        required_models=("qwen3:8b", "deepseek-r1:8b"),
        support_models=(DEFAULT_OPENCLAW_MEMORY_MODEL,),
    )

    assert plan["current_model_store_path"] == "D:\\Ollama\\models"
    assert plan["recommended_model_store_path"] == "D:\\Ollama\\models"
    assert plan["recommended_drive"]["drive"] == "D:"
    assert plan["current_drive"]["drive"] == "D:"
    assert plan["status"] == "current_best"
    assert [drive["drive"] for drive in plan["drives"]] == ["D:", "G:", "C:"]
    assert plan["drives"][-1]["status"] == "not_enough_space"


def test_model_store_drive_plan_recommends_biggest_sufficient_drive_when_current_is_full(monkeypatch) -> None:
    # The "pick the biggest sufficient drive" behavior is still correct and needed - just only
    # when the CURRENT store genuinely lacks room, not as an unconditional default.
    gib = 1024**3
    free_by_drive = {
        "C:": 20.0,
        "D:": 2.0,
        "G:": 500.0,
    }

    def fake_disk_usage(path: Path) -> SimpleNamespace:
        drive = str(path.drive or path.anchor).upper()
        free_gb = free_by_drive[drive]
        return SimpleNamespace(total=int(1000 * gib), used=int((1000 - free_gb) * gib), free=int(free_gb * gib))

    monkeypatch.setattr("core.model_store_planner.platform.system", lambda: "Windows")
    monkeypatch.setattr(
        "core.model_store_planner.default_ollama_models_path",
        lambda env=None: Path("D:\\Ollama\\models"),
    )
    monkeypatch.setattr(
        "core.model_store_planner._mounted_windows_drive_roots",
        lambda: (Path("C:\\"), Path("D:\\"), Path("G:\\")),
    )
    monkeypatch.setattr("core.model_store_planner.shutil.disk_usage", fake_disk_usage)
    monkeypatch.setattr("core.model_store_planner._path_exists", lambda path: True)

    plan = build_model_store_drive_plan(
        required_models=("qwen3:8b", "deepseek-r1:8b"),
        support_models=(DEFAULT_OPENCLAW_MEMORY_MODEL,),
    )

    assert plan["current_model_store_path"] == "D:\\Ollama\\models"
    assert plan["recommended_model_store_path"] == "G:\\Ollama\\models"
    assert plan["recommended_drive"]["drive"] == "G:"
    assert plan["recommended_drive"]["free_gb"] == 500.0
    assert plan["current_drive"]["drive"] == "D:"
    assert plan["current_drive"]["status"] == "not_enough_space"
    assert plan["status"] == "move_recommended"
    assert plan["set_env_command"] == 'setx OLLAMA_MODELS "G:\\Ollama\\models"'


def test_model_store_drive_plan_avoids_system_drive_even_with_more_free_space(monkeypatch) -> None:
    gib = 1024**3
    free_by_drive = {
        "C:": 900.0,
        "D:": 200.0,
    }

    def fake_disk_usage(path: Path) -> SimpleNamespace:
        drive = str(path.drive or path.anchor).upper()
        free_gb = free_by_drive[drive]
        return SimpleNamespace(total=int(1000 * gib), used=int((1000 - free_gb) * gib), free=int(free_gb * gib))

    monkeypatch.setattr("core.model_store_planner.platform.system", lambda: "Windows")
    monkeypatch.setenv("SystemDrive", "C:")
    monkeypatch.setattr(
        "core.model_store_planner.default_ollama_models_path",
        lambda env=None: Path("C:\\Ollama\\models"),
    )
    monkeypatch.setattr(
        "core.model_store_planner._mounted_windows_drive_roots",
        lambda: (Path("C:\\"), Path("D:\\")),
    )
    monkeypatch.setattr("core.model_store_planner.shutil.disk_usage", fake_disk_usage)
    # Fresh install: nothing has been downloaded to the default path yet, so the
    # current-store tiebreaker must not fire and "avoid the OS drive" still wins.
    monkeypatch.setattr("core.model_store_planner._path_exists", lambda path: False)

    plan = build_model_store_drive_plan(
        required_models=("qwen3:8b", "deepseek-r1:8b"),
        support_models=(DEFAULT_OPENCLAW_MEMORY_MODEL,),
    )

    # C: has far more free space, but D: is recommended because installs should avoid
    # the OS drive whenever a non-OS drive has enough room.
    assert plan["recommended_drive"]["drive"] == "D:"
    assert plan["drives"][0]["drive"] == "D:"
    assert plan["drives"][0]["is_system_drive"] is False
    assert plan["drives"][1]["drive"] == "C:"
    assert plan["drives"][1]["is_system_drive"] is True


def test_model_store_drive_plan_falls_back_to_system_drive_when_only_option(monkeypatch) -> None:
    gib = 1024**3

    def fake_disk_usage(path: Path) -> SimpleNamespace:
        free_gb = 50.0
        return SimpleNamespace(total=int(1000 * gib), used=int((1000 - free_gb) * gib), free=int(free_gb * gib))

    monkeypatch.setattr("core.model_store_planner.platform.system", lambda: "Windows")
    monkeypatch.setenv("SystemDrive", "C:")
    monkeypatch.setattr(
        "core.model_store_planner.default_ollama_models_path",
        lambda env=None: Path("C:\\Ollama\\models"),
    )
    monkeypatch.setattr(
        "core.model_store_planner._mounted_windows_drive_roots",
        lambda: (Path("C:\\"),),
    )
    monkeypatch.setattr("core.model_store_planner.shutil.disk_usage", fake_disk_usage)
    monkeypatch.setattr("core.model_store_planner._path_exists", lambda path: False)

    plan = build_model_store_drive_plan(
        required_models=("qwen3:8b",),
        support_models=(DEFAULT_OPENCLAW_MEMORY_MODEL,),
    )

    assert plan["recommended_drive"]["drive"] == "C:"
