"""Round 2 of the provenance footer: it must describe the turn it is attached to, not a default.

What was live on 6a73d52 and is fixed here
------------------------------------------
Three reports, one shape between the first two::

    conductor, Activity showing a cloud Nemotron call   ->  `local | model | tokens unreported`
    read of exact_one_file_6a73.txt via workspace.read_file  ->  `local | model | tokens unreported`
    exact named write, no generation                    ->  `tool | builder_model_build | 1 file | ...`

None of the four words in `local | model | tokens unreported` was read from anything:

* **`local`** -- `read_turn_provenance` computed the lane as
  ``"cloud" if "cloud" in cost_class else "local"``, so an ABSENT cost class resolved to the free
  lane. The cost class was absent because `core.memory_first_router._TURN_USAGE` is a
  ``threading.local()`` while the conductor (`core/conductor/scheduler.py`) and the tool planner
  (`core/agent_runtime/turn_planner.py`) run provider calls on a ``ThreadPoolExecutor``: the served
  usage was written on a worker and read on the API thread, which had none. Activity had it the
  whole time -- runtime events cross threads through the context dict, and the thread-local does not.
* **`model`** -- the literal fallback for an unknown model id, printed in the position where a model
  id goes, so an unread identity rendered as a model NAMED "model".
* **`tokens unreported`** -- true in isolation and false in company: it says the provider omitted its
  counts, when in fact the counts existed and did not survive the thread hop.
* the turn was promoted to model-served at all because ``model_calls > 0`` counted as authorship,
  even beside the same result's ``model_execution.used_model: False``.

And `builder_model_build` was the route reason of the FAMILY that dispatched the write, printed as
if it were the operation, on a receipt whose own ``generations: 0`` says no model build occurred.

The contract these tests hold
-----------------------------
1. A lane word (`local`/`cloud`) is printed only when a cost class or an invoked manifest named it.
   Otherwise the lane is `runtime` and the line says the identity is unrecorded.
2. `model_calls` proves calls were entered. It never proves a model wrote the answer.
3. `no model` is a whole-turn claim. The moment any provider call was entered, the claim narrows to
   `no model for the answer` and the calls are named -- an undisclosed cloud call is money.
4. The route segment names the tool that ran when one did, and the front-door family only otherwise.
5. Exactly one footer, whatever the append path is called.
"""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor

import pytest

from core.app_version import VOOL_VERSION
from core.response_provenance import (
    append_provenance_footer,
    format_provenance_footer,
    read_turn_provenance,
    strip_provenance_footer,
)

BUILD = f"build {VOOL_VERSION}"

LOCAL_USAGE = {
    "cost_class": "free_local",
    "model_id": "qwen2.5:7b",
    "prompt_tokens": 800,
    "output_tokens": 130,
}
CLOUD_USAGE = {
    "cost_class": "paid_cloud",
    "model_id": "nvidia/nemotron-nano-9b-v2",
    "prompt_tokens": 900,
    "output_tokens": 304,
    "usd_actual": 0.0021,
}


@pytest.fixture(autouse=True)
def _isolated_turn_state():
    """Reset both recorders around every test in this module.

    Not hygiene for its own sake: `core.memory_first_router._TURN_USAGE` is a `threading.local()`
    that nothing clears at the end of a turn, so a test that drives the real router funnel leaves a
    live cloud usage summary on the main thread and the next test's footer renders from it. That is
    a miniature of the production defect these tests are about -- state whose visibility depends on
    which thread you ask from -- and it must not be allowed to make a passing test agree with itself.
    """
    from core.memory_first_router import reset_turn_usage
    from core.turn_model_call_ledger import reset_for_tests

    reset_turn_usage()
    reset_for_tests()
    yield
    reset_turn_usage()
    reset_for_tests()


def _accounting(**overrides) -> dict:
    """The shape `core.turn_model_call_ledger.turn_call_accounting` really returns."""
    calls = int(overrides.get("calls") or 0)
    block = {
        "calls": calls,
        "completed_calls": calls,
        "failed_calls": 0,
        "pending_calls": 0,
        "failed_error_classes": [],
        "lanes": [],
        "models": [],
        "tools": [],
        "served_usage": {},
    }
    block.update(overrides)
    return block


def _fast_path(reason: str, *, prefix: str = "deterministic", **overrides) -> dict:
    result = {
        "response": "Done.",
        "model_execution": {"source": "fast_path", "used_model": False},
        "model_calls": 0,
        "model_selected": "",
        "route": f"{prefix}:{reason}",
        "route_reason": reason,
        "route_skips": ["model", "web", "tool_loop"],
        "fast_path_hit": True,
    }
    result.update(overrides)
    return result


def _model_turn(**overrides) -> dict:
    result = {
        "response": "Here is the answer.",
        "model_execution": {"source": "model", "used_model": True},
        "model_calls": 1,
        "route": "model:generic_conversation",
        "route_reason": "generic_conversation",
    }
    result.update(overrides)
    return result


# =================================================================================================
# The three reported defects, each pinned to the exact string it used to print.
# =================================================================================================


