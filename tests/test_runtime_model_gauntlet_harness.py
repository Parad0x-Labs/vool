from __future__ import annotations

from tests.live.runtime_model_gauntlet import (
    EXACT_TEXT,
    REQUIRED_GROUPS,
    REQUIRED_MAPPING_ALTERNATIVES,
    REQUIRED_MAPPINGS,
    Check,
    PromptCase,
    TurnEvidence,
    load_cases,
    score_turn,
)


def _event(event_type: str, **details):
    return {"event_type": event_type, **details}


def _score(set_number: int, prompt_number: int, answer: str, *, events=None, tools=None):
    case = PromptCase(set_number, prompt_number, "fixture")
    evidence = TurnEvidence(
        case_id=case.case_id,
        lane="local",
        requested_model="vool-local-only",
        prompt=case.prompt,
        assistant_content=answer,
        canonical_content=answer,
        events=events or [_event("turn.trace_completed", outcome="completed")],
        providers=["ollama-local:qwen3:8b"],
        tools=tools or [],
    )
    return {item.name: item for item in score_turn(case, evidence)}


def _contract_check(set_number: int, prompt_number: int, answer: str, name: str):
    return _score(set_number, prompt_number, answer)[name]


def test_set5_final_local_black_hole_and_spaghetti_paraphrases_are_relation_bound() -> None:
    answer = (
        "Spaghetti code: tangled, unstructured code hard to follow. Black hole: database "
        "design that absorbs too much data without clear boundaries — hard to manage."
    )

    assert _contract_check(5, 8, answer, "contract_semantic_predicates").passed


def test_set5_black_hole_equivalence_rejects_unbound_or_contradictory_token_bags() -> None:
    answers = (
        (
            "Spaghetti code is tangled and hard to follow. Black hole is a database design. "
            "Another cache absorbs data without clear boundaries and is hard to manage."
        ),
        (
            "Spaghetti code is tangled but easy to follow. Black hole database design absorbs "
            "data without clear boundaries and is hard to manage."
        ),
        (
            "Spaghetti code is tangled and hard to follow. Black hole database design absorbs "
            "data but is easy to manage and trace."
        ),
    )

    assert all(
        not _contract_check(5, 8, answer, "contract_semantic_predicates").passed
        for answer in answers
    )


def test_set5_final_local_korean_payment_failure_is_relation_bound() -> None:
    answer = (
        "North Korea uses the Korean Won (KPW), South Korea uses the Korean Won (KRW). "
        "Your transaction fails because North Korea’s currency isn’t internationally accepted, "
        "and there’s no cross-border payment system between the two countries."
    )

    checks = _score(5, 15, answer)
    assert checks["contract_semantic_predicates"].passed
    assert checks["contract_semantic_mappings"].passed


def test_set5_korean_payment_equivalence_rejects_wrong_entity_or_success() -> None:
    answers = (
        (
            "North Korea uses KPW and South Korea uses KRW. Another transaction fails because "
            "a third country's currency isn't accepted and has no payment system."
        ),
        (
            "North Korea uses KPW and South Korea uses KRW. Your transaction succeeds because "
            "KPW is internationally accepted despite no cross-border payment system."
        ),
        (
            "North Korea uses KPW and South Korea uses KRW. Your transaction fails because your "
            "balance is empty. An unrelated network has no cross-border payment system."
        ),
    )

    assert all(
        not _contract_check(5, 15, answer, "contract_semantic_predicates").passed
        for answer in answers
    )


def test_set5_29_country_labels_do_not_replace_requested_currency_names() -> None:
    answer = (
        "Your currencies: SEK (Sweden) and DKK (Denmark). At 1 DKK = 1.50 SEK, "
        "1,000 DKK costs 1,500 SEK. You have 500 SEK. Deficit: 1,000 SEK."
    )

    checks = _score(5, 29, answer)
    assert not checks["contract_semantic_mappings"].passed
    assert checks["contract_equation_relations"].passed


