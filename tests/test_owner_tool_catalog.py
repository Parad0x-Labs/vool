"""The compact owner catalog: owner turns on a local model are offered one read tool per family.

Root cause (2026-10-29 blind measurement, qwen3:8b on CPU): the right tool ran on 17 of 70 asks
that needed this computer. Two word gates decided from the raw words, before the model, whether
it would see any tool, and a fresh phrasing ("whats eating my cpu", "kas yra mano Downloads
aplanke?") fell to the tools-less chat lane. On the owner's own turn served by a local model the
gates now let go and the offer seats the catalog; the model decides. These tests pin the scope:
a remote session, a cloud model, a tools-forbidden turn and the flag turned off keep today's
behaviour, and nothing in the catalog writes.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.owner_tool_catalog import OWNER_CATALOG, owner_catalog_applies, turn_answers_on_local_model

OWNER = {"surface": "api", "platform": "api", "_owner_local": True, "_http_ingress": True,
         "runtime_session_id": "s-cat", "turn_id": "t-cat"}
REMOTE = {**OWNER, "_owner_local": False}

LOCAL = SimpleNamespace(provider_name="ollama-local", model_name="qwen3:8b", adapter_type="openai_compatible",
                        source_type="http", runtime_config={"base_url": "http://127.0.0.1:11434"}, metadata={})
CLOUD = SimpleNamespace(provider_name="openrouter", model_name="some/cloud-model", adapter_type="openai_compatible",
                        source_type="http", runtime_config={"base_url": "https://openrouter.ai/api/v1"},
                        metadata={"cost_class": "paid_cloud"})

# Fresh phrasings no recognizer was written for: the catalog must reach them anyway.
FRESH_NEEDS = [
    "kas yra mano Downloads aplanke?",
    "whats eating my cpu right now",
    "which of your skills are switched off",
    "whats Tomas's phone number in my contacts",
    "lê o meu CV em PDF e diz-me qual foi o meu último emprego",
]


@pytest.fixture
def registry(monkeypatch):
    def _set(*manifests):
        monkeypatch.setattr("core.model_registry.ModelRegistry.list_manifests",
                            lambda self, enabled_only=False, limit=256: list(manifests))
        monkeypatch.setattr("core.auto_local_only_mode.turn_is_local_only", lambda _ctx: False)
    return _set


def _lane_agent():
    from core.agent_runtime.fast_path_facade import FastPathFacadeMixin
    from core.agent_runtime.hive_topic_facade import HiveTopicFacadeMixin
    from core.agent_runtime.proceed_intent_support import ProceedIntentSupportMixin

    class _Agent(ProceedIntentSupportMixin, FastPathFacadeMixin, HiveTopicFacadeMixin):
        def _is_chat_truth_surface(self, _source_context: object) -> bool:
            return True

    return _Agent()


def _gates_open(text: str, context: dict) -> bool:
    from core.agent_runtime.runtime_checkpoint_lane_policy import should_keep_ai_first_chat_lane
    from core.execution.planner import should_attempt_tool_intent

    kept = should_keep_ai_first_chat_lane(
        _lane_agent(), user_input=text, classification={"task_class": "chat_conversation"},
        interpretation=None, source_context=dict(context), checkpoint_state=None,
    )
    return (not kept) and should_attempt_tool_intent(text, task_class="chat_conversation", source_context=dict(context))


def _offered(text: str, context: dict) -> tuple[str, ...]:
    from core.tool_offer_assembly import assemble_tool_offer

    return tuple(assemble_tool_offer(task_class="chat_conversation", user_text=text,
                                     source_context=dict(context)).intents)


@pytest.mark.parametrize("text", FRESH_NEEDS)
def test_an_owner_turn_on_the_local_model_reaches_the_catalog(text: str, registry) -> None:
    registry(LOCAL)
    assert owner_catalog_applies(text, dict(OWNER))
    assert _gates_open(text, OWNER)
    offered = set(_offered(text, OWNER))
    assert set(OWNER_CATALOG) <= offered, sorted(set(OWNER_CATALOG) - offered)
    assert "respond.direct" in offered


def test_the_catalog_is_read_only_and_small() -> None:
    from core.tool_intent_executor import runtime_tool_specs

    specs = {str(s.get("intent")): s for s in runtime_tool_specs()}
    assert len(OWNER_CATALOG) <= 12
    for intent in OWNER_CATALOG:
        assert intent in specs, intent
        assert specs[intent].get("read_only") is True, intent


def test_a_seated_write_keeps_its_seat_beside_the_catalog(registry) -> None:
    registry(LOCAL)
    offered = _offered("save a note: call the plumber about the sink on monday", OWNER)
    assert "operator.save_note" in offered
    assert set(OWNER_CATALOG) <= set(offered)


@pytest.mark.parametrize("text", FRESH_NEEDS[:2])
def test_a_remote_turn_gets_no_catalog(text: str, registry) -> None:
    registry(LOCAL)
    assert not owner_catalog_applies(text, dict(REMOTE))
    assert not set(OWNER_CATALOG) <= set(_offered(text, REMOTE))


def test_a_cloud_lane_keeps_todays_gates(registry) -> None:
    registry(LOCAL, CLOUD)
    assert not turn_answers_on_local_model(dict(OWNER))
    assert not owner_catalog_applies(FRESH_NEEDS[0], dict(OWNER))
    # Pinned to the cloud model while a local one is also registered: not local.
    assert not turn_answers_on_local_model({**OWNER, "requested_model": "some/cloud-model"})
    # Pinned to the local model: local, whatever else is registered.
    assert turn_answers_on_local_model({**OWNER, "requested_model": "qwen3:8b"})


def test_an_empty_or_unreadable_registry_is_not_local(registry, monkeypatch) -> None:
    registry()
    assert not turn_answers_on_local_model(dict(OWNER))

    def _boom(self, enabled_only=False, limit=256):
        raise RuntimeError("registry unreadable")

    monkeypatch.setattr("core.model_registry.ModelRegistry.list_manifests", _boom)
    assert not turn_answers_on_local_model(dict(OWNER))


def test_local_only_binds_without_reading_the_registry(monkeypatch) -> None:
    monkeypatch.setattr("core.auto_local_only_mode.turn_is_local_only", lambda _ctx: True)

    def _boom(self, enabled_only=False, limit=256):
        raise AssertionError("registry read on a Local Only turn")

    monkeypatch.setattr("core.model_registry.ModelRegistry.list_manifests", _boom)
    assert turn_answers_on_local_model(dict(OWNER))


def test_a_tools_forbidden_turn_gets_no_catalog(registry) -> None:
    registry(LOCAL)
    text = "without using any tools, just tell me from memory what is usually in a downloads folder"
    assert not owner_catalog_applies(text, dict(OWNER))


def test_the_flag_turns_it_off(registry, monkeypatch) -> None:
    registry(LOCAL)
    monkeypatch.setenv("VOOL_OWNER_LOCAL_TOOL_CATALOG", "0")
    assert not owner_catalog_applies(FRESH_NEEDS[0], dict(OWNER))
    assert not set(OWNER_CATALOG) <= set(_offered(FRESH_NEEDS[0], OWNER))


@pytest.mark.parametrize("text", [
    "which of your skills are switched off",
    "what language are you set to reply in?",
    "what did i download this week?",
])
def test_a_question_about_the_owner_or_vool_is_not_adjudicated_on_a_catalog_turn(text: str, registry) -> None:
    from core.entity_ambiguity import single_plain_know_question

    registry(LOCAL)
    assert not single_plain_know_question(text, source_context=dict(OWNER))
    # Off the catalog (a cloud lane is registered too), the probe keeps its jurisdiction.
    registry(LOCAL, CLOUD)
    assert single_plain_know_question(text, source_context=dict(OWNER))


@pytest.mark.parametrize("text", ["whats the capital of australia", "how tall is the tower in springfield?"])
def test_a_world_question_keeps_the_ambiguity_probe_on_a_catalog_turn(text: str, registry) -> None:
    from core.entity_ambiguity import single_plain_know_question

    registry(LOCAL)
    assert single_plain_know_question(text, source_context=dict(OWNER))
