"""F2 OWNER BOUNDARY: what a semantic turn proof will and will not accept.

The four mechanisms this replaced all failed toward OWNING text -- `ClauseKind.UNKNOWN` read as
permission, a coordinator enumeration deciding where a list continued, a `[^.;?!]+?` tail deciding
where one ended, and two regex vocabularies deciding polarity. So every test here is written the
other way round: the interesting assertion is what does NOT become a role.

Both proof paths are exercised on their own terms. The formal grammars are asserted to ABSTAIN
outside their closed syntax, which is the property that keeps them from growing into the vocabulary
they replaced. The bounded path is driven through the real `parse_proposal` -> `validate_frame`
pipeline with a proposer standing in for the model, so a proposal that paraphrases, invents,
mis-numbers a list or omits a polarity is refused here rather than downstream.
"""
from __future__ import annotations

import pytest

from core.conductor.semantic_proof import (
    AbstentionReason,
    EvidenceStatus,
    Polarity,
    ProofProvenance,
    ProvenFrame,
    SemanticFrameEvidence,
    SemanticRoleEvidence,
    formal_grammar_evidence,
    frame_contract,
    prove_turn,
    validate_frame,
)
from core.semantic.canonical_text import CanonicalText
from tests.semantic_proposer import (
    coordinated,
    failing_proposer,
    frame,
    proposer,
    raw_proposer,
    role,
)


def _canonical(text: str) -> CanonicalText:
    return CanonicalText.of(text)


def _families(proof) -> list[str]:
    return [f.family for f in proof.frames]


def _roles(proof, family: str, role_name: str) -> list[str]:
    return [
        r.surface
        for f in proof.frames
        if f.family == family
        for r in f.roles
        if r.role_name == role_name
    ]


# --- proof path 1: the closed formal grammars ---------------------------------------------------


@pytest.mark.parametrize(
    "text,family,expected",
    [
        ("What is 137 x 29?", "calculation", "137 x 29"),
        ("compute 1,024 / 8 for me", "calculation", "1,024 / 8"),
        ("500 EUR to JPY", "fx_quote", "500 EUR to JPY"),
        ("EUR into JPY please", "fx_quote", "EUR into JPY"),
        ("region: eu-west-1", "structured_field", "region: eu-west-1"),
    ],
)
def test_the_formal_grammars_prove_their_own_closed_syntax(text, family, expected):
    proof = prove_turn(text)
    frames = [f for f in proof.frames if f.family == family]
    assert len(frames) == 1, f"{text!r} produced {_families(proof)}"
    assert frames[0].surface == expected
    assert frames[0].provenance is ProofProvenance.FORMAL_GRAMMAR


@pytest.mark.parametrize(
    "text",
    [
        "weather in Oslo and Tromso",
        "the price of Gold and Silver",
        "find a locksmith in Porto",
        "open core/conductor/planner.py and summarise it",
        "tell me about the Renault Laguna",
        "what happened in Q3",
        "https://example.com/report",
        "the blue folder",
    ],
)
def test_the_formal_grammars_abstain_outside_their_declared_syntax(text):
    """THE ANTI-VOCABULARY PROPERTY. No noun, no gazetteer, no verb list -- so no frame.

    This is what stops the formal path from regrowing into the thing it replaced. A closed grammar
    that started recognising places would be an alias table with a different name.
    """
    proof = prove_turn(text)
    assert proof.frames == (), f"{text!r} was claimed by a formal grammar: {_families(proof)}"
    assert proof.unclaimed_spans, "unclaimed text must stay visible, not disappear"


def test_a_formal_frame_never_reaches_for_a_noun_beside_it():
    """NEGATIVE CONTROL. Arithmetic in a sentence claims the arithmetic and nothing else."""
    proof = prove_turn("Work out 12 x 5 for the Vilnius office and email Dalia about it.")
    assert _families(proof) == ["calculation"]
    assert _roles(proof, "calculation", "expression") == ["12 x 5"]
    unclaimed = " ".join(proof.canonical.text[a:b] for a, b in proof.unclaimed_spans)
    assert "Vilnius" in unclaimed and "Dalia" in unclaimed


