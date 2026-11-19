"""On a machine with no embed model, a saved fact asked for in other words is named by one judgement.

Measured on the real app on the cloud route, where the owner's cloud model answers and no local model is installed:
"the stepladder is kept in the boiler cupboard." was saved, "where do we store the small ladder?" shares no word
with it, nothing could say which saved rows are near the question, and the reply was "I don't have that saved, and
I looked through your other chats ..." with no model asked. On a machine with an embed model the same question is
answered.

One judgement over the rows that share no word names a candidate. It decides nothing: the named fact goes to the
same yes/no judgements as a row an embed model finds near, so a similar fact about another thing is still not the
answer. With nobody to judge, the search is the word search it was. All names and facts are invented here.
"""
from __future__ import annotations

import json
import re

import pytest

from core import embedding_service, intent_arbiter, tiered_context_loader
from core.agent_runtime import cross_chat_recall as recall
from core.memory import entries
from core.memory.entries import search_relevant_memory
from tests.test_everyday_remember_v08 import _say
from tests.test_memory_forget_resurrection_p0 import _policy, isolated_home  # isolated_home is a fixture

OWNER = {"_owner_local": True}
AWNING = "the caravan awning pegs are in the green tub in the outhouse"
HARP = "Mrs Fenwick tunes the harp every spring"
ASK = "where did we put the bits that hold the tent porch down?"


class Judge:
    """The judge bound for a turn no local model answers: names the fact whose text holds ``names``, and answers the
    yes/no judgements with ``confirms``."""

    def __init__(self, names: str | None, confirms: str = "yes"):
        self.names, self.confirms, self.prompts = names, confirms, []

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if "which ONE saved fact" in prompt:
            listed = re.findall(r'^(\d+)\. "(.*)"$', prompt.split("Saved facts:")[-1], re.M)
            place = next((int(number) for number, fact in listed if self.names and self.names in fact), 0)
            return json.dumps({"answer": place})
        return json.dumps({"answer": self.confirms})

    @property
    def namings(self) -> list[str]:
        return [prompt for prompt in self.prompts if "which ONE saved fact" in prompt]

    @property
    def yes_or_no(self) -> list[str]:
        return [prompt for prompt in self.prompts if "which ONE saved fact" not in prompt]


@pytest.fixture
def judge(monkeypatch):
    def bind(names: str | None, confirms: str = "yes") -> Judge:
        made = Judge(names, confirms)
        intent_arbiter.bind_turn_judge({}, made)
        return made

    monkeypatch.setattr(intent_arbiter, "_keep_judge_model", lambda: "")
    recall._MEANING_VERDICTS.clear()
    recall._NAMED_FACTS.clear()
    yield bind
    intent_arbiter.bind_turn_judge({}, None)
    recall._MEANING_VERDICTS.clear()
    recall._NAMED_FACTS.clear()


def _saved():
    assert _say("Remember that " + AWNING, "seed")[1].startswith("Locked in")
    assert _say("Remember that " + HARP, "seed")[1].startswith("Locked in")
    return _policy("ask")


def _shown(question: str, **context) -> list[str]:
    items = tiered_context_loader._persistent_memory_items(
        question, [], session_id="ask", policy=_policy("ask"),
        by_meaning=tiered_context_loader._may_read_by_meaning({**OWNER, **context}, "ask"), source_context={**OWNER, **context},
    )
    return [item.content for item in items]


# ------------------------------------------------------------ the search


def test_the_search_asks_no_model_and_takes_the_readers_naming(isolated_home):
    policy = _saved()
    assert search_relevant_memory(ASK, access_policy=policy, limit=6, by_meaning=True) == []
    seen: list[list[str]] = []

    def name(question, facts):
        seen.append(list(facts))
        return [facts.index(AWNING)]

    rows = search_relevant_memory(ASK, access_policy=policy, limit=6, by_meaning=True, name_the_fact=name)
    assert [row["text"] for row in rows] == [AWNING]
    assert rows[0]["found_by"] == "named" and "meaning" not in rows[0]
    # the newest first, facts the owner saved, none of the profile's template lines
    assert seen == [[HARP, AWNING]]


