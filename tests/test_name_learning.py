"""VOOL learns the operator's name the way a person does, and never asks.

The owner's report this covers: the name lived in a Settings field. Saying "my name is Alex" in
chat produced a suggestion with Save / Edit / Only this chat buttons, "I'm Alex" was kept for one
chat, a second name raised a "Replace it?" question, and nothing was ever read off the operator's
own mail or a pasted letter.

Each test drives the production chat door (no model: every path here is deterministic) and names
the clause it pins. The phrasing families are deliberately wide and include the look-alikes that
must teach nothing -- a fix that satisfies one sentence is the defect, not the cure.
"""
from __future__ import annotations

import pytest

from core import name_learning
from core.operator_profile_interpretation import interpret_profile_turn
from tests.operator_profile_rig import (
    OWNER,
    body_json,
    chat,
    make_agent,
    profile_env_generator,
    run_once_in_thread,
    served_get,
    served_post,
    store_email_credential,
)


@pytest.fixture()
def profile_env(tmp_path, monkeypatch):
    # The computer account holds no person's name unless a test says so.
    monkeypatch.setattr(name_learning, "account_given_name", lambda: "")
    yield from profile_env_generator(tmp_path, monkeypatch)


def _name(agent, session: str) -> str:
    return chat(agent, "what's my name?", session)["text"]


def _items(agent) -> list[dict]:
    return [i for i in body_json(served_get(agent, "/api/profile", {}))["items"] if i["category"] == "preferred_name"]


def _frame(turn: dict) -> dict:
    return turn["payload"].get("vool_profile") or {}


def _proposal(text: str):
    names = [p for p in interpret_profile_turn(text) if p.category == "preferred_name"]
    return names[0] if names else None


# --- reading: what is a name statement, an introduction, and what is neither -----------------

STATED = (
    ("my name is Marta", "Marta"),
    ("my name is marta", "Marta"),
    ("My name's Jean-Luc", "Jean-Luc"),
    ("call me Bex", "Bex"),
    ("you can call me Bex please", "Bex"),
    ("pls call me bex", "Bex"),
    ("I go by Teo", "Teo"),
    ("i prefer to be called Teo", "Teo"),
    ("actually call me Teo", "Teo"),
    ("Sorry, my name is Ines", "Ines"),
    ("call me Ines, not Agnes", "Ines"),
    ("my name isn't Agnes, it's Ines", "Ines"),
    ("don't call me Agnes, call me Ines", "Ines"),
    ("I'm not Agnes, I'm Ines", "Ines"),
    ("set my name to Oskar", "Oskar"),
    ("remember my name is Oskar", "Oskar"),
)
INTRODUCTIONS = (
    ("I'm Noor", "Noor"),
    ("Hi, I'm Noor, can you help me plan a trip to Porto?", "Noor"),
    ("I'm Noor, what's the weather like?", "Noor"),
    ("hey it's Kofi", "Kofi"),
    ("Kofi here, quick question", "Kofi"),
    ("this is Linh from the bakery", "Linh"),
    ("hello this is Mary-Ann speaking", "Mary-Ann"),
    ("I am Jean-Luc and I need a hand", "Jean-Luc"),
    ("name's Lee", "Lee"),
    ("I'm Dana btw", "Dana"),
    ("no, it's Kofi", "Kofi"),
)
NOT_A_NAME = (
    "I'm tired",
    "im back",
    "I'm Lithuanian",
    "I'm Vegan and I need a meal plan",
    "It's Friday",
    "It's Christmas",
    "I'm Working on a report",
    "I'm Sam's brother",
    "I'm not sure about this",
    "call me at 5",
    "call me when you're done",
    "call me crazy but I think it works",
    "call me a taxi",
    "call me back later",
    "This is Great, thanks",
    "he said 'my name is Bob'",
    "what is my name?",
    "what should I call you?",
)


@pytest.mark.parametrize(("text", "name"), STATED)
def test_a_stated_name_is_read_as_a_statement(text, name):
    proposal = _proposal(text)
    assert proposal is not None and proposal.action == "remember"
    assert proposal.value == name and proposal.strength in {"strong", "explicit"}, proposal


