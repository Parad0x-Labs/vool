"""A go-ahead resumes the pending turn only when it asks for nothing of its own.

Measured 2026-09-15 at cbe05fa3, served through VoolAgent.run_once with the synthetic Notes runner: after 'delete my
Apple note "Groceries"' asked for confirmation, the follow-up 'go ahead and delete my Apple note "Groceries"' was a proceed
message (`core.agent_runtime.proceed_intent_support`), `core.agent_runtime.checkpoints.prepare_runtime_checkpoint` resumed
the question, the stored request replaced the message, and the delete was asked again. 'delete my apple note titled Go
ahead list' read "go ahead" out of its unquoted title and was dropped the same way.

These are seam tests: `prepare_runtime_checkpoint` with the real proceed reader over a pending checkpoint, the two readers
it asks, and the model-driven resume of an interrupted tool turn. The served Notes effects live in
tests/pa_beta_gate/test_served_follow_up_own_request.py.
"""
from __future__ import annotations

import re
from types import SimpleNamespace
from unittest import mock

import pytest

from core.agent_runtime.checkpoints import prepare_runtime_checkpoint
from core.agent_runtime.proceed_intent_support import ProceedIntentSupportMixin
from core.agent_runtime.request_authority import REQUEST_PROVENANCE_KEY, request_provenance_for_visible_user_text

# The two readers the seam asks are imported where they are read, so every decision test here also runs, and fails for
# the behaviour it names, against a tree that predates them.

_STORED = 'delete my Apple note "Groceries"'
_SESSION = "follow-up-own-request"


class _Reader(ProceedIntentSupportMixin):
    """The agent's own proceed and resume readers, nothing else."""


def _pending(*, trusted: bool = True) -> dict:
    context = {REQUEST_PROVENANCE_KEY: request_provenance_for_visible_user_text(_STORED, session_id=_SESSION)} if trusted else {}
    return {"checkpoint_id": "cp-pending", "session_id": _SESSION, "status": "pending_approval", "request_text": _STORED,
            "source_context": context}


def _prepare(text: str, *, trusted: bool = True) -> dict:
    pending = _pending(trusted=trusted)
    return prepare_runtime_checkpoint(
        _Reader(),
        session_id=_SESSION,
        raw_user_input=text,
        effective_input=text,
        source_context={},
        latest_resumable_checkpoint_fn=lambda _sid: dict(pending),
        resume_runtime_checkpoint_fn=lambda _cid, **_kw: dict(pending),
        create_runtime_checkpoint_fn=lambda **_kw: {"checkpoint_id": "cp-new"},
        latest_failed_checkpoint_fn=lambda _sid: None,
    )


def _assert_runs_as_its_own_request(text: str) -> None:
    bundle = _prepare(text)
    assert bundle["state"] == "created", (text, bundle["state"])
    assert bundle["effective_input"] == text, (text, bundle["effective_input"])
    assert not bundle["source_context"].get("runtime_checkpoint_resumed"), text


def _assert_resumes(text: str) -> None:
    bundle = _prepare(text)
    assert bundle["state"] == "resumed", (text, bundle["state"])
    assert bundle["effective_input"] == _STORED, (text, bundle["effective_input"])
    assert bundle["source_context"].get("runtime_checkpoint_resumed") is True, text


# ------------------------------------------------------------------------------------ the decision at the seam

# (case, follow-up): a go-ahead that carries a request of its own. Every one is a proceed message at cbe05fa3 and resumed.
_OWN_REQUESTS = [
    ("reported", 'go ahead and delete my Apple note "Groceries"'),
    ("paraphrase-proceed-to", 'proceed to delete my Apple note "Groceries"'),
    ("paraphrase-please-remove-for-me", 'please go ahead and remove the Apple note "Groceries" for me'),
    ("paraphrase-i-want-you-to", 'I want you to delete my Apple note "Groceries", go ahead'),
    ("paraphrase-question-then-go-ahead", 'can you delete my Apple note "Groceries"? go ahead'),
    ("paraphrase-ok-do-it-dash", 'ok do it - delete my Apple note "Groceries"'),
    ("sloppy-no-conjunction", 'go ahead delete apple note "Groceries"'),
    ("sloppy-shouted", 'GO AHEAD AND DELETE MY APPLE NOTE "GROCERIES"'),
    ("sloppy-n-curly-quotes-pls", "go ahead n delete my apple note “Groceries” pls"),
    ("sloppy-trailing-do-it-no-comma", 'delete apple note "Groceries" do it'),
    ("sloppy-again", 'go ahead and delete my apple note "Groceries" again'),
    ("title-holds-the-go-ahead", 'delete my Apple note "Do it later", do it'),
    ("another-note", 'go ahead and delete my Apple note "Eyes on Q3"'),
    ("another-kind-rename", 'go ahead and rename my Apple note "Plan" to "Plan v2"'),
    ("another-kind-append", 'continue and append to my Apple note "Yes list" with "call the venue"'),
    ("another-lane-agenda", "go ahead and show my agenda for tomorrow"),
    ("another-lane-search", "yes, go ahead and search the workspace for tool_intent"),
    ("another-lane-arithmetic", "go ahead and calculate 17 times 23"),
    ("verb-no-list-names-schedule", 'go ahead and schedule a meeting "Ops review" tomorrow at 10'),
    ("quoted-value-holds-the-go-ahead", 'go ahead and open my Apple note "Continue"'),
]


