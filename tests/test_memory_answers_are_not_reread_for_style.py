"""An answer read from memory records is not sent back to the reader for its length.

The defect (external review of the frozen code, checked against it): on an ordinary-chat turn the
response guard rejects an answer over the 64-word ceiling (`ordinary_response_too_long`) or a
one-word reply to a why/how question (`ordinary_answer_too_short`). On a free lane the router then
runs the reader again with the whole request -- every retrieved record again -- plus a style
instruction. The review measured, on a 94-question memory check, six such second passes costing
57,579 tokens (about 6% of the run), five on preference questions, and one rewrite that moved an
unrelated preference to the front and changed the meaning. Where no rewrite runs, the long answer
is cut at the word ceiling (a list loses its later items) and the short one is replaced by the safe
fallback.

The contract after the repair:
  * the policy says whether the reader request carries retrieved memory records
    (`memory_records_supplied`); the server derives it from the request's own system messages,
    never from the user's wording;
  * on such a turn the two length verdicts are recorded (`waived_reasons`) and not enforced: the
    reader is called once and the answer ships as written, every list item kept;
  * every other verdict (an image-prompt block, an earlier turn's code, a missing requested part,
    an unrequested comparison) still sends the answer back exactly as before;
  * a turn without records keeps the old path in full, and a bare list marker is still not a
    complete short answer.

The seams asserted are the reader call count at the router (`adapter.run_text_task`) and the exact
text the final display check ships. Every name, place and sentence here is synthetic.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

import pytest

import core.memory_first_router as mfr
import core.ordinary_chat_response_guard as guard
from adapters.base_adapter import ModelResponse
from core.agent_runtime.response import _validate_final_chat_output
from core.memory_first_router import MemoryFirstRouter
from storage.model_provider_manifest import ModelProviderManifest

RECORDS = (
    "<retrieved_context>\n"
    "[2024-03-02] Odile: I'm vegetarian, and please keep coriander away from my plate.\n"
    "[2024-03-09] User: The allotment by the canal gives me runner beans, chard and plums from "
    "Bram's tree.\n"
    "[2024-04-14] Teodor: Skip the overhang wall for two weeks and ice the shoulder.\n"
    "[2024-05-20] User: Ysolde showed me the damp sponge trick for trimming pots.\n"
    "</retrieved_context>"
)

READER_LEAD = (
    "Answer using the notes from our earlier chats. You may give only answers those notes "
    "support. If the notes do not support an answer, say you do not know. Give a concise final "
    "answer.\n"
)

REWRITE = "A short rewritten answer."


def _words(text: str) -> int:
    return len(guard._WORD_RE.findall(text))


def _reader_request(
    user_text: str,
    *,
    records: bool = True,
    history: tuple[tuple[str, str], ...] = (),
    output_mode: str = "plain_text",
):
    """The provider request for one turn, built by the router; `normalize_prompt` is fixed.

    The retrieved records reach the reader as their own system message, exactly where the
    runtime places them; `records=False` builds the same turn with no records.
    """
    wire = [
        {"role": "system", "content": "BASE SYSTEM"},
        *({"role": role, "content": content} for role, content in history),
        *([{"role": "system", "content": RECORDS}] if records else []),
        {"role": "user", "content": user_text},
    ]
    internal = SimpleNamespace(
        metadata={},
        temperature=0.2,
        max_output_tokens=512,
        context_summary="",
        trace_id="memory-answer-length-trace",
        attachments=(),
        messages=[SimpleNamespace(metadata={}, **message) for message in wire],
        system_prompt=lambda: "BASE SYSTEM",
        user_prompt=lambda: user_text,
        as_openai_messages=lambda: [dict(message) for message in wire],
    )
    interpretation = SimpleNamespace(
        raw_text=user_text, normalized_text=user_text, user_text="", understanding_confidence=0.9
    )
    router = MemoryFirstRouter.__new__(MemoryFirstRouter)
    with mock.patch.object(mfr, "normalize_prompt", return_value=internal):
        return MemoryFirstRouter._build_request(
            router,
            task=None,
            classification={"task_class": "chat"},
            interpretation=interpretation,
            context_result=None,
            persona=None,
            output_mode=output_mode,
            task_kind="conversation",
            surface="openclaw",
            source_context={},
        )


def _policy(request) -> dict:
    return dict(request.metadata["ordinary_chat_output_policy"])


def _manifest() -> ModelProviderManifest:
    return ModelProviderManifest(
        provider_name="ollama-local",
        model_name="qwen2.5:7b",
        source_type="http",
        adapter_type="cloud_fallback_provider",
        license_name="provider terms",
        license_reference="https://example.invalid/terms",
        weight_location="external",
        runtime_dependency="openai-compatible",
        capabilities=["summarize"],
        runtime_config={
            "base_url": "http://127.0.0.1:11434/v1",
            "api_path": "/chat/completions",
            "timeout_seconds": 5.0,
        },
        metadata={
            "deployment_class": "local",
            "cost_class": "free_local",
            "runtime_family": "openai-compatible",
        },
    )


def _invoke(request, replies: list[str], *, cost: str = "free_local"):
    """Run the router's provider seam once with a scripted reader; return the reader's calls.

    `cost` is the reported spend class: free lanes may take the one bounded rewrite, a paid lane
    may not. More replies than calls are scripted, so an extra reader call shows up as a count,
    never as an exhausted script.
    """
    adapter = mock.Mock()
    adapter.run_text_task.side_effect = [
        ModelResponse(text, usage={"completion_tokens": 40}, finish_reason="stop")
        for text in replies
    ]
    router = MemoryFirstRouter(registry=mock.Mock())
    router.registry.build_adapter.return_value = adapter
    context: dict[str, object] = {}
    with (
        mock.patch("core.memory_first_router.should_probe_health", return_value=False),
        mock.patch("core.memory_first_router.circuit_is_open", return_value=False),
        mock.patch("core.memory_first_router.provider_cost_class", return_value="free_local"),
        mock.patch("core.memory_first_router.reported_cost_class", return_value=cost),
    ):
        _, response, error = router._invoke_manifest(
            manifest=_manifest(),
            request=request,
            output_mode="plain_text",
            task=SimpleNamespace(task_id="memory-answer-length"),
            source_context=context,
        )
    assert error is None and response is not None
    return response, adapter.run_text_task.call_count, context


def _receipt(context: dict) -> dict:
    return dict(dict(context["response_control"])["ordinary_chat_output"])


# ---------------------------------------------------------------------------------------------
# The family: answers read from records that run past the ceiling
# ---------------------------------------------------------------------------------------------

DINNER = (
    "Going by your notes, these fit the evening:\n"
    "1. A runner bean and chard risotto, since both come from your allotment and Odile is "
    "vegetarian.\n"
    "2. Roasted squash with lemon and hazelnuts, leaving out coriander, which you avoid.\n"
    "3. A tomato and white bean stew that you can make the day before, because you said "
    "Saturdays are busy.\n"
    "4. A plum crumble for dessert, using the plums from Bram's tree.\n"
    "The risotto suits best if you want to show off the allotment."
)
BIRDS = (
    "You logged six species across the three estuary walks. On the first walk in March you saw a "
    "curlew and a pair of avocets near the sluice. In April you added a little egret, a whimbrel "
    "on the mudflats, and a marsh harrier over the reed beds. The last walk in May gave you a "
    "spoonbill, which you called the best sighting of the season and planned to report to the "
    "county recorder."
)
SHOULDER = (
    "Teodor gave you three things to do for the shoulder. He told you to skip the overhang wall "
    "for two weeks and stick to slab routes, to do band pull-aparts and slow wall slides every "
    "evening, and to ice it for ten minutes after each session. He also said to book a physio "
    "appointment if the clicking was still there by the end of the month, and to tape the finger "
    "you jammed."
)
POTTERY = (
    "In the first week you centred clay for the first time and threw two lopsided bowls. The "
    "second week was trimming, and you said Ysolde's tip about a damp sponge finally made it "
    "click. In week three you glazed four mugs in a speckled oatmeal glaze, and one cracked in "
    "the kiln. The final week you made a lidded jar for your sister, which came out even, and you "
    "signed up for the spring term afterwards."
)
PRESENTS = (
    "You have bought four of the five presents so far:\n"
    "- a jar of heather honey for Fenna, from the farmers' market stall you liked best\n"
    "- a pocket songbook for Corwin, because he keeps borrowing yours\n"
    "- warm fingerless gloves for Mirela, since the rehearsal hall is cold\n"
    "- a set of tea tins for Kasimir, wrapped and ready\n"
    "You still need something for Wren, and you planned to wrap them all together on Sunday "
    "afternoon."
)
CLUB = (
    "You stopped after the club moved the Thursday ride to the early morning slot in September "
    "last year. You said it clashed with dropping your son at school, and that the new route along "
    "the ring road felt unsafe in the dark. You also mentioned that two friends you used to ride "
    "with had left the club, so you started doing the Saturday gravel loop on your own instead."
)
# A list whose later items sit past word 64: the word-ceiling cut drops items six to eight.
AUTUMN_JOBS = (
    "Your autumn jobs, in the order you planned them:\n"
    "1. lift the last of the early potatoes before the frost\n"
    "2. cut back the runner beans and save seed from the best pods\n"
    "3. mulch the rainbow chard bed with leaf mould from the shared heap\n"
    "4. plant Bram's garlic cloves along the south edge\n"
    "5. move the compost bins nearer the gate\n"
    "6. sow green manure on the empty potato rows\n"
    "7. fix the broken hinge on the shed door\n"
    "8. clean and oil the hand tools before storing them."
)

OVER_CEILING_FAMILY = [
    # the reported shape: a preference question on a reader turn that asks for a concise answer
    pytest.param(READER_LEAD + "What could I cook for the Saturday dinner with Odile?", DINNER,
                 id="reported-preference-reader-turn"),
    # clean paraphrases: other question shapes and other remembered domains
    pytest.param("Which birds did I tick off on the estuary walks this spring?", BIRDS,
                 id="clean-recall-of-several-sightings"),
    pytest.param("Remind me what Teodor told me to do about my sore shoulder.", SHOULDER,
                 id="clean-remind-me-instructions"),
    pytest.param("How did the pottery course go, week by week?", POTTERY, id="clean-how-did-it-go"),
    pytest.param("Which presents have I already bought for the choir swap?", PRESENTS,
                 id="clean-bulleted-items"),
    pytest.param("Why did I stop riding with the Thursday club?", CLUB, id="clean-why-reasons"),
    pytest.param(READER_LEAD + "What jobs did I plan for the allotment this autumn?", AUTUMN_JOBS,
                 id="clean-numbered-plan-reader-turn"),
    # sloppy, user-typed variants
    pytest.param("wat birds did i see at the estuary this spring", BIRDS, id="sloppy-typo-no-mark"),
    pytest.param("remind me teodors advice abt my shoulder", SHOULDER, id="sloppy-fragment"),
    pytest.param("pottery course how did it go??", POTTERY, id="sloppy-reordered"),
    pytest.param("presents i got for choir swap", PRESENTS, id="sloppy-bare-fragment"),
    pytest.param("WHY DID I QUIT THE THURSDAY RIDES", CLUB, id="sloppy-shouting"),
    pytest.param("ok so autumn jobs on the allotment, what did i plan", AUTUMN_JOBS,
                 id="sloppy-casual"),
]


@pytest.mark.parametrize("turn,answer", OVER_CEILING_FAMILY)
def test_the_family_answer_breaks_only_the_word_ceiling(turn: str, answer: str) -> None:
    """Precondition: without records, each answer's one verdict is the length verdict."""
    assert 64 < _words(answer) < 120, _words(answer)
    policy = _policy(_reader_request(turn, records=False))
    assert policy["max_words"] == "64", policy["max_words"]
    verdict = guard.inspect_ordinary_chat_output(answer, policy, current_user_text=turn)
    assert verdict.reasons == ("ordinary_response_too_long",), verdict