@pytest.mark.parametrize(
    "text",
    ["XYZ to ABC", "convert this CSV to XML for me", "convert JPG to PNG", "turn PDF into TXT"],
)
def test_three_capitals_with_a_preposition_are_not_a_conversion(text):
    """MEMBERSHIP is what makes this closed syntax, and it is checked against ISO 4217.

    The shape test that shipped authorized network work on file formats: CSV/XML, JPG/PNG and
    PDF/TXT are all well formed under "three upper-case letters with a preposition between them",
    and none of them is a currency. An unknown genuine currency is the bounded path's to own.
    """
    proof = prove_turn(text)
    assert proof.frames == (), f"{text!r} was proven as {_families(proof)}"


def test_a_real_iso_pair_is_captured_but_a_closed_grammar_may_not_affirm_it():
    """FAIL CLOSED. The frame is visible on the ledger; only a semantic proof can execute it.

    A conversion reaches the network, and the grammar cannot see the sentence it sits inside --
    "never convert EUR to JPY" is, to it, an ISO pair with an operator between them.
    """
    proof = prove_turn("convert 500 EUR to JPY")
    assert _families(proof) == ["fx_quote"]
    assert _roles(proof, "fx_quote", "base_currency") == ["EUR"]
    assert proof.frames[0].polarity is Polarity.UNRESOLVED
    assert not proof.frames[0].executable


@pytest.mark.parametrize(
    "text",
    [
        "never convert EUR to JPY",
        "don't convert USD to EUR",
        'EUR to JPY is mentioned in this document',
        'the string "USD to GBP"',
    ],
)
def test_a_closed_grammar_never_affirms_a_conversion_out_of_prose(text):
    """Every one of these is a real ISO pair, and none of them is a request to convert."""
    proof = prove_turn(text)
    assert all(not frame.executable for frame in proof.frames), (
        f"{text!r} produced an executable conversion"
    )


def test_a_semantic_negation_survives_an_overlapping_formal_reading():
    """The formal grammar may not affirm over a bounded refusal covering the same span."""
    proof = prove_turn(
        "never convert 500 EUR to JPY",
        propose=proposer(
            frame(
                "fx_quote",
                scope="never convert 500 EUR to JPY",
                predicate="convert",
                roles=[role("base_currency", "EUR"), role("quote_currency", "JPY")],
                polarity="negated",
            )
        ),
    )
    assert [f.polarity for f in proof.frames] == [Polarity.NEGATED]
    assert proof.frames[0].provenance is ProofProvenance.BOUNDED_MODEL


# --- proof path 2: the bounded model, and the rules it must satisfy -----------------------------


WEATHER_TWO = "Give me the weather in Oslo and Tromso"


def _weather_proposal(*locations: str, scope: str = "the weather in Oslo and Tromso", **kw):
    return proposer(
        frame(
            "weather_lookup",
            scope=scope,
            predicate="weather",
            roles=coordinated("location", *locations),
            **kw,
        )
    )


def test_a_bounded_proposal_becomes_roles_the_runtime_read_out_of_its_own_text():
    proof = prove_turn(WEATHER_TWO, propose=_weather_proposal("Oslo", "Tromso"))
    assert _families(proof) == ["weather_lookup"]
    assert _roles(proof, "weather_lookup", "location") == ["Oslo", "Tromso"]
    assert proof.frames[0].provenance is ProofProvenance.BOUNDED_MODEL


@pytest.mark.parametrize(
    "separator",
    [" and ", " plus ", " also ", " btw ", "/", ": ", " — ", ", ", "\n", " or "],
)
def test_membership_is_asserted_so_no_separator_can_be_unknown_to_it(separator):
    """The coordinator enumeration is gone, so a separator it never met cannot break a list.

    `_CONJUNCTION_RE` knew `, ; and then plus &`. It did not know `also`, `btw`, `/`, `:`, an em
    dash, a newline or `or` -- and each of those either merged two subjects into one or ran the
    list into whatever followed. Membership is now stated by the proof, so the character between
    two members is not consulted anywhere.
    """
    text = f"Give me the weather in Oslo{separator}Tromso"
    proof = prove_turn(
        text,
        propose=_weather_proposal(
            "Oslo", "Tromso", scope=f"the weather in Oslo{separator}Tromso"
        ),
    )
    assert _roles(proof, "weather_lookup", "location") == ["Oslo", "Tromso"], text


