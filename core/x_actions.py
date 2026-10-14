"""Actions on X that act publicly as the user: post, reply, quote, delete, like, repost, follow.

The money rule applies: the model can only PROPOSE an action (``propose``). Nothing goes to X until
the user approves that exact action in the app (``approve``, reached only through the owner-local
Settings route ``/api/x/activity``, never through a tool argument). Declined, expired and failed
actions are never retried on their own.

* A proposal freezes what will be sent (kind, text, target post or account) and its hash. Approval
  sends exactly that; nothing about it can change after it is shown.
* Targets are read from X when proposed (the post being liked or replied to, the account being
  followed), so the approval card shows what the action touches, not just an id.
* Every send counts against the user's monthly action limit (``core.x_usage``), counted under the
  same lock that marks the action as sending, so parallel approvals cannot run past the limit.
* Approval needs the action's hash (over the payload and a secret per-action nonce), which only the
  owner-local Settings panel is shown; the model's view of an action leaves it out. Post text with
  invisible format characters (direction marks, zero-width, tag characters) is refused, so the
  card shows exactly what will be posted.
* A send whose answer is lost (timeout, dropped connection, X server error) may still have reached
  X: it is marked ``unknown`` and never re-sent automatically; the card says to check X first.
* Proposals expire after 24 hours. At most 20 can wait at once.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import threading
import time
from typing import Any

from core import usage_quota, x_usage
from core.runtime_paths import data_path

_FILE = "x_actions.json"
EXPIRY_S = 24 * 3600
MAX_PENDING = 20
POST_QUOTA = "x.post"  # the daily posting cap in core/usage_quota
KINDS = ("post", "reply", "quote", "delete", "like", "unlike", "repost", "unrepost", "follow", "unfollow")
_TEXT_KINDS = {"post", "reply", "quote"}
_POST_KINDS = {"reply", "quote", "delete", "like", "unlike", "repost", "unrepost"}
_USER_KINDS = {"follow", "unfollow"}
SENDING_STALE_S = 600
_lock = threading.RLock()


def _hidden(ch: str) -> bool:
    """Characters that reorder or hide text (format controls, tag characters), so what the card
    shows would not be what X shows."""
    import unicodedata

    return unicodedata.category(ch) == "Cf" or 0xE0000 <= ord(ch) <= 0xE007F

_VERBS = {
    "post": "Post on X", "reply": "Reply to", "quote": "Quote a post by", "delete": "Delete your post",
    "like": "Like a post by", "unlike": "Remove your like from a post by", "repost": "Repost a post by",
    "unrepost": "Undo your repost of a post by", "follow": "Follow", "unfollow": "Unfollow",
}


def _result(ok: bool, status: str, message: str, action: dict[str, Any] | None = None):
    from core.x_api import XResult

    return XResult(ok, status, message, {"action": action} if action else {})


def _load() -> dict[str, Any]:
    path = data_path(_FILE)
    if not path.exists():
        return {"actions": {}}
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise RuntimeError("x actions store unreadable") from None
    if not isinstance(state, dict) or not isinstance(state.get("actions"), dict):
        raise RuntimeError("x actions store unreadable")
    return state


def _save(state: dict[str, Any]) -> None:
    path = data_path(_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, sort_keys=True, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def _expire(state: dict[str, Any], now: float) -> None:
    for action in state["actions"].values():
        if action.get("state") == "pending" and now - float(action.get("created_at") or 0) > EXPIRY_S:
            action["state"] = "expired"
            action["updated_at"] = now
        elif action.get("state") == "sending" and now - float(action.get("updated_at") or 0) > SENDING_STALE_S:
            # VOOL stopped mid-send: it may have reached X, so it is never sent again on its own.
            action["state"] = "unknown"
            action["updated_at"] = now
            action["message"] = "VOOL stopped while sending this. It may or may not have reached X: check X before trying again."
    # Keep the store small: the newest 200 finished actions.
    done = sorted((a for a in state["actions"].values() if a.get("state") != "pending"),
                  key=lambda a: float(a.get("updated_at") or 0))
    for old in done[:-200]:
        state["actions"].pop(old["id"], None)


def _digest(payload: dict[str, Any], nonce: str) -> str:
    """The approval hash: the frozen payload under a secret nonce that never leaves the store, so
    it can't be computed from the text alone; only the Settings panel is shown it."""
    return hashlib.sha256((nonce + json.dumps(payload, sort_keys=True)).encode("utf-8")).hexdigest()