@pytest.mark.parametrize(("text", "name"), INTRODUCTIONS)
def test_an_introduction_is_read_as_learnable(text, name):
    proposal = _proposal(text)
    assert proposal is not None and proposal.value == name and proposal.learnable, proposal


@pytest.mark.parametrize("text", NOT_A_NAME)
def test_look_alikes_teach_no_name(text):
    proposal = _proposal(text)
    assert proposal is None or (proposal.action == "forget" and proposal.rejected), proposal


def test_an_introduction_inside_a_question_leaves_the_question_in_the_turn():
    assert _proposal("Hi, I'm Noor, can you help me plan a trip to Porto?").whole is False
    assert _proposal("my name is Noor, what is the weather").whole is False
    assert _proposal("I'm Noor").whole is True
    assert _proposal("my name is Noor").whole is True


# --- chat: stated, introduced, corrected, dropped ----------------------------------------------


def test_an_introduction_teaches_the_name_for_every_chat(profile_env):
    agent = make_agent()
    turn = chat(agent, "hey I'm Noor", "n-a")
    assert turn["text"] == "Nice to meet you, Noor."
    assert not _frame(turn).get("candidates")
    assert "Noor" in _name(agent, "n-b")
    chat(agent, "my name is Noor", "n-b")
    assert _name(agent, "n-c") == "Your name is Noor — the name in your profile."
    assert [(i["value"], i["scope"], i["origin"]) for i in _items(agent)] == [("Noor", "global", "explicit")]


def test_an_introduction_in_front_of_a_question_does_not_swallow_the_question(profile_env):
    agent = make_agent()
    turn = chat(agent, "Hi, I'm Noor, what is the capital of Portugal?", "n-a")
    # The profile lane did not answer for the model: the question went on to the chat lane.
    assert turn["text"] != "Nice to meet you, Noor." and "I'll call you" not in turn["text"]
    saved = _frame(turn).get("saved") or []
    assert [s["report"] for s in saved] == ["I'll call you Noor."] and saved[0]["item_id"].startswith("opi-")
    assert not _frame(turn).get("notices")
    assert "Noor" in _name(agent, "n-b")


def test_an_introduction_never_replaces_a_name_the_operator_stated(profile_env):
    agent = make_agent()
    chat(agent, "my name is Marta", "n-a")
    chat(agent, "hi, I'm Kofi", "n-b")  # somebody else at the keyboard, or a nickname for one chat
    assert "Kofi" in _name(agent, "n-b")
    assert "Marta" in _name(agent, "n-c")
    assert [i["value"] for i in _items(agent)] == ["Marta"]


def test_a_this_chat_nickname_is_dropped_alone_and_a_stated_name_outranks_it(profile_env):
    agent = make_agent()
    chat(agent, "my name is Marta", "n-a")
    chat(agent, "hi, I'm Kofi", "n-b")
    turn = chat(agent, "I'm not Kofi", "n-b")
    assert turn["text"] == "Sorry about that. I won't call you Kofi any more."
    assert "Marta" in _name(agent, "n-b") and [i["value"] for i in _items(agent)] == ["Marta"]
    chat(agent, "hi, I'm Kofi", "n-c")
    chat(agent, "call me Ines", "n-c")
    assert "Ines" in _name(agent, "n-c") and "Ines" in _name(agent, "n-d")


def test_a_private_chat_teaches_no_name(profile_env, monkeypatch):
    from core.private_chat import PRIVATE_TURN_KEY
    from core.request_trust import OWNER_LOCAL_KEY

    monkeypatch.setattr(name_learning, "account_given_name", lambda: "Dana")
    agent = make_agent()
    ctx = {OWNER_LOCAL_KEY: True, PRIVATE_TURN_KEY: True, "runtime_session_id": "n-private"}
    for text in ("hey I'm Noor", "my name is Noor", _LETTER.format(to="Dana", sender="Piotr")):
        run_once_in_thread(agent, text, session_id="n-private", source_context=dict(ctx))
    assert _items(agent) == [] and "Noor" not in _name(agent, "n-b") and "Dana" not in _name(agent, "n-b")
    from core.operator_profile_turn import learn_name_from_mail

    store_email_credential("work")
    assert learn_name_from_mail([_mail(to="Dana Whitfield <work@example.invalid>")], source_context=dict(ctx)) is None


