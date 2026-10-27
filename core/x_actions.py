"""Owner-approved X requests: public actions and private bookmark reads and changes.

The money rule applies: the model can only PROPOSE an action (``propose``). Nothing goes to X until
the user approves that exact action in the app (``approve``, reached only through the owner-local
Settings route ``/api/x/activity``, never through a tool argument). Declined, expired and failed
actions are never retried on their own.

* A proposal freezes what will be sent (kind, text, target post or account) and its hash. Approval
  sends exactly that; nothing about it can change after it is shown.
* Public-action targets are read from X when proposed (the post being liked or replied to, the account being
  followed), so the approval card shows what the action touches, not just an id. Bookmark targets
  are frozen locally: even reading a bookmark page waits for Settings approval.
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
import tempfile
import threading
import time
from contextlib import contextmanager, suppress
from typing import Any

from core import usage_quota, x_usage
from core.runtime_paths import data_path

_FILE = "x_actions.json"
EXPIRY_S = 24 * 3600
MAX_PENDING = 20
POST_QUOTA = "x.post"  # the daily posting cap in core/usage_quota
KINDS = ("post", "reply", "quote", "delete", "like", "unlike", "repost", "unrepost", "follow", "unfollow",
         "hide_reply", "unhide_reply", "mute", "unmute", "dm", "list_create", "list_add", "list_remove", "bookmark_read", "bookmark_add", "bookmark_remove")
_TEXT_KINDS = {"post", "reply", "quote"}  # public posts: X's 280 and the daily posting cap
_POST_KINDS = {"reply", "quote", "delete", "like", "unlike", "repost", "unrepost", "hide_reply", "unhide_reply"}
_USER_KINDS = {"follow", "unfollow", "mute", "unmute", "dm", "list_add", "list_remove"}
_LIST_KINDS = {"list_add", "list_remove"}
_BOOKMARK_KINDS = {"bookmark_read", "bookmark_add", "bookmark_remove"}
_BOOKMARK_SIGN_IN = "Sign in for bookmarks in Settings > API Keys > X."
# Actions whose provider answer can be private: their results are sealed (core.x_private_calls).
_SEALED_RESULT_KINDS = {"api", "bookmark_read"}
MAX_DM = 10_000
SENDING_STALE_S = 600
LOCK_WAIT_S = 10.0  # how long a step waits for another VOOL process to finish with the list
_lock = threading.RLock()
_lock_depth = threading.local()


@contextmanager
def _state_lock():
    """This process's lock plus the list's lock file. Another VOOL process holds it only for one
    read and write, so a step waits briefly for it; LockUnavailable when it stays taken."""
    from core.cross_process_lock import LockUnavailable, PublicationLock

    with _lock:
        if getattr(_lock_depth, 'held', False):
            yield
            return
        deadline = time.monotonic() + LOCK_WAIT_S
        while True:
            held = PublicationLock(data_path(_FILE).with_suffix('.lock'))
            try:
                held.__enter__()
                break
            except LockUnavailable:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.02)
        _lock_depth.held = True
        try:
            yield
        finally:
            _lock_depth.held = False
            held.__exit__(None, None, None)


def _hidden(ch: str) -> bool:
    """Characters that reorder or hide text (format controls, tag characters), so what the card
    shows would not be what X shows."""
    import unicodedata

    return unicodedata.category(ch) == "Cf" or 0xE0000 <= ord(ch) <= 0xE007F

_VERBS = {
    "post": "Post on X", "reply": "Reply to", "quote": "Quote a post by", "delete": "Delete your post",
    "like": "Like a post by", "unlike": "Remove your like from a post by", "repost": "Repost a post by",
    "unrepost": "Undo your repost of a post by", "follow": "Follow", "unfollow": "Unfollow",
    "hide_reply": "Hide a reply by", "unhide_reply": "Unhide a reply by", "mute": "Mute", "unmute": "Unmute",
    "dm": "Send a direct message to", "list_create": "Create the", "list_add": "Add", "list_remove": "Remove",
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
    """Replace the list atomically, readable by this account only, and on disk (file and folder)
    before returning: a "sending" mark must survive a crash so a sent action is never sent again."""
    path = data_path(_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".x_actions.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump(state, output, sort_keys=True, indent=1)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(name, 0o600)
        os.replace(name, path)
    except BaseException:
        with suppress(OSError):
            os.unlink(name)
        raise
    if os.name != "nt":
        folder = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(folder)
        finally:
            os.close(folder)


def _expire(state: dict[str, Any], now: float) -> None:
    """Apply expiry and recovery. Callers hold the state lock, so an action still "preparing" here
    was left by a VOOL that stopped while saving it: it was never sendable and is closed."""
    for action in state["actions"].values():
        if action.get("state") == "pending" and now - float(action.get("created_at") or 0) > EXPIRY_S:
            action["state"] = "expired"
            action["updated_at"] = now
            if not _erase_api_request(action):
                action["cleanup_pending"] = True
        elif action.get("state") == "preparing":
            action.update(state="failed", updated_at=now,
                          message="VOOL stopped while saving this request. Nothing was sent.")
            if not _erase_api_request(action):
                action["cleanup_pending"] = True
        elif action.get("state") == "sending" and now - float(action.get("updated_at") or 0) > SENDING_STALE_S:
            # VOOL stopped mid-send: it may have reached X, so it is never sent again on its own.
            action["state"] = "unknown"
            action["updated_at"] = now
            action["message"] = "VOOL stopped while sending this. It may or may not have reached X: check X before trying again."
            if not _erase_api_request(action):
                action["cleanup_pending"] = True
    # Keep the store small: the newest 200 finished actions, with their private copies erased first.
    done = sorted((a for a in state["actions"].values() if a.get("state") not in {"pending", "preparing", "sending"}),
                  key=lambda a: float(a.get("updated_at") or 0))
    for old in done[:-200]:
        if not _erase_api_private(old):
            old["cleanup_pending"] = True
            continue
        state["actions"].pop(old["id"], None)
    _sweep_private(state)


def _digest(payload: dict[str, Any], nonce: str) -> str:
    """The approval hash: the frozen payload under a secret nonce that never leaves the store, so
    it can't be computed from the text alone; only the Settings panel is shown it."""
    return hashlib.sha256((nonce + json.dumps(payload, sort_keys=True)).encode("utf-8")).hexdigest()


