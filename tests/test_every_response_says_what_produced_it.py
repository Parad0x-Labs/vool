"""Every assistant response exposes provenance -- including the ones that ran no model.

The live defect, 2026-08-11. One turn carried a footer::

    local | qwen2.5:7b | 930 tok

and the exact file write in the very next turn carried none::

    Files — workspace root
    | exact_one_file_6204.txt | 1 | written |

`core.web.api.runtime._format_usage_footer` returned "" whenever the token total was zero, and a
deterministic action runs no model, so its total is zero. Absence was doing double duty: "no
tokens" and "nothing to say". The user only ever saw the second reading, and could not tell whether
a model had been involved in the file write at all.

WHY THE MATRIX IS NOT `exact_one_file_6204`
-------------------------------------------
Fixing the one observed turn would be the hard-coded input->output mapping CLAUDE.md 0.2 bans. The
cases below are RESPONSE CLASSES, each built from the shape its own producer really returns
(`fast_path_result` and `action_fast_path_result` in `core/agent_runtime/fast_command_surface.py`,
the builder receipt in `core/agent_runtime/builder/controller.py`), plus the failure and
approval-pending variants, plus classes invented here that no producer in this repo emits today --
because a contract that only covers the shapes that exist is a contract that breaks on the next
lane.

The negative controls are the other half, and the sharpest of them is the one that matters most: a
deterministic answer must NEVER read as model-generated.
"""

from __future__ import annotations

import pytest

from core.app_version import VOOL_VERSION
from core.response_provenance import (
    append_provenance_footer,
    format_model_usage_footer,
    format_provenance_footer,
    read_turn_provenance,
    strip_provenance_footer,
)

LOCAL_USAGE = {
    "cost_class": "free_local",
    "model_id": "qwen2.5:7b",
    "prompt_tokens": 800,
    "output_tokens": 130,
}
PAID_CLOUD_USAGE = {
    "cost_class": "paid_cloud",
    "model_id": "anthropic/claude",
    "prompt_tokens": 2000,
    "output_tokens": 450,
    "usd_actual": 0.0123,
}


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


def _deterministic_turn(reason: str, *, prefix: str = "deterministic", **overrides) -> dict:
    """The shape `fast_path_result` / `action_fast_path_result` really return."""
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


# ---------------------------------------------------------------------------------------------
# The reported turn, and the one that already worked.
# ---------------------------------------------------------------------------------------------


def test_the_ordinary_chat_footer_is_unchanged() -> None:
    """The line the user already sees, byte for byte. A repair that improves the silent case by
    changing the working one is not a repair."""
    assert format_provenance_footer(_model_turn(), LOCAL_USAGE) == "`local | qwen2.5:7b | 930 tok`"


def test_the_exact_file_write_now_says_what_produced_it() -> None:
    result = _deterministic_turn(
        "exact_workspace_write",
        prefix="action",
        response="Files — workspace root\n\n| exact_one_file_6204.txt | 1 | written |",
        details={"builder_model_build": {"files_written": ["exact_one_file_6204.txt"]}},
    )

    footer = format_provenance_footer(result, None)

    assert footer == f"`tool | exact_workspace_write | 1 file | no model | build {VOOL_VERSION}`"


# ---------------------------------------------------------------------------------------------
# The response-class matrix. Every one gets a footer; none of them may claim a model wrongly.
# ---------------------------------------------------------------------------------------------

