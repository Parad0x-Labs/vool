"""The authoritative gate's tool pins must be READ from pyproject, not copied beside it.

MEASURED 2026-08-18. `ops/verify.py` carried `REQUIRED_TOOLS = {"pytest": "9.1.0", "ruff":
"0.15.16"}` while `pyproject.toml` declared the same two versions in
`[project.optional-dependencies] dev`. The pyproject comment beside them read, in full:

    # Same pin as the CI gate, so `pip install -e ".[dev]"` and the gate cannot disagree about
    # which rules exist. Bump both lines together.

"Bump both lines together" is a manual coordination that no automated dependency update can
perform. Dependabot PR #87 raised ruff to 0.16.3 in pyproject, could not know about the second
copy, and the gate refused its own repository:

    !! verification tool mismatch: ruff required 0.15.16, got 0.16.3

Every future bump of either tool would have failed the same way. A pin that must be edited in two
places to stay true is one edit away from being false -- and the gate is the component that must
not be wrong, since everything else is measured against it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from ops.verify import REQUIRED_TOOLS

PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"


@pytest.mark.parametrize("tool", ("pytest", "ruff"))
def test_the_gate_requires_exactly_what_pyproject_pins(tool: str) -> None:
    match = re.search(rf'"{tool}==([^"]+)"', PYPROJECT.read_text(encoding="utf-8"))

    assert match is not None, f"{tool} lost its exact pin in pyproject.toml"
    assert REQUIRED_TOOLS[tool] == match.group(1).strip()


def test_the_gate_does_not_carry_its_own_copy_of_a_version() -> None:
    """A literal version string back in verify.py is the defect returning."""
    body = (Path(__file__).resolve().parent.parent / "ops" / "verify.py").read_text(encoding="utf-8")
    code = "\n".join(
        line for line in body.splitlines()
        if not line.lstrip().startswith("#") and not line.lstrip().startswith("!!")
    )
    literals = re.findall(r'"(?:pytest|ruff)==?[0-9]+\.[0-9]+', code)

    assert not literals, f"a tool version is hardcoded in the gate again: {literals}"


def test_a_bump_in_pyproject_moves_the_gate(tmp_path, monkeypatch) -> None:
    """The property the fix exists for, exercised rather than asserted: change the declared pin and
    the gate's requirement follows it."""
    import ops.verify as verify

    original = PYPROJECT.read_text(encoding="utf-8")
    assert '"ruff==' in original
    bumped = original.replace('"ruff==0.15.16"', '"ruff==9.9.9"')
    assert bumped != original, "the fixture no longer matches the declared pin"

    PYPROJECT.write_text(bumped, encoding="utf-8")
    try:
        assert verify._required_tools()["ruff"] == "9.9.9"
    finally:
        PYPROJECT.write_text(original, encoding="utf-8")

    assert verify._required_tools()["ruff"] == "0.15.16"


def test_a_missing_pin_fails_loudly_rather_than_defaulting(monkeypatch) -> None:
    """If the declaration disappears, the gate must refuse rather than invent a version --
    a gate that guesses its own contract is worse than one that stops."""
    import ops.verify as verify

    original = PYPROJECT.read_text(encoding="utf-8")
    PYPROJECT.write_text(original.replace('"ruff==0.15.16"', '"ruff>=0.3"'), encoding="utf-8")
    try:
        with pytest.raises(SystemExit) as caught:
            verify._required_tools()
        assert "ruff" in str(caught.value)
    finally:
        PYPROJECT.write_text(original, encoding="utf-8")
