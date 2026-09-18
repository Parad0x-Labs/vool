"""M3 — an unsupported current claim can never reach the wire.

The defect, measured on `a2308a26` and still live on `b3f5117f`: three grounding authorities
ran on every current-information turn and NONE of them was consulted where bytes are committed.
`core.finalization.finalize_answer` -- the one seam every served answer traverses, through all
six transport doors -- took `source_context` and deleted it on the next line, and M2's envelope
stamp (`evidence_synthesis_binding`) had zero production readers at that SHA. A fabricated
current answer standing behind a green retrieval receipt published exactly as a grounded one
did, because "a retrieval happened" and "this answer derives from it" were read as one fact.

Every test here drives the REAL publication seam and asserts on the bytes that seam commits
plus the durable lifecycle record it writes -- never on a predicate's return value, and never on
the answer text a lane happened to produce. The fixtures are the repository's own measured
incident rows (`validation-logs/live-search-ui-proof-20260901`, replayed through the M4 lane's
committed corpus), not invented ones.

The four sabotages at the end run inside this pack on every execution: each neuters one load-
bearing seam and asserts that a NAMED test above stops holding. A gate nobody can prove bites is
a gate that has not been shown to be doing anything.
"""

from __future__ import annotations

import pytest

from core import grounding_lifecycle as lifecycle_ledger
from core.finalization import finalize_answer
from core.grounded_synthesis_binding import binding_record, mint_evidence_set, turn_scope
from core.grounding_publication import (
    UNSUPPORTED_WORK_NOTICE_LEAD,
    publication_verdict,
)

# --------------------------------------------------------------------------- fixtures

#: The two rows the measured turn really retrieved (Phoronix / InfoWorld, both dated).
RUST_ROWS = [
    {
        "summary": "Phoronix | 2026-08-31 | Rust Coreutils 0.11 Released With Debug Helper Messages",
        "result_title": "Rust Coreutils 0.11 Released",
        "result_url": "https://www.phoronix.com/news/rust-coreutils-0-11",
        "origin_domain": "phoronix.com",
        "source_type": "web_derived",
    },
    {
        "summary": "InfoWorld | 2026-08-28 | Rust language adds algebraic floating-point methods",
        "result_title": "Rust adds algebraic floating-point methods",
        "result_url": "https://www.infoworld.com/article/rust-algebraic-floats",
        "origin_domain": "infoworld.com",
        "source_type": "web_derived",
    },
]

#: What the model shipped over those rows on the measured turn. None of it is in them.
RUST_FABRICATION = (
    "Sure! Here are some recent headlines:\n\n"
    '1. "Rust 1.64 Released with Improved Performance and Safety Features" - TechCrunch\n'
    "2. \"Mozilla's Firefox Now Uses Rust for WebAssembly Runtime\" - Hacker News\n"
    '3. "How Mozilla is Using Rust to Improve Firefox" - Ars Technica'
)

RUST_REQUEST = "show me recent news coverage about the Rust programming language"

GROUNDED_ANSWER = (
    "Rust Coreutils 0.11 was released with debug helper messages. "
    "Source: [phoronix.com](https://www.phoronix.com/news/rust-coreutils-0-11)."
)

MIXED_ANSWER = (
    "Rust Coreutils 0.11 was released with debug helper messages. "
    "Source: [phoronix.com](https://www.phoronix.com/news/rust-coreutils-0-11).\n"
    "Rust 2.0 shipped today with a garbage collector. "
    "Source: [techcrunch.com](https://techcrunch.com/rust-2)."
)

#: A real music headline that shares exactly one generic publication word with a software
#: claim. On `a2308a26` that single word read as grounding for four invented headlines.
MUSIC_ROWS = [
    {
        "summary": "Pitchfork | 2026-08-30 | Indie band Glasshouse released their third studio album",
        "result_title": "Glasshouse released third album",
        "result_url": "https://pitchfork.com/glasshouse",
        "origin_domain": "pitchfork.com",
        "source_type": "web_derived",
    }
]


@pytest.fixture(autouse=True)
def _isolated_runtime(tmp_path, monkeypatch):
    """A private home, a private database, and an empty lifecycle ledger per test."""

    from core import runtime_paths
    from storage.db import configure_default_db_path, reset_default_connection

    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    runtime_paths.configure_runtime_home(tmp_path)
    configure_default_db_path(tmp_path / "data" / "test.db")
    reset_default_connection()
    from storage.migrations import run_migrations

    run_migrations()
    lifecycle_ledger.reset_for_tests()
    yield
    lifecycle_ledger.reset_for_tests()
    reset_default_connection()
    configure_default_db_path(None)
    runtime_paths.configure_runtime_home(None)


