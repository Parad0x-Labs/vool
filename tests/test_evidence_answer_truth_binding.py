"""The model may be wrong; the durable execution trace must not be rewritten by prose afterwards.

The production failure these tests are built around. Activity recorded an attempted fetch of
`https://github.com/VOOL-ai/openclaw-skills`; the reply then told the user "I never tried GitHub."
A second observed reply asserted a *provider-attested* model when the runtime carried no
provider-attestation source of any kind.

Every test here drives the real collector against the real stores — `core.execution_records` and
the `runtime_session_events` Activity ledger — and asserts on the repaired prose, not on an
internal flag. A test that only checked a boolean would pass with the repair wording broken, which
is the half the user actually reads.
"""
from __future__ import annotations

import json
import uuid

import pytest

from core import execution_records
from core.agent_runtime.action_honesty_validator import enforce_final_action_honesty
from core.agent_runtime.evidence_claim_binder import (
    VERDICT_CONTRADICTED,
    VERDICT_SUPPORTED,
    VERDICT_UNPROVABLE,
    bind_response_to_evidence,
    question_covers_whole_conversation,
)
from core.runtime_evidence import ModelProvenance, TurnEvidence, collect_turn_evidence, host_of
from core.runtime_task_events import emit_runtime_event

GITHUB_URL = "https://github.com/VOOL-ai/openclaw-skills"


@pytest.fixture()
def session() -> str:
    """A session id unique per test, so no test can pass by inheriting another's records."""

    sid = f"evidence-{uuid.uuid4().hex[:12]}"
    execution_records.clear(sid)
    yield sid
    execution_records.clear(sid)


def _context(session_id: str, turn_id: str, **extra: object) -> dict[str, object]:
    return {"runtime_session_id": session_id, "cancel_turn_id": turn_id, **extra}


def _record_fetch(session_id: str, turn_id: str, *, url: str = GITHUB_URL, ok: bool = False, status: str = "404", generation: int = 0) -> None:
    """A real `web.fetch` record, shaped the way the live tool produces one.

    `web.fetch` declares no ToolClaim, so its record carries an EMPTY `resolved_target` and the URL
    survives only in the arguments. Any binder that reads the resolved target alone is blind to
    exactly the tool at the centre of the production failure, so the fixture reproduces that shape
    rather than a convenient one.
    """

    execution_records.record(
        session_id=session_id,
        intent="web.fetch",
        arguments={"url": url},
        ok=ok,
        status=status,
        source_context=_context(session_id, turn_id),
        generation=generation,
    )


# --- INVARIANT 1 + EXACT REGRESSION 1: durable receipts win over a denial ------------------------


def test_github_denial_is_contradicted_by_the_failed_fetch_receipt(session: str) -> None:
    """EXACT REGRESSION 1. tool=web.fetch, target=github.com/VOOL-ai/openclaw-skills, result=404.

    The reply "I never tried GitHub." must not survive. The repair must state the attempt AND its
    failure, because both are true and the failure is what the model used to talk itself out of the
    attempt having happened.
    """

    turn = "turn-1"
    _record_fetch(session, turn)

    evidence = collect_turn_evidence(session_id=session, source_context=_context(session, turn))
    outcome = bind_response_to_evidence("I never tried GitHub.", evidence)

    assert outcome.checked and outcome.contradicted
    assert [f.verdict for f in outcome.findings] == [VERDICT_CONTRADICTED]
    assert "never tried" not in outcome.response.lower()
    assert "github" in outcome.response.lower()
    assert "failed" in outcome.response.lower()


def test_a_failed_attempt_is_still_an_attempt(session: str) -> None:
    """INVARIANT 5. Failure erases the result, never the attempt.

    Guards the exact hole in the pre-existing layer: `execution_records.verify_claim` filters on
    `ok`, so a 404'd fetch was invisible to every check in the runtime.
    """

    turn = "turn-fail"
    _record_fetch(session, turn, ok=False, status="404")

    evidence = collect_turn_evidence(session_id=session, source_context=_context(session, turn))
    assert [a.ok for a in evidence.attempts] == [False]
    assert evidence.attempts_against_host("github")

    outcome = bind_response_to_evidence("No GitHub call occurred on this turn.", evidence)
    assert outcome.contradicted


def test_only_the_conflicting_sentence_is_replaced(session: str) -> None:
    """A reply with one false denial and four correct sentences loses the denial, not the reply."""

    turn = "turn-surgical"
    _record_fetch(session, turn)
    reply = (
        "Here is what I found in the workspace. "
        "The config lives in settings.toml and the loader reads it at import time. "
        "I never tried GitHub. "
        "The two call sites agree on the default."
    )

    evidence = collect_turn_evidence(session_id=session, source_context=_context(session, turn))
    outcome = bind_response_to_evidence(reply, evidence)

    assert "settings.toml and the loader reads it at import time" in outcome.response
    assert "The two call sites agree on the default." in outcome.response
    assert "I never tried GitHub." not in outcome.response


# --- EXACT REGRESSION 2: a failed read is still an attempted read --------------------------------


