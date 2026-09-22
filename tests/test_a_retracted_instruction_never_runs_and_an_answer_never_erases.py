"""An instruction the turn takes back must never execute, and enforcement must never erase an answer.

One measured turn, two independent roots, 2026-08-15 on the served surface::

    U: Search my local workspace for `password.txt`. WAIT, STOP. Cancel the search immediately.
       Instead, write a 2-line javascript function that returns "Hello".
       Constraints: Output ONLY raw javascript. NO markdown, NO backticks, NO json.
       The first line must contain the word BINGO in a comment.
    A: (empty answer)
       Work log: workspace.search_text  query=password.txt -> no_results
                 web retrieval started/completed
                 deep lane qwen3:14b completed, 1,136 tokens

ROOT A -- the full turn text, retracted clause included, was adopted as the request at the
interpretation intake (`adapt_user_input(user_input)`), and every reasoning consumer inherited it:
the conductor planner planned the cancelled search, the classifier read `security_hardening` off
the bare word "password", and the research gate bought a web retrieval off the backticked filename
inside the RETRACTED clause. The repair is ONE narrowing at that intake seam
(`core.within_turn_retraction.intake_request_text`), not per-lane checks -- the lane that
re-decides retraction on its own is the lane that eventually forgets.

ROOT B -- the deep lane produced 1,136 tokens and the user saw "". `apply_raw_output_contract`
erased on ANY residual violation, and every realistic reply shape (fenced block, prose+fence, a
3-line function under a 2-line contract, code plus a trailing action-dict echo) violates
`exact_lines`. The repair: a shape-only violation on a real deliverable REPAIRS and ships;
content-class violations still erase at the contract seam, and the final UI seam then reports a
runtime notice rather than a silent empty string.

The negative controls carry equal weight: a false retraction silently discards real work, and a
loosened eraser leaks scaffolding -- each the same harm pointing the other way.
"""

from __future__ import annotations

from unittest import mock

import pytest

from core.curiosity_roamer import _adaptive_research_decision
from core.raw_output_contract import (
    apply_raw_output_contract,
    parse_raw_output_contract,
)
from core.task_router import classify
from core.within_turn_retraction import intake_request_text

REPORTED = (
    "Search my local workspace for `password.txt`. WAIT, STOP. Cancel the search immediately. "
    'Instead, write a 2-line javascript function that returns "Hello". '
    "Constraints: Output ONLY raw javascript. NO markdown, NO backticks, NO json. "
    "The first line must contain the word BINGO in a comment."
)


# =============================================================================================
# ROOT A -- the intake narrowing, at the seams that misread the measured turn
# =============================================================================================


def test_the_intake_text_of_the_reported_turn_carries_the_request_and_not_the_retraction() -> None:
    intake = intake_request_text(REPORTED)

    assert "password.txt" not in intake
    assert "javascript" in intake.lower()
    assert "BINGO" in intake


def test_the_classifier_seam_no_longer_reads_the_retracted_clause_as_security_work() -> None:
    """The measured misroute: `security_hardening` off the bare word "password"."""

    assert classify(REPORTED)["task_class"] == "security_hardening"
    assert classify(intake_request_text(REPORTED))["task_class"] != "security_hardening"


def test_the_research_gate_no_longer_buys_a_web_retrieval_off_the_retracted_filename() -> None:
    """The measured spurious retrieval: `has_specific_issue` fired on the backticked
    `password.txt` INSIDE the withdrawn clause and escalated the chat turn to web research."""

    import core.curiosity_roamer as curiosity_roamer

    class _Interp:
        topic_hints: list[str] = []

    # The suite pins web fallback OFF, which would short-circuit the decision before the
    # escalation logic under test ever runs. Open only the policy gate; the decision itself --
    # markers, task class, escalation -- runs for real.
    context = {"surface": "openclaw", "allow_remote_fetch": True}
    with mock.patch.object(
        curiosity_roamer.policy_engine, "allow_web_fallback", return_value=True
    ):
        full = _adaptive_research_decision(
            user_input=REPORTED,
            classification=classify(REPORTED),
            interpretation=_Interp(),
            source_context=dict(context),
        )
        narrowed_text = intake_request_text(REPORTED)
        narrowed = _adaptive_research_decision(
            user_input=narrowed_text,
            classification=classify(narrowed_text),
            interpretation=_Interp(),
            source_context=dict(context),
        )

    assert full["enabled"] is True, "the measured defect stopped reproducing -- rewrite this test"
    assert narrowed["enabled"] is False


