"""The owner-local door for My routines (GET /api/routines..., POST /api/routines/<action>).

Only this computer's own pages reach it (the same check as other Settings writes: loopback, same
origin, JSON). Nothing here calls a model or the network. Every change the person makes goes through
a step they confirm: saving a routine, running the exact list they looked at, going back to a
version, removing a routine.
"""
from __future__ import annotations

import base64
import binascii
from typing import Any

from core.routines.model import RoutineError, valid_id

_NO_STORE = {"Cache-Control": "no-store"}
MAX_IMPORT_BYTES = 64 * 1024


def _only(body: dict[str, Any], keys: set[str]) -> None:
    if set(body) - keys:
        raise RoutineError("bad_fields", "Check the request and try again.")


def _confirmed(body: dict[str, Any], message: str) -> None:
    if body.get("confirm") is not True:
        raise RoutineError("confirmation_required", message, 409)


#: What the page shows beside a routine (see _view); sent back with it, it is set aside, never stored.
_VIEW_ONLY = {"version", "words", "rename_words", "into_words", "uses_receipts", "last_run"}


def _routine_in(body: dict[str, Any]) -> dict[str, Any]:
    raw = body.get("routine")
    if not isinstance(raw, dict):
        raise RoutineError("bad_routine", "That routine could not be read.")
    return {key: value for key, value in raw.items() if key not in _VIEW_ONLY}


def _view(routine: dict[str, Any]) -> dict[str, Any]:
    from core.routines.model import uses_receipts
    from core.routines.runner import receipts_for
    from core.routines.store import describe, rule_words

    runs = receipts_for(routine["id"])
    return {
        **routine,
        "words": describe(routine),
        "rename_words": rule_words(routine["rename"]),
        "into_words": rule_words(routine["into"]),
        "uses_receipts": uses_receipts(routine),
        "last_run": runs[0] if runs else None,
    }


def _folders(under: str) -> dict[str, Any]:
    """The three places a routine may work, or the folders inside one of them (one level)."""
    from core.routines.folders import admit, safe_roots

    roots = [{"name": root.name, "path": str(root)} for root in safe_roots() if root.is_dir()]
    if not under:
        return {"roots": roots, "folders": []}
    folder = admit(under)
    entries = []
    try:
        for entry in sorted(folder.iterdir(), key=lambda item: item.name.casefold()):
            if entry.name.startswith(".") or entry.is_symlink() or not entry.is_dir():
                continue
            entries.append({"name": entry.name, "path": str(entry)})
            if len(entries) >= 200:
                break
    except OSError:
        raise RoutineError("folder_missing", "That folder could not be opened.") from None
    return {"roots": roots, "folder": str(folder), "folders": entries}


def overview() -> dict[str, Any]:
    from core.routines import receipts, store, watch
    from core.routines.model import FILE_TOKENS, MAX_FILES_LIMIT, NAME_LIMIT, RECEIPT_TOKENS, RESULT_TOKENS

    return {
        "routines": [_view(routine) for routine in store.all_routines()],
        "staged": [dict(routine, words=store.describe(routine)) for routine in store.staged()],
        "watch": watch.refresh(),
        "receipts_here": receipts.readable_here(),
        "roots": _folders("")["roots"],
        "tokens": {"file": list(FILE_TOKENS), "receipt": list(RECEIPT_TOKENS), "result": list(RESULT_TOKENS)},
        "limits": {"max_files": MAX_FILES_LIMIT, "name": NAME_LIMIT},
    }


def names() -> dict[str, Any]:
    """Only each routine's id and name: what the command palette lists, without touching any folder."""
    from core.routines import store

    return {"routines": [{"id": routine["id"], "name": routine["name"]} for routine in store.all_routines()]}