@pytest.mark.parametrize("turn,answer", OVER_CEILING_FAMILY)
def test_an_over_ceiling_answer_read_from_records_is_not_read_again(
    turn: str, answer: str
) -> None:
    request = _reader_request(turn)
    assert _policy(request)["memory_records_supplied"] is True
    response, calls, context = _invoke(request, [answer, REWRITE])
    assert calls == 1, "a length-only verdict on a memory answer took a second reader call"
    assert response.output_text == answer
    receipt = _receipt(context)
    assert receipt["allowed"] is True
    assert receipt["waived_reasons"] == ["ordinary_response_too_long"]


@pytest.mark.parametrize("turn,answer", OVER_CEILING_FAMILY)
def test_without_records_the_same_answer_still_takes_the_rewrite(turn: str, answer: str) -> None:
    """Negative control: the old path is unchanged for a turn that carries no records."""
    request = _reader_request(turn, records=False)
    assert _policy(request)["memory_records_supplied"] is False
    response, calls, context = _invoke(request, [answer, REWRITE])
    assert calls == 2
    assert response.output_text == REWRITE
    assert "waived_reasons" not in _receipt(context)


def test_the_family_saves_one_reader_call_per_length_only_verdict() -> None:
    """The measurable effect, counted at the seam over the whole family."""
    with_records = without_records = 0
    for param in OVER_CEILING_FAMILY:
        turn, answer = param.values
        with_records += _invoke(_reader_request(turn), [answer, REWRITE])[1]
        without_records += _invoke(_reader_request(turn, records=False), [answer, REWRITE])[1]
    assert (with_records, without_records) == (
        len(OVER_CEILING_FAMILY),
        2 * len(OVER_CEILING_FAMILY),
    )


