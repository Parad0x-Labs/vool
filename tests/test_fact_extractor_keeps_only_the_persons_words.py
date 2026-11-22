"""The background fact extractor writes only what the person said.

Blind audit of 58f60bbf3 (local qwen3:8b, a Lithuanian chat, profile DB rows): the model's lines
"<a fact the user explicitly stated about themselves>", the same placeholder in front of the assistant's
greeting, "Name: <name>" and "Name: Įrašyk" (in project_context) and the assistant's explanation of files and
folders were all written as the person's memory. Wording is built from parts at random; every case fails before.
"""
from __future__ import annotations

import json
import random

import pytest

from core.fact_extractor import FactExtractor
from core.vool_memory import VoolMemory


def _run(tmp_path, messages, facts):
    memory = VoolMemory(runtime_home=tmp_path)
    try:
        extractor = FactExtractor(memory=memory, model_client=lambda _c: json.dumps({"facts": facts}, ensure_ascii=False))
        applied = extractor.run_sync(messages)
        blocks = {block: str(memory.block_read(block) or "") for block in ("user_profile", "project_context", "preferences")}
    finally:
        memory.close()
    return applied, blocks


def _strip_accents(text: str) -> str:
    import unicodedata

    return "".join(ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch))


@pytest.mark.parametrize("seed", range(6))
def test_a_copied_placeholder_is_never_written(tmp_path, seed):
    rng = random.Random(f"placeholder-{seed}")
    placeholder = rng.choice(["<a fact the user explicitly stated about themselves>", "<name>", "<the corrected value>",
                              "<the currently stored value>"])
    greeting = rng.choice(["Sveikas! Galiu padėti su failais ir tekstu.", "Hi! I can help with files, text and pictures."])
    said = rng.choice(["labas", "hey there", "sveiki, kaip sekasi?"])
    block = rng.choice(["user_profile", "project_context", "preferences"])
    content = rng.choice([placeholder, f"{placeholder} {greeting}", f"{placeholder} Zalias Pavyzdys"])
    applied, blocks = _run(tmp_path, [{"role": "user", "content": said}, {"role": "assistant", "content": greeting}],
                           [{"action": "ADD", "block": block, "content": content}])
    assert applied == []
    assert "<" not in "".join(blocks.values())


@pytest.mark.parametrize("seed", range(6))
def test_a_name_line_needs_the_profile_in_every_block(tmp_path, seed):
    from tests.operator_profile_rig import set_owner_preferred_name

    rng = random.Random(f"name-{seed}")
    verb = rng.choice(["Įrašyk", "Irasyk", "Užrašyk", "Note"])
    said = f"{verb} {rng.choice(['kad', 'that'])} mano {rng.choice(['šuo', 'katė'])} {rng.choice(['Reksas', 'Murka'])}"
    block = rng.choice(["project_context", "preferences", "user_profile"])
    action = rng.choice(["ADD", "UPDATE"])
    fact = ({"action": "ADD", "block": block, "content": f"Name: {verb}"} if action == "ADD"
            else {"action": "UPDATE", "block": block, "old": "Name: Ada", "new": f"Name: {verb}"})
    set_owner_preferred_name("")
    try:
        applied, blocks = _run(tmp_path, [{"role": "user", "content": said}], [fact])
    finally:
        set_owner_preferred_name("")
    assert applied == []
    assert "Name:" not in "".join(blocks.values())


@pytest.mark.parametrize("seed", range(6))
def test_the_assistants_answer_is_not_the_persons_fact(tmp_path, seed):
    rng = random.Random(f"answer-{seed}")
    asked, answer = rng.choice([
        ("kuo skiriasi failas nuo aplanko?",
         "Failas yra vienas duomenų kūnas (pvz., dokumentas, nuotrauka), o aplankas yra vieta, kur saugomi kiti failai."),
        ("whats the diffrence between a file and a folder",
         "A file is a single body of data such as a document or a photo, while a folder is a place that holds other files."),
        ("Kas yra Lietuvos sostinė?", "Lietuvos sostinė yra Vilnius, didžiausias šalies miestas prie Neries upės."),
    ])
    block = rng.choice(["project_context", "user_profile"])
    applied, blocks = _run(tmp_path, [{"role": "user", "content": asked}, {"role": "assistant", "content": answer}],
                           [{"action": "ADD", "block": block, "content": answer}])
    assert applied == []
    assert not blocks[block]


@pytest.mark.parametrize("seed", range(8))
def test_what_the_person_said_is_still_kept(tmp_path, seed):
    rng = random.Random(f"kept-{seed}")
    colour, place = rng.choice([("žalia", "rūsyje"), ("mėlyna", "garaže"), ("raudona", "palėpėje")])
    said = f"{rng.choice(['Įsimink', 'Atsimink', 'zinok'])}: mano palapinė {colour} ir ji gyvena {place}."
    if rng.random() < 0.5:
        said = _strip_accents(said)
    if rng.random() < 0.5:
        said = said.replace("palapin", "palapni", 1)  # a slip
    reply = rng.choice(["Supratau.", "Gerai, įsiminiau.", "Ok!"])
    fact = rng.choice([f"Mano palapinė yra {colour}", f"Palapinė laikoma {place}", f"Palapinės spalva: {colour}",
                       "The user's tent is kept in the basement"])
    applied, blocks = _run(tmp_path, [{"role": "user", "content": said}, {"role": "assistant", "content": reply}],
                           [{"action": "ADD", "block": "user_profile", "content": fact}])
    assert [item.content for item in applied] == [fact]
    assert fact in blocks["user_profile"]


def test_a_paraphrase_in_the_assistants_spelling_is_still_the_persons(tmp_path):
    said = "my favorite color is teal and i ride a recumbent bike"
    reply = "Noted: your favourite colour is teal, and you ride a recumbent bicycle."
    fact = "Favourite colour: teal"
    applied, blocks = _run(tmp_path, [{"role": "user", "content": said}, {"role": "assistant", "content": reply}],
                           [{"action": "ADD", "block": "preferences", "content": fact}])
    assert [item.content for item in applied] == [fact]
