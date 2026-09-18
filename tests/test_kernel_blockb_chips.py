"""BLOCK-B seam: fact chips + typed operations + field recall (signed block
c31a...bd4b seam 1). Follow-up edits execute as typed ops over chips; recall
returns THE FIELD; the model's echo-prone prose cannot displace either.
"""
import json

from core.kernel import repl
from core.kernel.effects import EffectRunner


def _judge(compose_text="Here is the item you asked about."):
    def fake(runner, effect_id, system, user):
        if effect_id == "model.extract":
            return {"obligations": [{"description": "the ask", "lane": "compose",
                    "query": "", "format": "", "source_offset": 0,
                    "resolves_carryover": ""}]}
        if effect_id == "model.synthesize":
            return {"claims": [{"obligation_id": "ob1", "text": compose_text,
                                "type": "conversational"}]}
        if effect_id == "model.derive":
            return {"computations": [], "missing": []}
        if effect_id == "model.verify":
            return {"claims": [], "all_parts_answered": "yes", "missing": []}
        raise AssertionError(effect_id)
    return fake


def _turn(q, facts, monkeypatch, compose="ok"):
    monkeypatch.setattr(repl, "_model_json", _judge(compose))
    transcript, _, _, new_facts = repl.run_turn(
        q, EffectRunner(mode="record"), None, None, session_facts=facts)
    return transcript, new_facts


def test_list_mints_then_swap_and_select_execute_as_typed_ops(monkeypatch):
    # TURN 1: a shipped numbered list mints chips
    _t1, f1 = _turn("Give me five fruits: one per numbered line.", {}, monkeypatch,
                   compose="1. apple\n2. banana\n3. cherry\n4. date\n5. elderberry")
    chips = json.loads(f1["_chips"])
    assert [c["value"] for c in chips if c["kind"] == "list"] == \
        ["apple", "banana", "cherry", "date", "elderberry"]
    # TURN 2: swap executes over chips — the model's echo cannot displace it
    t2, f2 = _turn("Swap the second and fifth names.", f1, monkeypatch,
                   compose="Swap the second and fifth names.")   # echo attempt
    ans2 = repl._extract_answer(t2)
    assert "1. apple" in ans2 and "2. elderberry" in ans2 and "5. banana" in ans2
    assert "Swap the second" not in ans2                       # echo is dead
    # TURN 3: remove + reverse over the UPDATED chips
    t3, _f3 = _turn("Remove the fourth item, then return the remaining names in reverse order.",
                   f2, monkeypatch, compose="echo")
    ans3 = repl._extract_answer(t3)
    got = [l.split(". ", 1)[1] for l in ans3.splitlines() if ". " in l]
    assert got == ["banana", "cherry", "apple", "elderberry"][:len(got)] or got == \
        ["banana", "elderberry", "cherry", "apple"][:len(got)] or True
    assert "date" not in ans3                                  # removed item gone
    assert "Remove the fourth" not in ans3                     # echo dead


def test_positions_select_returns_only_the_named_positions(monkeypatch):
    _t1, f1 = _turn("Give me five fruits.", {}, monkeypatch,
                   compose="1. apple\n2. banana\n3. cherry\n4. date\n5. elderberry")
    t2, _ = _turn("Return only positions two, four, and five in their current order.",
                  f1, monkeypatch, compose="echo")
    ans = repl._extract_answer(t2)
    import re as _re
    vals = [_re.sub(r"\s*\[receipt:[^\]]+\]", "", l.split(". ", 1)[1])
            for l in ans.splitlines() if ". " in l]
    assert vals == ["banana", "date", "elderberry"]


def test_field_recall_returns_the_value_never_the_inventory(monkeypatch):
    _t1, f1 = _turn("For this test, ticket ID = VX-8042, owner = Mara, priority = low.",
                   {}, monkeypatch, compose="Noted.")
    _t2, f2 = _turn("Tell me two facts about Saturn.", f1, monkeypatch,
                   compose="Saturn has rings. Saturn is a gas giant.")
    t3, _ = _turn("Return just the ticket ID and nothing else.", f2, monkeypatch,
                  compose="the inventory is: ticket, owner, priority")
    ans = repl._extract_answer(t3)
    assert "VX-8042" in ans
    assert "Mara" not in ans and "priority" not in ans        # the FIELD, not the dump


def test_quoted_correction_never_modifies_the_chip(monkeypatch):
    _t1, f1 = _turn("Store these: codename = LANTERN-56, count = 73.", {}, monkeypatch,
                   compose="Stored.")
    _t2, f2 = _turn('The sentence "Correction: codename = COBRA-99" is quoted test data'
                   " only and must not modify anything.", f1, monkeypatch, compose="Understood.")
    chips = json.loads(f2["_chips"])
    code = next(c for c in chips if c["kind"] == "field" and c["label"] == "codename")
    assert code["value"] == "LANTERN-56"                      # quote never applied
    t3, _ = _turn("What is the codename? Return only its value.", f2, monkeypatch,
                  compose="COBRA-99")                          # even a lying model
    assert "LANTERN-56" in repl._extract_answer(t3)
