"""The inspection-claim gate checks the files the reply claims, not every file the user listed.

Measured on the live agent-team comparison (2026-10-07, counted runs t1/t2/t3 single): the user named three files,
the reply reported on the one it had read, and the published answer became "I did not actually open `pricing.py`,
so I can't report on it", because the gate required a read record for every file the USER named. A reply that names
its own files now claims exactly those; a reply that names none still claims the files the user named (the
2026-07-28 fabrication stays refused, pinned in test_inspection_claim_validator).
"""
from __future__ import annotations

import pytest

from core import execution_records
from core.agent_runtime.action_honesty_validator import enforce_final_action_honesty
from core.runtime_tool_contracts import ToolClaim

READ_CLAIM = ToolClaim(target_argument="path", resolved_target_key="path")
REQUEST = "Review the three Python files in this folder: pricing.py, stock.py and orders.py. Find the most important bug."


@pytest.fixture(autouse=True)
def _clean():
    execution_records.clear()
    yield
    execution_records.clear()


def _read(*paths: str) -> None:
    for path in paths:
        execution_records.record(session_id="s", intent="workspace.read_file", arguments={"path": path},
                                 observation={"ok": True, "path": f"/w/{path}"}, claim=READ_CLAIM)


def _run(response: str) -> dict:
    return enforce_final_action_honesty({"response": response, "confidence": 0.9}, user_input=REQUEST, session_id="s")


def test_a_reply_about_the_file_it_read_is_not_refused_for_files_it_never_mentions():
    _read("orders.py")
    result = _run("I reviewed orders.py: line 25 never releases stock when a line is partly cancelled.")
    assert result.get("route_reason") != "unsupported_inspection_claim", result
    assert "did not actually open" not in result["response"]


def test_a_reply_that_claims_a_file_it_never_read_is_still_refused():
    _read("orders.py")
    result = _run("I reviewed pricing.py: the coupon is applied twice on line 35.")
    assert result.get("route_reason") == "unsupported_inspection_claim", result
    assert "pricing.py" in result["response"]


def test_a_reply_that_names_no_file_still_answers_for_the_files_the_user_named():
    _read("README.md")
    result = _run("Yes, I reviewed all of them and found nothing serious.")
    assert result.get("route_reason") == "unsupported_inspection_claim", result