def _admit(text: str) -> str:
    from core.semantic.semantic_result_seam import admit_semantic_result, reset_admission

    reset_admission()
    record = admit_semantic_result({"response": text})
    return str(record["_semantic_admission"]["semantic_result_id"])


def build_turn(
    *,
    request_id: str = "req:m3:1",
    turn_id: str = "turn-m3-1",
    task_id: str = "task-m3-1",
    request_text: str = RUST_REQUEST,
    rows: list[dict] | None = None,
    retrieve: bool = True,
    bind: bool = True,
    reference_binding: bool = True,
    model_call: bool = True,
    foreign_binding: dict | None = None,
) -> dict:
    """Drive one turn's grounding lifecycle through the SAME writers production uses.

    Nothing here reaches into the ledger's internals: the requirement is registered through
    M1's registration seam, the evidence set and the binding record are minted by M2's own
    functions, the model call is recorded by the same entry-time recorder the provider seam
    calls, and the admission check that decides whether a binding belongs to this turn is the
    production one. A test that hand-wrote a lifecycle row would prove only that the gate reads
    dictionaries.
    """

    context: dict = {"request_id": request_id, "cancel_turn_id": turn_id, "session_id": "s-m3"}
    lifecycle_ledger.register_required(
        context,
        request_text=request_text,
        reason_codes=("current_info_signal:news_request",),
    )
    scope = turn_scope(context, task_id=task_id)
    notes = list(rows if rows is not None else RUST_ROWS)
    evidence_set = mint_evidence_set(notes, scope=scope, query=request_text)
    record = binding_record(evidence_set)
    if retrieve:
        lifecycle_ledger.record_retrieved(
            context,
            outcome="bound",
            receipt={
                "schema": "vool.web_retrieval_receipt.v1",
                "status": "available",
                "lifecycle": "succeeded",
                "source_count": len(notes),
                "search_provider": "brave",
            },
            source_count=len(notes),
            notes=notes,
        )
    if bind:
        lifecycle_ledger.record_bound(
            context, binding=foreign_binding or record, notes=notes, scope=scope
        )
    model_context = dict(context)
    if reference_binding and bind and foreign_binding is None:
        model_context["evidence_synthesis_binding"] = dict(record)
    if model_call:
        lifecycle_ledger.record_synthesis_call(
            model_context, model_call_id="mc-1", call_role=""
        )
        lifecycle_ledger.record_claim_support(context, payload={}, model_authored=True)
    return context


def publish(context: dict, content: str) -> dict:
    """Finalize `content` for the turn `context` describes, and return the real commit.

    The request id is bound the way the A0 door binds it, because that -- not an argument -- is
    how the publication seam finds the turn it is finalizing.
    """

    from core.semantic.semantic_admissions import bound_request_context

    _admit(content)
    with bound_request_context(str(context.get("request_id") or "")):
        return finalize_answer(
            turn_id=str(context.get("cancel_turn_id") or ""), canonical_content=content
        )


def lifecycle_of(commit: dict) -> dict:
    return dict(commit.get("grounding_lifecycle") or {})


def publication_of(commit: dict) -> dict:
    return dict(lifecycle_of(commit).get("publication") or {})


# --------------------------------------------------------------------------- RED -> GREEN


def test_the_committed_rust_fabrication_cannot_publish():
    """The measured incident: four invented headlines over two real retrieved rows.

    Everything upstream was green -- the retrieval ran, the receipt said `succeeded`, the
    Activity rail truthfully said three sources -- and that is exactly what made the fabrication
    read sourced. The bytes must not be the ones the model wrote.
    """

    context = build_turn()
    commit = publish(context, RUST_FABRICATION)
    served = str(commit["canonical_content"])

    assert served != RUST_FABRICATION
    for invention in ("TechCrunch", "Hacker News", "Ars Technica", "1.64"):
        assert invention not in served, f"the fabricated detail {invention!r} reached the wire"
    publication = publication_of(commit)
    assert publication["state"] == "refused"
    assert publication["failed_stage"] == "claim_supported"


