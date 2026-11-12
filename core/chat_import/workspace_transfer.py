"""Apply reviewed working state through its canonical owners and retain undo intents."""
from __future__ import annotations

import json
from functools import partial
from pathlib import Path
from typing import Any

from core.chat_import import store

_CATEGORIES = ("instructions", "memories", "skills", "tools", "schedules")


def selection(plan: dict, raw: Any) -> dict:
    from core.cloud_escalation_policy import available_import_models

    if not isinstance(raw, dict) or set(raw) - {*_CATEGORIES, "private", "model", "working_copy"}:
        raise ValueError("invalid working context selection")
    if any(type(raw.get(key, False)) is not bool for key in (*_CATEGORIES, "private", "working_copy")):
        raise ValueError("invalid working context selection")
    result = {key: raw.get(key, False) for key in (*_CATEGORIES, "private", "working_copy")}
    result["private"] = bool(result["private"] or plan["private"])
    if result["private"] and (result["instructions"] or result["memories"]):
        raise ValueError("private context cannot become rules or memory")
    model = raw.get("model", "")
    if not isinstance(model, str) or (model and model not in available_import_models()):
        raise ValueError("local model unavailable")
    result["model"] = model
    return result


def admission_payload(plan: dict, chosen: dict) -> dict:
    """Only words that will be written, without technical ids, stamps or inode numbers."""
    return {"project": {"name": plan["name"], "root": plan["root"]}, "files": plan["files"],
            "models": plan["models"], "providers": plan["providers"], "notes": plan.get("notes", []),
            **{category: plan[category] for category in _CATEGORIES if chosen[category]}}


def apply(plan: dict, chosen: dict, *, batch_id: str, session_id: str, source: str,
          continuation: bool, review_key: str) -> None:
    from core.cloud_escalation_policy import import_local_chat_model
    from core.context_namespace import grant_context_import
    from core.execution.mcp_bridge import import_disabled_server
    from core.memory.entries import add_memory_fact, set_session_meta
    from core.memory.revocation import writing
    from core.operator.reminders import import_paused_task
    from core.private_chat import set_private
    from core.project_store import (
        _project_id_for,
        add_import_privacy,
        hold_project_context,
        import_project,
        validate_project_root,
    )
    from core.session_turn_gate import hold_session_turn_gate
    from core.skill_tools import stage_imported_skill
    from core.user_rules import import_rule
    from storage.db import get_connection

    payload = admission_payload(plan, chosen)
    valid, resolved = validate_project_root(plan["root"])
    if not valid or resolved != plan["root"]:
        raise ValueError("reviewed folder changed")
    info = Path(resolved).stat()
    if [info.st_dev, info.st_ino] != plan["root_identity"]:
        raise ValueError("reviewed folder changed")
    with hold_project_context(_project_id_for(resolved)), hold_session_turn_gate(session_id), writing(payload, session_id):
        store.note_resource(batch_id, session_id, "projects")
        project = import_project(plan["name"], plan["root"], batch_id=batch_id, session_id=session_id, source=source)
        project_id = project["id"]
        add_import_privacy(project_id, batch_id, session_id)
        set_session_meta(session_id, project_id=project_id)
        grant_context_import(session_id, scope="project", source_id="project:" + project_id,
                             source_project_id=project_id)
        if chosen["private"]:
            set_private(session_id, True)
        if chosen["instructions"]:
            store.note_resource(batch_id, session_id, "rules")
            for item in plan["instructions"]:
                result = import_rule("project" if item["scope"] == "project" else "chat", item["text"],
                                     project_id=project_id, batch_id=batch_id, session_id=session_id, source=source)
                if result.kind not in {"saved", "duplicate"}:
                    raise ValueError("source instructions were refused")
        if chosen["memories"]:
            from core.chat_import.ownership import keyed_digest

            for index, item in enumerate(plan["memories"]):
                # Preserve long source notes as ordered bounded facts through the
                # public writer; never write a raw memory file or a shadow store.
                text = item["text"]
                import textwrap

                chunks = textwrap.wrap(" ".join(text.split()), width=320, break_on_hyphens=False)
                for part, chunk in enumerate(chunks):
                    if not add_memory_fact(chunk, session_id=session_id, project_id=project_id,
                                           scope="project" if item["scope"] == "project" else "chat",
                                           source=f"chat-import:{source}:{batch_id}", share_scope="local_only",
                                           fact_key="chat-import:" + keyed_digest([batch_id, session_id, index, part])):
                        raise ValueError("source memory was refused")
        if chosen["skills"]:
            store.note_resource(batch_id, session_id, "skills")
            for item in plan["skills"]:
                stage_imported_skill(name=item["name"], body=item["body"], batch_id=batch_id,
                                     session_id=session_id, source=source)
        if chosen["tools"]:
            store.note_resource(batch_id, session_id, "tools")
            for item in plan["tools"]:
                import_disabled_server(label=item["name"], command=item["command"], args=item["args"],
                                       batch_id=batch_id, session_id=session_id, source=source)
        if chosen["schedules"]:
            store.note_resource(batch_id, session_id, "schedules")
            for item in plan["schedules"]:
                import_paused_task(note=item["note"], source_schedule=item["schedule"], batch_id=batch_id,
                                   session_id=session_id, source=source, get_connection_fn=get_connection)
        if chosen["model"]:
            store.note_resource(batch_id, session_id, "models")
            import_local_chat_model(session_id, model=chosen["model"], batch_id=batch_id, source=source)
        descriptor = {"source": source, "files": plan["files"], "models": plan["models"],
                      "review_key": review_key, "notes": plan.get("notes", []),
                      "providers": plan["providers"], "warnings": plan["warnings"],
                      "project_id": project_id,
                      "drafts": len(plan["skills"]) if chosen["skills"] else 0,
                      "disabled_tools": len(plan["tools"]) if chosen["tools"] else 0,
                      "paused_tasks": len(plan["schedules"]) if chosen["schedules"] else 0}
        connection = store.connect()
        try:
            connection.execute(f"UPDATE {store.CHATS_TABLE} SET context_json = ?, continuation_enabled = ?, context_state = 'ready' WHERE session_id = ?",
                               (json.dumps(descriptor, ensure_ascii=False), int(continuation), session_id))
            connection.commit()
        finally:
            connection.close()