def test_read_file_denial_is_contradicted_by_the_errored_read(session: str) -> None:
    """EXACT REGRESSION 2. workspace.read_file attempted, result=error, denial must be repaired."""

    turn = "turn-read"
    execution_records.record(
        session_id=session,
        intent="workspace.read_file",
        arguments={"path": "notes/spec.md"},
        ok=False,
        status="error",
        source_context=_context(session, turn),
    )

    evidence = collect_turn_evidence(session_id=session, source_context=_context(session, turn))
    outcome = bind_response_to_evidence("I never tried to read the file.", evidence)

    assert outcome.contradicted
    assert "never tried to read" not in outcome.response.lower()
    assert "failed" in outcome.response.lower()


def test_a_denial_naming_the_file_is_bound_to_that_file(session: str) -> None:
    """CLASS B. The claim is about a NAMED target, so it binds to that target, not to any read."""

    turn = "turn-named"
    execution_records.record(
        session_id=session,
        intent="workspace.read_file",
        arguments={"path": "/Users/me/proj/notes/spec.md"},
        ok=False,
        status="error",
        source_context=_context(session, turn),
    )
    evidence = collect_turn_evidence(session_id=session, source_context=_context(session, turn))

    assert evidence.attempts_against_target("notes/spec.md")
    assert not evidence.attempts_against_target("other/unrelated.md")

    outcome = bind_response_to_evidence("I did not open notes/spec.md.", evidence)
    assert outcome.contradicted


# --- INVARIANT 2 + EXACT REGRESSION 3: absence is not proof --------------------------------------


def test_a_true_negative_is_accepted_when_the_account_is_complete(session: str) -> None:
    """EXACT REGRESSION 3. A complete authoritative turn record with no web actions.

    "I did not use the web" is TRUE here and must survive untouched. A truth binder that repairs
    correct sentences is a censor, not a verifier.
    """

    turn = "turn-local"
    execution_records.record(
        session_id=session,
        intent="workspace.read_file",
        arguments={"path": "README.md"},
        ok=True,
        source_context=_context(session, turn),
    )

    evidence = collect_turn_evidence(
        session_id=session,
        source_context=_context(session, turn),
        remote_account_complete=True,
    )
    outcome = bind_response_to_evidence("I did not use the web.", evidence)

    assert not outcome.applied
    assert outcome.response == "I did not use the web."
    assert [f.verdict for f in outcome.findings] == [VERDICT_SUPPORTED]


def test_the_same_negative_is_repaired_when_the_account_is_incomplete(session: str) -> None:
    """INVARIANT 2. The identical sentence, the identical (empty) evidence — only the completeness
    of the account differs, and that alone decides whether the negative may stand."""

    turn = "turn-unknown"
    evidence = collect_turn_evidence(
        session_id=session,
        source_context=_context(session, turn),
        remote_account_complete=False,
    )
    outcome = bind_response_to_evidence("I did not use the web.", evidence)

    assert outcome.applied and not outcome.contradicted
    assert [f.verdict for f in outcome.findings] == [VERDICT_UNPROVABLE]
    assert "cannot verify" in outcome.response.lower()


def test_an_unattributed_record_blocks_the_negative_even_with_a_complete_scope(session: str) -> None:
    """A record the runtime cannot place in a turn is a hole in the account.

    `can_prove_absence` must not answer yes while the runtime is holding evidence it cannot
    attribute — that is absence of knowledge presented as knowledge of absence.
    """

    turn = "turn-holes"
    execution_records.record(  # no source_context, so the record carries no turn stamp
        session_id=session,
        intent="web.fetch",
        arguments={"url": "https://example.com/x"},
        ok=True,
    )

    evidence = collect_turn_evidence(
        session_id=session,
        source_context=_context(session, turn),
        remote_account_complete=True,
    )
    assert evidence.unattributed_records >= 1
    assert not evidence.can_prove_absence("remote")

    outcome = bind_response_to_evidence("I did not use the web.", evidence)
    assert [f.verdict for f in outcome.findings] == [VERDICT_UNPROVABLE]


# --- INVARIANT 3 + EXACT REGRESSION 4: unsourced runtime facts -----------------------------------


def test_provider_attestation_without_a_source_is_rejected(session: str) -> None:
    """EXACT REGRESSION 4. requested=A, selected=A, provider_attested=None.

    "The provider says it served A" asserts a second, independent source that does not exist. The
    repair must keep the fact the runtime DOES have (its own selection) and drop the one it does not.
    """

    turn = "turn-attest"
    evidence = collect_turn_evidence(
        session_id=session,
        source_context=_context(session, turn, requested_model="model-a"),
        provenance={"requested_model": "model-a", "runtime_selected_model": "model-a"},
    )
    assert not evidence.provenance.has_provider_attestation

    outcome = bind_response_to_evidence("The provider says it served model-a.", evidence)
    assert outcome.contradicted
    assert "provider says" not in outcome.response.lower()
    assert "model-a" in outcome.response
    assert "do not have independent provider attestation" in outcome.response.lower()


