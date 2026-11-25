"""Remember, recall and forget reach VOOL's memory in any language.

Blind audit of 58f60bbf3 (local qwen3:8b, Lithuanian and English, raw rows in the audit's raw-memory folder):
- "ušmiršk palapinės faktą — trink tą informaciją apie palapinę iš savo atminties" ("forget the tent fact") was judged
  a Contacts ask (the turn's judge had no memory choice) and raised a contacts.delete approval for "žalia palapinė";
  the forget order itself was then saved as a memory row.
- "Įsimink du dalykus: mano palapinė žalia ir ji gyvena rūsyje." ("remember two things: ...") saved nothing.
- "forget everything about my tent, erase that memory completely" was taken as one target of all those words and
  matched nothing.

The memory lane's readers know English shapes only. Now VOOL's memory is a choice of the turn's judge (keep, recall,
forget), a judged forget names its targets from what VOOL holds by a second judgement, and the memory lane carries the
order out through the same forget path. The judges are scripted here: they pick the held items that carry the
subject's stem (an oracle, not the code under test). Wordings are built from parts at random, with and without
accents and with slips; every case below fails before this change.
"""
from __future__ import annotations

import random
import unicodedata

import pytest

import core.intent_arbiter as intent_arbiter
import core.persistent_memory as pm
from core import plugin_ask
from core.persistent_memory import everyday_memory_turn, maybe_handle_memory_command
from core.request_trust import OWNER_LOCAL_KEY
from tests.test_keep_receipt_decides_save_claims_v08 import CHAT, _fresh  # _fresh is a fixture
from tests.test_memory_forget_resurrection_p0 import _policy, isolated_home  # isolated_home is a fixture

__all__ = ["_fresh", "isolated_home"]

OWNER = {OWNER_LOCAL_KEY: True, "_owner_local": True, "chat_id": CHAT, "surface": "openclaw", "platform": "openclaw",
         "session_id": CHAT}
OTHER_CHAT = "memory-any-language-other"


def _plain(text: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch))


def _vary(rng: random.Random, text: str) -> str:
    if rng.random() < 0.5:
        text = _plain(text)
    if rng.random() < 0.4:
        text = text[0].lower() + text[1:]
    return text


def _stem_oracle(stems):
    """A scripted judge: names the held items that carry one of the subject's stems (accents off)."""
    def pick(_message, held, **_kwargs):
        return tuple(number for number, item in enumerate(held, 1)
                     if any(stem in _plain(item).casefold() for stem in stems))
    return pick


@pytest.fixture
def home(isolated_home, monkeypatch):
    from core.memory.revocation import initialize_memory_home

    initialize_memory_home(isolated_home)
    # The keep step's told-as-fact question, scripted: every kept sentence here tells a fact unless a test says not.
    monkeypatch.setattr(intent_arbiter, "arbitrate_told_as_fact", lambda *_a, **_k: "yes")
    _policy(CHAT)
    _policy(OTHER_CHAT)
    plugin_ask.forget()
    yield isolated_home
    plugin_ask.forget()


def _judged(text: str, kind: str) -> None:
    """The turn's judge read ``text`` as this memory ask (recorded as judge_turn records it)."""
    plugin_ask._record_builtins(text, (f"memory_{kind}",))


def _entries() -> str:
    from core.memory import entries
    from core.memory.files import memory_entries_path

    return " ".join(str(row.get("text") or "") for row in entries._load_erasure_rows(memory_entries_path())
                    if str(row.get("status") or "active").lower() not in {"erased", "superseded"})


def _keep(rng, chat=CHAT):
    colour, place = rng.choice([("žalia", "rūsyje"), ("mėlyna", "garaže"), ("oranžinė", "palėpėje")])
    lead = rng.choice(["Įsimink du dalykus:", "Atsimink šitą:", "Užsirašyk sau —", "Prašau įsiminti:"])
    text = _vary(rng, f"{lead} mano palapinė {colour} ir ji gyvena {place}.")
    _judged(text, "keep")
    handled, reply = maybe_handle_memory_command(text, session_id=chat, access_policy=_policy(chat),
                                                 source_context={**OWNER, "chat_id": chat, "session_id": chat})
    return text, handled, str(reply), colour


# ------------------------------------------------------------ the judge is asked about memory


def test_the_turns_judge_is_offered_memory_and_a_picked_forget_seats_no_contacts(monkeypatch):
    from core.capability_graph import model_visible_specs

    plugin_ask.forget()
    monkeypatch.setattr(plugin_ask, "_installed_with_tools", lambda: [])
    seen: list = []

    def arbitrate(message, choices, **_kwargs):
        seen.append(list(choices))
        return (next(n for n, choice in enumerate(choices, 1) if choice.startswith("Memory, forget")),)

    monkeypatch.setattr(intent_arbiter, "arbitrate_plugin_ask", arbitrate)
    monkeypatch.setattr(intent_arbiter, "arbitrate_memory_kind", lambda *_a, **_k: "forget")
    monkeypatch.setattr(intent_arbiter, "arbitrate_forget_order", lambda *_a, **_k: None)
    rng = random.Random("judge-offer")
    text = _vary(rng, rng.choice(["ušmiršk palapinės faktą — trink tą informaciją apie palapinę iš savo atminties",
                                  "pamiršk ką sakiau apie palapinę", "vergiss das mit meinem Zelt"]))
    plugin_ask.judge_turn(text, source_context=dict(OWNER), session_id=CHAT)
    assert any(choice.startswith("Memory, keep") for choice in seen[0])
    assert plugin_ask.judged_memory_ask(text) == "forget"
    assert not plugin_ask.judged_tool_ask(text)
    offered = {spec["intent"] for spec in model_visible_specs(user_text=text, source_context=dict(OWNER))}
    assert "contacts.delete" not in offered
    plugin_ask.forget()


# ------------------------------------------------------------ keep