def test_a_row_that_shares_a_word_is_not_put_to_the_naming(isolated_home):
    policy = _saved()
    seen: list[list[str]] = []
    rows = search_relevant_memory("who tunes the piano?", access_policy=policy, limit=6, by_meaning=True,
                                  name_the_fact=lambda question, facts: seen.append(list(facts)) or [])
    assert [row["text"] for row in rows] == [HARP] and "found_by" not in rows[0]
    assert seen == [[AWNING]]


@pytest.mark.parametrize("named", [None, [], [7], ["0"], [-1]])
def test_a_naming_that_names_nothing_it_was_given_leaves_the_word_search(isolated_home, named):
    policy = _saved()
    assert search_relevant_memory(ASK, access_policy=policy, limit=6, by_meaning=True, name_the_fact=lambda q, f: named) == []


def test_a_naming_that_fails_leaves_the_word_search(isolated_home):
    policy = _saved()

    def name(question, facts):
        raise RuntimeError("paid_call_refused:cap")

    assert search_relevant_memory(ASK, access_policy=policy, limit=6, by_meaning=True, name_the_fact=name) == []


def test_with_an_embed_model_the_naming_is_never_asked(isolated_home, monkeypatch):
    policy = _saved()
    monkeypatch.setattr(embedding_service, "embed_texts_neural", lambda texts, *, query=False: ([[1.0, 0.0] for _ in texts], "test-space"))
    entries._meaning_vectors.clear()
    rows = search_relevant_memory(ASK, access_policy=policy, limit=6, by_meaning=True,
                                  name_the_fact=lambda q, f: pytest.fail("named with an embed model answering"))
    assert {row["found_by"] for row in rows} == {"meaning"}
    entries._meaning_vectors.clear()


def test_without_by_meaning_nothing_is_named(isolated_home):
    policy = _saved()
    assert search_relevant_memory(ASK, access_policy=policy, limit=6,
                                  name_the_fact=lambda q, f: pytest.fail("named for a reader that did not ask")) == []


# ------------------------------------------------------------ the recall lane and the context loader


def test_the_named_fact_is_the_saved_fact_on_the_judgements_yes(isolated_home, judge):
    _saved()
    asked = judge("awning pegs", "yes")
    assert recall._rows_that_mean_it(ASK, "ask", dict(OWNER)) == ([AWNING], True)
    # the lane hands the question to the answering model, which is shown the fact
    assert recall.cross_chat_recall(ASK, session_id="ask", source_context=dict(OWNER)) is None
    assert _shown(ASK) == [AWNING]
    # one naming for the turn, whoever asks: the lane, its guard and the loader
    assert len(asked.namings) == 1 and HARP in asked.namings[0] and AWNING in asked.namings[0]


def test_a_named_fact_the_judgement_reads_as_another_thing_is_not_the_answer(isolated_home, judge):
    _saved()
    asked = judge("awning pegs", "no")
    question = "where did we put the bits that hold the gazebo down?"
    assert recall._rows_that_mean_it(question, "ask", dict(OWNER)) == ([], True)
    found = recall.cross_chat_recall(question, session_id="ask", source_context=dict(OWNER))
    assert found is not None and found.response.startswith("I don't have that saved") and "green tub" not in found.response
    assert _shown(question) == []
    assert len(asked.namings) == 1 and asked.yes_or_no


def test_when_the_judgement_names_none_the_lane_says_not_saved_after_one_call(isolated_home, judge):
    _saved()
    asked = judge(None)
    found = recall.cross_chat_recall("what is my bus pass number?", session_id="ask", source_context=dict(OWNER))
    assert found is not None and found.response.startswith("I don't have that saved")
    assert len(asked.namings) == 1 and asked.yes_or_no == []
    assert _shown("what is my bus pass number?") == []
    assert len(asked.namings) == 1


def test_a_fact_the_words_find_that_holds_the_subject_needs_no_naming(isolated_home, judge):
    _saved()
    asked = judge("awning pegs", "yes")
    assert _shown("who tunes the harp?") == [HARP]
    assert recall.cross_chat_recall("who tunes the harp?", session_id="ask", source_context=dict(OWNER)) is None
    assert asked.prompts == []


