"""A Contacts demand releases the turn from the tools-less chat lane, the way wallet and email demands do.

Measured on the served Contacts journey (stage green-10, 2026-09-15): "Save Alex Chen as a contact: ..." classified
`integration_orchestration`, stayed in the tools-less lane, and the certified model was offered no tool. What executes: the
production requirement authority (core.execution_requirements.classify_requirements), which the lane policy and the tool
gate both consult. No model, daemon or network.
"""
from __future__ import annotations

import pytest

from core.execution_requirements import classify_requirements


@pytest.mark.parametrize("text", [
    "Save Alex Chen as a contact: work email alex.chen@example.test, Telegram alexchen_kiln.",
    "What is Alex Chen's Telegram handle?",
    "Store Zoë Ng in my address book.",
    "create a contact for me pls, name it Test1 and ad solana address to that contact 9vDnXsPonRJa7yAmvwRGMAdxt8W13Qbm7HZuvauM3Ya3",
    "Please add a new contact named Jamie, email jamie@example.test.",
])
def test_a_contacts_demand_requires_the_contacts_tools(text: str) -> None:
    requirements = classify_requirements(text, task_class="integration_orchestration")
    assert requirements.tools_required and "contacts" in requirements.allowed_toolsets, requirements
    assert "contacts_action_request" in requirements.reason_codes


@pytest.mark.parametrize("text", [
    "Write a contact form for my pottery website.",
    "Create a contact form for my pottery website.",
    "Create a contact page for my site.",
    "Explain how to create a contact with a protected wallet address.",
    "What is the capital of France?",
])
def test_controls_text_that_names_no_saved_contact_has_no_contacts_requirement(text: str) -> None:
    assert "contacts_action_request" not in classify_requirements(text, task_class="chat_conversation").reason_codes


def test_control_a_tool_prohibition_still_wins() -> None:
    requirements = classify_requirements("Do not use any tools: what is Alex Chen's Telegram handle?", task_class="chat_conversation")
    assert "contacts_action_request" not in requirements.reason_codes, requirements