@pytest.mark.parametrize("seed", range(4))
def test_a_keep_in_lithuanian_is_saved_without_its_lead(home, seed):
    rng = random.Random(f"keep-{seed}")
    text, handled, reply, colour = _keep(rng)
    assert handled and reply.startswith("Locked in"), reply
    saved = _entries()
    assert _plain(colour) in _plain(saved) and "palap" in _plain(saved)
    assert "Įsimink" not in saved and "Atsimink" not in saved and "Prašau" not in saved
    # The keep step leaves the turn to the memory lane, so the fact is never written twice.
    assert everyday_memory_turn(text, session_id=CHAT, source_context=dict(OWNER))


# ------------------------------------------------------------ forget


@pytest.mark.parametrize("seed", range(4))
def test_a_forget_in_lithuanian_forgets_the_fact_and_is_never_kept(home, monkeypatch, seed):
    rng = random.Random(f"forget-{seed}")
    _keep(rng)
    other = _vary(rng, "Prašau įsiminti: mano šuo vardu Reksas.")
    _judged(other, "keep")
    maybe_handle_memory_command(other, session_id=CHAT, access_policy=_policy(CHAT), source_context=dict(OWNER))
    assert "palap" in _plain(_entries()) and "Reksas" in _entries()
    monkeypatch.setattr(intent_arbiter, "arbitrate_forget_targets", _stem_oracle(["palap"]))
    order = _vary(rng, rng.choice([
        "ušmiršk palapinės faktą — trink tą informaciją apie palapinę iš savo atminties",
        "pamiršk viską apie mano palapinę",
        "ištrink iš atminties ką sakiau apie palapnę",
    ]))
    _judged(order, "forget")
    # Not kept as a fact by the keep step, and not anyone else's turn.
    assert everyday_memory_turn(order, session_id=CHAT, source_context=dict(OWNER))
    handled, reply = maybe_handle_memory_command(order, session_id=CHAT, access_policy=_policy(CHAT),
                                                 source_context=dict(OWNER))
    assert handled and str(reply).startswith("Forget applied"), reply
    assert "palap" not in _plain(_entries())
    assert "Reksas" in _entries()
    assert "trink" not in _entries() and "pamir" not in _plain(_entries())


def test_a_judged_name_the_message_never_touches_erases_nothing(home, monkeypatch):
    rng = random.Random("untouched")
    _keep(rng)
    monkeypatch.setattr(intent_arbiter, "arbitrate_forget_targets", lambda _m, held, **_k: tuple(range(1, len(held) + 1)))
    order = "pamiršk ką sakiau apie šunį"
    _judged(order, "forget")
    handled, reply = maybe_handle_memory_command(order, session_id=CHAT, access_policy=_policy(CHAT),
                                                 source_context=dict(OWNER))
    assert handled and reply == pm.JUDGED_FORGET_NAMES_NOTHING
    assert "palap" in _plain(_entries())


def test_with_no_judge_a_judged_forget_erases_nothing_and_is_handed_on(home, monkeypatch):
    rng = random.Random("no-judge")
    _keep(rng)
    monkeypatch.setattr(intent_arbiter, "arbitrate_forget_targets", lambda *_a, **_k: None)
    order = "pamiršk viską apie mano palapinę"
    _judged(order, "forget")
    handled, _reply = maybe_handle_memory_command(order, session_id=CHAT, access_policy=_policy(CHAT),
                                                  source_context=dict(OWNER))
    assert not handled
    assert "palap" in _plain(_entries())


def test_an_english_forget_whose_words_select_nothing_forgets_what_it_names(home, monkeypatch):
    from tests.test_everyday_remember_v08 import _say

    rng = random.Random("english-tent")
    colour = rng.choice(["green", "teal", "orange"])
    assert _say(f"Remember that my tent is {colour}")[1].startswith("Locked in")
    assert _say(f"Remember that my tent lives in the {rng.choice(['basement', 'garage', 'attic'])}")[1].startswith("Locked in")
    assert _say("Remember that my dog is called Rex")[1].startswith("Locked in")
    monkeypatch.setattr(intent_arbiter, "arbitrate_forget_targets", _stem_oracle(["tent"]))
    order = rng.choice(["forget everything about my tent, erase that memory completely",
                        "forget all the stuff about my tnet and the tent, wipe it",
                        "erase everything you know about my tent pls"])
    handled, reply = maybe_handle_memory_command(order, session_id=CHAT, access_policy=_policy(CHAT),
                                                 source_context=dict(OWNER))
    assert handled and str(reply).startswith("Forget applied"), reply
    assert "tent" not in _entries()
    assert "Rex" in _entries()


# ------------------------------------------------------------ recall


@pytest.mark.parametrize("seed", range(3))
def test_a_recall_in_lithuanian_in_another_chat_shows_the_saved_facts(home, monkeypatch, seed):
    rng = random.Random(f"recall-{seed}")
    _text, _handled, _reply, colour = _keep(rng)
    monkeypatch.setattr(intent_arbiter, "arbitrate_recall_facts", _stem_oracle(["palap"]))
    question = _vary(rng, rng.choice(["kur mano palapinė dabar? Ir kokios jos spalvos?",
                                      "primink, kokios spalvos mano palapnė ir kur ji laikoma",
                                      "ką tau sakiau apie palapinę?"]))
    _judged(question, "recall")
    context = {**OWNER, "chat_id": OTHER_CHAT, "session_id": OTHER_CHAT}
    handled, reply = maybe_handle_memory_command(question, session_id=OTHER_CHAT, access_policy=_policy(OTHER_CHAT),
                                                 source_context=context)
    assert handled, reply
    assert _plain(colour) in _plain(str(reply))
    assert "found nothing" not in str(reply) and "_" not in str(reply)


def test_a_recall_nothing_saved_answers_is_handed_on(home, monkeypatch):
    rng = random.Random("recall-none")
    _keep(rng)
    monkeypatch.setattr(intent_arbiter, "arbitrate_recall_facts", lambda *_a, **_k: ())
    question = "kaip vadinasi mano katė?"
    _judged(question, "recall")
    handled, _reply = maybe_handle_memory_command(question, session_id=CHAT, access_policy=_policy(CHAT),
                                                  source_context=dict(OWNER))
    assert not handled