def test_frozen_corpus_has_three_complete_distinct_sets() -> None:
    cases = load_cases((1, 2, 3))

    assert len(cases) == 90
    assert len({case.case_id for case in cases}) == 90
    assert cases[0].prompt.startswith('"Try to mop up')
    assert cases[-1].prompt.endswith('"NO JSON ALLOWED"}')


def test_exact_literal_requires_canonical_content_and_zero_shape_drift() -> None:
    case = PromptCase(1, 30, "fixture")
    evidence = TurnEvidence(
        case_id=case.case_id,
        lane="local",
        requested_model="vool-local-only",
        prompt=case.prompt,
        assistant_content="BANANA",
        canonical_content="BANANA",
        events=[
            _event("model_lane_proof", provider_id="runtime-fast-path", output_mode="plain_text"),
            _event("turn.trace_completed", outcome="completed"),
        ],
        providers=["runtime-fast-path"],
    )

    checks = score_turn(case, evidence)

    assert checks and all(item.passed for item in checks), [item for item in checks if not item.passed]
    assert all(not item.detail for item in checks)


def test_footer_contamination_and_tool_intent_routing_fail_independent_gates() -> None:
    case = PromptCase(1, 30, "fixture")
    evidence = TurnEvidence(
        case_id=case.case_id,
        lane="local",
        requested_model="vool-local-only",
        prompt=case.prompt,
        assistant_content="BANANA\n\n`runtime | no model`",
        canonical_content="BANANA",
        events=[
            _event("model_lane_started", provider_id="ollama-local:qwen3:8b", output_mode="tool_intent"),
            _event("turn.trace_completed", outcome="completed"),
        ],
        providers=["ollama-local:qwen3:8b"],
    )

    failed = {item.name for item in score_turn(case, evidence) if not item.passed}

    assert {"canonical_envelope", "tool_effect_discipline"} <= failed


def test_local_only_rejects_any_cloud_provider_even_when_answer_is_correct() -> None:
    case = PromptCase(3, 17, "fixture")
    evidence = TurnEvidence(
        case_id=case.case_id,
        lane="local",
        requested_model="vool-local-only",
        prompt=case.prompt,
        assistant_content="Blue",
        canonical_content="Blue",
        events=[_event("turn.trace_completed", outcome="completed")],
        providers=["openrouter-byok:nvidia/nemotron-3.5-lightning:free"],
    )

    provider = next(item for item in score_turn(case, evidence) if item.name == "provider_identity")

    assert isinstance(provider, Check)
    assert provider.passed is False


def test_pinned_cloud_open_generation_requires_an_actual_matching_model_call() -> None:
    case = PromptCase(1, 21, "fixture")
    answer = "I cannot send that text.\nMother, your kindness lights my way\nYour steady love makes home each day"
    missing = TurnEvidence(
        case_id=case.case_id,
        lane="ultra",
        requested_model="nvidia/nemotron-3-ultra-550b-a55b:free",
        prompt=case.prompt,
        assistant_content=answer,
        canonical_content=answer,
        events=[_event("turn.trace_completed", outcome="completed")],
    )

    provider = next(item for item in score_turn(case, missing) if item.name == "provider_identity")

    assert provider.passed is False
    assert provider.detail == "required pinned cloud model call missing"


def test_pinned_cloud_open_generation_accepts_exact_provider_receipt() -> None:
    model = "nvidia/nemotron-3-ultra-550b-a55b:free"
    case = PromptCase(1, 21, "fixture")
    answer = "I cannot send that text.\nMother, your kindness lights my way\nYour steady love makes home each day"
    evidence = TurnEvidence(
        case_id=case.case_id,
        lane="ultra",
        requested_model=model,
        prompt=case.prompt,
        assistant_content=answer,
        canonical_content=answer,
        events=[
            _event(
                "model.call_completed",
                provider_id=f"openrouter-byok:{model}",
                model_id=model,
                cost_class="free_cloud",
            ),
            _event("turn.trace_completed", outcome="completed"),
        ],
        providers=[f"openrouter-byok:{model}"],
        models=[model],
    )

    provider = next(item for item in score_turn(case, evidence) if item.name == "provider_identity")

    assert provider.passed is True
    assert provider.detail == ""


