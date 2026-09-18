"""Per-project context isolation: the scope helpers + the real wall through load() (A's memory never in B)."""

from __future__ import annotations

import pytest

from core import runtime_paths
from core import tiered_context_loader as tcl
from core.context_namespace import ensure_chat_namespace, grant_context_import
from core.human_input_adapter import HumanInputInterpretation
from core.identity_manager import load_active_persona
from core.memory.entries import (
    record_memory_entry,
    resolve_memory_access_policy,
)
from core.persistent_memory import (
    conversation_log_path,
    memory_entries_path,
    session_summaries_path,
    set_session_meta,
    user_heuristics_path,
)
from core.task_router import classify, create_task_record


@pytest.fixture
def _fresh(tmp_path, monkeypatch):
    from storage.migrations import run_migrations

    monkeypatch.setenv("VOOL_HOME", str(tmp_path / "home"))
    runtime_paths.configure_runtime_home(tmp_path / "home")
    run_migrations()
    for p in (memory_entries_path(), session_summaries_path(), user_heuristics_path(), conversation_log_path()):
        if p.exists():
            p.unlink()
    yield
    runtime_paths.configure_runtime_home(None)


def _interp(text):
    return HumanInputInterpretation(
        raw_text=text, normalized_text=text, reconstructed_text=text, intent_mode="request",
        topic_hints=["acme", "deploy", "key"], reference_targets=[], understanding_confidence=0.8,
        quality_flags=[], needs_clarification=False, turn_id=None,
    )


def _load(project_id, *, session_id):
    ensure_chat_namespace(
        session_id,
        project_id=project_id,
        grant_current_receipts=False,
    )
    if project_id:
        grant_context_import(
            session_id,
            scope="project",
            source_id=f"project:{project_id}",
            source_project_id=project_id,
        )
    loader = tcl.TieredContextLoader()
    task = create_task_record("what do you know about the acme deploy key rotation schedule")
    interp = _interp(task.task_summary)
    src = {"surface": "openclaw", "platform": "openclaw"}
    result = loader.load(
        task=task, classification=classify(task.task_summary, context=interp.as_context()),
        interpretation=interp, persona=load_active_persona("default"), session_id=session_id, source_context=src,
    )
    return " ".join(i.content for i in result.relevant_items).lower()


def _seed_two_projects():
    chat_a = "openclaw:" + "a" * 20
    chat_b = "openclaw:" + "b" * 20
    ensure_chat_namespace(
        chat_a,
        project_id="proj_A",
        grant_current_receipts=False,
    )
    grant_context_import(
        chat_a,
        scope="project",
        source_id="project:proj_A",
        source_project_id="proj_A",
    )
    ensure_chat_namespace(
        chat_b,
        project_id="proj_B",
        grant_current_receipts=False,
    )
    grant_context_import(
        chat_b,
        scope="project",
        source_id="project:proj_B",
        source_project_id="proj_B",
    )
    policy_a = resolve_memory_access_policy(chat_id=chat_a)
    policy_b = resolve_memory_access_policy(chat_id=chat_b)
    record_memory_entry(
        "The Acme deploy key rotates every Tuesday at midnight.", category="fact", session_id=chat_a,
        source="test", confidence=0.95, keywords=["acme", "deploy", "key", "rotates", "tuesday"], share_scope="local_only",
        project_id="proj_A", scope="project", authority="confirmed_memory",
        fact_key="acme_deploy_rotation", expires_at=None, review_after=None,
        access_policy=policy_a,
    )
    record_memory_entry(
        "The Beta widget uses a purple gradient header.", category="fact", session_id=chat_b,
        source="test", confidence=0.95, keywords=["beta", "widget", "purple", "gradient"], share_scope="local_only",
        project_id="proj_B", scope="project", authority="confirmed_memory",
        fact_key="beta_widget_header", expires_at=None, review_after=None,
        access_policy=policy_b,
    )
    set_session_meta(chat_a, project_id="proj_A")
    set_session_meta(chat_b, project_id="proj_B")


def test_bound_chat_never_sees_another_projects_memory(_fresh) -> None:
    _seed_two_projects()
    # A chat in project B asking about Acme (project A's secret) must not surface it.
    joined = _load("proj_B", session_id="openclaw:" + "c" * 20)
    assert "acme" not in joined and "tuesday" not in joined


def test_bound_chat_still_sees_its_own_projects_memory(_fresh) -> None:
    _seed_two_projects()
    # A chat in project A asking about Acme DOES recall project A's own memory.
    joined = _load("proj_A", session_id="openclaw:" + "d" * 20)
    assert "acme" in joined


def test_unbound_chat_does_not_import_project_memory(_fresh) -> None:
    _seed_two_projects()
    # A General chat has no project grant, so project facts stay quarantined.
    joined = _load("", session_id="openclaw:" + "e" * 20)
    assert "acme" not in joined and "tuesday" not in joined
