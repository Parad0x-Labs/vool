""""Correction: my dentist is Dr Abara" replaces the saved dentist, the way "Update: ..." does.

Measured on the real app (release walk at 36033c05, local model): "remember that my dentist is Dr Okafor", then
"Correction: my dentist is Dr Abara" was answered as if it was taken, wrote no memory row, and a new chat still
answered Dr Okafor. The same with "my bakery is Lund's" and "correction, my bakery is Rossi's". The correction command
takes only a value it can type (a code, an identifier, a date), the everyday update did not take a "Correction:" lead,
and "correction" also read as a retraction word, so the turn was nobody's and went to the model, which saves nothing.

A "Correction:" lead is now an update lead; the retraction words are read in what follows it. The controls: a
correction that retracts ("Correction: never mind") changes nothing saved, and a coded correction keeps its command.
"""
from __future__ import annotations

import os

from tests.semantic_phase0._fixtures import (
    block_outbound_network,
    keep_the_checkout_clean,
    make_agent_module,
    pin_the_signing_key_passphrase,
    pin_volatile_machine_observations,
    reseal_network_after_function_fixtures,
)
from tests.semantic_phase0.test_a_forget_counts_one_fact_once_v08 import _say


def _rows() -> list[tuple[str, str]]:
    from core.memory.entries import _load_jsonl, memory_entries_path

    return [(str(row.get("status") or ""), str(row.get("fact") or row.get("text") or ""))
            for row in _load_jsonl(memory_entries_path())]


def test_a_correction_lead_replaces_the_saved_value(make_agent_module, monkeypatch):
    monkeypatch.setitem(os.environ, "VOOL_SEMANTIC_REACH", "0")
    agent = make_agent_module()
    _say(agent, "remember that my dentist is Dr Okafor", "correct-a")
    _say(agent, "remember that my bakery is Lund's", "correct-a")
    dentist = _say(agent, "Correction: my dentist is Dr Abara", "correct-b")
    bakery = _say(agent, "correction, my bakery is Rossi's", "correct-b")
    active = [fact for status, fact in _rows() if status == "active"]
    assert any("Abara" in fact for fact in active), (dentist, _rows())
    assert not any("Okafor" in fact for fact in active), (dentist, _rows())
    assert any("Rossi" in fact for fact in active), (bakery, _rows())
    assert not any("Lund" in fact for fact in active), (bakery, _rows())
    assert "Abara" in dentist and "Rossi" in bakery, (dentist, bakery)


def test_a_correction_that_retracts_changes_nothing_saved(make_agent_module, monkeypatch):
    monkeypatch.setitem(os.environ, "VOOL_SEMANTIC_REACH", "0")
    agent = make_agent_module()
    _say(agent, "remember that my dentist is Dr Okafor", "retract-a")
    _say(agent, "Correction: never mind, I meant nothing", "retract-b")
    active = [fact for status, fact in _rows() if status == "active"]
    assert any("Okafor" in fact for fact in active), _rows()


def test_a_coded_correction_keeps_its_command():
    from core.persistent_memory import _correction_command_takes

    assert _correction_command_takes("Correction: the deploy key is KEY-7781")