@pytest.mark.parametrize(("case", "text"), _OWN_REQUESTS, ids=[row[0] for row in _OWN_REQUESTS])
def test_a_go_ahead_that_brings_its_own_request_runs_as_that_request(case, text):
    assert _Reader()._is_proceed_message(text) is True, (case, "the family must stay proceed messages")
    _assert_runs_as_its_own_request(text)


# (case, follow-up): a go-ahead that points at the pending work -- bare, with an adjunct, or with an object that only
# points back. These resume, as they did at cbe05fa3.
_GO_AHEADS = [
    ("bare-yes", "yes"),
    ("bare-go-ahead", "go ahead"),
    ("bare-continue", "continue"),
    ("ok-do-it", "ok do it"),
    ("carry-on-shouted-marks", "carry on!!"),
    ("please-just-do-it-now", "please just do it now"),
    ("yes-comma-go-ahead", "yes, go ahead"),
    ("go-ahead-with-a-plan", "go ahead with the Q3 plan"),
    ("carry-on-in-a-folder", "carry on in the Work folder"),
    ("proceed-with-next-steps", "proceed with next steps"),
    ("continue-where-you-stopped", "continue where you stopped"),
    ("continue-the-research-object", "continue the research on oil prices"),
    ("delete-it", "go ahead and delete it"),
    ("delete-it-again", "go ahead and delete it again"),
    ("send-it", "go ahead and send it"),
    ("proceed-with-the-delete", "proceed with the delete"),
    ("go-ahead-and-finish", "go ahead and finish"),
    ("hive-research-markers", "please do research and deliver it to the hive"),
    ("start-working", "start working"),
]


@pytest.mark.parametrize(("case", "text"), _GO_AHEADS, ids=[row[0] for row in _GO_AHEADS])
def test_a_go_ahead_that_asks_for_nothing_of_its_own_resumes_the_pending_turn(case, text):
    _assert_resumes(text)


@pytest.mark.parametrize("text", ["resume", "keep going", "try again", "pick up where you left off"])
def test_an_explicit_resume_still_resumes(text):
    _assert_resumes(text)


# (case, follow-up): the request's own words negate. Approval is not polarity-aware (a strict xfail in
# tests/test_operator_approval_words.py), so a negating follow-up must never become a fresh, approved request.
_NEGATED = [
    ("no-dont-go-ahead", 'no, don\'t go ahead and delete my Apple note "Groceries"'),
    ("go-ahead-but-dont", 'go ahead but don\'t delete my Apple note "Groceries"'),
    ("reported-actually-dont-do-it", 'delete my Apple note "Groceries", actually don\'t do it'),
    ("not-now", 'not now, go ahead and delete my Apple note "Groceries"'),
    ("i-dont-want-you-to", 'I don\'t want you to delete my Apple note "Groceries", go ahead'),
    ("never-proceed", 'never proceed to delete my Apple note "Groceries"'),
]


@pytest.mark.parametrize(("case", "text"), _NEGATED, ids=[row[0] for row in _NEGATED])
def test_a_follow_up_that_negates_is_never_run_as_its_own_request(case, text):
    from core.agent_runtime.proceed_intent_support import proceed_carries_its_own_request

    assert _Reader()._is_proceed_message(text) is True, case
    assert proceed_carries_its_own_request(text) is False, case
    _assert_resumes(text)


# (case, follow-up, the value): a go-ahead word inside a value the parser reads from outside quotes is not a go-ahead.
_UNQUOTED_VALUES = [
    ("reported-titled", "delete my apple note titled Go ahead list", "Go ahead list"),
    ("named", "remove apple note named Do it later", "Do it later"),
    ("called", "delete my apple note called Carry on", "Carry on"),
    ("read-titled", "open my apple note titled Continue reading list", "Continue reading list"),
]