def test_the_accepted_wording_states_selection_without_claiming_attestation(session: str) -> None:
    """EXACT REGRESSION 4, accept half. The honest sentence must pass through untouched."""

    turn = "turn-attest-ok"
    evidence = collect_turn_evidence(
        session_id=session,
        source_context=_context(session, turn),
        provenance={"runtime_selected_model": "model-a"},
    )
    honest = "The runtime selected model-a, but I do not have independent provider attestation."
    outcome = bind_response_to_evidence(honest, evidence)

    assert not outcome.applied
    assert outcome.response == honest


def test_attestation_is_accepted_once_relay_supplies_a_source(session: str) -> None:
    """The Relay contract, exercised against a fake provenance object.

    Relay owns provider parsing; this layer owns the rule that the field needs a named voucher.
    Supplying both makes the same sentence legitimate, with no code change here.
    """

    turn = "turn-relay"
    evidence = collect_turn_evidence(
        session_id=session,
        source_context=_context(session, turn),
        provenance={
            "requested_model": "model-a",
            "runtime_selected_model": "model-a",
            "provider_attested_model": "model-a",
            "attestation_source": "relay.provider_response",
        },
    )
    assert evidence.provenance.has_provider_attestation

    outcome = bind_response_to_evidence("The provider says it served model-a.", evidence)
    assert not outcome.applied
    assert [f.verdict for f in outcome.findings] == [VERDICT_SUPPORTED]


def test_a_model_id_with_no_voucher_is_not_an_attestation() -> None:
    """The laundering path, closed. Copying the selected model into the attested field must not
    manufacture a second independent source out of the first one."""

    assert not ModelProvenance(
        runtime_selected_model="model-a", provider_attested_model="model-a"
    ).has_provider_attestation
    assert ModelProvenance(
        provider_attested_model="model-a", attestation_source="relay.provider_response"
    ).has_provider_attestation


def test_runtime_selected_model_is_read_from_the_lane_proof(session: str) -> None:
    """CLASS C. Selection comes from the router's own record of what it actually invoked.

    `actual_adapter_model_id` is read off the manifest that answered and is kept apart from
    `planned_model_id` precisely so a substitution stays visible; the evidence layer must read the
    actual one, not the plan.
    """

    turn = "turn-lane"
    context = _context(session, turn)
    emit_runtime_event(
        context,
        event_type="model_lane_proof",
        message="lane proof",
        details={"planned_model_id": "planned-model", "actual_adapter_model_id": "served-model"},
    )
    evidence = collect_turn_evidence(session_id=session, source_context=context)
    assert evidence.provenance.runtime_selected_model == "served-model"


# --- INVARIANT 4 + EXACT REGRESSION 5: attempt generations ---------------------------------------


def test_retry_generations_keep_their_chronology(session: str) -> None:
    """EXACT REGRESSION 5. Attempt 1 tried GitHub and failed; attempt 2 read the workspace and
    succeeded. "No GitHub attempt occurred" must fall, and the repair must place the GitHub attempt
    in the EARLIER generation rather than flattening the turn into one undated blur."""

    turn = "turn-retry"
    _record_fetch(session, turn, ok=False, status="404", generation=1)
    execution_records.record(
        session_id=session,
        intent="workspace.read_file",
        arguments={"path": "README.md"},
        ok=True,
        source_context=_context(session, turn),
        generation=2,
    )

    evidence = collect_turn_evidence(session_id=session, source_context=_context(session, turn))
    assert evidence.generations(evidence.attempts) == (1, 2)

    outcome = bind_response_to_evidence("No GitHub attempt occurred.", evidence)
    assert outcome.contradicted
    assert "generation 1" in outcome.response
    assert "earlier attempt" in outcome.response.lower()


def test_a_single_generation_does_not_invent_an_ordering(session: str) -> None:
    """The other side of the same rule: with one generation there is no earlier/later to report,
    and claiming one would be a fabricated ordering."""

    turn = "turn-single"
    _record_fetch(session, turn, generation=1)
    evidence = collect_turn_evidence(session_id=session, source_context=_context(session, turn))
    outcome = bind_response_to_evidence("I never tried GitHub.", evidence)

    assert outcome.contradicted
    assert "earlier attempt" not in outcome.response.lower()


# --- INVARIANT 4 + EXACT REGRESSION 6: turn and conversation isolation ---------------------------


def test_prior_turn_evidence_is_not_bound_to_a_question_about_this_turn(session: str) -> None:
    """EXACT REGRESSION 6. Turn N called GitHub; turn N+1 did not; the question is about now.

    Records are kept per session and nothing clears them between turns, so without turn identity
    this is the default failure — last turn's fetch answering this turn's question.
    """

    _record_fetch(session, "turn-N")
    execution_records.record(
        session_id=session,
        intent="workspace.read_file",
        arguments={"path": "README.md"},
        ok=True,
        source_context=_context(session, "turn-N+1"),
    )

    evidence = collect_turn_evidence(
        session_id=session,
        source_context=_context(session, "turn-N+1"),
        remote_account_complete=True,
    )
    assert [a.intent for a in evidence.attempts] == ["workspace.read_file"]
    assert not evidence.attempts_against_host("github")

    outcome = bind_response_to_evidence("I did not use the web on this turn.", evidence)
    assert not outcome.applied