def public(action: dict[str, Any], *, for_settings: bool = False, include_result: bool = False) -> dict[str, Any]:
    """An action as shown. Only the Settings panel gets the hash that approval needs. A documented
    X request is decrypted only for the Settings approval card, and its result only for the owner
    who approved it and the chat that proposed it (``include_result``)."""
    keep = ("id", "kind", "text", "state", "created_at", "updated_at", "target", "result", "message", "summary", "cleanup_pending")
    shown = {k: action.get(k) for k in keep if action.get(k) not in (None, "", {})}
    if action.get("kind") in _BOOKMARK_KINDS:
        shown["account"] = action["account"]
        if action["kind"] == "bookmark_read":
            shown["max_results"] = action["payload"]["max_results"]
            shown["next_page"] = bool(action["payload"].get("next_token"))
    if action.get("kind") == "api" and (action.get("payload") or {}).get("operation_id"):
        # A public catalog name, so Settings can show the feature in the owner's language.
        shown["operation_id"] = action["payload"]["operation_id"]
    if action.get("kind") in _SEALED_RESULT_KINDS:
        _show_api_private(action, shown, preview=for_settings and action.get("state") == "pending",
                          include_result=include_result)
    if for_settings and action.get("state") == "pending":
        shown["hash"] = action.get("hash")
    if for_settings:
        from core.x_platform_policy import weighted_length

        shown.update(account=action.get("account"), feedback_scope=action.get("feedback_scope", "global"),
                     weighted_length=weighted_length(str(action.get("text") or "")))
    return shown


def summary(action: dict[str, Any]) -> str:
    target = action.get("target") or {}
    kind = str(action.get("kind"))
    verb = _VERBS.get(kind, kind)
    if kind in _BOOKMARK_KINDS:
        verbs = {"bookmark_read": "Read X bookmarks", "bookmark_add": "Add X bookmark", "bookmark_remove": "Remove X bookmark"}
        return verbs[kind] + (" " + str(target.get("post_id")) if target.get("post_id") else "")
    if kind in _LIST_KINDS:
        where = f"your list \"{target['list_name']}\"" if target.get("list_name") else "your list"
        return f"{verb} {_who(target)} {'to' if kind == 'list_add' else 'from'} {where}"
    if kind in _USER_KINDS:
        return f"{verb} {_who(target)}"
    if kind == "list_create":
        line = f"{verb} {'private' if target.get('private') else 'public'} list \"{target.get('name')}\""
        return line + (f", described as \"{target['description']}\"" if target.get("description") else "")
    if kind in {"post", "delete"}:
        return verb
    return f"{verb} @{target.get('author')}" if target.get("author") else verb


def _replies_to_you(post: dict[str, Any], me: str) -> bool:
    """X lets an account hide replies only in conversations it started."""
    from core import x_api

    root = str(post.get("conversation_id") or "")
    if not root or root == str(post.get("id") or "") or post.get("author_id") == me:
        return False
    read = x_api.post_details(root)
    return bool(read.ok and read.data.get("posts") and read.data["posts"][0].get("author_id") == me)


def _who(target: dict[str, Any]) -> str:
    """The account as a person reads it: display name and handle, so a look-alike handle stands out."""
    handle = f"@{target.get('username')}"
    name = "".join(ch for ch in str(target.get("name") or "") if not _hidden(ch)).strip()[:50]
    return f"{name} ({handle})" if name and name.lower() != str(target.get("username") or "").lower() else handle


