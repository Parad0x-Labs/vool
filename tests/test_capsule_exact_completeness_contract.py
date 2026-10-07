"""Scalar exact shortcuts require whole-request applicability. Contributor: sls_0x."""
from __future__ import annotations

import pytest

from core import context_retrieval as cr

CHAT = "scalar-completeness"


def telemetry():
    return {
        "capsule_mode": "distilled",
        "selected_facts": [
            "- exact code: M-08",
            "- current region: eu-west-2",
            "- recalled identifier: NODE-817",
            "- latest spend cap: 0.003 SOL",
        ],
        "source_session_ids": [cr._session_scope_key(CHAT)],
    }


@pytest.mark.parametrize("question,scalar", [
    ("On 2025-07-08, before the later move and rename, what storage code, location, and sleeve did the copper astrolabe have?", "M-08"),
    ("What is the storage code and location?", "M-08"),
    ("What location and storage code did the object have?", "M-08"),
    ("What is the storage code? Explain why it was selected.", "M-08"),
    ("What is the storage code, and who chose it?", "M-08"),
    ("Which current region and spend cap are recorded?", "eu-west-2"),
])
def test_scalar_cannot_fulfill_coordinated_fields_or_followup(question, scalar):
    assert cr.capsule_exact_response(question, telemetry(), session_id=CHAT,
                                     active_mission_slots=[]) == ""
    assert not cr.validate_capsule_exact_response(scalar, question, session_id=CHAT,
                                                  active_mission_slots=[])


@pytest.mark.parametrize("question,scalar", [
    ("What is the storage code?", "M-08"),
    ("On 2025-07-08, before the later move and rename, what storage code did the copper astrolabe have?", "M-08"),
    ("What code did I set for heating and cooling systems?", "M-08"),
    ('The quoted example is "What code and location did it have?". What is the storage code?', "M-08"),
    ("What operator node ID is stored?", "NODE-817"),
    ("Which deployment region is correct?", "eu-west-2"),
    ("What is the latest launch cap?", "0.003 SOL"),
])
def test_scalar_shortcuts_keep_descriptive_conjunctions_and_simple_recall(question, scalar):
    assert cr.capsule_exact_response(question, telemetry(), session_id=CHAT,
                                     active_mission_slots=[]) == scalar
    assert cr.validate_capsule_exact_response(scalar, question, session_id=CHAT,
                                               active_mission_slots=[])


def test_multi_field_recall_keeps_exact_current_session_filter_authority():
    query = "What storage code, location, and sleeve did the object have?"
    assert cr.exact_recall_requires_current_session(query)
    assert cr.is_capsule_exact_recall_query(query)


def test_post_model_api_preserves_complete_answer_instead_of_partial_override(monkeypatch, tmp_path):
    from core.web.api.runtime import RuntimeServices, run_agent
    from tests.test_capsule_override_safety import _FakeAgent

    class CompleteAgent(_FakeAgent):
        def run_once(self, user_text, **kwargs):
            super().run_once(user_text, **kwargs)
            return {"response": "RCPT-9182 is in the north drawer in a felt sleeve.", "confidence": .8}

    agent = CompleteAgent(session_id=CHAT)
    runtime = RuntimeServices(agent=agent, runtime_home=str(tmp_path))
    monkeypatch.setattr("core.web.api.runtime._memory_recall_response", lambda *args, **kwargs: None)
    monkeypatch.setattr("core.web.api.runtime.schedule_memory_extraction", lambda *args, **kwargs: None)
    result = run_agent(runtime, "What receipt code, location, and sleeve are recorded?",
                       session_id=CHAT,
                       source_context={"surface": "api", "platform": "api", "allow_remote_fetch": False},
                       workspace_root_provider=lambda: str(tmp_path))
    assert result["response"] == "RCPT-9182 is in the north drawer in a felt sleeve."
    assert "response_control" not in result