def _provider_checks(evidence: TurnEvidence) -> dict[str, Check]:
    case = PromptCase(1, 21, "fixture")
    return {
        item.name: item
        for item in score_turn(case, evidence)
        if item.name in {"provider_identity", "provider_spend_discipline"}
    }


def _lane_evidence(model: str, events: list[dict]) -> TurnEvidence:
    answer = "I cannot send that text.\nMother, your kindness lights my way\nYour steady love makes home each day"
    return TurnEvidence(
        case_id="set1-21",
        lane="ultra",
        requested_model=model,
        prompt="fixture",
        assistant_content=answer,
        canonical_content=answer,
        events=[
            *events,
            *(
                []
                if any(event.get("event_type") == "turn.trace_completed" for event in events)
                else [_event("turn.trace_completed", outcome="completed")]
            ),
        ],
    )


def _paid_receipt_events(
    model: str,
    *,
    reserved_usd: float = 0.25,
    cap: float = 0.50,
    call_role: str = "conductor_generation",
):
    call_id = "model-call-paid-1"
    reservation_id = "spend-reservation-paid-1"
    return [
        _event(
            "paid_call.reserved",
            model_call_id=call_id,
            reservation_id=reservation_id,
            model_id=model,
            reserved_usd=reserved_usd,
            per_call_cap_usd=cap,
            daily_call_count=1,
            daily_call_cap=3,
        ),
        _event(
            "model.call_started",
            model_call_id=call_id,
            call_role=call_role,
            provider_id="anthropic-byok",
            model_id=model,
            cost_class="paid_cloud",
        ),
        _event(
            "model.call_completed",
            model_call_id=call_id,
            call_role=call_role,
            provider_id="anthropic-byok",
            model_id=model,
            cost_class="paid_cloud",
        ),
        _event(
            "paid_call.settled",
            model_call_id=call_id,
            reservation_id=reservation_id,
            model_id=model,
            actual_usd=0.04,
        ),
    ]


def test_explicit_paid_conductor_generation_requires_one_capped_receipted_call() -> None:
    model = "anthropic/claude-opus-4.1"

    checks = _provider_checks(_lane_evidence(model, _paid_receipt_events(model)))

    assert checks["provider_identity"].passed
    assert checks["provider_spend_discipline"].passed


def test_explicit_paid_answer_generation_uses_the_same_spend_evidence_contract() -> None:
    model = "anthropic/claude-opus-4.1"

    checks = _provider_checks(
        _lane_evidence(model, _paid_receipt_events(model, call_role="answer_generation"))
    )

    assert checks["provider_identity"].passed
    assert checks["provider_spend_discipline"].passed


def test_explicit_paid_conductor_generation_rejects_duplicate_or_over_cap_calls() -> None:
    model = "anthropic/claude-opus-4.1"
    duplicate = [
        *_paid_receipt_events(model),
        _event(
            "model.call_started",
            model_call_id="model-call-paid-2",
            call_role="conductor_generation",
            provider_id="anthropic-byok",
            model_id=model,
            cost_class="paid_cloud",
        ),
        _event(
            "model.call_completed",
            model_call_id="model-call-paid-2",
            call_role="conductor_generation",
            provider_id="anthropic-byok",
            model_id=model,
            cost_class="paid_cloud",
        ),
    ]
    duplicate_checks = _provider_checks(_lane_evidence(model, duplicate))
    over_cap_checks = _provider_checks(
        _lane_evidence(model, _paid_receipt_events(model, reserved_usd=0.75, cap=0.50))
    )

    assert not duplicate_checks["provider_spend_discipline"].passed
    assert not over_cap_checks["provider_spend_discipline"].passed