# ------------------------------------------------------------ the keep step


def test_the_keep_step_keeps_no_order_the_judge_read_as_a_tool_ask(home, monkeypatch):
    from core.persistent_memory import keep_stated_facts

    monkeypatch.setattr(intent_arbiter, "arbitrate_lasting_fact", lambda *_a, **_k: "yes")
    monkeypatch.setattr(intent_arbiter, "arbitrate_standing_state", lambda *_a, **_k: "yes")
    monkeypatch.setattr(intent_arbiter, "arbitrate_told_as_fact", lambda *_a, **_k: "yes")
    rng = random.Random("orders")
    text = rng.choice(["just pay for it, dont ask me again, buy the cheapest hosting plan with my credits now",
                       "Išsaugok kontaktą: Zalias Pavyzdys, el. paštas zalias@pavyzdys.lt, telefonas +37060000000"])
    plugin_ask._record_builtins(text, ("contacts",))
    assert keep_stated_facts(text, session_id=CHAT, source_context=dict(OWNER)) == 0
    assert "Pavyzdys" not in _entries() and "hosting" not in _entries()


# ------------------------------------------------------------ Mac run of b43e6aced (real app, local qwen3:8b)
# - "which colour did i say my boat was, and where is it stored?" in a new chat, beside a saved "mano valtis mėlyna ...":
#   the English recall reader matched, its word lookup found nothing, and the model said it knew nothing about a boat.
# - after a Lithuanian forget of the boat, "kokios spalvos mano valtis?" in a new chat was answered with the colour.


@pytest.mark.parametrize("seed", range(3))
def test_an_english_recall_finds_a_fact_told_in_lithuanian(home, monkeypatch, seed):
    rng = random.Random(f"en-recall-{seed}")
    _text, _handled, _reply, colour = _keep(rng)
    monkeypatch.setattr(intent_arbiter, "arbitrate_recall_facts", _stem_oracle(["palap"]))
    question = rng.choice(["which colour did i say my tent was, and where is it stored?",
                           "what colur is my tent, do you remember where i keep it?",
                           "what do you remember about my tnet and where its stored"])
    context = {**OWNER, "chat_id": OTHER_CHAT, "session_id": OTHER_CHAT}
    handled, reply = maybe_handle_memory_command(question, session_id=OTHER_CHAT, access_policy=_policy(OTHER_CHAT),
                                                 source_context=context)
    assert handled, reply
    assert _plain(colour) in _plain(str(reply))


@pytest.mark.parametrize("seed", range(3))
def test_a_recall_after_a_lithuanian_forget_has_no_record(home, monkeypatch, seed):
    rng = random.Random(f"after-forget-{seed}")
    _text, _handled, _reply, colour = _keep(rng)
    monkeypatch.setattr(intent_arbiter, "arbitrate_forget_targets", _stem_oracle(["palap"]))
    monkeypatch.setattr(intent_arbiter, "arbitrate_recall_facts", _stem_oracle(["palap"]))
    order = _vary(rng, rng.choice(["pamiršk viską ką sakiau apie savo palapinę",
                                   "ištrink iš atminties viską apie mano palapinę, nebereikia"]))
    _judged(order, "forget")
    handled, reply = maybe_handle_memory_command(order, session_id=CHAT, access_policy=_policy(CHAT),
                                                 source_context=dict(OWNER))
    assert handled and str(reply).startswith("Forget applied"), reply
    question = _vary(rng, rng.choice(["kokios spalvos mano palapinė?", "ar prisimeni, kur laikau savo palapinę?",
                                      "pasakyk kokia mano palapines spalva"]))
    _judged(question, "recall")
    context = {**OWNER, "chat_id": OTHER_CHAT, "session_id": OTHER_CHAT}
    handled, reply = maybe_handle_memory_command(question, session_id=OTHER_CHAT, access_policy=_policy(OTHER_CHAT),
                                                 source_context=context)
    assert handled and reply == pm.JUDGED_RECALL_FORGOTTEN, reply
    assert _plain(colour) not in _plain(str(reply))


def test_a_fact_told_again_after_a_forget_is_recalled_again(home, monkeypatch):
    rng = random.Random("told-again")
    _keep(rng)
    monkeypatch.setattr(intent_arbiter, "arbitrate_forget_targets", _stem_oracle(["palap"]))
    monkeypatch.setattr(intent_arbiter, "arbitrate_recall_facts", _stem_oracle(["palap"]))
    order = "pamiršk viską apie mano palapinę"
    _judged(order, "forget")
    assert maybe_handle_memory_command(order, session_id=CHAT, access_policy=_policy(CHAT),
                                       source_context=dict(OWNER))[0]
    _text, handled, reply, colour = _keep(random.Random("told-again-new"), chat=OTHER_CHAT)
    assert handled and reply.startswith("Locked in"), reply
    question = "kokios spalvos mano palapinė?"
    _judged(question, "recall")
    context = {**OWNER, "chat_id": OTHER_CHAT, "session_id": OTHER_CHAT}
    handled, reply = maybe_handle_memory_command(question, session_id=OTHER_CHAT, access_policy=_policy(OTHER_CHAT),
                                                 source_context=context)
    assert handled and _plain(colour) in _plain(str(reply)), reply


# ------------------------------------------------------------ Mac run of ed76b5ec6
# - "what colour is my kayak?" after the kayak was forgotten: the judge named three facts about a backpack.
# - "primink, kokios spalvos mano kuprinė ..." (a recall) and "Prasau pamirsti ..." (a forget) were saved as facts by
#   the keep step at the start of the turn.


def _keep_line(text: str, chat: str = CHAT) -> None:
    _judged(text, "keep")
    handled, reply = maybe_handle_memory_command(text, session_id=chat, access_policy=_policy(chat),
                                                 source_context={**OWNER, "chat_id": chat, "session_id": chat})
    assert handled and str(reply).startswith("Locked in"), reply


