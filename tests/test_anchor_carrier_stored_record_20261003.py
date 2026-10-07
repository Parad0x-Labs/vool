"""Anchor carriers deliver the stored record, never a body-less shim.

When the delivered winner of a subject slot lacks a question anchor term, the anchor-carrier lane
rides a candidate that carries it. It delivered through a shim with no body or integrity (fork-v5
wiring audit): no speaker label, single clause windows ("I restored a car last year" cut before its
answer clause), and envelope or acknowledgment fragments ("2023\nDave: Wow", "Calvin: Yup",
"Session date: 4:12 pm on 22 February") delivered as anchors.

Laws: the lane resolves the candidate's stored occurrence by key from the candidate pool and
delivers through it (verified body, speaker label, sentence groups); a carrier the temporal contract
excluded still rides value-free, as an exact unit whose window is the prefix's own source slice (so
its speaker label binds and delivery never widens it back to the excluded value); a span that only
acknowledges binds nothing; an envelope-only span is refused at delivery.

All names, places and sentences are synthetic.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import core.context_retrieval as cr
from tests.test_envelope_provenance_not_content_20261003 import _items, _payload
from tests.test_question_date_time_leg_20261002 import (
    _hash_backend,
    _ingest,
    _profile,
)
from tests.test_time_leg_follows_allowance_20261003 import _ts

# ───────────────────────── anchor carriers deliver the stored record ──────

_LIFT_LADDER = [
    ("2026-01-10T09:00:00", "Session date: 9:00 am on 10 January, 2026\n"
     "Brannagh: Lovely morning on the slopes. The day pass at the ski lift cost 30 marks for years."),
    ("2026-03-05T09:00:00", "Session date: 9:00 am on 5 March, 2026\nBrannagh: It rose to 34 marks in March."),
    ("2026-10-02T09:00:00", "Session date: 9:00 am on 2 October, 2026\nBrannagh: And to 38 marks from October."),
]


def _as_of_capsule(profile: Path, chat: str, question: str, as_of: str) -> str:
    from core.context_namespace import ensure_chat_namespace
    from core.memory.entries import resolve_memory_access_policy

    ensure_chat_namespace(chat, grant_current_receipts=False)
    policy = resolve_memory_access_policy(chat_id=chat)
    messages = cr.inject_retrieved(
        chat, question, [{"role": "user", "content": question}],
        access_policy=policy,
        source_context={"chat_id": chat, "runtime_home": str(profile), "question_as_of": as_of},
        env={"VOOL_CONTEXT_CAPSULE_V2": "1"},
    )
    return next((str(m.get("content") or "") for m in messages
                 if "<retrieved_context>" in str(m.get("content") or "")), "")


@pytest.mark.usefixtures("_hash_backend")
@pytest.mark.parametrize("question", [
    "As of mid August 2026, what did the day pass cost?",
    "As of mid August 2026, what did the day pass cost? Give the session date too.",
    "as of mid august 2026 how much was the day pass, with the date of that chat",
    "As of mid August 2026, what was the price of a day pass? Answer with the session date.",
])
def test_anchor_carrier_rides_as_the_stored_record_value_free(tmp_path, question):
    profile = _profile(tmp_path)
    _ingest(profile, "lift", [(_ts(t), text) for t, text in _LIFT_LADDER])
    capsule = _as_of_capsule(profile, "lift", question, "2026-08-15T00:00:00")
    items = _items(capsule)
    assert any("34 marks" in item for item in items), capsule
    # the superseded carrier binds the subject, attributed to its speaker,
    # without its excluded value and without being widened back to it
    carrier = [item for item in items if "day pass" in item.lower()]
    assert carrier, capsule
    assert all('reported source prefix "Brannagh:"' in item for item in carrier), capsule
    assert "30 marks" not in capsule and "38 marks" not in capsule, capsule
    # no envelope-only item or envelope fragment anywhere
    assert not [item for item in items if not any(ch.isalpha() for ch in _payload(item))], capsule
    assert not [item for item in items if _payload(item).strip().startswith("2026\n")], capsule


@pytest.mark.parametrize("span", [
    "Calvin: Yup",
    "Dave: Wow",
    "Dave: Wow, nice!",
    "Hey Sam!",
    "Thanks, Dave! Take care.",
    "Thanks, bye!",
    "Orla: Haha yeah, cheers",
    "ok sure thanks",
    "Mira: Oh wow, that's awesome!",
])
def test_backchannel_spans_bind_nothing(span):
    assert cr._span_is_bare_acknowledgment(span)


@pytest.mark.parametrize("span", [
    # a negation is content, not a receipt: the receipt register's "not"
    # must not refuse an answer-bearing carrier
    "Ines: We're not engaged yet but we've been together for four years.",
    "Sam: I sold it to a collector.",
    "Noted, the ferry leaves at 9.",
    "Dave: Wow, the new carburettor finally fits!",
    "yeah the kiln is fixed",
    "Thanks, Dave! Boston was great.",
    "",
])
def test_content_spans_are_not_acknowledgments(span):
    assert not cr._span_is_bare_acknowledgment(span)