def test_saying_your_name_again_after_deleting_the_chat_it_was_said_in_keeps_it(profile_env):
    """Measured on the chat page 2026-11-11: say your name, delete that chat, say it again. The
    acknowledgement repeated the erased reply byte for byte, so the new turn was born under the
    old reply's erasure and the name VOOL had just acknowledged was gone at the first read."""
    from tests.operator_profile_rig import canonical_session

    agent = make_agent()
    for said_as in ("hey I'm Noor", "my name is Bex"):
        name = said_as.split()[-1]
        h = {k: f"d-{name.lower()}-{k}" for k in ("a", "probe", "b", "c", "e")}

        def delete(key, h=h):
            return int(served_post(agent, "/api/chat/session", {"session_id": canonical_session(h[key]), "delete": True}).status)

        chat(agent, said_as, h["a"])
        chat(agent, said_as, h["probe"])
        assert delete("a") == 200 and delete("probe") == 200
        assert _items(agent) == []  # deleting the chat it was learned in still forgets it
        said = chat(agent, said_as, h["b"])["text"]
        assert name in said and name in _name(agent, h["c"]), (said, _name(agent, h["c"]))
        # ... and deleting THAT chat forgets it again: the erasure sweep still reaches the item.
        assert delete("b") == 200
        assert _items(agent) == [] and name not in _name(agent, h["e"])
        delete("c")
        delete("e")


def test_a_correction_replaces_the_name_and_nothing_asks(profile_env):
    agent = make_agent()
    chat(agent, "I'm Noor", "n-a")
    for text, new, old in (
        ("no, it's Nora", "Nora", "Noor"),
        ("call me Bex not Nora", "Bex", "Nora"),
        ("my name isn't Bex, it's Ines", "Ines", "Bex"),
        ("actually my name is Teo", "Teo", "Ines"),
    ):
        turn = chat(agent, text, "n-a")
        assert turn["text"] == f"Got it, {new} it is. I won't call you {old} any more.", (text, turn["text"])
        assert not _frame(turn).get("conflicts") and not _frame(turn).get("candidates")
        assert new in _name(agent, "n-b") and old not in _name(agent, "n-b")
    assert [(i["value"], i["origin"]) for i in _items(agent)] == [("Teo", "explicit")]


@pytest.mark.parametrize("text", ("I'm not Noor", "that's not my name", "thats not my name", "wrong name", "my name isn't Noor", "don't call me Noor"))
def test_not_my_name_drops_it(profile_env, text):
    agent = make_agent()
    chat(agent, "hey I'm Noor", "n-a")
    turn = chat(agent, text, "n-a")
    assert turn["text"] == "Sorry about that. I won't call you Noor any more.", turn["text"]
    assert "Noor" not in _name(agent, "n-b") and _items(agent) == []


@pytest.mark.parametrize("text", ("I'm not sure", "I'm not Kofi", "don't call me maybe", "my name isn't Kofi"))
def test_a_denial_that_is_not_about_the_name_in_use_changes_nothing(profile_env, text):
    agent = make_agent()
    chat(agent, "my name is Marta", "n-a")
    turn = chat(agent, text, "n-a")
    assert "Marta" not in turn["text"] or "won't" not in turn["text"]
    assert "Sorry about that" not in turn["text"] and not _frame(turn).get("saved")
    assert [i["value"] for i in _items(agent)] == ["Marta"]


def test_a_dropped_name_is_not_guessed_again_but_the_operators_own_word_still_wins(profile_env, monkeypatch):
    agent = make_agent()
    chat(agent, "hey I'm Noor", "n-a")
    chat(agent, "I'm not Noor", "n-a")
    # the same introduction is not taken as the standing name a second time ...
    chat(agent, "hey I'm Noor", "n-b")
    assert _items(agent) == [] and "Noor" not in _name(agent, "n-c")
    # ... nor is anything read off a pasted letter while the last word on the name was "drop it" ...
    monkeypatch.setattr(name_learning, "account_given_name", lambda: "Noor")
    chat(agent, _LETTER.format(to="Noor", sender="Piotr"), "n-d")
    assert _items(agent) == []
    # ... but a different name they introduce themselves with is learned, and a stated one always is.
    chat(agent, "hi, I'm Nora", "n-e")
    assert [i["value"] for i in _items(agent)] == ["Nora"]
    chat(agent, "call me Noor", "n-e")
    assert [i["value"] for i in _items(agent)] == ["Noor"]