def test_a_question_about_the_conversation_may_use_the_earlier_turn(session: str) -> None:
    """EXACT REGRESSION 6, other half. Isolation cuts both ways: refusing to look past this turn
    when the user asked about the whole session is the same error in the opposite direction."""

    _record_fetch(session, "turn-N")
    evidence = collect_turn_evidence(
        session_id=session,
        source_context=_context(session, "turn-N+1"),
        remote_account_complete=True,
    )

    assert question_covers_whole_conversation("In this whole conversation, did you ever call GitHub?")
    assert not question_covers_whole_conversation("Did you call GitHub on this turn?")

    outcome = bind_response_to_evidence("I never called GitHub.", evidence, whole_conversation=True)
    assert outcome.contradicted


def test_the_impersonal_passive_denial_from_the_live_drive_is_caught(session: str) -> None:
    """The production defect reproducing verbatim on the live acceptance drive, 2026-08-07.

    A `web.fetch` to github.com was attempted and failed on turn 1; asked about it on turn 2,
    qwen3:8b answered "No external sources were accessed in this conversation." The first two
    denial forms both missed it — no first-person subject for the verb form, and "sources ... were
    accessed" is not an event-noun clause — so the sentence shipped to the user contradicting the
    trace. The wording is pinned here because it is what a real model actually produced, not what
    a regex author imagined it would produce.
    """

    _record_fetch(session, "turn-1")
    evidence = collect_turn_evidence(
        session_id=session,
        source_context=_context(session, "turn-2"),
        remote_account_complete=True,
    )
    outcome = bind_response_to_evidence(
        "No external sources were accessed in this conversation.", evidence
    )

    assert outcome.contradicted, "the live-drive phrasing must not slip past the binder"
    assert "no external sources were accessed" not in outcome.response.lower()
    assert "github" in outcome.response.lower()


def test_the_claim_sentence_scope_outranks_the_question_scope(session: str) -> None:
    """A claim that names its own window is a claim about that window.

    The same evidence, the same question, two claim sentences: the one scoped to the conversation
    is contradicted by the earlier turn, the one scoped to this turn is not.
    """

    _record_fetch(session, "turn-1")
    evidence = collect_turn_evidence(
        session_id=session,
        source_context=_context(session, "turn-2"),
        remote_account_complete=True,
    )

    widened = bind_response_to_evidence("I never used the web in this conversation.", evidence)
    assert widened.contradicted

    narrowed = bind_response_to_evidence("I never used the web on this turn.", evidence)
    assert not narrowed.applied


def test_an_over_broad_negative_still_reports_the_provable_half(session: str) -> None:
    """Measured on the live drive, 2026-08-07: a turn that was provably web-free, answered with a
    claim about the whole CONVERSATION. The runtime cannot prove a conversation-wide negative — no
    per-turn completeness marker survives across turns — but it can prove the turn's, and answering
    only "I cannot verify" threw away a fact it actually held.
    """

    turn = "turn-overbroad"
    evidence = collect_turn_evidence(
        session_id=session,
        source_context=_context(session, turn),
        remote_account_complete=True,
    )
    outcome = bind_response_to_evidence(
        "No external sources were accessed in this conversation.", evidence
    )

    assert outcome.applied and not outcome.contradicted
    assert "on this turn there was no web call" in outcome.response.lower()
    assert "cannot establish that for the earlier turns" in outcome.response.lower()


def test_the_agent_named_in_third_person_still_counts_as_a_self_report(session: str) -> None:
    """This product's replies routinely say "VOOL did not ..." rather than "I did not ..."."""

    turn = "turn-third-person"
    _record_fetch(session, turn)
    evidence = collect_turn_evidence(session_id=session, source_context=_context(session, turn))
    assert bind_response_to_evidence("VOOL did not access github.com.", evidence).contradicted


def test_a_fetch_past_the_activity_window_still_contradicts_the_denial(session: str) -> None:
    """SENTINEL FINDING 1. The Activity read took the OLDEST bounded window, not the newest.

    `list_runtime_session_events(after_seq=0, limit=200)` orders by `seq ASC`, so on a session past
    200 events it returned the opening rows and nothing since. A GitHub fetch at row 205 of 400 was
    invisible, and the denial it should have contradicted passed — on exactly the long sessions
    where a user is most likely to ask "did you actually try that?".
    """

    turn = "turn-long-session"
    context = _context(session, turn)
    for index in range(204):
        emit_runtime_event(context, event_type="status", message=f"filler {index}", details={})
    emit_runtime_event(
        context,
        event_type="tool_failed",
        message=f"Web fetch for `{GITHUB_URL}` failed with HTTP 404.",
        details={"tool_name": "web.fetch"},
    )
    for index in range(195):
        emit_runtime_event(context, event_type="status", message=f"trailer {index}", details={})

    evidence = collect_turn_evidence(session_id=session, source_context=context)
    assert evidence.attempts_against_host("github"), "the newest window must reach the fetch"

    outcome = bind_response_to_evidence("I never tried GitHub.", evidence)
    assert outcome.contradicted