@pytest.mark.parametrize(
    "tail",
    [
        "my grandmother's recipe",
        "the release notes",
        "customer data",
        "core/conductor/planner.py",
        "https://example.com/report",
        "the Q3 report",
        "the blue folder",
    ],
)
def test_text_the_proposal_does_not_claim_becomes_no_role_at_all(tail):
    """I10 AT THE PROOF BOUNDARY. Unclaimed text is visible and attached to nothing.

    The residual-tail rule this replaced owned whatever followed the last coordinator, which is how
    a place called "open core/conductor/planner" came to exist. There is no tail rule now: a span
    is a role because a proof says so, and for everything else the answer is silence.
    """
    text = f"Give me the weather in Oslo and {tail}"
    proof = prove_turn(
        text, propose=_weather_proposal("Oslo", scope="the weather in Oslo")
    )
    assert _roles(proof, "weather_lookup", "location") == ["Oslo"], text
    unclaimed = " ".join(proof.canonical.text[a:b] for a, b in proof.unclaimed_spans)
    assert tail in unclaimed, f"{tail!r} vanished instead of staying visible"


def test_a_positively_proven_subject_survives_even_when_nothing_can_resolve_it():
    """An UNKNOWN subject the proof owns stays captured. Resolution is a later, separate answer."""
    proof = prove_turn(
        "weather in Vasyugan",
        propose=_weather_proposal("Vasyugan", scope="weather in Vasyugan"),
    )
    assert _roles(proof, "weather_lookup", "location") == ["Vasyugan"]


# --- the validator's rules, one refusal each ----------------------------------------------------


def _evidence(canonical, **overrides):
    scope = canonical.find("the weather in Oslo")
    defaults = dict(
        frame_id="f1",
        frame_scope=scope,
        predicate_span=canonical.find("weather"),
        family="weather_lookup",
        polarity=Polarity.AFFIRMED,
        roles=(
            SemanticRoleEvidence("location", canonical.find("Oslo"), "g", 0),
        ),
        status=EvidenceStatus.PROVEN,
        provenance=ProofProvenance.BOUNDED_MODEL,
    )
    defaults.update(overrides)
    return SemanticFrameEvidence(**defaults)


def test_a_well_formed_proposal_is_proven():
    canonical = _canonical("Give me the weather in Oslo")
    proven = validate_frame(_evidence(canonical), canonical)
    assert isinstance(proven, ProvenFrame)
    assert [r.surface for r in proven.roles] == ["Oslo"]


@pytest.mark.parametrize(
    "overrides,reason",
    [
        ({"status": EvidenceStatus.ABSTAINED}, AbstentionReason.PROPOSER_ABSTAINED),
        ({"family": "astrology_reading"}, AbstentionReason.UNKNOWN_FAMILY),
        ({"polarity": "affirmed"}, AbstentionReason.POLARITY_NOT_PROVEN),
        ({"polarity": None}, AbstentionReason.POLARITY_NOT_PROVEN),
    ],
)
def test_the_validator_refuses_a_frame_it_cannot_establish(overrides, reason):
    canonical = _canonical("Give me the weather in Oslo")
    outcome = validate_frame(_evidence(canonical, **overrides), canonical)
    assert not isinstance(outcome, ProvenFrame)
    assert outcome.reason is reason


def test_a_span_measured_against_another_text_cannot_bind():
    """Two integers are not a span. The whole point of `CanonicalText`."""
    canonical = _canonical("Give me the weather in Oslo")
    other = _canonical("Give me the weather in Riga")
    outcome = validate_frame(
        _evidence(
            canonical,
            roles=(SemanticRoleEvidence("location", other.find("Riga"), "g", 0),),
        ),
        canonical,
    )
    assert outcome.reason is AbstentionReason.SPAN_NOT_BOUND


def test_a_role_outside_the_frame_scope_is_refused():
    canonical = _canonical("Give me the weather in Oslo and the price of Gold")
    outcome = validate_frame(
        _evidence(
            canonical,
            roles=(SemanticRoleEvidence("location", canonical.find("Gold"), "g", 0),),
        ),
        canonical,
    )
    assert outcome.reason is AbstentionReason.SPAN_OUTSIDE_SCOPE


def test_a_role_the_family_does_not_declare_is_refused():
    canonical = _canonical("Give me the weather in Oslo")
    outcome = validate_frame(
        _evidence(
            canonical,
            roles=(SemanticRoleEvidence("asset_subject", canonical.find("Oslo"), "g", 0),),
        ),
        canonical,
    )
    assert outcome.reason is AbstentionReason.UNSUPPORTED_ROLE


def test_a_missing_required_role_is_refused():
    canonical = _canonical("Give me the weather in Oslo")
    outcome = validate_frame(_evidence(canonical, roles=()), canonical)
    assert outcome.reason is AbstentionReason.MISSING_REQUIRED_ROLE


