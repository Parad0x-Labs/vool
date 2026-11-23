"""A learned memory line never changes a number the person typed, and never copies an order given to a tool.

Fresh-question audit of 02ff11496 (local qwen3:8b): a contact saved as "+1 555 0163" was learned as "Tel. +1 553
0163"; a German weekly reminder that failed to set was learned as "Erinnerung: Mülltonne rausstellen jeden Montag um 18
Uhr"; "which reminders do I have" and "what is Jörg's number" were then answered from those lines. Numbers, names,
languages and tools are drawn at random; the model's facts come from a stub, the turn's judge from its record.
"""
from __future__ import annotations

import json
import random

import pytest

from core import plugin_ask
from core.fact_extractor import FactExtractor
from core.vool_memory import VoolMemory

BLOCKS = ("user_profile", "project_context", "preferences")


@pytest.fixture(autouse=True)
def _no_judgements():
    plugin_ask.forget()
    yield
    plugin_ask.forget()


def _run(tmp_path, messages, facts):
    memory = VoolMemory(runtime_home=tmp_path)
    try:
        extractor = FactExtractor(memory=memory, model_client=lambda _c: json.dumps({"facts": facts}, ensure_ascii=False))
        applied = extractor.run_sync(messages)
        blocks = {block: str(memory.block_read(block) or "") for block in BLOCKS}
    finally:
        memory.close()
    return applied, "\n".join(blocks.values())


def _phone(rng: random.Random) -> tuple[str, list[str]]:
    groups = [str(rng.randint(1, 99)), f"{rng.randint(200, 989)}", f"{rng.randint(0, 9999):04d}"]
    return rng.choice([" ", "-", " "]).join(["+" + groups[0], *groups[1:]]), groups


@pytest.mark.parametrize("seed", range(8))
def test_a_changed_digit_is_never_learned(tmp_path, seed):
    rng = random.Random(f"digit-{seed}")
    phone, groups = _phone(rng)
    who = rng.choice(["Rūta Jankauskienė", "Jörg Brandt", "Marisol Vega", "Tomasz Wrona"])
    said = rng.choice([
        f"mano draugės {who} telefonas yra {phone}",
        f"die Handynummer von {who} ist {phone}",
        f"my neighbour {who} has the number {phone}",
        f"el móvil de {who} es {phone}",
    ])
    # One digit of one group changed, as the model wrote it.
    index = rng.randrange(1, 3)
    group = groups[index]
    at = rng.randrange(len(group))
    changed = group[:at] + str((int(group[at]) + rng.randint(1, 8)) % 10) + group[at + 1:]
    wrong = " ".join(["+" + groups[0], *[changed if i == index else g for i, g in enumerate(groups) if i]])
    applied, written = _run(tmp_path, [{"role": "user", "content": said}],
                            [{"action": "ADD", "block": "user_profile", "content": f"{who}: {wrong}"}])
    assert applied == [], written
    assert changed not in written


@pytest.mark.parametrize("seed", range(6))
def test_a_number_typed_by_the_person_is_learned_in_any_layout(tmp_path, seed):
    rng = random.Random(f"layout-{seed}")
    code = str(rng.randint(1000, 9999))
    hour, minute = rng.randint(6, 9), rng.choice([15, 30, 45])
    said, fact = rng.choice([
        (f"my gym locker code is {code}", f"Gym locker code: {code}"),
        (f"mano spintelės kodas sporto klube {code}", f"Sporto klubo spintelės kodas: {code}"),
        (f"ich stehe jeden Tag um {hour}:{minute} auf, mein Spindcode ist {code}",
         f"Steht um 0{hour}:{minute} auf, Spindcode {code}"),
        (f"my bike lock is {code[:2]} {code[2:]}", f"Bike lock: {code}"),
    ])
    applied, written = _run(tmp_path, [{"role": "user", "content": said}],
                            [{"action": "ADD", "block": "user_profile", "content": fact}])
    assert len(applied) == 1, written
    assert code[:2] in written


@pytest.mark.parametrize("seed", range(6))
def test_an_order_for_a_tool_is_never_a_learned_line(tmp_path, seed):
    rng = random.Random(f"order-{seed}")
    key, said, fact = rng.choice([
        ("reminders", "Erinnere mich jeden Montag um 18 Uhr daran, die Mülltonne rauszustellen",
         "Erinnerung: Mülltonne rausstellen jeden Montag um 18 Uhr"),
        ("reminders", "primink man rytoj vakare palaistyti gėles balkone", "Priminimas: palaistyti gėles balkone"),
        ("contacts", "išsaugok kontaktą Rūta Jankauskienė, telefonas +1 555 0163",
         "Kontaktas Rūta Jankauskienė, telefonas +1 555 0163"),
        ("contacts", "speichere Jörg Brandt als Kontakt, Handy +1 555 0185", "Kontakt: Jörg Brandt, Handy +1 555 0185"),
        ("notes", "jot down in my notes that the plumber comes thursday", "Plumber comes thursday"),
    ])
    plugin_ask._record_builtins(said, (key,))
    applied, written = _run(tmp_path, [{"role": "user", "content": said}],
                            [{"action": "ADD", "block": rng.choice(BLOCKS), "content": fact}])
    assert applied == [], written


@pytest.mark.parametrize("seed", range(4))
def test_a_fact_told_beside_an_order_is_still_learned(tmp_path, seed):
    rng = random.Random(f"beside-{seed}")
    order = rng.choice(["remind me tomorrow at 9 to call the bank", "primink man rytoj 9 val. paskambinti į banką"])
    told, fact = rng.choice([
        ("my sister Ilona lives in Kaunas", "Sister Ilona lives in Kaunas"),
        ("mano sesuo Ilona gyvena Kaune", "Sesuo Ilona gyvena Kaune"),
    ])
    plugin_ask._record_builtins(order, ("reminders",))
    plugin_ask._record_builtins(told, ())
    applied, written = _run(tmp_path, [{"role": "user", "content": order}, {"role": "assistant", "content": "ok"},
                                       {"role": "user", "content": told}],
                            [{"action": "ADD", "block": "user_profile", "content": fact}])
    assert len(applied) == 1, written
    assert "Ilona" in written


def test_with_no_judgement_a_fact_is_learned_as_before(tmp_path):
    said = "my locker code at the pool is 3312"
    applied, written = _run(tmp_path, [{"role": "user", "content": said}],
                            [{"action": "ADD", "block": "user_profile", "content": "Pool locker code: 3312"}])
    assert len(applied) == 1 and "3312" in written