def test_an_answer_generated_before_its_retrieval_cannot_publish():
    """`model.call_completed` then `web_retrieval_started` -- the recorded order of the defect.

    A call entered before anything was bound carries no evidence-set id in its own context, so
    the turn cannot show that the answer derives from what was later fetched. No timing is
    consulted: the absence of the reference IS the ordering fact.
    """

    context = build_turn(reference_binding=False)
    commit = publish(context, RUST_FABRICATION)

    publication = publication_of(commit)
    assert publication["state"] in {"refused", "failed"}
    assert publication["failed_stage"] == "bound_to_synthesis"
    assert str(commit["canonical_content"]) != RUST_FABRICATION


def test_a_green_receipt_with_no_binding_supports_nothing():
    """A receipt proves a retrieval HAPPENED. It has never proved that anything found was used."""

    context = build_turn(bind=False)
    stages = lifecycle_ledger.lifecycle_for_context(context).as_dict()["stages"]
    assert stages["retrieved"] is True, "the receipt is real and the record says so"
    assert stages["bound_to_synthesis"] is False

    commit = publish(context, RUST_FABRICATION)
    assert str(commit["canonical_content"]) != RUST_FABRICATION
    assert publication_of(commit)["failed_stage"] == "bound_to_synthesis"


def test_one_supported_and_one_fabricated_claim_publishes_only_the_supported_truth():
    """A supported sibling may not launder the claim standing next to it.

    Partial truth is deterministic: the supported line survives WITH its link, the fabricated
    line is gone, and the reader is told a statement was withheld rather than handed a quietly
    shortened answer.
    """

    context = build_turn()
    commit = publish(context, MIXED_ANSWER)
    served = str(commit["canonical_content"])

    assert "Rust Coreutils 0.11 was released" in served
    assert "https://www.phoronix.com/news/rust-coreutils-0-11" in served
    assert "garbage collector" not in served.split(UNSUPPORTED_WORK_NOTICE_LEAD)[0]
    assert UNSUPPORTED_WORK_NOTICE_LEAD in served

    publication = publication_of(commit)
    assert publication["state"] == "partial"
    assert publication["withheld_claim_count"] == 1
    assert publication["supported_claim_count"] == 1


def test_an_unrelated_source_sharing_one_publication_word_supports_nothing():
    """A music headline that says "released" does not ground a software release claim."""

    context = build_turn(rows=MUSIC_ROWS)
    commit = publish(context, "Rust 1.64 was released with improved performance and safety features.")

    assert publication_of(commit)["state"] == "refused"
    assert "1.64" not in str(commit["canonical_content"])


def test_two_claims_over_two_sources_map_independently():
    """Each claim is adjudicated against the source that carries it, not against the pile."""

    context = build_turn()
    answer = (
        "Rust Coreutils 0.11 was released with debug helper messages.\n"
        "Rust added algebraic floating-point methods."
    )
    verdict = publication_verdict(lifecycle_ledger.lifecycle_for_context(context), answer)

    assert verdict.state == "published"
    supported = verdict.claim_support["claims"]
    by_source = {
        claim["text"]: tuple(claim["supporting_sources"])
        for claim in supported
        if claim["status"] == "supported"
    }
    assert len(by_source) == 2, by_source
    assert len({sources for sources in by_source.values()}) == 2, (
        f"two claims resolved to the same source set: {by_source}"
    )


def test_a_failed_typed_tool_plus_an_unrelated_fallback_cannot_hide_the_failure():
    """The Porto incident: the weather lookup failed, an unrelated note stood in for it.

    The typed observation records its OWN failure, so it mints nothing; the wiki row that came
    back from somewhere else cannot support a temperature nobody measured.
    """

    context: dict = {"request_id": "req:m3:porto", "cancel_turn_id": "turn-porto"}
    lifecycle_ledger.register_required(context, request_text="and Porto?")
    failed_observation = {
        "schema": "tool_observation_v1",
        "intent": "live_data.weather_lookup",
        "ok": False,
        "status": "failed",
        "failure_class": "no observation returned",
        "source_count": 0,
    }
    unrelated_row = {
        "summary": "Wikipedia | Porto is the second-largest city in Portugal, on the Douro estuary.",
        "result_title": "Porto",
        "origin_domain": "en.wikipedia.org",
        "source_type": "web_derived",
    }
    added = lifecycle_ledger.record_typed_observations(
        context, entries=[failed_observation, unrelated_row]
    )
    assert added == 1, "the failed lookup must not mint an observation"
    model_context = dict(context)
    lifecycle_ledger.record_synthesis_call(model_context, model_call_id="mc-porto")
    lifecycle_ledger.record_claim_support(context, payload={}, model_authored=True)

    commit = publish(context, "Porto: Cloudy, high 22 C, low 17 C right now. Source: wttr.in.")
    served = str(commit["canonical_content"])
    assert "22 C" not in served and "17 C" not in served
    assert publication_of(commit)["state"] == "refused"


