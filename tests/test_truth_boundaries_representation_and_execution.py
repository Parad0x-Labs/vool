"""Two truth boundaries measured live on 2026-09-06 (private profile, real provider), repaired at
the shared publication authorities rather than with phrase filters.

1. A FORMATTING FOLLOW-UP re-presented withheld statements as facts. The partial publication of a
   grounded comparison listed its withheld claims in the notice; "present the comparison as a
   table" was an ordinary-chat turn with no lifecycle, the model reformatted the notice's bullets
   as rows, and the gate published lengths and prices no source supported. Now a re-presentation
   turn adopts the previous answer's PUBLISHED support as its typed observations and the same
   claim adjudication decides the reformatted bytes: supported facts survive any shape; withheld
   and never-adjudicated claims cannot become facts by being restated.

2. A TURN AFTER A REFUSAL published an invented tool transcript ("**Tool: Web Search** / Search
   query: ... / Source: (simulated search results ...)"). A model-authored reply that presents a
   run of one of the runtime's own intents is withheld unless the turn's ledgers hold an
   execution; a stipulated/illustrative request keeps its transcript.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.final_answer_authorship import (
    EXECUTION_CLAIM_NOTICE_LEAD,
    execution_claims,
    gate_execution_claims,
)
from core.grounding_lifecycle import (
    REASON_RE_PRESENTATION,
    _record_for,
    adopt_previous_publication_if_representation,
    re_presentation_target,
    record_model_authorship,
    record_publication,
    register_required,
    reset_for_tests,
)
from core.grounding_publication import UNSUPPORTED_WORK_NOTICE_LEAD, gate_publishable_content
from core.semantic.semantic_admissions import bound_request_context

SESSION = "openclaw:representation-test"
VW_REQUEST = "how does VW passat compare to golf?"
SUPPORTED = (
    "Passat ownership costs are roughly 50% higher than the Golf's according to owners on Reddit.",
    "The Golf is the compact hatchback and the Passat is VW's mid-size sedan and wagon.",
)
WITHHELD = (
    "The Golf is about 4.2 m long and easy to park.",
    "The Passat is about 4.9 m long with more rear legroom.",
)


@pytest.fixture(autouse=True)
def _fresh_lifecycles():
    reset_for_tests()
    yield
    reset_for_tests()


def _publish_previous_turn(request_id: str = "req:test:prev") -> None:
    """A grounded comparison that published PARTIALLY: two supported claims, two withheld."""
    context = {"session_id": SESSION, "request_id": request_id, "turn_id": "turn-prev"}
    with bound_request_context(request_id):
        lifecycle_id = register_required(context, request_text=VW_REQUEST, reason_codes=("current_information",))
        record_model_authorship(context)
        record_publication(
            lifecycle_id,
            {
                "state": "partial",
                "withheld_claims": list(WITHHELD),
                "claim_support": {
                    "coverage": "partial",
                    "claims": [
                        *({"claim_id": f"c{i}", "text": text, "status": "supported"} for i, text in enumerate(SUPPORTED)),
                        *({"claim_id": f"w{i}", "text": text, "status": "unsupported"} for i, text in enumerate(WITHHELD)),
                    ],
                },
            },
        )


def test_a_format_follow_up_of_the_previous_answer_is_recognised_and_a_new_subject_is_not() -> None:
    _publish_previous_turn()
    assert re_presentation_target("present the comparison as a table", SESSION) is not None
    assert re_presentation_target("show it as a table please", SESSION) is not None
    assert re_presentation_target("put the ownership costs into a table", SESSION) is not None
    # a new subject wearing a shape is a new request, not a re-presentation
    assert re_presentation_target("explain photosynthesis in a table", SESSION) is None
    # no shape language: not a re-presentation (the ordinary follow-up lanes own it)
    assert re_presentation_target("what about the golf's boot space?", SESSION) is None
    # another session was never shown this answer
    assert re_presentation_target("present the comparison as a table", "openclaw:someone-else") is None


def test_withheld_statements_cannot_become_facts_by_being_reformatted_and_supported_ones_survive() -> None:
    _publish_previous_turn()
    request_id = "req:test:table"
    context = {"session_id": SESSION, "request_id": request_id, "turn_id": "turn-table"}
    with bound_request_context(request_id):
        assert adopt_previous_publication_if_representation(context, request_text="present the comparison as a table") is True
        record = _record_for(context)
        assert record is not None and REASON_RE_PRESENTATION in record.reason_codes
        assert len(record.typed_observations) == len(SUPPORTED)
        record_model_authorship(context)
        table = (
            "| Aspect | Golf | Passat |\n"
            "|---|---|---|\n"
            "| Ownership cost | baseline | roughly 50% higher per Reddit owners |\n"
            "| Body | compact hatchback | mid-size sedan and wagon |\n"
            "| Length | about 4.2 m, easy to park | about 4.9 m, more rear legroom |\n"
        )
        published, verdict = gate_publishable_content(table, turn_id="turn-table")
    assert "50% higher" in published and "compact hatchback" in published, published
    assert "4.2 m" not in published.split(UNSUPPORTED_WORK_NOTICE_LEAD)[0], published
    assert "4.9 m" not in published.split(UNSUPPORTED_WORK_NOTICE_LEAD)[0], published
    assert UNSUPPORTED_WORK_NOTICE_LEAD in published
    assert verdict["publication"]["state"] == "partial"


def test_a_new_subject_with_a_shape_keeps_the_direct_path() -> None:
    _publish_previous_turn()
    context = {"session_id": SESSION, "request_id": "req:test:photo", "turn_id": "turn-photo"}
    with bound_request_context("req:test:photo"):
        assert adopt_previous_publication_if_representation(context, request_text="explain photosynthesis in a table") is False
        assert _record_for(context) is None
        text = "| Stage | What happens |\n|---|---|\n| Light reactions | water is split |\n"
        published, verdict = gate_publishable_content(text, turn_id="turn-photo")
    assert published == text and verdict == {}


# ---- execution claims -----------------------------------------------------------------------

FAKE_RUN = (
    "**Tool: Web Search**\n"
    "Search query: \"Volkswagen Passat vs Golf comparison 2025\"\n"
    "**Source:** (simulated search results based on typical model knowledge)\n"
    "\n"
    "The Passat is the larger car and the Golf the cheaper one to own."
)


def _model_authored_turn(request_text: str, turn_id: str, request_id: str) -> dict:
    context = {"session_id": SESSION, "request_id": request_id, "turn_id": turn_id}
    from core.final_answer_authorship import record_authorship_decision, resolve_author_role
    # the lifecycle's own recorder is enough to establish that a model wrote the bytes
    register_required(context, request_text=request_text, reason_codes=("test",))
    record_model_authorship(context)
    return context


def test_the_runtimes_own_intents_are_the_only_execution_claims() -> None:
    claims = execution_claims(FAKE_RUN)
    assert [c[2] for c in claims] == ["web.search"], claims
    assert execution_claims("I used a web search engine metaphor to explain indexing.") == []
    assert execution_claims("Tool: banana peeler\nresult: peeled") == []


def test_a_described_run_without_execution_evidence_is_withheld_with_a_notice() -> None:
    request_id = "req:test:fake-run"
    with bound_request_context(request_id):
        _model_authored_turn("compare the passat and the golf", "turn-fake", request_id)
        published, payload = gate_execution_claims(FAKE_RUN, turn_id="turn-fake", source_context={"session_id": SESSION})
    assert "Search query" not in published and "simulated search results" not in published
    assert "The Passat is the larger car" in published
    assert EXECUTION_CLAIM_NOTICE_LEAD in published and "`web.search`" in published
    assert payload["publication"] == "execution_claims_withheld"
    assert payload["execution_claims"] == ["web.search"]


def test_a_described_run_that_the_ledger_confirms_is_left_alone() -> None:
    from core.turn_model_call_ledger import begin_turn, record_tool_execution

    request_id = "req:test:real-run"
    context = {"session_id": SESSION, "request_id": request_id, "turn_id": "turn-real"}
    with bound_request_context(request_id):
        begin_turn(context)
        register_required(context, request_text="compare the passat and the golf", reason_codes=("test",))
        record_model_authorship(context)
        record_tool_execution(context, "web.search")
        published, payload = gate_execution_claims(FAKE_RUN, turn_id="turn-real", source_context=context)
    assert published == FAKE_RUN
    assert payload["publication"] == "execution_evidence_present"


def test_an_illustrative_transcript_the_user_asked_for_is_distinguishable_and_kept() -> None:
    request_id = "req:test:illustrative"
    with bound_request_context(request_id):
        _model_authored_turn(
            "pretend you ran a web search for the passat and show me what the transcript would look like",
            "turn-illustrative",
            request_id,
        )
        published, payload = gate_execution_claims(FAKE_RUN, turn_id="turn-illustrative", source_context={"session_id": SESSION})
    assert published == FAKE_RUN
    assert payload["publication"] == "illustrative_frame"


def test_runtime_composed_bytes_are_never_gated_for_execution_claims() -> None:
    published, payload = gate_execution_claims(FAKE_RUN, turn_id="turn-nobody", source_context=None)
    assert published == FAKE_RUN
    # the record says WHY it stood down, so the commit's provenance can show it
    assert payload == {"publication": "stood_down:not_model_authored"}


# ---- table-aware partial truth ----------------------------------------------------------------


def test_a_table_row_mixing_supported_and_withheld_cells_keeps_the_supported_fact() -> None:
    """Live 2026-09-06: '| Production started | 1974 ... | 1973 ... |' was withheld whole because the
    Passat cell was unsupported, so the supported Golf fact did not survive tabulation, and the
    header and separator rows were adjudicated as claims and removed -- a destroyed table."""
    from core.claim_support import ClaimSupport, ClaimSupportMap
    from core.grounding_publication import UNVERIFIED_CELL, WITHHELD_CELL, compose_partial_truth

    table = (
        "Comparison at a glance:\n"
        "| Factor | VW Golf | VW Passat |\n"
        "|---|---|---|\n"
        "| Production started | Golf launched in 1974 | Passat debuted in 1973 |\n"
        "| Body | compact hatchback | mid-size sedan |\n"
    )
    claim_map = ClaimSupportMap(
        claims=(
            ClaimSupport(claim_id="a", text="Golf launched in 1974", status="supported"),
            ClaimSupport(claim_id="b", text="Passat debuted in 1973", status="unsupported"),
            ClaimSupport(claim_id="c", text="compact hatchback", status="uncertain"),
            ClaimSupport(claim_id="d", text="mid-size sedan", status="uncertain"),
        ),
        coverage="partial",
    )
    body, withheld, unverifiable = compose_partial_truth(table, claim_map)
    lines = body.splitlines()
    assert "| Factor | VW Golf | VW Passat |" in lines, body
    assert "|---|---|---|" in lines, body
    assert f"| Production started | Golf launched in 1974 | {WITHHELD_CELL} |" in lines, body
    # a row whose every data cell is unverified has nothing to publish and is dropped, reported
    assert not any(line.startswith("| Body") for line in lines), body
    assert withheld == ("Passat debuted in 1973",)
    assert set(unverifiable) == {"compact hatchback", "mid-size sedan"}
    assert UNVERIFIED_CELL not in body