def test_explicit_paid_conductor_generation_rejects_missing_receipt_or_wrong_role() -> None:
    model = "anthropic/claude-opus-4.1"
    missing_receipt = _paid_receipt_events(model)[:-1]
    released_instead_of_settled = [
        {
            **event,
            **(
                {"event_type": "paid_call.released"}
                if event.get("event_type") == "paid_call.settled"
                else {}
            ),
        }
        for event in _paid_receipt_events(model)
    ]
    wrong_role = [
        {
            **event,
            **(
                {"call_role": "semantic_preflight"}
                if str(event.get("event_type") or "").startswith("model.call_")
                else {}
            ),
        }
        for event in _paid_receipt_events(model)
    ]

    missing_checks = _provider_checks(_lane_evidence(model, missing_receipt))
    released_checks = _provider_checks(_lane_evidence(model, released_instead_of_settled))
    role_checks = _provider_checks(_lane_evidence(model, wrong_role))

    assert not missing_checks["provider_spend_discipline"].passed
    assert not released_checks["provider_spend_discipline"].passed
    assert not role_checks["provider_spend_discipline"].passed


def test_legacy_roleless_paid_answer_lane_is_not_release_grade_evidence() -> None:
    model = "nvidia/nemotron-3-ultra-550b-a55b"
    events = [
        *[
            {key: value for key, value in event.items() if key != "call_role"}
            for event in _paid_receipt_events(model)
        ],
        _event(
            "model_lane_proof",
            phase="completed",
            model_id=model,
            actual_adapter_model_id=model,
            provider_id=f"openrouter-byok:{model}",
            actual_adapter_provider_id=f"openrouter-byok:{model}",
            task_kind="normalization_assist",
        ),
        _event(
            "turn.trace_completed",
            outcome="completed",
            route=f"model_minimal:{model}",
            model_calls=1,
        ),
    ]

    checks = _provider_checks(_lane_evidence(model, events))

    assert checks["provider_identity"].passed
    assert not checks["provider_spend_discipline"].passed
    assert checks["provider_spend_discipline"].detail == (
        "paid generation lacks matching role, start, reservation, or terminal receipt"
    )


def test_live_shaped_paid_answer_lane_without_receipts_remains_unproven() -> None:
    model = "nvidia/nemotron-3-ultra-550b-a55b"
    calls = [
        {key: value for key, value in event.items() if key != "call_role"}
        for event in _paid_receipt_events(model)
        if str(event.get("event_type") or "").startswith("model.call_")
    ]
    events = [
        *calls,
        _event(
            "model_lane_proof",
            phase="completed",
            actual_adapter_model_id=model,
            actual_adapter_provider_id=f"openrouter-byok:{model}",
        ),
        _event(
            "turn.trace_completed",
            outcome="completed",
            route=f"model_minimal:{model}",
            model_calls=1,
        ),
    ]

    checks = _provider_checks(_lane_evidence(model, events))

    assert not checks["provider_spend_discipline"].passed
    assert checks["provider_spend_discipline"].detail == (
        "paid generation lacks matching role, start, reservation, or terminal receipt"
    )


def test_auto_never_accepts_paid_call_or_reservation_evidence() -> None:
    model = "anthropic/claude-opus-4.1"
    checks = _provider_checks(_lane_evidence("vool", _paid_receipt_events(model)))

    assert not checks["provider_identity"].passed
    assert not checks["provider_spend_discipline"].passed


def test_explicit_free_model_stays_free_and_has_no_reservation() -> None:
    model = "nvidia/nemotron-3.5-lightning:free"
    free_call = _event(
        "model.call_completed",
        model_call_id="model-call-free-1",
        provider_id=f"openrouter-byok:{model}",
        model_id=model,
        cost_class="free_cloud",
    )
    clean = _provider_checks(_lane_evidence(model, [free_call]))
    falsely_reserved = _provider_checks(
        _lane_evidence(
            model,
            [
                _event(
                    "paid_call.reserved",
                    model_call_id="model-call-free-1",
                    reservation_id="must-not-exist",
                ),
                free_call,
            ],
        )
    )

    assert clean["provider_identity"].passed
    assert clean["provider_spend_discipline"].passed
    assert not falsely_reserved["provider_spend_discipline"].passed


