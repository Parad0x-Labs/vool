"""Every memory-erasure clause requires this turn's registered execution."""

import pytest

from core.memory.command_result import requires_forget_outcome


@pytest.mark.parametrize("prompt", [
    "Remember my name is Lena. Forget MINT-84.",
    "Remember my name is Lena and forget MINT-84.",
    "Remember my name is Lena; erase MINT-84.",
    "Explain the next step. Forget MINT-84.",
    "Explain the next step and forget MINT-84.",
    "Explain the next step but forget MINT-84.",
    "Explain the next step and erase MINT-84.",
    "Explain the next step but erase MINT-84.",
    "Explain the next step and then erase MINT-84.",
    "Tell me a story.\nForget MINT-84.",
    "Forget my name. Forget MINT-84.",
])
def test_each_erasure_clause_requires_a_registered_outcome(prompt):
    assert requires_forget_outcome({"response": "Done."}, user_input=prompt)


@pytest.mark.parametrize("prompt", [
    "Remember my name is Lena. Forget my name.",
    'Remember the sentence "Forget MINT-84."',
    'Explain this quotation: "First continue. Forget MINT-84."',
    'Explain this quotation: "First continue and forget MINT-84."',
    'Explain this quotation: "First continue and erase MINT-84."',
    "Explain this code: `continue. Forget MINT-84.`",
    "Explain the next step and do not forget MINT-84.",
    "Explain the next step and do not erase MINT-84.",
    "Remember my name is Lena. Do not forget MINT-84.",
    "Remember my name is Lena.",
])
def test_profile_commands_and_quoted_or_negated_clauses_do_not_claim_erasure(prompt):
    assert not requires_forget_outcome({"response": "Done."}, user_input=prompt)


def test_effective_input_checks_every_clause():
    assert requires_forget_outcome(
        {"response": "Done."}, user_input="Continue.",
        effective_input="Remember my name is Lena. Forget MINT-84.",
    )


def test_mixed_prefix_cannot_authorize_an_unregistered_claim():
    from core.agent_runtime.action_honesty_validator import enforce_final_action_honesty

    prompt = "Remember my name is Lena. Forget MINT-84."
    claimed = "Forget applied. Removed 1 memory entry."
    result = enforce_final_action_honesty(
        {"response": claimed, "deterministic": True},
        user_input=prompt, session_id="mixed-prefix-r6",
        source_context={"chat_id": "mixed-prefix-r6", "client_turn_id": "r6-turn"},
    )
    assert result["response"] != claimed
    assert result["action_honesty_validator"]["applied"]