def action(name: str, body: dict[str, Any]) -> dict[str, Any] | bytes:
    from core.routines import share, store, watch
    from core.routines.infer import propose
    from core.routines.model import validate
    from core.routines.plan import plan
    from core.routines.runner import receipts_for, run

    if name == "watch_start":
        _only(body, {"folder"})
        return {"watch": watch.start(str(body.get("folder") or ""))}
    if name == "watch_stop":
        _only(body, set())
        return {"watch": watch.stop()}
    if name == "watch_discard":
        _only(body, set())
        watch.forget()
        return {"watch": None}
    if name == "propose":
        _only(body, set())
        state = watch.current()
        if state is None:
            raise RoutineError("no_watch", "There is nothing to learn from yet. Start a watch and do the task once.", 409)
        return {"proposal": propose(state)}
    if name == "try":
        _only(body, {"routine"})
        raw = _routine_in(body)
        routine = validate(dict(raw, name=raw.get("name") or "Try"), require_folder=True)
        return {"plan": plan(routine)}
    if name == "save":
        _only(body, {"routine", "confirm"})
        _confirmed(body, "Look at the routine, then choose Save.")
        saved = store.save(_routine_in(body))
        watch.forget()  # What the watch saw is not kept once the routine is saved.
        return {"routine": _view(saved)}
    if name == "update":
        _only(body, {"routine", "confirm"})
        _confirmed(body, "Look at the changes, then choose Save.")
        raw = _routine_in(body)
        store.get(str(raw.get("id") or ""))  # Only a routine that exists is updated here.
        return {"routine": _view(store.save(raw))}
    if name == "plan":
        _only(body, {"id"})
        return {"plan": plan(store.get(str(body.get("id") or "")))}
    if name == "run":
        _only(body, {"id", "digest", "confirm"})
        _confirmed(body, "Look at the list, then choose Run.")
        return {"receipt": run(store.get(str(body.get("id") or "")), str(body.get("digest") or ""))}
    if name == "runs":
        _only(body, {"id"})
        routine = store.get(str(body.get("id") or ""))
        return {"runs": receipts_for(routine["id"])}
    if name == "history":
        _only(body, {"id"})
        return {"versions": store.history(str(body.get("id") or ""))}
    if name == "rollback":
        _only(body, {"id", "version", "confirm"})
        _confirmed(body, "Choose the version, then confirm going back to it.")
        return {"routine": _view(store.rollback(str(body.get("id") or ""), body.get("version")))}
    if name == "remove":
        _only(body, {"id", "confirm"})
        _confirmed(body, "Confirm that you want to remove this routine.")
        store.remove(str(body.get("id") or ""))
        return {"removed": True}
    if name == "share_preview":
        _only(body, {"id"})
        return {"share": share.preview(store.get(str(body.get("id") or "")))}
    if name == "share":
        _only(body, {"id", "sha256"})
        return share.export(store.get(str(body.get("id") or "")), str(body.get("sha256") or ""))
    if name == "import":
        _only(body, {"data"})
        encoded = body.get("data")
        if not isinstance(encoded, str) or len(encoded) > ((MAX_IMPORT_BYTES + 2) // 3) * 4:
            raise RoutineError("bad_share", "That file is not a routine shared from VOOL.")
        try:
            data = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error):
            raise RoutineError("bad_share", "That file is not a routine shared from VOOL.") from None
        staged = store.stage(share.read(data))
        return {"staged": dict(staged, words=store.describe(staged))}
    if name == "staged_save":
        _only(body, {"id", "folder", "name", "confirm"})
        _confirmed(body, "Choose the folder, then choose Save.")
        staged_id = str(body.get("id") or "")
        if not any(item["id"] == staged_id for item in store.staged()):
            raise RoutineError("not_found", "That routine is not there any more.", 404)
        routine = next(item for item in store.staged() if item["id"] == staged_id)
        routine = dict(routine, folder=str(body.get("folder") or ""))
        if body.get("name"):
            routine["name"] = str(body.get("name"))
        saved = store.save(routine)
        store.unstage(staged_id)
        return {"routine": _view(saved)}
    if name == "staged_discard":
        _only(body, {"id"})
        store.unstage(str(body.get("id") or ""))
        return {"removed": True}
    raise RoutineError("not_found", "That action is not available.", 404)


def _error(exc: RoutineError):
    from core.web.api.service import json_response

    return json_response(exc.status, {"ok": False, "error": exc.code, "message": exc.message}, headers=_NO_STORE)


def post(path: str, body: dict[str, Any], headers: dict[str, Any] | None, client_host: str):
    from core.web.api.service import ApiResponse, _credential_page_refusal, json_response

    refusal = _credential_page_refusal(headers, client_host)
    if refusal:
        return json_response(415 if refusal == "content_type" else 403, {"ok": False, "error": "owner_request_required"},
                             headers=_NO_STORE)
    name = path.removeprefix("/api/routines/")
    try:
        result = action(name, body if isinstance(body, dict) else {})
    except RoutineError as exc:
        return _error(exc)
    except (OSError, ValueError, RuntimeError):
        return json_response(503, {"ok": False, "error": "storage_unavailable",
                                   "message": "Your routines could not be opened safely. Try again."}, headers=_NO_STORE)
    if isinstance(result, bytes):
        routine_id = str(body.get("id") or "")
        filename = ("routine-" + routine_id + ".zip") if valid_id(routine_id) else "routine.zip"
        return ApiResponse(200, "application/zip", result, headers={**_NO_STORE, "X-Content-Type-Options": "nosniff",
                                                                     "Content-Disposition": f'attachment; filename="{filename}"'})
    return json_response(200, {"ok": True, **result}, headers=_NO_STORE)


def get(path: str, query: dict[str, Any] | None, client_host: str, headers: dict[str, Any] | None):
    from core.web.api.service import _credential_page_refusal, json_response

    lowered = {str(k).lower(): str(v) for k, v in (headers or {}).items()}
    if _credential_page_refusal({**lowered, "content-type": "application/json"}, client_host):
        return json_response(403, {"ok": False, "error": "owner_request_required"}, headers=_NO_STORE)
    try:
        if path == "/api/routines":
            return json_response(200, {"ok": True, **overview()}, headers=_NO_STORE)
        if path == "/api/routines/folders":
            under = query.get("under", "") if isinstance(query, dict) else ""
            if isinstance(under, list):
                under = under[0] if under else ""
            return json_response(200, {"ok": True, **_folders(str(under or ""))}, headers=_NO_STORE)
        if path == "/api/routines/names":
            return json_response(200, {"ok": True, **names()}, headers=_NO_STORE)
        if path == "/api/routines/watch":
            from core.routines import watch

            return json_response(200, {"ok": True, "watch": watch.refresh()}, headers=_NO_STORE)
    except RoutineError as exc:
        return _error(exc)
    except (OSError, ValueError, RuntimeError):
        return json_response(503, {"ok": False, "error": "storage_unavailable",
                                   "message": "Your routines could not be opened safely. Try again."}, headers=_NO_STORE)
    return json_response(404, {"ok": False, "error": "not_found"}, headers=_NO_STORE)


__all__ = ["action", "get", "names", "overview", "post"]