def propose(arguments: dict[str, Any], *, source_context: dict[str, Any] | None = None):
    """Freeze one action for the user to approve. Reads the target from X; sends nothing."""
    from core import x_api, x_capabilities
    from core.x_voice_profile import turn_scope

    args = dict(arguments or {})
    kind = str(args.get("kind") or "").strip().lower()
    if kind not in KINDS:
        return _result(False, "invalid_arguments", f"kind must be one of: {', '.join(KINDS)}. Nothing was proposed.")
    try:
        policy = x_capabilities.load()
    except x_capabilities.XCapabilityRefused as exc:
        return _result(False, exc.status, str(exc))
    saved = x_api._saved() if kind not in _BOOKMARK_KINDS else {}
    account = saved.get("account") if isinstance(saved.get("account"), dict) else {}
    from core.x_setup import FIELDS

    if kind in _BOOKMARK_KINDS:
        from core import x_oauth2
        try:
            account = x_oauth2.account()
        except x_oauth2.OAuthFailure:
            return _result(False, "needs_account", _BOOKMARK_SIGN_IN)
    elif not account.get("id") or not all(saved.get(name) for name in FIELDS):
        return _result(False, "needs_account",
                       "Acting on X needs your own X account connected (the four account keys in Settings > API Keys > X). "
                       "Nothing was proposed.")
    if not policy['enabled']:
        return _result(False, 'disabled', 'X calls are off. Choose your features and limits in Settings first.')
    payload: dict[str, Any] = {"kind": kind}
    target: dict[str, Any] = {}
    if kind in _BOOKMARK_KINDS:
        if kind == "bookmark_read":
            limit = args.get("max_results", 20)
            cursor = args.get("next_token", "")
            if (isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100
                    or not isinstance(cursor, str) or len(cursor) > 512 or any(ord(c) < 32 or _hidden(c) for c in cursor)):
                return _result(False, "invalid_arguments", "Invalid bookmark arguments.")
            payload.update(max_results=limit, next_token=cursor)
        else:
            raw = str(args.get("post") or "").strip()
            match = x_api._POST_URL_RE.fullmatch(raw)
            post_id = match.group(1) if match else raw
            if not x_api._ID_RE.fullmatch(post_id):
                return _result(False, "invalid_arguments", "Invalid bookmark arguments.")
            payload["post_id"] = post_id
            target.update(post_id=post_id, url=x_api.post_url("i", post_id))
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
    if kind == "dm":
        text = str(args.get("text") or "").strip()
        if not text or len(text) > MAX_DM:
            return _result(False, "invalid_arguments", f"Say what the message should say (up to {MAX_DM:,} characters). Nothing was proposed.")
        if any(_hidden(ch) for ch in text):
            return _result(False, "invalid_arguments", "That text has invisible characters that change how it reads, "
                           "so it could look different on X than on the approval card. Nothing was proposed.")
        payload["text"] = text
    if kind == "list_create":
        name = str(args.get("name") or "").strip()
        description = str(args.get("description") or "").strip()
        if not 1 <= len(name) <= 25 or len(description) > 100 or any(_hidden(ch) for ch in name + description):
            return _result(False, "invalid_arguments", "A list needs a name of up to 25 characters (and a description of "
                           "up to 100). Nothing was proposed.")
        private = str(args.get("private")).strip().lower() in {"1", "true", "yes", "on"}
        payload.update(name=name, description=description, private=private)
        target = {"name": name, "description": description, "private": private}
    try:
        if kind in _POST_KINDS:
            pid = x_api._post_id(args.get("post") or args.get("post_id") or "")
            read = x_api.post_details(pid)
            if not read.ok or not read.data.get("posts"):
                return _result(False, read.status, f"VOOL could not read that post from X, so nothing was proposed. {read.message}")
            post = read.data["posts"][0]
            if kind == "delete" and post.get("author_id") != str(account["id"]):
                return _result(False, "not_yours", "That post is not from your connected X account, so it can't be deleted. Nothing was proposed.")
            if kind in {"hide_reply", "unhide_reply"} and not _replies_to_you(post, str(account["id"])):
                return _result(False, "not_yours", "You can only hide replies under your own posts. That one isn't a "
                               "reply in a conversation you started, so nothing was proposed.")
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
        if kind in _LIST_KINDS:
            lid = str(args.get("list_id") or args.get("list") or "").strip().rstrip("/").rsplit("/", 1)[-1]
            looked_list = x_api.lists(list_id=lid, kind="details")
            if not looked_list.ok:
                return _result(False, looked_list.status, f"VOOL could not read that list from X, so nothing was proposed. {looked_list.message}")
            listed = looked_list.data.get("list") or {}
            if str(listed.get("owner_id") or "") != str(account["id"]):
                return _result(False, "not_yours", "That list is not owned by your connected X account. Nothing was proposed.")
            target.update(list_id=lid, list_name=listed.get("name"))
            payload["list_id"] = lid
    except x_api.XReadError as exc:
        return _result(False, exc.status, exc.message + " Nothing was proposed.")
    from core.turn_contract import PRINCIPAL_OWNER_LOCAL, TurnRequest

    turn = (source_context or {}).get("turn_request")
    owner_chat = bool(isinstance(turn, TurnRequest) and turn.trust_principal == PRINCIPAL_OWNER_LOCAL
                      and (turn.surface == "desktop" or
                           (turn.surface == "api" and (source_context or {}).get("_desktop_ui_request") is True))
                      and (source_context or {}).get("turn_author", "owner") == "owner")
    now = time.time()
    action = {
        "id": "xa_" + secrets.token_hex(8), "kind": kind, "payload": payload,
        "text": payload.get("text", ""),
        "nonce": (nonce := secrets.token_hex(16)), "hash": _digest(payload, nonce), "target": target, "state": "pending", "created_at": now, "updated_at": now,
        "account": {"id": str(account["id"]), "username": str(account.get("username") or "")},
        "session": _session(source_context),
        "feedback_scope": turn_scope(source_context),
        "owner_local_chat": owner_chat,
        "request_id": turn.request_id if isinstance(turn, TurnRequest) else "",
    }
    action["summary"] = summary(action)
    from core.x_request_gateway import legacy_descriptor

    try:
        method, path, body = _request_for(action)
        prepared = legacy_descriptor(method, path, query=_request_query(action), body=body)
        policy, ceiling = x_capabilities.permitted(prepared['operation_id'])
        payload['policy_fingerprint'] = x_capabilities.fingerprint(policy)
        payload['account_fingerprint'] = _api_account_fingerprint(saved)
        action['hash'] = _digest(payload, nonce)
        action['target']['ceiling_microusd'] = ceiling
    except (ValueError, x_capabilities.XCapabilityRefused) as exc:
        return _result(False, getattr(exc, 'status', 'invalid_request'), str(exc))
    with _state_lock():
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
    _notify_chat_proposal(action, source_context or {})
    note = x_usage.action_refusal()
    message = (f"{action['summary']} is ready and waiting for your approval. Nothing has been sent to X. "
               "Approve or decline it in Settings > API Keys > X.")
    if note:
        message += " Note: " + note
    return _result(True, "awaiting_approval", message, public(action))


def _api_account_fingerprint(saved: dict[str, Any]) -> str:
    """The X credentials an approval is bound to: the saved keys and, when signed in with
    OAuth 2.0, that account (tokens rotate on refresh, so they are not part of it)."""
    from core import x_oauth2
    from core.x_setup import FIELDS

    bound = {name: saved.get(name) for name in (*FIELDS, "bearer_token", "account")}
    try:
        bound["oauth2_account"] = x_oauth2.account()["id"] if x_oauth2.connected() else ""
    except x_oauth2.OAuthFailure:
        bound["oauth2_account"] = ""
    return hashlib.sha256(json.dumps(bound, sort_keys=True).encode()).hexdigest()


def _require_same_account(saved: dict[str, Any]) -> None:
    from core import x_api

    if _api_account_fingerprint(x_api._saved()) != _api_account_fingerprint(saved):
        raise x_api.XReadError('account_changed', 'The X credentials changed during approval. Nothing was sent.')