def _owners() -> dict:
    from core.cloud_escalation_policy import imported_chat_models, purge_imported_chat_models
    from core.execution.mcp_bridge import imported_servers, purge_imported_servers
    from core.operator.reminders import imported_paused_tasks, purge_imported_paused_tasks
    from core.project_store import imported_projects, purge_imported_projects
    from core.skill_tools import imported_skill_drafts, purge_imported_skill_drafts
    from core.user_rules import imported_rules, purge_imported_rules
    from storage.db import get_connection

    return {"projects": (imported_projects, purge_imported_projects),
            "rules": (imported_rules, purge_imported_rules),
            "skills": (imported_skill_drafts, purge_imported_skill_drafts),
            "tools": (imported_servers, purge_imported_servers),
            "schedules": (partial(imported_paused_tasks, get_connection_fn=get_connection),
                          partial(purge_imported_paused_tasks, get_connection_fn=get_connection)),
            "models": (imported_chat_models, purge_imported_chat_models)}


def assert_undo_possible(batch_id: str, sessions: list[str]) -> None:
    from core.context_namespace import list_chat_namespaces
    from core.project_store import _load, project_native_work

    owners = _owners()
    namespaces = list_chat_namespaces(limit=None)
    for pid, project in _load().items():
        for guard in project.get("local_context_imports", []):
            if guard.get("batch_id") != batch_id:
                continue
            current = project_native_work(pid, exclude=tuple(sessions))
            prior = {sid: stamp for sid, stamp in guard.get("prior_work", {}).items() if sid not in sessions}
            if current != prior:
                raise ValueError("shared imported context has new work")
    for batch, session, owner in store.resource_owners(batch_id=batch_id):
        rows = owners[owner][0](batch, session)
        if any(not row["unchanged"] for row in rows):
            raise ValueError("imported context has new work")
        if owner == "projects" and any(row.get("import_created", True) and ns.project_id == row["id"] and ns.chat_id not in sessions
                                        for row in rows for ns in namespaces):
            raise ValueError("imported project is in use")


def purge(batch_id: str, session_id: str, *, token: str = "") -> int:
    from core.memory.carriers import carries

    owners = _owners()
    removed = 0
    for batch, session, owner in store.resource_owners(batch_id=batch_id, session_id=session_id):
        inventory, erase = owners[owner]
        removed += erase(batch, session, token=token)
        rows = inventory(batch, session)
        if token and any(carries(row["payload"], token) for row in rows):
            raise RuntimeError("source artifact erasure incomplete")
        if not rows:
            store.clear_resource(batch, session, owner)
    if not token:
        from core.project_store import remove_import_privacy

        remove_import_privacy(batch_id, session_id)
    return removed


def forget_artifacts(session_id: str, token: str) -> int:
    return sum(purge(batch, session, token=token)
               for batch, session in dict.fromkeys((batch, session) for batch, session, _owner
                                                   in store.resource_owners(session_id=session_id)))


def retains_forgotten_artifacts(session_id: str, token: str) -> bool:
    from core.memory.carriers import carries

    owners = _owners()
    return any(carries(row["payload"], token)
               for batch, session, owner in store.resource_owners(session_id=session_id)
               for row in owners[owner][0](batch, session))