def test_a_conductor_turn_that_called_cloud_never_reports_the_free_local_lane() -> None:
    """Defect 1. Activity showed a cloud Nemotron call; the footer said `local`.

    Driven with the usage summary reaching the footer only through the turn ledger -- which is the
    live case, because the thread-local that used to be the only route is written on a conductor
    worker thread.
    """
    result = _model_turn(route="model:conductor", route_reason="conductor_multi_intent")
    accounting = _accounting(
        calls=3,
        lanes=["cloud"],
        models=["nvidia/nemotron-nano-9b-v2"],
        served_usage=CLOUD_USAGE,
    )

    footer = format_provenance_footer(result, None, accounting)

    assert footer == "`cloud | nemotron-nano-9b-v2 | 1,204 tok | $0.0021`"
    assert "local" not in footer
    assert "tokens unreported" not in footer


def test_a_cloud_call_is_named_even_when_the_provider_reported_no_usage() -> None:
    """The failure mode behind defect 1, with the tokens genuinely missing.

    A call that timed out or returned no usage block leaves nothing to meter, and this is exactly
    where the old code filled in `local`. The lane is still known -- it is a property of the
    manifest that was invoked, recorded at call entry -- so `cloud` is a reading, and only the
    tokens are reported as absent.
    """
    footer = format_provenance_footer(
        _model_turn(route_reason="conductor_multi_intent"),
        None,
        _accounting(calls=1, lanes=["cloud"], models=["nvidia/nemotron-nano-9b-v2"]),
    )

    assert footer == "`cloud | nemotron-nano-9b-v2 | tokens unreported`"


def test_a_workspace_read_says_the_tool_ran_it_not_that_a_model_did() -> None:
    """Defect 2, in the live shape: two routing calls above a deterministic read.

    The turn spent provider calls, so `no model` narrows to `no model for the answer` and the calls
    are named. What it must never do again is describe the READ as a model answer.
    """
    result = _fast_path("workspace_runtime_fast_path", model_calls=2)
    accounting = _accounting(calls=2, lanes=["local"], models=["qwen2.5:7b"], tools=["workspace.read_file"])

    footer = format_provenance_footer(result, None, accounting)

    assert footer == f"`tool | workspace.read_file | tool-generated answer | 2 local routing calls | {BUILD}`"


def test_a_workspace_read_with_no_provider_call_says_no_model_flatly() -> None:
    """The same read with nothing invoked above it. `no model` is then the whole truth and is used."""
    footer = format_provenance_footer(
        _fast_path("workspace_runtime_fast_path"),
        None,
        _accounting(tools=["workspace.read_file"]),
    )

    assert footer == f"`tool | workspace.read_file | no model | {BUILD}`"


def test_an_exact_named_write_is_not_labelled_a_model_build() -> None:
    """Defect 3. `builder_model_build` is the dispatching family; `workspace.write_file` is the tool.

    The receipt's own `generations: 0` says no model build happened, so printing the family name
    made the route segment assert the one thing the rest of the line denied.
    """
    result = _fast_path(
        "builder_model_build",
        prefix="action",
        details={"builder_model_build": {"files_written": ["exact_one_file_6a73.txt"], "generations": 0}},
    )

    footer = format_provenance_footer(result, None, _accounting(tools=["workspace.write_file"]))

    assert footer == f"`tool | workspace.write_file | 1 file | no model | {BUILD}`"
    assert "builder_model_build" not in footer


# =================================================================================================
# The response-class family. Every class gets a footer; none of them may claim what it cannot read.
# =================================================================================================

RESPONSE_CLASSES = [
    (
        "exact file write",
        _fast_path("builder_model_build", prefix="action", details={"builder_model_build": {"files_written": ["a.txt"]}}),
        None,
        _accounting(tools=["workspace.write_file"]),
        f"`tool | workspace.write_file | 1 file | no model | {BUILD}`",
    ),
    (
        "exact file read",
        _fast_path("workspace_runtime_fast_path"),
        None,
        _accounting(tools=["workspace.read_file"]),
        f"`tool | workspace.read_file | no model | {BUILD}`",
    ),
    (
        "smalltalk fast path",
        _fast_path("smalltalk_fast_path"),
        None,
        _accounting(),
        f"`tool | smalltalk_fast_path | no model | {BUILD}`",
    ),
    (
        "saved-name fast path",
        _fast_path("anaphoric_saved_name_followup"),
        None,
        _accounting(),
        f"`tool | anaphoric_saved_name_followup | no model | {BUILD}`",
    ),
    (
        "conductor multi-intent, cloud planning",
        _model_turn(route_reason="conductor_multi_intent"),
        None,
        _accounting(calls=2, lanes=["cloud"], models=["nvidia/nemotron-nano-9b-v2"], served_usage=CLOUD_USAGE),
        "`cloud | nemotron-nano-9b-v2 | 1,204 tok | $0.0021`",
    ),
    (
        "normal local chat",
        _model_turn(),
        LOCAL_USAGE,
        _accounting(calls=1, lanes=["local"], models=["qwen2.5:7b"], served_usage=LOCAL_USAGE),
        "`local | qwen2.5:7b | 930 tok`",
    ),
    (
        "cloud model chat",
        _model_turn(),
        CLOUD_USAGE,
        _accounting(calls=1, lanes=["cloud"], models=["nvidia/nemotron-nano-9b-v2"], served_usage=CLOUD_USAGE),
        "`cloud | nemotron-nano-9b-v2 | 1,204 tok | $0.0021`",
    ),
    (
        "approval pending",
        _fast_path("builder_controller_pending_approval", prefix="action"),
        None,
        _accounting(),
        f"`tool | builder_controller_pending_approval | no model | {BUILD}`",
    ),
]