@pytest.mark.parametrize(("case", "text", "value"), _UNQUOTED_VALUES, ids=[row[0] for row in _UNQUOTED_VALUES])
def test_a_go_ahead_word_in_an_unquoted_title_is_not_a_go_ahead(case, text, value):
    from core.operator.parser import unquoted_request_values

    assert value in unquoted_request_values(text), (case, unquoted_request_values(text))
    assert _Reader()._is_proceed_message(text) is False, case
    _assert_runs_as_its_own_request(text)


def test_an_untrusted_checkpoint_never_swallows_a_follow_up_that_brings_its_own_request():
    # A checkpoint without request provenance is refused as a resume; a bare go-ahead is refused with nothing to run,
    # while a follow-up with its own request is a fresh turn and runs its own words.
    refused = _prepare("go ahead", trusted=False)
    assert refused["state"] == "rejected_resume" and refused["effective_input"] == "", refused["state"]
    fresh = _prepare('go ahead and delete my Apple note "Groceries"', trusted=False)
    assert fresh["state"] == "created" and fresh["effective_input"] == 'go ahead and delete my Apple note "Groceries"', fresh


def test_repeating_the_stored_request_word_for_word_still_resumes():
    _assert_resumes(_STORED)


# ------------------------------------------------------------------------------------------ the request reader

def _spans(text: str, *phrases: str) -> tuple[tuple[int, int], ...]:
    return tuple(match.span() for phrase in phrases for match in re.finditer(re.escape(phrase), text))


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # a lane reads it, but a noun phrase names what to continue, not work of its own
        ("the research on oil prices", False),
        ("research oil prices", True),
        # an embedded wh-clause asks nothing; a question does
        ("where you stopped", False),
        ('where is my Apple note "Plan"?', True),
        # an infinitive keeps its verb; a prepositional phrase has none
        ('to delete my Apple note "Plan"', True),
        ("to the next step", False),
        # an operand of its own, or only words that point back
        ('delete my Apple note "Plan"', True),
        ("delete it", False),
        ("send that again", False),
        ('open "Continue"', True),
        # a prohibition asks for nothing
        ('don\'t delete my Apple note "Plan"', False),
        # a verb no list names, read by its lane
        ('schedule a meeting "Ops review" tomorrow at 10', True),
        ("with the Q3 plan", False),
    ],
)
def test_asks_for_work_of_its_own_reads_openers_and_operands(text, expected):
    from core.agent_runtime.answer_coverage import asks_for_work_of_its_own

    assert asks_for_work_of_its_own(text) is expected, text


def test_set_aside_words_are_not_read():
    from core.agent_runtime.answer_coverage import asks_for_work_of_its_own

    text = "go ahead and delete it"
    assert asks_for_work_of_its_own(text) is False
    own = 'go ahead and rename my Apple note "Plan" to "Plan v2"'
    assert asks_for_work_of_its_own(own, set_aside=_spans(own, "go ahead")) is True
    # Setting aside the request's own verb leaves nothing to run: the reader reads only what is left.
    assert asks_for_work_of_its_own(own, set_aside=_spans(own, "go ahead", "rename")) is False


# ------------------------------------------------------------------- the model-driven resume of an interrupted turn

_CONTEXT = {"surface": "openclaw", "platform": "openclaw"}


@pytest.fixture
def continuity(tmp_path):
    from core.runtime_continuity import configure_runtime_continuity_db_path, reset_runtime_continuity_state
    from storage.migrations import run_migrations

    db_path = tmp_path / "runtime-continuity.db"
    run_migrations(db_path=db_path)
    configure_runtime_continuity_db_path(str(db_path))
    reset_runtime_continuity_state()
    yield
    reset_runtime_continuity_state()
    configure_runtime_continuity_db_path(None)


def _decision(**fields):
    from core.memory_first_router import ModelExecutionDecision

    base = dict(source="provider_execution", provider_id="ollama-local:test", provider_name="ollama-local", model_name="test",
                confidence=0.8, trust_score=0.84, used_model=True, validation_state="valid")
    return ModelExecutionDecision(**{**base, **fields})


