"""A lasting fact the person mentions in passing is kept, in whatever shape it is said.

Measured on the real app (an independent question set, the v0.8 line with the keep step, a real local model):
"lavender oil on the pillow eases my hay fever.", "a spoon of blackstrap molasses goes in my evening milk, nothing
else." and "the coat with the torn lining goes to the tailor on mill street." were answered with a pleasant remark,
no memory row was written, and the later questions had nothing to find. The keep step knew a personal fact only by a
few shapes ("my ... is ...", "I'm allergic to ...").

Whether a sentence no shape lists is a lasting fact about the person is a judgement by the loaded local model. The
tests reach no model, so the judgement is stood in for where a test needs a yes or a no; left alone (no judge),
nothing more is kept than before. All names and facts here are invented for this contract.
"""
from __future__ import annotations

import pytest

from core import intent_arbiter
from core.memory import everyday_remember as everyday
from core.memory import keep_receipt
from core.memory.entries import list_memory_entries
from core.persistent_memory import keep_stated_facts
from tests.test_memory_forget_resurrection_p0 import _policy, isolated_home

OWNER = {"_owner_local": True}
RYE = "rye bread from the Tuesday market is the only one that agrees with my stomach."
CASE = "the green suitcase with the broken zip goes to the repair stall at the covered market."


def _rows(chat: str = "c1") -> list[str]:
    return [str(row.get("fact") or row.get("text") or "") for row in list_memory_entries(access_policy=_policy(chat), limit=50)]


def _keep(text: str, chat: str = "c1", context: dict | None = None) -> int:
    with keep_receipt.collecting():
        return keep_stated_facts(text, session_id=chat, access_policy=_policy(chat),
                                 source_context=dict(OWNER if context is None else context))


@pytest.fixture
def judge(monkeypatch):
    """The judgement, answering yes; the sentences it was asked about are returned."""
    asked: list[str] = []
    verdict = {"value": "yes"}

    def lasting(sentence, **_):
        asked.append(sentence)
        return verdict["value"]

    monkeypatch.setattr(intent_arbiter, "arbitrate_lasting_fact", lasting)
    monkeypatch.setattr(intent_arbiter, "arbitrate_standing_state", lambda sentence, **_: verdict.get("standing", "yes"))
    monkeypatch.setattr(intent_arbiter, "arbitrate_told_as_fact", lambda sentence, **_: verdict.get("told", "yes"))
    asked_and_verdict = (asked, verdict)
    return asked_and_verdict


@pytest.mark.parametrize("text", [RYE, CASE, "my cousin Odile breeds border terriers."])
def test_a_sentence_no_shape_lists_is_one_the_judge_may_be_asked_about(text):
    assert everyday.stated_facts(text) == []
    assert everyday.unlisted_statements(text) == [text]


@pytest.mark.parametrize(
    "text",
    [
        "which loaf should I buy?",  # a question
        "write the birthday speech again, it needs to be funnier.",  # a request
        "I'm shattered today, the buses were a mess.",  # about this moment
        "the repair quote is in the downloads folder.",  # about files
        "never mind, scratch that about the suitcase.",  # a take-back
        "ok thanks",  # too short to state anything
        "the suitcase moved to the hall cupboard.",  # a change: the update lane's
        "I lost one stirrup leather last week.",  # one past occasion
        "the vet rang about the dog an hour ago.",  # one past occasion
        "if we won the lottery we'd move to the coast.",  # a supposition
        "Scribble down that the knife grinder comes on the first Monday of the month.",  # opens with a lead
        "Stick it in your notes that the gutters get cleared every October.",  # opens with a lead
        "For the record: the loft pole is behind the wardrobe.",  # a colon lead: the lead lanes' business
    ],
)
def test_what_states_no_lasting_fact_by_its_form_is_not_put_to_the_judge(text):
    assert everyday.unlisted_statements(text) == []


def test_a_fact_a_shape_already_lists_is_not_judged_twice():
    text = "my allotment shed is green. the spare key lives with Mrs Okoro next door."
    assert everyday.stated_facts(text) == ["my allotment shed is green."]
    assert everyday.unlisted_statements(text) == ["the spare key lives with Mrs Okoro next door."]


