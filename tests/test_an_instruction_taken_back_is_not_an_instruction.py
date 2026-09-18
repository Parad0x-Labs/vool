"""A turn that withdraws an instruction mid-sentence has not given it.

Measured on the served surface, 2026-08-15::

    U: Search my local workspace for `password.txt`. WAIT, STOP. Cancel the search immediately.
       Instead, write a 2-line javascript function that returns "Hello". ...
    A: (empty answer)
       Work log: workspace.search_text  query=password.txt  ->  no_results
       32,151 tokens

The search the user cancelled ran anyway -- against the very filename they had just named as the
thing not to look for -- and the work they DID ask for came back empty.

The neighbouring turn in the same session was fine, which is what made this one worth chasing:
"Trigger a web search for the weather in Paris. WAIT! Stop. Do NOT search the web." ran no search.
It survived by accident of phrasing. `analyze_retrieval_constraints` reads PROHIBITION ("do not
search"), and that turn happened to use it. The password turn used RETRACTION ("cancel the search"),
and nothing in the runtime read retraction at all:

    paris     has_prohibition=True    -> blocked
    password  has_prohibition=False   -> ran

Two different speech acts. One forbids a class of action; the other withdraws an instruction already
given. Only the first had a reader.

The invariant, stated by POSITION rather than by tool or by topic -- a retraction cue withdraws what
came before it, and what follows is the live request -- so it holds for a workspace search, a web
search, a file read, or a tool that does not exist yet.

The controls matter as much as the repair here. Reading retraction too eagerly silently discards
work the user actually asked for, which is the same harm pointing the other way: "cancel the
subscription and tell me why it renewed" is a REQUEST to cancel something, not a retraction.
"""

from __future__ import annotations

import pytest

from core.execution.planner import has_explicit_tool_intent_request
from core.within_turn_retraction import intake_request_text, live_request_after_retraction

_REPORTED = (
    "Search my local workspace for `password.txt`. WAIT, STOP. Cancel the search immediately. "
    'Instead, write a 2-line javascript function that returns "Hello".'
)


def _tool_intent(text: str) -> bool:
    return has_explicit_tool_intent_request(text, task_class="general")


# ---------------------------------------------------------------------------------------------
# The reported turn
# ---------------------------------------------------------------------------------------------


def test_the_reported_turn_no_longer_reads_as_a_request_to_search() -> None:
    """The defect exactly as measured: the withdrawn search drove the plan."""

    assert _tool_intent(_REPORTED) is False


def test_the_live_half_of_the_reported_turn_is_what_survives() -> None:
    """Withdrawing the search must leave the javascript request standing, not eat the whole turn."""

    live = live_request_after_retraction(_REPORTED)

    assert live is not None
    assert "javascript" in live.lower()
    assert "password.txt" not in live


# ---------------------------------------------------------------------------------------------
# The class, not the phrasing -- these share no wording with the report
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "withdrawn"),
    (
        ("Read config.yaml and summarise it. Actually ignore that, just say hi to me.", "config.yaml"),
        ("Delete the temp folder. Scratch that, tell me what is in it.", "delete"),
        ("Look up the weather in Oslo. I changed my mind. Instead, write a haiku.", "oslo"),
        ("Open the incident report. Cancel that, show me the summary instead.", "incident"),
        ("Email the on-call engineer. Never mind, draft the message for me instead.", "on-call"),
        ("Fetch the latest release notes. WAIT, STOP. Just explain semantic versioning.", "release notes"),
    ),
)
def test_the_withdrawn_object_never_survives_into_the_live_request(text: str, withdrawn: str) -> None:
    """The invariant that holds for every tool: the thing the user took back is simply gone.

    Asserted on the WITHDRAWN OBJECT rather than on a tool-intent verdict, because what remains
    after a retraction may legitimately still need a tool -- "cancel that, show me the summary
    instead" is a real request. What must never happen is the runtime acting on the withdrawn half.
    """

    live = live_request_after_retraction(text)

    assert live is not None, text
    assert withdrawn.lower() not in live.lower(), text