@pytest.mark.parametrize("seed", range(3))
def test_a_forgotten_thing_is_no_record_even_when_the_judge_names_another_held_thing(home, monkeypatch, seed):
    rng = random.Random(f"kayak-{seed}")
    thing, other = rng.sample(["kayak", "hammock", "toolbox", "scooter"], 2)
    _keep_line(f"Įsimink: mano {rng.choice(['kuprinė', 'palapinė'])} žalia ir guli balkone.")
    _keep_line(f"Remember that my {other} is {rng.choice(['teal', 'navy'])} and lives in the shed.")
    _keep_line(f"Remember that my {thing} is lilac and lives in the loft.")
    monkeypatch.setattr(intent_arbiter, "arbitrate_forget_targets", _stem_oracle([thing]))
    order = f"pamiršk viską apie mano {thing}"
    _judged(order, "forget")
    assert maybe_handle_memory_command(order, session_id=CHAT, access_policy=_policy(CHAT),
                                       source_context=dict(OWNER))[0]
    # A judge that names every held fact, whatever is asked: the one measured named the backpack for the kayak.
    monkeypatch.setattr(intent_arbiter, "arbitrate_recall_facts", lambda _q, held, **_k: tuple(range(1, len(held) + 1)))
    question = rng.choice([f"what colour is my {thing}?", f"where do i keep my {thing} again?", f"wheres my {thing}"])
    _judged(question, "recall")
    context = {**OWNER, "chat_id": OTHER_CHAT, "session_id": OTHER_CHAT}
    handled, reply = maybe_handle_memory_command(question, session_id=OTHER_CHAT, access_policy=_policy(OTHER_CHAT),
                                                 source_context=context)
    assert handled and reply == pm.JUDGED_RECALL_FORGOTTEN, reply
    # A question about a fact still held is answered from it, though "my" is in the forgotten one too.
    monkeypatch.setattr(intent_arbiter, "arbitrate_recall_facts", _stem_oracle([other]))
    question = f"what colour is my {other}?"
    _judged(question, "recall")
    handled, reply = maybe_handle_memory_command(question, session_id=OTHER_CHAT, access_policy=_policy(OTHER_CHAT),
                                                 source_context=context)
    assert handled and other in str(reply) and reply != pm.JUDGED_RECALL_FORGOTTEN, reply


@pytest.mark.parametrize("seed", range(4))
def test_the_keep_step_keeps_no_recall_or_forget_the_judge_read(home, monkeypatch, seed):
    from core.persistent_memory import keep_stated_facts

    monkeypatch.setattr(intent_arbiter, "arbitrate_lasting_fact", lambda *_a, **_k: "yes")
    monkeypatch.setattr(intent_arbiter, "arbitrate_standing_state", lambda *_a, **_k: "yes")
    monkeypatch.setattr(intent_arbiter, "arbitrate_told_as_fact", lambda *_a, **_k: "yes")
    rng = random.Random(f"keep-step-{seed}")
    kind, text = rng.choice([
        ("recall", "primink, kokios spalvos mano kuprinė ir kur ji laikoma"),
        ("recall", "primink man kokia spalva buvo mano kuprine"),
        ("forget", "Prasau pamirsti informacija apie kuprine."),
        ("forget", "nebeatsimink nieko apie mano kuprinę, ištrink"),
    ])
    _judged(text, kind)
    assert keep_stated_facts(text, session_id=CHAT, source_context=dict(OWNER)) == 0
    assert "kupr" not in _plain(_entries())


# ------------------------------------------------------------ Mac run of 2bba40425
# "Atsimink šitą: mano palapinė yra oranžinė, o stovi ji palėpėje." ("remember this: ...") was judged a forget by the
# tool judgement, and nothing was saved. The memory pick is put to the memory's own question, whose answer stands.


#: (tool judgement's memory pick, the memory question's answer, the forget yes/no, the told-as-fact yes/no) -> kind.
_READINGS = [
    ("Memory, keep", "keep", None, None, "keep"),            # both readings agree
    ("Memory, forget", "forget", None, None, "forget"),
    ("Memory, recall", "recall", None, None, "recall"),
    ("Memory, forget", "keep", "yes", "no", "forget"),       # bef26afe4: a Lithuanian forget read as a keep
    ("Memory, forget", "keep", "no", "yes", ""),             # 2bba40425: a Lithuanian remember read as a forget
    ("Memory, recall", "keep", None, "yes", "recall"),       # 32f3c9e38: "pamirsk ka pasakojau ..." kept as a fact
    ("Memory, recall", "forget", "yes", None, "forget"),     # d585608b6: a Lithuanian forget read as a recall
    ("Memory, recall", "forget", "no", None, "recall"),
    ("Memory, recall", "none", None, None, "recall"),        # bef26afe4: a recall the tool judgement had right
    (None, "keep", None, "no", ""),                          # bef26afe4: "parasyk trumpa eilerasti ..." read as a keep
    # iMac gate on 95fae99a0: "zapamiętaj proszę: moja sąsiadka Ewa ma kota ..." was read keep by the memory question
    # alone and not kept. One reading stands when a second one says the message tells a fact and is no forget order.
    (None, "keep", None, "yes", "keep"),
    (None, "keep", "yes", "yes", ""),
    ("Memory, forget", None, None, None, ""),                # a forget never stands on an unreadable second reading
    ("Memory, forget", None, "yes", None, "forget"),
    (None, "forget", None, None, ""),
    ("Memory, recall", "keep", "yes", None, "forget"),       # 28ac44632 run16: "istrink, ka tau sakiau apie ..."
    ("Memory, recall", "keep", "no", "yes", "recall"),       # readings that disagree, and no forget: as before
    ("Memory, keep", "recall", "yes", "no", "forget"),
    ("Memory, keep", "recall", "yes", "yes", "recall"),      # 786af505 run20: a message that tells a fact is no forget
    ("Memory, recall", "recall", "yes", None, "recall"),     # readings that agree are never asked the forget question
    ("Memory, keep", "keep", "yes", "yes", "keep"),
]


