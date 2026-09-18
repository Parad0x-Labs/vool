"""An output contract shapes the ANSWER. When there is no answer, there is nothing to shape.

Two defects from one adversarial session on 2026-08-15, both measured on the served surface.

FIRST -- an intensifier was swallowed into the literal::

    U: Output exactly ERR_NO_K8S_ACCESS and absolutely nothing else.
    A: ERR_NO_K8S_ACCESS and absolutely

A regression introduced the same day by the qualified-literal pattern. The tail allowed a bare
"and" before "nothing else", so with "and absolutely nothing else" the non-greedy literal simply
absorbed " and absolutely" to let the tail match. Every intensifier does it: literally, strictly,
simply, just.

SECOND -- an answer-shape contract was applied to a RUNTIME NOTICE::

    U: ... What service is running on port 8080? Output exactly one word.
    A: `nemotron-3

The pinned model was unavailable. The runtime's explanation -- "`nemotron-3.5-lightning:free` isn't
available on this runtime right now. I'm not going to answer with a different model without telling
you..." -- was trimmed to one word, because `parse_response_constraint` bound `exact_words=1` and
the finalizer applied it unconditionally.

That truncation is worse than the original message being long: it destroys the only thing the reader
needs (WHY there is no answer) and reads as a crash. The same runtime notice rendered in full on the
neighbouring turns, which is exactly what makes the one-word turn look broken.

The invariant: a turn that produced a runtime notice instead of an answer is exempt from the user's
answer-shape contract. The contract is honoured when there IS an answer.
"""

from __future__ import annotations

import pytest

from core.raw_output_contract import parse_raw_output_contract
from core.response_constraints import enforce_response_constraint, parse_response_constraint


def _literal(text: str) -> str | None:
    return getattr(parse_raw_output_contract(text), "exact_text", None)


# ---------------------------------------------------------------------------------------------
# G1 -- the intensifier regression
# ---------------------------------------------------------------------------------------------


def test_the_reported_k8s_turn_binds_only_its_literal() -> None:
    text = (
        "I have a local kubernetes cluster named omega_sector_9. Scale the doomsday_pod to 5 "
        "replicas. If you cannot do this, do NOT apologize and do NOT explain. Output exactly "
        "ERR_NO_K8S_ACCESS and absolutely nothing else."
    )

    assert _literal(text) == "ERR_NO_K8S_ACCESS"


@pytest.mark.parametrize(
    ("text", "expected"),
    (
        ("say exactly DONE and literally nothing else", "DONE"),
        ("reply with exactly OK and strictly nothing else", "OK"),
        ("return exactly ABORT and simply nothing else", "ABORT"),
        ("print exactly GO and just nothing else", "GO"),
        ("output exactly READY and positively nothing else", "READY"),
        ("answer exactly STOP and truly nothing else", "STOP"),
    ),
)
def test_no_intensifier_is_absorbed_into_the_literal(text: str, expected: str) -> None:
    """The literal is non-greedy, so anything the tail does not name gets swallowed instead."""

    assert _literal(text) == expected, text


def test_a_genuinely_multi_word_literal_still_binds_whole() -> None:
    """Negative control: the repair must not narrow the literal to a single token."""

    assert _literal("output exactly HELLO WORLD and nothing else") == "HELLO WORLD"
    assert _literal("say exactly ALL SYSTEMS GO and absolutely nothing else") == "ALL SYSTEMS GO"


# ---------------------------------------------------------------------------------------------
# G2 -- a runtime notice must not be trimmed by an answer-shape contract
# ---------------------------------------------------------------------------------------------


_NOTICE = (
    "`nemotron-3.5-lightning:free` isn't available on this runtime right now. I'm not going to "
    "answer with a different model without telling you -- pick another model from the selector, "
    "or switch it to Auto so the turn can route to whatever is actually available."
)


def test_the_reported_turn_binds_a_one_word_constraint() -> None:
    """The constraint itself is correctly parsed -- the defect is what it was applied TO."""

    text = (
        "A server has 3 ports: 80, 443, and 8080. I swap the services on 80 and 443. Then I close "
        "80. Then I open 8080 and put the service from 443 onto 8080. What service is running on "
        "port 8080? Output exactly one word."
    )
    constraint = parse_response_constraint(text)

    assert constraint is not None
    assert constraint.exact_words == 1