def test_a_general_question_over_the_ceiling_still_takes_the_rewrite() -> None:
    """Negative control: an ordinary chat answer with no records, outside any remembered domain."""
    turn = "Why does a sundial run slow in some months?"
    answer = (
        "A sundial follows the real sun, and clocks follow an averaged sun. Because the orbit is an "
        "ellipse, the planet moves faster near perihelion in early January and slower in early "
        "July, and the tilt of the axis adds a second wobble. Together they make solar noon drift "
        "by up to about sixteen minutes across the year, so in some months the dial reads behind a "
        "clock and in others ahead of it."
    )
    request = _reader_request(turn, records=False)
    assert guard.inspect_ordinary_chat_output(
        answer, _policy(request), current_user_text=turn
    ).reasons == ("ordinary_response_too_long",)
    response, calls, _ = _invoke(request, [answer, REWRITE])
    assert calls == 2
    assert response.output_text == REWRITE


# ---------------------------------------------------------------------------------------------
# The family: complete short answers read from records
# ---------------------------------------------------------------------------------------------

SHORT_FAMILY = [
    pytest.param(READER_LEAD + "Why did I give up the allotment plot by the canal?", "Flooding.",
                 id="reported-shape-reader-turn"),
    pytest.param("Why did Ysolde move the pottery class to Tuesdays?", "Renovations.",
                 id="clean-why"),
    pytest.param("How did I get to the choir retreat?", "Minibus.", id="clean-how-did"),
    pytest.param("How come I skipped the spring book fair?", "Flu.", id="clean-how-come"),
    pytest.param("Why did Corwin leave the choir in March?", "Relocation.", id="clean-why-person"),
    pytest.param("How did I hurt the finger I jammed?", "Bouldering.", id="clean-how-cause"),
    pytest.param("why did teodor tape my finger", "Sprain.", id="sloppy-no-mark"),
    pytest.param("WHY did i swap the road bike", "Commuting.", id="sloppy-caps"),
    pytest.param("how did odile react to the risotto??", "Delighted", id="sloppy-no-full-stop"),
    pytest.param("how come i missed the plant swap", "Illness.", id="sloppy-lowercase"),
    pytest.param("why'd Wren skip the poster meeting", "Deadline.", id="sloppy-contraction"),
]