@pytest.mark.parametrize("case", range(len(_READINGS)))
def test_the_memory_kind_is_decided_by_both_readings(monkeypatch, case):
    tool_pick, kind, forget_yes, told_yes, expected = _READINGS[case]
    rng = random.Random(f"kind-{case}")
    plugin_ask.forget()
    monkeypatch.setattr(plugin_ask, "_installed_with_tools", lambda: [])

    def arbitrate(message, choices, **_kwargs):
        if tool_pick is None:
            return (0,)
        return (next(n for n, choice in enumerate(choices, 1) if choice.startswith(tool_pick)),)

    monkeypatch.setattr(intent_arbiter, "arbitrate_plugin_ask", arbitrate)
    monkeypatch.setattr(intent_arbiter, "arbitrate_memory_kind", lambda *_a, **_k: kind)
    monkeypatch.setattr(intent_arbiter, "arbitrate_forget_order", lambda *_a, **_k: forget_yes)
    monkeypatch.setattr(intent_arbiter, "arbitrate_told_as_fact", lambda *_a, **_k: told_yes)
    text = _vary(rng, rng.choice(["Atsimink šitą: mano dviratis žalias, stovi rūsyje.", "merk dir: mein Rad ist grün",
                                  "nebeatsimink nieko apie dviratį", "kur stovi mano dviratis?"])) + f" ({case})"
    plugin_ask.judge_turn(text, source_context=dict(OWNER), session_id=CHAT)
    assert plugin_ask.judged_memory_ask(text) == expected
    plugin_ask.forget()


@pytest.mark.parametrize("case", range(5))
def test_the_memory_question_is_asked_beside_any_tool_pick(monkeypatch, case):
    """Probe of 7682b4155 (qwen3:8b): "kur laikau savo palapinę?" was judged a Contacts ask and never reached the memory
    question. A recall is added beside a tool; a keep or a forget never takes a tool's order."""
    tool, second, keys = [
        ("Contacts", "recall", ("contacts", "memory_recall")),
        ("My things", "recall", ("things", "memory_recall")),
        ("Reminders", "keep", ("reminders",)),
        ("Contacts", "forget", ("contacts",)),
        (None, "forget", ("memory_forget",)),
    ][case]
    # A forget on one reading is asked the told-as-fact question too: an order tells no fact.
    plugin_ask.forget()
    monkeypatch.setattr(plugin_ask, "_installed_with_tools", lambda: [])

    def arbitrate(message, choices, **_kwargs):
        return (next(n for n, choice in enumerate(choices, 1) if choice.startswith(tool)),) if tool else (0,)

    monkeypatch.setattr(intent_arbiter, "arbitrate_plugin_ask", arbitrate)
    monkeypatch.setattr(intent_arbiter, "arbitrate_memory_kind", lambda *_a, **_k: second)
    monkeypatch.setattr(intent_arbiter, "arbitrate_forget_order", lambda *_a, **_k: "yes")
    monkeypatch.setattr(intent_arbiter, "arbitrate_told_as_fact", lambda *_a, **_k: "no" if second == "forget" else "yes")
    text = f"zinute {case}: kur laikau savo palapine?"
    plugin_ask.judge_turn(text, source_context=dict(OWNER), session_id=CHAT)
    assert plugin_ask.judged_builtins(text) == keys
    plugin_ask.forget()


def test_the_memory_question_reads_only_its_four_answers():
    from core.intent_arbiter import _memory_kind_prompt, _memory_kind_reader

    assert _memory_kind_reader('{"answer": "forget"}') == "forget"
    assert _memory_kind_reader('ok {"answer": "Keep"}') == "keep"
    assert _memory_kind_reader('{"answer": "delete"}') is None and _memory_kind_reader("forget") is None
    prompt = _memory_kind_prompt("labas")
    assert prompt.rstrip().endswith('{"answer": "none"}') and 'Message: "labas" ->' in prompt


def test_vools_own_reply_is_never_a_forget_target(home, monkeypatch):
    """Mac (d585608b6, 2bba40425, 8898ba4b4): a judged forget recorded VOOL's reply "locked in. i'll remember that." as
    a forgotten token of the chat."""
    rng = random.Random("own-reply")
    thing = rng.choice(["kayak", "hammock", "lantern"])
    text = f"Remember that my {thing} is navy and lives in the shed."
    _keep_line(text)
    reply = rng.choice(["Locked in. I’ll remember that.", "Got it, noted.", "Locked in. I'll remember that."])
    pm._write_conversation_event(session_id=CHAT, user_input=text, assistant_output=reply, source_context=dict(OWNER))
    monkeypatch.setattr(pm, "_whole_erase_learned_lines", lambda _context: [reply])
    held = pm._held_for_judgement(session_id=CHAT, policy=_policy(CHAT), source_context=dict(OWNER), served=False)
    assert held and all(pm._plain_sentence(item) not in {pm._plain_sentence(reply)}
                        for item in held), held
    assert any(thing in item for item in held)


class _LaneAgent:
    """The agent seams the memory fast path calls: a fast-path result, and no companion memory."""

    def _fast_path_result(self, *, response, **_kwargs):
        return {"response": str(response)}

    def _maybe_handle_companion_memory_fast_path(self, *_args, **_kwargs):
        return None


def _recall_lane(text: str):
    from core.agent_runtime import memory_runtime

    return memory_runtime.maybe_handle_memory_fast_path(
        _LaneAgent(), text, session_id=CHAT, source_context=dict(OWNER),
        access_policy=_policy(CHAT), maybe_handle_memory_command_fn=maybe_handle_memory_command)