def test_a_role_filler_that_is_the_predicate_itself_establishes_nothing():
    canonical = _canonical("Give me the weather in Oslo")
    outcome = validate_frame(
        _evidence(
            canonical,
            roles=(SemanticRoleEvidence("location", canonical.find("weather"), "g", 0),),
        ),
        canonical,
    )
    assert outcome.reason is AbstentionReason.PREDICATE_AS_ROLE


@pytest.mark.parametrize(
    "group,ordinals",
    [("", (0, 0)), ("g", (0, 0)), ("g", (0, 2)), ("g", (-1, 0))],
)
def test_coordination_membership_must_be_explicit_and_complete(group, ordinals):
    canonical = _canonical("Give me the weather in Oslo and Tromso")
    outcome = validate_frame(
        _evidence(
            canonical,
            frame_scope=canonical.find("the weather in Oslo and Tromso"),
            roles=(
                SemanticRoleEvidence("location", canonical.find("Oslo"), group, ordinals[0]),
                SemanticRoleEvidence("location", canonical.find("Tromso"), group, ordinals[1]),
            ),
        ),
        canonical,
    )
    assert outcome.reason is AbstentionReason.COORDINATION_NOT_EXPLICIT


def test_only_the_declared_fan_out_role_may_carry_several_members():
    canonical = _canonical("convert EUR to JPY and USD")
    outcome = validate_frame(
        _evidence(
            canonical,
            frame_scope=canonical.find("EUR to JPY and USD"),
            predicate_span=canonical.find("to"),
            family="fx_quote",
            roles=(
                SemanticRoleEvidence("base_currency", canonical.find("EUR"), "g", 0),
                SemanticRoleEvidence("quote_currency", canonical.find("JPY"), "q", 0),
                SemanticRoleEvidence("quote_currency", canonical.find("USD"), "q", 1),
            ),
        ),
        canonical,
    )
    assert outcome.reason is AbstentionReason.ROLE_NOT_COORDINATED
    assert frame_contract("fx_quote").fan_out_role == ""


def test_a_label_that_does_not_match_the_text_is_an_invented_surface():
    """A proposer's own words never reach a requirement -- the runtime reads its own text."""
    canonical = _canonical("Give me the weather in Oslo")
    span = canonical.find("Oslo")
    outcome = validate_frame(
        _evidence(
            canonical,
            roles=(
                SemanticRoleEvidence(
                    "location",
                    type(span)(**{**span.to_dict(), "label": "Osloo"}),
                    "g",
                    0,
                ),
            ),
        ),
        canonical,
    )
    assert outcome.reason is AbstentionReason.INVENTED_SURFACE


# --- hostile and absent proposers ---------------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        "",
        "I think they want the weather.",
        "{",
        '{"frames": "lots"}',
        '{"frames": [{"family": "weather_lookup"}]}',
        '[]',
    ],
)
def test_a_malformed_proposal_produces_no_frame(payload):
    proof = prove_turn(WEATHER_TWO, propose=raw_proposer(payload))
    assert proof.frames == ()


def test_a_paraphrased_surface_cannot_be_located_and_abstains():
    """The model corrected the spelling. That correction is not in the user's text, so it fails."""
    proof = prove_turn(
        "weather in Tromso",
        propose=_weather_proposal("Tromsø", scope="weather in Tromso"),
    )
    assert proof.frames == ()
    assert any(a.reason is AbstentionReason.INVENTED_SURFACE for a in proof.abstentions)


def test_a_proposer_that_raises_abstains_rather_than_guessing():
    proof = prove_turn(WEATHER_TWO, propose=failing_proposer())
    assert proof.frames == ()
    assert any(a.reason is AbstentionReason.PROPOSER_ABSTAINED for a in proof.abstentions)


def test_no_proposer_at_all_is_a_safe_state_and_not_a_cloud_dependency():
    """LOCAL-ONLY. Absent proposer, the formal path still proves, and nothing else is claimed."""
    proof = prove_turn("What is 137 x 29? And the weather in Oslo.")
    assert _families(proof) == ["calculation"]
    assert any(a.reason is AbstentionReason.PROPOSER_ABSTAINED for a in proof.abstentions)


