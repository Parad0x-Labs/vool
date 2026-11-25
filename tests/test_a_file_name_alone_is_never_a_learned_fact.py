"""A file name or path alone is never learned as something the person told VOOL.

iMac gate on 174b25378 (qwen3:8b): after "please create plumber-notes.txt saying the kitchen tap drips at night" the
extractor learned "plumber-notes.txt" under project_context; it was the only held fact, and in a new chat "which locker
did I say I use at the gym?" was answered "You told me: plumber-notes.txt". The extractor's own rules already say no
file paths. The model's facts come from a stub; names, folders and blocks are drawn at random.
"""
from __future__ import annotations

import json
import random

import pytest

from core.fact_extractor import FactExtractor
from core.vool_memory import VoolMemory

BLOCKS = ("user_profile", "project_context", "preferences")
NAMES = ("plumber-notes.txt", "seed-order.txt", "druskos-sarasas.txt", "Q3-plan.pdf", "garden.md")


def _run(tmp_path, messages, facts):
    memory = VoolMemory(runtime_home=tmp_path)
    try:
        extractor = FactExtractor(memory=memory, model_client=lambda _c: json.dumps({"facts": facts}, ensure_ascii=False))
        extractor.run_sync(messages)
        return "\n".join(str(memory.block_read(block) or "") for block in BLOCKS)
    finally:
        memory.close()


@pytest.mark.parametrize("seed", range(8))
def test_a_file_name_from_a_write_request_is_not_learned(tmp_path, seed):
    rng = random.Random(f"file-name-{seed}")
    name = rng.choice(NAMES)
    shown = rng.choice([name, f"File: {name}", f"~/Documents/{name}", f"/srv/work/proj/{name}", f"./{name}"])
    messages = [{"role": "user", "content": f"please create {name} saying the kitchen tap drips at night"},
                {"role": "assistant", "content": f"Created {name}."}]
    learned = _run(tmp_path, messages, [{"action": "ADD", "block": rng.choice(BLOCKS), "content": shown, "turns": [1]}])
    assert name not in learned, learned


def test_a_fact_that_mentions_a_file_or_an_address_is_still_learned(tmp_path):
    messages = [{"role": "user", "content": "my gym locker number is 218 and my email is jonas@example.com"}]
    learned = _run(tmp_path, messages, [
        {"action": "ADD", "block": "user_profile", "content": "Gym locker number: 218", "turns": [1]},
        {"action": "ADD", "block": "user_profile", "content": "Email: jonas@example.com", "turns": [1]},
    ])
    assert "218" in learned and "jonas@example.com" in learned, learned
