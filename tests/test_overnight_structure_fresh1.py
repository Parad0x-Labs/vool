"""Post-freeze structure acceptance: independent sources and wording.

Frozen before execution against 775bb7e8. This is evidence delivery, not
live model answer accuracy. Facts and expected relationships are authored.
"""
import pytest

from core.context_retrieval import _evidence_clause_windows
from tests.test_overnight_source_structure import _recall, _store, source_env

WORLDS = [
    (
        "The museum tour briefing has several routes.\n"
        "Our museum tour briefing helps visitors choose routes.\n"
        "Visitors can review the museum tour briefing before choosing routes.\n"
        "+ Porcelain gallery: 17 displays\n+ Textile gallery: 8 displays\n",
        "In the museum tour briefing we discussed earlier, how many displays "
        "were in the porcelain gallery?",
        ["Porcelain gallery: 17 displays"],
    ),
    (
        "The reef survey packing inventory contains useful equipment.\n"
        "Useful equipment from our reef survey packing inventory is listed below.\n"
        "The reef survey packing inventory can help with useful equipment.\n"
        "- **Marker buoys** — 23\n- Spare fins — 5\n",
        "Looking back at our reef survey packing inventory: what quantity "
        "of marker buoys did you list?",
        ["**Marker buoys** — 23"],
    ),
    (
        "The herb greenhouse plan explains its watering assignments.\n"
        "The herb greenhouse plan has watering assignments for each bed.\n"
        "Watering assignments in the herb greenhouse plan are easy to change.\n"
        "Bed | Dawn | Dusk\n--- | --- | ---\n"
        "Sage | Mira | Oren\nMint | Oren | Mira\nThyme | Mira | Oren\n",
        "From the herb greenhouse plan, who watered the mint bed at dusk?",
        ["Bed | Dawn | Dusk", "Mint | Oren | Mira"],
    ),
    (
        "The ferry inspection roster lists certification assignments.\n"
        "Certification assignments for the ferry inspection roster can change.\n"
        "Here are the ferry inspection roster certification assignments.\n"
        "| Inspector | Hull | Radio |\n| --- | --- | --- |\n"
        + "".join(f"| Inspector-{i:02d} | Pending | Cleared |\n" for i in range(40))
        + "| Zelia | Cleared | Pending |\n",
        "What radio certification did Zelia have in the ferry inspection roster?",
        ["| Inspector | Hull | Radio |", "| Zelia | Cleared | Pending |"],
    ),
]


@pytest.mark.parametrize("body,question,required", WORLDS)
def test_new_source_relationship_reaches_real_capsule(source_env, body, question, required):
    _store(source_env, "fresh-structure", "Please prepare this reference for later.", body)
    context = _recall(source_env, "fresh-structure", question)
    for clause in required:
        assert clause in context, context
    assert "assistant said" in context, context
    assert not any("user said" in line and any(x in line for x in required)
                   for line in context.splitlines()), context


@pytest.mark.parametrize("body,question,required", WORLDS)
def test_offsets_are_original_source_bytes(body, question, required):
    windows = _evidence_clause_windows(question, body)
    for window in windows:
        assert body[window["start"]:window["end"]] == window["text"]
    for clause in required:
        assert any(clause in window["text"] for window in windows), windows


def test_new_foreign_roster_is_excluded(source_env):
    body, question, required = WORLDS[3]
    _store(source_env, "other-roster", "Create the roster.", body)
    _store(source_env, "own-roster", "My garden fence is white.", "Noted.")
    context = _recall(source_env, "own-roster", question)
    assert required[1] not in context, context


def test_missing_item_does_not_acquire_neighbor_value():
    body = "Supplies:\n* Chalk — 4 boxes\n* Charcoal — quantity undecided\n* Clay — 19 bags\n"
    question = "How much charcoal was on the supplies list from our earlier chat?"
    windows = _evidence_clause_windows(question, body)
    bound = [w for w in windows if w.get("structure_bound")]
    assert bound and bound[0]["text"].strip() == "* Charcoal — quantity undecided", windows
