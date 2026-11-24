"""A package script that asks for Python runs on VOOL's own interpreter, not on what its shebang finds.

On a Mac, `#!/usr/bin/env python3` finds /usr/bin/python3, the developer-tools stub. Inside the
sandbox it cannot write its cache or read the developer folder and exits 71 before the script runs
(Mac gate on e36daa6a9), and on a Mac without the developer tools it only offers to install them.
"""
from __future__ import annotations

import sys

import pytest

from core import plugin_executor


def _script(tmp_path, first_line: str, name: str = "server"):
    path = tmp_path / name
    path.write_text(first_line + "\nprint('hi')\n", encoding="utf-8")
    path.chmod(0o755)
    return path


@pytest.mark.parametrize("line", [
    "#!/usr/bin/env python3", "#!/usr/bin/python3", "#!/usr/bin/env python", "#!/usr/local/bin/python3.12",
    "#!/usr/bin/env -S python3 -u", "#! /usr/bin/env python3",
])
def test_a_python_shebang_gets_vools_interpreter(tmp_path, line):
    script = _script(tmp_path, line)
    assert plugin_executor._with_own_python([str(script), "--flag"]) == [sys.executable, str(script), "--flag"]


@pytest.mark.parametrize("line", ["#!/bin/sh", "#!/usr/bin/env node", "#!/usr/bin/env pythonista", "no shebang"])
def test_other_programs_are_left_alone(tmp_path, line):
    script = _script(tmp_path, line)
    assert plugin_executor._with_own_python([str(script)]) == [str(script)]


def test_an_interpreter_or_a_missing_program_is_left_alone(tmp_path):
    assert plugin_executor._with_own_python([sys.executable, "x.py"]) == [sys.executable, "x.py"]
    assert plugin_executor._with_own_python([str(tmp_path / "missing")]) == [str(tmp_path / "missing")]
    assert plugin_executor._with_own_python([]) == []


def test_the_confined_launch_runs_the_script_with_vools_python(tmp_path):
    script = _script(tmp_path, "#!/usr/bin/env python3")
    try:
        argv, label = plugin_executor.confined_argv(
            [str(script)], writable_roots=(), working_directory=tmp_path, argument_reads=False,
        )
    except plugin_executor.ConfinementUnavailableError:
        pytest.skip("no kernel confinement on this host")
    assert label.startswith("kernel:")
    assert argv[-2:] == [sys.executable, str(script)]
    # The script itself is still readable inside the sandbox (named among the read roots).
    assert any(str(tmp_path) in str(token) for token in argv[:-2])
