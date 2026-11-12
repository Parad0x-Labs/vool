"""The census CLI gives the same verdict as `vool commands --check`.

TDL-L33 (2026-11-12): `python -m core.command_registry.census` printed "census FAILED" with one
"adapter binding broken ... (no such registry command)" line for every generated adapter (149 on
the v0.8 candidate), while `vool commands --check`, which hands the census the registry's command
ids in-process, said "census: ok". The CLI passed an empty set of registry ids, so no binding could
ever resolve. It now reads the same registry the check does, and a binding that is really broken
still turns it red.
"""

from __future__ import annotations

import subprocess
import sys

import core.command_registry.census as census_mod
from core.command_registry.execute import registry_default
from core.command_registry.projections import check_output


def test_the_cli_says_ok_when_the_commands_check_says_ok(capsys) -> None:
    _report, lines = check_output(registry_default())
    census_lines = [line for line in lines if line.startswith("census")]
    assert any(line.startswith("census: ok") for line in census_lines), census_lines

    code = census_mod.main([])
    out = capsys.readouterr().out
    assert "adapter binding broken" not in out, out[:2000]
    assert out.strip().splitlines()[-1] == "census ok", out[-2000:]
    assert code == 0


def test_a_binding_that_is_really_broken_still_fails_the_cli(monkeypatch, capsys) -> None:
    real = census_mod._registry_command_ids()
    assert "council.stop" in real
    monkeypatch.setattr(census_mod, "_registry_command_ids", lambda: real - {"council.stop"})

    code = census_mod.main([])
    out = capsys.readouterr().out
    assert code == 1
    assert out.strip().splitlines()[-1] == "census FAILED", out[-2000:]
    broken = [line for line in out.splitlines() if line.startswith("adapter binding broken")]
    assert broken and all("council.stop" in line for line in broken), broken


def test_the_module_entry_point_prints_census_ok() -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "core.command_registry.census"],
        capture_output=True, text=True, timeout=300, cwd=str(census_mod.PROJECT_ROOT),
    )
    assert proc.returncode == 0, (proc.stdout[-2000:], proc.stderr[-2000:])
    assert proc.stdout.strip().splitlines()[-1] == "census ok", proc.stdout[-2000:]
