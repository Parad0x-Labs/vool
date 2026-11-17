"""A saved fact is found by a question in other words than its own, when an embed model is there to say so.

Measured on the real app (the v0.8 line, local model, an embed model installed): with "kiwi fruit makes my throat
swell up" saved, "is there any food I can't eat?" was answered with general advice; with "Mrs Thackeray looks after
our septic tank" saved, "who do I ring when the drains back up?" was answered "I don't have that saved". The
saved-row search dropped every row that shares no word with the question before anything could read it.

The embed model is not reachable from the tests, so the call that asks it is stood in for by a small table of
directions; with it left alone (no embed model), the search must be the word search it was. All names and facts
here are invented for this contract.
"""
from __future__ import annotations

import pytest

from core import embedding_service, intent_arbiter, tiered_context_loader
from core.agent_runtime import cross_chat_recall as recall
from core.memory import entries
from core.memory.entries import search_relevant_memory
from core.persistent_memory import maybe_handle_memory_command
from tests.test_everyday_remember_v08 import _say
from tests.test_memory_forget_resurrection_p0 import _policy, isolated_home  # noqa: F401  (isolated_home is a fixture)

OWNER = {"_owner_local": True}
#: The stand-in embed model: a text points along the subjects it is about.
SUBJECTS = {
    "food": ("food", "eat", "kiwi", "porridge", "breakfast"),
    "plumbing": ("drains", "septic", "plumber"),
    "music": ("cello", "instrument", "violin"),
    "pitch": ("pitch",),
}


def _direction(text: str) -> list[float]:
    low = text.lower()
    vector = [1.0 if any(word in low for word in words) else 0.0 for words in SUBJECTS.values()]
    return [*vector, 0.05]


@pytest.fixture
def embed_model(monkeypatch):
    """An embed model that answers; the texts it was shown are returned for the tests that check what it saw."""
    seen: list[str] = []

    def embed(texts, *, query=False):
        seen.extend(texts)
        return [_direction(text) for text in texts], "test-space"

    monkeypatch.setattr(embedding_service, "embed_texts_neural", embed)
    entries._meaning_vectors.clear()
    yield seen
    entries._meaning_vectors.clear()


def _texts(rows) -> list[str]:
    return [str(row.get("text") or "") for row in rows]


def test_a_question_that_shares_no_word_with_the_fact_finds_it_by_meaning(isolated_home, embed_model):
    assert _say("Remember that kiwi fruit makes my throat swell up", "seed")[1].startswith("Locked in")
    assert _say("Remember that Mrs Thackeray looks after our septic tank", "seed")[1].startswith("Locked in")
    policy = _policy("ask")
    assert search_relevant_memory("is there any food I can't eat?", access_policy=policy, limit=6) == []
    rows = search_relevant_memory("is there any food I can't eat?", access_policy=policy, limit=6, by_meaning=True)
    assert _texts(rows) == ["kiwi fruit makes my throat swell up"], rows
    assert rows[0]["found_by"] == "meaning" and rows[0]["meaning"] >= entries._MEANING_FLOOR


def test_with_no_embed_model_the_search_is_the_word_search(isolated_home):
    assert _say("Remember that kiwi fruit makes my throat swell up", "seed")[1].startswith("Locked in")
    assert _say("Remember that my throat lozenges are in the hall drawer", "seed")[1].startswith("Locked in")
    policy = _policy("ask")
    assert embedding_service.embed_texts_neural(["anything"]) is None  # the tests reach no local model
    for question in ("is there any food I can't eat?", "what makes my throat swell up?"):
        by_words = search_relevant_memory(question, access_policy=policy, limit=6)
        assert search_relevant_memory(question, access_policy=policy, limit=6, by_meaning=True) == by_words
    assert all("found_by" not in row and "meaning" not in row for row in by_words) and by_words


def test_rows_found_by_words_keep_their_score_and_come_first(isolated_home, embed_model):
    assert _say("Remember that kiwi fruit makes my throat swell up", "seed")[1].startswith("Locked in")
    assert _say("Remember that porridge is my usual breakfast", "seed")[1].startswith("Locked in")
    policy = _policy("ask")
    question = "what do I eat for breakfast?"
    by_words = search_relevant_memory(question, access_policy=policy, limit=6)
    both = search_relevant_memory(question, access_policy=policy, limit=6, by_meaning=True)
    assert _texts(by_words) == ["porridge is my usual breakfast"]
    assert _texts(both) == ["porridge is my usual breakfast", "kiwi fruit makes my throat swell up"]
    assert both[0]["score"] == by_words[0]["score"] and "found_by" not in both[0]


def test_a_row_far_from_the_question_is_not_a_candidate(isolated_home, embed_model):
    assert _say("Remember that Mrs Thackeray looks after our septic tank", "seed")[1].startswith("Locked in")
    rows = search_relevant_memory("is there any food I can't eat?", access_policy=_policy("ask"), limit=6, by_meaning=True)
    assert rows == []


def test_a_forgotten_fact_is_never_scored_and_never_found(isolated_home, embed_model):
    assert _say("Remember that kiwi fruit makes my throat swell up", "seed")[1].startswith("Locked in")
    handled, reply = maybe_handle_memory_command(
        "forget that kiwi fruit makes my throat swell up", session_id="seed", access_policy=_policy("seed")
    )
    assert handled and "Removed 1" in reply, reply
    embed_model.clear()
    rows = search_relevant_memory("is there any food I can't eat?", access_policy=_policy("ask"), limit=6, by_meaning=True)
    assert rows == []
    assert not any("kiwi" in text for text in embed_model), embed_model


