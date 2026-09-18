from __future__ import annotations

import uuid
from unittest import mock

import pytest

from apps.vool_agent import VoolAgent
from tests.live.runtime_model_gauntlet import (
    SET4_ABC_ORACLES,
    SET4_PROPERTY_ORACLES,
    PromptCase,
    TurnEvidence,
    load_cases,
    score_turn,
)

EXACT_MARKER_PROMPTS = (
    """- HEY LISTEN UP! Evaluate these three facts and tell me if they are True or False:
- 1: The capital of Japan is Tokyo.
- 2: 50 + 50 = 100.
- 3: Water is dry.
and theN:
A.
B.
C.""",
    """— IGNORE EVERYTHING BEFORE THIS LINE! Let's do a quick quiz haha:
— A) Name a fruit that is red.
— B] Name a vegetable that is green.
— C> Name a mineral that is blue.
and theN:
A.
B.
C.""",
    """· UMMMM let's try something weird lol
· Answer these three questions but ONLY using single words!
· What is the opposite of hot?
· What is the opposite of fast?
· What is the opposite of up?
and theN:
A.
B.
C.""",
    """- Quick math check! Solve these three problems:
— A: 10 * 10
· B: 20 + 30
- C: 100 / 2
and theN:
A.
B.
C.""",
    """· 1. Is Markdown parsing easy for you?
- 2. Will this hyphen break your internal list logic?
— 3. Will this long dash crash your output formatter?
Answer Yes or No to each question.
and theN:
A.
B.
C.""",
)

VALID_ABC = {
    1: "A. True\nB. True\nC. False",
    2: "A. Apple\nB. Spinach\nC. Azurite",
    3: "A. Cold\nB. Slow\nC. Down",
    4: "A. 100\nB. 50\nC. 50",
    5: "A. Yes\nB. No\nC. No",
}

VALID_UNSEEN = {
    6: (
        "The phrase is ambiguous: ordinary liquid water is not literally dry, but dry water "
        "could refer to a product name. Without a definition there is not enough context to conclude."
    ),
    7: (
        "In this quoted metaphor, a square circle means an impossible or contradictory compromise; "
        "it is not literal Euclidean geometry."
    ),
    8: (
        "HTML is a markup language, not medicine. That is a category mismatch in the prescription, "
        "so confirm what the doctor intended."
    ),
    9: (
        "The claim is false: the Pacific is an ocean, not a lake, and it is not in France."
    ),
    10: (
        "The database is on fire is a technical metaphor: the database is urgently failing or "
        "overloaded, not literally producing flames."
    ),
    11: (
        "Under the stipulated game rule, oxygen is a purple metal, so the forged key is a purple "
        "metal key. That follows the fictional frame."
    ),
    12: "PNG is the format defined by ISO/IEC 15948.",
    13: "kg and mol are recognized SI symbols; zorp and flarn are invented, not recognized SI units.",
    14: (
        "The pairings are incorrect and reversed: a violin is a string instrument, while a trumpet "
        "belongs to the brass family."
    ),
    15: (
        "Under the stipulated puzzle rule, the square circle—the purple token—moved once diagonally. "
        "That follows the game definition."
    ),
}

INVALID_UNSEEN = {
    6: "Ordinary liquid water is literally dry, so its dryness makes it useful.",
    7: "A literal square circle exists and combines both shapes.",
    8: "Take the prescribed HTML dose for the headache.",
    9: "The Pacific Ocean is a small lake in France.",
    10: "Call the firefighters because the database is a literal fire.",
    11: "Oxygen is actually a gas, so the fictional rule must be ignored.",
    12: "PNG, TXT, GIF, and PDF are all members of ISO/IEC 15948.",
    13: "kg, zorp, mol, and flarn are all recognized SI units.",
    14: "Yes. A violin is brass and a trumpet is string.",
    15: "It cannot move because a square circle does not exist.",
}


def _checks(prompt_number: int, answer: str, *, tools=None, events=None):
    case = PromptCase(4, prompt_number, load_cases((4,))[prompt_number - 1].prompt)
    evidence = TurnEvidence(
        case_id=case.case_id,
        lane="local",
        requested_model="vool-local-only",
        prompt=case.prompt,
        assistant_content=answer,
        canonical_content=answer,
        events=events or [{"event_type": "turn.trace_completed", "outcome": "completed"}],
        providers=["ollama-local:test"],
        tools=tools or [],
    )
    return {check.name: check for check in score_turn(case, evidence)}