def test_prior_turn_evidence_is_not_described_as_this_turn(session: str) -> None:
    """SENTINEL FINDING 2. The repair asserted "the durable execution record for this turn" even
    when the attempt was two turns back and the current turn's record was empty — replacing one
    false claim about the trace with another."""

    _record_fetch(session, "turn-1")
    evidence = collect_turn_evidence(
        session_id=session,
        source_context=_context(session, "turn-9"),
        remote_account_complete=True,
    )
    served = bind_response_to_evidence(
        "I never tried GitHub in this conversation.", evidence
    ).response

    assert "for this turn" not in served
    assert "an earlier turn in this conversation" in served


def test_current_turn_evidence_still_says_this_turn(session: str) -> None:
    """The other half of finding 2: when the attempt IS on this turn, say so."""

    turn = "turn-now"
    _record_fetch(session, turn)
    evidence = collect_turn_evidence(session_id=session, source_context=_context(session, turn))
    served = bind_response_to_evidence("I never tried GitHub.", evidence).response

    assert "durable execution record for this turn" in served
    assert "an earlier turn" not in served


@pytest.mark.parametrize("first_ok,second_ok", [(False, True), (True, False)])
def test_two_attempts_on_one_url_survive_an_unknown_generation(
    session: str, first_ok: bool, second_ok: bool
) -> None:
    """SENTINEL FINDING 3. Identity was `(intent, target, generation)`, which is not an identity
    when the generation is unknown: two real fetches of one URL landed in the same bucket, merged,
    and "failure wins" reported the pair as a single failed call. Chronology the runtime genuinely
    had was thrown away because a field it did not have was zero for both.

    Parametrised in both orders so the fix cannot be a rule about which outcome comes first.
    """

    turn = "turn-two-fetches"
    _record_fetch(session, turn, ok=first_ok, status="" if first_ok else "404", generation=0)
    _record_fetch(session, turn, ok=second_ok, status="" if second_ok else "404", generation=0)

    evidence = collect_turn_evidence(session_id=session, source_context=_context(session, turn))
    attempts = evidence.attempts_against_host("github")

    assert len(attempts) == 2, "two distinct calls must not collapse into one"
    assert [a.ok for a in attempts] == [first_ok, second_ok], "chronology must be preserved"
    assert all(a.generation == 0 for a in attempts), "no generation number may be invented"
    assert bind_response_to_evidence("I never tried GitHub.", evidence).contradicted


def test_one_call_reported_by_both_stores_still_collapses_without_a_generation(session: str) -> None:
    """The control for finding 3: ordinal identity must not stop cross-store dedupe.

    One fetch, recorded in memory AND written to Activity, with no generation on either side, is
    still one attempt — otherwise the fix for double-counting would have traded itself away.
    """

    turn = "turn-one-call-two-stores"
    context = _context(session, turn)
    _record_fetch(session, turn, ok=False, status="404", generation=0)
    emit_runtime_event(
        context,
        event_type="tool_failed",
        message=f"Web fetch for `{GITHUB_URL}` failed with HTTP 404.",
        details={"tool_name": "web.fetch"},
    )

    evidence = collect_turn_evidence(session_id=session, source_context=context)
    assert len(evidence.attempts) == 1


@pytest.mark.parametrize(
    "denial",
    [
        "VOOL made no network calls to GitHub.",
        "At no point did I contact github.com.",
        "I made no requests to github.com.",
        "Never did we reach github.com.",
    ],
)
def test_natural_denial_phrasings_are_caught(session: str, denial: str) -> None:
    """SENTINEL FINDING 6. Two ordinary English forms were unreachable: the quantifier on the
    OBJECT ("made no network calls") and a fronted negation ("At no point did I ...")."""

    turn = "turn-phrasings"
    _record_fetch(session, turn)
    evidence = collect_turn_evidence(session_id=session, source_context=_context(session, turn))
    assert bind_response_to_evidence(denial, evidence).contradicted, f"missed: {denial!r}"


def test_an_anaphoric_object_is_left_alone_rather_than_guessed_at(session: str) -> None:
    """"Never did we fetch that page." is DETECTED as a denial and then deliberately dropped.

    "That page" names no checkable subject — it points at something earlier in the conversation
    that this layer does not resolve. Binding it to whichever URL the turn happens to hold would be
    a guess dressed as evidence, and the first time the anaphor referred to a different page the
    repair would assert a contradiction that does not exist. Stated as a boundary, not smuggled in.
    """

    turn = "turn-anaphora"
    reply = "Never did we fetch that page."
    _record_fetch(session, turn)
    evidence = collect_turn_evidence(session_id=session, source_context=_context(session, turn))
    assert bind_response_to_evidence(reply, evidence).response == reply


