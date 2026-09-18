"""A message that asks several things must produce several answers, not one.

Measured on the shipped build, 2026-08-05, driving the real app:

    "what is the euro to usd today and will it rain and where i can change my tyres in vilnius"
      -> "Weather in euro to usd and will it rain and where i can change my tyres in vilnius:
          Vilnius, Lithuania: Sunny, 21 C ..."

    "ok what about gold and silver price?"
      -> Gold answered; "No live quote came back for `silver`"

`live_info_mode()` returns ONE mode for a turn and each lane below picks ONE subject, so a
multi-part message cannot survive. Handing it to the model+tool lane instead was measured and is
worse -- 154.86s, zero tool calls, "Check XE.com ... search Google Maps".

These tests cover the planner's pure logic, which is where the entire safety argument lives: the
model chooses the split, and the runtime refuses any split that introduces a word the user did not
write. No model is involved in any test below except through an injected fake, so every assertion
is about runtime behaviour rather than about a particular model's phrasing.
"""
from __future__ import annotations

import json

import pytest

from core.agent_runtime.turn_planner import (
    MAX_PLANNED_REQUESTS,
    PlannedTask,
    execution_waves,
    parse_plan,
    plan_turn,
    turn_may_hold_several_requests,
    verify_plan,
)
from core.plain_task_routing import (
    is_ordinary_multi_part_plain_task,
    multipart_has_non_plain_request,
)

THREE_PART = "what is the euro to usd today and will it rain and where i can change my tyres in vilnius"


def _fake_model(payload) -> object:
    """An `ask_model` that returns `payload` verbatim (or raises, if payload is an Exception)."""

    def _ask(_system: str, _prompt: str) -> str:
        if isinstance(payload, BaseException):
            raise payload
        return payload if isinstance(payload, str) else json.dumps(payload)

    return _ask


# --------------------------------------------------------------------------------------------
# The gate: cheap, permissive, and never decides the split
# --------------------------------------------------------------------------------------------

@pytest.mark.parametrize(
    "message, expected",
    [
        (THREE_PART, True),
        ("ok what about gold and silver price?", True),
        ("check the weather in vilnius, compare gold and silver, find a tyre shop", True),
        ("what is the price of bitcoin right now?", False),   # single request, keeps the 0.3s path
        ("hi", False),
        ("", False),
        ("thanks", False),
    ],
)
def test_only_a_plausibly_multi_part_message_costs_a_planner_call(message, expected) -> None:
    assert turn_may_hold_several_requests(message) is expected


# --------------------------------------------------------------------------------------------
# The measured failures, planned correctly
# --------------------------------------------------------------------------------------------

def test_the_three_part_message_becomes_three_requests() -> None:
    plan = plan_turn(THREE_PART, ask_model=_fake_model([
        {"request": "what is the euro to usd today", "depends_on": []},
        {"request": "will it rain in vilnius", "depends_on": []},
        {"request": "where i can change my tyres in vilnius", "depends_on": []},
    ]))
    assert [task.request for task in plan] == [
        "what is the euro to usd today",
        "will it rain in vilnius",
        "where i can change my tyres in vilnius",
    ]


def test_shared_context_may_be_carried_into_a_sibling_request() -> None:
    """"will it rain" has no location of its own; it inherits `vilnius` from a sibling clause.

    This is the case that made the live answer nonsense -- the weather lane took the whole sentence
    as the place name. Carrying the location in is REQUIRED, and it is safe precisely because
    `vilnius` is the user's own word.
    """
    plan = plan_turn(THREE_PART, ask_model=_fake_model([
        {"request": "will it rain in vilnius"},
        {"request": "what is the euro to usd today"},
    ]))
    assert plan and "vilnius" in plan[0].request


