"""The process list has to answer the question that was asked, in the words it was asked in.

Two defects measured live 2026-07-31 against the running daemon on d51250a:

1. "show me what's running" reached no routing family at all. It spent 40 seconds in the model and
   came back as a shell snippet for the user to run themselves -- ```ps -ef | grep -v 'grep'``` --
   while `machine.list_processes` answers the same question in 0.1s. The model had understood the
   question; only the route was missing. Both existing patterns required one of
   processes/programs/apps/applications to be present, and people drop that noun constantly.

2. "give me the top processes by memory" was answered with a fixed "combined CPU and memory
   pressure" ranking whose first entry used 0.6% of memory, while the process using 30.7% was
   fourth. `machine_process_sort` already read the requested ranking correctly -- the operator
   handler simply discarded `intent` on its first line and never consulted it.
"""

from __future__ import annotations

import pytest

from core.execution.constants import machine_live_load_intent, machine_process_sort
from core.operator.handlers import handle_inspect_processes
from core.operator.models import OperatorActionIntent

# -- 1. the noun-less phrasing ------------------------------------------------------------------

REACHES_THE_PROCESS_LIST = [
    "show me what's running",          # verbatim, the measured failure
    "show me whats running",
    "what is running right now",
    "whats running on this machine",
    "what's currently running",
    "what is running?",
    "whats running at the moment",
    "show me what is running on my mac",
    # -- second live round, 2026-07-31. Four more ordinary phrasings still missed. "top 5 processes
    # by cpu please" spent 75s in the model and returned nothing: the operator lane matches the
    # literal substring "top processes", and a count in the middle breaks it. "show me the heaviest
    # apps by ram" was answered with the hardware spec sheet. "any processes going crazy right now"
    # names no resource at all. "list everything thats running on this machine" was answered with
    # the model-provider status.
    "top 5 processes by cpu please",
    "show me the heaviest apps by ram",
    "any processes going crazy right now",
    "list everything thats running on this machine",
    "show me the top 10 processes by memory",
    "is any app going haywire",
    "list anything thats running right now",
]

# "running" with no noun is idiomatic English for a dozen other things. Each of these continues into
# an object that is not this machine, which is what the end-anchor in the pattern tests for.
STAYS_OFF_THE_PROCESS_LIST = [
    "what's running late",
    "what's running through your mind",
    "whats running in docker",
    "what is running on port 8080",
    "what's running in the marathon",
    "whats running down the street",
    "what is running in production",
    "whats running in the background on modern phones",
    "write a script that shows what is running",
    # A superlative over "apps" or "programs" is not by itself a question about this host, which is
    # why the ranking form additionally requires the resource it is ranked BY.
    "what are the biggest apps in the app store",
    "which apps are heaviest to download",
    "top apps of 2026",
    "top 5 albums of the year",
    "the biggest programs in the curriculum",
    "write a script that lists the top processes by cpu",
    "list everything on my shopping list",
    # "open TO" is the availability sense of open, not the running one. This sentence is named in the
    # source comment as one this lane once answered with a process table -- and it still did, because
    # the bare word "open" matched and the shared gate reads it as a genuine question.
    "what programs are open to graduates without a technical degree",
    "which grant programs are open to nonprofits",
    "our deployment process uses a lot of memory on the CI box",
    "write a blog post about which apps are running in the background on modern phones",
]


@pytest.mark.parametrize("phrasing", REACHES_THE_PROCESS_LIST)
def test_asking_what_is_running_without_the_noun_still_lists_processes(phrasing: str) -> None:
    assert machine_live_load_intent(phrasing) == "machine.list_processes", (
        f"{phrasing!r} asks what is running on this host. The runtime can answer it in 0.1s; "
        f"handing the user a `ps` command to run themselves is not an answer."
    )


@pytest.mark.parametrize("phrasing", STAYS_OFF_THE_PROCESS_LIST)
def test_running_about_something_else_does_not_list_processes(phrasing: str) -> None:
    assert machine_live_load_intent(phrasing) != "machine.list_processes", (
        f"{phrasing!r} is not about processes on this host."
    )


# -- 2. the requested ranking -------------------------------------------------------------------

# Ordered so that the top entry by MEMORY and the top entry by CPU are different rows, which is the
# only arrangement in which a wrong sort is visible at all. Mirrors the shape measured live: the
# busiest process by CPU was near-idle on memory, and vice versa.
ROWS = [
    {"pid": 30478, "name": "python", "cpu_percent": 99.3, "mem_percent": 0.6},
    {"pid": 35717, "name": "ollama", "cpu_percent": 7.6, "mem_percent": 30.7},
    {"pid": 412, "name": "WindowServer", "cpu_percent": 37.7, "mem_percent": 0.2},
    {"pid": 1926, "name": "Claude Helper", "cpu_percent": 4.9, "mem_percent": 3.0},
]


def _run(raw_text: str):
    class _Gate:
        mode = "execute"
        reason = ""

    return handle_inspect_processes(
        OperatorActionIntent(kind="inspect_processes", raw_text=raw_text),
        task_id="t1",
        session_id="s1",
        evaluate_local_action_fn=lambda *a, **k: _Gate(),
        inspect_processes_fn=lambda: list(ROWS),
        audit_log_fn=lambda *a, **k: None,
    )


def test_top_processes_by_memory_is_ranked_by_memory() -> None:
    result = _run("give me the top processes by memory")
    first = result.details["top_processes"][0]
    assert first["pid"] == 35717, (
        "asked for the top processes BY MEMORY and got a ranking headed by the process using "
        f"{first['mem_percent']}% of memory; 30.7% was available and should lead."
    )
    assert "memory" in result.response_text.splitlines()[0].lower()


def test_top_processes_by_cpu_is_ranked_by_cpu() -> None:
    result = _run("show me the top processes by cpu")
    first = result.details["top_processes"][0]
    assert first["pid"] == 30478, "asked for the top processes BY CPU and got a different ranking"
    assert "cpu" in result.response_text.splitlines()[0].lower()


def test_the_heading_names_the_ranking_that_was_actually_applied() -> None:
    """The old heading claimed a combined ranking regardless of what was asked or applied.

    A heading that describes a different ordering than the rows are in is its own defect: it tells
    the reader the list means something it does not.
    """
    memory_first_line = _run("what are the memory hogs").response_text.splitlines()[0]
    cpu_first_line = _run("what are the cpu hogs").response_text.splitlines()[0]
    assert memory_first_line != cpu_first_line
    assert "combined" not in memory_first_line.lower()


def test_an_unreadable_percentage_does_not_crash_the_ranking() -> None:
    """A row whose percentage cannot be parsed sorts last rather than taking the whole read down."""

    class _Gate:
        mode = "execute"
        reason = ""

    result = handle_inspect_processes(
        OperatorActionIntent(kind="inspect_processes", raw_text="top processes by memory"),
        task_id="t1",
        session_id="s1",
        evaluate_local_action_fn=lambda *a, **k: _Gate(),
        inspect_processes_fn=lambda: [
            {"pid": 1, "name": "broken", "cpu_percent": 0.0, "mem_percent": None},
            {"pid": 2, "name": "real", "cpu_percent": 1.0, "mem_percent": 12.5},
        ],
        audit_log_fn=lambda *a, **k: None,
    )
    assert result.ok is True
    assert result.details["top_processes"][0]["pid"] == 2


def test_process_sort_reads_the_requested_ranking() -> None:
    assert machine_process_sort("give me the top processes by memory") == "memory"
    assert machine_process_sort("give me the top processes by cpu") == "cpu"