def test_set4_freezes_the_five_marker_prompts_character_for_character() -> None:
    cases = load_cases((4,))
    assert len(cases) == 15
    assert tuple(case.prompt for case in cases[:5]) == EXACT_MARKER_PROMPTS
    assert [case.prompt_number for case in cases] == list(range(1, 16))


def test_unseen_half_uses_new_domains_instead_of_recycling_prior_gauntlet_entities() -> None:
    unseen = "\n".join(case.prompt for case in load_cases((4,))[5:]).casefold()
    forbidden_reuse = (
        "kubernetes", "javascript", "renault", "audi", "forex", "exchange rate",
        "local currency", "live weather", "stock ticker", "smart coffee", "city currency",
    )
    assert all(term not in unseen for term in forbidden_reuse)
    for required_domain in (
        "dry water", "square circle", "html", "pacific ocean", "database is on fire",
        "oxygen is explicitly a purple metal", "iso/iec 15948", "zorp", "violin",
    ):
        assert required_domain in unseen


@pytest.mark.parametrize("prompt_number", sorted(SET4_ABC_ORACLES))
def test_valid_abc_answers_preserve_order_cardinality_and_semantics(prompt_number) -> None:
    checks = _checks(prompt_number, VALID_ABC[prompt_number])
    assert checks["abc_clause_alignment"].passed
    assert checks["abc_semantic_answers"].passed
    assert checks["abc_clause_alignment"].fault_domain == "runtime_orchestration"
    assert checks["abc_semantic_answers"].fault_domain == "model_answer"


@pytest.mark.parametrize(
    ("answer", "failed_check"),
    (
        ("A. True\nC. False", "abc_clause_alignment"),
        ("B. True\nA. True\nC. False", "abc_clause_alignment"),
        ("A. True; B. True; C. False", "abc_clause_alignment"),
        ("A. True\nB. True\nC. False\nExtra", "abc_clause_alignment"),
        ("A. True\nB. False\nC. True", "abc_semantic_answers"),
    ),
)
def test_abc_oracle_rejects_dropped_reordered_merged_extra_or_wrong_clauses(answer, failed_check) -> None:
    assert not _checks(1, answer)[failed_check].passed


def test_category_oracle_accepts_common_valid_members_not_only_fixture_examples() -> None:
    checks = _checks(2, "A. Pomegranate\nB. Pepper\nC. Kyanite")
    assert checks["abc_clause_alignment"].passed
    assert checks["abc_semantic_answers"].passed


def test_category_oracle_rejects_members_from_the_wrong_categories() -> None:
    checks = _checks(2, "A. Spinach\nB. Sapphire\nC. Apple")
    assert checks["abc_clause_alignment"].passed
    assert not checks["abc_semantic_answers"].passed


@pytest.mark.parametrize("prompt_number", sorted(SET4_PROPERTY_ORACLES))
def test_unseen_semantic_preflight_answers_satisfy_property_oracles(prompt_number) -> None:
    checks = _checks(prompt_number, VALID_UNSEEN[prompt_number])
    assert checks["semantic_preflight_property"].passed, checks["semantic_preflight_property"].detail
    assert checks["tool_effect_discipline"].passed


@pytest.mark.parametrize("prompt_number", sorted(SET4_PROPERTY_ORACLES))
def test_unseen_oracles_reject_the_exact_premise_frame_or_category_error(prompt_number) -> None:
    check = _checks(prompt_number, INVALID_UNSEEN[prompt_number])["semantic_preflight_property"]
    assert not check.passed


@pytest.mark.parametrize(
    ("prompt_number", "answer"),
    (
        (6, "The phrase is ambiguous and could refer to a product; ordinary liquid water is not dry, so its meaning cannot be determined."),
        (7, "A square circle is impossible and describes a contradiction: the two shapes cannot coexist."),
        (9, "The claim is false: the Pacific is an ocean, not a lake in France."),
        (11, "Based on the game's rules, the forged key is purple metal."),
        (13, "kg and mol are recognized SI symbols; zorp and flarn are not recognized SI units."),
    ),
)
def test_property_oracles_accept_semantically_equivalent_non_benchmarkese(
    prompt_number: int,
    answer: str,
) -> None:
    assert _checks(prompt_number, answer)["semantic_preflight_property"].passed