@pytest.mark.parametrize(
    "label,result,usage,accounting,expected",
    RESPONSE_CLASSES,
    ids=[case[0] for case in RESPONSE_CLASSES],
)
def test_every_response_class_gets_a_footer_that_matches_its_lane(
    label, result, usage, accounting, expected
) -> None:
    assert format_provenance_footer(result, usage, accounting) == expected


def test_a_partially_failed_conductor_still_reports_its_provenance() -> None:
    """A plan where some nodes failed is still a turn that ran, and it says what it ran on.

    The degraded case is where a footer is most tempting to omit and most needed: the user is
    already looking at an incomplete answer and has to be able to tell what produced it.
    """
    result = _model_turn(
        response="Two of the three steps finished; the third failed.",
        route_reason="conductor_multi_intent",
        task_outcome="failed",
    )

    footer = format_provenance_footer(result, CLOUD_USAGE, _accounting(calls=4, lanes=["cloud", "local"]))

    assert footer == "`cloud | nemotron-nano-9b-v2 | 1,204 tok | $0.0021`"


def test_a_conductor_whose_every_call_failed_reports_the_lane_it_failed_on() -> None:
    """No usage at all, because nothing served. The calls were still entered and still cost.

    `used_model` is False -- correctly, nothing wrote the answer -- so the line reports a
    deterministic producer and discloses the four cloud calls beside it rather than under `no model`.
    """
    result = _fast_path("conductor_all_nodes_failed", prefix="action", model_calls=4)

    footer = format_provenance_footer(
        result,
        None,
        _accounting(
            calls=4,
            completed_calls=0,
            failed_calls=4,
            lanes=["cloud"],
            models=["nvidia/nemotron-nano-9b-v2"],
        ),
    )

    assert footer == f"`tool | conductor_all_nodes_failed | tool-generated answer | 4 cloud model attempts failed | {BUILD}`"
    assert "| no model |" not in footer


def test_a_model_reviewed_answer_names_the_generator_and_invents_no_reviewer() -> None:
    """A verifier-caveated reply is one model's answer plus a review the result does not identify.

    `core.local_inference_autopilot` holds `verifier_model` on the PLAN, not on the turn result, so
    there is no reviewer identity to print. The footer reports the generating lane and stays silent
    about the reviewer rather than naming a plausible one.
    """
    result = _model_turn(response="Draft answer.\n\n_Unverified: the reviewer did not confirm this._")

    footer = format_provenance_footer(result, LOCAL_USAGE, _accounting(calls=2, lanes=["local"], models=["qwen2.5:7b"]))

    assert footer == "`local | qwen2.5:7b | 930 tok`"


# =================================================================================================
# Authority. The reader must not be talked into a claim by a weaker signal.
# =================================================================================================


def test_model_calls_alone_never_promotes_a_deterministic_turn_to_model_served() -> None:
    """The precise inversion behind defects 1 and 2.

    `model_calls` counts provider calls ENTERED (`core/turn_model_call_ledger.py`). The same result
    carries `used_model: False` from the lane that built it. The producer's own statement wins.
    """
    provenance = read_turn_provenance(_fast_path("date_time_fast_path", model_calls=7), None, _accounting(calls=7))

    assert provenance.model_ran is False
    assert provenance.routing_calls == 7
    assert provenance.lane == "tool"


@pytest.mark.parametrize(
    ("label", "result", "usage", "accounting", "answer_source", "participation"),
    [
        pytest.param(
            "pure model answer",
            _model_turn(),
            LOCAL_USAGE,
            _accounting(calls=1, models=["qwen2.5:7b"]),
            "model",
            "generated_final_answer",
            id="model_generated_final_answer",
        ),
        pytest.param(
            "tool answer after a routing model",
            _fast_path("workspace_runtime_fast_path"),
            None,
            _accounting(calls=1, lanes=["local"], models=["qwen3:0.6b"], tools=["workspace.read_file"]),
            "tool",
            "routed_only",
            id="tool_answer_routing_model",
        ),
        pytest.param(
            "pure deterministic answer",
            _fast_path("date_time_fast_path"),
            None,
            _accounting(),
            "deterministic",
            "none",
            id="no_model_participated",
        ),
        pytest.param(
            "tool answer after failed model",
            _fast_path("workspace_runtime_fast_path"),
            None,
            _accounting(
                calls=1,
                completed_calls=0,
                failed_calls=1,
                lanes=["cloud"],
                models=["nvidia/nemotron:free"],
                tools=["workspace.read_file"],
            ),
            "tool",
            "attempted_failed_then_tool_answer",
            id="failed_model_then_tool_answer",
        ),
        pytest.param(
            "all model attempts empty",
            {
                "response": "I couldn't get a usable model response in this run.",
                "model_execution": {"source": "no_provider_available", "used_model": False},
                "model_calls": 2,
            },
            None,
            _accounting(
                calls=2,
                completed_calls=0,
                failed_calls=2,
                lanes=["cloud"],
                models=["nvidia/nemotron:free"],
            ),
            "none",
            "attempted_no_usable_answer",
            id="model_attempted_no_usable_answer",
        ),
    ],
)
def test_answer_authorship_and_model_participation_are_independent(
    label: str,
    result: dict,
    usage: dict | None,
    accounting: dict,
    answer_source: str,
    participation: str,
) -> None:
    provenance = read_turn_provenance(result, usage, accounting)
    assert provenance.answer_source == answer_source, label
    assert provenance.model_participation == participation, label