@pytest.mark.parametrize("turn,answer", SHORT_FAMILY)
def test_the_short_answer_breaks_only_the_too_short_rule(turn: str, answer: str) -> None:
    """Precondition: without records, each one-word answer is withdrawn as too short."""
    policy = _policy(_reader_request(turn, records=False))
    verdict = guard.inspect_ordinary_chat_output(answer, policy, current_user_text=turn)
    assert verdict.reasons == ("ordinary_answer_too_short",), verdict


@pytest.mark.parametrize("turn,answer", SHORT_FAMILY)
def test_a_complete_short_answer_read_from_records_is_not_read_again(
    turn: str, answer: str
) -> None:
    response, calls, context = _invoke(_reader_request(turn), [answer, REWRITE])
    assert calls == 1, "a too-short verdict on a complete memory answer took a second reader call"
    assert response.output_text == answer
    assert _receipt(context)["waived_reasons"] == ["ordinary_answer_too_short"]


@pytest.mark.parametrize("turn,answer", SHORT_FAMILY)
def test_without_records_a_one_word_answer_is_still_sent_back(turn: str, answer: str) -> None:
    response, calls, _ = _invoke(_reader_request(turn, records=False), [answer, REWRITE])
    assert calls == 2
    assert response.output_text == REWRITE