def test_a_replaced_value_is_not_found_by_meaning(isolated_home, embed_model):
    assert _say("Remember that my cello is kept at my sister's flat", "seed")[1].startswith("Locked in")
    assert _say("Update: my cello is kept at the music school now", "seed")[1].startswith("Updated")
    rows = search_relevant_memory("where's my instrument stored?", access_policy=_policy("ask"), limit=6, by_meaning=True)
    assert len(rows) == 1 and "music school" in rows[0]["text"] and "sister" not in rows[0]["text"], rows


def test_only_the_owners_own_turn_is_shown_rows_found_by_meaning(isolated_home, embed_model):
    assert _say("Remember that kiwi fruit makes my throat swell up", "seed")[1].startswith("Locked in")
    policy = _policy("ask")
    question = "is there any food the owner can't eat?"

    def shown(context: dict) -> list[str]:
        items = tiered_context_loader._persistent_memory_items(
            question, [], session_id="ask", policy=policy,
            by_meaning=tiered_context_loader._may_read_by_meaning(context, "ask"),
        )
        return [item.content for item in items]

    assert any("kiwi" in content for content in shown(dict(OWNER)))
    assert shown({**OWNER, "turn_author": "agent"}) == []
    assert tiered_context_loader._may_read_by_meaning({**OWNER, "turn_author": "agent"}, "ask") is False
    assert tiered_context_loader._may_read_by_meaning(dict(OWNER), "ask") is True


def test_the_recall_lane_takes_a_near_row_as_the_saved_fact_only_on_a_yes(isolated_home, embed_model, monkeypatch):
    assert _say("Remember that Mrs Thackeray looks after our septic tank", "seed")[1].startswith("Locked in")
    _policy("ask")
    asked: list[tuple[str, str]] = []
    verdict = {"value": "yes"}

    def judge(question, fact, **_):
        asked.append((question, fact))
        return verdict["value"]

    monkeypatch.setattr(intent_arbiter, "arbitrate_meaning", judge)
    question = "who do I ring when the drains back up?"
    assert recall._rows_that_mean_it(question, "ask", dict(OWNER)) == (["Mrs Thackeray looks after our septic tank"], True)
    assert asked == [(question, "Mrs Thackeray looks after our septic tank")]
    # a turn that is not the owner's is never read to by meaning, and nothing is judged for it
    assert recall._rows_that_mean_it(question, "ask", {**OWNER, "turn_author": "agent"}) == ([], True)
    assert len(asked) == 1
    # judged the saved fact: the lane stands down and the model reads the row
    assert recall.cross_chat_recall(question, session_id="ask", source_context=dict(OWNER)) is None
    # judged not to be it: the fact asked for is not saved, and the lane says so as before
    verdict["value"] = "no"
    assert recall._rows_that_mean_it(question, "ask", dict(OWNER)) == ([], True)
    found = recall.cross_chat_recall(question, session_id="ask", source_context=dict(OWNER))
    assert found is not None and not found.found and found.response.startswith("I don't have that saved"), found
    # nobody could judge the near row: the lane does not know that nothing is saved, so it does not say so
    verdict["value"] = None
    assert recall._rows_that_mean_it(question, "ask", dict(OWNER)) == ([], False)
    assert recall.cross_chat_recall(question, session_id="ask", source_context=dict(OWNER)) is None


def test_a_row_that_shares_a_word_and_does_not_cover_is_not_put_to_the_judgement(isolated_home, embed_model, monkeypatch):
    assert _say("Remember that my camping pitch is number 83", "seed")[1].startswith("Locked in")
    _policy("ask")
    monkeypatch.setattr(intent_arbiter, "arbitrate_meaning", lambda *a, **k: pytest.fail("judged a row the words rule out"))
    question = "what's the pitch of my singing voice?"
    rows = search_relevant_memory(question, access_policy=_policy("ask"), limit=6, by_meaning=True)
    assert _texts(rows) == ["my camping pitch is number 83"] and "found_by" not in rows[0], rows
    assert recall._rows_that_mean_it(question, "ask", dict(OWNER)) == ([], True)
    found = recall.cross_chat_recall(question, session_id="ask", source_context=dict(OWNER))
    assert found is not None and not found.found and "83" not in found.response, found


def test_with_no_embed_model_the_recall_lane_asks_no_judgement(isolated_home, monkeypatch):
    assert _say("Remember that Mrs Thackeray looks after our septic tank", "seed")[1].startswith("Locked in")
    _policy("ask")
    monkeypatch.setattr(intent_arbiter, "arbitrate_meaning", lambda *a, **k: pytest.fail("judged without an embed model"))
    found = recall.cross_chat_recall("who do I ring when the drains back up?", session_id="ask", source_context=dict(OWNER))
    assert found is not None and found.response.startswith("I don't have that saved"), found


def test_the_meaning_question_is_a_judgement_on_the_chat_models_runner(monkeypatch):
    calls: list[dict] = []

    def followup(*args, **kwargs):
        calls.append(kwargs)
        return "no"

    monkeypatch.setattr(intent_arbiter, "_keep_judge_model", lambda: "qwen3:8b")
    monkeypatch.setattr(intent_arbiter, "arbitrate_followup", followup)
    assert intent_arbiter.arbitrate_meaning("what's my pole vault record?", "my punting pole is 12 feet.") == "no"
    assert calls[0]["operation"] == "meaning_arbitration" and calls[0]["model"] == "qwen3:8b"
    assert calls[0]["runner_role"] == "general" and calls[0]["call_role"] == intent_arbiter._judgement_role()
    assert "punting pole" in calls[0]["prompt"] and "pole vault" in calls[0]["prompt"]
    monkeypatch.setattr(intent_arbiter, "_keep_judge_model", lambda: "")
    assert intent_arbiter.arbitrate_meaning("what's my pole vault record?", "my punting pole is 12 feet.") is None
