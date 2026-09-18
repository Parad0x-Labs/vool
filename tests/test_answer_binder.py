"""The answer binder: does the reply only claim what the tools produced?

Anchored on the real failure of 2026-07-28. `~/Desktop/vool-w5x1` held exactly `iota.rb`,
`tau.sql`, `upsilon.md`; `machine.list_directory` ran and returned all three; the answer described
contents that do not exist. That shape defeats the checks already in the runtime — it names the
right folder and no wrong filename, so hunting for invented names finds nothing, and it shares
vocabulary with the request, so a bag-of-words overlap test passes it.

Half of this file is false-positive guards, and that is proportionate. A binder that blocks correct
answers is worse than the fabrication it prevents, so every way a legitimate reply could look
suspicious gets a test: the user naming a file themselves, a parent directory, a general summary,
an empty folder, prose that merely contains a dot.
"""
from __future__ import annotations

import pytest

from core import answer_binder, execution_records
from core.runtime_tool_contracts import ToolClaim

LIST_CLAIM = ToolClaim(target_argument="path", resolved_target_key="path", result_items_key="entries")
WRITE_CLAIM = ToolClaim(target_argument="path", resolved_target_key="path", asserts_action=True)

REAL_OBSERVATION = {
    "ok": True,
    "status": "executed",
    "path": "~/Desktop/vool-w5x1",
    "count": 3,
    "entries": [
        {"name": "iota.rb", "type": "file"},
        {"name": "tau.sql", "type": "file"},
        {"name": "upsilon.md", "type": "file"},
    ],
}

# The exact answer the product produced. Kept verbatim: paraphrasing it would lose the property
# that makes it hard — it is fluent, on-topic, and names nothing checkably wrong.
FABRICATION = (
    "The directory ~/Desktop/vool-w5x1 contains a collection of files and folders related to "
    "VOOL and OpenClaw runtime behavior. These include configuration files and runtime logs."
)
REQUEST = "give me a rundown of what lives in ~/Desktop/vool-w5x1"


@pytest.fixture(autouse=True)
def _clean():
    execution_records.clear()
    yield
    execution_records.clear()


def _listed(session: str = "s", observation=None) -> None:
    execution_records.record(
        session_id=session,
        intent="machine.list_directory",
        arguments={"path": "~/Desktop/vool-w5x1"},
        observation=observation if observation is not None else REAL_OBSERVATION,
        claim=LIST_CLAIM,
    )


def _check(answer: str, *, session: str = "s", request: str = REQUEST):
    return answer_binder.check(answer, session_id=session, user_input=request)


# --------------------------------------------------------------------------------------
# The failure this exists for
# --------------------------------------------------------------------------------------


def test_the_measured_fabrication_is_caught() -> None:
    _listed()
    result = _check(FABRICATION)
    assert result.ok is False
    assert {i.kind for i in result.issues} == {"uncited_contents"}


def test_the_correct_answer_passes() -> None:
    _listed()
    assert _check("Visible entries under ~/Desktop/vool-w5x1: iota.rb, tau.sql and upsilon.md.").ok


def test_invented_filenames_are_caught() -> None:
    _listed()
    result = _check("The folder ~/Desktop/vool-w5x1 contains index.html and styles.css.")
    kinds = {i.kind for i in result.issues}
    assert "unbound_item" in kinds


def test_a_folder_no_tool_touched_is_caught() -> None:
    _listed()
    result = _check("The directory ~/Desktop/some-other-folder contains iota.rb.")
    assert "unbound_target" in {i.kind for i in result.issues}


def test_naming_only_some_returned_items_is_fine() -> None:
    """Quoting one real name is grounding; the answer need not be exhaustive."""

    _listed()
    assert _check("~/Desktop/vool-w5x1 contains iota.rb among other files.").ok


# --------------------------------------------------------------------------------------
# False-positive guards. Each of these is a correct answer that must not be flagged.
# --------------------------------------------------------------------------------------