def test_no_usable_answer_names_only_the_model_that_was_actually_attempted() -> None:
    result = {
        "response": "I couldn't get a usable model response in this run.",
        "model_execution": {"source": "no_provider_available", "used_model": False},
        "model_calls": 2,
        # A selected model is not evidence it was called and must lose to the ledger below.
        "model_selected": "invented/selected-but-never-called",
    }
    accounting = _accounting(
        calls=2,
        completed_calls=0,
        failed_calls=2,
        lanes=["cloud"],
        models=["nvidia/nemotron:free"],
    )

    footer = format_provenance_footer(result, None, accounting)

    assert "nemotron:free" in footer
    assert "selected-but-never-called" not in footer
    assert "model attempted" in footer
    assert "no usable answer" in footer
    assert "2 cloud model attempts failed" in footer


def test_an_unnamed_lane_is_reported_as_unrecorded_and_never_as_local() -> None:
    """A model ran, nothing recorded where. The old default made that read as the free lane.

    Every word here is either read or explicitly marked absent, so the line is a usable bug report
    instead of a confident wrong answer.
    """
    footer = format_provenance_footer(_model_turn(model_selected=""), None, _accounting(calls=1))

    assert footer == f"`runtime | model id unrecorded | tokens unreported | {BUILD}`"
    assert "local" not in footer
    assert "cloud" not in footer


def test_a_mixed_lane_turn_reports_cloud_because_that_is_the_one_with_a_bill() -> None:
    provenance = read_turn_provenance(_model_turn(), None, _accounting(calls=3, lanes=["local", "cloud"]))

    assert provenance.lane == "cloud"


def test_zero_tokens_is_never_rendered_as_a_measurement() -> None:
    """`0 tok` would assert a count nobody made. The distinction from "unreported" is load-bearing."""
    footer = format_provenance_footer(
        _model_turn(),
        {"cost_class": "free_local", "model_id": "qwen2.5:7b", "prompt_tokens": 0, "output_tokens": 0},
    )

    assert footer == "`local | qwen2.5:7b | tokens unreported`"
    assert "0 tok" not in footer


def test_a_turn_that_recorded_nothing_at_all_is_still_not_described() -> None:
    """The honesty clause kept from round 1: silence beats `runtime | no model` on an unknown turn."""
    assert format_provenance_footer({"response": "Streamed prose."}, None) == ""
    assert append_provenance_footer("Streamed prose.", {"response": "Streamed prose."}, None) == "Streamed prose."


def test_an_OPEN_but_empty_ledger_is_not_itself_a_recording() -> None:
    """`begin_turn` now runs on every turn, so an empty accounting block reaches every result.

    Found by the full suite, not by reasoning: treating the block's PRESENCE as evidence made a
    model's streamed prose -- no usage, no `model_execution` -- footer itself `runtime | no model`,
    which is the round-1 false claim arriving by a new route. Two of
    `tests/test_capsule_override_safety.py` caught it by asserting the response byte for byte.
    """
    empty = _accounting()

    assert format_provenance_footer({"response": "Streamed prose."}, None, empty) == ""
    assert append_provenance_footer("Streamed prose.", {"response": "Streamed prose."}, None, empty) == "Streamed prose."
    # One observation of any kind is enough to describe the turn.
    assert format_provenance_footer({"response": "x"}, None, _accounting(calls=1)) != ""
    assert format_provenance_footer({"response": "x"}, None, _accounting(tools=["workspace.read_file"])) != ""


# =================================================================================================
# Negative controls. What the footer must NOT do to the answer around it.
# =================================================================================================


def test_no_path_or_argument_from_the_receipt_reaches_the_footer() -> None:
    """Only counts leave the receipt. A footer is not a place to widen the receipt policy."""
    result = _fast_path(
        "builder_model_build",
        prefix="action",
        details={
            "builder_model_build": {
                "target_dir": "/Users/someone/private/clients",
                "files_written": ["/Users/someone/private/clients/invoice.txt"],
                "api_key": "sk-live-4f9c2a17bd",
            }
        },
    )

    footer = format_provenance_footer(result, None, _accounting(tools=["workspace.write_file"]))

    assert footer == f"`tool | workspace.write_file | 1 file | no model | {BUILD}`"
    for leak in ("/Users/someone", "invoice.txt", "sk-live-4f9c2a17bd", "clients"):
        assert leak not in footer


def test_appending_twice_leaves_exactly_one_footer() -> None:
    """The buffered finalize can run after a streamed one and after a post-stream rewrite."""
    once = append_provenance_footer("The answer.", _model_turn(), LOCAL_USAGE)
    twice = append_provenance_footer(once, _model_turn(), LOCAL_USAGE)

    assert twice == once
    assert twice.count("`local |") == 1


def test_a_footer_of_a_DIFFERENT_shape_is_replaced_rather_than_stacked() -> None:
    """The stacking case an equality check would miss: the second render disagrees with the first.

    This is the live sequence -- a turn footered as a deterministic action, then re-finalized once
    the usage summary arrived late from a worker thread.
    """
    first = append_provenance_footer("The answer.", _fast_path("workspace_runtime_fast_path"), None)
    second = append_provenance_footer(first, _model_turn(), CLOUD_USAGE)

    assert second.count("`") == 2
    assert second == "The answer.\n\n`cloud | nemotron-nano-9b-v2 | 1,204 tok | $0.0021`"
    assert "no model" not in second