def test_with_nobody_to_judge_the_lane_answers_as_it_did(isolated_home, monkeypatch):
    _saved()
    monkeypatch.setattr(intent_arbiter, "_keep_judge_model", lambda: "")
    intent_arbiter.bind_turn_judge({}, None)
    recall._NAMED_FACTS.clear()
    assert recall._rows_that_mean_it(ASK, "ask", dict(OWNER)) == ([], True)
    found = recall.cross_chat_recall(ASK, session_id="ask", source_context=dict(OWNER))
    assert found is not None and found.response.startswith("I don't have that saved")
    assert recall._NAMED_FACTS == {}


def test_an_unreadable_naming_is_nobody_judging_and_is_asked_again(isolated_home, monkeypatch):
    _saved()
    monkeypatch.setattr(intent_arbiter, "_keep_judge_model", lambda: "")
    recall._NAMED_FACTS.clear()
    answers = iter(["the second one, I think", json.dumps({"answer": 9})])
    intent_arbiter.bind_turn_judge({}, lambda prompt: next(answers))
    assert recall.name_the_saved_fact(ASK, [HARP, AWNING], session_id="ask", source_context=dict(OWNER)) is None
    assert recall.name_the_saved_fact(ASK, [HARP, AWNING], session_id="ask", source_context=dict(OWNER)) is None
    assert recall._NAMED_FACTS == {}
    intent_arbiter.bind_turn_judge({}, None)


def test_an_agents_turn_and_a_private_chat_ask_no_naming(isolated_home, judge, monkeypatch):
    _saved()
    asked = judge("awning pegs", "yes")
    assert recall.name_the_saved_fact(ASK, [AWNING], session_id="ask", source_context={**OWNER, "turn_author": "agent"}) is None
    monkeypatch.setattr(recall, "_private", lambda *a, **k: True)
    assert recall.name_the_saved_fact(ASK, [AWNING], session_id="ask", source_context=dict(OWNER)) is None
    assert asked.prompts == []


# ------------------------------------------------------------ the judgement itself


def test_the_naming_reads_a_number_in_range_and_nothing_else(monkeypatch):
    monkeypatch.setattr(intent_arbiter, "_keep_judge_model", lambda: "")
    facts = [HARP, AWNING]
    for answer, expected in [('{"answer": 2}', 2), ('{"answer": "1"}', 1), ('{"answer": 0}', 0), ('Sure: {"answer": 2}', 2),
                             ('{"answer": 3}', None), ('{"answer": -1}', None), ('{"answer": true}', None),
                             ('{"answer": "yes"}', None), ("2", None), ("", None)]:
        intent_arbiter.bind_turn_judge({}, lambda prompt, answer=answer: answer)
        assert intent_arbiter.arbitrate_named_fact(ASK, facts, source_context={}) == expected, answer
    intent_arbiter.bind_turn_judge({}, None)
    assert intent_arbiter.arbitrate_named_fact(ASK, facts, source_context={}) is None
    assert intent_arbiter.arbitrate_named_fact(ASK, [], source_context={}) is None


def test_the_naming_is_a_judgement_on_the_chat_models_runner_when_one_is_loaded(monkeypatch):
    calls: list[dict] = []

    def followup(*args, **kwargs):
        calls.append(kwargs)
        return kwargs["parse"]('{"answer": 2}')

    monkeypatch.setattr(intent_arbiter, "_keep_judge_model", lambda: "qwen3:8b")
    monkeypatch.setattr(intent_arbiter, "arbitrate_followup", followup)
    intent_arbiter.bind_turn_judge({}, lambda prompt: pytest.fail("asked the turn's judge with a local judge loaded"))
    assert intent_arbiter.arbitrate_named_fact(ASK, [HARP, AWNING], source_context={}) == 2
    assert calls[0]["operation"] == "named_fact_arbitration" and calls[0]["model"] == "qwen3:8b"
    assert calls[0]["runner_role"] == "general" and calls[0]["call_role"] == intent_arbiter._judgement_role()
    assert '1. "' + HARP in calls[0]["prompt"] and '2. "' + AWNING in calls[0]["prompt"]
    intent_arbiter.bind_turn_judge({}, None)