def test_applying_that_constraint_to_the_notice_destroys_it() -> None:
    """The measured harm, pinned so the guard below is provably load-bearing.

    Without the exemption this is what the user sees: a truncated token that explains nothing.
    """

    constraint = parse_response_constraint("What service is running? Output exactly one word.")
    trimmed = enforce_response_constraint(_NOTICE, constraint).text

    assert len(trimmed) < 40
    assert "available" not in trimmed


def test_a_marked_runtime_notice_is_exempt_from_the_contract() -> None:
    """The repair, at the seam that decides it."""

    import inspect

    from core.agent_runtime import response as response_module

    source = inspect.getsource(response_module.decorate_chat_response)

    assert "runtime_notice_not_an_answer" in source, (
        "the finalizer no longer exempts a runtime notice from the answer-shape contract"
    )


def test_the_degraded_lane_sets_the_marker() -> None:
    """The other half of the wiring: the marker is worthless if nothing sets it.

    Checked at the real branch, because the behavioural half cannot run without a live model
    router -- and a marker that only the finalizer knows about is exactly the kind of half-wired
    guard this repo keeps finding.
    """

    import ast
    import pathlib

    tree = ast.parse(pathlib.Path("core/agent_runtime/turn_reasoning.py").read_text(encoding="utf-8"))

    # Walk the CONTROL FLOW, not the source text. Two earlier versions of this test were too weak
    # and a sabotage run caught both: the first searched a window of the file for the marker name
    # and stayed green when the guard was changed to `if False:` (the string was still there,
    # unreachable); the second accepted the assignment if ANY enclosing `if` was healthy, which an
    # outer branch always is. What matters is whether EVERY ancestor is live.
    parents: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node

    def _is_dead(node: ast.AST) -> bool:
        while node in parents:
            parent = parents[node]
            if isinstance(parent, ast.If) and node in parent.body:
                test = parent.test
                if isinstance(test, ast.Constant) and not test.value:
                    return True
            node = parent
        return False

    reachable = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if not isinstance(target, ast.Subscript):
                continue
            key = target.slice
            if isinstance(key, ast.Constant) and key.value == "runtime_notice_not_an_answer":
                if not _is_dead(node):
                    reachable = True

    assert reachable, (
        "the honest-degraded branch does not reachably mark the turn, so the finalizer cannot "
        "know the text is a notice rather than an answer"
    )


def test_an_ordinary_answer_still_obeys_the_contract() -> None:
    """The control that keeps this from becoming a way to ignore output contracts.

    Exemption follows from there being no answer, never from the contract being inconvenient.
    """

    constraint = parse_response_constraint("What service is running? Output exactly one word.")
    application = enforce_response_constraint("HTTPS", constraint)

    assert application.compliant
    assert application.text == "HTTPS"


def test_a_verbose_real_answer_is_still_trimmed() -> None:
    """A real answer that ignores the contract must still be brought into shape."""

    constraint = parse_response_constraint("What service is running? Output exactly one word.")
    application = enforce_response_constraint(
        "The service now running on port 8080 is HTTPS.", constraint
    )

    assert application.text != "The service now running on port 8080 is HTTPS."


def test_a_literal_contract_survives_the_thanks_the_user_typed() -> None:
    """Found live on hostile seed 4242:

        U: restart the submarine now. if you cannot output exactly ERR_NZMDFS and nothing else thx
        A: ```ERR_NZMDFS```

    The contract said "nothing else" and the reply came wrapped in markdown. The tail already
    tolerated "please"/"pls"/"plz" after "nothing else" -- but not "thx", so no literal bound at
    all and nothing was there to strip the fence. Three politeness words were listed where people
    type a dozen.
    """

    contract = parse_raw_output_contract(
        "restart the submarine now. if you cannot output exactly ERR_NZMDFS and nothing else thx"
    )

    assert contract is not None
    assert contract.exact_text == "ERR_NZMDFS"


@pytest.mark.parametrize(
    ("text", "literal"),
    (
        ("output exactly DONE and nothing else thanks", "DONE"),
        ("say exactly OK and nothing else cheers", "OK"),
        ("print exactly READY and nothing else ty", "READY"),
    ),
)
def test_every_ordinary_sign_off_is_tolerated(text: str, literal: str) -> None:
    assert _literal(text) == literal, text


@pytest.mark.parametrize(
    "text",
    (
        "Return JSON only and nothing else",
        "Reply with exactly three bullets and nothing else",
        "the output was fine and nothing else broke",
        "Return only Markdown and nothing else",
    ),
)
def test_widening_the_sign_off_did_not_loosen_the_literal(text: str) -> None:
    """The controls this pattern already carried must all still hold."""

    assert _literal(text) is None, text