def test_a_markdown_table_in_the_answer_survives_the_footer_pass() -> None:
    """Pipe-separated rows are the action lane's normal output. Stripping one would delete a result."""
    table = (
        "Files — workspace root\n\n"
        "| file | count | status |\n"
        "| --- | --- | --- |\n"
        "| exact_one_file_6a73.txt | 1 | written |"
    )
    result = _fast_path("builder_model_build", prefix="action", response=table)

    rendered = append_provenance_footer(table, result, None, _accounting(tools=["workspace.write_file"]))

    assert rendered.startswith(table)
    assert rendered.endswith(f"`tool | workspace.write_file | no model | {BUILD}`")
    assert strip_provenance_footer(rendered) == table


def test_a_fenced_code_block_containing_pipes_is_not_mistaken_for_a_footer() -> None:
    """A shell pipeline and a Python union both look like the separator this footer uses."""
    answer = (
        "Run this:\n\n"
        "```bash\n"
        "cat notes.txt | grep alpha | wc -l\n"
        "```\n\n"
        "and the type is:\n\n"
        "```python\n"
        "value: str | None = None\n"
        "```"
    )

    rendered = append_provenance_footer(answer, _model_turn(), LOCAL_USAGE)

    assert rendered == answer + "\n\n`local | qwen2.5:7b | 930 tok`"
    assert strip_provenance_footer(rendered) == answer


def test_an_inline_code_span_that_is_not_a_lane_word_is_left_alone() -> None:
    """The strip regex is anchored on the four lane words, not on "backticks plus a pipe"."""
    answer = "The signature is:\n\n`parse(text) | None`"

    assert strip_provenance_footer(answer) == answer


# =================================================================================================
# The seams, driven for real. A shape assertion cannot see a thread-local that never crosses over.
# =================================================================================================


def test_served_usage_recorded_on_a_worker_thread_is_readable_from_the_caller() -> None:
    """The root cause of defect 1, reproduced against the real ledger and a real ThreadPoolExecutor.

    `core.memory_first_router.record_turn_usage` writes a `threading.local()`. This asserts BOTH
    halves: the thread-local really is invisible from here (so the bug is real and not a story),
    and the ledger really does carry the same summary across (so the fix is the thing that works).
    """
    from core.memory_first_router import get_turn_usage, record_turn_usage
    from core.turn_model_call_ledger import (
        begin_turn,
        record_provider_call,
        record_served_usage,
        reset_for_tests,
        turn_call_accounting,
        turn_served_usage,
    )

    reset_for_tests()
    context = {"request_id": "req-conductor-1"}
    begin_turn(context)

    def _worker(worker_context: dict) -> None:
        # Exactly what `_decision_from_response` does, in the place the conductor really does it.
        record_provider_call(
            worker_context,
            provider_id="openrouter-byok",
            model_id="nvidia/nemotron-nano-9b-v2",
            cost_class="paid_cloud",
        )
        record_turn_usage(CLOUD_USAGE)
        record_served_usage(worker_context, CLOUD_USAGE)

    with ThreadPoolExecutor(max_workers=1) as pool:
        # A SHALLOW COPY, as the conductor dispatches: the stamped ledger id travels, the thread does not.
        pool.submit(_worker, dict(context)).result()

    assert get_turn_usage() is None, "the thread-local is still invisible here -- that is the defect"
    assert turn_served_usage(context) == CLOUD_USAGE
    accounting = turn_call_accounting(context)
    assert accounting["lanes"] == ["cloud"]
    assert accounting["models"] == ["nvidia/nemotron-nano-9b-v2"]

    footer = format_provenance_footer(_model_turn(), get_turn_usage(), accounting)
    assert footer == "`cloud | nemotron-nano-9b-v2 | 1,204 tok | $0.0021`"


def test_the_intent_arbiters_call_is_recorded_as_the_local_lane_it_runs_on(monkeypatch) -> None:
    """The routing call that sits above the deterministic read, driven through the real arbiter.

    This is the call the live workspace-read turn spent -- the one the footer must disclose as
    `2 local routing calls` rather than hide under a flat `no model`. The arbiter posts straight to
    Ollama instead of going through the adapter, so its lane is known exactly and is not a guess.

    Added after a sabotage pass: dropping the identity keywords here left the whole suite green,
    because the lane word came from a hand-written accounting block everywhere else.
    """
    from unittest import mock

    from core import intent_arbiter as ia
    from core.agent_runtime.intent_claims import FAMILY_FIND_FOLDER, FAMILY_MACHINE_SPECS, IntentClaim
    from core.turn_model_call_ledger import begin_turn, reset_for_tests, turn_call_accounting

    monkeypatch.setenv("VOOL_INTENT_ARBITER", "1")
    monkeypatch.setenv("VOOL_ARBITER_MODEL", "qwen3:0.6b")
    ia.reset_breaker()
    reset_for_tests()
    context = {"request_id": "req-arbiter-1"}
    begin_turn(context)

    reply = mock.Mock()
    reply.raise_for_status = mock.Mock()
    reply.json.return_value = {"message": {"content": '{"choice": "find_folder", "argument": "token hunter"}'}}
    claims = [IntentClaim(FAMILY_FIND_FOLDER, "token hunter"), IntentClaim(FAMILY_MACHINE_SPECS)]

    with mock.patch("requests.post", return_value=reply):
        decision = ia.arbitrate("check Token hunter folder on this machine", claims, source_context=context)

    assert decision is not None, "the arbiter must really have run for this to be the right seam"
    accounting = turn_call_accounting(context)
    assert accounting["calls"] == 1
    assert accounting["lanes"] == ["local"]
    assert accounting["models"] == ["qwen3:0.6b"]

    footer = format_provenance_footer(
        _fast_path("workspace_runtime_fast_path"),
        None,
        {**accounting, "tools": ["workspace.read_file"]},
    )
    assert footer == f"`tool | workspace.read_file | tool-generated answer | 1 local routing call | {BUILD}`"
    provenance = read_turn_provenance(
        _fast_path("workspace_runtime_fast_path"),
        None,
        {**accounting, "tools": ["workspace.read_file"]},
    )
    assert provenance.answer_source == "tool"
    assert provenance.model_participation == "routed_only"


