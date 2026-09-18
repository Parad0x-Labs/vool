"""The machine-specs fast path may only claim questions that ask what this hardware IS.

Found by two independent blind QA drives of the running daemon on `12128c2`, then reproduced
here against the same build over `/api/chat` before anything was changed:

    "whats eating my cpu right now"            -> the canned specs block (no process data)
    "What macOS version is this machine running?" -> the VOOL release/build block
    "How many CPU cores?"                      -> the model lane answered "4 cores." on a 10-core host
    "is this a laptop or desktop, and what's
     the battery percentage?"                  -> the model lane answered "Laptop. Battery at 72%."
                                                  on a desktop iMac that has no battery
    "what is this machine's uptime?"           -> no usable answer at all
    "What files are inside ~/Desktop/my-budget-folder?"
                                               -> answered by whichever lane happened to win the run

One cause, in two directions. ``_MACHINE_SPEC_MARKERS`` is a substring list holding " cpu ",
" ram ", " chip " and " this machine ", so any sentence carrying one of those words was read as a
hardware question -- and any sentence that did NOT carry one fell through to a model with no tool
result and a number to invent. Both are the same mistake: routing on a topic word instead of on
what the sentence asks for.

This is the fourth time that shape has been fixed in this repo (prose read as a destructive
command; a discussion read as a build instruction; a greeting eating the question after it), so
the rule is pinned here per phrasing rather than left to the marker list to get right.
"""
from __future__ import annotations

import types

import pytest

from core.agent_runtime.fast_paths_machine import (
    looks_like_machine_specs_question,
    looks_like_supported_machine_read_request,
)
from core.execution.constants import (
    machine_host_state_intent,
    machine_live_load_intent,
    machine_path_listing_intent,
    machine_process_sort,
)

# --------------------------------------------------------------------------------------
# The reported phrasings, one assertion each, verbatim
# --------------------------------------------------------------------------------------


class TestTheSpecsFamilyStandsDown:
    """Every quoted phrasing, and the family that should own it instead."""

    @pytest.mark.parametrize(
        "phrasing",
        [
            "what is this machine's uptime?",
            "is this a laptop or desktop, and what's the battery percentage?",
            "whats eating my cpu right now",
            "What files are inside ~/Desktop/my-budget-folder?",
        ],
    )
    def test_a_live_state_question_is_not_a_specs_question(self, phrasing: str) -> None:
        assert looks_like_machine_specs_question(phrasing) is False, (
            f"{phrasing!r} still reads as a hardware-inventory question"
        )

    def test_uptime_routes_to_the_host_state_read(self) -> None:
        assert machine_host_state_intent("what is this machine's uptime?") == "machine.host_state"

    def test_battery_and_chassis_route_to_the_host_state_read(self) -> None:
        phrasing = "is this a laptop or desktop, and what's the battery percentage?"
        assert machine_host_state_intent(phrasing) == "machine.host_state"

    def test_cpu_pressure_routes_to_the_process_list_ranked_by_cpu(self) -> None:
        phrasing = "whats eating my cpu right now"
        assert machine_live_load_intent(phrasing) == "machine.list_processes"
        # A CPU question answered with a memory ranking is still the wrong answer.
        assert machine_process_sort(phrasing) == "cpu"

    def test_a_typed_out_path_plus_what_is_inside_it_routes_to_a_listing(self) -> None:
        phrasing = "What files are inside ~/Desktop/my-budget-folder?"
        assert machine_path_listing_intent(phrasing) == "~/Desktop/my-budget-folder"
        # ...and the read gate admits it, so the listing is deterministic rather than whatever
        # the arbiter picked on the day.
        assert looks_like_supported_machine_read_request(phrasing) is True


class TestTheQuestionsTheSpecsProbeCanAnswer:
    """Two phrasings were refused or fabricated while the probe held the value all along."""

    @pytest.mark.parametrize(
        "phrasing",
        ["What macOS version is this machine running?", "How many CPU cores?"],
    )
    def test_it_reaches_the_specs_lane(self, phrasing: str) -> None:
        assert looks_like_supported_machine_read_request(phrasing) is True

    def test_the_os_version_question_is_not_a_question_about_the_vool_build(self) -> None:
        from core.web.api.service import _looks_like_runtime_version_question

        assert _looks_like_runtime_version_question("What macOS version is this machine running?") is False
        # ...and the product's own version question still is one.
        assert _looks_like_runtime_version_question("what version of vool are you running?") is True
        assert _looks_like_runtime_version_question("what version are you running?") is True

    def test_how_many_extracts_a_specs_request_the_same_way_how_much_does(self) -> None:
        from core.execution.planner import _extract_machine_specs_request

        assert _extract_machine_specs_request("How many CPU cores?") == {}
        assert _extract_machine_specs_request("how much ram do I have?") == {}