@pytest.mark.parametrize("seed", range(3))
def test_a_recall_with_nothing_saved_is_not_saved_never_a_guess(home, monkeypatch, seed):
    """Mac run9 (e6c724ef8): with nothing saved, "kokios spalvos mano valtis?" was handed to the model and answered
    "raudona", and "where is my boat?" "docked at the marina by the old lighthouse"."""
    rng = random.Random(f"nothing-saved-{seed}")
    text = _vary(rng, rng.choice(["kokios spalvos mano valtis?", "kur laikau savo dviratį?",
                                  "primink, kokia mano skėčio spalva", "ar prisimeni, kur mano lempa?"]))
    _judged(text, "recall")
    monkeypatch.setattr(intent_arbiter, "arbitrate_recall_facts", lambda *_a, **_k: ())
    result = _recall_lane(text)
    # In the language the person wrote in (core.i18n.lane_receipts): the same "not saved", never a guess.
    from core.i18n.lane_receipts import in_the_persons_language

    assert result and result["response"] == in_the_persons_language(pm.JUDGED_RECALL_NOTHING, text), result


def test_a_recall_with_a_saved_fact_still_reads_it_and_a_chat_with_earlier_turns_goes_on(home, monkeypatch):
    rng = random.Random("nothing-saved-held")
    _text, handled, _reply, colour = _keep(rng)
    assert handled
    plugin_ask.forget()
    question = "kokios spalvos mano palapine?"
    _judged(question, "recall")
    monkeypatch.setattr(intent_arbiter, "arbitrate_recall_facts", _stem_oracle(["palapin"]))
    result = _recall_lane(question)
    assert result and _plain(colour) in _plain(result["response"]), result
    # Not judged a memory recall: nothing answers in its place.
    plugin_ask.forget()
    assert _recall_lane("kiek bus 23 kart 4?") is None


def test_a_forget_never_puts_vools_receipt_on_the_forget_list(home):
    """Mac run9 (e6c724ef8): the forget of a sentence said in this chat also forgot each line of the reply it got, so
    "locked in. i'll remember that." was on the forget list. A reply line that says the sentence back still is."""
    rng = random.Random("receipt-not-forgotten")
    thing = rng.choice(["kayak", "hammock", "lantern"])
    said = f"my {thing} is navy and lives in the shed"
    pm._write_conversation_event(session_id=CHAT, user_input=f"Remember that {said}.", assistant_output=pm.KEPT_REPLY,
                                 source_context=dict(OWNER))
    pm._write_conversation_event(session_id=CHAT, user_input=f"So {said}, ok?",
                                 assistant_output=f"Yes, your {thing} is navy, in the shed.", source_context=dict(OWNER))
    lines = pm._replies_to_own_chat_sentence(said, session_id=CHAT, source_context=dict(OWNER))
    assert pm.KEPT_REPLY not in lines, lines
    assert any(thing in line for line in lines), lines


@pytest.mark.parametrize("seed", range(3))
def test_a_question_both_readings_call_a_keep_is_never_kept(home, monkeypatch, seed):
    """Mac run10 (eb8c907cc): "primink, kokios spalvos mano dviratis ir kur ji laikoma" ("remind me what colour my bike
    is and where it is kept") was read a keep by both readings, saved as a fact and answered "Locked in"."""
    rng = random.Random(f"question-not-kept-{seed}")
    text = _vary(rng, rng.choice(["primink, kokios spalvos mano dviratis ir kur ji laikoma",
                                  "pasakyk man kur stovi mano valtis", "erinnere mich wo mein Fahrrad steht"]))
    _judged(text, "keep")
    monkeypatch.setattr(intent_arbiter, "arbitrate_told_as_fact", lambda *_a, **_k: "no")
    monkeypatch.setattr(intent_arbiter, "arbitrate_recall_facts", lambda *_a, **_k: ())
    handled, reply = maybe_handle_memory_command(text, session_id=CHAT, access_policy=_policy(CHAT),
                                                 source_context=dict(OWNER))
    assert not _entries().strip(), _entries()
    assert str(reply) != pm.KEPT_REPLY and (not handled or str(reply) == pm.JUDGED_RECALL_NOTHING), (handled, reply)


def test_a_reply_that_says_the_fact_back_never_hides_it(home, monkeypatch):
    """Mac run11 (4c72471b7): the reply to "atsimink — kaimynų šuniukas mėgsta tik ryžius" said the sentence back, so the
    saved fact was taken for VOOL's own words: a later recall held nothing and a later forget erased nothing."""
    rng = random.Random("reply-says-it-back")
    fact = _vary(rng, "kaimynų šuniukas mėgsta tik ryžius")
    said = f"atsimink — {fact}"
    _judged(said, "keep")
    maybe_handle_memory_command(said, session_id=CHAT, access_policy=_policy(CHAT), source_context=dict(OWNER))
    pm._write_conversation_event(session_id=CHAT, user_input=said, assistant_output=f"{fact}. Saved to memory.",
                                 source_context=dict(OWNER))
    held = pm._held_for_judgement(session_id=OTHER_CHAT, policy=_policy(OTHER_CHAT), source_context=dict(OWNER),
                                  served=False)
    assert any("ryž" in item or "ryz" in item for item in held), held


@pytest.mark.parametrize("seed", range(3))
def test_a_judged_keep_saves_the_fact_without_the_request(home, monkeypatch, seed):
    """Mac run12 (6aabca905): "prisimink, kad kaimyno šuo ėda vien obuolius" was saved whole, the request with it."""
    rng = random.Random(f"kept-fact-words-{seed}")
    lead = rng.choice(["prisimink, kad", "Įsidėmėk, kad", "nepamiršk, jog"])
    fact = rng.choice(["kaimyno šuo ėda vien obuolius", "mano dviratis stovi rūsyje", "sesuo gyvena Kaune"])
    text = _vary(rng, f"{lead} {fact}")
    _judged(text, "keep")

    def pick(_message, versions, **_kwargs):  # a scripted judge: the version that starts where the fact starts
        start = _plain(fact).casefold().split()[0]
        return next(n for n, v in enumerate(versions, 1) if _plain(v).casefold().startswith(start))

    monkeypatch.setattr(intent_arbiter, "arbitrate_kept_fact", pick)
    handled, reply = maybe_handle_memory_command(text, session_id=CHAT, access_policy=_policy(CHAT),
                                                 source_context=dict(OWNER))
    assert handled and str(reply) == pm.KEPT_REPLY, reply
    saved = _plain(_entries()).casefold()
    assert _plain(fact).casefold().split()[-1] in saved and _plain(lead).casefold().split()[0] not in saved, saved