def test_only_so_many_facts_are_read_by_one_naming(monkeypatch):
    monkeypatch.setattr(intent_arbiter, "_keep_judge_model", lambda: "")
    prompts: list[str] = []
    intent_arbiter.bind_turn_judge({}, lambda prompt: prompts.append(prompt) or '{"answer": 0}')
    facts = [f"the spare key for locker {n} is with the caretaker" for n in range(intent_arbiter.NAMED_FACTS_ASKED + 5)]
    assert intent_arbiter.arbitrate_named_fact(ASK, facts, source_context={}) == 0
    assert f'{intent_arbiter.NAMED_FACTS_ASKED}. "' in prompts[0] and f'{intent_arbiter.NAMED_FACTS_ASKED + 1}. "' not in prompts[0]
    intent_arbiter.bind_turn_judge({}, None)


# ------------------------------------------------------------ the words of the search


@pytest.mark.parametrize(("one", "other"), [("dogs", "dog"), ("cats", "cat"), ("keys", "key"), ("glasses", "glass"), ("boxes", "box")])
def test_a_plain_plural_meets_its_word_on_three_letters(one, other):
    assert entries._match_stem(one) == entries._match_stem(other)


@pytest.mark.parametrize("word", ["bus", "gas", "axis", "boss", "plus", "glass", "status"])
def test_a_word_that_ends_in_s_and_is_no_plural_is_left_whole(word):
    assert entries._match_stem(word) == word


def test_a_two_letter_word_that_names_a_thing_is_a_word_of_the_search(isolated_home):
    assert _say("Remember that my gp is Dr Lisle", "seed")[1].startswith("Locked in")
    found = search_relevant_memory("and who is my gp?", access_policy=_policy("ask"), limit=6)
    assert [str(row.get("text")) for row in found] == ["my gp is Dr Lisle"]
    assert "gp" in entries._search_tokens("who is my gp?") and "tv" in entries._search_tokens("the tv is in the den")


def test_a_two_letter_word_that_names_nothing_adds_no_word():
    assert not {"go", "pm", "at"} & entries._search_tokens("I go there at 5 pm")
    assert "pm" not in entries._search_tokens("the bus leaves at 5 pm") and "at" not in entries._search_tokens("the at sign")


def test_the_dogs_vet_meets_the_fact_about_the_dog():
    assert entries._search_tokens("who is my dogs vet?") & entries._search_tokens("actually we settled on Dr Croft for the dog.")


# ------------------------------------------------------------ the word for the kind of answer


def test_the_word_for_the_kind_of_answer_is_not_held_against_a_fact_that_gives_the_value(isolated_home):
    assert _say("Remember that my rowing boat is the maroon one by the jetty", "seed")[1].startswith("Locked in")
    assert _say("Remember that the boathouse padlock code is 5512", "seed")[1].startswith("Locked in")
    _policy("ask")
    question = "what colour is my rowing boat?"
    assert recall.saved_memory_rows(question, session_id="ask", source_context=dict(OWNER)) == ["my rowing boat is the maroon one by the jetty"]
    assert recall.cross_chat_recall(question, session_id="ask", source_context=dict(OWNER)) is None
    # another thing: the fact about the rowing boat is not the canoe's
    found = recall.cross_chat_recall("what colour is my canoe?", session_id="ask", source_context=dict(OWNER))
    assert found is not None and found.response.startswith("I don't have that saved") and "maroon" not in found.response


def test_a_fact_that_holds_the_word_for_the_kind_still_counts_it(isolated_home):
    assert _say("Remember that my dogs vet is Dr Rennie", "seed")[1].startswith("Locked in")
    _policy("ask")
    assert recall._asked_kind("which vet is my dog registered with?") == frozenset({"vet"})
    covering, _closest = recall._memory_rows(
        "which vet is my dog registered with?", recall._asked_terms("which vet is my dog registered with?"), "ask", dict(OWNER)
    )
    assert covering == ["my dogs vet is Dr Rennie"]


@pytest.mark.parametrize(("question", "kind"), [
    ("what colour is my shed?", {"colour"}), ("which day is the bin collection?", {"day"}),
    ("what is my gp called?", set()), ("what kind of car is it?", set()), ("who is my dentist?", set()),
])
def test_the_kind_of_answer_is_read_by_its_place(question, kind):
    from core.chat_housekeeping import query_terms

    assert recall._asked_kind(question) == frozenset(term for word in kind for term in query_terms(word))