RESPONSE_CLASSES = [
    # (label, result, usage, model_expected)
    ("normal chat, local", _model_turn(), LOCAL_USAGE, True),
    ("normal chat, paid cloud", _model_turn(), PAID_CLOUD_USAGE, True),
    ("exact file write", _deterministic_turn("exact_workspace_write", prefix="action"), None, False),
    ("project file read", _deterministic_turn("workspace_read_fast_path"), None, False),
    ("deterministic fast path", _deterministic_turn("date_time_fast_path"), None, False),
    ("ui command", _deterministic_turn("ui_command_fast_path"), None, False),
    (
        "builder scaffold, model-backed",
        _deterministic_turn(
            "builder_model_build",
            prefix="action",
            model_calls=3,
            details={"builder_model_build": {"files_written": ["a.py", "b.py"], "generations": 3}},
        ),
        LOCAL_USAGE,
        True,
    ),
    (
        "builder scaffold, no model",
        _deterministic_turn(
            "builder_exact_write",
            prefix="action",
            details={"builder_model_build": {"files_written": ["a.txt"]}},
        ),
        None,
        False,
    ),
    (
        "failed action",
        _deterministic_turn("channel_action", prefix="action", mode="tool_failed", response="That failed."),
        None,
        False,
    ),
    (
        "approval pending",
        _deterministic_turn("approval_required", prefix="action", mode="awaiting_approval"),
        None,
        False,
    ),
    ("table-bearing response", _deterministic_turn("workspace_read_fast_path", prefix="action"), None, False),
    # a class no producer in this repo emits today: it records that no model ran and names no lane
    ("recorded, lane unnamed", {"response": "hi", "model_execution": {"used_model": False}}, None, False),
]


@pytest.mark.parametrize(
    ("label", "result", "usage", "model_expected"),
    RESPONSE_CLASSES,
    ids=[case[0] for case in RESPONSE_CLASSES],
)
def test_every_response_class_gets_exactly_one_footer(
    label: str, result: dict, usage: dict | None, model_expected: bool
) -> None:
    footer = format_provenance_footer(result, usage)

    assert footer.startswith("`") and footer.endswith("`"), f"{label}: {footer!r}"
    assert footer.count("\n") == 0, f"{label}: the footer must be one line -- {footer!r}"
    assert footer.strip("`").strip(), f"{label}: empty footer"


@pytest.mark.parametrize(
    ("label", "result", "usage", "model_expected"),
    RESPONSE_CLASSES,
    ids=[case[0] for case in RESPONSE_CLASSES],
)
def test_no_response_class_claims_a_model_that_did_not_run(
    label: str, result: dict, usage: dict | None, model_expected: bool
) -> None:
    """The one thing worse than the missing footer this replaces: a footer naming a model on a turn
    where none ran. That is a false claim, not a gap."""
    footer = format_provenance_footer(result, usage)
    provenance = read_turn_provenance(result, usage)

    assert provenance.model_ran is model_expected, f"{label}: {footer!r}"
    if model_expected:
        assert "no model" not in footer, f"{label}: {footer!r}"
    else:
        assert "no model" in footer, f"{label}: {footer!r}"
        assert " tok" not in footer, f"{label}: a modelless turn reported tokens -- {footer!r}"
        assert "qwen" not in footer and "claude" not in footer, f"{label}: {footer!r}"


@pytest.mark.parametrize(
    ("label", "result", "usage", "model_expected"),
    RESPONSE_CLASSES,
    ids=[case[0] for case in RESPONSE_CLASSES],
)
def test_no_response_class_leaks_a_path_or_an_argument(
    label: str, result: dict, usage: dict | None, model_expected: bool
) -> None:
    """The footer reports a COUNT, never a name. Widening what a receipt may show is the receipt
    policy's decision, and a footer is not the place to make it."""
    footer = format_provenance_footer(result, usage)

    for leaked in ("exact_one_file_6204.txt", "a.py", "b.py", "a.txt", "/", "\\"):
        assert leaked not in footer, f"{label}: {footer!r} leaked {leaked!r}"


def test_a_deterministic_turn_names_the_lane_that_served_it() -> None:
    """Two different fast paths must not render the same footer -- otherwise the line says a tool
    ran and nothing more, which is the same non-answer in a longer form."""
    clock = format_provenance_footer(_deterministic_turn("date_time_fast_path"), None)
    write = format_provenance_footer(_deterministic_turn("exact_workspace_write", prefix="action"), None)

    assert clock != write
    assert "date_time_fast_path" in clock
    assert "exact_workspace_write" in write