def test_a_proposal_over_a_region_a_formal_grammar_already_proved_is_dropped():
    """Two proofs of one region is two answers to a question that has one.

    When they AGREE the deterministic reading wins: an expression parsed by a grammar is more
    precise than the same expression pointed at by a model.
    """
    proof = prove_turn(
        "What is 137 x 29?",
        propose=proposer(
            frame(
                "calculation",
                scope="137 x 29",
                predicate="x",
                roles=[role("expression", "137 x 29")],
            )
        ),
    )
    assert len(proof.frames) == 1
    assert proof.frames[0].provenance is ProofProvenance.FORMAL_GRAMMAR


# --- polarity -----------------------------------------------------------------------------------


def test_polarity_is_frame_local_and_siblings_do_not_infect_each_other():
    """MIXED POLARITY. Independent frames, independent assertions -- both directions."""
    text = "Never search the weather in Porto, but do get me the price of Gold."
    proof = prove_turn(
        text,
        propose=proposer(
            frame(
                "weather_lookup",
                scope="Never search the weather in Porto",
                predicate="search",
                roles=[role("location", "Porto")],
                polarity="negated",
            ),
            frame(
                "market_quote",
                scope="do get me the price of Gold",
                predicate="price",
                roles=[role("asset_subject", "Gold")],
                polarity="affirmed",
            ),
        ),
    )
    by_family = {f.family: f for f in proof.frames}
    assert by_family["weather_lookup"].polarity is Polarity.NEGATED
    assert not by_family["weather_lookup"].executable
    assert by_family["market_quote"].polarity is Polarity.AFFIRMED
    assert by_family["market_quote"].executable


def test_the_mirrored_polarity_pair_is_independent_too():
    text = "Get me the weather in Porto, but do not look up the price of Gold."
    proof = prove_turn(
        text,
        propose=proposer(
            frame(
                "weather_lookup",
                scope="Get me the weather in Porto",
                predicate="weather",
                roles=[role("location", "Porto")],
                polarity="affirmed",
            ),
            frame(
                "market_quote",
                scope="do not look up the price of Gold",
                predicate="price",
                roles=[role("asset_subject", "Gold")],
                polarity="negated",
            ),
        ),
    )
    by_family = {f.family: f for f in proof.frames}
    assert by_family["weather_lookup"].executable
    assert not by_family["market_quote"].executable


def test_an_unresolved_polarity_is_not_executable():
    proof = prove_turn(
        "If I travel, get the weather in Porto",
        propose=proposer(
            frame(
                "weather_lookup",
                scope="get the weather in Porto",
                predicate="weather",
                roles=[role("location", "Porto")],
                polarity="unresolved",
            )
        ),
    )
    assert proof.frames[0].polarity is Polarity.UNRESOLVED
    assert not proof.frames[0].executable


def test_the_deleted_polarity_vocabularies_are_not_consulted_anywhere():
    """A prohibition worded in a way no regex list held is still NEGATED when the proof says so."""
    text = "Under no circumstances is the weather in Porto to be fetched."
    proof = prove_turn(
        text,
        propose=proposer(
            frame(
                "weather_lookup",
                scope=text.rstrip("."),
                predicate="fetched",
                roles=[role("location", "Porto")],
                polarity="negated",
            )
        ),
    )
    assert proof.frames[0].polarity is Polarity.NEGATED


# --- structural: turn IR is not consulted for role ownership ------------------------------------


def test_turn_ir_is_not_a_role_authority_anywhere_in_capture():
    """The seam that used to decide ownership is gone from the capture path entirely.

    Asserted behaviourally rather than by reading source: `parse_turn_ir` is replaced with something
    that raises, and capture still produces exactly the frames the proof established. A capture path
    that still asked Turn IR whether a span may be owned could not survive this.
    """
    import core.turn_ir as turn_ir

    original = turn_ir.parse_turn_ir

    def _explode(*_a, **_k):
        raise AssertionError("turn_ir must not be consulted for role ownership")

    turn_ir.parse_turn_ir = _explode
    try:
        proof = prove_turn(
            "Give me the weather in Oslo and Tromso and 12 x 5",
            propose=_weather_proposal("Oslo", "Tromso"),
        )
    finally:
        turn_ir.parse_turn_ir = original
    assert sorted(_families(proof)) == ["calculation", "weather_lookup"]
    assert _roles(proof, "weather_lookup", "location") == ["Oslo", "Tromso"]