# ---------------------------------------------------------------------------------------------
# Where no rewrite is authorized: the memory answer is neither cut nor replaced
# ---------------------------------------------------------------------------------------------


def test_on_a_lane_without_rewrites_a_listed_memory_answer_is_not_cut() -> None:
    turn = "What jobs did I plan for the allotment this autumn?"
    response, calls, _ = _invoke(_reader_request(turn), [AUTUMN_JOBS], cost="paid_cloud")
    assert calls == 1
    assert response.output_text == AUTUMN_JOBS
    # Negative control: the same answer on a turn without records is still cut at the ceiling.
    cut, calls, _ = _invoke(_reader_request(turn, records=False), [AUTUMN_JOBS], cost="paid_cloud")
    assert calls == 1
    assert "fix the broken hinge" not in cut.output_text


def test_on_a_lane_without_rewrites_a_short_memory_answer_is_not_replaced() -> None:
    turn = "Why did Ysolde move the pottery class to Tuesdays?"
    response, _, _ = _invoke(_reader_request(turn), ["Renovations."], cost="paid_cloud")
    assert response.output_text == "Renovations."
    replaced, _, _ = _invoke(
        _reader_request(turn, records=False), ["Renovations."], cost="paid_cloud"
    )
    assert replaced.output_text == guard.ordinary_chat_safe_fallback()


# ---------------------------------------------------------------------------------------------
# The final display check ships the same text
# ---------------------------------------------------------------------------------------------


def _display(answer: str, turn: str, *, records: bool) -> tuple[str, dict]:
    context: dict[str, object] = {
        "ordinary_chat_output_policy": _policy(_reader_request(turn, records=records)),
    }
    return _validate_final_chat_output(answer, source_context=context), context


def test_the_display_check_ships_a_listed_memory_answer_whole() -> None:
    turn = "What jobs did I plan for the allotment this autumn?"
    shipped, context = _display(AUTUMN_JOBS, turn, records=True)
    assert shipped == AUTUMN_JOBS
    final_ui = dict(dict(context["response_control"])["final_ui"])
    assert final_ui["fallback_applied"] is False
    assert final_ui["ordinary_chat_output"]["waived_reasons"] == ["ordinary_response_too_long"]


def test_the_display_check_still_cuts_an_answer_without_records() -> None:
    """Negative control, and the harm the waiver removes: the cut drops items six to eight."""
    turn = "What jobs did I plan for the allotment this autumn?"
    shipped, _ = _display(AUTUMN_JOBS, turn, records=False)
    assert "move the compost bins nearer the gate" in shipped
    assert "fix the broken hinge on the shed door" not in shipped
    assert "clean and oil the hand tools" not in shipped


# ---------------------------------------------------------------------------------------------
# Non-length verdicts on a memory turn still take the rewrite
# ---------------------------------------------------------------------------------------------


def test_an_image_prompt_block_on_a_memory_turn_is_still_rewritten() -> None:
    turn = "What did Wren paint for the choir poster?"
    answer = (
        "Subject: a choir on a hill at dusk.\n"
        "Camera: wide shot, 35mm lens.\n"
        "Lighting: cinematic golden hour with film grain."
    )
    request = _reader_request(turn)
    verdict = guard.inspect_ordinary_chat_output(answer, _policy(request), current_user_text=turn)
    assert verdict.reasons == ("image_prompt_shaped_output",)
    assert verdict.waived_reasons == ()
    response, calls, _ = _invoke(request, [answer, REWRITE])
    assert calls == 2
    assert response.output_text == REWRITE


def test_an_earlier_turns_code_in_a_memory_answer_is_still_rewritten() -> None:
    history = (
        ("user", "Keep the locker code FENNEL-5530 safe for me."),
        ("assistant", "Noted."),
    )
    turn = "Which shoes did I leave at the climbing gym?"
    answer = "Your blue approach shoes are in locker FENNEL-5530 at the gym."
    request = _reader_request(turn, history=history)
    verdict = guard.inspect_ordinary_chat_output(answer, _policy(request), current_user_text=turn)
    assert verdict.reasons == ("unrequested_prior_turn_literal",)
    clean = "Your blue approach shoes are in your locker at the gym."
    response, calls, _ = _invoke(request, [answer, clean])
    assert calls == 2
    assert response.output_text == clean