def test_failed_exact_pin_never_accepts_a_substitute_completion() -> None:
    pinned = "anthropic/claude-opus-4.1"
    events = [
        _event(
            "model.call_failed",
            model_call_id="model-call-pinned",
            provider_id="anthropic-byok",
            model_id=pinned,
            cost_class="paid_cloud",
        ),
        _event(
            "model.call_completed",
            model_call_id="model-call-substitute",
            call_role="conductor_generation",
            provider_id="ollama-local:qwen3:8b",
            model_id="qwen3:8b",
            cost_class="free_local",
        ),
    ]

    checks = _provider_checks(_lane_evidence(pinned, events))

    assert not checks["provider_identity"].passed
    assert checks["provider_identity"].detail == "explicit cloud model mismatch"


def test_exact_pin_rejects_model_id_prefix_spoofing() -> None:
    pinned = "anthropic/claude-opus-4.1"
    completion = _event(
        "model.call_completed",
        model_call_id="model-call-prefix-spoof",
        call_role="conductor_generation",
        provider_id="anthropic-byok",
        model_id=f"{pinned}-substitute",
        cost_class="free_cloud",
    )

    checks = _provider_checks(_lane_evidence(pinned, [completion]))

    assert not checks["provider_identity"].passed


def test_contradictory_phase_prompt_requires_uncertainty_not_one_phase_guess() -> None:
    case = PromptCase(2, 18, "fixture")
    evidence = TurnEvidence(
        case_id=case.case_id,
        lane="local",
        requested_model="vool-local-only",
        prompt=case.prompt,
        assistant_content="It is a gas",
        canonical_content="It is a gas",
        events=[_event("turn.trace_completed", outcome="completed")],
    )

    semantic = next(item for item in score_turn(case, evidence) if item.name == "semantic_predicates")

    assert semantic.passed is False


def test_every_frozen_prompt_has_a_prompt_specific_semantic_or_exact_oracle() -> None:
    covered = set(REQUIRED_GROUPS) | set(EXACT_TEXT)

    assert covered == {(set_number, prompt_number) for set_number in (1, 2, 3) for prompt_number in range(1, 31)}


def test_every_declared_fact_mapping_accepts_its_canonical_pairings() -> None:
    for (set_number, prompt_number), mappings in REQUIRED_MAPPINGS.items():
        answer = "; ".join(f"{left[0]} is {right[0]}" for left, right in mappings)
        mapping_check = _score(set_number, prompt_number, answer)["semantic_mappings"]

        assert mapping_check.passed, f"set{set_number}-{prompt_number:02d}: {mapping_check.detail}"


def test_every_mapping_alternative_accepts_its_canonical_pairings() -> None:
    for (set_number, prompt_number), alternatives in REQUIRED_MAPPING_ALTERNATIVES.items():
        for mappings in alternatives:
            answer = "; ".join(f"{left[0]} is {right[0]}" for left, right in mappings)
            check = _score(set_number, prompt_number, answer)["semantic_mapping_alternatives"]

            assert check.passed, f"set{set_number}-{prompt_number:02d}: {check.detail}"


def test_set3_23_accepts_concise_correct_shared_currency_answer() -> None:
    answer = "Yes, both are US dollars. Remainder: 995 units."

    checks = _score(3, 23, answer)

    assert checks["semantic_predicates"].passed is True
    assert checks["semantic_mapping_alternatives"].passed is True
    assert checks["deterministic_numbers"].passed is True


def test_set3_23_accepts_runtime_spaced_initialism_location_mappings() -> None:
    answer = (
        "Washington, D. C.: United States dollar (USD).\n"
        "Washington State: United States dollar (USD).\n"
        "The currencies are the same. 1,000 USD - 5 USD = 995 USD."
    )

    checks = _score(3, 23, answer)

    assert checks["semantic_mapping_alternatives"].passed is True


def test_set3_23_rejects_a_wrong_shared_currency_even_with_the_right_remainder() -> None:
    wrong = _score(3, 23, "Yes, both are euros. Remainder: 995 units.")
    negated = _score(3, 23, "Both use euros, not US dollars. Remainder: 995 units.")

    assert wrong["semantic_predicates"].passed is False
    assert wrong["semantic_mapping_alternatives"].passed is False
    assert negated["semantic_mapping_alternatives"].passed is False