def test_the_constraints_suffix_survives_narrowing_with_an_identical_contract() -> None:
    """Narrowing must never weaken the output contract the same turn stated after the pivot."""

    full_contract = parse_raw_output_contract(REPORTED)
    narrowed_contract = parse_raw_output_contract(intake_request_text(REPORTED))

    assert full_contract is not None
    assert narrowed_contract == full_contract
    assert narrowed_contract.exact_lines == 2
    assert narrowed_contract.code_deliverable is True


def test_the_intake_narrowing_is_idempotent() -> None:
    once = intake_request_text(REPORTED)

    assert intake_request_text(once) == once


# ---------------------------------------------------------------------------------------------
# The class, not the phrasing: retraction paraphrases across tool domains, sloppy variants
# included. None of these share wording with the report.
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "withdrawn", "live_marker"),
    (
        # file read, sloppy lowercase "hold on scratch that"
        (
            "open notes.txt and read me the second paragraph. hold on scratch that, "
            "write a limerick about coffee instead",
            "notes.txt",
            "limerick",
        ),
        # web lookup, sloppy "wait stop cancel that"
        (
            "look up the weather in Reykjavik for me. wait stop cancel that, "
            "tell me what causes auroras instead",
            "reykjavik",
            "auroras",
        ),
        # directory listing
        (
            "list every file in my downloads folder. never mind that, explain what a symlink is",
            "downloads",
            "symlink",
        ),
        # web search
        (
            "search the web for flight deals to osaka. i changed my mind, "
            "give me packing tips for japan instead",
            "flight deals",
            "packing",
        ),
        # file read of a sensitive-looking target, bare "wait, no."
        (
            "grab the contents of secrets.json. wait, no. just draft an apology email to my landlord",
            "secrets.json",
            "apology",
        ),
        # remote fetch, terse "wait stop"
        (
            "fetch the changelog from github. wait stop, just summarize what semver means",
            "changelog",
            "semver",
        ),
        # shouted, punctuation-free variant
        (
            "read the deploy log and paste the errors. WAIT STOP CANCEL THAT. "
            "NOW TELL ME A JOKE ABOUT ROBOTS",
            "deploy log",
            "joke",
        ),
    ),
)
def test_the_withdrawn_object_never_reaches_the_intake_text(
    text: str, withdrawn: str, live_marker: str
) -> None:
    intake = intake_request_text(text)

    assert intake != text, text
    assert withdrawn.lower() not in intake.lower(), text
    assert live_marker.lower() in intake.lower(), text


# ---------------------------------------------------------------------------------------------
# Negative controls -- a false retraction silently discards what the user asked for
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    (
        # Cancelling IS the task, with an explanation request attached.
        "cancel the subscription and explain why it renewed",
        # An adversarial near-miss: the cancellation happened OUTSIDE this turn.
        "restart the search you cancelled yesterday",
        # Cue words as subject matter.
        "stop words are removed by the tokenizer, explain how that works",
        "wait for the build to finish then run the tests",
        # Ordinary requests without any cue at all.
        "Search my local workspace for password.txt and tell me what you find",
        "write a 2-line javascript function that returns 'Hello'",
    ),
)
def test_a_turn_without_a_live_retraction_passes_through_byte_identical(text: str) -> None:
    assert intake_request_text(text) == text, text