def test_prior_turn_evidence_cannot_support_this_turn():
    """Turn A's binding record, presented on turn B, is refused rather than consumed.

    The scope digest is RE-DERIVED from turn B's identity instead of trusted from the record,
    so a retry, a resume or a cache hit cannot re-present an earlier turn's evidence set.
    """

    turn_a: dict = {"request_id": "req:m3:A", "cancel_turn_id": "turn-A"}
    lifecycle_ledger.register_required(turn_a, request_text=RUST_REQUEST)
    scope_a = turn_scope(turn_a, task_id="task-A")
    record_a = binding_record(mint_evidence_set(RUST_ROWS, scope=scope_a, query=RUST_REQUEST))

    turn_b = build_turn(
        request_id="req:m3:B", turn_id="turn-B", task_id="task-B", foreign_binding=record_a
    )
    state = lifecycle_ledger.lifecycle_for_context(turn_b).as_dict()
    assert state["stages"]["bound_to_synthesis"] is False
    assert state["rejected_binding_count"] == 1

    commit = publish(turn_b, GROUNDED_ANSWER)
    assert publication_of(commit)["failed_stage"] == "bound_to_synthesis"
    assert "phoronix" not in str(commit["canonical_content"]).lower()


def test_a_grounded_answer_publishes_unchanged_with_its_links():
    """The success case, and the one most easily broken by a gate: nothing may be damaged.

    Byte-identical output, the source link intact, and the lifecycle recorded as published.
    """

    context = build_turn()
    commit = publish(context, GROUNDED_ANSWER)

    assert str(commit["canonical_content"]) == GROUNDED_ANSWER
    assert "https://www.phoronix.com/news/rust-coreutils-0-11" in str(commit["canonical_content"])
    publication = publication_of(commit)
    assert publication["state"] == "published"
    assert publication["coverage"] == "full"
    assert publication["evidence_set_id"].startswith("evset-")


# --------------------------------------------------------------------------- controls


@pytest.mark.parametrize(
    "content",
    [
        "Hey! I'm doing well, thanks for asking. How can I help?",
        "39 x 24 = 936.",
        "The second line of notes.txt is: BRAVO-LINE-TWO she jogged to the market",
        "Water freezes at 0 degrees Celsius at standard pressure.",
    ],
)
def test_direct_and_timeless_answers_are_untouched(content):
    """No lifecycle row means M1 never marked the turn current-information.

    These turns publish byte-identically and the commit carries no lifecycle at all -- the
    difference between "not applicable" and "not reached" is visible, and no claim matcher runs.
    """

    from core.semantic.semantic_admissions import bound_request_context

    _admit(content)
    with bound_request_context("req:m3:direct"):
        commit = finalize_answer(turn_id="turn-direct", canonical_content=content)

    assert str(commit["canonical_content"]) == content
    assert "grounding_lifecycle" not in commit


def test_a_runtime_composed_typed_observation_publishes_unchanged():
    """A weather render is code's output, not a generation, and code cannot fabricate a reading.

    Requirement 7: the lane's own successful observation mints the support. The discriminator is
    the ABSENCE of a model call on the turn, read from two independent recorders.
    """

    context: dict = {"request_id": "req:m3:weather", "cancel_turn_id": "turn-weather"}
    lifecycle_ledger.register_required(context, request_text="weather in Vilnius right now")
    lifecycle_ledger.record_typed_observations(
        context,
        entries=[
            {
                "schema": "tool_observation_v1",
                "intent": "live_data.weather_lookup",
                "ok": True,
                "status": "executed",
                "source_count": 1,
            }
        ],
    )
    rendered = "Vilnius: Clear, 17 C (today's high 19 C / low 13 C). Source: [wttr.in](https://wttr.in)."
    commit = publish(context, rendered)

    assert str(commit["canonical_content"]) == rendered
    publication = publication_of(commit)
    assert publication["state"] == "published"
    assert publication["support_origin"] == "typed_observation"


# --------------------------------------------------------------------------- record & shape


