"""Privacy compartments (recovered kernel gold) + the paid-capsule boundary.

Pins the recovered need-to-know law at its two live seams:

- ``core.kernel.compartments`` — the deterministic grammar, the minimized view,
  and the fail-closed leak scan (denied text, PARTIAL quantities, credentials
  even when granted);
- ``core.model_handoff_capsule.build_model_handoff_capsule`` — compartment
  bytes and broken compartment markup never cross to a paid/third-party lane.
"""

from __future__ import annotations

import pytest

from core.kernel.capabilities import ForkContext
from core.kernel.compartments import (
    CompartmentLeakError,
    MalformedCompartmentError,
    NeedToKnowLane,
    parse_compartments,
)
from core.model_handoff_capsule import (
    CapsuleItem,
    build_model_handoff_capsule,
)

# ---------------------------------------------------------------------------
# The grammar
# ---------------------------------------------------------------------------

def test_parse_splits_public_frame_and_compartments() -> None:
    frame, compartments = parse_compartments(
        "What is entropy? [private:salary] I earn 4200 EUR [/private] Thanks."
    )
    assert frame == "What is entropy?  Thanks."
    assert set(compartments) == {"salary"}
    assert compartments["salary"].text == "I earn 4200 EUR"


@pytest.mark.parametrize(
    "text",
    [
        "broken [private:salary] never closed",
        "orphan [/private] close",
        "dup [private:a] x [/private] [private:a] y [/private]",
        "nested [private:a] x [private:b] y [/private] [/private]",
        "empty [private:a]   [/private]",
    ],
)
def test_malformed_markup_refuses(text: str) -> None:
    with pytest.raises(MalformedCompartmentError):
        parse_compartments(text)


def test_unmarked_text_is_untouched() -> None:
    frame, compartments = parse_compartments("an ordinary turn with no markers")
    assert frame == "an ordinary turn with no markers"
    assert compartments == {}


# ---------------------------------------------------------------------------
# The minimized view + leak scan
# ---------------------------------------------------------------------------

TURN = "Compare offers. [private:salary] I earn 4,200 EUR a month [/private] for a mortgage."


def _lane(compartments: tuple[str, ...]) -> NeedToKnowLane:
    # Law 3: a child's grants are subset-enforced, so the parent must hold
    # everything the lane asks for or the grant silently does not apply.
    parent = ForkContext(
        fork_id="turn",
        caps=core_caps(tuple(f"compartment.{c}" for c in compartments)),
        parent_id=None,
    )
    return NeedToKnowLane.create(lane_id="cloud", parent=parent, compartments=compartments)


def core_caps(tokens: tuple[str, ...] = ("compartment.salary",)):
    from core.kernel.capabilities import CapabilitySet

    return CapabilitySet(tokens)


def test_denied_compartment_bytes_are_structurally_absent() -> None:
    frame, compartments = parse_compartments(TURN)
    lane = _lane(())  # the cloud lane holds no grant
    view, included = lane.minimize_for_view(frame, compartments) if hasattr(lane, "minimize_for_view") else (None, None)
    if view is None:
        from core.kernel.compartments import minimized_view

        view, included = minimized_view(lane, frame, compartments)
    assert included == ()
    assert "4,200" not in view
    assert "salary" not in view


def test_leak_scan_refuses_partial_quantity_of_a_denied_compartment() -> None:
    frame, compartments = parse_compartments(TURN)
    # The smuggle: the frame publicly repeats only the NUMBER of the private field.
    smuggled = f"Compare offers. The ratio for 4,200 EUR monthly is what I need. {frame}"
    lane = _lane(())
    with pytest.raises(CompartmentLeakError):
        lane.__class__  # noqa: B018 - keep the lane import honest
        from core.kernel.compartments import leak_scan

        leak_scan(smuggled, compartments, included=())


def test_leak_scan_refuses_credentials_even_when_granted() -> None:
    text = "Help me budget. [private:keys] api_key = sk-abc123def456ghi789jkl [/private]"
    frame, compartments = parse_compartments(text)
    lane = _lane(("keys",))
    from core.kernel.compartments import minimized_view

    with pytest.raises(CompartmentLeakError):
        minimized_view(lane, frame, compartments)


def test_granted_compartment_travels_and_is_reported() -> None:
    frame, compartments = parse_compartments(TURN)
    lane = _lane(("salary",))
    from core.kernel.compartments import minimized_view

    view, included = minimized_view(lane, frame, compartments)
    assert included == ("salary",)
    assert "4,200" in view


# ---------------------------------------------------------------------------
# The paid-capsule boundary (the live runtime caller)
# ---------------------------------------------------------------------------

def _capsule(user_goal: str, items: tuple[CapsuleItem, ...] = ()):
    return build_model_handoff_capsule(
        task_id="t-1",
        turn_id="turn-1",
        subtask_id="sub-1",
        user_goal=user_goal,
        blocked_subtask="none",
        rules=(),
        plan_state=(),
        expected_output_schema={},
        forbidden_operations=(),
        verification_criteria=(),
        items=items,
        approved_workspace_roots=("/tmp",),
    )


def test_unmarked_goal_builds_unchanged() -> None:
    capsule = _capsule("summarize the plan")
    assert capsule.user_goal == "summarize the plan"


def test_compartment_marker_in_goal_refuses_at_the_paid_boundary() -> None:
    with pytest.raises(PermissionError, match="compartment marker in capsule user_goal"):
        _capsule("Budget for [private:salary] 4200 EUR [/private] monthly")


def test_orphan_close_in_goal_refuses_too() -> None:
    with pytest.raises(PermissionError, match="compartment marker in capsule user_goal"):
        _capsule("Budget for monthly [/private] spending")


def test_compartment_marker_in_item_refuses_at_the_paid_boundary() -> None:
    item = CapsuleItem(
        item_id="i-1",
        kind="note",
        content="notes [private:salary] 4200 [/private]",
        provenance="test",
    )
    with pytest.raises(PermissionError, match="compartment marker in capsule item"):
        _capsule("summarize the plan", (item,))


def test_minimized_public_frame_builds_cleanly() -> None:
    # The caller-side path the refusal points to: parse, keep the public frame.
    frame, _ = parse_compartments("Budget for [private:salary] 4200 EUR [/private] monthly")
    capsule = _capsule(frame)
    assert "4200" not in capsule.user_goal