def test_the_kept_fact_question_offers_only_the_persons_own_words():
    prompt = intent_arbiter._kept_fact_prompt("prisimink, kad šuo ėda obuolius",
                                              ["prisimink, kad šuo ėda obuolius", "kad šuo ėda obuolius"])
    assert '1. "prisimink, kad šuo ėda obuolius"' in prompt and '2. "kad šuo ėda obuolius"' in prompt


def _judged_none(text: str) -> None:
    """The turn's judge read ``text`` and picked nothing (recorded as judge_turn records it)."""
    plugin_ask._record_builtins(text, ())


@pytest.mark.parametrize("seed", range(3))
def test_a_question_both_readings_call_none_is_answered_from_a_held_fact_it_names(home, monkeypatch, seed):
    """Mac gate of 561e3e9d0: "Koks sportas man labiausiai patinka?" beside a saved "Mano mėgstamiausias sportas yra
    krepšinis" was read none by both readings and a model made up an answer."""
    rng = random.Random(f"none-but-held-{seed}")
    sport = rng.choice(["krepšinis", "tenisas", "plaukimas"])
    said = f"Mano mėgstamiausias sportas yra {sport}"
    _judged(said, "keep")
    maybe_handle_memory_command(said, session_id=OTHER_CHAT, access_policy=_policy(OTHER_CHAT),
                                source_context={**OWNER, "chat_id": OTHER_CHAT, "session_id": OTHER_CHAT})
    question = _vary(rng, rng.choice(["Koks sportas man labiausiai patinka?", "kokį sportą mėgstu labiausiai?",
                                      "ar žinai mano mėgstamiausią sportą?"]))
    _judged_none(question)
    monkeypatch.setattr(intent_arbiter, "arbitrate_recall_facts", _stem_oracle(["sport"]))
    handled, reply = maybe_handle_memory_command(question, session_id=CHAT, access_policy=_policy(CHAT),
                                                 source_context=dict(OWNER))
    assert handled and _plain(sport) in _plain(str(reply)), (handled, reply)
    # A question that shares no word with anything held is not put to the recall judge.
    other = "kiek bus 23 kart 4?"
    _judged_none(other)
    asked = []
    monkeypatch.setattr(intent_arbiter, "arbitrate_recall_facts", lambda *a, **k: asked.append(a) or ())
    assert maybe_handle_memory_command(other, session_id=CHAT, access_policy=_policy(CHAT),
                                       source_context=dict(OWNER)) == (False, "")
    assert not asked


@pytest.mark.parametrize("seed", range(3))
def test_a_keep_the_person_asks_not_to_make_keeps_nothing(home, monkeypatch, seed):
    """Mac gate of 561e3e9d0 / run13: "merk dir das bitte nicht: meine Lampe heißt Zaunkönig" was a keep to both
    readings, answered "Locked in" and saved."""
    rng = random.Random(f"keep-not-wanted-{seed}")
    name = rng.choice(["Zaunkönig", "Pfefferkorn", "Blaubeere"])
    text = _vary(rng, rng.choice([f"merk dir das bitte nicht: meine Lampe heißt {name}",
                                  f"mano šuo vardu {name}, bet to neįsimink",
                                  f"my kettle is called {name}, don't save that"]))
    _judged(text, "keep")
    monkeypatch.setattr(intent_arbiter, "arbitrate_keep_wanted", lambda *_a, **_k: "no")
    handled, reply = maybe_handle_memory_command(text, session_id=CHAT, access_policy=_policy(CHAT),
                                                 source_context=dict(OWNER))
    assert handled and reply == pm.KEEP_NOT_WANTED_REPLY, reply
    assert _plain(name).casefold() not in _plain(_entries()).casefold()


def test_a_kept_fact_with_no_marked_lead_is_kept_whole(monkeypatch):
    """Mac run15 (879d8eeb8): offered every first word, the judge cut "mano kuprinė žalia ir ji laikoma balkone" to "ji
    laikoma balkone". Only a lead set off by a mark is ever left off, with at most one word after the mark."""
    asked = []
    monkeypatch.setattr(intent_arbiter, "arbitrate_kept_fact", lambda _m, versions, **_k: asked.append(versions) or 3)
    whole = "mano kuprinė žalia ir ji laikoma balkone"
    assert pm._kept_fact_words(whole, session_id=CHAT, source_context={}) == whole and not asked
    led = "prisimink, kad kaimyno šuo ėda vien morkas"
    assert pm._kept_fact_words(led, session_id=CHAT, source_context={}) == "kaimyno šuo ėda vien morkas"
    assert all(len(version.split()) >= len(led.split()) - 2 for version in asked[0]), asked


@pytest.mark.parametrize("seed", range(3))
def test_a_fact_said_in_passing_with_dont_keep_it_is_not_kept_by_the_keep_step(home, monkeypatch, seed):
    """Mac run17 (1bf694c5): "mein Hamster heißt Wirbelwind, aber speicher das nicht" was read none by both readings,
    the keep step wrote it, and the reply ended 'Saved to memory: ...'."""
    rng = random.Random(f"keep-step-not-wanted-{seed}")
    name = rng.choice(["Wirbelwind", "Krümel", "Pfeffer"])
    text = _vary(rng, rng.choice([f"mein Hamster heißt {name}, aber speicher das nicht",
                                  f"mano žiurkėnas vardu {name}, tik nesaugok šito"]))
    _judged_none(text)
    for judge in ("arbitrate_lasting_fact", "arbitrate_standing_state", "arbitrate_told_as_fact"):
        monkeypatch.setattr(intent_arbiter, judge, lambda *_a, **_k: "yes")
    monkeypatch.setattr(intent_arbiter, "arbitrate_kept_fact", lambda *_a, **_k: None)
    monkeypatch.setattr(intent_arbiter, "arbitrate_keep_wanted", lambda *_a, **_k: "no")
    assert pm.keep_stated_facts(text, session_id=CHAT, access_policy=_policy(CHAT), source_context=dict(OWNER)) == 0
    assert name.casefold() not in _entries().casefold()
    monkeypatch.setattr(intent_arbiter, "arbitrate_keep_wanted", lambda *_a, **_k: "yes")
    plain = f"my hamster is called {name}"
    assert pm.keep_stated_facts(plain, session_id=CHAT, access_policy=_policy(CHAT), source_context=dict(OWNER)) == 1