def public(action: dict[str, Any], *, for_settings: bool = False) -> dict[str, Any]:
    """An action as shown. Only the Settings panel gets the hash that approval needs."""
    keep = ("id", "kind", "text", "state", "created_at", "updated_at", "target", "result", "message", "summary")
    shown = {k: action.get(k) for k in keep if action.get(k) not in (None, "", {})}
    if for_settings and action.get("state") == "pending":
        shown["hash"] = action.get("hash")
    return shown


def summary(action: dict[str, Any]) -> str:
    target = action.get("target") or {}
    kind = str(action.get("kind"))
    verb = _VERBS.get(kind, kind)
    if kind in _USER_KINDS:
        return f"{verb} @{target.get('username')}"
    if kind in {"post", "delete"}:
        return verb
    return f"{verb} @{target.get('author')}" if target.get("author") else verb


def propose(arguments: dict[str, Any], *, source_context: dict[str, Any] | None = None):
    """Freeze one action for the user to approve. Reads the target from X; sends nothing."""
    from core import x_api

    args = dict(arguments or {})
    kind = str(args.get("kind") or "").strip().lower()
    if kind not in KINDS:
        return _result(False, "invalid_arguments", f"kind must be one of: {', '.join(KINDS)}. Nothing was proposed.")
    saved = x_api._saved()
    account = saved.get("account") if isinstance(saved.get("account"), dict) else {}
    from core.x_setup import FIELDS

    if not account.get("id") or not all(saved.get(name) for name in FIELDS):
        return _result(False, "needs_account",
                       "Acting on X needs your own X account connected (the four account keys in Settings > API Keys > X). "
                       "Nothing was proposed.")
    payload: dict[str, Any] = {"kind": kind}
    target: dict[str, Any] = {}
    if kind in _TEXT_KINDS:
        text = str(args.get("text") or "").strip()
        if not text:
            return _result(False, "invalid_arguments", "Say what the post should say. Nothing was proposed.")
        from core.x_platform_policy import STANDARD_POST_LIMIT, weighted_length

        # X counts weighted characters (links 23, most emoji and CJK 2); a longer post needs a
        # paid X plan VOOL can't confirm, so it would fail at X only after the user approved it.
        if weighted_length(text) > STANDARD_POST_LIMIT:
            return _result(False, "invalid_arguments",
                           f"That post is {weighted_length(text)} characters as X counts them (links count as 23, most "
                           f"emoji as 2), over X's {STANDARD_POST_LIMIT}. Shorten it or split it into a thread. Nothing was proposed.")
        if any(_hidden(ch) for ch in text):
            return _result(False, "invalid_arguments", "That text has invisible characters that change how it reads, "
                           "so it could look different on X than on the approval card. Nothing was proposed.")
        payload["text"] = text
    try:
        if kind in _POST_KINDS:
            pid = x_api._post_id(args.get("post") or args.get("post_id") or "")
            read = x_api.post_details(pid)
            if not read.ok or not read.data.get("posts"):
                return _result(False, read.status, f"VOOL could not read that post from X, so nothing was proposed. {read.message}")
            post = read.data["posts"][0]
            if kind == "delete" and post.get("author_id") != str(account["id"]):
                return _result(False, "not_yours", "That post is not from your connected X account, so it can't be deleted. Nothing was proposed.")
            target = {"post_id": pid, "author": post.get("author"), "text": str(post.get("text") or "")[:500],
                      "url": post.get("url")}
            payload["post_id"] = pid
        if kind in _USER_KINDS:
            looked = x_api.lookup_users([str(args.get("username") or args.get("user") or "")])
            if not looked.ok or not looked.data.get("users"):
                return _result(False, looked.status, f"VOOL could not find that account on X, so nothing was proposed. {looked.message}")
            user = looked.data["users"][0]
            target = {"user_id": user["id"], "username": user["username"], "name": user["name"],
                      "followers": user.get("followers"), "url": user.get("url")}
            payload["user_id"] = user["id"]
    except x_api.XReadError as exc:
        return _result(False, exc.status, exc.message + " Nothing was proposed.")
    now = time.time()
    action = {
        "id": "xa_" + secrets.token_hex(8), "kind": kind, "text": payload.get("text", ""), "payload": payload,
        "nonce": (nonce := secrets.token_hex(16)), "hash": _digest(payload, nonce), "target": target, "state": "pending", "created_at": now, "updated_at": now,
        "account": {"id": str(account["id"]), "username": str(account.get("username") or "")},
        "session": str((source_context or {}).get("session_id") or ""),
    }
    action["summary"] = summary(action)
    with _lock:
        try:
            state = _load()
        except RuntimeError:
            return _result(False, "store_unreadable", "VOOL could not read its list of X actions. Nothing was proposed.")
        _expire(state, now)
        if sum(1 for a in state["actions"].values() if a.get("state") == "pending") >= MAX_PENDING:
            return _result(False, "busy", f"{MAX_PENDING} X actions are already waiting for approval. "
                           "Approve or decline some in Settings > API Keys > X first. Nothing was proposed.")
        state["actions"][action["id"]] = action
        _save(state)
    note = x_usage.action_refusal()
    message = (f"{action['summary']} is ready and waiting for your approval. Nothing has been sent to X. "
               "Approve or decline it in Settings > API Keys > X.")
    if note:
        message += " Note: " + note
    return _result(True, "awaiting_approval", message, public(action))


