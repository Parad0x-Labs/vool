"""An idle poll is one control request; mixed work must not disappear behind its ACK."""
import pytest

from core.agent_runtime.fast_paths_utility import heartbeat_poll_fast_path
from core.agent_runtime.turn_frontdoor import closed_semantic_contract_covers_turn

ORIGINAL = "Read HEARTBEAT.md if it exists (workspace context). Follow it strictly. Do not infer or repeat old tasks from prior chats. If nothing needs attention, reply HEARTBEAT_OK."
NOVEL = "Check HEARTBEAT.md and reply HEARTBEAT_OK when nothing needs attention."


@pytest.mark.parametrize("prompt", [ORIGINAL, NOVEL])
def test_idle_poll_owns_turn_before_decomposition(tmp_path, prompt):
    (tmp_path / "HEARTBEAT.md").write_text("# Nothing scheduled\n")
    context = {"workspace": str(tmp_path)}
    assert closed_semantic_contract_covers_turn(prompt, session_id="poll", source_context=context)
    assert heartbeat_poll_fast_path(prompt, source_context=context) == "HEARTBEAT_OK"


@pytest.mark.parametrize("suffix", [" Also explain what a stack is.", " And read notes.txt.", " Also tell me the weather in Vilnius."])
def test_idle_ack_does_not_swallow_another_request(tmp_path, suffix):
    (tmp_path / "HEARTBEAT.md").write_text("")
    context = {"workspace": str(tmp_path)}
    prompt = ORIGINAL + suffix
    assert heartbeat_poll_fast_path(prompt, source_context=context) is None
    assert not closed_semantic_contract_covers_turn(prompt, session_id="poll", source_context=context)


def test_actionable_heartbeat_is_not_acknowledged_as_idle(tmp_path):
    (tmp_path / "HEARTBEAT.md").write_text("- Review the local build report.\n")
    context = {"workspace": str(tmp_path)}
    assert heartbeat_poll_fast_path(ORIGINAL, source_context=context) is None
    assert not closed_semantic_contract_covers_turn(ORIGINAL, session_id="poll", source_context=context)


@pytest.mark.parametrize("prompt", [ORIGINAL, NOVEL])
def test_idle_poll_completes_through_real_agent_without_model(tmp_path, monkeypatch, prompt):
    from apps.vool_agent import VoolAgent

    (tmp_path / "HEARTBEAT.md").write_text("")
    agent = VoolAgent(backend_name="test-backend", device="heartbeat-ownership", persona_id="default")

    def unexpected_model(**kwargs):
        raise AssertionError("An idle heartbeat must not buy a model call.")

    monkeypatch.setattr(agent.memory_router, "resolve", unexpected_model)
    monkeypatch.setattr(agent.memory_router, "resolve_tool_intent", unexpected_model)
    monkeypatch.setattr(agent.hive_activity_tracker, "build_chat_footer", lambda **kwargs: "Hive: noisy footer")
    result = agent.run_once(prompt, session_id_override="openclaw:heartbeat-ownership",
                            source_context={"surface": "api", "platform": "api", "workspace": str(tmp_path)})
    assert result["response"] == "HEARTBEAT_OK"
    assert result["model_execution"]["used_model"] is False
