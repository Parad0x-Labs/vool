from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ExecutionResult:
    returncode: int
    stdout: str
    stderr: str


class ExecutionBackend(Protocol):
    def run(self, argv: list[str], cwd: str, timeout_seconds: int) -> ExecutionResult:
        ...