def _current() -> dict[str, Any]:
    """The store with expiry applied (and saved). Raises RuntimeError when it can't be read."""
    with _lock:
        state = _load()
        before = json.dumps(state, sort_keys=True)
        _expire(state, time.time())
        if json.dumps(state, sort_keys=True) != before:
            _save(state)
        return state


def pending() -> list[dict[str, Any]]:
    state = _current()
    return [public(a, for_settings=True) for a in sorted(state["actions"].values(), key=lambda a: float(a.get("created_at") or 0))
            if a.get("state") == "pending"]


def recent(limit: int = 10) -> list[dict[str, Any]]:
    state = _current()
    done = [a for a in state["actions"].values() if a.get("state") != "pending"]
    done.sort(key=lambda a: float(a.get("updated_at") or 0), reverse=True)
    return [public(a) for a in done[:limit]]


def status_for_model(action_id: str = "", *, source_context: dict[str, Any] | None = None):
    """The state of an action this chat proposed (the newest one when no id is given)."""
    try:
        state = _current()
    except RuntimeError:
        return _result(False, "store_unreadable", "VOOL could not read its list of X actions.")
    session = str((source_context or {}).get("session_id") or "")
    actions = [a for a in state["actions"].values() if str(a.get("session") or "") == session]
    if action_id:
        action = next((a for a in actions if a.get("id") == action_id), None)
    else:
        action = max(actions, key=lambda a: float(a.get("created_at") or 0), default=None)
    if not action:
        return _result(False, "not_found", "No such X action in this chat.")
    words = {"pending": "is waiting for your approval", "declined": "was declined; nothing was sent",
             "expired": "expired without approval; nothing was sent", "sent": "was sent",
             "failed": "failed; nothing was sent", "sending": "is being sent",
             "unknown": "may or may not have reached X (the answer was lost); check X before trying again"}
    text = f"{action.get('summary')} {words.get(action.get('state'), action.get('state'))}."
    if action.get("state") == "sent" and (action.get("result") or {}).get("url"):
        text += f" {action['result']['url']}"
    return _result(True, str(action.get("state")), text, public(action))


def decline(action_id: Any) -> dict[str, Any]:
    with _lock:
        state = _load()
        action = state["actions"].get(str(action_id or ""))
        if not action or action.get("state") != "pending":
            return {"ok": False, "state": "not_pending", "message": "That X action is no longer waiting for approval."}
        action.update(state="declined", updated_at=time.time(), message="Declined. Nothing was sent to X.")
        _save(state)
    return {"ok": True, "action": public(action), "message": "Declined. Nothing was sent to X."}


def _request_for(action: dict[str, Any]) -> tuple[str, str, dict[str, Any] | None]:
    payload, me = action["payload"], action["account"]["id"]
    kind = payload["kind"]
    if kind == "post":
        return "POST", "/2/tweets", {"text": payload["text"]}
    if kind == "reply":
        return "POST", "/2/tweets", {"text": payload["text"], "reply": {"in_reply_to_tweet_id": payload["post_id"]}}
    if kind == "quote":
        return "POST", "/2/tweets", {"text": payload["text"], "quote_tweet_id": payload["post_id"]}
    if kind == "delete":
        return "DELETE", f"/2/tweets/{payload['post_id']}", None
    if kind == "like":
        return "POST", f"/2/users/{me}/likes", {"tweet_id": payload["post_id"]}
    if kind == "unlike":
        return "DELETE", f"/2/users/{me}/likes/{payload['post_id']}", None
    if kind == "repost":
        return "POST", f"/2/users/{me}/retweets", {"tweet_id": payload["post_id"]}
    if kind == "unrepost":
        return "DELETE", f"/2/users/{me}/retweets/{payload['post_id']}", None
    if kind == "follow":
        return "POST", f"/2/users/{me}/following", {"target_user_id": payload["user_id"]}
    if kind == "unfollow":
        return "DELETE", f"/2/users/{me}/following/{payload['user_id']}", None
    raise ValueError(kind)