def test_a_missing_requested_part_on_a_memory_turn_is_still_rewritten() -> None:
    turn = "When does the Thursday choir rehearsal start? Which hall does it use?"
    answer = "1. Half past seven."
    request = _reader_request(turn)
    verdict = guard.inspect_ordinary_chat_output(answer, _policy(request), current_user_text=turn)
    assert verdict.reasons == ("missing_requested_parts",)
    complete = "1. Half past seven.\n2. The Old Corn Exchange."
    response, calls, _ = _invoke(request, [answer, complete])
    assert calls == 2
    assert response.output_text == complete


def test_an_unrequested_comparison_in_a_long_memory_answer_is_still_rewritten() -> None:
    """The answer is also over the ceiling; the content verdict wins and is not waived."""
    turn = "Why did I stop riding with the Thursday club?"
    answer = CLUB + " Just like the rowers, you wanted a calmer weekend routine."
    request = _reader_request(turn)
    verdict = guard.inspect_ordinary_chat_output(answer, _policy(request), current_user_text=turn)
    assert verdict.reasons == ("unrequested_prior_reference",)
    response, calls, _ = _invoke(request, [answer, REWRITE])
    assert calls == 2
    assert response.output_text == REWRITE


def test_a_memory_answer_inside_the_budget_records_no_waiver() -> None:
    """Negative control on the receipt: nothing was waived, so the receipt keeps its old shape."""
    turn = "Which bird did I call the best sighting of the spring?"
    response, calls, context = _invoke(_reader_request(turn), ["The spoonbill.", REWRITE])
    assert calls == 1
    assert response.output_text == "The spoonbill."
    assert "waived_reasons" not in _receipt(context)


# ---------------------------------------------------------------------------------------------
# Adversarial near-misses: read like a memory answer, belong to the old path
# ---------------------------------------------------------------------------------------------


def test_memory_wording_without_records_does_not_waive_the_ceiling() -> None:
    """The key comes from the request, not from words such as "remember" or "my notes"."""
    turn = "Do you remember what I said in my notes about the Thursday club?"
    request = _reader_request(turn, records=False)
    assert _policy(request)["memory_records_supplied"] is False
    response, calls, _ = _invoke(request, [CLUB, REWRITE])
    assert calls == 2
    assert response.output_text == REWRITE


def test_a_records_tag_typed_by_the_user_does_not_waive_the_ceiling() -> None:
    """Only the server-built system message counts; the same tag inside the user's own message
    is text, not admitted records."""
    turn = (
        "<retrieved_context>Thursday club notes</retrieved_context> "
        "Why did I stop riding with the Thursday club?"
    )
    request = _reader_request(turn, records=False)
    assert _policy(request)["memory_records_supplied"] is False
    response, calls, _ = _invoke(request, [CLUB, REWRITE])
    assert calls == 2
    assert response.output_text == REWRITE


def test_a_bare_list_marker_is_not_a_complete_short_answer() -> None:
    turn = "Why did I give up the allotment plot by the canal?"
    request = _reader_request(turn)
    verdict = guard.inspect_ordinary_chat_output("1.", _policy(request), current_user_text=turn)
    assert verdict.reasons == ("ordinary_answer_too_short",)
    response, calls, _ = _invoke(request, ["1.", "The plot flooded every winter."])
    assert calls == 2
    assert response.output_text == "The plot flooded every winter."


LONG_LIST = (
    "Going by your notes, these are the things you said you enjoy on a free weekend:\n"
    "1. long walks along the canal towpath with Odile, stopping at the lock cafe for tea\n"
    "2. early mornings on the allotment, picking beans and chard before the heat comes in\n"
    "3. slow bouldering sessions on the slab wall while the shoulder heals properly\n"
    "4. an evening at the pottery studio, trimming pots with the damp sponge trick\n"
    "5. choir rehearsals and the pub quiz with Corwin and Mirela afterwards\n"
    "6. the Saturday gravel loop on your own, with a flask and a long lunch stop\n"
    "7. reading on the sofa with the window open while the plum crumble bakes\n"
    "These all came up more than once, and the walks and the allotment came up most often."
)