def _interrupted_search(session_id: str):
    """A model-driven tool turn ('find tool intent wiring') whose search crashed mid-step: the checkpoint is interrupted
    and holds the pending search. The same stand-in shape as tests/test_runtime_continuity.py's resume test."""
    from apps.vool_agent import VoolAgent
    from core.runtime_continuity import latest_resumable_checkpoint

    agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
    agent.start()
    agent.context_loader.load = mock.Mock(return_value=SimpleNamespace(  # type: ignore[assignment]
        local_candidates=[], retrieval_confidence_score=0.0, assembled_context=lambda: "", context_snippets=lambda: [],
        report=SimpleNamespace(retrieval_confidence=0.0, total_tokens_used=lambda: 0,
                               to_dict=lambda: {"external_evidence_attachments": []}),
    ))
    agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
        return_value=_decision(task_hash="final-synthesis", output_text="Grounded final answer after resume.", confidence=0.82))
    agent.memory_router.resolve_tool_intent = mock.Mock(side_effect=[  # type: ignore[assignment]
        _decision(task_hash="tool-intent-search",
                  structured_output={"intent": "workspace.search_text", "arguments": {"query": "tool_intent"}}),
        _decision(task_hash="tool-intent-direct",
                  structured_output={"intent": "respond.direct", "arguments": {"message": "Grounded final answer after resume."}}),
    ])
    with mock.patch("core.agent_runtime.agent.execute_tool_intent", side_effect=RuntimeError("tool crashed mid-step")), \
            mock.patch("core.agent_runtime.agent.orchestrate_parent_task", return_value=None), pytest.raises(RuntimeError):
        agent.run_once("find tool intent wiring", session_id_override=session_id, source_context=dict(_CONTEXT))
    checkpoint = latest_resumable_checkpoint(session_id)
    assert checkpoint is not None and checkpoint["status"] == "interrupted", checkpoint
    return agent, checkpoint


def _calls(execute) -> list[tuple[str, bool]]:
    """Each tool execution: its intent, and whether it ran under a resumed checkpoint."""
    return [
        (str((call.args[0] if call.args else call.kwargs.get("payload") or {}).get("intent") or ""),
         bool(dict(call.kwargs.get("source_context") or {}).get("runtime_checkpoint_resumed")))
        for call in execute.call_args_list
    ]


def _search_execution(payload: dict, **_kwargs):
    from core.tool_intent_executor import ToolIntentExecution

    return ToolIntentExecution(
        handled=True, ok=True, status="executed", mode="tool_executed", tool_name=str((payload or {}).get("intent") or ""),
        response_text='Search matches for "tool_intent":\n- core/tool_intent_executor.py:42 def execute_tool_intent(',
        details={"query": "tool_intent"},
    )


@pytest.mark.parametrize("text", ["continue", "carry on with that", "go ahead and do it", "please continue where you stopped",
                                  "go ahead with the search"])
def test_an_interrupted_model_turn_resumes_on_a_go_ahead_that_brings_nothing_of_its_own(continuity, text):
    from core.runtime_continuity import latest_resumable_checkpoint

    session_id = "openclaw:resume-" + re.sub(r"\W+", "-", text)
    agent, checkpoint = _interrupted_search(session_id)
    stored = dict((checkpoint.get("state") or {}).get("pending_tool_payload") or {})
    with mock.patch("core.agent_runtime.agent.execute_tool_intent", side_effect=_search_execution) as execute, \
            mock.patch("core.agent_runtime.agent.orchestrate_parent_task", return_value=None):
        result = agent.run_once(text, session_id_override=session_id, source_context=dict(_CONTEXT))
    calls = _calls(execute)
    assert result["mode"] == "tool_executed", (text, result.get("mode"), result.get("route"), calls)
    assert "Grounded final answer after resume." in result["response"], (text, result["response"][:300])
    # The one step that ran is the interrupted turn's own pending step, adopted through the resume.
    assert stored.get("intent") and calls == [(stored["intent"], True)], (text, calls, stored)
    assert latest_resumable_checkpoint(session_id) is None, text


def test_an_interrupted_model_turn_is_not_replayed_by_a_follow_up_with_its_own_request(continuity):
    from core.runtime_continuity import latest_resumable_checkpoint

    session_id = "openclaw:own-request-after-interruption"
    agent, checkpoint = _interrupted_search(session_id)
    with mock.patch("core.agent_runtime.agent.execute_tool_intent", side_effect=_search_execution) as execute, \
            mock.patch("core.agent_runtime.agent.orchestrate_parent_task", return_value=None):
        result = agent.run_once("go ahead and calculate 17 times 23", session_id_override=session_id,
                                source_context=dict(_CONTEXT))
    still = latest_resumable_checkpoint(session_id)
    diagnosis = (_calls(execute), result.get("mode"), result.get("route"), result["response"][:300],
                 still and (still["checkpoint_id"], still["status"]), checkpoint["checkpoint_id"])
    assert not any(resumed for _intent, resumed in _calls(execute)), diagnosis
    assert "391" in result["response"], diagnosis
    assert still is not None and still["checkpoint_id"] == checkpoint["checkpoint_id"], diagnosis