def propose_api(prepared: dict[str, Any], *, source_context: dict[str, Any] | None = None):
    """Freeze one documented mutation for Settings approval, without sending.

    The exact request is sealed in the private X store (``core.x_private_calls``); the action list
    keeps only its keyed reference. The action is journaled as "preparing" before the copy is
    written and becomes "pending" only after the write succeeded, so a crash in between leaves a
    known reference that is closed and erased, never a sendable action without its request."""
    from core import x_api, x_capabilities, x_private_calls, x_request_gateway
    from core.x_endpoint_catalog import needs_approval, operation

    try:
        entry = operation(prepared["operation_id"])
        if not needs_approval(entry) or prepared["method"] != entry["method"] or entry["deferred"]:
            return _result(False, "invalid_request", "This is not an X request that waits for approval.")
        policy, ceiling = x_capabilities.permitted(entry["id"])
        saved = x_api._saved()
        try:
            x_request_gateway.credential_mode(entry, saved)
        except x_api.XReadError:
            return _result(False, "needs_account", "Connect the X account required by this feature first. Nothing was sent.")
        now = time.time()
        with _state_lock():
            state = _load()
            _expire(state, now)
            if sum(a.get("state") == "pending" for a in state["actions"].values()) >= MAX_PENDING:
                return _result(False, "busy", "There are already too many X actions waiting for approval.")
            action_id = "xa_" + secrets.token_hex(8)
            try:
                blob = x_private_calls.prepare(action_id, "request", prepared)
            except ValueError:
                return _result(False, "invalid_request", "This X request exceeds the stored request limit. Nothing was sent.")
            account = saved.get("account") if isinstance(saved.get("account"), dict) else {}
            payload = {"kind": "api", "operation_id": entry["id"], "request_ref": blob.ref,
                       "display_account": {key: account.get(key) for key in ("id", "username") if account.get(key)},
                       "policy_fingerprint": x_capabilities.fingerprint(policy),
                       "account_fingerprint": _api_account_fingerprint(saved)}
            nonce = secrets.token_hex(16)
            action = {"id": action_id, "kind": "api", "payload": payload, "nonce": nonce,
                      "hash": _digest(payload, nonce),
                      "target": {"operation_id": entry["id"], "method": entry["method"],
                                 "path": entry["path"], "ceiling_microusd": ceiling},
                      "summary": entry["summary"], "state": "preparing", "created_at": now,
                      "updated_at": now, "session": _session(source_context)}
            state["actions"][action_id] = action
            _save(state)
            try:
                blob.write()
            except Exception:
                action.update(state="failed", updated_at=time.time(),
                              message="The private X request could not be saved. Nothing was sent.")
                if not _erase_api_request(action):
                    action["cleanup_pending"] = True
                _save(state)
                raise
            action.update(state="pending", updated_at=time.time())
            _save(state)
        return _result(True, "awaiting_approval", "The exact X request is waiting for approval in Settings. Nothing was sent.", public(action))
    except x_capabilities.XCapabilityRefused as exc:
        return _result(False, exc.status, str(exc))
    except Exception:
        return _result(False, "storage_unavailable", "The private X request could not be saved. Nothing was sent.")


def _show_api_private(action: dict[str, Any], shown: dict[str, Any], *, preview: bool, include_result: bool) -> None:
    """Fill a documented request's preview and result from the private store, as allowed."""
    from core import x_private_calls, x_request_gateway

    payload = action.get("payload") or {}
    if action.get("kind") == "api":
        shown.pop("text", None)
    if preview and action.get("kind") == "api":
        try:
            request = x_private_calls.get(payload.get("request_ref"), action["id"], "request")
            shown["text"] = x_request_gateway.safe_preview({"account": payload.get("display_account") or {},
                                                            "request": request})
        except Exception:
            shown["request_unavailable"] = True
    stored = action.get("result")
    if isinstance(stored, dict):
        result = {key: value for key, value in stored.items() if key != "result_ref"}
        ref = stored.get("result_ref")
        if ref and include_result:
            try:
                value = x_private_calls.get(ref, action["id"], "result")
            except Exception:
                result["result_unavailable"] = True
            else:
                if action.get("kind") == "bookmark_read" and isinstance(value, dict):
                    # Bookmark pages keep their own shape: posts and the next page's token.
                    result.update({key: value[key] for key in ("posts", "next_token") if key in value})
                else:
                    result["result"] = value
        shown["result"] = result


def _approve_api(action_id: Any, expected_hash: Any, *, ui_locale: str = 'en') -> dict[str, Any]:
    from core import x_api, x_capabilities, x_private_calls, x_request_gateway

    with _state_lock():
        state = _load()
        _expire(state, time.time())
        action = state["actions"].get(str(action_id or ""))
        if not action or action.get("kind") != "api" or action.get("state") != "pending":
            return {"ok": False, "state": "not_pending", "message": "This X request is no longer pending."}
        if (str(expected_hash or "") != action.get("hash") or
                _digest(action["payload"], str(action.get("nonce") or "")) != action.get("hash")):
            return {"ok": False, "state": "changed", "message": "This X request does not match what was shown. Nothing was sent."}
        payload = action["payload"]
        if _api_account_fingerprint(x_api._saved()) != payload["account_fingerprint"]:
            return {"ok": False, "state": "account_changed", "message": "The connected X credentials changed. Nothing was sent."}
        try:
            # The reference is bound into the approval hash, and the store checks that the copy
            # holds exactly the content it addresses, for this action.
            prepared = x_private_calls.get(payload["request_ref"], action["id"], "request")
            if not isinstance(prepared, dict) or prepared.get("operation_id") != payload["operation_id"]:
                raise ValueError("operation mismatch")
        except Exception:
            return {"ok": False, "state": "request_unavailable",
                    "message": "The private X request could not be read. Nothing was sent. Decline it and ask again."}
        try:
            x_capabilities.permitted(payload["operation_id"], expected_fingerprint=payload["policy_fingerprint"])
        except x_capabilities.XCapabilityRefused:
            return {"ok": False, "state": "configuration_changed", "message": "Your X features or limits changed. Propose this request again."}
        action.update(state="sending", updated_at=time.time())
        _save(state)
    result = x_request_gateway.execute(prepared, {"runtime_session_id": action.get("session") or "", "ui_locale": ui_locale},
                                       settings_approved=True, expected_policy=payload["policy_fingerprint"],
                                       expected_account=payload['account_fingerprint'])
    receipt = (result.data or {}).get("receipt") or {}
    if result.ok:
        state_name = "sent"
    elif result.status == 'outcome_unknown' or (receipt.get("attempted") and not (400 <= int(receipt.get("http_status") or 0) < 500)):
        state_name = "unknown"
    else:
        state_name = "pending" if receipt.get("state") == "unsent" else "failed"
    action = _finish_api(action, state=state_name, data=result.data or {}, message=result.message)
    return {"ok": result.ok, "state": result.status, "action": public(action, include_result=True), "message": result.message}


