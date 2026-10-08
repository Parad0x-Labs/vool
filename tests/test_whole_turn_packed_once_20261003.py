"""A whole turn is packed once (core/context_retrieval.py, the evidence delivery path).

A short speaker-labelled turn is delivered whole (its question sentences and caption belong to it).
Every window and every sibling sentence of that turn widened to the SAME whole turn, and each
widening packed another copy: callers checked only the narrow span against the delivered text.
The copies spent the evidence char budget, the final collapse then removed them, and later lanes
had been refused for room the capsule never used (measured, c1-recall zero-spend replay: one
capsule built 108 evidence lines of which 35 were distinct; median capsule 3.9k of 8.2k tokens).

Law: the identity of what will actually be packed is checked after widening - a whole turn already
delivered is not delivered again, on any lane.

All names, places and sentences are invented for this contract.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tests.test_question_date_time_leg_20261002 import (
    _hash_backend,
    _ingest,
    _profile,
)
from tests.test_time_leg_follows_allowance_20261003 import _wide_capsule

UTC = timezone.utc
_MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September"]


def _ts(text: str) -> float:
    return datetime.fromisoformat(text).replace(tzinfo=UTC).timestamp()


def _sessions(per_session: list[list[str]]) -> list[tuple[float, str]]:
    turns: list[tuple[float, str]] = []
    for index, session in enumerate(per_session):
        stamp = f"2026-{index + 1:02d}-1{index}T10:00:00"
        header = f"10:00 am on 1{index} {_MONTHS[index]}, 2026"
        turns += [(_ts(stamp), f"Session date: {header}\n{turn}") for turn in session]
    return turns


_INGESTED: dict[str, Path] = {}


def _home_with(tmp_path: Path, chat: str, store: list[tuple[float, str]]) -> Path:
    """A fresh profile holding *store*: ingested once per distinct store through the real
    conversation-event path, then cloned per test (capsule reads move node access counters, so
    tests never share one live store)."""
    import hashlib
    import shutil
    import tempfile

    key = hashlib.sha256(repr((chat, store)).encode()).hexdigest()[:16]
    if key not in _INGESTED or not _INGESTED[key].exists():
        seed_root = Path(tempfile.mkdtemp(prefix="dialogue-recall-seed-", dir=str(tmp_path.parent)))
        seed = _profile(seed_root)
        _ingest(seed, chat, store)
        _INGESTED[key] = seed
    profile = tmp_path / "home"
    shutil.copytree(_INGESTED[key], profile)
    return _profile(tmp_path)


_FILLER_A = ["The bakery reopened.", "My cousin visits next month.", "I repotted the fern.",
             "The ferry was late."]
_FILLER_B = ["The tram was late again.", "I fixed the leaking tap myself.",
             "My brother started a podcast.", "The gym raised its fees."]


def _studio_store() -> list[tuple[float, str]]:
    sessions = []
    for i in range(5):
        turns = []
        for j in range(2):
            turns += [f"Lena: {_FILLER_A[(i + j) % 4]}", f"Oskar: {_FILLER_B[(i + j) % 4]}"]
        if i == 0:
            turns.insert(1, "Oskar: Pottery class tonight, wish me luck!")
        if i == 1:
            turns.insert(2, "Oskar: Lunch with the cousin ran long, we walked the whole harbour "
                            "front twice. After that the pottery class went late and both mugs came "
                            "out lopsided. Fried fish by the old pier finished the day off nicely.")
        if i == 3:
            turns.insert(2, "Oskar: The teacher at my pottery class says my glaze work is "
                            "improving a lot.")
        if i == 4:
            turns.insert(2, "Oskar: The broken kiln at the studio finally got repaired on Friday.")
        sessions.append(turns)
    return _sessions(sessions)


@pytest.mark.usefixtures("_hash_backend")
@pytest.mark.parametrize("target", [200, 240, 320, 2048])
@pytest.mark.parametrize("question", [
    "What did Oskar say about the lopsided mugs?",
    "what did oskar say abt the lopsided mugs",
    "Tell me what Oskar mentioned about the lopsided mugs and the broken kiln.",
    "lopsided mugs oskar??",
    "Which mugs came out lopsided for Oskar?",
])
def test_a_whole_turn_is_packed_once(tmp_path, question, target):
    profile = _home_with(tmp_path, "studio", _studio_store())
    capsule, telemetry = _wide_capsule(profile, "studio", question, target_tokens=target)
    delivered = Counter(
        (ref.get("occurrence_id"), str(ref.get("line")))
        for ref in telemetry.get("evidence_refs") or [] if ref.get("delivered"))
    assert delivered and max(delivered.values()) == 1, delivered.most_common(2)
    whole = [str(ref.get("line")) for ref in telemetry.get("evidence_refs") or []
             if ref.get("delivered") and "lopsided" in str(ref.get("line"))]
    assert len(whole) <= 1, whole
    assert capsule.count("both mugs came out lopsided") == 1, capsule