@pytest.mark.parametrize(
    "text",
    (
        "Read config.yaml and summarise it. Actually ignore that, just say hi to me.",
        "Look up the weather in Oslo. I changed my mind. Instead, write a haiku.",
        "Fetch the latest release notes. WAIT, STOP. Just explain semantic versioning.",
    ),
)
def test_a_turn_left_conversational_by_its_retraction_asks_for_no_tool(text: str) -> None:
    """Where the surviving request needs nothing fetched, no tool intent may remain."""

    assert _tool_intent(text) is False, text


@pytest.mark.parametrize(
    "text",
    (
        "Search my local workspace for password.txt and tell me what you find",
        "Look up the current weather in Oslo please",
        "Search the web for the latest release notes",
    ),
)
def test_an_unretracted_request_still_reaches_the_tools(text: str) -> None:
    """The control that keeps the repair from becoming a way to ignore real work."""

    assert _tool_intent(text) is True, text


# ---------------------------------------------------------------------------------------------
# Negative controls -- a cue is not a retraction just because the word appears
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    (
        # Cancelling IS the task.
        "cancel the subscription and tell me why it renewed",
        "cancel my flight and rebook it for Tuesday",
        # "stop" and "wait" as subject matter, not as commands.
        "stop words are removed by the tokenizer, explain how that works",
        "wait for the build to finish then run the tests",
        # A retraction of something OUTSIDE this turn is a different lane's problem.
        "never mind",
        "I changed my mind about the colour scheme",
    ),
)
def test_a_passing_mention_is_not_a_retraction(text: str) -> None:
    """False retraction silently discards what the user asked for -- the same harm, reversed."""

    assert live_request_after_retraction(text) is None, text


def test_only_the_final_change_of_mind_stands() -> None:
    """A turn may change its mind twice; the live request is the last one, not the first."""

    live = live_request_after_retraction(
        "Search the web for X. Actually ignore that, read the changelog. "
        "Cancel that, just tell me the version number."
    )

    assert live is not None
    assert "version number" in live
    assert "changelog" not in live


def test_a_retraction_with_nothing_after_it_withdraws_nothing() -> None:
    """Never leave the runtime with no request at all: an empty remainder is not a retraction."""

    assert live_request_after_retraction("Search my workspace for password.txt. Cancel that.") is None


# ---------------------------------------------------------------------------------------------
# A typo in one cue must not resurrect the withdrawn instruction
# ---------------------------------------------------------------------------------------------


def test_a_misspelled_second_cue_does_not_revive_the_withdrawn_search() -> None:
    """Measured live on hostile seed 777, and the withdrawn search RAN:

        U: search my workspace for wallet_backup.zip. WAIT no, cnacel that, just tell me 25+20
        A: "search my workspace for wallet_backup.zip: no matching source found."

    The user cancelled twice and misspelled the second cue. "WAIT no" was already a recognised
    cue -- the pairing simply demanded the live request IMMEDIATELY after it, and "cnacel that,"
    sat in the gap. A user who cancels harder than required must not end up cancelling less.
    """

    live = live_request_after_retraction(
        "search my workspace for wallet_backup.zip. WAIT no, cnacel that, just tell me 25+20"
    )

    assert live is not None
    assert "25+20" in live
    assert "wallet_backup" not in live


@pytest.mark.parametrize(
    ("text", "survives"),
    (
        ("read config.yaml. wait stop, ignore all that, just say hi to me", "say hi"),
        ("list my files. hold on scratch that, um, just tell me 2+2", "2+2"),
        ("fetch the logs. WAIT, STOP. no really, just explain what a log is", "explain"),
    ),
)
def test_filler_between_the_cue_and_the_request_is_tolerated(text: str, survives: str) -> None:
    live = live_request_after_retraction(text)

    assert live is not None, text
    assert survives in live, text