def test_the_commit_carries_every_lifecycle_stage_or_the_one_that_failed():
    """Requirement 11: Activity and the API read the stages off the commit, never re-derive them."""

    published = publication_of(publish(build_turn(), GROUNDED_ANSWER))
    assert published["failed_stage"] == ""

    context = build_turn(request_id="req:m3:stages", turn_id="turn-stages", bind=False)
    commit = publish(context, RUST_FABRICATION)
    record = lifecycle_of(commit)
    assert set(record["stages"]) == {
        "required",
        "retrieved",
        "bound_to_synthesis",
        "claim_supported",
        "published",
    }
    assert record["stages"]["required"] is True
    assert record["stages"]["retrieved"] is True
    assert record["failed_stage"] == "bound_to_synthesis"
    assert record["retrieval_outcome"] == "bound"


def test_activity_carries_the_publication_stage():
    """Requirement 11, the Activity half: the terminal stage is a durable runtime event.

    Finalization holds no context by design, so the event is addressed from the turn identity
    the lifecycle recorded at REQUIRED -- read back, never invented. Every earlier stage already
    emits its own row (`web_retrieval_started`/`completed`, `evidence_bound_to_synthesis`); this
    is the one that was missing, and it is the one that says what SHIPPED.
    """

    from core.runtime_continuity import list_runtime_session_events

    context = build_turn(request_id="req:m3:activity", turn_id="turn-activity", bind=False)
    publish(context, RUST_FABRICATION)

    rows = [
        event
        for event in list_runtime_session_events("s-m3")
        if str(event.get("event_type") or "") == "grounding_publication"
    ]
    assert rows, "the publication stage never reached Activity"
    details = rows[-1].get("details") or rows[-1]
    assert details.get("failed_stage") == "bound_to_synthesis"
    assert "required" in (details.get("stages_reached") or [])
    assert "retrieved" in (details.get("stages_reached") or [])


def test_gating_already_gated_bytes_is_a_fixed_point():
    """Finalization refuses different-content re-finalization, so the transform must not drift.

    A partial answer finalized a second time must produce the SAME bytes -- otherwise the second
    pass would strip its own notice and the commit authority would reject the turn.
    """

    first = publish(build_turn(), MIXED_ANSWER)
    served = str(first["canonical_content"])

    second = publish(
        build_turn(request_id="req:m3:again", turn_id="turn-again", task_id="task-again"), served
    )
    assert str(second["canonical_content"]) == served


def test_a_planned_parent_is_gated_on_every_merged_claim():
    """Requirement 9: children run on a copy of the parent's context and share its lifecycle.

    The merged bytes are gated as a whole, which is the only place a claim merged out of two
    children cannot slip between two separate accounts.
    """

    parent = build_turn(request_id="req:m3:plan", turn_id="turn-plan", task_id="task-plan")
    child = dict(parent)
    lifecycle_ledger.register_required(child, request_text="and the second part")
    assert child[lifecycle_ledger.LIFECYCLE_ID_KEY] == parent[lifecycle_ledger.LIFECYCLE_ID_KEY], (
        "a child minted its own lifecycle; the parent's evidence would be invisible at publication"
    )
    lifecycle_ledger.record_child_lifecycle(parent, child, task_index=1, request="and the second part")

    commit = publish(parent, MIXED_ANSWER)
    record = lifecycle_of(commit)
    assert record["children"] and record["children"][0]["same_lifecycle"] is True
    assert publication_of(commit)["state"] == "partial"


# --------------------------------------------------------------------------- sabotage

# Each sabotage runs inside this pack on EVERY execution and has to do two things: reproduce the
# defect (the bytes that must never ship, shipping) and red a NAMED invariant above. A sabotage
# that only reds something is not evidence -- a vacuous assertion, an unrelated import error and
# a genuinely bitten guard all look the same from the outside.


def _fabrication_is_refused() -> None:
    """The named invariant three of the four sabotages must break."""

    lifecycle_ledger.reset_for_tests()
    context = build_turn(request_id="req:m3:sab", turn_id="turn-sab", task_id="task-sab")
    commit = publish(context, RUST_FABRICATION)
    assert str(commit["canonical_content"]) != RUST_FABRICATION, "the fabrication published"


def _assert_named(check) -> str:
    """Run one named invariant and return the assertion it raised. Sabotage must break a NAME."""

    lifecycle_ledger.reset_for_tests()
    with pytest.raises(AssertionError) as caught:
        check()
    return str(caught.value)