def test_a_file_the_user_named_is_not_an_invented_name() -> None:
    """Without this, "did you find notes.md?" makes every reply suspect."""

    _listed()
    result = answer_binder.check(
        "I did not find notes.md in ~/Desktop/vool-w5x1; it holds iota.rb, tau.sql and upsilon.md.",
        session_id="s",
        user_input="is notes.md in ~/Desktop/vool-w5x1?",
    )
    assert result.ok, [i.detail for i in result.issues]


def test_a_parent_directory_is_not_an_unbound_target() -> None:
    _listed()
    assert _check("Under ~/Desktop, the folder vool-w5x1 holds iota.rb.").ok


def test_a_general_summary_without_content_claims_passes() -> None:
    """A reply that does not assert what is inside cannot be claiming something false."""

    _listed()
    assert _check("I had a look at that folder for you.").ok


def test_prose_containing_dots_is_not_read_as_filenames() -> None:
    _listed()
    assert _check(
        "~/Desktop/vool-w5x1 has iota.rb etc. That is roughly 3.5 files, i.e. a small folder."
    ).ok


def test_an_empty_folder_answer_passes() -> None:
    """The observation builder drops empty values, so there is no `entries` key at all here."""

    _listed(observation={"ok": True, "status": "executed", "path": "~/Desktop/vool-w5x1", "count": 0})
    assert _check("~/Desktop/vool-w5x1 contains no files.").ok


def test_a_turn_with_no_tool_calls_is_not_checked() -> None:
    """Fails open. An ordinary conversation has nothing to bind against."""

    result = _check("The capital of France is Paris.")
    assert result.ok is True and result.checked is False


def test_an_empty_answer_is_not_checked() -> None:
    _listed()
    assert _check("   ").checked is False


def test_a_tool_with_no_declared_claim_does_not_trigger_checks() -> None:
    execution_records.record(session_id="s", intent="some.tool", arguments={"a": 1})
    assert _check("Anything at all about /wherever/you/like.").ok


# --------------------------------------------------------------------------------------
# Shape and reporting
# --------------------------------------------------------------------------------------


def test_the_result_serialises_for_logging() -> None:
    _listed()
    payload = _check(FABRICATION).as_dict()
    assert payload["ok"] is False and payload["checked"] is True
    assert payload["issues"] and payload["item_count"] == 3
    assert payload["targets"] == ["~/Desktop/vool-w5x1"]


def test_the_deterministic_fallback_renders_what_the_tools_found() -> None:
    """Used when a regenerated answer still fails: a plain listing beats a confident invention."""

    _listed()
    rendered = answer_binder.deterministic_rendering("s")
    assert "~/Desktop/vool-w5x1" in rendered
    for name in ("iota.rb", "tau.sql", "upsilon.md"):
        assert name in rendered


def test_records_from_another_session_are_not_used() -> None:
    _listed(session="other")
    assert _check(FABRICATION, session="s").checked is False


def test_an_absolute_answer_path_matches_a_home_relative_target() -> None:
    """Tools label targets `~/Desktop/x`; an answer may spell the same place absolutely."""

    _listed()
    assert _check("/Users/anyone/Desktop/vool-w5x1 contains iota.rb.").ok


# --------------------------------------------------------------------------------------
# The seam. `enforce_final_action_honesty` is on every return path a turn has, so wiring the
# check here is what covers the front-door fast paths as well as the model lane.
# --------------------------------------------------------------------------------------


def _enforce(answer: str, **flags):
    import contextlib

    from core import runtime_flags
    from core.agent_runtime.action_honesty_validator import enforce_final_action_honesty

    _listed()
    with contextlib.ExitStack() as stack:
        for name, value in flags.items():
            stack.enter_context(runtime_flags.override(name, value))
        return enforce_final_action_honesty(
            {"response": answer, "confidence": 0.9}, user_input=REQUEST, session_id="s"
        )