def test_a_genuine_security_request_still_classifies_as_security_work() -> None:
    """The control that keeps the narrowing from becoming a way to dodge the security lane."""

    text = "rotate the password policy and harden ssh on my server"

    assert intake_request_text(text) == text
    assert classify(text)["task_class"] == "security_hardening"


# =============================================================================================
# ROOT A -- the REAL intake seam: `VoolAgent.run_once` hands reasoning only the live request
# =============================================================================================


def test_run_once_hands_interpretation_and_the_conductor_only_the_live_request() -> None:
    """Drives `VoolAgent.run_once` -- the method the API server calls -- and records two real
    seams without replacing either: the text handed to `adapt_user_input` (interpretation intake,
    the earliest wrong decision on the measured turn) and the `raw_input` handed to the conductor
    planner (the seam whose qwen3:4b plan ran the cancelled `password.txt` search). The conductor
    wrapper returns a canned envelope so the turn ends before any model lane, exactly like the
    established `run_once` seam tests in this suite.
    """

    import core.agent_runtime.agent as vool_agent_module
    from apps.vool_agent import VoolAgent

    adapt_texts: list[str] = []
    conductor_raw_inputs: list[str] = []
    real_adapt = vool_agent_module.adapt_user_input

    def recording_adapt(user_input, **kwargs):
        adapt_texts.append(str(user_input))
        return real_adapt(user_input, **kwargs)

    def recording_conductor(self, *, effective_input, raw_input, session_id, source_context):
        conductor_raw_inputs.append(str(raw_input))
        return {
            "response": "conductor seam reached",
            "model_calls": 0,
            "route": "test:conductor_seam",
        }

    agent = VoolAgent(backend_name="test-backend", device="retraction-test", persona_id="default")
    source_context = {"surface": "openclaw", "platform": "openclaw"}

    with mock.patch.object(vool_agent_module, "adapt_user_input", new=recording_adapt), \
         mock.patch.object(VoolAgent, "_maybe_answer_conductor_turn", new=recording_conductor):
        agent.run_once(REPORTED, source_context=dict(source_context))
        # Negative control on the same wiring: an ordinary turn passes through byte-identical.
        agent.run_once(
            "write a short haiku about rivers",
            source_context=dict(source_context),
        )

    assert adapt_texts, "the interpretation intake never ran -- this test observed nothing"
    reported_intakes = [text for text in adapt_texts if "javascript" in text.lower()]
    assert reported_intakes, "the reported turn never reached the interpretation intake"
    for text in reported_intakes:
        assert "password.txt" not in text, (
            "the retracted clause reached the interpretation intake: " + text
        )
        assert "BINGO" in text

    assert conductor_raw_inputs, "the conductor seam never ran -- this test observed nothing"
    for raw_input in conductor_raw_inputs:
        assert "password.txt" not in raw_input, (
            "the retracted clause reached the conductor planner: " + raw_input
        )
    assert any("javascript" in raw_input.lower() for raw_input in conductor_raw_inputs)

    assert "write a short haiku about rivers" in adapt_texts, (
        "a turn with no retraction must reach interpretation byte-identical"
    )


# =============================================================================================
# ROOT B -- enforcement REPAIRS a real deliverable and never ends a turn as a silent empty
# =============================================================================================

CONTRACT_TEXT = (
    'Instead, write a 2-line javascript function that returns "Hello". '
    "Constraints: Output ONLY raw javascript. NO markdown, NO backticks, NO json. "
    "The first line must contain the word BINGO in a comment."
)

TWO_LINE_FUNCTION = '// BINGO\nfunction hello() { return "Hello"; }'
THREE_LINE_FUNCTION = '// BINGO\nfunction hello() {\n  return "Hello"; }'


def _contract():
    contract = parse_raw_output_contract(CONTRACT_TEXT)
    assert contract is not None
    return contract