@pytest.mark.parametrize(
    "reply",
    [
        "The build makes no network calls to GitHub.",
        "This module made no assumptions about the caller.",
        "At no point does the parser allocate.",
        "There are no network calls in the hot path.",
    ],
)
def test_the_new_phrasings_do_not_catch_statements_about_other_subjects(
    session: str, reply: str
) -> None:
    """The widened patterns stay gated on the AGENT as subject. A sentence about the build, the
    module or the parser is not a report of what this runtime did."""

    turn = "turn-phrasings-control"
    _record_fetch(session, turn)
    evidence = collect_turn_evidence(session_id=session, source_context=_context(session, turn))
    assert bind_response_to_evidence(reply, evidence).response == reply, f"rewrote: {reply!r}"


def test_another_conversation_is_never_read(session: str) -> None:
    """INVARIANT 4. Evidence is keyed by session; a second conversation's Activity is not evidence."""

    other = f"{session}-other"
    execution_records.clear(other)
    try:
        _record_fetch(other, "turn-1")
        evidence = collect_turn_evidence(session_id=session, source_context=_context(session, "turn-1"))
        assert evidence.attempts == ()
        assert evidence.conversation_attempts == ()
    finally:
        execution_records.clear(other)


def test_a_turn_that_cannot_be_identified_is_not_judged(session: str) -> None:
    """Fails open. With no turn id there is no window to judge against, and rewriting every denial
    on a lane that carries no turn id would be a false-positive machine."""

    _record_fetch(session, "turn-1")
    evidence = collect_turn_evidence(session_id=session, source_context={"runtime_session_id": session})
    outcome = bind_response_to_evidence("I never tried GitHub.", evidence)

    assert not outcome.checked
    assert outcome.response == "I never tried GitHub."


# --- Durable Activity is evidence on its own -----------------------------------------------------


def test_the_durable_activity_ledger_alone_contradicts_the_denial(session: str) -> None:
    """INVARIANT 1 names Activity specifically. The in-memory records die with the process; the
    Activity row is what the user is looking at when they say "but it says you tried"."""

    turn = "turn-activity"
    context = _context(session, turn)
    emit_runtime_event(
        context,
        event_type="tool_failed",
        message="fetch failed",
        details={"tool_name": "web.fetch", "url": GITHUB_URL, "status": "404"},
    )

    evidence = collect_turn_evidence(session_id=session, source_context=context)
    assert any(a.source == "activity" for a in evidence.attempts)

    outcome = bind_response_to_evidence("I never tried GitHub.", evidence)
    assert outcome.contradicted


def test_the_url_is_recovered_from_the_activity_message(session: str) -> None:
    """The shape a live daemon actually writes, captured 2026-08-07.

    The `tool_failed` row for the GitHub fetch carries NO url/target detail key — every one of them
    is absent — and the address survives only inside the human-readable message. Read from the
    detail keys alone, the durable half of the evidence degrades to "some web.fetch failed", which
    cannot answer a denial that names GitHub. That half is also all that is left once a restart
    clears the in-memory records, so this is the path the invariant actually depends on.
    """

    turn = "turn-message-url"
    context = _context(session, turn)
    emit_runtime_event(
        context,
        event_type="tool_failed",
        message=f"Web fetch for `{GITHUB_URL}` failed with HTTP 404.",
        details={"tool_name": "web.fetch"},
    )

    evidence = collect_turn_evidence(session_id=session, source_context=context)
    assert [a.host for a in evidence.attempts] == ["github"]
    assert bind_response_to_evidence("I never tried GitHub.", evidence).contradicted


def test_a_dispatch_row_is_not_reported_as_a_success(session: str) -> None:
    """A `tool_selected` records that a call went out, not that it worked.

    On real rows the pair read as "tried web.fetch — succeeded, and github — failed": the same
    fetch counted twice, and its dispatch half described as a success beside its own failure.
    """

    turn = "turn-dispatch"
    context = _context(session, turn)
    emit_runtime_event(
        context, event_type="tool_selected", message="Running web.fetch", details={"tool_name": "web.fetch"}
    )
    emit_runtime_event(
        context,
        event_type="tool_failed",
        message=f"Web fetch for `{GITHUB_URL}` failed with HTTP 404.",
        details={"tool_name": "web.fetch"},
    )

    evidence = collect_turn_evidence(session_id=session, source_context=context)
    assert len(evidence.attempts) == 1, "the two halves of one fetch must not read as two calls"

    served = bind_response_to_evidence("I never tried GitHub.", evidence).response
    assert "succeeded" not in served
    assert served.count("github") == 1


def test_a_lone_dispatch_row_reports_an_unknown_outcome(session: str) -> None:
    """With no completion row the outcome is genuinely unknown, and saying "succeeded" would be a
    claim the trace never made."""

    turn = "turn-lone-dispatch"
    context = _context(session, turn)
    emit_runtime_event(
        context,
        event_type="tool_selected",
        message=f"Running web.fetch for {GITHUB_URL}",
        details={"tool_name": "web.fetch"},
    )
    evidence = collect_turn_evidence(session_id=session, source_context=context)
    served = bind_response_to_evidence("I never tried GitHub.", evidence).response

    assert "outcome not recorded" in served
    assert "succeeded" not in served