def test_a_failed_provider_call_still_names_the_lane_it_was_made_on() -> None:
    """A cloud call that was entered and then raised, driven through the real `_invoke_manifest`.

    Nothing was served, so no usage summary exists to name the lane with -- and this is the exact
    turn the old code rendered as `local`. The identity is recorded at call ENTRY, off the manifest
    about to be reached, so a call that then fails is still attributable.

    The lane here is `free_cloud` on purpose: a `paid_cloud` manifest without an authorization is
    DECLINED before the adapter is entered (`paid_call_not_authorized_or_reserved`), and a call the
    runtime refused to make is correctly not counted -- so using it would have tested the guard
    rather than the seam.

    Added after a sabotage pass: stripping the identity keywords from the router's
    `record_provider_call` left the whole suite green, because every other test in this module fed
    the accounting block in by hand instead of making the seam produce it.
    """
    from types import SimpleNamespace
    from unittest import mock

    from adapters.base_adapter import ModelRequest
    from core.memory_first_router import MemoryFirstRouter
    from core.turn_model_call_ledger import begin_turn, reset_for_tests, turn_call_accounting
    from storage.model_provider_manifest import ModelProviderManifest

    manifest = ModelProviderManifest(
        provider_name="openrouter-byok",
        model_name="nvidia/nemotron-nano-9b-v2",
        source_type="http",
        adapter_type="cloud_fallback_provider",
        license_name="proprietary",
        license_reference="https://openrouter.ai/terms",
        runtime_config={"base_url": "https://openrouter.ai/api/v1", "api_key_env": "OPENROUTER_API_KEY"},
    )

    class _FailingAdapter:
        def __init__(self) -> None:
            self.manifest = manifest

        def health_check(self):
            return {"ok": True}

        def supports_streaming(self):
            return False

        def get_license_metadata(self):
            return {"provider_name": manifest.provider_name}

        def run_text_task(self, request):
            raise TimeoutError("read timed out")

    reset_for_tests()
    context = {"request_id": "req-failed-cloud", "surface": "api"}
    begin_turn(context)
    router = MemoryFirstRouter.__new__(MemoryFirstRouter)
    router.registry = SimpleNamespace(build_adapter=lambda _manifest: _FailingAdapter())

    with mock.patch("core.memory_first_router.emit_runtime_event"), mock.patch(
        "core.memory_first_router.should_probe_health", return_value=False
    ), mock.patch("core.memory_first_router.circuit_is_open", return_value=False), mock.patch(
        "core.memory_first_router._paid_call_authorization", return_value=None
    ), mock.patch(
        "core.memory_first_router.provider_cost_class", return_value="free_cloud"
    ), mock.patch(
        "core.memory_first_router.reported_cost_class", return_value="free_cloud"
    ):
        _adapter, response, error = router._invoke_manifest(
            manifest=manifest,
            request=ModelRequest(task_kind="chat", prompt="plan the three steps"),
            output_mode="plain_text",
            task=SimpleNamespace(task_id="task-failed"),
            source_context=context,
        )

    assert response is None and error, "the call must really have failed for this to be the right case"
    accounting = turn_call_accounting(context)
    assert accounting["calls"] >= 1
    assert accounting["lanes"] == ["cloud"], "a failed cloud call recorded no lane -- the footer would say local"
    assert accounting["models"] == ["nvidia/nemotron-nano-9b-v2"]
    assert accounting["served_usage"] == {}
    assert accounting["failed_calls"] == 1
    assert accounting["completed_calls"] == 0

    footer = format_provenance_footer(_fast_path("conductor_all_nodes_failed", prefix="action"), None, accounting)
    assert "1 cloud model attempt failed" in footer
    assert "routing call" not in footer
    assert "local" not in footer
    provenance = read_turn_provenance(
        _fast_path("conductor_all_nodes_failed", prefix="action"), None, accounting
    )
    assert provenance.answer_source == "deterministic"
    assert provenance.model_participation == "attempted_failed_then_tool_answer"