def test_the_action_count_is_read_from_the_receipt_not_guessed() -> None:
    one = format_provenance_footer(
        _deterministic_turn("b", prefix="action", details={"x": {"files_written": ["a"]}}), None
    )
    two = format_provenance_footer(
        _deterministic_turn("b", prefix="action", details={"x": {"files_written": ["a", "c"]}}), None
    )
    none = format_provenance_footer(_deterministic_turn("b", prefix="action", details={"x": {}}), None)

    assert "1 file |" in one
    assert "2 files |" in two
    assert "file" not in none


# ---------------------------------------------------------------------------------------------
# Not twice. Not on nothing.
# ---------------------------------------------------------------------------------------------


def test_appending_twice_leaves_one_footer() -> None:
    """The append path runs more than once on some routes. A plain "is it already there?" test only
    catches an IDENTICAL line, so a differently-shaped second footer used to stack under the first."""
    result = _deterministic_turn("exact_workspace_write", prefix="action")
    body = "Files — workspace root\n\n| exact_one_file_6204.txt | 1 | written |"

    once = append_provenance_footer(body, result, None)
    twice = append_provenance_footer(once, result, None)

    assert once == twice
    assert twice.count("no model") == 1


def test_a_reshaped_footer_replaces_the_old_one_rather_than_stacking() -> None:
    model_first = append_provenance_footer("Answer.", _model_turn(), LOCAL_USAGE)
    then_deterministic = append_provenance_footer(
        model_first, _deterministic_turn("date_time_fast_path"), None
    )

    assert then_deterministic.count("`") == 2
    assert "qwen2.5:7b" not in then_deterministic


def test_a_result_that_records_nothing_is_not_described() -> None:
    """The honesty clause, and the one case where silence is right. A result with no usage, no
    `model_execution`, no `model_calls` and no route is a result the runtime cannot describe --
    `runtime | no model` on it would be an ASSERTION that no model ran, and
    `tests/test_task_event_stream.py::test_clean_prose_streams_through_unchanged` streams a MODEL's
    prose through exactly that shape. Found by running the suite, not by reasoning about it."""
    assert format_provenance_footer({"response": "Paris is the capital of France."}, None) == ""
    assert append_provenance_footer("Paris is the capital of France.", {"response": "x"}, None) == (
        "Paris is the capital of France."
    )
    # but a result that records ANYTHING is described
    assert format_provenance_footer({"model_execution": {"used_model": False}}, None) != ""
    assert format_provenance_footer({"model_calls": 0}, None) != ""
    assert format_provenance_footer({"route_reason": "date_time_fast_path"}, None) != ""


def test_an_empty_response_gets_no_footer() -> None:
    """A footer under an empty bubble is a bubble that exists only to carry metadata."""
    assert append_provenance_footer("", _model_turn(), LOCAL_USAGE) == ""
    assert append_provenance_footer("   ", _model_turn(), LOCAL_USAGE) == "   "


def test_strip_removes_a_footer_of_any_lane() -> None:
    for lane_line in (
        "`local | qwen2.5:7b | 930 tok`",
        "`cloud | claude | 2,450 tok | $0.0123`",
        "`tool | date_time_fast_path | no model | build 0.5.0`",
        "`runtime | no model | build 0.5.0`",
    ):
        assert strip_provenance_footer("Body.\n\n" + lane_line) == "Body."
    # a code span that is not a footer must survive
    assert strip_provenance_footer("Run `ls -la` now.") == "Run `ls -la` now."


# ---------------------------------------------------------------------------------------------
# The pre-existing usage-only formatter keeps its own contract.
# ---------------------------------------------------------------------------------------------


def test_the_usage_only_formatter_is_still_empty_when_unmetered() -> None:
    """`_format_usage_footer` answers "what did the model lane cost", and "" is its right answer for
    a turn with no metered tokens. Only the PROVENANCE footer is unconditional."""
    assert format_model_usage_footer({"prompt_tokens": 0, "output_tokens": 0}) == ""
    assert format_model_usage_footer(None) == ""
    assert format_model_usage_footer(LOCAL_USAGE) == "`local | qwen2.5:7b | 930 tok`"