def test_a_lasting_fact_said_in_passing_is_kept_on_the_judges_yes(isolated_home, judge):
    asked, _ = judge
    assert _keep(RYE) == 1
    assert asked == [RYE]
    assert [row.rstrip(".") for row in _rows()] == [RYE.rstrip(".")], _rows()


def test_the_judges_no_keeps_nothing(isolated_home, judge):
    asked, verdict = judge
    verdict["value"] = "no"
    assert _keep(CASE) == 0 and asked == [CASE] and _rows() == []


def test_with_no_judge_nothing_more_is_kept_than_before(isolated_home):
    assert intent_arbiter.arbitrate_lasting_fact(RYE) is None  # the tests reach no local model
    assert _keep(RYE) == 0 and _rows() == []
    assert _keep("my allotment shed is green.") == 1  # the shapes keep what they kept


def test_a_private_chat_and_an_agents_turn_are_never_put_to_the_judge(isolated_home, judge, monkeypatch):
    asked, _ = judge
    assert _keep(RYE, context={**OWNER, "turn_author": "agent"}) == 0
    monkeypatch.setattr("core.private_chat.turn_is_private", lambda *a, **k: True)
    assert _keep(RYE) == 0
    assert asked == [] and _rows() == []


@pytest.mark.parametrize(
    "said",
    [
        "the film club login wants the password otter-Fennel-29 these days.",
        "the shed alarm takes the passcode four four seven one.",
        "the bank sent a new PIN in the post for the joint account.",
    ],
)
def test_a_sentence_that_names_a_kind_of_secret_is_not_sent_to_the_judge_and_not_kept(isolated_home, judge, said):
    asked, _ = judge
    assert _keep(said) == 0
    assert asked == [] and _rows() == []


def test_the_lasting_fact_question_is_a_judgement_on_the_chat_models_runner(monkeypatch):
    calls: list[dict] = []

    def followup(*args, **kwargs):
        calls.append(kwargs)
        return "yes"

    monkeypatch.setattr(intent_arbiter, "_keep_judge_model", lambda: "qwen3:8b")
    monkeypatch.setattr(intent_arbiter, "arbitrate_followup", followup)
    assert intent_arbiter.arbitrate_lasting_fact(CASE) == "yes"
    assert calls[0]["operation"] == "lasting_fact_arbitration" and calls[0]["model"] == "qwen3:8b"
    assert calls[0]["runner_role"] == "general" and calls[0]["call_role"] == intent_arbiter._judgement_role()
    assert CASE in calls[0]["prompt"]
    monkeypatch.setattr(intent_arbiter, "_keep_judge_model", lambda: "")
    assert intent_arbiter.arbitrate_lasting_fact(CASE) is None


def test_a_reply_that_says_it_noted_a_fact_the_keep_step_wrote_is_not_replaced_by_a_denial(isolated_home, judge):
    from core.agent_runtime import action_honesty_validator as validator

    context = dict(OWNER)
    with keep_receipt.collecting() as receipt:
        receipt.in_passing = keep_stated_facts(RYE, session_id="c1", access_policy=_policy("c1"), source_context=context) > 0
    keep_receipt.stamp(context, receipt)
    assert validator._keep_step_wrote(context) is True
    assert validator._keep_step_wrote(dict(OWNER)) is False  # a turn whose keep step wrote nothing
    said = "Good to know. I have saved it to memory."
    assert validator.completion_claim_kind(said) == "persistence"
    kept = validator.enforce_final_action_honesty({"response": said}, user_input=RYE, session_id="c1", source_context=context)
    assert kept["response"] == said
    denied = validator.enforce_final_action_honesty({"response": said}, user_input=RYE, session_id="c1", source_context=dict(OWNER))
    assert denied["response"] == validator._HONEST_NO_PERSISTENCE_RESPONSE


@pytest.mark.parametrize(
    "text",
    [
        "I think that the cedar chest is the best place for the blankets.",
        "the trouble is that the back door needs a shoulder to close.",
        "it's the wool that brings my neck out in a rash.",
        RYE,  # "... is the only one that agrees with my stomach": "that" far from the start, a subject in front
    ],
)
def test_a_sentence_with_a_subject_in_front_of_that_does_not_open_with_a_lead(text):
    assert everyday.unlisted_statements(text) == [text]