# --------------------------------------------------------------------------------------
# The specs handler must not be weakened to achieve any of the above
# --------------------------------------------------------------------------------------


class TestGenuineSpecsQuestionsStillWork:
    @pytest.mark.parametrize(
        "phrasing",
        [
            "what is my machine specs?",
            "what chip do I have",
            "how much ram does this machine have?",
            "what are the system specs of this machine?",
            "what gpu am I using?",
            "how many cpu cores does this machine have?",
        ],
    )
    def test_a_hardware_question_is_still_claimed(self, phrasing: str) -> None:
        assert looks_like_machine_specs_question(phrasing) is True
        assert looks_like_supported_machine_read_request(phrasing) is True

    @pytest.mark.parametrize(
        "raw", ["what is my machine scpecs?", "show me my mahcine specs"]
    )
    def test_the_typo_forms_still_reach_the_specs_lane(self, raw: str) -> None:
        """The front door hands the handler CORRECTED text (df8fc41), so that is what is checked."""
        from core.input_normalizer import normalize_user_text

        corrected = normalize_user_text(raw).normalized_text
        assert looks_like_supported_machine_read_request(corrected) is True, (
            f"{raw!r} -> {corrected!r} no longer reaches the specs lane"
        )


class TestTheOtherMachineLanesKeepTheirQuestions:
    """The new classifiers must not take work off the lanes that already answer correctly."""

    def test_storage_pressure_still_belongs_to_the_disk_lane(self) -> None:
        assert machine_live_load_intent("what's eating my disk space") is None

    def test_the_operator_lane_keeps_its_own_phrasings(self) -> None:
        # `list the top processes by cpu usage` already answered with real PIDs and CPU% through
        # the operator lane (parse -> gate -> audit log). Neither new family may intercept it, and
        # the specs family must not report a competing claim just because it says "cpu".
        phrasing = "list the top processes by cpu usage"
        assert machine_live_load_intent(phrasing) is None
        assert looks_like_machine_specs_question(phrasing) is False

    def test_an_operator_owned_process_question_is_not_left_looking_like_a_near_miss(self) -> None:
        """Standing down from ROUTING it must not turn it into an unclaimed message.

        Removing the bogus machine_specs claim emptied the claim list, `near_miss` went True, and
        the arbiter was consulted for a phrasing the operator lane already owned: measured on the
        fixed daemon at 6.1s where it had been 0.2s. The family reading is still "process list"
        even when another lane serves it.
        """
        from core.agent_runtime.intent_claims import is_ambiguous, near_miss, probe_claims

        phrasing = "list the top processes by cpu usage"
        claims = probe_claims(phrasing)
        assert [claim.family for claim in claims] == ["list_processes"]
        assert is_ambiguous(claims) is False
        assert near_miss(phrasing, claims) is False

    def test_a_general_knowledge_battery_question_is_not_a_host_read(self) -> None:
        assert machine_host_state_intent("how long does a macbook battery last?") is None

    def test_uptime_of_something_that_is_not_this_host_is_not_a_host_read(self) -> None:
        assert machine_host_state_intent("how long has the build been running?") is None

    def test_naming_a_path_while_asking_something_else_is_not_a_listing(self) -> None:
        # A bare preposition is not a request for a file list. This sentence wants the folder
        # explained, and a list of filenames is the same class of miss as the whole-Desktop dump
        # this path's original guard was written for.
        assert machine_path_listing_intent(
            "take a look inside /Users/me/Desktop/ledger-demo and tell me what the billing helper does"
        ) is None
        assert machine_path_listing_intent("what does the billing helper in that repo do?") is None
        # ...while an explicit ask for the contents of that same folder is claimed.
        assert machine_path_listing_intent(
            "what files are in /Users/me/Desktop/ledger-demo"
        ) == "/Users/me/Desktop/ledger-demo"


# --------------------------------------------------------------------------------------
# Wired, not merely written — the classifier is only worth anything if dispatch obeys it
# --------------------------------------------------------------------------------------


