from __future__ import annotations

import pytest

from core.context_retrieval import _ASSISTANT_OUTPUT_ASK_RE, _evidence_clause_windows, _query_shape


@pytest.mark.parametrize("query, body, values", [
    ("How many distinct checks did your breeding-tank checklist prescribe, and what were they?", "Breeding-tank checks:\n1. Confirm the inlet screen is clear.\n2. Verify the air stone is bubbling.\n3. Read the salinity strip.\n4. Reseat the lid latch.", ("inlet screen", "air stone", "salinity strip", "lid latch")),
    ("How many different components were in the festival microphone kit you listed, and which components made up that kit?", "Festival microphone kit:\n- cardioid capsule\n- foam shield\n- flexible gooseneck\n- locking cable\n- spare clamp", ("cardioid capsule", "foam shield", "flexible gooseneck", "locking cable", "spare clamp")),
])
def test_count_requires_complete_source_set(query, body, values):
    assert _query_shape(query) == "enumeration"
    assert _ASSISTANT_OUTPUT_ASK_RE.search(query)
    windows = _evidence_clause_windows(query, body)
    delivered = "\n".join(w["text"] for w in windows)
    assert all(value in delivered for value in values)
    for w in windows:
        assert body[w["start"]:w["end"]] == w["text"]


def test_unrelated_heading_cannot_bind_collection():
    query = "How many components were in the microphone kit you listed?"
    body = "Cabinet inventory:\n- spare clamp\n- foam shield\n- locking cable"
    assert not _evidence_clause_windows(query, body)


def test_named_item_lookup_does_not_pack_every_sibling():
    body = "Stage kit:\n1. Capsule: 2 grams.\n2. Shield: 5 grams.\n3. Clamp: 17 grams."
    windows = _evidence_clause_windows("What mass did you specify for the Clamp?", body)
    assert any("17 grams" in w["text"] for w in windows)
    assert not any("2 grams" in w["text"] for w in windows)


from tests.test_overnight_source_structure import _recall, _store, source_env


@pytest.mark.parametrize("query, body", [
    ("How many checks did your sailing-rig checklist contain, and what were they?", "Sailing-rig checks:\n1. Inspect the mast foot.\n2. Inspect the boom pin.\n3. Tighten the forestay.\n4. Test the cleat.\n5. Secure the halyard."),
    ("How many components were in the sound booth kit you listed?", "Sound booth kit:\n- cable tester\n- spare attenuator\n- headband microphone\n- monitor speaker"),
])
def test_entire_bound_set_reaches_serialized_capsule(source_env, query, body):
    _store(source_env, "compact-source", "Please write this checklist for later.", body)
    context = _recall(source_env, "compact-source", query)
    assert body in context, context
    assert "assistant said" in context


def test_reconstruction_with_total_is_complete_set():
    query = "Can you reconstruct the instrument-cleaning checklist you wrote and tell me its total number of checkpoints?"
    body = "Instrument-cleaning checkpoints:\n1. Disconnect the mains lead.\n2. Open the dust flap.\n3. Brush the intake grille.\n4. Wipe the outer casing.\n5. Refit the dust flap.\n6. Confirm the mains lead remains disconnected."
    assert _query_shape(query) == "enumeration"
    assert any(body == w["text"] for w in _evidence_clause_windows(query, body))