def _finish_api(action: dict[str, Any], *, state: str, data: dict[str, Any], message: str) -> dict[str, Any]:
    """Record a documented request's outcome: the result sealed, only its reference and the
    receipt in the list, and the request copy erased once the action can never be sent again."""
    from core.cross_process_lock import LockUnavailable

    metadata: dict[str, Any] = {"receipt": data.get("receipt") or {}}
    try:
        return _finish_api_locked(action, state=state, data=data, message=message, metadata=metadata)
    except LockUnavailable:
        # The list stayed locked by another VOOL process: X's outcome is still what the owner sees.
        fields = {"state": state, "result": {**metadata, "result_unavailable": "result" in data},
                  "message": message + " (VOOL could not save this result.)", "updated_at": time.time()}
        return {**action, **fields}


def _finish_api_locked(action: dict[str, Any], *, state: str, data: dict[str, Any], message: str,
                       metadata: dict[str, Any]) -> dict[str, Any]:
    from core import x_private_calls

    written = ""
    with _state_lock():
        try:
            current = _load()
            stored = current["actions"][action["id"]]
        except (RuntimeError, OSError, KeyError):
            stored, current = None, None
        if "result" in data:
            try:
                blob = x_private_calls.prepare(action["id"], "result", data["result"])
                if stored is not None:
                    blob.write()
                    metadata["result_ref"] = written = blob.ref
                else:
                    metadata["result_unavailable"] = True
            except Exception:
                metadata["result_unavailable"] = True
        fields: dict[str, Any] = {"state": state, "result": metadata, "message": message, "updated_at": time.time()}
        if stored is None:
            return {**action, **fields, "message": message + " (VOOL could not save this result.)"}
        stored.update(fields)
        if state != "pending" and not _erase_api_request(stored):
            stored["cleanup_pending"] = True
        try:
            _save(current)
        except OSError:
            if written:
                x_private_calls.delete(written)
            return {**action, **fields, "message": message + " (VOOL could not save this result.)"}
        return stored


def _erase_api_request(action: dict[str, Any]) -> bool:
    """Erase a documented request's private copy. True when it is gone (or there never was one)."""
    if action.get("kind") != "api":
        return True
    from core import x_private_calls

    ref = (action.get("payload") or {}).get("request_ref")
    try:
        return x_private_calls.delete(ref) if x_private_calls.is_ref(ref) else True
    except Exception:
        return False


def _erase_api_private(action: dict[str, Any]) -> bool:
    """Erase a documented request's request and result copies; the receipt stays."""
    erased = _erase_api_request(action)
    stored = action.get("result") if action.get("kind") in _SEALED_RESULT_KINDS else None
    if isinstance(stored, dict) and stored.get("result_ref"):
        from core import x_private_calls

        try:
            gone = x_private_calls.delete(stored["result_ref"])
        except Exception:
            gone = False
        if gone:
            stored.pop("result_ref", None)
            stored["result_erased"] = True
        erased = erased and gone
    return erased


def _sweep_private(state: dict[str, Any]) -> None:
    """Erase private copies no action refers to. Called under the state lock only."""
    from core import x_private_calls

    keep: set[str] = set()
    for action in state["actions"].values():
        if action.get("kind") == "api":
            keep.add(str((action.get("payload") or {}).get("request_ref") or ""))
        if isinstance(action.get("result"), dict):
            keep.add(str(action["result"].get("result_ref") or ""))
    with suppress(OSError):
        x_private_calls.sweep(keep)


def cleanup_api_request(action_id: Any) -> dict[str, Any]:
    """The owner erases a finished request's private copies (request and result), retrying any
    earlier failed erasure. Never cancels or resends an action; the receipt stays."""
    with _state_lock():
        state = _load()
        action = state["actions"].get(str(action_id or ""))
        if not action or action.get("kind") not in _SEALED_RESULT_KINDS or action.get("state") in {"pending", "preparing", "sending"}:
            return {"ok": False, "state": "not_terminal", "message": "This private request is still needed by a pending action."}
        cleaned = _erase_api_private(action)
        action["cleanup_pending"] = not cleaned
        _save(state)
    return {"ok": cleaned, "state": "cleaned" if cleaned else "cleanup_pending", "action": public(action)}


def _current() -> dict[str, Any]:
    """The store with expiry applied (and saved). Raises RuntimeError when it can't be read."""
    with _state_lock():
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
    return [public(a, for_settings=True) for a in done[:limit]]


def _session(source_context: dict[str, Any] | None) -> str:
    """The chat an action belongs to, resolved the same way as other chat-scoped drafts."""
    ctx = source_context if isinstance(source_context, dict) else {}
    return str(ctx.get("runtime_session_id") or ctx.get("chat_id") or ctx.get("session_id") or "")


def status_for_model(action_id: str = "", *, source_context: dict[str, Any] | None = None):
    """The state of an action this chat proposed (the newest one when no id is given)."""
    try:
        state = _current()
    except RuntimeError:
        return _result(False, "store_unreadable", "VOOL could not read its list of X actions.")
    session = _session(source_context)
    if not session:  # no chat to match on: never show another chat's actions (DM text included)
        return _result(False, "not_found", "No such X action in this chat.")
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
    return _result(True, str(action.get("state")), text, public(action, include_result=True))


def decline(action_id: Any) -> dict[str, Any]:
    with _state_lock():
        state = _load()
        action = state["actions"].get(str(action_id or ""))
        if not action or action.get("state") != "pending":
            return {"ok": False, "state": "not_pending", "message": "That X action is no longer waiting for approval."}
        action.update(state="declined", updated_at=time.time(), message="Declined. Nothing was sent to X.")
        _save(state)
    if not _erase_api_request(action):
        action = _finish(action, cleanup_pending=True)
    return {"ok": True, "action": public(action), "message": "Declined. Nothing was sent to X."}


def _request_query(action: dict[str, Any]) -> dict[str, Any]:
    payload = action["payload"]
    if payload["kind"] != "bookmark_read":
        return {}
    params: dict[str, Any] = {"max_results": payload["max_results"], "tweet.fields": "created_at,author_id"}
    if payload.get("next_token"):
        params["pagination_token"] = payload["next_token"]
    return params