def _finish(action: dict[str, Any], **fields: Any) -> dict[str, Any]:
    """Record how a send ended. If the store can't be written, the outcome is still returned."""
    with _lock:
        try:
            state = _load()
            stored = state["actions"][action["id"]]
            stored.update(updated_at=time.time(), **fields)
            _save(state)
            return stored
        except (RuntimeError, OSError, KeyError):
            outcome = {**action, **fields, "updated_at": time.time()}
            outcome["message"] = str(fields.get("message") or "") + " (VOOL could not save this result.)"
            return outcome


def approve(action_id: Any, *, expected_hash: Any = None) -> dict[str, Any]:
    """The user approved this exact action in the app: send it once. Owner-local route only."""
    import urllib.error

    from core import x_api
    from core.remote_fetch_policy import CredentialRedirectRefusedError, RemoteFetchRefusedError, open_remote_url
    from core.secret_redaction import register_exact_secret
    from core.x_setup import FIELDS, OAuth1Keys, oauth1_authorization

    with _lock:
        try:
            state = _load()
        except RuntimeError:
            return {"ok": False, "state": "store_unreadable", "message": "VOOL could not read its list of X actions. Nothing was sent."}
        _expire(state, time.time())
        action = state["actions"].get(str(action_id or ""))
        if not action or action.get("state") != "pending":
            _save(state)
            return {"ok": False, "state": "not_pending",
                    "message": "That X action is no longer waiting for approval (sent, declined or expired). Nothing was sent."}
        if str(expected_hash or "") != action.get("hash") or _digest(action["payload"], str(action.get("nonce") or "")) != action.get("hash"):
            return {"ok": False, "state": "changed", "message": "That X action does not match what was shown. Nothing was sent."}
        saved = x_api._saved()
        account = saved.get("account") if isinstance(saved.get("account"), dict) else {}
        if not all(saved.get(name) for name in FIELDS) or str(account.get("id") or "") != action["account"]["id"]:
            return {"ok": False, "state": "account_changed",
                    "message": "The connected X account is not the one this action was prepared for. Nothing was sent."}
        refusal = x_usage.reserve_action()
        if refusal:
            return {"ok": False, "state": "usage_limit_reached", "message": refusal}
        publishes = action["kind"] in _TEXT_KINDS
        if publishes:
            # Posts, replies and quotes also keep the daily posting cap (core/usage_quota "x.post").
            day = usage_quota.consume_quota(POST_QUOTA, tier=usage_quota.active_tier())
            if not day.allowed:
                x_usage.unreserve_action()
                return {"ok": False, "state": "daily_limit_reached",
                        "message": f"VOOL has already posted {day.used} times on X today, its daily limit. "
                                   "Nothing was sent; you can approve this again tomorrow."}
        action.update(state="sending", updated_at=time.time())
        try:
            _save(state)
        except OSError:
            x_usage.unreserve_action()
            if publishes:
                usage_quota.release_quota(POST_QUOTA)
            return {"ok": False, "state": "store_unreadable", "message": "VOOL could not save this X action. Nothing was sent."}
    method, path, body = _request_for(action)
    keys = OAuth1Keys(**{name: saved[name] for name in FIELDS})
    for name in FIELDS:
        register_exact_secret(saved[name])
    url = x_api.API_ORIGIN + path
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Authorization": oauth1_authorization(method, url, keys), "Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    try:
        from core.effect_gateway import named_background_effect_scope

        with named_background_effect_scope("x.approved_action"):
            response = open_remote_url(url, data=data, headers=headers, method=method, timeout=x_api.REQUEST_TIMEOUT_S,
                                       no_proxy=True, provider_id="x.api", keyed_or_keyless="keyed",
                                       credential_headers=("Authorization",))
            raw = response.read(256 * 1024) or b""
    except RemoteFetchRefusedError:
        x_usage.unreserve_action()
        if publishes:
            usage_quota.release_quota(POST_QUOTA)
        action = _finish(action, state="pending", message="VOOL is not allowed to go online right now (for example Local Only is on). Nothing was sent; you can approve again later.")
        return {"ok": False, "state": "refused", "message": action["message"], "action": public(action)}
    except CredentialRedirectRefusedError:
        # X received the request before it answered with a redirect, so it may have acted on it.
        action = _finish(action, state="unknown", message="X answered with a redirect that VOOL did not follow. "
                         "It may or may not have gone out: check X before trying again.")
        return {"ok": False, "state": "unknown", "message": action["message"], "action": public(action)}
    except urllib.error.HTTPError as exc:
        try:
            err_body = json.loads((exc.read(64 * 1024) or b"{}").decode("utf-8"))
        except Exception:
            err_body = None
        err = x_api._http_failure(int(exc.code or 0), err_body)
        message = err.message
        if err.status == "plan_refused":
            message += (" To act on X, your X app needs \"Read and write\" permission (developer.x.com > your app > "
                        "User authentication settings); then generate new Access Tokens and reconnect X in Settings.")
        if int(exc.code or 0) >= 500 or 300 <= int(exc.code or 0) < 400:
            # X may have done it before failing to answer, or before redirecting.
            action = _finish(action, state="unknown", message=message + " It may or may not have gone out: check X before trying again.")
            return {"ok": False, "state": "unknown", "message": action["message"], "action": public(action)}
        action = _finish(action, state="failed", message=message + " X did not do it.")
        return {"ok": False, "state": err.status, "message": action["message"], "action": public(action)}
    except Exception as exc:
        if _never_connected(exc):
            action = _finish(action, state="failed", message="Could not reach X from this machine. Nothing was sent.")
        else:
            action = _finish(action, state="unknown",
                             message="X's answer was lost. It may or may not have gone out: check X before trying again.")
        return {"ok": False, "state": action["state"], "message": action["message"], "action": public(action)}
    try:
        answer = json.loads(raw.decode("utf-8")) if raw else {}
    except (UnicodeDecodeError, ValueError):
        answer = {}
    result: dict[str, Any] = {}
    data_part = answer.get("data") if isinstance(answer, dict) else None
    if isinstance(data_part, dict) and action["kind"] in _TEXT_KINDS and x_api._ID_RE.fullmatch(str(data_part.get("id") or "")):
        handle = str(action["account"].get("username") or "")
        result = {"post_id": str(data_part["id"]),
                  "url": x_api.post_url(handle if x_api._HANDLE_RE.fullmatch(handle) else "i", str(data_part["id"]))}
    elif isinstance(data_part, dict):
        result = {k: v for k, v in data_part.items() if isinstance(v, (bool, int)) and k != "url"}
    action = _finish(action, state="sent", result=result, message="Sent to X.")
    return {"ok": True, "action": public(action), "message": f"{action['summary']}: sent." + (f" {result['url']}" if result.get("url") else "")}