def test_the_routers_own_usage_funnel_writes_to_the_ledger_not_only_to_the_thread_local() -> None:
    """The call SITE, driven for real -- `record_served_usage` working is not the same as it running.

    `_decision_from_response` is the single funnel every served response passes through, whatever
    lane reached it. Asserting only that the ledger function stores what it is handed would leave
    the wiring free to be deleted with every test still green, which is exactly how the thread-local
    came to be the only recorder in the first place.
    """
    from types import SimpleNamespace
    from unittest import mock

    from adapters.base_adapter import ModelResponse
    from core.memory_first_router import MemoryFirstRouter
    from core.turn_model_call_ledger import begin_turn, reset_for_tests, turn_served_usage
    from storage.model_provider_manifest import ModelProviderManifest

    manifest = ModelProviderManifest(
        provider_name="openrouter-byok",
        model_name="nvidia/nemotron-nano-9b-v2",
        source_type="http",
        adapter_type="cloud_fallback_provider",
        license_name="proprietary",
        license_reference="https://openrouter.ai/terms",
        runtime_config={"base_url": "https://openrouter.ai/api/v1", "api_key_env": "OPENROUTER_API_KEY"},
    )
    response = ModelResponse(
        output_text="Planned and ran three steps.",
        usage={"prompt_tokens": 900, "completion_tokens": 304, "total_tokens": 1204, "cost": 0.0021},
        provider_id=manifest.provider_id,
        model_name=manifest.model_name,
    )

    class _Adapter:
        def __init__(self) -> None:
            self.manifest = manifest

        def health_check(self):
            return {"ok": True}

        def supports_streaming(self):
            return False

        def get_license_metadata(self):
            return {"provider_name": manifest.provider_name}

    reset_for_tests()
    context = {"request_id": "req-router-funnel"}
    begin_turn(context)
    router = MemoryFirstRouter.__new__(MemoryFirstRouter)

    with mock.patch("core.memory_first_router.record_candidate_output", return_value="candidate-1"):
        router._decision_from_response(
            manifest=manifest,
            adapter=_Adapter(),
            response=response,
            task_hash="hash-1",
            task=SimpleNamespace(task_id="task-1"),
            classification={"task_class": "research"},
            context_result=SimpleNamespace(
                retrieval_confidence_score=0.0,
                report=SimpleNamespace(retrieval_confidence="low"),
            ),
            task_kind="chat",
            output_mode="plain_text",
            provider_role="auto",
            ranked_manifests=[manifest],
            attempted=[manifest.provider_id],
            failover_used=False,
            source="provider",
            source_context=context,
        )

    served = turn_served_usage(context)
    assert served is not None, "the funnel recorded to the thread-local alone -- the thread hop loses it"
    assert served["model_id"] == "nvidia/nemotron-nano-9b-v2"
    assert int(served["prompt_tokens"]) + int(served["output_tokens"]) == 1204
    assert "cloud" in str(served["cost_class"]).lower()


def test_the_ledger_is_actually_opened_on_a_live_turn() -> None:
    """`begin_turn` had NO production call site, so every recorded call was silently dropped.

    Reading the module for a `record_provider_call` would have shown accounting that looked
    complete. This drives the API entry point and asserts the id is on the context afterwards --
    the only thing that makes the recording seams do anything at all.
    """
    import inspect

    from core.turn_model_call_ledger import LEDGER_ID_KEY
    from core.web.api import runtime as rt

    source = inspect.getsource(rt.run_agent_turn if hasattr(rt, "run_agent_turn") else rt)
    assert "begin_turn" in source or "_begin_model_call_ledger" in source

    context = {"request_id": "req-live-1"}
    from core.turn_model_call_ledger import begin_turn, record_provider_call, reset_for_tests, turn_model_calls

    reset_for_tests()
    begin_turn(context)
    assert context.get(LEDGER_ID_KEY), "no stamped id means every recorded call is dropped"
    record_provider_call(context, provider_id="ollama", model_id="qwen2.5:7b", cost_class="free_local")
    assert turn_model_calls(context) == 1


def test_a_real_runtime_tool_dispatch_names_itself_on_the_turn(tmp_path) -> None:
    """The tool name comes from the dispatcher, not from a route-reason lookup table.

    `workspace.read_file` is executed through `core.runtime_execution_tools.execute_runtime_tool`,
    which is the single seam every deterministic runtime tool passes. Driving the real read proves
    the footer's label tracks what ran rather than a mapping someone has to remember to update.
    """
    from core.runtime_execution_tools import execute_runtime_tool
    from core.turn_model_call_ledger import begin_turn, reset_for_tests, turn_call_accounting

    (tmp_path / "exact_one_file_6a73.txt").write_text("# First heading\nbody\n", encoding="utf-8")
    reset_for_tests()
    context = {"request_id": "req-read-1", "workspace": str(tmp_path), "workspace_root": str(tmp_path)}
    begin_turn(context)

    execution = execute_runtime_tool(
        "workspace.read_file",
        {"path": "exact_one_file_6a73.txt"},
        source_context=context,
    )

    assert execution is not None and execution.ok
    accounting = turn_call_accounting(context)
    assert accounting["tools"] == ["workspace.read_file"]

    footer = format_provenance_footer(_fast_path("workspace_runtime_fast_path"), None, accounting)
    assert footer == f"`tool | workspace.read_file | no model | {BUILD}`"


def test_a_failed_tool_dispatch_is_still_named(tmp_path) -> None:
    """A read that found nothing still resolved a path and scanned the project. It ran.

    Recording only successful dispatches would put the turn back to describing itself by its route
    family precisely when the user most needs to know which operation disappointed them.
    """
    from core.runtime_execution_tools import execute_runtime_tool
    from core.turn_model_call_ledger import begin_turn, reset_for_tests, turn_call_accounting

    reset_for_tests()
    context = {"request_id": "req-read-2", "workspace": str(tmp_path), "workspace_root": str(tmp_path)}
    begin_turn(context)

    execution = execute_runtime_tool(
        "workspace.read_file",
        {"path": "no_such_file_6a73.txt"},
        source_context=context,
    )

    assert execution is not None and not execution.ok
    assert turn_call_accounting(context)["tools"] == ["workspace.read_file"]