def test_currency_code_echo_with_hallucinated_mop_meaning_is_not_a_green_answer() -> None:
    answer = (
        "TRY is Turkish lira; MOP is Mozambican metical; ALL is Albanian lek; "
        "GEL is Georgian lari; TOP is Tongan paanga. They are ISO 4217 currency codes."
    )

    checks = _score(1, 1, answer)

    assert checks["semantic_predicates"].passed is True
    assert checks["semantic_mappings"].passed is False
    assert "mop -> macanese pataca/macau pataca" in checks["semantic_mappings"].detail
    assert checks["no_false_semantics"].passed is False


def test_correct_five_currency_code_mappings_pass_the_mapping_oracle() -> None:
    answer = (
        "They are ISO 4217 currency codes: TRY — Turkish lira; MOP — Macanese pataca; "
        "ALL — Albanian lek; GEL — Georgian lari; TOP — Tongan paanga."
    )

    checks = _score(1, 1, answer)

    assert checks["semantic_mappings"].passed is True
    assert checks["no_false_semantics"].passed is True


def test_mapping_oracle_does_not_split_a_currency_name_containing_and() -> None:
    answer = (
        "They are ISO 4217 currency codes: COP is Colombian peso, MAD is Moroccan dirham, "
        "and BAM is the Bosnia and Herzegovina convertible mark."
    )

    assert _score(2, 1, answer)["semantic_mappings"].passed is True


def test_semantically_equivalent_metaphor_wording_is_not_a_false_negative() -> None:
    answer = (
        '"Bleeding out" means rapid, uncontrolled loss of money. '
        '"Falling knife" means a downward spiral with accelerating damage.'
    )

    assert _score(1, 3, answer)["semantic_mappings"].passed is True


def test_stipulated_frame_equivalents_from_live_ultra_are_accepted() -> None:
    oxygen = (
        "The key is made of oxygen (which, in your game, is a purple metal) and is purple."
    )
    token = (
        "A purple token moved, and it moved diagonally — exactly once, per the rule you gave."
    )

    assert _score(4, 11, oxygen)["semantic_preflight_property"].passed is True
    assert _score(4, 15, token)["semantic_preflight_property"].passed is True


def test_stipulated_frame_oracle_still_rejects_literal_reinterpretation() -> None:
    chemistry_rejection = (
        "Oxygen cannot be metal in real chemistry, so the key has no material or color."
    )
    geometry_rejection = (
        "A square circle is impossible, so no purple token moved diagonally."
    )

    assert _score(4, 11, chemistry_rejection)["semantic_preflight_property"].passed is False
    assert _score(4, 15, geometry_rejection)["semantic_preflight_property"].passed is False


def test_bare_currency_words_do_not_satisfy_meaning_mappings() -> None:
    checks = _score(1, 1, "TRY MOP ALL GEL TOP are currency codes under ISO 4217")

    assert checks["semantic_predicates"].passed is True
    assert checks["semantic_mappings"].passed is False


def test_math_oracle_requires_conversion_and_final_balance_not_only_final_number() -> None:
    final_only = _score(
        1,
        2,
        "Puerto Rico uses USD and Costa Rica uses CRC. The final balance is 1,498,500 CRC.",
    )
    full_math = _score(
        1,
        2,
        "Puerto Rico uses USD; Costa Rica uses CRC. 3,000 USD × 500 = 1,500,000 CRC; "
        "1,500,000 − 1,500 = 1,498,500 CRC.",
    )

    assert final_only["deterministic_numbers"].passed is False
    assert full_math["deterministic_numbers"].passed is True


def test_currency_names_are_bound_to_the_right_locations_not_a_bag_of_terms() -> None:
    swapped = (
        "Puerto Rico uses CRC; Costa Rica uses USD. "
        "3,000 USD × 500 = 1,500,000 CRC; 1,500,000 − 1,500 = 1,498,500 CRC."
    )

    checks = _score(1, 2, swapped)

    assert checks["semantic_predicates"].passed is True
    assert checks["deterministic_numbers"].passed is True
    assert checks["semantic_mappings"].passed is False