def _never_connected(exc: BaseException) -> bool:
    """True only when the request provably never reached X (no connection was made)."""
    import socket
    import urllib.error

    reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
    return isinstance(reason, (ConnectionRefusedError, socket.gaierror))


# --------------------------------------------------------------------------- Settings route


def overview() -> dict[str, Any]:
    """What the Settings X panel shows: usage, limits, actions waiting and the last few results."""
    try:
        return {"ok": True, "usage": x_usage.snapshot(), "pending": pending(), "recent": recent(), "store": "ok"}
    except RuntimeError:
        return {"ok": True, "usage": x_usage.snapshot(), "pending": [], "recent": [], "store": "unreadable",
                "store_message": "VOOL could not read its list of X actions, so anything waiting can't be shown "
                                 "or sent. Nothing will be sent until this is fixed."}


def handle(body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    """One owner-local Settings action on X usage and approvals → (HTTP status, payload)."""
    action = body.get("action") if isinstance(body, dict) else None
    try:
        if action == "overview":
            return 200, overview()
        if action == "set_limits":
            out = x_usage.set_limits(monthly_reads=body.get("monthly_reads", "keep"),
                                     monthly_actions=body.get("monthly_actions", "keep"))
            return (200 if out.get("ok") else 400), out
        if action == "reset_usage":
            return 200, x_usage.reset_meter()
        if action == "approve":
            out = approve(body.get("action_id"), expected_hash=body.get("hash"))
            return (200 if out.get("ok") else 400), out
        if action == "decline":
            out = decline(body.get("action_id"))
            return (200 if out.get("ok") else 400), out
    except RuntimeError:
        return 400, {"ok": False, "state": "store_unreadable", "message": "VOOL could not read its list of X actions. Nothing was sent."}
    return 400, {"ok": False, "state": "invalid", "message": "Unknown X step."}


__all__ = ["KINDS", "approve", "decline", "handle", "overview", "pending", "propose", "public", "recent", "status_for_model"]