def test_undo_on_a_name_just_learned_forgets_it(profile_env):
    agent = make_agent()
    saved = _frame(chat(agent, "Hi, I'm Noor, what is the capital of Portugal?", "n-a"))["saved"][0]
    response = served_post(agent, "/api/profile/restore", {"item_id": saved["item_id"]})
    assert int(response.status) == 200 and body_json(response)["change"]["kind"] == "forgotten"
    assert _items(agent) == [] and "Noor" not in _name(agent, "n-b")


def test_saying_a_learned_name_yourself_makes_it_yours(profile_env):
    agent = make_agent()
    chat(agent, "hey I'm Noor", "n-a")
    turn = chat(agent, "my name is Noor", "n-b")
    assert not _frame(turn).get("candidates") and not _frame(turn).get("conflicts")
    assert [(i["value"], i["origin"]) for i in _items(agent)] == [("Noor", "explicit")]


def test_the_answer_with_no_name_points_at_the_chat_not_at_settings(profile_env):
    agent = make_agent()
    reply = _name(agent, "n-a")
    assert "Settings" not in reply and "tell me" in reply.lower()


def test_memory_paused_learns_no_name(profile_env):
    agent = make_agent()
    assert int(served_post(agent, "/api/profile/pause", {"paused": True}).status) == 200
    chat(agent, "hey I'm Noor", "n-a")
    chat(agent, "my name is Noor", "n-a")
    assert _items(agent) == []


def test_a_channel_user_teaches_their_own_name_and_only_theirs(profile_env, monkeypatch):
    monkeypatch.setattr(name_learning, "account_given_name", lambda: "Dana")
    agent = make_agent()
    ctx = {"surface": "telegram", "platform": "telegram", "source_user_id": "111", "runtime_session_id": "tg-111"}
    reply = run_once_in_thread(agent, "hey I'm Noor", session_id="tg-111", source_context=dict(ctx))
    assert "Noor" in str(reply.get("response") or "")
    from core import operator_profile

    assert operator_profile.resolve("channel:telegram:111", "preferred_name").value_text == "Noor"
    assert operator_profile.resolve(OWNER, "preferred_name") is None
    # The computer account is the owner's: a letter a phone user pastes is never checked against it.
    ctx2 = {"surface": "telegram", "platform": "telegram", "source_user_id": "222", "runtime_session_id": "tg-222"}
    run_once_in_thread(agent, _LETTER.format(to="Dana", sender="Piotr"), session_id="tg-222", source_context=dict(ctx2))
    assert operator_profile.resolve("channel:telegram:222", "preferred_name") is None


# --- pasted letters: evidence, believed only when it agrees with this computer's owner ---------

_LETTER = (
    "Can you help me answer this?\n\n"
    "Hi {to},\n\n"
    "My name is {sender} and I am writing about the invoice from last week.\n"
    "Could you confirm the amount?\n\n"
    "Best regards,\n"
    "{sender} Nowak"
)


def test_a_pasted_letter_teaches_the_name_it_greets_when_this_computer_agrees(profile_env, monkeypatch):
    monkeypatch.setattr(name_learning, "account_given_name", lambda: "Dana")
    agent = make_agent()
    turn = chat(agent, _LETTER.format(to="Dana", sender="Piotr"), "p-a")
    saved = _frame(turn).get("saved") or []
    assert [s["report"] for s in saved] == ["I'll call you Dana. I noticed the name in what you pasted."]
    assert not _frame(turn).get("candidates")
    assert "Dana" in _name(agent, "p-b")
    assert [(i["value"], i["origin"]) for i in _items(agent)] == [("Dana", "inferred")]


def test_a_letter_the_operator_wrote_teaches_the_name_it_is_signed_with(profile_env, monkeypatch):
    monkeypatch.setattr(name_learning, "account_given_name", lambda: "Piotr")
    agent = make_agent()
    chat(agent, _LETTER.format(to="Dana", sender="Piotr"), "p-a")
    assert [i["value"] for i in _items(agent)] == ["Piotr"]