def _confirmed_bookmarks(action: dict[str, Any], answer: dict[str, Any], read_hold: int) -> list[dict[str, Any]] | None:
    """Check X's answer to an approved bookmark request; the read rows, or None for a change."""
    from core import x_api

    def refuse(reason: str):
        raise x_api.XReadError('provider_error', 'X returned an unexpected bookmark answer (' + reason + ').')

    part = answer.get("data")
    if not read_hold:
        if not isinstance(part, dict) or part.get("bookmarked") is not (action["kind"] == "bookmark_add"):
            refuse("unconfirmed bookmark")
        return None
    meta = answer.get("meta", {})
    if not isinstance(meta, dict):
        refuse("metadata")
    rows = part if isinstance(part, list) else ([] if part is None and meta.get("result_count") == 0 else None)
    if not isinstance(rows, list) or len(rows) > read_hold:
        refuse("posts")
    if not all(isinstance(row, dict) and x_api._ID_RE.fullmatch(str(row.get("id") or "")) and isinstance(row.get("text"), str) for row in rows):
        refuse("post")
    cursor = meta.get("next_token", "")
    if not isinstance(cursor, str) or len(cursor) > 512 or any(ord(c) < 32 or _hidden(c) for c in cursor):
        refuse("cursor")
    return rows


def _request_for(action: dict[str, Any]) -> tuple[str, str, dict[str, Any] | None]:
    payload, me = action["payload"], action["account"]["id"]
    kind = payload["kind"]
    if kind == "bookmark_read":
        return "GET", f"/2/users/{me}/bookmarks", None
    if kind == "bookmark_add":
        return "POST", f"/2/users/{me}/bookmarks", {"tweet_id": payload["post_id"]}
    if kind == "bookmark_remove":
        return "DELETE", f"/2/users/{me}/bookmarks/{payload['post_id']}", None
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
    if kind in {"hide_reply", "unhide_reply"}:
        return "PUT", f"/2/tweets/{payload['post_id']}/hidden", {"hidden": kind == "hide_reply"}
    if kind == "mute":
        return "POST", f"/2/users/{me}/muting", {"target_user_id": payload["user_id"]}
    if kind == "unmute":
        return "DELETE", f"/2/users/{me}/muting/{payload['user_id']}", None
    if kind == "dm":
        return "POST", f"/2/dm_conversations/with/{payload['user_id']}/messages", {"text": payload["text"]}
    if kind == "list_create":
        return "POST", "/2/lists", {"name": payload["name"], "description": payload["description"], "private": payload["private"]}
    if kind == "list_add":
        return "POST", f"/2/lists/{payload['list_id']}/members", {"user_id": payload["user_id"]}
    if kind == "list_remove":
        return "DELETE", f"/2/lists/{payload['list_id']}/members/{payload['user_id']}", None
    raise ValueError(kind)


def _finish(action: dict[str, Any], **fields: Any) -> dict[str, Any]:
    """Record how a send ended. If the store can't be written, the outcome is still returned."""
    try:
        with _state_lock():
            state = _load()
            stored = state["actions"][action["id"]]
            stored.update(updated_at=time.time(), **fields)
            _save(state)
            return stored
    except (RuntimeError, OSError, KeyError):
        outcome = {**action, **fields, "updated_at": time.time()}
        outcome["message"] = str(fields.get("message") or "") + " (VOOL could not save this result.)"
        return outcome


