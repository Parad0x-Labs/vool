from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

import pytest

WINDOWS_ABSOLUTE_PATH = re.compile(r"^[A-Za-z]:[\\/]")


def bash_executable() -> str:
    candidates: list[str] = []
    if os.name == "nt":
        candidates.extend(
            [
                r"C:\Program Files\Git\bin\bash.exe",
                r"C:\Program Files\Git\usr\bin\bash.exe",
            ]
        )
    found = shutil.which("bash")
    if found:
        candidates.append(found)
    candidates.append("/bin/bash")
    for candidate in candidates:
        if Path(candidate).exists():
            return candidate
    pytest.skip("bash is not available")


def bash_path(path: str | Path) -> str:
    value = str(path)
    if os.name != "nt" or not WINDOWS_ABSOLUTE_PATH.match(value):
        return value
    normalized = value.replace("\\", "/")
    return f"/{normalized[0].lower()}{normalized[2:]}"


def bash_script_args(script: str | Path, *args: str) -> list[str]:
    command = [bash_executable()]
    if os.name == "nt":
        command.append("-l")
    command.append(bash_path(script))
    command.extend(bash_path(arg) for arg in args)
    return command