@pytest.mark.parametrize("account_name", ("", "Marta"))
def test_a_pasted_letter_alone_settles_nothing(profile_env, monkeypatch, account_name):
    """Nothing in a letter says which of its two people is the operator. With no agreeing name on
    this computer -- none at all, or a third person's -- neither is taken, and the "My name is
    Piotr" INSIDE the letter is never read as the operator speaking."""
    monkeypatch.setattr(name_learning, "account_given_name", lambda: account_name)
    agent = make_agent()
    turn = chat(agent, _LETTER.format(to="Dana", sender="Piotr"), "p-a")
    assert not _frame(turn).get("saved") and not _frame(turn).get("candidates")
    assert _items(agent) == []
    reply = _name(agent, "p-a")
    assert "Piotr" not in reply and "Dana" not in reply


def test_a_pasted_letter_never_replaces_a_name_the_operator_gave(profile_env, monkeypatch):
    monkeypatch.setattr(name_learning, "account_given_name", lambda: "Dana")
    agent = make_agent()
    chat(agent, "call me Dee", "p-a")
    turn = chat(agent, _LETTER.format(to="Dana", sender="Piotr"), "p-a")
    assert not _frame(turn).get("saved")
    assert [i["value"] for i in _items(agent)] == ["Dee"]


def test_the_operators_own_line_above_a_paste_still_counts(profile_env):
    agent = make_agent()
    chat(agent, "I'm Noor.\n\nHi Maria,\nplease see the notes below\nthanks a lot\nBest,\nTom", "p-a")
    assert [i["value"] for i in _items(agent)] == ["Noor"]


def test_pasted_readers_are_narrow():
    letter = _LETTER.format(to="Dana", sender="Piotr")
    assert name_learning.pasted_name_observations(letter) == [("Dana", "salutation"), ("Piotr", "signoff")]
    assert name_learning.pasted_name_observations("Hi Dana, how are you?") == []  # an ordinary sentence
    assert name_learning.pasted_name_observations("a\nb\nHi team,\nc\nThanks,\nSupport") == []
    assert name_learning.name_from_pasted_text(letter, known_given_name="Dana") == "Dana"
    assert name_learning.name_from_pasted_text(letter.replace("Hi Dana", "Hi Dan"), known_given_name="Daniel") == "Dan"
    assert name_learning.name_from_pasted_text(letter, known_given_name="Dan") == ""  # Dana is somebody else
    assert name_learning.name_from_pasted_text(letter, known_given_name="") == ""
    assert name_learning.own_voice_spans("my name is Noor") is None
    assert name_learning.own_voice_spans(letter) != []
    assert interpret_profile_turn(letter) == []


def test_account_and_display_names_are_read_conservatively():
    given = name_learning.given_name_from_display
    assert given("Dana Whitfield") == "Dana"
    assert given("Whitfield, Dana") == "Dana"
    assert given("dana whitfield") == "Dana"
    for not_a_person in ("", "admin", "Support Team", "dana@example.com", "DW", "Studio 5", "Info"):
        assert given(not_a_person) == "", not_a_person


# --- the operator's own mail -------------------------------------------------------------------


def _mail(to: str = "", sender: str = "") -> dict:
    return {"to": to, "from": sender, "subject": "Invoice", "date": "", "message_id": "<m1@example.invalid>", "snippet": ""}


def test_own_mail_reader_takes_only_the_operators_own_address():
    own = ["work@example.invalid"]
    read = name_learning.name_from_own_mail
    assert read([_mail(to="Dana Whitfield <work@example.invalid>", sender="Piotr Nowak <piotr@else.invalid>")], own) == "Dana"
    assert read([_mail(to="piotr@else.invalid", sender='"Whitfield, Dana" <work@example.invalid>')], own) == "Dana"
    assert read([_mail(to="Piotr Nowak <piotr@else.invalid>", sender="Lee <lee@else.invalid>")], own) == ""
    assert read([_mail(to="work@example.invalid")], own) == ""  # no display name
    assert read([_mail(to="work <work@example.invalid>")], own) == ""  # the address repeated
    assert read([_mail(to="Support Team <work@example.invalid>")], own) == ""
    # two appearances that disagree settle nothing
    assert read([_mail(to="Dana W <work@example.invalid>"), _mail(to="Marta K <work@example.invalid>")], own) == ""
    assert read([_mail(to="Dana W <work@example.invalid>")], []) == ""


