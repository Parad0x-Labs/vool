from pathlib import Path

import pytest

from core.agent_runtime.grounded_mode import forbids_inference
from core.execution_requirements import requirements_for

PROMPT = (Path(__file__).parent / 'fixtures/contact_wallet_design_review.txt').read_text()


def test_original_design_review_does_not_require_live_retrieval():
    requirement = requirements_for(PROMPT)
    assert not requirement.current_information_required
    assert not requirement.tools_required
    assert requirement.inference_allowed


@pytest.mark.parametrize('text', [
    'Propose a plugin boundary. Do not invent unnecessary infrastructure.',
    'Design a small queue. Do not invent extra services or databases.',
    'Review this approach. Do not invent new requirements.',
    'Explain this quoted rule: "Do not invent facts".',
])
def test_design_scope_and_quoted_rules_are_not_bans_on_reasoning(text):
    assert not forbids_inference(text)


@pytest.mark.parametrize('text', [
    'Do not invent.',
    'Do not invent facts or sources.',
    "Don't invent any figures.",
    'Do not guess the revenue.',
    'No speculation please.',
])
def test_real_factual_constraints_still_bind(text):
    assert forbids_inference(text)


def test_a_design_request_with_an_independent_live_question_still_requires_evidence():
    requirement = requirements_for(PROMPT + '\nAlso look up the latest Solana release notes with sources.')
    assert requirement.current_information_required