def _finalize(draft: str, *, upstream_rejected: bool = False):
    """Drive the terminal UI seam -- `_validate_final_chat_output` -- with the real contract."""
    from core.agent_runtime.response import _validate_final_chat_output

    source_context: dict[str, object] = {"raw_output_contract": _contract().to_dict()}
    if upstream_rejected:
        # What the model router records when its own application erased the draft upstream.
        source_context["response_control"] = {
            "raw_output": {"rejected": True, "violations": ["markdown"]}
        }
    final = _validate_final_chat_output(draft, source_context=source_context)
    return final, source_context


def test_a_fenced_reply_ships_as_raw_code_without_backticks() -> None:
    final, _ = _finalize(f"```javascript\n{TWO_LINE_FUNCTION}\n```")

    assert final == TWO_LINE_FUNCTION
    assert "```" not in final


def test_a_wrong_line_count_ships_the_repaired_deliverable_not_an_empty_answer() -> None:
    """The measured erasure: every realistic qwen3:14b shape is 3+ lines and was blanked."""

    result = apply_raw_output_contract(THREE_LINE_FUNCTION, _contract())

    assert result.text == THREE_LINE_FUNCTION
    assert result.compliant is False
    assert result.violations == ("exact_lines",)

    final, _ = _finalize(f"```javascript\n{THREE_LINE_FUNCTION}\n```")

    assert final == THREE_LINE_FUNCTION


def test_prose_around_the_fence_is_stripped_and_the_code_survives() -> None:
    final, _ = _finalize(
        "Here is the function you asked for:\n"
        f"```javascript\n{TWO_LINE_FUNCTION}\n```"
    )

    assert final == TWO_LINE_FUNCTION


def test_a_trailing_action_dict_echo_is_removed_and_the_code_survives() -> None:
    """The measured leak: an unquoted `{action: write, content: ...}` echo is unparseable as
    JSON, so the strict-JSON tool-span stripper never saw it and it rendered as answer text."""

    final, _ = _finalize(f"{TWO_LINE_FUNCTION}\n{{action: write, content: hello.js}}")

    assert final == TWO_LINE_FUNCTION
    assert "action" not in final


def test_pure_prose_under_a_raw_code_contract_yields_a_notice_never_a_silent_empty() -> None:
    prose = (
        "I cannot run searches, but a javascript function is a reusable block of behaviour "
        "you can call by name."
    )
    final, context = _finalize(prose)

    assert final.strip(), "the turn ended as a silent empty string"
    assert final != prose, "prose is not a code deliverable and must not ship as one"
    assert context.get("runtime_notice_not_an_answer") is True


def test_an_upstream_erased_draft_still_surfaces_as_a_notice_at_the_final_seam() -> None:
    """The router erased the draft before the final seam ever saw text. The record it leaves in
    `response_control` is the evidence a deliverable existed, and the user still gets a report."""

    final, context = _finalize("", upstream_rejected=True)

    assert final.strip(), "an upstream-erased turn ended as a silent empty string"
    assert context.get("runtime_notice_not_an_answer") is True


# ---------------------------------------------------------------------------------------------
# Negative controls -- repair must not become a leak, and honest empties stay empty
# ---------------------------------------------------------------------------------------------


def test_a_compliant_reply_is_untouched_byte_for_byte() -> None:
    result = apply_raw_output_contract(TWO_LINE_FUNCTION, _contract())

    assert result.text == TWO_LINE_FUNCTION
    assert result.changed is False
    assert result.compliant is True

    final, context = _finalize(TWO_LINE_FUNCTION)

    assert final == TWO_LINE_FUNCTION
    assert context.get("runtime_notice_not_an_answer") is None


def test_a_planning_checklist_still_never_ships_as_the_answer() -> None:
    """Content-class violations keep erasing at the contract seam -- shipping the scaffold would
    regress the checklist-leak protection -- and the final seam reports instead of staying silent."""

    checklist = "Identify the theme\nChoose an image\nVerify the rhyme"
    verse_contract = parse_raw_output_contract(
        "Write a two-line verse about gravity. Final answer only; no checklist or title."
    )
    assert verse_contract is not None
    result = apply_raw_output_contract(checklist, verse_contract)

    assert result.text == ""
    assert "internal_scaffold" in result.violations

    final, context = _finalize(checklist)

    assert "Identify the theme" not in final
    assert final.strip(), "a withheld scaffold must yield a report, not a silent empty"
    assert context.get("runtime_notice_not_an_answer") is True