def test_a_name_on_the_operators_own_mail_is_learned_and_said_once_with_an_undo(profile_env):
    from core.operator_profile_turn import learn_name_from_mail
    from core.request_trust import OWNER_LOCAL_KEY

    agent = make_agent()
    store_email_credential("work")
    ctx = {OWNER_LOCAL_KEY: True, "runtime_session_id": "m-a"}
    messages = [_mail(to="Dana Whitfield <work@example.invalid>", sender="Piotr Nowak <piotr@else.invalid>")]
    item = learn_name_from_mail(messages, source_context=ctx)
    assert item is not None and item.value_text == "Dana" and item.origin == "inferred"
    # Asked outright, VOOL says the name AND that nobody told it, so a wrong guess is easy to fix.
    assert _name(agent, "m-b") == "I have you down as Dana. I picked that up along the way, so tell me if it's wrong."
    first = chat(agent, "what is the capital of Portugal?", "m-b")
    said = [s["report"] for s in _frame(first).get("saved") or []]
    assert said == ["I'll call you Dana. I noticed the name on your email."]
    assert not _frame(chat(agent, "and of Spain?", "m-b")).get("saved")  # once
    # Undo drops it, and the mailbox is not asked again.
    response = served_post(agent, "/api/profile/restore", {"item_id": _frame(first)["saved"][0]["item_id"]})
    assert body_json(response)["change"]["kind"] == "forgotten"
    assert learn_name_from_mail(messages, source_context=ctx) is None
    assert _items(agent) == []


def test_mail_never_replaces_a_name_and_never_teaches_a_guest(profile_env):
    from core.operator_profile_turn import learn_name_from_mail
    from core.request_trust import OWNER_LOCAL_KEY

    agent = make_agent()
    store_email_credential("work")
    messages = [_mail(to="Dana Whitfield <work@example.invalid>")]
    guest = {"surface": "telegram", "platform": "telegram", "source_user_id": "111", "runtime_session_id": "tg-111"}
    assert learn_name_from_mail(messages, source_context=guest) is None
    chat(agent, "call me Dee", "m-a")
    assert learn_name_from_mail(messages, source_context={OWNER_LOCAL_KEY: True, "runtime_session_id": "m-a"}) is None
    assert [i["value"] for i in _items(agent)] == ["Dee"]


# ---------------------------------------------------------------------------------------------
# A name said beside a request. Measured on a real build with a local model 2026-11-11: "Hi, I'm
# Kofi, what is the capital of Portugal?" was answered "... Lisbon. Could not be answered: Hi, I'm
# Kofi" and the name was not learned: the lanes that split a message into parts ran ahead of the
# profile seat and were handed the introduction as a request.
# ---------------------------------------------------------------------------------------------

BESIDE_A_REQUEST = (
    ("Hi, I'm Kofi, what is the capital of Portugal?", "Kofi", "what is the capital of Portugal?"),
    ("Linh here, quick one: what is the capital of Portugal?", "Linh", "quick one: what is the capital of Portugal?"),
    ("this is Kofi from the bakery, whats the capital of Portugal", "Kofi", "whats the capital of Portugal"),
    ("my name is Ines and what is the capital of Portugal?", "Ines", "what is the capital of Portugal?"),
    ("call me Sam not Rick, what's 2+2?", "Sam", "what's 2+2?"),
    ("I'm Kofi. What is the capital of Portugal?", "Kofi", "What is the capital of Portugal?"),
    ("Hi I'm Kofi can you help me with my CV?", "Kofi", "can you help me with my CV?"),
    ("what's the capital of Portugal? I'm Kofi btw", "Kofi", "what's the capital of Portugal?"),
)


@pytest.mark.parametrize("text,name,request_left", BESIDE_A_REQUEST)
def test_a_name_beside_a_request_is_read_apart_from_the_request(text, name, request_left):
    from core.operator_profile_interpretation import name_beside_request

    proposal, remainder, (start, end) = name_beside_request(text)
    assert (proposal.value, remainder) == (name, request_left)
    assert name in text[start:end] and request_left.strip("?") not in text[start:end]