@pytest.mark.parametrize(
    "text",
    (
        # The bound must not let a real clause be swallowed as "noise".
        "the deploy finishes in 5 hours, tell me what time it is",
        "cancel the subscription and tell me why it renewed",
        "wait for the build to finish then run the tests",
    ),
)
def test_the_noise_bound_never_swallows_a_real_clause(text: str) -> None:
    """Relaxing adjacency must not relax the requirement for a cue AND a request."""

    assert live_request_after_retraction(text) is None, text


# ---------------------------------------------------------------------------------------------
# A withdrawn action is withdrawn whatever verb names it -- measured 2026-08-15
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "withdrawn", "survives"),
    (
        # The three operator-transcript turns, verbatim.
        (
            "Read the last 50 lines of /var/log/syslog. WAIT, STOP. Cancel the file read "
            "immediately. Do not touch the file system. Instead, write a Python dictionary "
            "that maps three fruit names to their colors.",
            "syslog",
            "dictionary",
        ),
        (
            "Ping the server at 10.0.0.1. HOLD ON! Abort the network request. "
            "Instead, tell me what ICMP stands for.",
            "10.0.0.1",
            "icmp",
        ),
        (
            "Look up the current price of AAPL. HOLD ON! Abort the financial lookup. "
            "Instead, write a limerick about markets.",
            "aapl",
            "limerick",
        ),
        # Paraphrases across other tool domains and the rest of the verb family.
        (
            "Run a port scan on localhost. Hold on, halt the scan. Instead, describe what a port is.",
            "localhost",
            "describe what a port is",
        ),
        (
            "Download the ubuntu ISO. Never mind, scrap the download, just give me the checksum command.",
            "iso",
            "checksum",
        ),
        (
            "Query the users table. Wait, call off the query. Instead, explain what an index does.",
            "users table",
            "index",
        ),
        (
            "Fetch https://example.com/data.json. Actually, cancel the fetch, "
            "just show me an example payload.",
            "data.json",
            "example payload",
        ),
    ),
)
def test_every_retraction_verb_withdraws_its_object(text: str, withdrawn: str, survives: str) -> None:
    """Measured 2026-08-15: "Abort the financial lookup" and "Cancel the file read" both RAN the
    withdrawn action -- the cue read only the verb "cancel" and a closed noun set that allowed no
    leading modifier, so "network request" missed although "request" was in-set."""

    live = live_request_after_retraction(text)

    assert live is not None, text
    assert withdrawn.lower() not in live.lower(), text
    assert survives.lower() in live.lower(), text


@pytest.mark.parametrize(
    "text",
    (
        # IN-SET nouns on purpose: the earlier controls used out-of-set nouns ("subscription",
        # "flight"), which a wider noun set never touches -- they proved nothing about this
        # change. A conjunction after the object continues the SAME request; narrowing here
        # drops the half the user actually asked to have done.
        "cancel the request and tell me why it failed",
        "cancel the search and tell me how long it had been running",
        "cancel the download and tell me which mirror is faster",
        "cancel the last search and then run a fresh one",
        "abort the query and show me the partial results",
        # Meta-question about code, not a withdrawal of anything in this turn.
        "how do I cancel the network request in my fetch code?",
    ),
)
def test_a_conjunction_after_the_object_continues_the_same_request(text: str) -> None:
    """Both halves must reach the model: "cancel the X and tell me Y" is one request, not a
    retraction of its own first half."""

    assert live_request_after_retraction(text) is None, text


def test_a_conjunction_beyond_the_sentence_boundary_does_not_block_the_retraction() -> None:
    """The conjunction guard reads the cue's own clause only -- an "and" in the LIVE request
    ("list the files and count them") is none of its business."""

    live = live_request_after_retraction(
        "Search the web for X. Cancel the search immediately. Instead, list the files and count them."
    )

    assert live is not None
    assert "list the files and count them" in live


