"""A paste under one rework request is the request's material for every reader, never the ask.

Measured on the Mac skills A/B (v0.8 round 7 lane head f4f048f7, qwen3:8b, loop on and off), with
x-article-builder added from Browse: "Turn this into an X article:" over the A/B article
(tests/fixtures/skills_ab_x_article_paste.md) ran about 340 s and ended "Web lookups are off".

- The front door spent 107 s reading the paste as separate demands, and the live-info slice lane
  answered sentences of the paste with the web-off line.
- The classifier read the paste as research and the tool gate read it as a web and workspace
  demand, so the turn spent a 137-161 s tool round in which the model called nothing.
- The answer was shaped as a counted list from the numbers in the paste: 320 tokens of room.
- That answer call ran out the 60 s window for an ordinary reply, and the turn failed.

Every one of those readers now asks `turn_planner.pasted_material_head` first and reads only the
request before the paste. A request with an ask of its own ("Turn this into a PDF:") keeps it.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

ARTICLE = (Path(__file__).parent / "fixtures" / "skills_ab_x_article_paste.md").read_text(encoding="utf-8")
TURN = "Turn this into an X article:\n\n" + ARTICLE
OWNER = {"surface": "api", "_owner_local": True, "client_host": "127.0.0.1"}


def test_the_live_info_slice_does_not_answer_a_paste() -> None:
    from core.agent_runtime.answer_coverage import FAMILY_LIVE_INFO, FAMILY_RECEIPT_LOCATION, coverage_for

    for family in (FAMILY_LIVE_INFO, FAMILY_RECEIPT_LOCATION):
        assert coverage_for(TURN, family).consumed == ()
    # A live question that is not a paste still belongs to the lane.
    assert coverage_for("What's the weather in Vilnius right now?", FAMILY_LIVE_INFO).consumed


def test_the_demand_seam_reads_a_paste_as_one_request() -> None:
    from core.agent_runtime.demand_ownership import demand_coverage

    coverage = demand_coverage(TURN)
    assert not coverage.mixed
    assert len(coverage.units) == 1


def test_a_paste_is_classified_by_its_request() -> None:
    from apps.vool_agent import VoolAgent
    from core.agent_runtime import turn_dispatch

    agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
    classify_fn = mock.Mock(return_value={"task_class": "unknown"})
    with mock.patch.object(agent, "_resolve_runtime_task", return_value=mock.Mock(task_id="task-paste")), \
            mock.patch.object(agent, "_update_runtime_checkpoint_context"), \
            mock.patch.object(agent, "_update_task_class"), \
            mock.patch.object(agent, "_emit_runtime_event"), \
            mock.patch.object(agent, "_maybe_handle_hive_create_confirmation", return_value=None), \
            mock.patch.object(agent, "_extract_hive_topic_create_draft", return_value=None), \
            mock.patch.object(agent, "_maybe_handle_hive_topic_mutation_request", return_value=None), \
            mock.patch.object(agent, "_maybe_handle_hive_topic_create_request", return_value=None), \
            mock.patch.object(agent, "_maybe_run_builder_controller", return_value=None):
        turn_dispatch.prepare_turn_task_bundle(
            agent,
            effective_input=TURN,
            user_input=TURN,
            session_id="paste-classified",
            source_context=dict(OWNER),
            interpreted=SimpleNamespace(as_context=lambda: {}),
            classify_fn=classify_fn,
            parse_channel_post_intent_fn=mock.Mock(return_value=(None, None)),
            dispatch_outbound_post_intent_fn=mock.Mock(),
            parse_operator_action_intent_fn=mock.Mock(return_value=None),
            dispatch_operator_action_fn=mock.Mock(),
        )
    assert classify_fn.call_args.args[0] == "Turn this into an X article"


def test_the_classifier_reads_the_paste_as_research() -> None:
    """Why the classification seam reads the request: the paste alone says research."""
    from core.task_router import classify

    assert classify(TURN)["task_class"] == "research"
    assert classify("Turn this into an X article")["task_class"] != "research"


def test_the_tool_gate_reads_the_request_not_the_paste() -> None:
    from core.execution.planner import should_attempt_tool_intent

    assert should_attempt_tool_intent(TURN, task_class="unknown", source_context=dict(OWNER)) is False
    translate = "Translate this into German:\n\n" + ARTICLE[:1500]
    assert should_attempt_tool_intent(translate, task_class="unknown", source_context=dict(OWNER)) is False
    # The request's own ask still opens the tools.
    as_file = "Turn this into a PDF:\n\n" + ARTICLE
    assert should_attempt_tool_intent(as_file, task_class="unknown", source_context=dict(OWNER)) is True
    web = "Search the web for news about tool libraries and summarize it"
    assert should_attempt_tool_intent(web, task_class="unknown", source_context=dict(OWNER)) is True


def _profile(text: str) -> dict:
    from core.prompt_normalizer import _generation_profile

    return _generation_profile(
        surface="api", task_kind="normalization_assist", output_mode="plain_text", task_class="unknown",
        user_text=text,
    )


def test_the_answer_has_room_for_the_piece_it_remakes() -> None:
    profile = _profile(TURN)
    assert profile["profile_id"] == "chat_plain_text"
    assert profile["max_output_tokens"] == 2600
    assert profile["output_budget_intent"]["reason"] == "paste_rework_room"
    assert profile["output_budget_intent"]["ceiling"] == 0


def test_a_paste_with_a_stated_length_keeps_it() -> None:
    profile = _profile("Summarize this in 50 words:\n\n" + ARTICLE)
    assert profile["output_budget_intent"]["reason"] != "paste_rework_room"
    assert profile["max_output_tokens"] <= 520


def test_a_turn_with_no_paste_keeps_its_chat_length() -> None:
    profile = _profile("Can you explain how a tool library works for a small street?")
    assert profile["profile_id"] == "chat_plain_text"
    assert profile["max_output_tokens"] <= 520


def test_a_long_answer_gets_the_long_window() -> None:
    from core.memory_first_router import LONG_ANSWER_TOKENS, resolve_fallback_budget_seconds

    def budget(**kwargs):
        return resolve_fallback_budget_seconds("daily", forced_cpu=False, no_usable_gpu=False, **kwargs)

    assert budget(output_mode="plain_text", answer_tokens=2600) == 180.0
    assert budget(output_mode="plain_text", answer_tokens=LONG_ANSWER_TOKENS) == 180.0
    assert budget(output_mode="plain_text", answer_tokens=520) == 60.0
    assert budget(output_mode="plain_text") == 60.0
    assert resolve_fallback_budget_seconds("deep", forced_cpu=False, no_usable_gpu=False, answer_tokens=2600) is None


def _first_call_window(answer_room: int, monkeypatch) -> float | None:
    """Drive the real router once with one candidate that hangs; return the window it was given."""
    from core.memory_first_router import MemoryFirstRouter
    from core.model_health import reset_provider_health
    from storage.db import get_connection
    from storage.migrations import run_migrations
    from tests.test_fallback_budget_characterization import _FakeClock, _manifest

    monkeypatch.delenv("VOOL_OLLAMA_NUM_GPU", raising=False)
    monkeypatch.setattr("core.memory_first_router._machine_has_no_usable_gpu", lambda: False)
    monkeypatch.setattr("core.local_compute_budget.local_inference_is_cpu_only", lambda: False)
    run_migrations()
    reset_provider_health()
    conn = get_connection()
    try:
        for table in ("model_provider_manifests", "candidate_knowledge_lane", "local_tasks"):
            conn.execute(f"DELETE FROM {table}")
        conn.commit()
    finally:
        conn.close()
    clock = _FakeClock()
    manifest = _manifest("long")
    router = MemoryFirstRouter()
    router.registry.register_manifest(manifest)
    windows: list[float | None] = []

    def build_adapter(seen):
        window = (seen.runtime_config or {}).get("timeout_seconds")
        windows.append(window)
        adapter = mock.Mock()
        adapter.health_check.return_value = {"ok": True}
        adapter.get_license_metadata.return_value = {}

        def _hang(*_args, **_kwargs):
            clock.advance(float(window or 999.0))
            raise RuntimeError("provider_http_500:internal server error")

        adapter.run_text_task.side_effect = _hang
        return adapter

    real_build = router._build_round_request

    def build_request(**kwargs):
        request = real_build(**kwargs)
        request.max_output_tokens = answer_room
        return request

    plan = SimpleNamespace(
        lane="daily",
        selected_provider_id=manifest.provider_id,
        to_dict=lambda: {
            "schema": "vool.local_inference_autopilot.v1", "lane": "daily",
            "selected_provider_id": manifest.provider_id, "selected_model": manifest.model_name,
            "verifier_required": False, "verifier_provider_id": None, "verifier_model": None,
            "explicit_heavy": False, "prefix_cache": {"backend": "none", "supported": False}, "warnings": [],
        },
    )
    with mock.patch("core.memory_first_router.rank_provider_candidates", return_value=[manifest]), \
            mock.patch.object(router.registry, "build_adapter", side_effect=build_adapter), \
            mock.patch.object(router, "_build_round_request", side_effect=build_request), \
            mock.patch("core.memory_first_router.build_local_inference_autopilot_plan", return_value=plan), \
            mock.patch("core.memory_first_router.time.monotonic", side_effect=clock.monotonic):
        router._execute_provider_task(
            task=SimpleNamespace(task_id="long-answer-task", task_summary="hi"),
            classification={"task_class": "daily"},
            interpretation=SimpleNamespace(reconstructed_text="hi"),
            context_result=SimpleNamespace(
                retrieval_confidence_score=0.2,
                report=SimpleNamespace(
                    retrieval_confidence=0.2,
                    to_dict=lambda: {"retrieval_confidence": 0.2, "external_evidence_attachments": []},
                ),
            ),
            persona=SimpleNamespace(),
            task_hash="long-answer-hash",
            task_kind="chat",
            output_mode="plain_text",
            allow_paid_fallback=False,
            provider_role="queen",
            surface="openclaw",
            source_context={
                "surface": "openclaw",
                "_canonical_user_turn_id": "long-answer-turn",
                "session_id": "long-answer-session",
                "runtime_session_id": "long-answer-session",
            },
        )
    assert windows, "the candidate was never called"
    return windows[0]


@pytest.mark.parametrize(("answer_room", "window"), [(2600, 180.0), (320, 60.0)])
def test_the_router_gives_a_long_answer_call_the_long_window(answer_room, window, monkeypatch) -> None:
    assert _first_call_window(answer_room, monkeypatch) == pytest.approx(window)