def _route(monkeypatch, phrasing: str) -> tuple[object, list[tuple[str, dict]]]:
    """Drive the real machine fast path and record the tool it actually reached for."""
    import core.agent_runtime.fast_paths_machine as fp
    from apps.vool_agent import VoolAgent

    calls: list[tuple[str, dict]] = []

    def _record(intent, arguments=None, *, source_context=None):
        calls.append((str(intent), dict(arguments or {})))
        return types.SimpleNamespace(
            ok=True, response_text=f"[{intent}]", status="executed", details={"observation": {}}
        )

    monkeypatch.setattr(fp, "execute_authorized_runtime_tool", _record)
    agent = VoolAgent(backend_name="test-backend", device="openclaw-test", persona_id="default")
    result = agent._maybe_handle_direct_machine_read_request(
        phrasing,
        session_id=f"routing-{abs(hash(phrasing))}",
        source_surface="api",
        source_context={"surface": "api"},
    )
    return result, calls


class TestTheDispatchReachesThoseTools:
    @pytest.mark.parametrize(
        ("phrasing", "expected_intent"),
        [
            ("what is this machine's uptime?", "machine.host_state"),
            ("is this a laptop or desktop, and what's the battery percentage?", "machine.host_state"),
            ("whats eating my cpu right now", "machine.list_processes"),
            ("What files are inside ~/Desktop/my-budget-folder?", "machine.list_directory"),
            ("What macOS version is this machine running?", "machine.inspect_specs"),
            ("How many CPU cores?", "machine.inspect_specs"),
            ("what is my machine specs?", "machine.inspect_specs"),
            ("what chip do I have", "machine.inspect_specs"),
        ],
    )
    def test_the_reported_phrasing_reaches_its_own_tool(
        self, monkeypatch, phrasing: str, expected_intent: str
    ) -> None:
        result, calls = _route(monkeypatch, phrasing)
        assert result is not None, f"{phrasing!r} reached no tool at all"
        assert [intent for intent, _ in calls] == [expected_intent], (
            f"{phrasing!r} ran {[i for i, _ in calls]}, expected {expected_intent}"
        )

    def test_the_cpu_question_asks_the_process_list_to_rank_by_cpu(self, monkeypatch) -> None:
        _result, calls = _route(monkeypatch, "whats eating my cpu right now")
        assert calls[0][1].get("sort") == "cpu"

    def test_the_listing_is_given_the_path_the_user_typed(self, monkeypatch) -> None:
        _result, calls = _route(monkeypatch, "What files are inside ~/Desktop/my-budget-folder?")
        # The claim under test is the PATH -- that the listing targets what the user typed and not
        # a parent folder. It was written as exact-dict equality, which also froze the argument set;
        # `limit` was added later because the tool's own default of 50 was silently truncating a
        # Desktop listing at 50 of 81 entries. Assert the path, and that nothing redirects it.
        assert calls[0][1]["path"] == "~/Desktop/my-budget-folder"
        assert calls[0][1].get("directories_only") is not True


class TestTheToolsThemselvesReturnRealReadings:
    """Executed against this host, unmocked: a routing fix that reaches an empty tool is not a fix."""

    def test_host_state_measures_uptime_and_is_honest_about_the_battery(self) -> None:
        from core.runtime_execution_tools import execute_runtime_tool

        execution = execute_runtime_tool("machine.host_state", {})
        assert execution is not None and execution.ok, "machine.host_state did not execute"
        state = dict(execution.details["state"])
        assert state["uptime_seconds"] is not None and state["uptime_seconds"] > 0
        assert "Uptime:" in execution.response_text
        battery = dict(state["battery"])
        # present is True/False from a real sensor read -- never left unknown while a percentage
        # is quoted, which is how "Battery at 72%" got said about a machine with no battery.
        assert battery["present"] in (True, False)
        if battery["present"] is False:
            assert battery["percent"] is None
            assert "no battery" in execution.response_text
        else:
            assert isinstance(battery["percent"], float)
        assert state["chassis"] in {"laptop", "desktop"}

    def test_the_cpu_ranking_carries_a_cpu_number_per_process(self) -> None:
        from core.runtime_execution_tools import execute_runtime_tool

        execution = execute_runtime_tool("machine.list_processes", {"sort": "cpu", "limit": 5})
        assert execution is not None and execution.ok, "machine.list_processes did not execute"
        processes = list(execution.details["processes"])
        assert processes, "no processes were read"
        assert all("cpu_percent" in row for row in processes)
        # Ranked by the thing that was asked about, not by memory.
        percents = [float(row["cpu_percent"]) for row in processes]
        assert percents == sorted(percents, reverse=True)
        assert "% CPU" in execution.response_text