def test_sabotage_receipt_laundering_reds_the_no_binding_invariant(monkeypatch):
    """S1: rows a RECEIPT accounts for stand in for rows an answer was made of.

    That conflation is the whole defect: on `b3f5117f` "a retrieval happened" and "this answer
    derives from it" were one fact with one record.
    """

    from core import grounding_publication

    def _receipt_is_evidence(lifecycle):
        return [dict(note) for note in lifecycle.retrieved_notes], "bound_evidence"

    monkeypatch.setattr(grounding_publication, "_support_rows", _receipt_is_evidence)

    lifecycle_ledger.reset_for_tests()
    unbound = build_turn(request_id="req:m3:s1", turn_id="turn-s1", task_id="task-s1", bind=False)
    leaked = publication_verdict(lifecycle_ledger.lifecycle_for_context(unbound), GROUNDED_ANSWER)
    assert leaked.state == "published", "the sabotage did not take"

    message = _assert_named(test_a_green_receipt_with_no_binding_supports_nothing)
    assert "bound_to_synthesis" in message or "canonical_content" in message, message


def test_sabotage_binding_id_mismatch_reds_the_ordering_invariant(monkeypatch):
    """S2: accept a model call that never named the bound set.

    The evidence-set id in the answering call's own prompt context is what distinguishes an
    answer written FROM the evidence from one written before it existed. Ignore the id and the
    recorded order of the measured defect (`model.call_completed` then `web_retrieval_started`)
    publishes again.
    """

    monkeypatch.setattr(
        lifecycle_ledger.GroundingLifecycle,
        "synthesis_referenced_bound_evidence",
        property(lambda self: bool(self.bound_evidence_set_id)),
    )

    lifecycle_ledger.reset_for_tests()
    unreferenced = build_turn(
        request_id="req:m3:s2", turn_id="turn-s2", task_id="task-s2", reference_binding=False
    )
    leaked = publication_verdict(
        lifecycle_ledger.lifecycle_for_context(unreferenced), GROUNDED_ANSWER
    )
    assert leaked.state == "published", "the sabotage did not take"

    message = _assert_named(test_an_answer_generated_before_its_retrieval_cannot_publish)
    assert "bound_to_synthesis" in message, message


def test_sabotage_claim_map_bypass_reds_the_fabrication_invariant(monkeypatch):
    """S3: read every coverage as full -- M4's per-claim map computed and then discarded."""

    import dataclasses

    from core import claim_support

    real = claim_support.match_claims

    def _always_full(**kwargs):
        return dataclasses.replace(real(**kwargs), coverage="full")

    monkeypatch.setattr("core.claim_support.match_claims", _always_full)

    message = _assert_named(_fabrication_is_refused)
    assert "the fabrication published" in message, message


def test_sabotage_commit_gate_bypass_reds_the_fabrication_invariant(monkeypatch):
    """S4: take the gate back out of the commit seam -- exactly the `b3f5117f` shape."""

    monkeypatch.setattr(
        "core.grounding_publication.gate_publishable_content",
        lambda content, turn_id="": (content, {}),
    )

    message = _assert_named(_fabrication_is_refused)
    assert "the fabrication published" in message, message


def test_provider_failure_notice_is_not_replaced_by_a_no_sources_answer_refusal():
    from core.grounding_publication import gate_publishable_content

    context = build_turn(rows=[], retrieve=False, bind=False, model_call=True)
    notice = "The selected provider refused this request before dispatch: price exceeds the approved limit."
    content, record = gate_publishable_content(notice, turn_id=context['cancel_turn_id'], runtime_notice=True)
    assert content == notice
    assert record['publication']['coverage'] == 'runtime_notice'
    assert record['publication']['state'] != 'published'


def test_finalization_preserves_typed_failure_while_retaining_failed_grounding_status():
    from core.semantic.semantic_admissions import bound_request_context

    context = build_turn(rows=[], retrieve=False, bind=False, model_call=True)
    notice = "The selected model returned no usable answer. No alternate model was used."
    _admit(notice)
    with bound_request_context(context['request_id']):
        commit = finalize_answer(
            turn_id=context['cancel_turn_id'], canonical_content=notice,
            source_context={**context, 'runtime_notice_not_an_answer': True},
        )
    assert commit['canonical_content'] == notice
    assert lifecycle_of(commit)['publication']['state'] != 'published'