def test_formal_evidence_carries_its_provenance_and_a_predicate_span():
    canonical = _canonical("500 EUR to JPY")
    evidence = formal_grammar_evidence(canonical)
    assert len(evidence) == 1
    assert evidence[0].provenance is ProofProvenance.FORMAL_GRAMMAR
    assert evidence[0].predicate_span.resolve(canonical) == "to"


# --- MANDATED NEGATIVE CORPUS: none of these may authorize network work ---------------------------


@pytest.mark.parametrize(
    "text",
    [
        "convert this CSV to XML for me",
        "convert JPG to PNG",
        "turn PDF into TXT",
        "never convert EUR to JPY",
        "don't convert USD to EUR",
        "EUR to JPY is mentioned in this document",
        'the string "USD to GBP"',
        "compare CSV to XML",
    ],
)
def test_no_formal_frame_in_this_corpus_reaches_a_provider_argument(text):
    """The formal grammar's fail-closed contract, asserted at the ARGUMENT it would produce.

    Two independent repairs have to hold for every line here. The first four fail on MEMBERSHIP --
    CSV, XML, JPG, PNG, PDF and TXT are not in ISO 4217, so there is no conversion to prove. The
    rest are real currency codes in prose, and they fail on AUTHORITY -- `fx_quote` does not declare
    `formal_proof_may_affirm`, because a conversion reaches the network and a closed grammar cannot
    see the sentence it sits inside.
    """
    from core.conductor.realization import plan_realizations, resolve_capabilities
    from core.conductor.requirement_projection import resolve_ledger
    from core.conductor.requirements import capture_requirements

    ledger = resolve_ledger(capture_requirements(text))
    realizations = resolve_capabilities(plan_realizations(ledger))
    assert realizations.realizations == (), (
        f"{text!r} produced executable work: "
        f"{[(r.family, r.display_subject) for r in realizations.realizations]}"
    )


@pytest.mark.parametrize(
    "text,expected",
    [
        ("What is 137 x 29?", "calculation"),
        ("compute 1,024 / 8 for me", "calculation"),
        ("region: eu-west-1", "structured_field"),
    ],
)
def test_positive_control_closed_syntax_a_grammar_genuinely_proves(text, expected):
    """NEGATIVE CONTROLS ARE NOT ENOUGH. The formal path must still prove what it can.

    These reach no network, so their family declares that closed syntax is sufficient authority.
    Without this the corpus above would pass against a formal path that had simply been switched
    off.
    """
    proof = prove_turn(text)
    frames = [f for f in proof.frames if f.family == expected]
    assert frames and frames[0].executable, f"{text!r} lost its closed-syntax proof"


# --- BOOKKEEPING TOLERANCE: derived, never demanded ----------------------------------------------


def _reply(roles: list[dict], scope: str = "the weather in Oslo and Tromso") -> str:
    import json as _json

    return _json.dumps(
        {
            "frames": [
                {
                    "frame_id": "f1",
                    "family": "weather_lookup",
                    "scope": scope,
                    "predicate": "weather",
                    "polarity": "affirmed",
                    "roles": roles,
                }
            ]
        }
    )


@pytest.mark.parametrize(
    "roles",
    [
        # group omitted entirely -- what qwen2.5:7b actually emits
        [{"role": "location", "text": "Oslo"}, {"role": "location", "text": "Tromso"}],
        # group null
        [
            {"role": "location", "text": "Oslo", "group": None, "ordinal": 0},
            {"role": "location", "text": "Tromso", "group": None, "ordinal": 0},
        ],
        # ordinals both zero
        [
            {"role": "location", "text": "Oslo", "group": "g", "ordinal": 0},
            {"role": "location", "text": "Tromso", "group": "g", "ordinal": 0},
        ],
        # ordinals reversed against span order
        [
            {"role": "location", "text": "Oslo", "group": "g", "ordinal": 1},
            {"role": "location", "text": "Tromso", "group": "g", "ordinal": 0},
        ],
        # ordinals non-contiguous and groups inconsistent
        [
            {"role": "location", "text": "Oslo", "group": "a", "ordinal": 7},
            {"role": "location", "text": "Tromso", "group": "b", "ordinal": 99},
        ],
    ],
)
def test_runtime_bookkeeping_is_derived_and_never_demanded_of_the_proposer(roles):
    """Measured: 2/23 frames survived when these had to be reproduced, 23/23 when derived.

    Group and ordinal carry no information the runtime does not already hold -- a frame has one list
    per role, and its order is the order the members appear in the text. Refusing 21 semantically
    correct proofs over an identifier the model was never the authority for is ceremony.
    """
    proof = prove_turn(WEATHER_TWO, propose=raw_proposer(_reply(roles)))
    assert _roles(proof, "weather_lookup", "location") == ["Oslo", "Tromso"], (
        f"correct semantic content was discarded over bookkeeping: {proof.abstentions}"
    )
    members = proof.frames[0].roles
    assert [m.member_ordinal for m in members] == [0, 1]
    assert len({m.coordination_group_id for m in members}) == 1