@pytest.mark.parametrize("tool_pick", [None, "Memory, keep", "Memory, recall"])
def test_a_forget_settled_by_one_reading_stands_unless_the_message_tells_a_fact(monkeypatch, tool_pick):
    """probe18/probe19 (qwen3:8b): guarding a tie forget with the keep-wanted and lasting-fact questions lost real
    forgets ("fogret everything about my tent", "pamiršk viską apie mano lempą"). A "<fact>, don't keep it" read as a
    forget is safe: it erases only held facts it names, and says nothing was erased when it names none."""
    plugin_ask.forget()
    monkeypatch.setattr(plugin_ask, "_installed_with_tools", lambda: [])

    def arbitrate(message, choices, **_kwargs):
        if tool_pick is None:
            return (0,)
        return (next(n for n, choice in enumerate(choices, 1) if choice.startswith(tool_pick)),)

    asked = []
    monkeypatch.setattr(intent_arbiter, "arbitrate_plugin_ask", arbitrate)
    monkeypatch.setattr(intent_arbiter, "arbitrate_memory_kind", lambda *_a, **_k: "forget")
    monkeypatch.setattr(intent_arbiter, "arbitrate_forget_order", lambda *_a, **_k: "yes")
    monkeypatch.setattr(intent_arbiter, "arbitrate_keep_wanted", lambda *_a, **_k: asked.append("wanted") or "no")
    monkeypatch.setattr(intent_arbiter, "arbitrate_lasting_fact", lambda *_a, **_k: asked.append("lasting") or "yes")
    # Mac run20 (786af505): "mano suo vardu Wirbelwind, bet to neisimink" was a forget on one reading and erased two
    # unrelated facts. A message that tells a fact is not a forget on one reading.
    # Beside a recall reading (both read it as about something told before) the told-as-fact question is not asked:
    # it read plain forget orders as telling a fact (Mac run21, 91e89867).
    for told, expected in (("no", "forget"), (None, "forget"), ("yes", "forget" if tool_pick == "Memory, recall" else "")):
        monkeypatch.setattr(intent_arbiter, "arbitrate_told_as_fact", lambda *_a, _t=told, **_k: _t)
        text = f"pamiršk viską apie mano lempą ({tool_pick} {told})"
        plugin_ask.judge_turn(text, source_context=dict(OWNER), session_id=CHAT)
        assert plugin_ask.judged_memory_ask(text) == expected, told
    assert not asked
    plugin_ask.forget()


@pytest.mark.parametrize("seed", range(3))
def test_a_fact_whose_lead_went_at_a_dash_loses_no_more_words(home, monkeypatch, seed):
    """Mac run19 (f7e6cf69): "Prasau isiminti — mano valtis oranzine, laikau ja sandeliuke." lost its lead at the dash,
    then the picker read "mano valtis oranzine," as a second lead and saved only "laikau ja sandeliuke"."""
    rng = random.Random(f"one-lead-{seed}")
    colour = rng.choice(["oranzine", "žalia", "mėlyna"])
    text = rng.choice([f"Prasau isiminti — mano valtis {colour}, laikau ja sandeliuke.",
                       f"Įsimink: mano valtis {colour}, stovi garaže."])
    _judged(text, "keep")
    monkeypatch.setattr(intent_arbiter, "arbitrate_told_as_fact", lambda *_a, **_k: "yes")
    monkeypatch.setattr(intent_arbiter, "arbitrate_keep_wanted", lambda *_a, **_k: "yes")
    monkeypatch.setattr(intent_arbiter, "arbitrate_kept_fact", lambda _m, versions, **_k: len(versions))
    handled, reply = maybe_handle_memory_command(text, session_id=CHAT, access_policy=_policy(CHAT),
                                                 source_context=dict(OWNER))
    assert handled and reply == pm.KEPT_REPLY, reply
    assert f"mano valtis {colour}" in _entries(), _entries()


def test_a_forget_erases_only_what_a_word_naming_one_thing_ties_it_to(home, monkeypatch):
    """Mac run20 (786af505): a forget judged on "mano suo vardu Wirbelwind, bet to neisimink" erased "mano sporto šaka -
    tinklinis" (shares "mano") and "primink, ka valgo kaimyno suo" (shares "suo"). A word most held items share, or
    one under four letters, names no one thing."""
    held = ["mano sporto šaka – tinklinis", "primink, ka valgo kaimyno suo", "mano lempa kaštoninė, stovi palėpėje",
            "mano dviratis žalias"]
    monkeypatch.setattr(pm, "_held_for_judgement", lambda **_k: list(held))
    monkeypatch.setattr(pm, "_turn_may_read_memory", lambda *_a, **_k: True)
    monkeypatch.setattr(intent_arbiter, "arbitrate_forget_targets", lambda *_a, **_k: (1, 2, 3, 4))
    assert pm._judged_forget_items("mano suo vardu Wirbelwind, bet to neisimink", session_id=CHAT,
                                   source_context=dict(OWNER)) == []
    assert pm._judged_forget_items("pamiršk viską apie mano lempą", session_id=CHAT,
                                   source_context=dict(OWNER)) == [held[2]]