@pytest.mark.parametrize("text", ("I'm Kofi", "my name is Ines", "Hi, I'm Kofi, thanks", "I'm tired, what is the capital of Portugal?",
                                  "what is the capital of Portugal?", "I'm not Dana. What's 2+2?"))
def test_nothing_is_set_apart_when_there_is_no_name_or_no_request_beside_it(text):
    from core.operator_profile_interpretation import name_beside_request

    assert name_beside_request(text) is None


@pytest.mark.parametrize("text,name,request_left", BESIDE_A_REQUEST[:4])
def test_served_a_name_beside_a_question_is_learned_and_the_part_lanes_never_see_the_introduction(profile_env, monkeypatch, text, name, request_left):
    agent = make_agent()
    handed: list[str] = []
    for lane in ("_maybe_answer_conductor_turn", "_maybe_answer_demand_owned_turn", "_maybe_answer_planned_turn"):
        original = getattr(type(agent), lane)

        def spy(self, *args, _original=original, **kwargs):
            handed.extend(str(kwargs.get(key) or "") for key in (("plan_text",) if kwargs.get("plan_text") else ("raw_input", "effective_input")))
            return _original(self, *args, **kwargs)

        monkeypatch.setattr(type(agent), lane, spy)
    turn = chat(agent, text, "beside-a")
    assert not any(name in given for given in handed), handed
    reports = [entry.get("report") for entry in _frame(turn).get("saved") or []]
    assert len(reports) == 1 and name in reports[0], _frame(turn)  # said once, with its Undo
    assert not _frame(turn).get("candidates") and not _frame(turn).get("conflicts")
    assert [i["value"] for i in _items(agent)] == [name]
    assert name in _name(agent, "beside-b")
    # ... and it is not announced a second time on the next ordinary turn.
    later = chat(agent, "thanks, what should I cook tonight?", "beside-c")
    assert not _frame(later).get("saved"), _frame(later)


def test_served_a_name_beside_several_requests_is_learned_and_the_parts_are_planned_without_it(profile_env, monkeypatch):
    agent = make_agent()
    planned: list[str] = []
    original = type(agent)._maybe_answer_conductor_turn

    def spy(self, *args, **kwargs):
        planned.append(str(kwargs.get("plan_text") or kwargs.get("raw_input") or ""))
        return original(self, *args, **kwargs)

    monkeypatch.setattr(type(agent), "_maybe_answer_conductor_turn", spy)
    text = "hi my name is Bex and what is the capital of Portugal? also whats 12 times 12"
    turn = chat(agent, text, "several-a")
    assert planned and all("Bex" not in given for given in planned), planned
    assert "capital of Portugal" in planned[0] and len(planned[0]) == len(text)  # same positions as typed
    assert [entry.get("report") for entry in _frame(turn).get("saved") or []] == ["I'll call you Bex."], _frame(turn)
    assert [i["value"] for i in _items(agent)] == ["Bex"] and "Bex" in _name(agent, "several-b")


@pytest.mark.parametrize("text", ("my name isnt Agnes, its Sam", "dont call me Agnes, call me Ines", "no, it's Sam", "call me Sam not Agnes"))
def test_served_a_turn_that_only_states_a_name_is_answered_without_planning_parts(profile_env, monkeypatch, text):
    """Measured with a local model 2026-11-11: the comma made the part lanes plan (a 25 s model
    call) before the profile seat answered without using it."""
    agent = make_agent()
    chat(agent, "my name is Agnes", "only-a")
    planned: list[str] = []
    for lane in ("_maybe_answer_conductor_turn", "_maybe_answer_demand_owned_turn", "_maybe_answer_planned_turn"):
        monkeypatch.setattr(type(agent), lane, lambda self, *a, _lane=lane, **k: planned.append(_lane))
    turn = chat(agent, text, "only-a")
    assert not planned, planned
    assert turn["text"].startswith("Got it,") and "Agnes any more" in turn["text"], turn["text"]


def test_a_message_with_requests_and_no_name_still_reaches_the_part_lanes():
    from core.operator_profile_turn import turn_only_states_a_name

    for text in ("what is 2+2, and what's the weather in Kaunas?", "my name is Sam, what's 2+2?", "my name is Sam. always answer in French", "hello"):
        assert not turn_only_states_a_name(text), text
