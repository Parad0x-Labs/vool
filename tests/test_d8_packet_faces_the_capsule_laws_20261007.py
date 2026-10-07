"""The receipts packet faces the capsule's laws (D8-E, 2026-10-07): a row the turn refused never rides the packet.

compile_packet reads every receipt of the chat before the capsule's own laws run, so with the kernel on a record the
absence gate (item 17), the as-of law (item 15) or a retraction (port gate fact4) refused still rendered as a typed
row. filter_packet drops the rows whose occurrence the turn refused, keeps hop and derived lines, and recomputes the
completeness line; _packet_refusals reads the decisions the turn already made. Every record and question below was
written for this file, except the James and glaze cases the port's runs exposed.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import core.context_retrieval as cr
from core.evidence_compiler import Packet, filter_packet, obligations
from tests.test_question_date_time_leg_20261002 import _capsule, _hash_backend, _ingest, _profile  # noqa: F401

ON = {"VOOL_EVIDENCE_KERNEL": "1", "VOOL_MEMORY_RECEIPTS": "1", "VOOL_EVIDENCE_COMPILER": "1", "VOOL_EVIDENCE_VERIFY": "1"}


@pytest.fixture()
def _kernel_on(monkeypatch):
    for k, v in ON.items():
        monkeypatch.setenv(k, v)


def _packet(block: str) -> str:
    if "Evidence receipts (" not in block:
        return ""
    rest = block.split("Evidence receipts (", 1)[1]
    return rest.split(cr._TURN_LANE_HEADER, 1)[0] if cr._TURN_LANE_HEADER in rest else rest


def _store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, chat: str, turns) -> Path:
    from core.context_namespace import ensure_chat_namespace
    from core.memory.entries import resolve_memory_access_policy

    profile = tmp_path / "home"; profile.mkdir(parents=True, exist_ok=True)
    for name in ("NULLA_HOME", "VOOL_HOME"):
        monkeypatch.setenv(name, str(profile))
    for name in ("NULLA_WORKSPACE_ROOT", "VOOL_WORKSPACE_ROOT"):
        monkeypatch.setenv(name, str(profile / "workspace"))
    ensure_chat_namespace(chat, grant_current_receipts=False)
    policy = resolve_memory_access_policy(chat_id=chat)
    for stated, body in turns:
        receipt = cr.store_turn(chat, body, "Noted.", access_policy=policy,
                                source_context={"runtime_home": str(profile), "statement_at": stated})
        assert receipt["status"] in ("stored", "retained"), receipt
    return profile


def _ask(profile: Path, chat: str, question: str) -> tuple[str, dict]:
    from core.memory.entries import resolve_memory_access_policy

    messages = cr.inject_retrieved(chat, question, [{"role": "user", "content": question}],
                                   access_policy=resolve_memory_access_policy(chat_id=chat),
                                   source_context={"runtime_home": str(profile), "chat_id": chat},
                                   env={"NULLA_CONTEXT_CAPSULE_V2": "1", "VOOL_CONTEXT_CAPSULE_V2": "1", **ON})
    return "\n".join(m["content"] for m in messages if m["role"] == "system"), cr.get_last_retrieval_telemetry()


@pytest.mark.usefixtures("_hash_backend", "_kernel_on")
def test_item17_the_absence_gate_row_leaves_the_packet(tmp_path, monkeypatch):
    profile = _store(tmp_path, monkeypatch, "class-chat", [
        (1651392000.0, "Session date: 2022/05/01\nJames: Yes, two days ago I signed up for a cooking class. At only $10 per class, it's very cheap!"),
        (1704614400.0, "Session date: 2024/01/07\nTim: Great news, I'm finally in the study abroad program. Next month I'm off to Ireland."),
    ])
    block, telemetry = _ask(profile, "class-chat", "How much does James pay per dance class?")
    assert "$10" not in _packet(block) and "cooking" not in _packet(block), block
    refused = (telemetry.get("evidence_compiler") or {}).get("refused_rows") or []
    assert any(r.get("reason") == "absence-gate" for r in refused), telemetry.get("evidence_compiler")


@pytest.mark.usefixtures("_hash_backend", "_kernel_on")
def test_item15_the_as_of_refused_row_leaves_the_packet(tmp_path, monkeypatch):
    profile = _profile(tmp_path)
    _ingest(profile, "allot", [
        ("2025-02-10T09:00:00", "The allotment plot number is fourteen."),
        ("2025-05-12T09:00:00", "I planted the new raspberry canes along the south fence of the allotment this morning."),
        ("2025-05-12T09:30:00", "The water butt by the shed is leaking again."),
    ])
    for k, v in ON.items():
        monkeypatch.setenv(k, v)
    block, telemetry = _capsule(profile, "allot", "What did I plant along the south fence of the allotment in March 2025?")
    assert "raspberry" not in _packet(block), block
    refused = (telemetry.get("evidence_compiler") or {}).get("refused_rows") or []
    assert any(r.get("reason") == "future-relative-to-as-of" for r in refused), telemetry.get("evidence_compiler")


@pytest.mark.usefixtures("_hash_backend", "_kernel_on")
def test_fact4_a_retracted_value_leaves_the_packet(tmp_path, monkeypatch):
    profile = _store(tmp_path, monkeypatch, "glaze-chat", [
        (1770105600.0, "The glaze workshop is on Sunday at 11 am."),
        (1770192000.0, "Actually, scratch that — the glaze workshop is off."),
    ])
    block, telemetry = _ask(profile, "glaze-chat", "When is the glaze workshop?")
    assert "11 am" not in _packet(block), block
    refused = (telemetry.get("evidence_compiler") or {}).get("refused_rows") or []
    assert any(r.get("reason") == "withdrawn" for r in refused), telemetry.get("evidence_compiler")


@pytest.mark.usefixtures("_hash_backend", "_kernel_on")
def test_a_row_the_capsule_kept_stays_in_the_packet(tmp_path, monkeypatch):
    profile = _store(tmp_path, monkeypatch, "class-chat", [
        (1651392000.0, "Session date: 2022/05/01\nJames: Yes, two days ago I signed up for a cooking class. At only $10 per class, it's very cheap!"),
    ])
    block, telemetry = _ask(profile, "class-chat", "How much does James pay per cooking class?")
    assert "$10" in _packet(block), block
    assert not ((telemetry.get("evidence_compiler") or {}).get("refused_rows")), telemetry.get("evidence_compiler")


def test_an_empty_refusal_set_returns_the_packet_unchanged():
    packet = Packet(obligation=obligations("What is the booth number?"))
    packet.lines = ['- [2026-01-05] user said: "The booth number is Q-58."  <count: 58 booth>', "- This question needs: x (y). Receipts found: complete."]
    packet.facts = [{"occurrence_id": "o1", "receipt_id": "r1", "role": "user", "sentence": "The booth number is Q-58."}]
    packet.telemetry = {"complete": True, "coverage": {"candidates": 1, "shown": 1, "exhaustive": True}}
    assert filter_packet(packet, {}) is packet
    assert filter_packet(packet, {"other": "absence-gate"}) is packet


def test_a_dropped_row_recomputes_completeness_and_keeps_derived_lines():
    packet = Packet(obligation=obligations("Which was first, the kiln or the wheel?"))
    packet.lines = ['- [2025-01-02] user said: "The kiln came on 2 January."  (event day 2025-01-02, day)',
                    '- [2025-03-04] user said: "The wheel came on 4 March."  (event day 2025-03-04, day)',
                    "- derived: 61 days between them",
                    "- This question needs: dated records (x). Receipts found: complete. Coverage: exhaustive, all 2 matching records are shown."]
    packet.facts = [{"occurrence_id": "o1", "receipt_id": "r1", "role": "user", "event_at": 1735776000.0, "value_type": "event", "sentence": "The kiln came on 2 January."},
                    {"occurrence_id": "o2", "receipt_id": "r2", "role": "user", "event_at": 1741046400.0, "value_type": "event", "sentence": "The wheel came on 4 March."}]
    packet.telemetry = {"complete": True, "coverage": {"candidates": 2, "shown": 2, "exhaustive": True}}
    out = filter_packet(packet, {"o2": "future-relative-to-as-of"})
    assert out is not packet and len(out.facts) == 1 and out.facts[0]["occurrence_id"] == "o1"
    assert out.lines[0].startswith('- [2025-01-02]') and "- derived: 61 days between them" in out.lines
    assert all("wheel" not in line for line in out.lines), out.lines
    assert out.telemetry["refused_rows"] == [{"occurrence_id": "o2", "receipt_id": "r2", "reason": "future-relative-to-as-of"}]
    assert out.telemetry["complete"] is False and "event_date_b" in out.telemetry["missing"]
    assert out.lines[-1].startswith("- This question needs:") and "incomplete: missing event_date_b" in out.lines[-1]
    assert out.telemetry["coverage"]["shown"] == 1