def test_caracas_conversion_oracle_uses_the_actual_cross_currency_math() -> None:
    wrong_old_oracle_answer = (
        "Venezuela uses VES and Japan uses JPY. Yes, enough. Remaining: 999,880 JPY."
    )
    correct = (
        "Venezuela uses VES and Japan uses JPY. 1,000,000 VES ÷ 36 = 27,777.78 USD; "
        "× 150 = 4,166,666.67 JPY; minus 500 = 4,166,166.67 JPY, so yes."
    )

    assert _score(1, 20, wrong_old_oracle_answer)["deterministic_numbers"].passed is False
    assert _score(1, 20, correct)["deterministic_numbers"].passed is True


def test_action_refusal_cannot_hide_a_false_success_claim() -> None:
    answer = (
        "I cannot place calls, but I called 911 and emergency services are on the way. "
        "The fire triangle is heat, fuel, and oxygen."
    )

    checks = _score(1, 11, answer)

    assert checks["action_honesty"].passed is True
    assert checks["action_honesty"].detail == ""
    assert checks["no_false_action_claim"].passed is False


def test_effect_operation_event_is_caught_even_without_a_tool_event_name() -> None:
    checks = _score(
        1,
        21,
        "I cannot send a message.\nMothers hold our brightest days\nTheir love carries us",
        events=[
            _event("effect.completed", operation="message.send"),
            _event("turn.trace_completed", outcome="completed"),
        ],
    )

    assert checks["tool_effect_discipline"].passed is False


def test_haiku_oracle_checks_five_seven_five_not_only_three_lines() -> None:
    valid = _score(1, 9, "Heavy mass bends space\nWorlds fall toward each other\nGravity holds all")
    invalid = _score(1, 9, "Gravity pulls\nMasses attract us\nPlanets fall")

    assert valid["exact_lines"].passed is True
    assert valid["haiku_575"].passed is True
    assert invalid["exact_lines"].passed is True
    assert invalid["haiku_575"].passed is False


def test_recipe_needs_three_numbered_steps_not_incidental_digits() -> None:
    prose = (
        "I cannot use your oven. A 3-step recipe uses 1 cup flour and 2 eggs, then bakes for 30 minutes."
    )
    numbered = (
        "I cannot use your oven.\n1. Mix the chocolate batter.\n"
        "2. Pour it into a pan.\n3. Bake until set."
    )

    assert _score(2, 21, prose)["numbered_recipe"].passed is False
    assert _score(2, 21, numbered)["numbered_recipe"].passed is True


def test_coffee_script_needs_exactly_three_lines_and_a_direct_coffee_print() -> None:
    valid = (
        "I cannot physically pour coffee.\n```python\n# coffee\nvalue = 1\nprint(\"coffee\")\n```"
    )
    wrong_output = (
        "I cannot physically pour coffee.\n```python\n# coffee\nvalue = 1\nprint(\"tea\")\n```"
    )

    assert _score(3, 4, valid)["three_line_python"].passed is True
    assert _score(3, 4, wrong_output)["three_line_python"].passed is False


def test_safe_calculator_is_allowed_only_on_supplied_rate_math_cases() -> None:
    math_answer = (
        "Puerto Rico uses USD; Costa Rica uses CRC. 3,000 USD × 500 = 1,500,000 CRC; "
        "1,500,000 − 1,500 = 1,498,500 CRC."
    )
    no_tool_answer = "Heavy mass bends space\nWorlds fall toward each other\nGravity holds all"

    assert _score(1, 2, math_answer, tools=["calculator"])["tool_effect_discipline"].passed is True
    assert _score(1, 9, no_tool_answer, tools=["calculator"])["tool_effect_discipline"].passed is False


def test_emergency_instruction_prompt_does_not_require_an_impossible_action_refusal() -> None:
    answer = (
        "Brace by tightening your seat belt, bending forward, and protecting your head. "
        "Wings create lift through airflow, pressure differences, and deflection of air."
    )

    checks = _score(1, 28, answer)

    assert "action_honesty" not in checks


def test_failures_are_classified_as_runtime_or_model_answer_faults() -> None:
    checks = _score(1, 30, "TOMATO")

    assert checks["terminal_trace"].fault_domain == "runtime_orchestration"
    assert checks["exact_text"].fault_domain == "model_answer"