# ---------------------------------------------------------------------------------------------
# The dispatch gates read the NARROWED text -- a withdrawn clause must not drive them
# ---------------------------------------------------------------------------------------------


def _prepare_turn_bundle(*, raw: str, effective: str) -> dict:
    """Drive `prepare_turn_task_bundle` the way `apps/vool_agent.py` does: `user_input` is the
    verbatim turn, `effective_input` is the intake-narrowed request."""

    from types import SimpleNamespace
    from unittest import mock

    from core.agent_runtime import turn_dispatch

    agent = SimpleNamespace(
        _resolve_runtime_task=mock.Mock(return_value=SimpleNamespace(task_id="retraction-task")),
        _update_runtime_checkpoint_context=mock.Mock(),
        _update_task_class=mock.Mock(),
        _emit_runtime_event=mock.Mock(),
        _action_fast_path_result=mock.Mock(side_effect=lambda **kwargs: kwargs),
        # A turn the gates decline falls through to the hive/builder lanes; none of them claim
        # these turns, so the bundle comes back with a live `task` instead of a fast-path result.
        _maybe_handle_hive_create_confirmation=mock.Mock(return_value=None),
        _extract_hive_topic_create_draft=mock.Mock(return_value=None),
        _maybe_handle_hive_topic_mutation_request=mock.Mock(return_value=None),
        _maybe_handle_hive_topic_create_request=mock.Mock(return_value=None),
        _maybe_run_builder_controller=mock.Mock(return_value=None),
    )
    return turn_dispatch.prepare_turn_task_bundle(
        agent,
        effective_input=effective,
        user_input=raw,
        session_id="retraction-session",
        source_context={"surface": "api"},
        interpreted=SimpleNamespace(as_context=lambda: {}),
        classify_fn=mock.Mock(return_value={"task_class": "general"}),
        parse_channel_post_intent_fn=mock.Mock(return_value=(None, None)),
        dispatch_outbound_post_intent_fn=mock.Mock(),
        parse_operator_action_intent_fn=mock.Mock(return_value=None),
        dispatch_operator_action_fn=mock.Mock(),
    )


def test_a_withdrawn_delete_clause_does_not_drive_the_file_delete_gate() -> None:
    """Measured before the fix: the gate read RAW `user_input` via an or-chain, so this turn
    answered "I did not delete `temp.txt`" about a delete the user had already taken back,
    instead of answering the live question."""

    raw = "Delete temp.txt right away. WAIT, STOP. Cancel that, just tell me what a tmp file is for."
    effective = intake_request_text(raw)
    assert "temp.txt" not in effective  # precondition: intake really narrowed the turn

    bundle = _prepare_turn_bundle(raw=raw, effective=effective)

    result = bundle.get("result")
    route = result.get("route") if isinstance(result, dict) else None
    assert route != "action_honesty_no_execution", result


def test_a_withdrawn_named_tool_clause_does_not_drive_the_missing_tool_gate() -> None:
    raw = (
        "Use a local tool named shred_everything to wipe the cache. WAIT, STOP. "
        "Cancel that, just explain what a cache is."
    )
    effective = intake_request_text(raw)
    assert "shred_everything" not in effective

    bundle = _prepare_turn_bundle(raw=raw, effective=effective)

    result = bundle.get("result")
    route = result.get("route") if isinstance(result, dict) else None
    assert route != "tool_honesty_missing_tool", result


def test_an_unretracted_delete_still_reaches_the_honesty_gate() -> None:
    """The control: reading only the narrowed text must not disable the gate for a turn that
    never retracted anything (narrowing passes such a turn through byte-identical)."""

    raw = "Delete temp.txt and say it is done."
    effective = intake_request_text(raw)
    assert effective == raw

    bundle = _prepare_turn_bundle(raw=raw, effective=effective)

    result = bundle.get("result")
    assert isinstance(result, dict)
    assert result.get("route") == "action_honesty_no_execution"
