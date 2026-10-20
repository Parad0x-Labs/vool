"""Owner-local door for household records. No model, network or persisted upload staging."""
from __future__ import annotations

import base64
import binascii
import sqlite3

from core.my_things.store import MAX_FILE_BYTES, ThingsError, ThingsStore, file_payload


def _only(body, keys):
    if set(body) - set(keys):
        raise ThingsError("bad_fields", "Check the request and try again.")


def upload(body, *, sanitize=False):
    if not isinstance(body, dict) or set(body) - {"name", "kind", "data"}:
        raise ThingsError("bad_file", "Choose the file again.")
    name, kind, encoded = body.get("name"), body.get("kind"), body.get("data")
    if not isinstance(encoded, str) or len(encoded) > ((MAX_FILE_BYTES + 2) // 3) * 4:
        raise ThingsError("file_size", "Choose a file smaller than 8 MB.")
    try:
        data = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        raise ThingsError("bad_file", "This file could not be read. Choose it again.") from None
    return file_payload(name, data, kind, strip_metadata=sanitize)


def _download(data, name):
    from core.web.api.service import ApiResponse

    return ApiResponse(200, "application/zip", data, headers={"Cache-Control": "no-store", "Content-Disposition": 'attachment; filename="' + name + '"', "X-Content-Type-Options": "nosniff"})


def action(path, body):
    from core.web.api.service import json_response

    store = ThingsStore()
    name = path.removeprefix("/api/my-things/")
    if name == "preferences":
        _only(body, {"enabled"})
        result = store.set_enabled(body.get("enabled"))
    elif name == "save":
        _only(body, {"fields", "id", "revision", "confirm", "file"})
        if body.get("confirm") is not True:
            raise ThingsError("confirmation_required", "Review the details, then choose Save.", 409)
        files = [upload(body["file"], sanitize=True)] if isinstance(body.get("file"), dict) else None
        if "file" in body and files is None:
            raise ThingsError("bad_file", "Choose the file again.")
        result = {"thing": store.save(body.get("fields"), thing_id=body.get("id", ""), revision=body.get("revision", 0), files=files)}
    elif name == "attach":
        _only(body, {"id", "revision", "file"})
        if not isinstance(body.get("file"), dict):
            raise ThingsError("bad_file", "Choose the file again.")
        result = {"thing": store.attach(body.get("id"), body.get("revision"), upload(body["file"], sanitize=True))}
    elif name == "delete":
        _only(body, {"id", "revision", "confirm"})
        if body.get("confirm") is not True:
            raise ThingsError("confirmation_required", "Confirm that you want to delete this thing and its files.", 409)
        result = store.delete(body.get("id"), body.get("revision"))
    elif name == "forget":
        _only(body, {"target", "confirm", "expected"})
        result = store.forget(body.get("target"), confirm=body.get("confirm") is True, expected=body.get("expected"))
    elif name == "file":
        _only(body, {"id", "file_id"})
        from urllib.parse import quote

        from core.web.api.service import ApiResponse

        record = store.get(body.get("id"), with_files=True)
        file = next((file for file in record["files"] if file["id"] == body.get("file_id")), None)
        if file is None:
            raise ThingsError("missing", "This file is no longer attached.", 404)
        return ApiResponse(200, "application/octet-stream", base64.b64decode(file["data"], validate=True), headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "Content-Disposition": "attachment; filename*=UTF-8''" + quote(file["name"], safe="")})
    elif name == "export":
        _only(body, set())
        return _download(store.export(), "my-things.zip")
    elif name == "read":
        _only(body, {"file"})
        from core.my_things.reading import read_local

        if not store.preferences()["enabled"]:
            raise ThingsError("disabled", "My things is turned off.", 409)
        file = upload(body.get("file", {}))
        result = {"reading": read_local(base64.b64decode(file["data"]), file["name"])}
    elif name == "propose":
        _only(body, {"file"})
        from core.my_things.reading import proposal

        if not store.preferences()["enabled"]:
            raise ThingsError("disabled", "My things is turned off.", 409)
        file = upload(body.get("file", {}))
        result = proposal(base64.b64decode(file["data"]), file["name"])
    elif name == "match":
        _only(body, {"file"})
        from core.my_things.reading import match_photo

        file = upload(body.get("file", {}))
        result = match_photo(base64.b64decode(file["data"]), file["name"], store.list())
    elif name == "prepare":
        _only(body, {"id", "kind", "issue", "observed", "arrived"})
        from core.my_things.prepare import prepare

        record = store.get(body.get("id"), with_files=True)
        result = prepare(record, kind=body.get("kind"), issue=body.get("issue", ""), observed=body.get("observed", ""), arrived=body.get("arrived", []))
    elif name == "packet":
        _only(body, {"id", "kind", "issue"})
        from core.my_things.prepare import packet

        return _download(packet(store.get(body.get("id"), with_files=True), kind=body.get("kind"), issue=body.get("issue", "")), "request-draft.zip")
    elif name == "manual":
        _only(body, {"id", "url", "confirm"})
        from core.my_things.manuals import preview

        result = preview(body.get("id"), body.get("url"), confirmed=body.get("confirm"))
    else:
        raise ThingsError("not_found", "That action is not available.", 404)
    return json_response(200, {"ok": True, **result}, headers={"Cache-Control": "no-store"})


def post(path, body, headers, client_host):
    from core.web.api.service import _credential_page_refusal, json_response

    refusal = _credential_page_refusal(headers, client_host)
    if refusal:
        return json_response(415 if refusal == "content_type" else 403, {"ok": False, "error": "owner_request_required"}, headers={"Cache-Control": "no-store"})
    try:
        return action(path, body)
    except ThingsError as exc:
        return json_response(exc.status, {"ok": False, "error": exc.code, "message": exc.message}, headers={"Cache-Control": "no-store"})
    except (OSError, ValueError, sqlite3.Error, RuntimeError):
        return json_response(503, {"ok": False, "error": "storage_unavailable", "message": "Your things could not be opened safely. Try again."}, headers={"Cache-Control": "no-store"})


def get(path, client_host, headers):
    from core.web.api.service import _credential_page_refusal, json_response

    lowered = {str(k).lower(): str(v) for k, v in (headers or {}).items()}
    if _credential_page_refusal({**lowered, "content-type": "application/json"}, client_host):
        return json_response(403, {"ok": False, "error": "owner_request_required"}, headers={"Cache-Control": "no-store"})
    try:
        store = ThingsStore()
        if path == "/api/my-things/preferences":
            result = store.preferences()
        elif path == "/api/my-things":
            result = {"things": store.list()}
        else:
            raise ThingsError("not_found", "That view is not available.", 404)
        return json_response(200, {"ok": True, **result}, headers={"Cache-Control": "no-store"})
    except ThingsError as exc:
        return json_response(exc.status, {"ok": False, "error": exc.code, "message": exc.message}, headers={"Cache-Control": "no-store"})
    except (OSError, ValueError, sqlite3.Error, RuntimeError):
        return json_response(503, {"ok": False, "error": "storage_unavailable", "message": "Your things could not be opened safely. Try again."}, headers={"Cache-Control": "no-store"})