def test_with_both_flags_off_the_seam_is_untouched() -> None:
    """The kill switch. Shadow ships ON as of 2026-07-28, so both states are covered."""

    result = _enforce(FABRICATION, answer_binder_shadow=False, answer_binder_enforce=False)
    assert result["response"] == FABRICATION
    assert "answer_binding" not in result


def test_shadow_is_on_by_default_so_verdicts_are_recorded() -> None:
    result = _enforce(FABRICATION)
    assert result["answer_binding"]["ok"] is False
    assert result["response"] == FABRICATION, "shadow must still not change the answer"


def test_shadow_mode_records_a_verdict_without_acting() -> None:
    result = _enforce(FABRICATION, answer_binder_shadow=True)
    assert result["response"] == FABRICATION, "shadow mode must not change the answer"
    assert result["answer_binding"]["ok"] is False
    assert [i["kind"] for i in result["answer_binding"]["issues"]] == ["uncited_contents"]


def test_enforcing_replaces_a_fabrication_with_what_the_tools_returned() -> None:
    result = _enforce(FABRICATION, answer_binder_shadow=True, answer_binder_enforce=True)
    assert result["route_reason"] == "answer_binding_regrounded"
    for name in ("iota.rb", "tau.sql", "upsilon.md"):
        assert name in result["response"]
    assert result["confidence"] <= 0.6


def test_enforcing_leaves_a_grounded_answer_alone() -> None:
    good = "Visible entries under ~/Desktop/vool-w5x1: iota.rb, tau.sql and upsilon.md."
    result = _enforce(good, answer_binder_shadow=True, answer_binder_enforce=True)
    assert result["response"] == good
    assert result.get("route_reason") != "answer_binding_regrounded"


def test_a_binder_failure_never_breaks_the_turn() -> None:
    """A verification layer that can break an answer is worse than the bug it catches."""

    import contextlib
    from unittest import mock

    from core import runtime_flags
    from core.agent_runtime.action_honesty_validator import enforce_final_action_honesty

    _listed()
    with contextlib.ExitStack() as stack:
        stack.enter_context(runtime_flags.override("answer_binder_shadow", True))
        stack.enter_context(mock.patch("core.answer_binder.check", side_effect=RuntimeError("boom")))
        result = enforce_final_action_honesty(
            {"response": FABRICATION, "confidence": 0.9}, user_input=REQUEST, session_id="s"
        )
    assert result["response"] == FABRICATION


# --------------------------------------------------------------------------------------
# A tool call the runtime could not recognise must never be rendered as the answer.
# Observed live 2026-07-28 on a free cloud model: asked to audit a file, it replied with the
# literal text {"action": "read", "path": "config.py"} and the user saw it. Refusing to
# DISPATCH an unknown shape is not the same as refusing to SHOW it.
# --------------------------------------------------------------------------------------


def _guard(text: str):
    from core.agent_runtime.action_honesty_validator import _suppress_leaked_tool_call

    return _suppress_leaked_tool_call({"response": text, "confidence": 0.9})


@pytest.mark.parametrize(
    "leaked",
    [
        '{"action": "read", "path": "config.py"}',
        '{"intent": "machine.list_directory", "arguments": {"path": "/x"}}',
        '{"tool": "bash", "cmd": "ls"}',
        '{"function": "read_file", "path": "a.py"}',
    ],
)
def test_a_leaked_tool_call_is_replaced(leaked: str) -> None:
    guarded = _guard(leaked)
    assert guarded["route_reason"] == "leaked_tool_call_suppressed"
    assert leaked not in guarded["response"]
    assert guarded["confidence"] <= 0.3


@pytest.mark.parametrize(
    "answer",
    [
        'The config uses {"debug": true} which is fine.',
        '{"total": 42, "files": 3}',
        "Visible entries under ~/Desktop: a.md",
        "",
    ],
)
def test_a_real_answer_is_untouched(answer: str) -> None:
    """Prose that quotes JSON, and plain data without a tool-call key, are real answers."""

    assert _guard(answer).get("route_reason") != "leaked_tool_call_suppressed"