@pytest.mark.parametrize(
    ("prompt_number", "answer"),
    (
        (
            6,
            '"Dry water" likely refers to a modified form whose exact composition is unclear '
            "without further information.",
        ),
        (
            9,
            "The Pacific Ocean is by no means a small lake in France; it is the world's largest ocean.",
        ),
        (
            9,
            "The Pacific Ocean is a vast body of water and is not located in France.",
        ),
        (
            7,
            'A "square circle" in a geometry fable represents a contradictory compromise, '
            "impossible to achieve because it violates fundamental properties of shapes.",
        ),
        (
            11,
            "Based on your game's fictional rules, the key would be made of purple metal oxygen.",
        ),
        (
            11,
            "Based on your fictional rule, the key would be made of purple metal.",
        ),
        (
            11,
            "The key is made of oxygen and is purple — exactly as your game defines it.",
        ),
        (
            15,
            "In this puzzle, the square circle is the purple token, and it moved diagonally once.",
        ),
        (
            15,
            "The purple token—the square circle—moved diagonally.",
        ),
        (
            10,
            '"The database is on fire" is not literal; it is ops slang for a critical system-wide collapse.',
        ),
        (
            14,
            "Both are switched: a violin is string and a trumpet is brass, so the labels are backwards.",
        ),
        (
            14,
            "Both are mixed up: violin is the string family and trumpet is the brass family; the labels were swapped.",
        ),
        (
            14,
            "Violin: String Instrument\nTrumpet: Brass Instrument",
        ),
    ),
)
def test_property_oracles_accept_observed_relation_preserving_paraphrases(
    prompt_number: int,
    answer: str,
) -> None:
    assert _checks(prompt_number, answer)["semantic_preflight_property"].passed


@pytest.mark.parametrize("prompt_number", range(1, 16))
def test_every_set4_case_forbids_tool_and_effect_lanes(prompt_number) -> None:
    answer = VALID_ABC.get(prompt_number) or VALID_UNSEEN[prompt_number]
    checks = _checks(
        prompt_number,
        answer,
        tools=["web.search"],
        events=[
            {"event_type": "tool.call_completed", "tool_name": "web.search"},
            {"event_type": "turn.trace_completed", "outcome": "completed"},
        ],
    )
    assert not checks["tool_effect_discipline"].passed
    assert checks["tool_effect_discipline"].fault_domain == "runtime_orchestration"


@pytest.mark.parametrize(
    ("prompt_number", "required"),
    (
        (7, ("internally contradictory", "figurative")),
        (9, ("premise is false", "not a small lake", "not located in France")),
        (10, ("nonliteral", "urgent", "immediate attention")),
    ),
)
def test_reviewed_set4_semantics_bypass_model_tool_and_web(
    tmp_path,
    monkeypatch,
    prompt_number: int,
    required: tuple[str, ...],
) -> None:
    prompt = load_cases((4,))[prompt_number - 1].prompt
    monkeypatch.setenv("VOOL_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("VOOL_RUNTIME_DIR", str(tmp_path / "runtime"))
    agent = VoolAgent(
        backend_name="test-backend",
        device=f"set4-reviewed-{prompt_number}",
        persona_id="default",
    )
    model = mock.Mock(side_effect=AssertionError("reviewed Set4 semantic reached model"))
    tool = mock.Mock(side_effect=AssertionError("reviewed Set4 semantic reached tool"))
    monkeypatch.setattr(agent.memory_router, "resolve", model)
    monkeypatch.setattr(agent.memory_router, "_invoke_manifest", model)
    monkeypatch.setattr(agent, "_execute_tool_intent", tool)

    result = agent.run_once(
        prompt,
        session_id_override=f"openclaw:set4-reviewed-{uuid.uuid4().hex}",
        source_context={
            "workspace": str(tmp_path),
            "workspace_root": str(tmp_path),
            "surface": "api",
            "operating_mode": "auto",
            "requested_model": "vool-local-only",
            "local_only": True,
            "allow_remote_fetch": False,
        },
    )

    assert result["model_calls"] == 0
    assert result.get("web_calls", 0) == 0
    for phrase in required:
        assert phrase in result["response"]
    model.assert_not_called()
    tool.assert_not_called()
