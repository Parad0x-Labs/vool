"""A model that answers in words after real tool steps has answered: it is not asked a second time.

CI on fe98e849 (2026-11-22): the tool loop stopped on a round that named no tool after real steps (right), then asked a
model again for the answer the round had just written, and that second call went back to the model that had failed its
bounded retry pair. The round's own prose is the answer, held to the same checks as any written answer; an empty round
still has the answer written from the steps.
"""
from __future__ import annotations

import pathlib
import random
import tempfile
import uuid
from types import SimpleNamespace

from apps.vool_agent import VoolAgent
from core.human_input_adapter import HumanInputInterpretation
from core.identity_manager import load_active_persona
from core.memory_first_router import ModelExecutionDecision
from storage.migrations import run_migrations


def _marker() -> str:
    return "MARK_" + uuid.uuid4().hex[:10].upper()


class _Router:
    """Round 1 writes the file; round 2 is the scripted reply; any further call is counted."""

    def __init__(self, path: str, content: str, second_text: str):
        self.path, self.content, self.second_text = path, content, second_text
        self.tool_rounds = 0
        self.answers_asked = 0

    def resolve_tool_intent(self, **_kwargs):
        self.tool_rounds += 1
        if self.tool_rounds == 1:
            payload = {"intent": "workspace.write_file", "arguments": {"path": self.path, "content": self.content}}
            return ModelExecutionDecision(
                source="scripted", task_hash="h", used_model=True, confidence=0.8, structured_output=payload,
                tool_calls=(SimpleNamespace(intent=payload["intent"], arguments=payload["arguments"], call_id="c1",
                                            name=payload["intent"]),),
                validation_state="validated", provider_id="local-qwen-http:qwen-local",
            )
        return ModelExecutionDecision(
            source="scripted", task_hash="h", used_model=True, confidence=0.8, structured_output={},
            output_text=self.second_text, validation_state="validated", provider_id="local-qwen-http:qwen-local",
        )

    def resolve(self, **_kwargs):
        self.answers_asked += 1
        return ModelExecutionDecision(
            source="scripted", task_hash="h", used_model=True, confidence=0.8,
            output_text=f"Done: {self.path} now holds {self.content}.", validation_state="validated",
            provider_id="local-qwen-http:qwen-local",
        )


def _run(second_text_for):
    run_migrations()
    workspace = pathlib.Path(tempfile.mkdtemp())
    path = f"{random.choice(['note', 'marker', 'memo', 'check'])}-{uuid.uuid4().hex[:6]}.txt"
    content = _marker()
    router = _Router(path, content, second_text_for(path, content))
    agent = VoolAgent.__new__(VoolAgent)
    agent.memory_router = router
    agent._should_keep_ai_first_chat_lane = lambda **_kw: False
    agent._should_run_builder_controller = lambda **_kw: False
    agent._plan_tool_workflow = lambda **_kw: SimpleNamespace(handled=False, stop_after=False, next_payload=None, reason="")
    agent.hive_activity_tracker = None
    agent.public_hive_bridge = None
    session = f"openclaw:{uuid.uuid4().hex[:20]}"
    from tests._toolchain_fixtures import internal_scope

    source_context = {
        "session_id": session, "runtime_session_id": session, "surface": "api",
        "workspace": str(workspace), "workspace_root": str(workspace), "workspace_binding": "project",
        "project_id": workspace.name, "turn_id": "turn-answer-after-steps",
        **internal_scope("answer-after-steps", "create_files", "modify_files", intents=("workspace.write_file",)),
    }
    from unittest import mock

    from core.tiered_context_loader import TieredContextResult

    context_result = TieredContextResult(
        bootstrap_items=[], relevant_items=[], cold_items=[], local_candidates=[], swarm_metadata=[],
        report=mock.Mock(retrieval_confidence="low", swarm_metadata_consulted=False, cold_archive_opened=False),
        retrieval_confidence_score=0.15, cold_decision=mock.Mock(allow=False),
    )
    context_result.report.to_dict.return_value = {}
    context_result.report.total_tokens_used.return_value = 0
    context_result.assembled_context = lambda: "No strong local memory."
    ask = f"{random.choice(['write', 'put', 'save'])} {content} into {path} and tell me what it holds"
    result = agent._maybe_execute_model_tool_intent(
        task=SimpleNamespace(task_id="t-after-steps"),
        effective_input=ask,
        classification={"task_class": "debugging"},
        interpretation=HumanInputInterpretation(
            raw_text=ask, normalized_text=ask, reconstructed_text=ask, intent_mode="request", topic_hints=[],
            reference_targets=[], understanding_confidence=0.7, quality_flags=[],
        ),
        context_result=context_result,
        persona=load_active_persona("default"),
        session_id=session,
        source_context=source_context,
        surface="api",
    )
    assert (workspace / path).read_text(encoding="utf-8") == content
    return router, str((result or {}).get("response") or "")


def test_a_prose_round_after_real_steps_is_the_answer() -> None:
    opener = random.choice(["Done.", "All set.", "Written."])
    said: dict[str, str] = {}

    def _second(path: str, content: str) -> str:
        said["text"] = f"{opener} The file {path} now holds {content}."
        return said["text"]

    router, reply = _run(_second)
    assert router.answers_asked == 0, "the model was asked again for an answer it had already written"
    assert router.tool_rounds == 2
    assert said["text"] in reply


def test_an_empty_round_after_real_steps_still_gets_its_answer_written() -> None:
    router, reply = _run(lambda _path, _content: random.choice(["", "   "]))
    assert router.answers_asked == 1
    assert "Done:" in reply