def test_a_sentence_is_kept_only_on_a_yes_to_both_questions(isolated_home, judge):
    asked, verdict = judge
    event = "I chipped a tooth on an olive stone at my cousin's christening."
    verdict["standing"] = "no"  # a lasting private fact by the first question, something that happened by the second
    assert _keep(event) == 0 and asked == [event] and _rows() == []
    verdict["standing"] = None  # nobody could answer the second question
    assert _keep(event) == 0 and _rows() == []
    verdict["standing"] = "yes"
    assert _keep(CASE) == 1


def test_the_second_question_is_asked_only_after_a_yes_to_the_first(isolated_home, monkeypatch):
    second: list[str] = []
    monkeypatch.setattr(intent_arbiter, "arbitrate_lasting_fact", lambda sentence, **_: "no")
    monkeypatch.setattr(intent_arbiter, "arbitrate_standing_state", lambda sentence, **_: second.append(sentence) or "yes")
    assert _keep(CASE) == 0 and second == []


def test_the_standing_state_question_is_a_judgement_on_the_chat_models_runner(monkeypatch):
    calls: list[dict] = []
    monkeypatch.setattr(intent_arbiter, "_keep_judge_model", lambda: "qwen3:8b")
    monkeypatch.setattr(intent_arbiter, "arbitrate_followup", lambda *a, **k: calls.append(k) or "no")
    assert intent_arbiter.arbitrate_standing_state(CASE) == "no"
    assert calls[0]["operation"] == "standing_state_arbitration" and calls[0]["call_role"] == intent_arbiter._judgement_role()
    assert calls[0]["prompt"] != intent_arbiter._lasting_fact_prompt(CASE) and CASE in calls[0]["prompt"]
    monkeypatch.setattr(intent_arbiter, "_keep_judge_model", lambda: "")
    assert intent_arbiter.arbitrate_standing_state(CASE) is None



@pytest.mark.parametrize(
    "said",
    ["retitle the sailing club film so it carries Marguerite's name", "I'm mulling over a lean-to for the firewood."],
)
def test_a_request_or_a_passing_thought_is_not_kept_on_two_yeses(isolated_home, judge, said):
    # Measured on the served cb32d1b6: "rename the soccer video so we know its nadia's" and "I'm thinking of painting
    # the box room sage green." read yes to both questions and each wrote a row. The third question is what kind of
    # sentence it is; its no, or nobody answering it, keeps nothing.
    asked, verdict = judge
    verdict["told"] = "no"
    assert _keep(said) == 0 and asked == [said] and _rows() == []
    verdict["told"] = None
    assert _keep(said) == 0 and _rows() == []
    verdict["told"] = "yes"
    assert _keep(CASE) == 1


def test_the_third_question_is_asked_only_after_a_yes_to_the_first_two(isolated_home, monkeypatch):
    third: list[str] = []
    monkeypatch.setattr(intent_arbiter, "arbitrate_lasting_fact", lambda sentence, **_: "yes")
    monkeypatch.setattr(intent_arbiter, "arbitrate_standing_state", lambda sentence, **_: "no")
    monkeypatch.setattr(intent_arbiter, "arbitrate_told_as_fact", lambda sentence, **_: third.append(sentence) or "yes")
    assert _keep(CASE) == 0 and third == []
    monkeypatch.setattr(intent_arbiter, "arbitrate_standing_state", lambda sentence, **_: "yes")
    assert _keep(CASE) == 1 and third == [CASE]


def test_the_told_as_fact_question_is_a_judgement_on_the_chat_models_runner(monkeypatch):
    calls: list[dict] = []
    monkeypatch.setattr(intent_arbiter, "_keep_judge_model", lambda: "qwen3:8b")
    monkeypatch.setattr(intent_arbiter, "arbitrate_followup", lambda *a, **k: calls.append(k) or "no")
    assert intent_arbiter.arbitrate_told_as_fact(CASE) == "no"
    assert calls[0]["operation"] == "told_as_fact_arbitration" and calls[0]["call_role"] == intent_arbiter._judgement_role()
    assert calls[0]["prompt"] not in {intent_arbiter._lasting_fact_prompt(CASE), intent_arbiter._standing_state_prompt(CASE)}
    assert CASE in calls[0]["prompt"]
    monkeypatch.setattr(intent_arbiter, "_keep_judge_model", lambda: "")
    assert intent_arbiter.arbitrate_told_as_fact(CASE) is None