def test_one_call_recorded_in_both_stores_is_one_attempt(session: str) -> None:
    """The two stores overlap by design; the repair must not report the same fetch twice."""

    turn = "turn-dedupe"
    context = _context(session, turn)
    _record_fetch(session, turn, ok=False, status="404")
    emit_runtime_event(
        context,
        event_type="tool_failed",
        message="fetch failed",
        details={"tool_name": "web.fetch", "url": GITHUB_URL, "status": "404"},
    )

    evidence = collect_turn_evidence(session_id=session, source_context=context)
    assert len(evidence.attempts) == 1


# --- No overreach --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "reply",
    [
        "SOL is not a good store of value right now.",
        "The parser does not handle nested quotes correctly.",
        "I don't think that refactor is worth it.",
        "You never called the endpoint with a valid token.",
        "I can't browse arbitrary pages in this build.",
        "If you want, I could fetch that page for you.",
        "The build does not use GitHub Actions.",
        "No configuration changes are required for this to work.",
        "There is no cache layer in front of the resolver.",
        "None of the tests cover the retry path.",
    ],
)
def test_ordinary_prose_is_left_alone(session: str, reply: str) -> None:
    """This lane binds runtime-observable facts. It does not fact-check findings, police opinions,
    rewrite capability statements, or judge anything outside the runtime."""

    turn = "turn-prose"
    _record_fetch(session, turn)
    evidence = collect_turn_evidence(session_id=session, source_context=_context(session, turn))
    outcome = bind_response_to_evidence(reply, evidence)

    assert outcome.response == reply, f"rewrote prose it has no authority over: {reply!r}"


def test_host_extraction_rejects_things_that_are_not_hosts() -> None:
    assert host_of(GITHUB_URL) == "github"
    assert host_of("github.com/VOOL-ai") == "github"
    assert host_of("https://api.github.com/repos") == "github"
    assert host_of("/Users/me/notes.md") == ""
    assert host_of("~/Desktop/x") == ""
    assert host_of("./relative/path") == ""
    assert host_of("") == ""


def test_the_binder_never_raises_on_a_malformed_evidence_object() -> None:
    """A verification layer that can break a turn is a worse bug than the one it catches."""

    empty = TurnEvidence(turn_id="t")
    assert bind_response_to_evidence("", empty).response == ""
    assert bind_response_to_evidence("I never tried GitHub.", TurnEvidence()).checked is False


# --- End-to-end through the real validator entry point -------------------------------------------


def test_the_validator_repairs_the_denial_and_records_the_verdict(session: str) -> None:
    """The whole path the runtime actually takes: `enforce_final_action_honesty` is on every return
    path a turn has, so the check has to work from there and not only from the binder in isolation."""

    turn = "turn-e2e"
    _record_fetch(session, turn)

    out = enforce_final_action_honesty(
        {"response": "I never tried GitHub.", "confidence": 1.0},
        user_input="Did you try that external source?",
        session_id=session,
        source_context=_context(session, turn),
    )

    assert "never tried" not in str(out["response"]).lower()
    assert "github" in str(out["response"]).lower()
    assert out["route_reason"] == "evidence_contradiction_repaired"
    assert out["evidence_binding"]["contradicted"] is True
    assert out["confidence"] <= 0.5


def test_a_second_validator_pass_does_not_downgrade_a_proven_negative(session: str) -> None:
    """`enforce_final_action_honesty` runs twice on the /api/chat lane.

    The first pass runs inside `remote_fetch_policy_scope`, where the turn's remote account is
    readable; the second runs after that scope has closed, where it is not. Measured live: the
    second pass rewrote a correct "No, I did not use the web on this turn." into "I cannot verify"
    — a true statement turned into a hedge by nothing but call ordering. What the first pass proved
    must survive into the second.
    """

    turn = "turn-two-passes"
    reply = "No, I did not use the web on this turn."
    first = enforce_final_action_honesty(
        {"response": reply, "confidence": 1.0, "evidence_scope": {"remote_account_complete": True}},
        user_input="did you use the web?",
        session_id=session,
        source_context=_context(session, turn),
    )
    assert first["response"] == reply

    # The second pass sees no live scope at all — exactly the service-layer vantage point.
    second = enforce_final_action_honesty(
        dict(first),
        user_input="did you use the web?",
        session_id=session,
        source_context=_context(session, turn),
    )
    assert second["response"] == reply, "the second pass must not un-prove what the first proved"