def test_an_exact_literal_contract_still_binds_and_is_not_repaired_around() -> None:
    contract = parse_raw_output_contract(
        "Output exactly the string ERR_NO_ACCESS and nothing else."
    )
    assert contract is not None
    result = apply_raw_output_contract("Cannot access that\n* ERR_NO_ACCESS", contract)

    assert result.text == "ERR_NO_ACCESS"
    assert result.compliant is True


def test_a_turn_with_no_draft_and_no_upstream_rejection_stays_empty() -> None:
    """No deliverable ever existed: inventing a withheld-answer notice here would be a lie."""

    final, context = _finalize("")

    assert final == ""
    assert context.get("runtime_notice_not_an_answer") is None


def test_a_pasted_manifest_dict_is_not_mistaken_for_an_action_echo() -> None:
    """The echo stripper keys on the leading key name; `name`/`version` is user content."""

    draft = f'{TWO_LINE_FUNCTION}\n{{"name": "myapp", "version": "1.0.0"}}'
    result = apply_raw_output_contract(draft, _contract())

    assert '"myapp"' in result.repaired_text, (
        "a package.json-style dict was stripped as if it were runtime scaffolding"
    )


def test_shape_repair_also_holds_outside_code_contracts() -> None:
    """The rule is violation-class-keyed, not keyed to the reported prompt: a two-bullet answer
    under a three-bullet contract ships the bullets the model wrote."""

    contract = parse_raw_output_contract(
        "Exactly three bullet points, prefix each with *. Nothing else."
    )
    assert contract is not None
    result = apply_raw_output_contract(
        "* Cedar stores carbon\n* Wetlands slow floods", contract
    )

    assert result.text == "* Cedar stores carbon\n* Wetlands slow floods"
    assert result.compliant is False
    assert result.violations == ("bullet_count",)


# ---------------------------------------------------------------------------------------------
# "Raw" states the absence of a wrapper as plainly as "no markdown" does
# ---------------------------------------------------------------------------------------------


def test_output_only_raw_code_forbids_the_fence_without_saying_no_markdown() -> None:
    """Found live on hostile seed 4242, and wider than the typo that exposed it.

        U: write a 2-line bash function that returns PONG. Output ONLY raw bash. no makrdown
        A: ```bash
           pong() { echo PONG; }
           ```

    The user misspelled "markdown", so the literal no-markdown pattern missed -- but "Output ONLY
    raw bash" was right there and should already have forbidden the wrapper. Measured at the
    parser: `no_markdown` was False for "Output ONLY raw bash." with no typo at all, so every
    turn phrased that way had its contract ignored.

    Raw code inside a fence is not raw.
    """

    from core.raw_output_contract import apply_raw_output_contract, parse_raw_output_contract

    fenced = "```bash\npong() { echo PONG; }\n```"

    for request in (
        "write a bash fn. Output ONLY raw bash.",
        "write a bash fn. Output ONLY raw bash. no makrdown no backticks",
        "write a js fn. Output ONLY raw javascript.",
        "write a py fn. output only raw python",
    ):
        contract = parse_raw_output_contract(request)
        assert contract is not None and contract.no_markdown is True, request
        assert "```" not in apply_raw_output_contract(fenced, contract).text, request


def test_a_request_for_markdown_still_keeps_its_markup() -> None:
    """The control: "raw markdown only" asks FOR markup, and must not be stripped of it."""

    from core.raw_output_contract import parse_raw_output_contract

    contract = parse_raw_output_contract("show me a table. reply with raw markdown only")

    assert getattr(contract, "no_markdown", False) is False
