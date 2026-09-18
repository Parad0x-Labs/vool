"""Metamorphic audit checks: novel wording and renamed repositories must change the evidence.

These tests intentionally avoid the historical ``web0``/``pdf compress`` fixtures and the exact
live transcript phrase. A static canned response can pass a replay; it cannot pass when both the
workspace name and the defective source artifact change under it.
"""
from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from apps.vool_agent import VoolAgent
from core.agent_runtime.workspace_audit import (
    looks_like_code_audit_request,
    maybe_handle_workspace_audit_request,
)
from core.task_router import classify, model_execution_profile


class _NoFastAnswerAgent:
    def _fast_path_result(self, **kwargs):
        raise AssertionError("a successful bound audit must continue to the answer model")


@pytest.mark.parametrize(
    ("verb", "target"),
    [
        ("scrutinize", "the implementation"),
        ("examine", "our source code"),
        ("evaluate", "this project"),
        ("assess", "the codebase"),
        ("review", "my repository"),
        ("inspect", "the workspace for production failures"),
    ],
)
def test_unseen_audit_paraphrases_trigger_evidence_collection(verb: str, target: str) -> None:
    prompt = f"Please {verb} {target}, decide whether it is robust, and suggest repairs without editing anything."
    assert looks_like_code_audit_request(prompt), prompt


def test_audit_intent_does_not_capture_a_conceptual_audit_logging_question() -> None:
    assert not looks_like_code_audit_request(
        "Explain audit logging implementation patterns for a distributed system."
    )


@pytest.mark.parametrize(
    "prompt",
    [
        "Review this relay implementation as if approving it for production; keep it read-only.",
        "Inspect our payment gateway source code and propose repairs without editing files.",
        "Evaluate the background job runner codebase for production readiness.",
    ],
)
def test_qualified_artifact_names_still_trigger_bound_audit_tools(prompt: str) -> None:
    assert looks_like_code_audit_request(prompt), prompt


def test_distributed_audit_signals_trigger_without_an_exact_scripted_word_order() -> None:
    assert looks_like_code_audit_request(
        "Examine the command relay in the active directory and deliver a launch-blocker "
        "assessment with concrete remedies. Do not alter the workspace."
    )


def test_distributed_signals_still_reject_conceptual_directory_security_question() -> None:
    assert not looks_like_code_audit_request(
        "Examine directory traversal attacks and explain how they work in abstract terms."
    )


def test_plain_workspace_inspection_stays_on_the_generic_tool_lane() -> None:
    assert not looks_like_code_audit_request("Inspect the current workspace")


def test_collected_audit_evidence_skips_the_second_generic_tool_planner(monkeypatch) -> None:
    agent = VoolAgent(backend_name="test-backend", device="test", persona_id="default")
    monkeypatch.setattr(
        agent,
        "_should_attempt_tool_intent",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("tool planner must not run twice")),
    )

    result = agent._maybe_execute_model_tool_intent(
        task=None,
        effective_input="Examine the command relay in the active directory.",
        classification={"task_class": "workspace_audit"},
        interpretation=None,
        context_result=None,
        persona=None,
        session_id="audit-no-second-planner",
        source_context={
            "workspace_audit_evidence_collected": True,
            "runtime_tool_observations": [{"intent": "workspace.audit", "final_answer": False}],
        },
        surface="api",
    )

    assert result is None


def test_collected_evidence_forces_a_real_workspace_audit_class_not_unknown() -> None:
    result = classify(
        "Give me your verdict on whether the implementation is sound.",
        context={
            "source_context": {
                "workspace_audit_evidence_collected": True,
                "workspace_binding": "project",
            }
        },
    )
    assert result["task_class"] == "workspace_audit"
    profile = model_execution_profile("workspace_audit", chat_surface=True)
    assert profile["output_mode"] == "plain_text"
    assert profile["task_kind"] == "normalization_assist"


def _collect(root: Path, prompt: str, monkeypatch: pytest.MonkeyPatch) -> dict:
    context = {
        "surface": "api",
        "workspace": str(root),
        "workspace_root": str(root),
        "workspace_binding": "project",
        "project_id": root.name,
        "requested_model": "novel/zephyr-audit:free",
    }
    monkeypatch.setattr("core.runtime_task_events.emit_runtime_event", lambda *args, **kwargs: None)
    result = maybe_handle_workspace_audit_request(
        _NoFastAnswerAgent(),
        prompt,
        session_id=f"openclaw:{uuid.uuid5(uuid.NAMESPACE_URL, str(root)).hex[:20]}",
        source_surface="api",
        source_context=context,
    )
    assert result is None
    assert context["workspace_audit_evidence_collected"] is True
    return context["runtime_tool_observations"][-1]


def test_renaming_workspace_and_defect_changes_real_audit_evidence(tmp_path: Path, monkeypatch) -> None:
    raven = tmp_path / f"raven-{uuid.uuid5(uuid.NAMESPACE_DNS, 'raven-audit').hex[:8]}"
    comet = tmp_path / f"comet-{uuid.uuid5(uuid.NAMESPACE_DNS, 'comet-audit').hex[:8]}"
    raven.mkdir()
    comet.mkdir()
    (raven / "cipher_cache.py").write_text(
        "import hashlib\n\ndef cache_key(value):\n    return hashlib.md5(value).hexdigest()\n",
        encoding="utf-8",
    )
    (comet / "runner_gate.py").write_text(
        "def execute(payload):\n    try:\n        return eval(payload)\n    except Exception:\n        return None\n",
        encoding="utf-8",
    )

    raven_evidence = _collect(
        raven,
        "Scrutinize the implementation and give a blunt engineering verdict without changing files.",
        monkeypatch,
    )
    comet_evidence = _collect(
        comet,
        "Evaluate this project for fragile code, then propose repairs; keep it read-only.",
        monkeypatch,
    )

    raven_report = raven_evidence["response_preview"]
    comet_report = comet_evidence["response_preview"]
    assert "cipher_cache.py" in raven_report and "runner_gate.py" not in raven_report
    assert "runner_gate.py" in comet_report and "cipher_cache.py" not in comet_report
    assert raven_report != comet_report
    assert raven_evidence["final_answer"] is False
    assert comet_evidence["final_answer"] is False
