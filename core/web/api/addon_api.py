"""Owner-local add-on review gestures, never a model tool or caller-selected URL."""
from __future__ import annotations

from urllib.parse import urlsplit

from core import addon_store
from core.eyebrow_client import AddonError


def handle_addon_post(body: dict, headers: dict, client_host: str) -> tuple[int, dict]:
    if client_host not in {"127.0.0.1", "::1", "localhost"}:
        return 403, {"error": "owner_local_required", "message": "Use the local VOOL app to manage add-ons."}
    lower = {str(k).lower(): str(v) for k, v in headers.items()}
    if "application/json" not in lower.get("content-type", "").lower():
        return 415, {"error": "json_required", "message": "A JSON request is required."}
    origin = lower.get("origin", "")
    host = lower.get("host", "")
    if origin and (urlsplit(origin).netloc != host or urlsplit(origin).scheme not in {"http", "https"}):
        return 403, {"error": "cross_origin", "message": "Cross-origin add-on changes are refused."}
    if lower.get("sec-fetch-site") == "cross-site":
        return 403, {"error": "cross_origin", "message": "Cross-site add-on changes are refused."}
    action = body.get("action")
    fields = {
        "save_key": {"action", "value"}, "remove_key": {"action"}, "test_key": {"action"},
        "scan": {"action", "id", "approved"}, "install": {"action", "review_id", "accepted", "risk_override", "risk_acknowledged"},
        "reject": {"action", "review_id"}, "review": {"action", "review_id"},
        "local_check": {"action", "id"}, "local_report": {"action", "id"},
        "github_search": {"action", "query"},
        "github_repository": {"action", "repository"},
        "github_inspect": {"action", "repository", "ref", "path"},
    }
    if not isinstance(action, str) or action not in fields or set(body) - fields[action]:
        return 400, {"error": "invalid_action", "message": "Unknown action or unexpected fields."}
    try:
        if action.startswith('github_'):
            from core import addon_discovery
            if action == 'github_search':
                result = addon_discovery.search(str(body.get('query', '')))
            elif action == 'github_repository':
                result = addon_discovery.repository(str(body.get('repository', '')))
                result.pop('tree', None)
            else:
                result = addon_discovery.inspect(str(body.get('repository', '')), str(body.get('ref', '')), str(body.get('path', '')))
        elif action in {"save_key", "remove_key", "test_key"}:
            result = addon_store.security_action(action, body.get("value", ""))
        elif action == "local_check":
            result = addon_store.local_check(str(body.get("id", "")))
        elif action == "local_report":
            result = addon_store.local_report_saved(str(body.get("id", "")))
        elif action == "scan":
            result = addon_store.prepare(str(body.get("id", "")), approved=body.get("approved") is True)
        elif action == "install":
            result = addon_store.install_review(str(body.get("review_id", "")), accepted=body.get("accepted") is True,
                                               risk_override=body.get("risk_override") is True,
                                               risk_acknowledged=body.get("risk_acknowledged") is True)
        elif action == "review":
            result = addon_store.review_saved(str(body.get("review_id", "")))
        else:
            result = addon_store.reject(str(body.get("review_id", "")))
        return 200, result
    except AddonError as exc:
        return exc.status, {"ok": False, "error": exc.code, "message": exc.message}
    except Exception:
        # Never echo transport errors, credential values or personal storage paths.
        return 500, {"ok": False, "error": "addon_operation_failed", "message": "The add-on operation could not finish. Check storage access and review its status before retrying."}