def test_the_api_usage_footer_and_the_provenance_footer_render_the_model_line_identically() -> None:
    """One rendering, two callers. Two copies of the format string is how the streamed transcript
    and the buffered one would disagree after the first edit to either."""
    from core.web.api.runtime import _format_usage_footer

    assert _format_usage_footer(LOCAL_USAGE) == format_provenance_footer(_model_turn(), LOCAL_USAGE)
    assert _format_usage_footer(PAID_CLOUD_USAGE) == format_provenance_footer(
        _model_turn(), PAID_CLOUD_USAGE
    )


# ---------------------------------------------------------------------------------------------
# The API seam: buffered and streamed transcripts carry the same line.
# ---------------------------------------------------------------------------------------------


def test_the_buffered_finalizer_footers_a_modelless_action_result(monkeypatch: pytest.MonkeyPatch) -> None:
    """The real `_finalize_turn_usage`, with the real "no model ran this turn" state."""
    import core.memory_first_router as router
    from core.web.api.runtime import _finalize_turn_usage

    monkeypatch.setattr(router, "get_turn_usage", lambda: None)
    result = _deterministic_turn(
        "exact_workspace_write",
        prefix="action",
        response="Files — workspace root\n\n| exact_one_file_6204.txt | 1 | written |",
        details={"builder_model_build": {"files_written": ["exact_one_file_6204.txt"]}},
    )

    finalized = _finalize_turn_usage(dict(result))

    assert finalized["response"].endswith(
        f"`tool | exact_workspace_write | 1 file | no model | build {VOOL_VERSION}`"
    )
    assert "| exact_one_file_6204.txt | 1 | written |" in finalized["response"]


def test_the_buffered_finalizer_is_unchanged_for_a_model_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    import core.memory_first_router as router
    from core.web.api.runtime import _finalize_turn_usage

    monkeypatch.setattr(router, "get_turn_usage", lambda: dict(LOCAL_USAGE))
    finalized = _finalize_turn_usage(_model_turn(response="Here is the answer."))

    assert finalized["response"] == "Here is the answer.\n\n`local | qwen2.5:7b | 930 tok`"
    assert finalized["usage_summary"] == LOCAL_USAGE


def test_the_footer_can_still_be_turned_off(monkeypatch: pytest.MonkeyPatch) -> None:
    import core.memory_first_router as router
    from core.web.api.runtime import _finalize_turn_usage

    monkeypatch.setenv("VOOL_SHOW_USAGE_FOOTER", "0")
    monkeypatch.setattr(router, "get_turn_usage", lambda: None)
    finalized = _finalize_turn_usage(_deterministic_turn("date_time_fast_path", response="It is noon."))

    assert finalized["response"] == "It is noon."
    assert finalized["answer_provenance"]["answer_source"] == "deterministic"
    assert finalized["answer_provenance"]["model_participation"] == "none"


def test_backend_metadata_distinguishes_failed_model_then_tool_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import core.memory_first_router as router
    from core.normalized_provider_result import ProviderErrorClass
    from core.turn_model_call_ledger import (
        begin_turn,
        record_provider_call,
        record_provider_call_outcome,
        record_tool_execution,
        reset_for_tests,
    )
    from core.web.api.runtime import _finalize_turn_usage

    monkeypatch.setattr(router, "get_turn_usage", lambda: None)
    reset_for_tests()
    context = {"request_id": "failed-model-then-read"}
    begin_turn(context)
    call_id = record_provider_call(
        context,
        provider_id="openrouter-byok",
        model_id="nvidia/nemotron:free",
        cost_class="free_cloud",
    )
    record_provider_call_outcome(
        context,
        call_id,
        outcome="failed",
        error_class=ProviderErrorClass.EMPTY_PROVIDER_RESPONSE.value,
    )
    record_tool_execution(context, "workspace.read_file")

    finalized = _finalize_turn_usage(
        _deterministic_turn("workspace_runtime_fast_path", response="The file contains 42."),
        context,
    )

    provenance = finalized["answer_provenance"]
    assert provenance["answer_source"] == "tool"
    assert provenance["model_participation"] == "attempted_failed_then_tool_answer"
    assert provenance["failed_model_calls"] == 1
    assert provenance["participating_models"] == ["nvidia/nemotron:free"]
    assert "model attempt failed" in finalized["response"]