def test_a_long_listed_memory_answer_keeps_the_overanswer_path() -> None:
    """Near-miss: 120+ words in a 3+ item list is the overanswer rule, which this repair leaves
    unchanged; it is not a length-only verdict and is not waived."""
    turn = "What do I like doing on a free weekend?"
    assert _words(LONG_LIST) >= 120
    request = _reader_request(turn)
    verdict = guard.inspect_ordinary_chat_output(
        LONG_LIST, _policy(request), current_user_text=turn
    )
    assert verdict.reasons == ("boilerplate_overanswer",)
    assert verdict.waived_reasons == ()
    _, calls, _ = _invoke(request, [LONG_LIST, REWRITE])
    assert calls == 2


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Out of scope for this repair (kept unchanged on purpose): the overanswer rule still "
        "rereads a long listed memory answer on a free lane, and when the rewrite overanswers "
        "again its recovery keeps only the prose before the first list line, so every item is "
        "dropped. Recorded here as evidence; flip to a plain test when that path is repaired."
    ),
)
def test_a_long_listed_memory_answer_keeps_every_item_under_the_overanswer_rule() -> None:
    turn = "What do I like doing on a free weekend?"
    response, calls, _ = _invoke(_reader_request(turn), [LONG_LIST, LONG_LIST])
    assert "the Saturday gravel loop on your own" in response.output_text
    assert calls == 1


# ---------------------------------------------------------------------------------------------
# Cross-turn: the waiver belongs to the request that carries the records
# ---------------------------------------------------------------------------------------------


def test_a_follow_up_that_carries_records_is_not_read_again() -> None:
    history = (
        ("user", "Which birds did I tick off on the estuary walks this spring?"),
        ("assistant", "Six species, ending with a spoonbill in May."),
    )
    turn = "and the march walk, what was there"
    answer = (
        "On the March walk you saw a curlew probing the soft mud and a pair of avocets near the "
        "sluice gate. You wrote that the avocets stayed for most of the morning, that the tide was "
        "unusually low, and that a heron kept flushing the smaller waders along the far bank. You "
        "also noted a flock of brent geese overhead on the way back, though you were not sure of "
        "the count."
    )
    assert _words(answer) > 64
    response, calls, context = _invoke(_reader_request(turn, history=history), [answer, REWRITE])
    assert calls == 1
    assert response.output_text == answer
    assert _receipt(context)["waived_reasons"] == ["ordinary_response_too_long"]


def test_a_later_turn_without_records_does_not_inherit_the_waiver() -> None:
    """A memory turn earlier in the chat does not make the next turn a memory turn."""
    history = (
        ("user", "Why did I stop riding with the Thursday club?"),
        ("assistant", CLUB),
    )
    turn = "ok and why do clubs ride in a line like that"
    answer = (
        "Riding in a line lets everyone behind the leader sit in a pocket of slower air, which can "
        "save a large share of the effort at speed. Clubs rotate the front rider so nobody does all "
        "the work, keep a steady pace so the line stays together, and pass calls back about holes "
        "and traffic. It also keeps the group narrow on busy roads, which drivers find easier to "
        "pass safely."
    )
    request = _reader_request(turn, records=False, history=history)
    assert _policy(request)["memory_records_supplied"] is False
    response, calls, _ = _invoke(request, [answer, REWRITE])
    assert calls == 2
    assert response.output_text == REWRITE


# ---------------------------------------------------------------------------------------------
# The key itself: derived by the server, ordinary chat only
# ---------------------------------------------------------------------------------------------


def test_the_key_is_set_only_for_an_ordinary_chat_request_that_carries_records() -> None:
    turn = "Which birds did I tick off on the estuary walks this spring?"
    assert _policy(_reader_request(turn))["memory_records_supplied"] is True
    assert _policy(_reader_request(turn, records=False))["memory_records_supplied"] is False
    structured = guard.ordinary_chat_output_policy(
        prompt_profile="chat_minimal",
        output_mode="json_object",
        user_text=turn,
        memory_records_supplied=True,
    )
    assert structured["memory_records_supplied"] is False
    assert structured["mode"] == "not_applicable"