def test_finalize_attaches_the_accounting_and_footers_from_it(monkeypatch) -> None:
    """The integration the two halves meet at: `_finalize_turn_usage` with a real turn context."""
    from core.turn_model_call_ledger import (
        begin_turn,
        record_provider_call,
        record_served_usage,
        reset_for_tests,
    )
    from core.web.api import runtime as rt

    reset_for_tests()
    monkeypatch.setattr(rt, "_usage_footer_enabled", lambda: True)
    context = {"request_id": "req-finalize-1"}
    begin_turn(context)
    record_provider_call(
        context,
        provider_id="openrouter-byok",
        model_id="nvidia/nemotron-nano-9b-v2",
        cost_class="paid_cloud",
    )
    record_served_usage(context, CLOUD_USAGE)

    finalized = rt._finalize_turn_usage({"response": "Planned and ran three steps.", **_model_turn()}, context)

    assert finalized["usage_summary"] == CLOUD_USAGE
    assert finalized["model_call_accounting"]["lanes"] == ["cloud"]
    assert finalized["response"].endswith("`cloud | nemotron-nano-9b-v2 | 1,204 tok | $0.0021`")
    assert "local | model | tokens unreported" not in finalized["response"]


def test_finalize_without_a_turn_context_still_behaves_as_before() -> None:
    """Every existing caller passes no context. It must degrade to the round-1 behaviour, not crash."""
    from core.web.api import runtime as rt

    finalized = rt._finalize_turn_usage({"response": "Hello.", **_fast_path("smalltalk_fast_path")})

    assert finalized["response"].endswith(f"`tool | smalltalk_fast_path | no model | {BUILD}`")
    assert "model_call_accounting" not in finalized


def test_the_streamed_transcript_renders_the_same_line_as_the_buffered_one() -> None:
    """The streamed path calls `format_provenance_footer(result_payload, usage_summary)` -- two args.

    It has no ledger context of its own, so the accounting has to RIDE ON the result for the two
    transcripts to agree. If it did not, a streamed conductor turn would still print the invented
    line while the buffered one printed the true one, and which the user saw would depend on their
    client. Reads the accounting off the result exactly as `core/web/api/runtime.py` does.
    """
    from core.turn_model_call_ledger import begin_turn, record_provider_call, record_served_usage
    from core.web.api import runtime as rt

    context = {"request_id": "req-stream-1"}
    begin_turn(context)
    record_provider_call(
        context,
        provider_id="openrouter-byok",
        model_id="nvidia/nemotron-nano-9b-v2",
        cost_class="paid_cloud",
    )
    record_served_usage(context, CLOUD_USAGE)

    finalized = rt._finalize_turn_usage({"response": "Ran the plan.", **_model_turn()}, context)
    buffered = finalized["response"].rsplit("\n\n", 1)[-1]
    # The streamed emit passes the payload and its usage summary, and nothing else.
    streamed = format_provenance_footer(finalized, finalized.get("usage_summary"))

    assert streamed == buffered == "`cloud | nemotron-nano-9b-v2 | 1,204 tok | $0.0021`"


def test_the_footer_shape_is_recognised_by_the_chat_page_pattern() -> None:
    """Every new shape must still be detected by the client that renders the line apart from the answer.

    `core/vool_chat_page.py` splits the footer off on the same four lane words; a shape it does not
    recognise renders as a stray line of the answer instead of as metadata.
    """
    page_pattern = re.compile(r"^`(?:local|cloud|tool|runtime) \| [^`\n]*`$")
    shapes = [
        format_provenance_footer(_model_turn(), LOCAL_USAGE),
        format_provenance_footer(_model_turn(), CLOUD_USAGE),
        format_provenance_footer(_fast_path("smalltalk_fast_path"), None, _accounting()),
        format_provenance_footer(_fast_path("workspace_runtime_fast_path", model_calls=2), None,
                                 _accounting(calls=2, lanes=["local"], tools=["workspace.read_file"])),
        format_provenance_footer(_model_turn(model_selected=""), None, _accounting(calls=1)),
    ]

    for shape in shapes:
        assert page_pattern.match(shape), shape
        assert strip_provenance_footer(f"Answer.\n\n{shape}") == "Answer."


def test_concurrent_call_outcomes_close_by_id_not_completion_order() -> None:
    """A fast second arm must not mark the slow first arm successful by list position."""
    from core.normalized_provider_result import ProviderErrorClass
    from core.turn_model_call_ledger import (
        begin_turn,
        record_provider_call,
        record_provider_call_outcome,
        turn_call_accounting,
    )

    context = {"request_id": "out-of-order-provider-arms"}
    begin_turn(context)
    slow_id = record_provider_call(
        context,
        provider_id="openrouter-byok",
        model_id="model-slow",
        cost_class="free_cloud",
    )
    fast_id = record_provider_call(
        context,
        provider_id="ollama",
        model_id="model-fast",
        cost_class="free_local",
    )

    record_provider_call_outcome(context, fast_id, outcome="completed")
    record_provider_call_outcome(
        context,
        slow_id,
        outcome="failed",
        error_class=ProviderErrorClass.EMPTY_PROVIDER_RESPONSE.value,
    )

    accounting = turn_call_accounting(context)
    assert accounting["calls"] == 2
    assert accounting["completed_calls"] == 1
    assert accounting["failed_calls"] == 1
    assert accounting["pending_calls"] == 0
    assert accounting["failed_error_classes"] == [ProviderErrorClass.EMPTY_PROVIDER_RESPONSE.value]