def approve(action_id: Any, *, expected_hash: Any = None, ui_locale: str = 'en') -> dict[str, Any]:
    """The user approved this exact action in the app: send it once. Owner-local route only."""
    import urllib.error

    from core import x_api, x_capabilities, x_request_gateway
    from core.remote_fetch_policy import CredentialRedirectRefusedError, RemoteFetchRefusedError, open_remote_url
    from core.secret_redaction import register_exact_secret
    from core.x_setup import FIELDS, OAuth1Keys, oauth1_authorization

    with _state_lock():
        try:
            _api = (_load()["actions"].get(str(action_id or "")) or {}).get("kind") == "api"
        except RuntimeError:
            _api = False
    if _api:
        return _approve_api(action_id, expected_hash, ui_locale=ui_locale)

    with _state_lock():
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
        bookmark = action["kind"] in _BOOKMARK_KINDS
        try:
            method, path, request_body = _request_for(action)
            query = _request_query(action)
            prepared = x_request_gateway.legacy_descriptor(method, path, query=query, body=request_body)
            x_capabilities.permitted(prepared['operation_id'], expected_fingerprint=action['payload'].get('policy_fingerprint', ''))
        except (ValueError, x_capabilities.XCapabilityRefused) as exc:
            return {'ok': False, 'state': getattr(exc, 'status', 'invalid_request'),
                    'message': x_capabilities.ui_message('xfull.profile_changed', locale=ui_locale)}
        saved = x_api._saved() if not bookmark else {}
        account = saved.get("account") if isinstance(saved.get("account"), dict) else {}
        if bookmark:
            from core import x_oauth2
            try:
                account = x_oauth2.account()
            except x_oauth2.OAuthFailure:
                return {"ok": False, "state": "needs_account", "message": _BOOKMARK_SIGN_IN}
        if action['payload'].get('account_fingerprint') and _api_account_fingerprint(saved) != action['payload']['account_fingerprint']:
            return {'ok': False, 'state': 'account_changed',
                    'message': x_capabilities.ui_message('xfull.profile_changed', locale=ui_locale)}
        if str(account.get("id") or "") != action["account"]["id"] or (not bookmark and not all(saved.get(name) for name in FIELDS)):
            return {"ok": False, "state": "account_changed",
                    "message": "The connected X account is not the one this action was prepared for. Nothing was sent."}
        refusal = x_usage.reserve_action()
        if refusal:
            return {"ok": False, "state": "usage_limit_reached", "message": refusal}
        publishes = action["kind"] in _TEXT_KINDS
        if publishes:
            # Posts, replies and quotes also keep the daily posting cap (core/usage_quota "x.post").
            # A count that can't be read would restart at zero, so the cap fails closed instead.
            if not usage_quota.store_readable():
                x_usage.unreserve_action()
                return {"ok": False, "state": "daily_limit_unreadable",
                        "message": "VOOL could not read today's post count, so it can't check its daily limit. "
                                   "Nothing was sent."}
            day = usage_quota.consume_quota(POST_QUOTA, tier=usage_quota.active_tier())
            if not day.allowed:
                x_usage.unreserve_action()
                return {"ok": False, "state": "daily_limit_reached",
                        "message": f"VOOL has already posted {day.used} times on X today, its daily limit. "
                                   "Nothing was sent; you can approve this again tomorrow."}
        read_hold = int(action["payload"].get("max_results") or 0) if action["kind"] == "bookmark_read" else 0
        if read_hold:
            refusal = x_usage.reserve_reads(read_hold)
            if refusal:
                _release_slot(publishes)
                return {"ok": False, "state": "usage_limit_reached", "message": refusal}
        action.update(state="sending", updated_at=time.time())
        try:
            _save(state)
        except OSError:
            if read_hold:
                x_usage.release_reads(read_hold)
            x_usage.unreserve_action()
            if publishes:
                usage_quota.release_quota(POST_QUOTA)
            return {"ok": False, "state": "store_unreadable", "message": "VOOL could not save this X action. Nothing was sent."}
    from urllib.parse import urlencode

    method, path, body = _request_for(action)
    path += ("?" + urlencode(query)) if query else ""
    url = x_api.API_ORIGIN + path
    if bookmark:
        url = x_oauth2.API_ORIGIN + path
        try:
            token = x_oauth2.access_token(action["account"]["id"])
        except x_oauth2.OAuthFailure:
            if read_hold:
                x_usage.release_reads(read_hold)
            _release_slot(publishes)
            action = _finish(action, state="pending", message=_BOOKMARK_SIGN_IN)
            return {"ok": False, "state": "needs_account", "message": _BOOKMARK_SIGN_IN}
        register_exact_secret(token)
        authorization = "Bearer " + token
    else:
        keys = OAuth1Keys(**{name: saved[name] for name in FIELDS})
        for name in FIELDS:
            register_exact_secret(saved[name])
        authorization = oauth1_authorization(method, url, keys)
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Authorization": authorization, "Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    bookmark_rows = None
    read_uncertain = False
    delivered = 0
    try:
        from core.effect_gateway import named_background_effect_scope

        with named_background_effect_scope("x.approved_action"):
            def send():
                nonlocal bookmark_rows, read_uncertain, delivered
                if bookmark:
                    # Dispatched: from here a lost or malformed answer may still have cost reads.
                    read_uncertain = bool(read_hold)
                else:
                    headers['Authorization'] = oauth1_authorization(method, url, keys)
                limit = 4 * 1024 * 1024 if bookmark else 256 * 1024
                with open_remote_url(url, data=data, headers=headers, method=method, timeout=x_api.REQUEST_TIMEOUT_S,
                                     no_proxy=True, provider_id="x.api", keyed_or_keyless="keyed",
                                     credential_headers=("Authorization",), redirect_policy='refuse') as response:
                    raw = response.read(limit + 1) or b""
                    status = int(getattr(response, 'status', 200))
                if not raw and status == 204 and not bookmark:
                    return raw
                try:
                    answer = json.loads(raw)
                except (ValueError, UnicodeDecodeError):
                    answer = None
                if len(raw) > limit or not isinstance(answer, dict) or answer.get('errors') or not 200 <= status < 300:
                    raise x_api.XReadError('provider_error', 'X returned an invalid result.', http_status=status)
                confirmed = answer.get('data')
                if action['kind'] in _TEXT_KINDS and (not isinstance(confirmed, dict) or not x_api._ID_RE.fullmatch(str(confirmed.get('id') or ''))):
                    raise x_api.XReadError('provider_error', 'X did not confirm a post identifier.', http_status=status)
                if bookmark:
                    if read_hold and isinstance(answer.get('data'), list):
                        delivered = len(answer['data'])
                    bookmark_rows = _confirmed_bookmarks(action, answer, read_hold)
                return raw

            def same_account():
                if not bookmark:
                    _require_same_account(saved)
                    return
                try:
                    current = x_oauth2.account()['id']
                except x_oauth2.OAuthFailure:
                    current = ''
                if current != action["account"]["id"]:
                    raise x_api.XReadError('account_changed', 'The X sign-in changed during approval. Nothing was sent.')

            raw, receipt = x_request_gateway.perform(
                prepared, send, context={'runtime_session_id': action.get('session') or '', 'ui_locale': ui_locale},
                owner_local=True, expected_policy=action['payload'].get('policy_fingerprint', ''),
                before_dispatch=same_account)
            action['receipt'] = receipt
    except (x_api.XReadError, x_capabilities.XCapabilityRefused, usage_quota.QuotaUnavailable, PermissionError) as exc:
        receipt = getattr(exc, 'x_receipt', {})
        unsent = not receipt.get('attempted')
        if unsent:
            _release_slot(publishes)
        message_key = 'xfull.reservation_refused' if unsent else 'xact.lost_answer'
        action = _finish(action, state='pending' if unsent else 'unknown', receipt=receipt,
                         message=x_capabilities.ui_message(message_key, locale=ui_locale))
        state_name = getattr(exc, 'status', 'spend_cap_exhausted') if unsent else 'unknown'
        return {'ok': False, 'state': state_name, 'message': action['message'], 'action': public(action)}
    except RemoteFetchRefusedError:
        x_usage.unreserve_action()
        if publishes:
            usage_quota.release_quota(POST_QUOTA)
        action = _finish(action, state="pending", message="VOOL is not allowed to go online right now (for example Local Only is on). Nothing was sent; you can approve again later.")
        return {"ok": False, "state": "refused", "message": action["message"], "action": public(action)}
    except CredentialRedirectRefusedError:
        read_uncertain = bool(read_hold)
        # X received the request before it answered with a redirect, so it may have acted on it.
        action = _finish(action, state="unknown", message="X's answer was unusual, so VOOL stopped there. "
                         "It may or may not have gone out: check X before trying again.")
        return {"ok": False, "state": "unknown", "message": action["message"], "action": public(action)}
    except urllib.error.HTTPError as exc:
        try:
            err_body = json.loads((exc.read(64 * 1024) or b"{}").decode("utf-8"))
        except Exception:
            err_body = None
        err = x_api._http_failure(int(exc.code or 0), err_body)
        # The owner reads this in Settings: no raw "X said" detail there (the model still gets it on reads).
        message = err.message.split(" X said:", 1)[0]
        if err.status == "plan_refused":
            if action["kind"] == "dm":
                message = (f"X refused it. @{action['target'].get('username')} may not accept messages from you, or your "
                           "X app may need \"Read and write and Direct message\" permission (developer.x.com > your app > "
                           "User authentication settings; then generate new Access Tokens and reconnect X in Settings).")
            else:
                message = ("X refused it. Your X app may need \"Read and write\" permission (developer.x.com > your app > "
                           "User authentication settings; then generate new Access Tokens and reconnect X in Settings), "
                           "or X may not allow this one.")
        if int(exc.code or 0) >= 500 or 300 <= int(exc.code or 0) < 400:
            read_uncertain = bool(read_hold)
            # X may have done it before failing to answer, or before redirecting.
            action = _finish(action, state="unknown", message=message + " It may or may not have gone out: check X before trying again.")
            return {"ok": False, "state": "unknown", "message": action["message"], "action": public(action)}
        # X refused it outright, so it counts against neither the action limit nor the daily cap,
        # and a refused bookmark read carries no items.
        read_uncertain = False
        _release_slot(publishes)
        action = _finish(action, state="failed", message=message + " X did not do it.")
        return {"ok": False, "state": err.status, "message": action["message"], "action": public(action)}
    except Exception as exc:
        if _never_connected(exc):
            read_uncertain = False
            _release_slot(publishes)
            action = _finish(action, state="failed", message="Could not reach X from this machine. Nothing was sent.")
        else:
            read_uncertain = bool(read_hold)
            action = _finish(action, state="unknown",
                             message="X's answer was lost. It may or may not have gone out: check X before trying again.")
        return {"ok": False, "state": action["state"], "message": action["message"], "action": public(action)}
    finally:
        if read_hold:
            try:
                if bookmark_rows is not None:
                    x_usage.record_read(posts=len(bookmark_rows))
                elif read_uncertain:
                    # A lost or unusable answer may still have incurred reads: keep the approved
                    # ceiling, or every item X delivered when that is more.
                    x_usage.record_read(posts=delivered, other=max(0, read_hold - delivered))
            finally:
                x_usage.release_reads(read_hold)
    try:
        answer = json.loads(raw.decode("utf-8")) if raw else {}
    except (UnicodeDecodeError, ValueError):
        answer = {}
    result: dict[str, Any] = {}
    data_part = answer.get("data") if isinstance(answer, dict) else None
    if bookmark:
        if read_hold:
            result = {"posts": [x_api.shape_post(row) for row in bookmark_rows],
                      "next_token": answer.get("meta", {}).get("next_token", "")}
    elif isinstance(data_part, dict) and action["kind"] in _TEXT_KINDS and x_api._ID_RE.fullmatch(str(data_part.get("id") or "")):
        handle = str(action["account"].get("username") or "")
        result = {"post_id": str(data_part["id"]),
                  "url": x_api.post_url(handle if x_api._HANDLE_RE.fullmatch(handle) else "i", str(data_part["id"]))}
    elif isinstance(data_part, dict):
        result = {k: v for k, v in data_part.items() if isinstance(v, (bool, int)) and k != "url"}
    # A 2xx status without the expected receipt does not prove publication.
    if action["kind"] in _TEXT_KINDS and not result.get("post_id"):
        action = _finish(action, state="unknown", message="X did not return a post receipt. "
                         "It may or may not have gone out: check X before trying again.")
        return {"ok": False, "state": "unknown", "message": action["message"], "action": public(action)}
    done = "sent" if action["kind"] in _TEXT_KINDS or action["kind"] == "dm" else "done"
    message = "Sent to X." if done == "sent" else "Done on X."
    if action["kind"] in _SEALED_RESULT_KINDS:
        # Bookmarked posts are private: sealed like any documented request's answer.
        action = _finish_api(action, state="sent", data={"result": result, "receipt": action.get("receipt") or {}}, message=message)
        return {"ok": True, "action": public(action, include_result=True), "message": f"{action['summary']}: {done}."}
    action = _finish(action, state="sent", result=result, message=message)
    return {"ok": True, "action": public(action), "message": f"{action['summary']}: {done}." + (f" {result['url']}" if result.get("url") else "")}