def test_a_later_turn_does_not_inherit_the_previous_turn_s_remote_scope(session: str) -> None:
    """SENTINEL FINDING 4. Deleting `_REMOTE_FETCH_SCOPE_ACTIVE.reset(scope_token)` left the suite
    green, so nothing was holding the scope's exit.

    That reset is the entire difference between "the runtime counted, and counted zero" and "nobody
    was counting". Leaked, the flag stays true for every later turn in the process, and each one
    silently gains the right to prove a negative it never measured. Turn A opens the scope and
    exits; turn B, outside any scope, must not be able to prove anything.
    """

    from core.remote_fetch_policy import (
        remote_fetch_attempt_count,
        remote_fetch_policy_scope,
        remote_fetch_scope_active,
    )
    from core.runtime_evidence import remote_account_is_complete

    assert not remote_fetch_scope_active(), "no scope should be open before turn A"

    with remote_fetch_policy_scope({}):  # turn A
        assert remote_fetch_scope_active()
        assert remote_fetch_attempt_count() == 0
        assert remote_account_is_complete(), "inside the scope a zero count is a real proof"

    # Turn B: the scope has exited. Nothing is counting, so a zero count proves nothing.
    assert not remote_fetch_scope_active(), "the scope flag leaked past its own context manager"
    assert not remote_account_is_complete()

    evidence = collect_turn_evidence(
        session_id=session, source_context=_context(session, "turn-B")
    )
    outcome = bind_response_to_evidence("I did not use the web on this turn.", evidence)
    assert [f.verdict for f in outcome.findings] == [VERDICT_UNPROVABLE], (
        "turn B inherited a completeness proof it never earned"
    )


def test_one_completed_turn_writes_exactly_one_honesty_receipt(tmp_path, monkeypatch) -> None:
    """SENTINEL FINDING 5. Reintroducing the deleted HTTP-boundary emitter left the suite green.

    The defect was two emitters on one lane: `apps/vool_agent.py:_guard_final_result` signs a
    receipt for every finalized turn, and a second emit at the HTTP boundary wrote the same turn
    into the hash-chained ledger again — identical prompt and response hashes at consecutive turn
    indices. A duplicated entry in a tamper-evident ledger is not a spare row; it makes the chain
    disagree with the turn count anyone would reconcile it against, and nothing detected it.

    Driven through the real `/api/chat` dispatch. The agent is stubbed — as the whole API suite
    stubs it — with a stand-in that performs the SAME two finalization steps the real agent's
    `_guard_final_result` performs, so what is under test is whether the HTTP boundary adds a
    receipt on top of the one the agent already wrote.

    Boundary, stated rather than implied: this guards the HTTP side. Removing the emit inside
    `apps/vool_agent.py` (a file this lane does not own) would not redden it.
    """

    from apps.vool_api_server import _dispatch_post
    from core.agent_runtime.action_honesty_validator import (
        emit_turn_honesty_receipt,
        enforce_final_action_honesty,
    )
    from core.honesty_receipt import list_honesty_receipts, verify_honesty_chain
    from core.web.api.runtime import RuntimeServices

    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    pinned = "evidence-receipt-count-session"

    def agent_that_finalizes_like_the_real_one(
        user_text: str,
        *,
        session_id: str | None = None,
        source_context: dict | None = None,
    ) -> dict:
        guarded = enforce_final_action_honesty(
            {"response": "Done.", "confidence": 1.0},
            user_input=user_text,
            effective_input=user_text,
            session_id=session_id,
            source_context=source_context,
        )
        return emit_turn_honesty_receipt(
            guarded, user_input=user_text, session_id=session_id, source_context=source_context
        )

    import unittest.mock as _mock

    with _mock.patch(
        "apps.vool_api_server._run_agent", side_effect=agent_that_finalizes_like_the_real_one
    ):
        response = _dispatch_post(
            path="/api/chat",
            body={
                "model": "vool",
                "stream": False,
                "session_id": pinned,
                "turn_id": "receipt-turn-1",
                "messages": [{"role": "user", "content": "do the thing"}],
            },
            headers={"content-type": "application/json"},
            runtime=RuntimeServices(display_name="VOOL"),
            model_name="vool",
            workspace_root_provider=lambda: str(tmp_path),
        )
    assert response.status == 200

    session_id = json.loads(response.body.decode("utf-8"))["vool_session_id"]
    receipts = list_honesty_receipts(session_id)
    assert len(receipts) == 1, (
        f"one completed turn must write exactly one honesty receipt, got {len(receipts)}: "
        f"{[(r.get('turn_index'), str(r.get('prompt_hash'))[:8]) for r in receipts]}"
    )
    assert verify_honesty_chain(receipts) == (True, "ok")


def test_the_validator_leaves_a_supported_answer_untouched(session: str) -> None:
    turn = "turn-e2e-ok"
    execution_records.record(
        session_id=session,
        intent="workspace.read_file",
        arguments={"path": "README.md"},
        ok=True,
        source_context=_context(session, turn),
    )
    reply = "I read README.md and it documents the install steps."
    out = enforce_final_action_honesty(
        {"response": reply, "confidence": 1.0},
        user_input="what does the readme say?",
        session_id=session,
        source_context=_context(session, turn),
    )
    assert out["response"] == reply
    assert "evidence_binding" not in out