def test_the_semantic_content_those_fields_do_not_carry_is_still_required():
    """The other half. Deriving bookkeeping does not loosen anything the proposer DOES know."""
    # A role the family does not declare.
    assert not prove_turn(
        WEATHER_TWO,
        propose=raw_proposer(
            _reply([{"role": "asset_subject", "text": "Oslo"}])
        ),
    ).frames
    # A surface the message does not contain.
    assert not prove_turn(
        WEATHER_TWO, propose=raw_proposer(_reply([{"role": "location", "text": "Bergen"}]))
    ).frames
    # A polarity nobody stated.
    import json as _json

    no_polarity = _json.dumps(
        {
            "frames": [
                {
                    "family": "weather_lookup",
                    "scope": "the weather in Oslo and Tromso",
                    "predicate": "weather",
                    "roles": [{"role": "location", "text": "Oslo"}],
                }
            ]
        }
    )
    assert not prove_turn(WEATHER_TWO, propose=raw_proposer(no_polarity)).frames


def test_an_omitted_predicate_is_the_frame_itself_and_not_a_refusal():
    """Measured live: `"predicate": ""` threw away a complete, correct two-member location list."""
    import json as _json

    payload = _json.dumps(
        {
            "frames": [
                {
                    "family": "weather_lookup",
                    "scope": "the weather in Oslo and Tromso",
                    "predicate": "",
                    "polarity": "affirmed",
                    "roles": [
                        {"role": "location", "text": "Oslo"},
                        {"role": "location", "text": "Tromso"},
                    ],
                }
            ]
        }
    )
    proof = prove_turn(WEATHER_TWO, propose=raw_proposer(payload))
    assert _roles(proof, "weather_lookup", "location") == ["Oslo", "Tromso"]


def test_a_predicate_the_frame_does_not_contain_is_still_a_refusal():
    """NEGATIVE CONTROL. Omitting is not the same as pointing somewhere the text does not go."""
    import json as _json

    payload = _json.dumps(
        {
            "frames": [
                {
                    "family": "weather_lookup",
                    "scope": "the weather in Oslo and Tromso",
                    "predicate": "barometer",
                    "polarity": "affirmed",
                    "roles": [{"role": "location", "text": "Oslo"}],
                }
            ]
        }
    )
    assert not prove_turn(WEATHER_TWO, propose=raw_proposer(payload)).frames


# --- VALIDATOR HARDENING -------------------------------------------------------------------------


def test_two_fillers_of_one_frame_may_not_overlap():
    """P10. A role span that swallows another member is not one filler and not two."""
    import json as _json

    payload = _json.dumps(
        {
            "frames": [
                {
                    "family": "weather_lookup",
                    "scope": "the weather in Oslo and Tromso",
                    "predicate": "weather",
                    "polarity": "affirmed",
                    "roles": [
                        {"role": "location", "text": "Oslo and Tromso"},
                        {"role": "location", "text": "Oslo"},
                    ],
                }
            ]
        }
    )
    proof = prove_turn(WEATHER_TWO, propose=raw_proposer(payload))
    assert proof.frames == ()
    assert any(
        a.reason is AbstentionReason.ROLE_SPANS_OVERLAP for a in proof.abstentions
    ), [a.reason.value for a in proof.abstentions]


def test_two_frames_claiming_one_region_both_abstain():
    """P9. Two readings of one region is not two requests, and choosing between them is a guess."""
    proof = prove_turn(
        WEATHER_TWO,
        propose=proposer(
            frame(
                "weather_lookup",
                scope="the weather in Oslo and Tromso",
                predicate="weather",
                roles=[role("location", "Oslo")],
                frame_id="a",
            ),
            frame(
                "market_quote",
                scope="weather in Oslo",
                predicate="weather",
                roles=[role("asset_subject", "Oslo")],
                frame_id="b",
            ),
        ),
    )
    assert proof.frames == ()
    assert all(
        a.reason is AbstentionReason.CONFLICTING_FRAMES for a in proof.abstentions
    )


