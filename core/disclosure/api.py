from __future__ import annotations

from typing import Any


def handle_get(path: str, query: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    from core.disclosure.settings import load_settings
    from core.disclosure.wire import previews

    if path in {"/api/disclosure/compare/models", "/api/disclosure/compare/job"}:
        from core.model_compare.service import models_for, read
        try:
            ident = str((query.get("id") or [""])[0])
            return 200, {"models": models_for(ident)} if path.endswith("/models") else read(ident)
        except (KeyError, ValueError):
            return 410, {"error": "The prepared request expired. Run the task again to compare it."}
    if path == "/api/disclosure/local-models":
        from core.disclosure.local_choice import choices
        try:
            return 200, {"models": choices(str((query.get("id") or [""])[0]))}
        except (KeyError, ValueError):
            return 200, {"models": []}
    if path == "/api/disclosure/projects":
        from core.disclosure.rules import list_rules
        return 200, {"projects": list_rules()}
    if path == "/api/disclosure/settings":
        return 200, load_settings()
    if path == "/api/disclosure/pending":
        chat = (query.get("chat_id") or [None])[0]
        return 200, {"requests": previews(chat_id=chat)}
    return 404, {"error": "not_found"}


def handle_post(path: str, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    from core.disclosure.settings import save_settings
    from core.disclosure.wire import close_chat, decide

    try:
        if path == "/api/disclosure/compare/cancel":
            from core.model_compare.service import cancel
            return 200, cancel(str(body.get("id") or ""))
        if path == "/api/disclosure/compare/start":
            from core.model_compare.service import start
            return 200, start(str(body.get("id") or ""), str(body.get("sha256") or ""), body.get("models"))
        if path == "/api/disclosure/compare/prefer":
            from core.model_compare.service import prefer
            return 200, prefer(str(body.get("id") or ""), str(body.get("model") or ""))
        if path == "/api/disclosure/local":
            from core.disclosure.local_choice import prepare_retry
            return 200, prepare_retry(str(body.get("id") or ""), str(body.get("sha256") or ""), str(body.get("model") or ""))
        if path == "/api/disclosure/rules/propose":
            from core.disclosure.rules import propose
            return 200, propose(str(body.get("project_id") or ""), body.get("rules"))
        if path == "/api/disclosure/rules/confirm":
            from core.disclosure.rules import confirm
            return 200, confirm(str(body.get("id") or ""), str(body.get("sha256") or ""))
        if path == "/api/disclosure/decision":
            decide(str(body.get("id") or ""), str(body.get("sha256") or ""),
                   str(body.get("action") or ""), indices=body.get("indices"), sections=body.get("sections"))
            return 200, {"ok": True}
        if path == "/api/disclosure/settings":
            return 200, save_settings(preview_enabled=body.get("preview_enabled"))
        if path == "/api/disclosure/close":
            chat = body.get("chat_id")
            if not isinstance(chat, str) or not chat:
                raise ValueError("choose a chat")
            close_chat(chat)
            return 200, {"ok": True}
    except KeyError:
        return 410, {"error": "preview_expired"}
    except ValueError as exc:
        return 409, {"error": str(exc)}
    return 404, {"error": "not_found"}
