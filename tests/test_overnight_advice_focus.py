"""Advice query framing development reproductions, separate from ownership."""
import pytest
from core.context_retrieval import _advice_topic_clause, _advice_ask_frame_terms
from tests.test_p300_pref_retrieval_repair import pref_env, _ingest, _capsule


@pytest.mark.parametrize("question", [
    "I've been thinking about arranging a terrarium for my office, but I'm not sure which one to choose. Any suggestions?",
    "I've been thinking about making a sealed desktop garden, but I'm not sure which one to choose. Any suggestions?",
])
def test_compact_advice_query_recovers_owned_context_amid_register_distractors(pref_env, monkeypatch, question):
    import tests.test_p300_pref_retrieval_repair as geometry
    monkeypatch.setattr(geometry, "STUB_REGISTER_WORDS", geometry.STUB_REGISTER_WORDS | {
        "thinking", "been", "about", "not", "sure", "which", "one", "choose",
    })
    home, install = pref_env
    install({"terrarium", "moss", "glass", "closed", "ferns", "sealed", "desktop", "garden"})
    sessions = [("2024/09/01 (Sun) 12:00",
                 "I keep a closed glass terrarium with moss and small ferns on my desk.",
                 "The closed glass setup retains moisture.")]
    for i in range(10):
        sessions.append((f"2024/09/{i + 2:02d} (Mon) 12:00",
                         f"I've been thinking about my weekly activity {i}, but I'm not sure which one to choose. "
                         "I have been wondering whether to join the local chess group or the choir this week.",
                         "Thinking about a weekly activity before choosing one helps."))
    _ingest(home, "terrarium-advice", sessions)
    context, telemetry = _capsule(home, "terrarium-advice", question)
    assert "closed glass terrarium with moss and small ferns" in context, (context, telemetry)


@pytest.mark.parametrize("question,topic", [
    ("I've been thinking about arranging a terrarium for my office, but I'm not sure which one to choose. Any suggestions?",
     "arranging a terrarium for my office"),
    ("I'm considering building a bird feeder, but I am unsure what to pick. Any advice?",
     "building a bird feeder"),
    ("I have been wondering about choosing a canoe for calm rivers. Any tips?",
     "choosing a canoe for calm rivers"),
])
def test_advice_probe_names_topic_instead_of_metacognition(question, topic):
    assert _advice_topic_clause(question) == topic


def test_frame_extraction_preserves_negative_requirement():
    question = "I've been thinking about bread without sesame, but my child must avoid nuts. Any suggestions?"
    topic = _advice_topic_clause(question)
    assert "without sesame" in topic and "must avoid nuts" in topic
    assert "sesame" not in _advice_ask_frame_terms(question)
    assert "nuts" not in _advice_ask_frame_terms(question)


def test_substantive_first_person_state_is_not_request_framing():
    question = "I've been having trouble with a cracked violin bridge. Any advice?"
    assert _advice_topic_clause(question) == "I've been having trouble with a cracked violin bridge"


@pytest.mark.parametrize("question,required,removed", [
    ("I have been thinking about arranging a terrarium for an upcoming plant corner, but I am not sure which setup to choose. Any ideas?", "terrarium", {"which", "choose", "sure"}),
    ("I am considering my camp meal setup and keep wondering which cooking option to pick. What would you suggest?", "cooking", {"wondering", "which", "pick"}),
    ("I am weighing which pottery project to attempt next, but I am not sure what to choose. Do you have any suggestions?", "pottery", {"weighing", "which", "sure", "choose"}),
    ("I am planning a printmaking project and keep wondering which materials to choose. Any guidance?", "printmaking", {"wondering", "which", "choose"}),
])
def test_deliberation_is_removed_but_choice_object_is_kept(question,required,removed):
    topic=_advice_topic_clause(question)
    assert topic is not None and required in topic
    assert not any(word in topic.lower().split() for word in removed), topic


def test_choice_object_constraints_are_preserved():
    question="I am considering bread, but I am unsure which gluten-free rolls without sesame to choose. Any advice?"
    topic=_advice_topic_clause(question)
    assert "gluten-free" in topic and "without sesame" in topic, topic
    assert "gluten-free" not in _advice_ask_frame_terms(question)
    assert "sesame" not in _advice_ask_frame_terms(question)


@pytest.mark.parametrize("question,owned,words", [
    ("I have been thinking about arranging a terrarium for an upcoming plant corner, but I am not sure which setup to choose. Any ideas?", "For my terrarium plants I prefer closed glass jars with an unheated LED grow strip.", {"terrarium", "plants", "plant", "glass", "jars", "grow", "strip", "closed", "led"}),
    ("I am considering my camp meal setup and keep wondering which cooking option to pick. What would you suggest?", "For my camp meals I do not use alcohol stoves; I prefer an electric hotplate at powered sites.", {"camp", "meal", "meals", "cooking", "stoves", "hotplate", "electric", "powered"}),
    ("I am weighing which pottery project to attempt next, but I am not sure what to choose. Do you have any suggestions?", "I have already completed a pottery wheel course and thrown several porcelain bowls; I prefer advanced ceramic projects.", {"pottery", "project", "projects", "wheel", "porcelain", "bowls", "ceramic", "course"}),
    ("I am planning a printmaking project and keep wondering which materials to choose. Any guidance?", "For my printmaking projects I avoid solvent-based inks because of the fumes; I only use water-based relief inks.", {"printmaking", "project", "projects", "materials", "inks", "water", "relief", "solvent"}),
])
def test_owned_choice_context_survives_repeated_deliberation_distractors(pref_env,monkeypatch,question,owned,words):
    import tests.test_p300_pref_retrieval_repair as geometry
    monkeypatch.setattr(geometry,"STUB_REGISTER_WORDS", geometry.STUB_REGISTER_WORDS | {"thinking", "upcoming", "sure", "which", "choose", "choice", "option", "pick", "unsure", "weighing", "planning"})
    home,install=pref_env
    install(words)
    sessions=[("2025/03/10 (Mon) 10:00",owned,"")]
    distractor="I'm thinking about which option to choose for an upcoming decision. I'm not sure which one to pick, and I have been looking for suggestions. The undecided choice concerns parcel ribbons, not hobby equipment."
    sessions.extend((f"2026/04/{i:02d} (Wed) 10:00",distractor,"") for i in range(1,10))
    _ingest(home,"choice-advice",sessions)
    capsule,telemetry=_capsule(home,"choice-advice",question)
    assert owned in capsule, (capsule,telemetry)
