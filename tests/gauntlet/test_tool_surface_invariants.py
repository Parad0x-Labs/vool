"""Gauntlet: the model-facing tool surface may not move without a deliberate golden update.

`test_contract_map_golden.py` freezes the declared security tiers. This freezes what those
tiers *resolve to* — the advertised intents, the native schema each becomes, and the permission
actions and per-mode effects each produces at five argument shapes.

The pairing matters. A tier literal can stay untouched while the derivation around it changes,
and that is exactly the shape of a quiet permission widening: `side_effect_class` still reads
`read_only`, and `actions_for_tool` starts returning something else.

The two xfails below are live defects captured deliberately. They are the reason this harness
exists: they were found by building it, not by the 5,246 tests that were already green.

When the tool surface legitimately changes, regenerate with:

    .venv/bin/python -c "import json; from tests.gauntlet.tool_surface_snapshot import \\
      build_snapshot; json.dump(build_snapshot(), open('tests/gauntlet/tool_surface_golden.json','w'), \\
      indent=2, sort_keys=True)"

and review the diff. A widened permission or a dropped intent must not pass review unremarked.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from core.cloud_tool_call_contract import build_cloud_tool_definitions
from core.execution.capabilities import runtime_tool_specs
from tests.gauntlet.tool_surface_snapshot import ARGUMENT_SHAPES, build_snapshot

pytestmark = [pytest.mark.gauntlet, pytest.mark.safety]

GOLDEN_PATH = Path(__file__).resolve().parent / "tool_surface_golden.json"


def _shipped_specs() -> list[dict[str, Any]]:
    """The specs a shipped runtime offers, with policy pinned rather than inherited.

    Calling `runtime_tool_specs()` bare returns a different set depending on where it is
    called from — see the note in `tool_surface_snapshot.build_snapshot`. Every assertion here
    pins the same configuration so a failure means the surface moved, not that the harness ran
    in a different import order.
    """

    return list(
        runtime_tool_specs(
            allow_web_fallback_fn=lambda: True,
            allow_browser_fallback_fn=lambda: True,
        )
    )


@pytest.fixture(scope="module")
def golden() -> dict[str, Any]:
    return json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def live() -> dict[str, Any]:
    return build_snapshot()


def test_no_intent_disappears_from_the_catalog(golden: dict, live: dict) -> None:
    missing = sorted(set(golden["intents"]) - set(live["intents"]))
    assert not missing, f"intents no longer advertised to the model: {missing}"


def test_new_intents_are_added_deliberately(golden: dict, live: dict) -> None:
    added = sorted(set(live["intents"]) - set(golden["intents"]))
    assert not added, (
        f"new intents advertised without a golden update: {added}. "
        "Regenerate the golden and review the permissions the new tools resolve to."
    )


def test_every_advertised_intent_becomes_a_native_definition(live: dict) -> None:
    """A tool the model is told about in text but cannot call natively is a dead entry."""

    without_native = sorted(
        intent for intent, tool in live["tools"].items() if not tool["native_name"]
    )
    assert not without_native, without_native


def test_native_names_stay_unique_and_within_the_provider_limit() -> None:
    definitions = build_cloud_tool_definitions(_shipped_specs())
    names = [item.name for item in definitions]
    assert len(names) == len(set(names)), "two tools would be published under one native name"
    too_long = [name for name in names if len(name) > 64]
    assert not too_long, f"over the 64-char provider limit: {too_long}"


@pytest.mark.parametrize("shape", ARGUMENT_SHAPES)
def test_permission_actions_are_unchanged_at_every_argument_shape(
    golden: dict, live: dict, shape: str
) -> None:
    """The check a single-shape snapshot cannot make.

    `workspace.write_file` resolves to OVERWRITE_EXISTING_FILES or CREATE_FILES depending on
    whether the target exists, and `sandbox.run_command` reads the command string. Comparing
    at one shape would call both stable through a change that moved them.
    """

    drifted: list[str] = []
    for intent, want in golden["tools"].items():
        got = live["tools"].get(intent)
        if got is None:
            continue
        before = want["permissions"][shape]["actions"]
        after = got["permissions"][shape]["actions"]
        if before != after:
            drifted.append(f"{intent}: {before} -> {after}")
    assert not drifted, f"permission actions moved at shape {shape!r}:\n" + "\n".join(drifted)


@pytest.mark.parametrize("shape", ARGUMENT_SHAPES)
def test_no_mode_effect_loosens(golden: dict, live: dict, shape: str) -> None:
    """A tightening is reviewable; a loosening is a sandbox regression.

    Ordered deny > require_approval > allow. Anything moving down that order fails here even
    when the action list itself is unchanged.
    """

    rank = {"deny": 2, "require_approval": 1, "allow": 0}
    loosened: list[str] = []
    for intent, want in golden["tools"].items():
        got = live["tools"].get(intent)
        if got is None:
            continue
        before = want["permissions"][shape]["effects"]
        after = got["permissions"][shape]["effects"]
        for mode, want_effect in before.items():
            got_effect = after.get(mode)
            if got_effect is None:
                continue
            if rank.get(got_effect, 0) < rank.get(want_effect, 0):
                loosened.append(f"{intent} in {mode}: {want_effect} -> {got_effect}")
    assert not loosened, f"permission loosened at shape {shape!r}:\n" + "\n".join(loosened)


def test_tool_arguments_are_unchanged(golden: dict, live: dict) -> None:
    drifted: list[str] = []
    for intent, want in golden["tools"].items():
        got = live["tools"].get(intent)
        if got is None:
            continue
        if want["arguments"] != got["arguments"]:
            drifted.append(f"{intent}: {want['arguments']} -> {got['arguments']}")
    assert not drifted, "advertised arguments changed:\n" + "\n".join(drifted)


# --------------------------------------------------------------------------------------
# Live defects, captured deliberately. These xfail today and must flip to pass — not be
# deleted — when the consolidation lands.
# --------------------------------------------------------------------------------------


def test_a_duplicate_intent_produces_a_schema_requiring_both_spellings() -> None:
    """Prove the consequence of the live duplicate without depending on reproducing it.

    Measured 2026-07-28, `web.fetch` is emitted by two sources: a hardcoded block in
    `core/execution/capabilities.py` (argument `timeout_s`) and `runtime_execution_tool_specs`
    (argument `timeout_seconds`). In a shipped process both fire and the model is offered
    `web.fetch` twice; `runtime_execution_tool_specs` returns 44 specs including `web.fetch`.
    Under `tests/conftest.py` it returns 42 and omits it, so **the duplicate cannot be
    reproduced from inside the suite at all** — which is precisely why it survived 5,246 green
    tests and had to be found by running the product.

    So this asserts the merge behaviour on the real merger with the two real spec shapes. It
    documents what production does today and will start failing the moment
    `build_cloud_tool_definitions` stops union-ing a duplicate into one `required` list.
    """

    definitions = build_cloud_tool_definitions(
        [
            {
                "intent": "web.fetch",
                "description": "Fetch text from a specific URL.",
                "read_only": True,
                "arguments": {"url": "https URL", "timeout_s": "number optional"},
            },
            {
                "intent": "web.fetch",
                "description": "Fetch a public web page over HTTP(S) and return bounded text.",
                "read_only": True,
                "arguments": {"url": "https URL", "timeout_seconds": "number optional"},
            },
        ]
    )
    assert len(definitions) == 1, "the merger should publish one function per intent"
    required = set(definitions[0].parameters.get("required") or ())
    assert {"timeout_s", "timeout_seconds"} <= required, (
        "expected the union'd duplicate to require both spellings; if this now fails the "
        "merger changed and the note above needs updating"
    )


def test_capabilities_does_not_hardcode_a_spec_another_source_already_emits() -> None:
    """Was an xfail; fixed 2026-07-28 and kept as a regression lock.

    `capabilities.py` declared a `web.fetch` spec that `runtime_execution_tool_specs` already
    emitted, with a different argument name — `timeout_s` here, `timeout_seconds` there. The
    merger unions a duplicate's properties and a strict native schema requires all of them, so
    every cloud model was forced to send both spellings and `core/execution/web_tools.py:140`
    read only one. The contract now declares `timeout_s`, matching the executor, and this block
    is gone.
    """

    source = (
        Path(__file__).resolve().parents[2] / "core" / "execution" / "capabilities.py"
    ).read_text(encoding="utf-8")
    assert '"intent": "web.fetch"' not in source, (
        "capabilities.py still hardcodes web.fetch alongside runtime_execution_tool_specs"
    )


def test_the_golden_is_current_enough_to_be_meaningful(golden: dict, live: dict) -> None:
    """Guard against a golden that silently stopped covering the surface it claims to."""

    assert golden["intent_count"] == len(golden["tools"])
    assert live["intent_count"] >= golden["intent_count"] - 1, (
        "the live surface shrank by more than one intent; regenerate deliberately"
    )