# Original report, six clean semantic mutations, and five typo/user-style mutations. The plans are
# fixtures only for the model's structural reply; runtime verification still rejects any request
# containing content the user did not supply.
HETEROGENEOUS_FAMILY = (
    (
        THREE_PART,
        (
            "what is the euro to usd today",
            "will it rain in vilnius",
            "where i can change my tyres in vilnius",
        ),
    ),
    (
        "Give me the current copper price, will it snow in Helsinki, and find a piano tuner nearby.",
        ("current copper price", "will it snow in Helsinki", "find a piano tuner nearby"),
    ),
    (
        "What is GBP to JPY today? Is rain expected in Tokyo? Where can I repair a camera there?",
        ("What is GBP to JPY today", "Is rain expected in Tokyo", "Where can I repair a camera there"),
    ),
    (
        "Look up the latest wheat price, calculate 19 x 7, and tell me the weather in Warsaw.",
        ("latest wheat price", "calculate 19 x 7", "weather in Warsaw"),
    ),
    (
        "Find report.md, calculate 71 x 8, and look up Tallinn's current population online.",
        ("Find report.md", "calculate 71 x 8", "Tallinn's current population online"),
    ),
    (
        "What is diesel price today, will it rain, and where is an open pharmacy in Porto?",
        ("diesel price today", "will it rain in Porto", "where is an open pharmacy in Porto"),
    ),
    (
        "Will it rain in Riga, what is the current train ticket price in Tallinn, and find a bakery in Tallinn.",
        ("Will it rain in Riga", "current train ticket price in Tallinn", "find a bakery in Tallinn"),
    ),
    (
        "whats gold price now and will it snow oslo and where can i get boots fixed there",
        ("gold price now", "will it snow oslo", "where can i get boots fixed there"),
    ),
    (
        "3 things pls: current yen rate; rain in seoul tomorrow; find late dentist nearby",
        ("current yen rate", "rain in seoul tomorrow", "find late dentist nearby"),
    ),
    (
        "need latest corn price, weather berlin, plus locate phone repair near me",
        ("latest corn price", "weather berlin", "locate phone repair near me"),
    ),
    (
        "btc price today?? rain kaunas?? where mend laptop there??",
        ("btc price today", "rain kaunas", "where mend laptop there"),
    ),
    (
        "whts usd cad now, is it raining montreal, also where can we fix a watch there",
        ("usd cad now", "is it raining montreal", "where can we fix a watch there"),
    ),
)


@pytest.mark.parametrize(("prompt", "requests"), HETEROGENEOUS_FAMILY)
def test_non_plain_multipart_families_keep_the_planner_reachable(
    prompt: str,
    requests: tuple[str, ...],
) -> None:
    assert multipart_has_non_plain_request(prompt) is True
    assert is_ordinary_multi_part_plain_task(prompt) is False

    plan = plan_turn(
        prompt,
        ask_model=_fake_model([{"request": request, "depends_on": []} for request in requests]),
    )

    assert [task.request for task in plan] == list(requests)


def test_reported_heterogeneous_turn_also_keeps_the_conductor_reachable() -> None:
    from core.conductor.planner import plan_conductor_turn

    reply = [
        {"request": "what is the euro to usd today", "operation": "market_quote"},
        {"request": "will it rain in vilnius", "operation": "weather_lookup"},
        {
            "request": "where i can change my tyres in vilnius",
            "operation": "missing_information",
        },
    ]

    plan = plan_conductor_turn(THREE_PART, ask_model=_fake_model(reply), plan_id="boundary")

    assert plan is not None
    assert plan.clause_count == 3


def test_different_city_context_is_not_propagated_to_the_wrong_sibling() -> None:
    prompt = (
        "Will it rain in Riga, what is the current train ticket price in Tallinn, "
        "and find a bakery in Tallinn."
    )
    requests = (
        "Will it rain in Riga",
        "current train ticket price in Tallinn",
        "find a bakery in Tallinn",
    )

    plan = plan_turn(prompt, ask_model=_fake_model([{"request": item} for item in requests]))

    assert [task.request for task in plan] == list(requests)
    assert "Tallinn" not in plan[0].request


def test_live_fields_for_one_subject_are_not_forced_into_separate_tasks() -> None:
    """Adversarial near-miss: current data is non-plain, but one subject is still one request."""

    prompt = "Give the current weather, humidity, and wind in Rome."
    assert multipart_has_non_plain_request(prompt) is True
    assert plan_turn(prompt, ask_model=_fake_model([{"request": prompt}])) == []


def test_connectors_alone_do_not_force_conductor_decomposition() -> None:
    prompt = "Explain why salt and pepper complement each other."
    assert multipart_has_non_plain_request(prompt) is False
    assert plan_turn(prompt, ask_model=_fake_model([{"request": prompt}])) == []


def test_single_live_request_still_avoids_a_planner_call() -> None:
    def _must_not_run(*_args, **_kwargs):
        raise AssertionError("a single live request bought a decomposition call")

    assert plan_turn("What is copper trading at right now?", ask_model=_must_not_run) == []


def test_quoted_heterogeneous_example_is_not_read_as_the_users_work() -> None:
    prompt = (
        'Critique the wording "what is copper price today, will it rain, '
        'and where can I repair shoes nearby".'
    )
    assert multipart_has_non_plain_request(prompt) is False


