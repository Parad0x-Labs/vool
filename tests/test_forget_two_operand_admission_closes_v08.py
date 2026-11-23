"""A forget the request's reader counts as two operands, and the forget command as one target, closes its admission.

Mac run of d585608b6 (real app from source, local qwen3:8b): "forget everything about my scooter, erase that memory
completely" erased the scooter and answered "Forget applied", but the turn's forget admission stayed pending in the
ledger. The request's reader expected two operands ("everything about my scooter", "that memory completely"); the forget
command ran one target holding both and recorded its result under it. Every later read of the forgotten list in the
profile was then refused ("owner erasure is incomplete"), so a recall after a Lithuanian forget could no longer tell it
had nothing on record.
"""
from __future__ import annotations

import json
import random

import pytest

from tests.test_forget_turn_admission_r9 import _isolated_admission_database
from tests.test_question_date_time_leg_20261002 import _hash_backend, _ingest, _profile

__all__ = ["_hash_backend", "_isolated_admission_database"]


@pytest.mark.usefixtures("_hash_backend")
@pytest.mark.parametrize("seed", range(3))
def test_a_two_operand_forget_closes_its_admission(tmp_path, seed):
    from core.agent_runtime.agent import VoolAgent
    from core.memory import revocation

    rng = random.Random(f"two-operand-{seed}")
    code = rng.choice(["MINT84", "KESTREL19", "OAK552"])
    tail = rng.choice(["erase that memory completely", "wipe it", "delete that from memory pls"])
    text = f"forget my locker code {code}, {tail}"
    home = _profile(tmp_path)
    chat = f"two-operand-{seed}"
    _ingest(home, chat, [(1760000000, f"My locker code is {code}.")])
    agent = VoolAgent(backend_name="test-backend", device="admission-fixture", persona_id="default")
    context = {"chat_id": chat, "runtime_home": str(home), "client_turn_id": f"two-operand-{seed}"}
    agent.run_once(text, session_id_override=chat, source_context=context)
    rows = json.loads((home / ".forget-authority" / "ledger.json").read_text())["operations"]
    admissions = [row for row in rows.values() if row.get("kind") == "admission"]
    assert admissions and all(row["status"] == "complete" for row in admissions), admissions
    # The forgotten list reads again, across every chat.
    assert code.lower() in " ".join(revocation.revoked_tokens(None, home))


@pytest.mark.usefixtures("_hash_backend")
@pytest.mark.parametrize("seed", range(3))
def test_a_forget_of_everything_about_a_thing_kept_in_this_chat_erases_the_saved_fact(tmp_path, seed):
    """Mac run10 (eb8c907cc): "Remember that my telescope is navy and lives in the garage." then, in the same chat,
    "forget everything about my telescope, erase that memory completely" answered "Forget applied", but the saved row
    "my telescope is navy and lives in the garage." stayed active: the forget erased by the person's sentence as said
    ("Remember that my telescope ..."), which the saved fact does not carry."""
    from core.agent_runtime.agent import VoolAgent
    from core.memory import entries
    from core.memory.files import memory_entries_path

    rng = random.Random(f"everything-about-{seed}")
    thing, colour, place = rng.choice([("telescope", "navy", "garage"), ("hammock", "teal", "shed"),
                                       ("suitcase", "maroon", "loft")])
    tail = rng.choice([", erase that memory completely", "", ", wipe it pls"])
    home = _profile(tmp_path)
    chat = f"everything-about-{seed}"
    agent = VoolAgent(backend_name="test-backend", device="admission-fixture", persona_id="default")
    context = {"chat_id": chat, "runtime_home": str(home)}
    agent.run_once(f"Remember that my {thing} is {colour} and lives in the {place}.", session_id_override=chat,
                   source_context={**context, "client_turn_id": f"{chat}-1"})
    rows = entries._load_erasure_rows(memory_entries_path())
    assert any(thing in str(row.get("text") or "") for row in rows)
    agent.run_once(f"forget everything about my {thing}{tail}", session_id_override=chat,
                   source_context={**context, "client_turn_id": f"{chat}-2"})
    left = [str(row.get("text")) for row in entries._load_erasure_rows(memory_entries_path())
            if thing in str(row.get("text") or "")
            and str(row.get("status") or "active").lower() not in {"erased", "superseded"}]
    assert not left, left


@pytest.mark.usefixtures("_hash_backend")
def test_a_judged_recall_of_a_fact_told_in_another_language_is_read_before_not_saved(tmp_path, monkeypatch):
    """Mac run10 (eb8c907cc): "remind me what colur my bike is and where i keep it", judged a recall, beside a saved
    "mano dviratis violetinė ..." was answered by the word-matching cross-chat search: "I don't have that saved"."""
    import core.intent_arbiter as intent_arbiter
    from core import plugin_ask
    from core.agent_runtime.agent import VoolAgent

    home = _profile(tmp_path)
    agent = VoolAgent(backend_name="test-backend", device="admission-fixture", persona_id="default")
    context = {"runtime_home": str(home)}
    agent.run_once("Remember that mano dviratis violetinė ir stovi garaže.", session_id_override="keep-lt",
                   source_context={**context, "chat_id": "keep-lt", "client_turn_id": "keep-lt-1"})
    question = "remind me what colur my bike is and where i keep it"
    plugin_ask.forget()
    plugin_ask._record_builtins(question, ("memory_recall",))
    monkeypatch.setattr(plugin_ask, "_judge_turn", lambda text, *_a, **_k: plugin_ask._record_builtins(text, ("memory_recall",)))
    monkeypatch.setattr(intent_arbiter, "arbitrate_recall_facts",
                        lambda _q, held, **_k: tuple(n for n, item in enumerate(held, 1) if "dvirat" in item))
    try:
        result = agent.run_once(question, session_id_override="ask-en",
                                source_context={**context, "chat_id": "ask-en", "client_turn_id": "ask-en-1"})
    finally:
        plugin_ask.forget()
    assert "violet" in str(result.get("response") or ""), result.get("response")