def _release_slot(publishes: bool) -> None:
    """Give back the reserved action (and the day's post) when X provably did not act."""
    x_usage.unreserve_action()
    if publishes:
        usage_quota.release_quota(POST_QUOTA)


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
            out = approve(body.get("action_id"), expected_hash=body.get("hash"), ui_locale=str(body.get("ui_locale") or 'en'))
            return (200 if out.get("ok") else 400), out
        if action == "decline":
            out = decline(body.get("action_id"))
            return (200 if out.get("ok") else 400), out
        if action == "cleanup_request":
            out = cleanup_api_request(body.get("action_id"))
            return (200 if out.get("ok") else 400), out
    except RuntimeError:
        return 400, {"ok": False, "state": "store_unreadable", "message": "VOOL could not read its list of X actions. Nothing was sent."}
    return 400, {"ok": False, "state": "invalid", "message": "Unknown X step."}


__all__ = ["KINDS", "approve", "decline", "handle", "overview", "pending", "propose", "public", "recent", "status_for_model"]


def chat_cards(session_id: str, request_id: str) -> dict[str, Any]:
    """Only the guarded owner-local HTTP surface calls this; never a model tool."""
    if not session_id or not request_id:
        return {"ok": False, "cards": []}
    try:
        state = _current()
        cards = [public(a, for_settings=True) for a in state["actions"].values()
                 if a.get("owner_local_chat") is True and a.get("session") == session_id
                 and a.get("request_id") == request_id and a.get("state") in {"pending", "sending", "sent", "unknown", "failed", "expired", "declined"}]
        return {"ok": True, "cards": cards}
    except RuntimeError:
        return {"ok": False, "cards": []}


def _notify_chat_proposal(action: dict[str, Any], context: dict[str, Any]) -> None:
    if not action.get("owner_local_chat"):
        return
    try:
        from core.i18n.catalog import catalog_for
        from core.operator import notification_center

        catalog = catalog_for(str(context.get("ui_locale") or "en"))
        notification_center.record_item(
            dedupe_key="x-action:" + action["id"], source_kind="background_run",
            title=catalog.format("xact.waiting_title", count=1),
            body=catalog.text("xact.section_title"), session_id=action["session"],
            payload={"event_type": "task_pending_approval", "section": "needs", "status": "needs approval",
                     "x_action_id": action["id"], "request_id": action["request_id"], "audio_eligible": False},
        )
    except Exception:
        # Notification delivery cannot change a frozen proposal or its send decision.
        return