def test_sabotage_disabling_non_plain_detection_recreates_empty_decomposition(monkeypatch) -> None:
    """Load-bearing mutation: a live/local bundle becomes ordinary and the planner is skipped."""

    monkeypatch.setattr(
        "core.plain_task_routing.multipart_has_non_plain_request",
        lambda *_args, **_kwargs: False,
    )
    assert is_ordinary_multi_part_plain_task(THREE_PART) is True
    assert plan_turn(
        THREE_PART,
        ask_model=_fake_model(
            [
                {"request": "what is the euro to usd today"},
                {"request": "will it rain in vilnius"},
                {"request": "where i can change my tyres in vilnius"},
            ]
        ),
    ) == []


def test_gold_and_silver_may_be_planned_as_two_requests() -> None:
    plan = plan_turn("ok what about gold and silver price?", ask_model=_fake_model([
        {"request": "gold price"},
        {"request": "silver price"},
    ]))
    assert [task.request for task in plan] == ["gold price", "silver price"]


# --------------------------------------------------------------------------------------------
# The safety property: the planner may rearrange the user's words, never invent one
# --------------------------------------------------------------------------------------------

@pytest.mark.parametrize(
    "plan_payload, why",
    [
        ([{"request": "will it rain in london"}, {"request": "gold price"}],
         "invented a place the user never named"),
        ([{"request": "what is the euro to usd today"}, {"request": "the bitcoin price"}],
         "invented an asset"),
        ([{"request": "the answer is 21 degrees celsius"}, {"request": "gold price"}],
         "answered instead of splitting"),
        ([{"request": "search google maps for tyre shops"}, {"request": "gold price"}],
         "invented a tool/vendor"),
    ],
)
def test_a_plan_that_invents_content_is_rejected_whole(plan_payload, why) -> None:
    """A rejected plan falls back to today's single-request behaviour, which is why rejecting the
    WHOLE plan is correct -- answering the clean half of an invented plan is how a fabricated
    request would reach the user."""
    assert plan_turn(THREE_PART, ask_model=_fake_model(plan_payload)) == [], why


def test_verify_plan_allows_only_the_users_own_content_words() -> None:
    original = "gold and silver price in vilnius"
    assert verify_plan([PlannedTask(0, "gold price"), PlannedTask(1, "silver price")], original)
    assert verify_plan([PlannedTask(0, "gold price in vilnius")], original), "inheritance is allowed"
    assert not verify_plan([PlannedTask(0, "platinum price")], original)
    assert not verify_plan([], original), "an empty plan is not a valid plan"


def test_function_words_do_not_count_as_invented_content() -> None:
    """The planner must be free to make a fragment grammatical without tripping verification."""
    assert verify_plan([PlannedTask(0, "what is the gold price")], "gold and silver price")


# --------------------------------------------------------------------------------------------
# Adversarial planner replies
# --------------------------------------------------------------------------------------------

@pytest.mark.parametrize(
    "reply",
    [
        "",
        "I'm sorry, I can't do that.",
        "{}",
        "[]",
        "[1, 2, 3]",
        '[{"request": ""}]',
        "not json at all",
        '[{"request": "gold price", "depends_on": ["oops"]}]',
        '[{"request": "gold price", "depends_on": [5]}]',      # forward dependency
        '[{"request": "gold price", "depends_on": [0]}]',      # depends on itself
    ],
)
def test_an_unusable_planner_reply_falls_back_to_one_request(reply) -> None:
    assert plan_turn(THREE_PART, ask_model=_fake_model(reply)) == []


def test_a_planner_that_raises_does_not_take_the_turn_down() -> None:
    assert plan_turn(THREE_PART, ask_model=_fake_model(RuntimeError("provider exploded"))) == []


def test_a_fenced_or_chatty_reply_is_still_parsed() -> None:
    """Told not to, models still emit fences. Re-asking costs a whole round trip."""
    fenced = '```json\n[{"request": "gold price"}, {"request": "silver price"}]\n```'
    assert len(parse_plan(fenced)) == 2
    chatty = 'Sure! Here is the plan:\n[{"request": "gold price"}, {"request": "silver price"}]\nHope that helps.'
    assert len(parse_plan(chatty)) == 2


def test_a_runaway_plan_is_rejected_rather_than_truncated() -> None:
    """Answering an arbitrary prefix of a runaway plan is the silent-drop defect again."""
    payload = [{"request": f"gold price {n}"} for n in range(MAX_PLANNED_REQUESTS + 1)]
    assert parse_plan(json.dumps(payload)) == []


def test_a_single_request_plan_keeps_the_ordinary_path() -> None:
    assert plan_turn(THREE_PART, ask_model=_fake_model([{"request": "gold price"}])) == []


# --------------------------------------------------------------------------------------------
# Waves: parallel where independent, ordered where not
# --------------------------------------------------------------------------------------------

