"""In-memory execution records: the ground truth a binder checks an answer against.

Built from the measured failure of 2026-07-28. Folder `~/Desktop/vool-w5x1` held exactly
`iota.rb`, `tau.sql`, `upsilon.md`; the tool ran and returned all three; the answer said the
folder "contains a collection of files and folders related to VOOL and OpenClaw runtime
behavior". Nothing in the runtime could contradict that, because nothing kept what the tool
returned — receipts are written only for mutating tools, and the checkpoint copy is cleared as the
answer is returned.

Two design points have tests because getting either wrong reintroduces a real bug:

* **A record is not a receipt.** `core/tool_intent_executor.py:320-324` replays a stored receipt
  instead of running the tool. Records are write-only and never replayed, so a second read of a
  folder still reads the folder.
* **A missing key means empty, not unknown.** The observation builder drops any value equal to
  None/""/[]/{}, so an empty listing has no `entries` key at all.
"""
from __future__ import annotations

import pytest

from core import execution_records as records
from core.runtime_tool_contracts import ToolClaim

LIST_CLAIM = ToolClaim(target_argument="path", resolved_target_key="path", result_items_key="entries")
WRITE_CLAIM = ToolClaim(target_argument="path", resolved_target_key="path", asserts_action=True)

# Verbatim from a real machine.list_directory run against the failure folder.
REAL_OBSERVATION = {
    "schema": "tool_observation_v1",
    "intent": "machine.list_directory",
    "tool_surface": "machine",
    "ok": True,
    "status": "executed",
    "path": "~/Desktop/vool-w5x1",
    "count": 3,
    "entries": [
        {"name": "iota.rb", "path": "/Users/x/Desktop/vool-w5x1/iota.rb", "type": "file"},
        {"name": "tau.sql", "path": "/Users/x/Desktop/vool-w5x1/tau.sql", "type": "file"},
        {"name": "upsilon.md", "path": "/Users/x/Desktop/vool-w5x1/upsilon.md", "type": "file"},
    ],
}


@pytest.fixture(autouse=True)
def _clean():
    records.clear()
    yield
    records.clear()


def _list_record(session: str = "s1", observation=None):
    return records.record(
        session_id=session,
        intent="machine.list_directory",
        arguments={"path": "~/Desktop/vool-w5x1"},
        observation=observation if observation is not None else REAL_OBSERVATION,
        claim=LIST_CLAIM,
    )


def test_the_measured_failure_now_has_ground_truth() -> None:
    entry = _list_record()
    assert entry.resolved_target == "~/Desktop/vool-w5x1"
    assert entry.items == ("iota.rb", "tau.sql", "upsilon.md")


def test_a_read_only_tool_is_recorded() -> None:
    """The whole point: receipts cover only mutating tools, so a listing left nothing."""

    _list_record()
    assert records.bind_summary("s1")["call_count"] == 1


def test_the_tool_report_beats_the_users_spelling() -> None:
    """A user types `~/Desktop/x`; what matters is what the tool resolved that to.

    Binding against the raw argument would accept a claim about a folder never opened.
    """

    entry = records.record(
        session_id="s1",
        intent="machine.find_folder",
        arguments={"name": "~/Desktop/vool-w5x1"},
        observation={"ok": True},
        details={"resolved_target": "/Users/x/Desktop/vool-w5x1"},
        claim=ToolClaim(target_argument="name", resolved_target_key="resolved_target"),
    )
    assert entry.resolved_target == "/Users/x/Desktop/vool-w5x1"


def test_the_argument_is_the_last_resort_target() -> None:
    entry = records.record(
        session_id="s1",
        intent="machine.list_directory",
        arguments={"path": "~/Desktop/x"},
        observation={"ok": True},
        claim=LIST_CLAIM,
    )
    assert entry.resolved_target == "~/Desktop/x"


def test_a_missing_items_key_means_empty_not_unknown() -> None:
    """The observation builder drops empty values, so an empty listing has no `entries` key."""

    observation = {"ok": True, "status": "executed", "path": "~/Desktop/empty", "count": 0}
    entry = _list_record(observation=observation)
    assert entry.items == ()
    assert entry.has_target is True, "an empty result still tells us which folder was read"


def test_a_tool_with_no_declared_claim_is_still_recorded() -> None:
    entry = records.record(session_id="s1", intent="some.tool", arguments={"a": 1})
    assert entry.intent == "some.tool"
    assert entry.resolved_target == ""
    assert entry.items == ()


def test_string_items_and_dict_items_both_reduce_to_names() -> None:
    entry = records.record(
        session_id="s1",
        intent="machine.find_folder",
        observation={"matches": ["/a/b", {"name": "c"}, {"path": "/d"}, 7, ""]},
        claim=ToolClaim(result_items_key="matches"),
    )
    assert entry.items == ("/a/b", "c", "/d")


def test_citations_are_pulled_from_the_declared_keys() -> None:
    entry = records.record(
        session_id="s1",
        intent="operator.move_path",
        observation={"moved_to": "/archive/x"},
        claim=ToolClaim(cites=("moved_to",), asserts_action=True),
    )
    assert entry.citations == ("/archive/x",)


def test_an_action_claim_needs_a_matching_target() -> None:
    """Today any receipt from any tool backs any mutation claim. That is what this replaces."""

    records.record(
        session_id="s1",
        intent="machine.write_file",
        arguments={"path": "/a/real.txt"},
        observation={"path": "/a/real.txt"},
        claim=WRITE_CLAIM,
    )
    assert records.has_action_receipt("s1", target="/a/real.txt") is True
    assert records.has_action_receipt("s1", target="/some/other.txt") is False


def test_a_read_only_tool_cannot_back_an_action_claim() -> None:
    _list_record()
    assert records.has_action_receipt("s1") is False


def test_a_failed_action_does_not_back_a_claim() -> None:
    records.record(
        session_id="s1",
        intent="machine.write_file",
        arguments={"path": "/a/real.txt"},
        observation={"path": "/a/real.txt"},
        claim=WRITE_CLAIM,
        ok=False,
        status="not_allowed",
    )
    assert records.has_action_receipt("s1", target="/a/real.txt") is False


def test_sessions_do_not_leak_into_each_other() -> None:
    _list_record(session="s1")
    assert records.records_for("s2") == ()


def test_records_are_bounded_per_session() -> None:
    for _ in range(200):
        _list_record()
    assert len(records.records_for("s1")) <= 64


def test_sessions_are_bounded() -> None:
    for index in range(100):
        _list_record(session=f"s{index}")
    assert len(records._sessions) <= 32


def test_clear_drops_one_session_or_all() -> None:
    _list_record(session="s1")
    _list_record(session="s2")
    records.clear("s1")
    assert records.records_for("s1") == ()
    assert records.records_for("s2")
    records.clear()
    assert records.records_for("s2") == ()


def test_recording_never_mutates_the_caller_dicts() -> None:
    """Observation is write-only telemetry; it must not touch what the tool returned."""

    observation = dict(REAL_OBSERVATION)
    arguments = {"path": "~/Desktop/vool-w5x1"}
    before_obs, before_args = dict(observation), dict(arguments)
    records.record(
        session_id="s1",
        intent="machine.list_directory",
        arguments=arguments,
        observation=observation,
        claim=LIST_CLAIM,
    )
    assert observation == before_obs and arguments == before_args
