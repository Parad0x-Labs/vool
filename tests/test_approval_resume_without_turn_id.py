"""An approved resume that sends no turn id spends the approval of the turn that asked.

CI #681: with the request id standing in for a missing turn id, the resume (a new request) was a
different task to the permission policy, so the allowed approval was never spent and the same
write was asked again. The chat door now names the asking turn from the approval record itself.
"""
from __future__ import annotations

from core import request_trust
from core.mode_permission_policy import _logical_turn_id


def test_the_approval_turn_wins_over_the_request_turn() -> None:
    context = {"approval_turn_id": "turn-asked", "cancel_turn_id": "rq-new-request"}
    assert _logical_turn_id(context, {"client_turn_id": "client"}, "task") == "turn-asked"


def test_without_an_approval_turn_the_order_is_unchanged() -> None:
    assert _logical_turn_id({"cancel_turn_id": "rq-1"}, {"client_turn_id": "c"}, "t") == "rq-1"
    assert _logical_turn_id({}, {"client_turn_id": "c"}, "t") == "c"
    assert _logical_turn_id({}, {}, "t") == "t"
    assert _logical_turn_id({}, {}, None) == ""


def test_a_caller_cannot_send_its_own_approval_turn() -> None:
    assert "approval_turn_id" in request_trust.RESERVED_TRUST_KEYS


def test_the_door_finds_no_turn_for_an_unknown_or_oversized_token() -> None:
    from core.web.api.service import _turn_named_by_approval

    assert _turn_named_by_approval("s1", "") == ""
    assert _turn_named_by_approval("", "tok") == ""
    assert _turn_named_by_approval("s1", "x" * 200) == ""
