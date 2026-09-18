"""The only thing the parent is allowed to believe about a child run.

The discriminator is the whole point. A gauntlet that cannot tell "the product did the wrong thing"
apart from "the harness fell over" will eventually report the second as the first -- which is
exactly what happened when a missing SQLite table became three "release defects". So the child
emits one tagged document, and `kind` is machine-readable and closed:

    result      -- the scenario ran; its evidence may be asserted on
    infra_error -- anything else at all; the parent MUST fail as an infrastructure fault

`infra_error` is never an expected-red and never a mutation kill. The parent has no code path that
turns one into a product verdict.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

# A line prefix rather than "the last line of stdout": the runtime logs JSON to stdout, so the
# document has to be findable among lines that are themselves valid JSON.
SENTINEL = "@@GAUNTLET-RESULT@@ "

KIND_RESULT = "result"
KIND_INFRA = "infra_error"


def emit(kind: str, group: str, payload: dict[str, Any]) -> str:
    return SENTINEL + json.dumps({"kind": kind, "group": group, "payload": payload}, default=str)


def parse(stdout: str) -> dict[str, Any] | None:
    """The last tagged document in the stream, or None when the child never emitted one."""
    found = None
    for line in stdout.splitlines():
        if line.startswith(SENTINEL):
            try:
                found = json.loads(line[len(SENTINEL):])
            except Exception:
                continue
    return found


@dataclass
class ChildOutcome:
    kind: str
    group: str
    payload: dict[str, Any] = field(default_factory=dict)
    returncode: int = 0
    stdout: str = ""
    stderr: str = ""
    duration_s: float = 0.0
    infra_reason: str = ""

    @property
    def is_result(self) -> bool:
        return self.kind == KIND_RESULT

    def require_result(self) -> dict[str, Any]:
        """The only door to product assertions. Raises a loud infrastructure failure otherwise."""
        if self.is_result:
            return self.payload
        raise AssertionError(
            "GAUNTLET INFRASTRUCTURE FAULT -- not a product defect, not an expected-red, "
            f"not a mutation kill.\n"
            f"  group        : {self.group}\n"
            f"  reason       : {self.infra_reason or self.payload.get('reason') or '(none given)'}\n"
            f"  child rc     : {self.returncode}\n"
            f"  duration     : {self.duration_s:.1f}s\n"
            f"  child stderr :\n{(self.stderr or '(empty)')[-4000:]}\n"
            f"  child stdout tail:\n{(self.stdout or '(empty)')[-1500:]}"
        )


@dataclass
class EvidenceTurn:
    """A turn reconstructed in the PARENT from a child's evidence document.

    Exposes the same surface `harness.Turn` did, so the migrated assertions are byte-identical to
    the ones they replace. That is the point: porting the arrange step is a mechanical change, and
    rewriting the assertions at the same time would make it impossible to tell a migration bug from
    a behaviour change.
    """

    data: dict[str, Any]

    @property
    def said(self) -> str:
        return str(self.data.get("said", ""))

    @property
    def status(self) -> int:
        return int(self.data.get("status", 0))

    @property
    def reply(self) -> str:
        return str(self.data.get("reply", ""))

    @property
    def low(self) -> str:
        return str(self.data.get("low", ""))

    @property
    def session_id(self) -> str:
        return str(self.data.get("session_id", ""))

    @property
    def weather_requests(self) -> list[str]:
        return list(self.data.get("weather_requests") or [])

    @property
    def market_requests(self) -> list[str]:
        return list(self.data.get("market_requests") or [])

    @property
    def model_calls(self) -> int:
        return int(self.data.get("model_calls", 0))

    @property
    def prompt_seen_by_model(self) -> str:
        return str(self.data.get("prompt_seen_by_model", ""))

    def planned_entities(self, operation: str = "") -> list[str]:
        return [
            str(entity)
            for op, entity in (self.data.get("planned") or [])
            if not operation or str(op) == operation
        ]

    def tools_run(self) -> list[str]:
        return list(self.data.get("tools") or [])

    def numbers(self) -> list[str]:
        return list(self.data.get("numbers") or [])

    def has_number(self, value: float, tol: float = 0.51) -> bool:
        for raw in self.numbers():
            try:
                if abs(float(str(raw).replace(",", "")) - value) <= tol:
                    return True
            except ValueError:
                continue
        return False

    def mentions(self, *needles: str) -> bool:
        return all(n.lower() in self.low for n in needles)

    def missing(self, *needles: str) -> list[str]:
        return [n for n in needles if n.lower() not in self.low]

    def describe(self) -> str:
        return "\n" + str(self.data.get("describe", ""))


__all__ = [
    "KIND_INFRA",
    "KIND_RESULT",
    "SENTINEL",
    "ChildOutcome",
    "EvidenceTurn",
    "emit",
    "parse",
]