def test_contradictory_polarity_over_one_region_fails_closed():
    proof = prove_turn(
        WEATHER_TWO,
        propose=proposer(
            frame(
                "weather_lookup",
                scope="the weather in Oslo and Tromso",
                predicate="weather",
                roles=[role("location", "Oslo")],
                polarity="affirmed",
                frame_id="yes",
            ),
            frame(
                "weather_lookup",
                scope="weather in Oslo",
                predicate="weather",
                roles=[role("location", "Oslo")],
                polarity="negated",
                frame_id="no",
            ),
        ),
    )
    assert proof.frames == (), "a contradiction shipped one of its two readings"


def test_disjoint_frames_are_not_a_conflict():
    """NEGATIVE CONTROL. Overlap is the test, not multiplicity."""
    proof = prove_turn(
        "weather in Oslo and the price of Gold",
        propose=proposer(
            frame(
                "weather_lookup",
                scope="weather in Oslo",
                predicate="weather",
                roles=[role("location", "Oslo")],
            ),
            frame(
                "market_quote",
                scope="the price of Gold",
                predicate="price",
                roles=[role("asset_subject", "Gold")],
            ),
        ),
    )
    assert sorted(_families(proof)) == ["market_quote", "weather_lookup"]


def test_a_proposed_frame_may_not_claim_the_constraint_in_front_of_its_demand() -> None:
    """A frame scope is model-authored text. It may claim the demand; not the instruction obeyed.

    Traced end to end on acceptance turn 7 by replaying the runtime's OWN captured frame call to
    qwen2.5:7b (validation-logs/.../turn7-captured-model-calls.jsonl):

        {"family": "quantitative_reasoning", "polarity": "affirmed",
         "scope": "Do NOT search the web for this. From memory: what is the boiling point of
                   water at sea level in Celsius?"}

    Everything downstream was already correct -- `demand_units` minted TWO units with the
    prohibition excluded, the plan resolved the clean clause, and the captured answering call asked
    the certified author exactly "what is the boiling point of water at sea level in Celsius?" --
    so this scope was the only carrier of the welded text, and it reached the reader as
    "Could not be answered: - Do NOT search the web for this. From memory: ..." via
    `decision.failure_lines`. A correctly minted demand with a wrongly described requirement.

    The prohibition keeps every other effect it has: this narrows what a frame CLAIMS TO ASK FOR,
    never what the turn is allowed to do.
    """
    import json

    from core.conductor.requirements import capture_requirements
    from core.retrieval_constraints import analyze_retrieval_constraints

    request = (
        "Do NOT search the web for this. From memory: what is the boiling point of water at sea "
        "level in Celsius? Also, what is 10 percent of 250?"
    )
    welded = json.dumps(
        {
            "frames": [
                {
                    "family": "quantitative_reasoning",
                    "scope": (
                        "Do NOT search the web for this. From memory: what is the boiling point "
                        "of water at sea level in Celsius?"
                    ),
                    "predicate": "",
                    "polarity": "affirmed",
                    "roles": [],
                }
            ]
        }
    )
    ledger = capture_requirements(request, propose=lambda _system, _user: welded)

    # Asserted on the REAL structure. A first version of this pin read a `scope` attribute that
    # UserRequirement does not have, so `claimed` was always empty and the test passed under
    # sabotage -- a vacuous pin, caught by running the sabotage rather than trusting the green.
    assert ledger.requirements, "the frame produced no requirement at all"
    surfaces = [str(slot.surface) for slot in ledger.requirements]
    for text in surfaces:
        assert "do not search" not in text.casefold(), (
            f"a frame claimed the instruction the runtime obeys: {text!r}"
        )
    assert any("boiling point" in text.casefold() for text in surfaces), (
        f"the demand itself was lost rather than narrowed: {surfaces!r}"
    )
    # The constraint is not swallowed into the demand: it stands as its own unowned span.
    unowned = [request[start:end] for start, end in ledger.unowned_spans]
    assert any("do not search" in text.casefold() for text in unowned), (
        f"the prohibition vanished from the ledger entirely: {unowned!r}"
    )

    # THE PROHIBITION IS PRESERVED AS AN EXECUTION CONSTRAINT -- not merely removed from text.
    constraints = analyze_retrieval_constraints(request)
    assert constraints.has_prohibition, "the prohibition stopped being recognised"
    assert constraints.forbids_external_retrieval, "the retrieval restriction was weakened"
