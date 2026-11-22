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