def test_independent_requests_form_one_concurrent_wave() -> None:
    tasks = [PlannedTask(0, "a"), PlannedTask(1, "b"), PlannedTask(2, "c")]
    assert [[task.index for task in wave] for task in [None] for wave in execution_waves(tasks)] == [[0, 1, 2]]


def test_a_dependent_request_runs_after_the_one_it_needs() -> None:
    """"the weather in the city where the race is" cannot be fanned out flat -- running it in
    parallel produces a confident wrong answer rather than a slow right one."""
    tasks = [
        PlannedTask(0, "where is the race"),
        PlannedTask(1, "the weather there", depends_on=(0,)),
        PlannedTask(2, "gold price"),
    ]
    waves = [[task.index for task in wave] for wave in execution_waves(tasks)]
    assert waves == [[0, 2], [1]], f"dependency not respected: {waves}"


def test_no_request_is_dropped_even_if_dependencies_are_impossible() -> None:
    """`parse_plan` cannot emit a cycle, but losing a request is the defect this module exists to
    fix, so an impossible graph still runs everything rather than silently shrinking."""
    tasks = [PlannedTask(0, "a", depends_on=(1,)), PlannedTask(1, "b", depends_on=(0,))]
    planned = {task.index for wave in execution_waves(tasks) for task in wave}
    assert planned == {0, 1}


# --------------------------------------------------------------------------------------------
# Execution and merging: one failure must not cost the other answers, and must be SAID
# --------------------------------------------------------------------------------------------

from core.agent_runtime.turn_planner import TaskOutcome, merge_outcomes, run_plan

_THREE = [
    PlannedTask(0, "what is the euro to usd today"),
    PlannedTask(1, "will it rain in vilnius"),
    PlannedTask(2, "where i can change my tyres in vilnius"),
]


def test_every_planned_request_is_run_and_answered() -> None:
    outcomes = run_plan(_THREE, run_one=lambda task, done: f"answer:{task.index}")
    assert [outcome.answer for outcome in outcomes] == ["answer:0", "answer:1", "answer:2"]
    assert all(outcome.ok for outcome in outcomes)


def test_one_failing_request_does_not_cost_the_others() -> None:
    """The reason a person asks several things at once is to get several answers."""

    def _run(task, _done):
        if task.index == 1:
            raise RuntimeError("no FX lane exists")
        return f"answer:{task.index}"

    outcomes = run_plan(_THREE, run_one=_run)
    assert [o.ok for o in outcomes] == [True, False, True]
    assert "no FX lane exists" in outcomes[1].error


def test_an_empty_answer_counts_as_a_failure_not_a_silent_pass() -> None:
    outcomes = run_plan(_THREE, run_one=lambda task, done: "" if task.index == 2 else "ok")
    assert outcomes[2].ok is False


def test_the_merged_reply_names_what_it_could_not_answer() -> None:
    """Three answers out of four in silence is 'answered gold, dropped silver' one level up."""
    merged = merge_outcomes([
        TaskOutcome(task=_THREE[0], answer="EUR/USD is 1.09."),
        TaskOutcome(task=_THREE[1], error="no FX lane exists"),
        TaskOutcome(task=_THREE[2], answer="Three tyre shops in Vilnius: ..."),
    ])
    assert "EUR/USD is 1.09." in merged
    assert "Three tyre shops in Vilnius" in merged
    assert "could not answer" in merged.lower()
    assert "will it rain in vilnius" in merged, "the unanswered request is named, not dropped"


def test_a_fully_answered_plan_carries_no_failure_note() -> None:
    merged = merge_outcomes([
        TaskOutcome(task=_THREE[0], answer="A."),
        TaskOutcome(task=_THREE[1], answer="B."),
    ])
    assert merged == "A.\n\nB."
    assert "could not answer" not in merged.lower()


def test_a_dependent_request_can_read_what_it_waited_for() -> None:
    tasks = [PlannedTask(0, "where is the race"), PlannedTask(1, "weather there", depends_on=(0,))]
    seen: dict[int, list[int]] = {}

    def _run(task, done):
        seen[task.index] = sorted(done)
        return f"answer:{task.index}"

    run_plan(tasks, run_one=_run)
    assert seen[0] == [], "the first request waits for nothing"
    assert seen[1] == [0], "the dependent request sees the outcome it depended on"


def test_concurrent_execution_returns_every_answer_in_order() -> None:
    """Eight independent requests, run through the pool, none lost or reordered."""
    tasks = [PlannedTask(index, f"request {index}") for index in range(8)]
    outcomes = run_plan(tasks, run_one=lambda task, done: f"answer:{task.index}", max_workers=4)
    assert [outcome.answer for outcome in outcomes] == [f"answer:{n}" for n in range(8)]
