"""Housekeeping over the operator's own chats and projects: find, pin, rename, archive, move.

VOOL already keeps every chat in one transcript log, a per-chat meta file (title, archived,
project binding), a project registry and per-message pins. What it lacked was a reader that looks
ACROSS all of them by content ("which chat did we talk about solana staking in?"), a way to pin a
whole chat to the top of the sidebar, and one owner that the plain-words lane, the command
registry and the HTTP API all call for the same actions. This module is that owner.

Law of this module:

- ONE source per fact. Transcripts come from the conversation log, titles and bindings from
  ``list_conversation_sessions`` / ``set_session_meta``, projects from ``project_store``, message
  pins from ``message_pins``. Nothing is copied into a second history store. The only state this
  module owns is the chat-pin set and a small per-chat follow-up memory ("pin 2" after a search).
- EVERY MUTATION IS READ BACK. An action reports success only when a fresh read of the owning
  store shows the new value; otherwise it returns a typed failure. A caller can never print
  "renamed" for a rename that did not land.
- GOVERNED TEXT STAYS GOVERNED. An assistant turn whose finalization is WITHHELD or ERASED is never
  matched, ranked or quoted; when its availability cannot be read the text is dropped (fail
  closed), exactly as ``/api/chat/history`` and ``message_pins.list_servable_pins`` gate it.
- DELETED CHATS ARE GONE. A chat whose namespace is deleted is never searched, listed or pinned.
- Searching quotes at most a short, secret-redacted snippet per hit, with the source chat's
  restricted terms masked.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import math
import os
import re
import threading
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.runtime_paths import data_path

SCHEMA = "vool.housekeeping.v1"

_CHAT_PINS_FILE = "chat_pins.json"
_STATE_FILE = "housekeeping_state.json"
_STORE_LOCK = threading.Lock()

_TITLE_MAX = 200            # same ceiling set_session_meta applies
_PROJECT_NAME_MAX = 80      # same ceiling project_store applies
_SNIPPET_CHARS = 160
_DEFAULT_HITS = 5
_FOLLOWUP_TTL_SECONDS = 24 * 3600
_STATE_CHATS_MAX = 200      # follow-up memory kept for this many chats, newest first
_HANDLED_TURNS_MAX = 2000   # housekeeping request fingerprints kept for search exclusion

# --------------------------------------------------------------------------------------------
# Small file stores (atomic replace + last-good .bak, same discipline as message_pins)
# --------------------------------------------------------------------------------------------


def chat_pins_path() -> Path:
    return data_path(_CHAT_PINS_FILE)


def state_path() -> Path:
    return data_path(_STATE_FILE)


def _read_json_object(path: Path) -> dict[str, Any]:
    """A dict from ``path``; a corrupt file falls back to its ``.bak``, else empty.

    A corrupt primary is preserved as ``<name>.corrupt-<ts>`` rather than overwritten silently.
    """
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace") or "{}")
        if isinstance(data, dict):
            return data
    except (json.JSONDecodeError, OSError):
        pass
    with contextlib.suppress(OSError):
        path.replace(path.with_name(path.name + f".corrupt-{int(time.time())}"))
    bak = path.with_name(path.name + ".bak")
    try:
        data = json.loads(bak.read_text(encoding="utf-8", errors="replace") or "{}")
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _write_json_object(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, ensure_ascii=False)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)
    with contextlib.suppress(OSError):
        path.with_name(path.name + ".bak").write_text(payload, encoding="utf-8")


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


# --------------------------------------------------------------------------------------------
# Results
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ActionResult:
    """One housekeeping action's outcome. ``ok`` is True only after a read-back confirmed it."""

    ok: bool
    action: str
    session_id: str = ""
    project_id: str = ""
    error: str = ""
    before: dict[str, Any] = field(default_factory=dict)
    after: dict[str, Any] = field(default_factory=dict)
    changed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "action": self.action,
            "session_id": self.session_id,
            "project_id": self.project_id,
            "error": self.error,
            "before": dict(self.before),
            "after": dict(self.after),
            "changed": self.changed,
        }


@dataclass(frozen=True)
class SearchHit:
    session_id: str
    title: str
    project_id: str
    project_name: str
    archived: bool
    pinned: bool
    updated_at: str
    score: float
    matched_terms: tuple[str, ...]
    snippet: str
    snippet_role: str          # "user" | "assistant" | "title" | "pinned_message" | ""
    snippet_ts: str
    snippet_text_hash: str     # sha256 of the full matched message (for pinning that message)
    is_current_chat: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "title": self.title,
            "project_id": self.project_id,
            "project_name": self.project_name,
            "archived": self.archived,
            "pinned": self.pinned,
            "updated_at": self.updated_at,
            "score": round(self.score, 4),
            "matched_terms": list(self.matched_terms),
            "snippet": self.snippet,
            "snippet_role": self.snippet_role,
            "snippet_ts": self.snippet_ts,
            "snippet_text_hash": self.snippet_text_hash,
            "is_current_chat": self.is_current_chat,
        }


@dataclass(frozen=True)
class SearchResult:
    query: str
    terms: tuple[str, ...]
    hits: tuple[SearchHit, ...]
    chats_searched: int
    turns_searched: int
    projects_searched: int
    governed_turns_skipped: int
    oldest_turn_ts: str
    log_trimmed: bool
    error: str = ""
    found_terms: tuple[str, ...] = ()   # query terms that occur in at least one chat

    @property
    def ok(self) -> bool:
        return not self.error

    @property
    def unique(self) -> bool:
        """One clear winner: a single hit, or a top hit well ahead of the next one."""
        if not self.hits:
            return False
        if len(self.hits) == 1:
            return True
        top, second = self.hits[0], self.hits[1]
        if set(top.matched_terms) != set(self.found_terms or self.terms):
            return False
        return len(second.matched_terms) < len(top.matched_terms) or top.score >= 1.6 * max(second.score, 1e-9)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA,
            "query": self.query,
            "terms": list(self.terms),
            "hits": [hit.to_dict() for hit in self.hits],
            "unique": self.unique,
            "chats_searched": self.chats_searched,
            "turns_searched": self.turns_searched,
            "projects_searched": self.projects_searched,
            "governed_turns_skipped": self.governed_turns_skipped,
            "oldest_turn_ts": self.oldest_turn_ts,
            "log_trimmed": self.log_trimmed,
            "found_terms": list(self.found_terms),
            "error": self.error,
        }


# --------------------------------------------------------------------------------------------
# Text normalisation for search
# --------------------------------------------------------------------------------------------

_WORD_RE = re.compile(r"[^\W_]+(?:['’][^\W_]+)?", re.UNICODE)

#: Function words plus the vocabulary people use to ASK for a search ("I was talking about",
#: "can you find the chat where"). Those words describe the request, not the thing searched for,
#: so they never become query terms. Topic words are never listed here.
_STOPWORD_TEXT = """
    a about above after again against all also am an and any are aren't as at be because been
    before being below between both but by can can't cannot could couldn't did didn't do does
    doesn't doing don't down during each few for from further had hadn't has hasn't have haven't
    having he he'd he'll he's her here here's hers herself him himself his how how's i i'd i'll
    i'm i've if in into is isn't it it's its itself let's me more most mustn't my myself no nor
    not of off on once only or other ought our ours ourselves out over own same shan't she she'd
    she'll she's should shouldn't so some such than that that's the their theirs them themselves
    then there there's these they they'd they'll they're they've this those through to too under
    until up very was wasn't we we'd we'll we're we've were weren't what what's when when's where
    where's which while who who's whom why why's with won't would wouldn't you you'd you'll
    you're you've your yours yourself yourselves u ur im ive dont cant wont didnt doesnt isnt
    wasnt hey hi pls please plz ok okay yeah yes just like thing things stuff something somethin
    somewhere someone anything everything maybe kinda sorta really actually basically also again
    vool
    talk talks talked talking talkin mention mentions mentioned mentioning discuss discussed
    discussing discussion said say saying says told tell telling spoke speak speaking chat chats
    chatted chatting conversation conversations convo convos thread threads session sessions
    project projects remember remembered recall recalled forget forgot forgotten find found
    search searched searching look looking locate dig earlier past previous previously before
    ago last time once while pin pinned pinning did do done for me us know knew which where
    whatever wherever somehow wich whcih whihc pull dig track hunt bring take show open get old
    older helped word words phrase called named titled ask asked asking
    """
STOPWORDS = frozenset(_STOPWORD_TEXT.split())


def _fold(text: str) -> str:
    """Lowercase and strip accents, keeping letters and digits of every script."""
    decomposed = unicodedata.normalize("NFKD", str(text or ""))
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return stripped.lower().replace("’", "'")


def _stem(word: str) -> str:
    """A light suffix fold so 'staking'/'stake'/'stakes' and 'wallets'/'wallet' meet."""
    for suffix in ("ingly", "edly", "ings", "ing", "ied", "ies", "ed", "es", "s"):
        if suffix == "s" and word.endswith(("ss", "us", "is")):
            continue   # 'class', 'status', 'analysis' are not plurals
        if len(word) > len(suffix) + 2 and word.endswith(suffix):
            base = word[: -len(suffix)]
            if suffix in ("ied", "ies"):
                return base + "y"
            if suffix in ("ing", "ed") and len(base) >= 3 and base[-1] == base[-2] and base[-1] not in "aeiouls":
                base = base[:-1]   # 'pinned' -> 'pin', 'shipping' -> 'ship'
            return base.rstrip("e") or base
    return word.rstrip("e") if len(word) > 3 else word


def tokenize(text: str) -> list[str]:
    """Raw folded tokens (stopwords kept), in order."""
    return [m.group(0).replace("'", "") for m in _WORD_RE.finditer(_fold(text))]


def query_terms(query: str) -> tuple[str, ...]:
    """Distinct stems of the content words in ``query``, in first-seen order."""
    seen: list[str] = []
    for token in tokenize(query):
        if token in STOPWORDS or token.replace("'", "") in STOPWORDS:
            continue
        if len(token) < 2 and not token.isdigit():
            continue
        stem = _stem(token)
        if stem and stem not in seen:
            seen.append(stem)
    return tuple(seen[:12])


def _doc_stems(text: str) -> set[str]:
    return {_stem(token) for token in tokenize(text)}


def _term_in(term: str, stems: set[str]) -> bool:
    if term in stems:
        return True
    # A short prefix of a longer word ('solan' typed for 'solana') still counts, never the
    # reverse, and never for short terms where a prefix is nearly everything.
    if len(term) >= 4:
        return any(stem.startswith(term) for stem in stems if len(stem) > len(term))
    return False


# --------------------------------------------------------------------------------------------
# Governance gates
# --------------------------------------------------------------------------------------------


def _assistant_text_servable(row: dict[str, Any], text: str) -> bool:
    """Whether an assistant turn's bytes may be matched or quoted (A8 gate, fail closed)."""
    if not text:
        return False
    try:
        from core.finalization import (
            AVAILABILITY_ERASED,
            AVAILABILITY_WITHHELD,
            payload_availability_for_request_id,
            payload_availability_for_text,
        )

        verdict = None
        request_id = str(row.get("request_id") or "").strip()
        if request_id:
            verdict = payload_availability_for_request_id(request_id)
        if verdict is None:
            verdict = payload_availability_for_text(text)
        return verdict not in (AVAILABILITY_WITHHELD, AVAILABILITY_ERASED)
    except Exception:
        return False


def _snippet(text: str, terms: tuple[str, ...], restricted: list[str]) -> str:
    """A short window around the first matched term, whitespace-collapsed and redacted."""
    from core.secret_redaction import redact_secrets

    clean = " ".join(str(text or "").split())
    if not clean:
        return ""
    folded = _fold(clean)
    position = -1
    for match in _WORD_RE.finditer(folded):
        if _stem(match.group(0).replace("'", "")) in terms or any(
            len(term) >= 4 and _stem(match.group(0)).startswith(term) for term in terms
        ):
            position = match.start()
            break
    if position < 0:
        position = 0
    half = _SNIPPET_CHARS // 2
    start = max(0, position - half)
    end = min(len(clean), start + _SNIPPET_CHARS)
    start = max(0, end - _SNIPPET_CHARS)
    window = clean[start:end].strip()
    if start > 0:
        window = "…" + window
    if end < len(clean):
        window = window + "…"
    window = redact_secrets(window)
    for term in restricted or []:
        cleaned = str(term or "").strip()
        if cleaned:
            window = re.sub(re.escape(cleaned), "[restricted]", window, flags=re.IGNORECASE)
    return window


def _text_hash(text: str) -> str:
    return hashlib.sha256(" ".join(str(text or "").split()).encode("utf-8")).hexdigest()


def _request_fingerprint(session_id: str, user_text: str) -> str:
    folded = " ".join(_fold(user_text).split())[:4000]
    return hashlib.sha256((str(session_id) + "\x00" + folded).encode("utf-8")).hexdigest()[:24]


# --------------------------------------------------------------------------------------------
# Chat index (who exists, what they are called, where they live)
# --------------------------------------------------------------------------------------------


def _live_sessions() -> dict[str, dict[str, Any]]:
    """Every non-deleted chat the sidebar knows, keyed by session id."""
    from core.memory.entries import list_conversation_sessions

    return {
        str(row.get("session_id") or ""): dict(row)
        for row in list_conversation_sessions(limit=1_000_000)
        if str(row.get("session_id") or "")
    }


def _projects() -> dict[str, dict[str, Any]]:
    from core import project_store

    return {str(p.get("id") or ""): dict(p) for p in project_store.list_projects() if p.get("id")}


def _chat_is_deleted(session_id: str) -> bool:
    try:
        from core.context_namespace import load_chat_namespace

        namespace = load_chat_namespace(session_id)
    except Exception:
        return False
    return namespace is not None and namespace.lifecycle_state == "deleted"


def describe_chat(session_id: str) -> dict[str, Any] | None:
    """The sidebar's view of one chat plus its pin state, or None if it does not exist."""
    sid = str(session_id or "").strip()
    if not sid or _chat_is_deleted(sid):
        return None
    row = _live_sessions().get(sid)
    if row is None:
        return None
    pins = list_pinned_chats(sessions={sid: row})
    row["pinned"] = bool(pins)
    row["pinned_at"] = pins[0]["pinned_at"] if pins else ""
    project = _projects().get(str(row.get("project_id") or ""))
    row["project_name"] = str((project or {}).get("name") or "")
    return row


# --------------------------------------------------------------------------------------------
# Chat pins
# --------------------------------------------------------------------------------------------


def _load_chat_pins() -> dict[str, dict[str, Any]]:
    raw = _read_json_object(chat_pins_path())
    return {str(k): v for k, v in raw.items() if isinstance(v, dict) and str(k).strip()}


def list_pinned_chats(*, sessions: dict[str, dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Pinned chats that still exist, most recently pinned first. Each: {session_id, pinned_at}.

    A pin whose chat was deleted (or never existed) is not served; it is pruned from the store on
    the next pin write, so a deleted chat can never reappear pinned.
    """
    live = sessions if sessions is not None else _live_sessions()
    out = [
        {"session_id": sid, "pinned_at": str(entry.get("pinned_at") or "")}
        for sid, entry in _load_chat_pins().items()
        if sid in live and not _chat_is_deleted(sid)
    ]
    out.sort(key=lambda item: item["pinned_at"], reverse=True)
    return out


def pinned_chat_ids() -> dict[str, str]:
    """{session_id: pinned_at} for live pinned chats (used to decorate the sidebar listing)."""
    return {item["session_id"]: item["pinned_at"] for item in list_pinned_chats()}


def set_chat_pinned(session_id: str, pinned: bool, *, allow_new_chat: bool = False) -> ActionResult:
    """Pin or unpin one chat to the top of the sidebar. Idempotent; read back before reporting.

    ``allow_new_chat`` lets the CURRENT chat be pinned on its very first message, before the
    transcript has a row for it (the sidebar already lists such a chat from its namespace/meta).
    """
    action = "pin_chat" if pinned else "unpin_chat"
    sid = str(session_id or "").strip()
    if not sid or len(sid) > 200 or any(ord(ch) < 32 for ch in sid):
        return ActionResult(False, action, error="invalid_session_id")
    if _chat_is_deleted(sid):
        return ActionResult(False, action, session_id=sid, error="chat_deleted")
    live = _live_sessions()
    if sid not in live and not allow_new_chat:
        return ActionResult(False, action, session_id=sid, error="chat_not_found")
    if sid not in live and allow_new_chat:
        # Give the new chat a namespace so the sidebar lists it before its first turn is logged.
        from core.context_namespace import ensure_chat_namespace, load_chat_namespace

        if load_chat_namespace(sid) is None:
            ensure_chat_namespace(sid)
    with _STORE_LOCK:
        store = _load_chat_pins()
        before = {"pinned": sid in store}
        if pinned and sid not in store:
            store[sid] = {"pinned_at": _utcnow()}
        elif not pinned:
            store.pop(sid, None)
        # Prune pins of chats that no longer exist; never resurrect a deleted chat.
        live_now = live if sid in live else {**live, sid: {}}
        store = {k: v for k, v in store.items() if k in live_now and not _chat_is_deleted(k)}
        _write_json_object(chat_pins_path(), store)
    after_store = _load_chat_pins()
    after = {"pinned": sid in after_store}
    if after["pinned"] != bool(pinned):
        return ActionResult(False, action, session_id=sid, error="write_not_confirmed", before=before, after=after)
    return ActionResult(True, action, session_id=sid, before=before, after=after, changed=before != after)


# --------------------------------------------------------------------------------------------
# Chat and project mutations (through the existing stores)
# --------------------------------------------------------------------------------------------


def _meta(sid: str) -> dict[str, Any]:
    from core.memory.entries import load_session_meta

    return dict(load_session_meta().get(sid) or {})


def _require_chat(sid: str, action: str, *, allow_new_chat: bool) -> ActionResult | None:
    if not sid or len(sid) > 200 or any(ord(ch) < 32 for ch in sid):
        return ActionResult(False, action, error="invalid_session_id")
    if _chat_is_deleted(sid):
        return ActionResult(False, action, session_id=sid, error="chat_deleted")
    if not allow_new_chat and sid not in _live_sessions():
        return ActionResult(False, action, session_id=sid, error="chat_not_found")
    return None


def rename_chat(session_id: str, title: str, *, allow_new_chat: bool = False) -> ActionResult:
    sid = str(session_id or "").strip()
    refused = _require_chat(sid, "rename_chat", allow_new_chat=allow_new_chat)
    if refused:
        return refused
    clean = " ".join(str(title or "").split()).strip()[:_TITLE_MAX]
    if not clean:
        return ActionResult(False, "rename_chat", session_id=sid, error="empty_title")
    from core.memory.entries import set_session_meta

    before = {"title": str(_meta(sid).get("title") or (_live_sessions().get(sid) or {}).get("title") or "")}
    set_session_meta(sid, title=clean)
    after = {"title": str(_meta(sid).get("title") or "")}
    if after["title"] != clean:
        return ActionResult(False, "rename_chat", session_id=sid, error="write_not_confirmed", before=before, after=after)
    return ActionResult(True, "rename_chat", session_id=sid, before=before, after=after, changed=before != after)


def set_chat_archived(session_id: str, archived: bool) -> ActionResult:
    action = "archive_chat" if archived else "restore_chat"
    sid = str(session_id or "").strip()
    refused = _require_chat(sid, action, allow_new_chat=False)
    if refused:
        return refused
    from core.memory.entries import set_session_meta

    before = {"archived": bool((_live_sessions().get(sid) or {}).get("archived"))}
    set_session_meta(sid, archived=bool(archived))
    after = {"archived": bool((_live_sessions().get(sid) or {}).get("archived"))}
    if after["archived"] != bool(archived):
        return ActionResult(False, action, session_id=sid, error="write_not_confirmed", before=before, after=after)
    return ActionResult(True, action, session_id=sid, before=before, after=after, changed=before != after)


#: Names that mean "no project" (the sidebar's General group).
_GENERAL_NAMES = frozenset({"general", "no project", "none", "nothing", "unassigned", "ungrouped"})


def resolve_project(reference: str) -> tuple[dict[str, Any] | None, list[dict[str, Any]], str]:
    """Find one project by id or name. Returns (project, candidates, error).

    Exact id, then exact case-insensitive name, then a unique word-prefix or containment match.
    Ambiguity is an error with its candidates, never a guess.
    """
    ref = " ".join(str(reference or "").split()).strip().strip("\"'“”‘’").strip()
    if not ref:
        return None, [], "empty_project_name"
    projects = list(_projects().values())
    by_id = [p for p in projects if str(p.get("id")) == ref]
    if by_id:
        return by_id[0], by_id, ""
    folded = _fold(ref)
    folded_trimmed = re.sub(r"\s+project$", "", folded).strip() or folded
    exact = [p for p in projects if _fold(str(p.get("name") or "")) in (folded, folded_trimmed)]
    if len(exact) == 1:
        return exact[0], exact, ""
    if len(exact) > 1:
        return None, exact, "ambiguous_project"
    partial = [
        p for p in projects
        if folded_trimmed and (
            _fold(str(p.get("name") or "")).startswith(folded_trimmed)
            or folded_trimmed in _fold(str(p.get("name") or ""))
        )
    ]
    if len(partial) == 1:
        return partial[0], partial, ""
    if len(partial) > 1:
        return None, partial, "ambiguous_project"
    return None, [], "project_not_found"


def move_chat_to_project(session_id: str, project_reference: str, *, allow_new_chat: bool = False) -> ActionResult:
    sid = str(session_id or "").strip()
    refused = _require_chat(sid, "move_chat", allow_new_chat=allow_new_chat)
    if refused:
        return refused
    ref = " ".join(str(project_reference or "").split()).strip()
    if _fold(ref).strip("\"' ") in _GENERAL_NAMES:
        target_id, target = "", {"name": "General"}
    else:
        project, candidates, error = resolve_project(ref)
        if project is None:
            return ActionResult(
                False, "move_chat", session_id=sid, error=error,
                after={"candidates": [str(c.get("name") or "") for c in candidates[:6]]},
            )
        target_id, target = str(project.get("id") or ""), project
    from core.memory.entries import set_session_meta

    before = {"project_id": str((_live_sessions().get(sid) or {}).get("project_id") or "")}
    set_session_meta(sid, project_id=target_id)
    current = _live_sessions().get(sid) or {"project_id": _meta(sid).get("project_id") or ""}
    after = {"project_id": str(current.get("project_id") or ""), "project_name": str(target.get("name") or "")}
    if after["project_id"] != target_id:
        return ActionResult(False, "move_chat", session_id=sid, project_id=target_id, error="write_not_confirmed", before=before, after=after)
    return ActionResult(True, "move_chat", session_id=sid, project_id=target_id, before=before, after=after, changed=before["project_id"] != target_id)


def rename_project(project_reference: str, new_name: str) -> ActionResult:
    """Rename a project through the registry's own idempotent-by-folder write."""
    project, candidates, error = resolve_project(project_reference)
    if project is None:
        return ActionResult(
            False, "rename_project", error=error,
            after={"candidates": [str(c.get("name") or "") for c in candidates[:6]]},
        )
    clean = " ".join(str(new_name or "").split()).strip()[:_PROJECT_NAME_MAX]
    pid = str(project.get("id") or "")
    if not clean:
        return ActionResult(False, "rename_project", project_id=pid, error="empty_project_name")
    root = str(project.get("root") or "")
    if not root or not os.path.isdir(root):
        # create_project refuses a missing folder, and a rename must not re-home a project.
        return ActionResult(False, "rename_project", project_id=pid, error="project_folder_missing")
    from core import project_store

    before = {"name": str(project.get("name") or "")}
    ok, payload = project_store.create_project(clean, root)
    after_project = project_store.get_project(pid) or {}
    after = {"name": str(after_project.get("name") or "")}
    if not ok or after["name"] != clean:
        return ActionResult(
            False, "rename_project", project_id=pid,
            error=("write_not_confirmed" if ok else str(payload or "rename_refused")),
            before=before, after=after,
        )
    return ActionResult(True, "rename_project", project_id=pid, before=before, after=after, changed=before != after)


def pin_message(session_id: str, role: str, text: str, ts: str = "") -> ActionResult:
    """Pin one message inside a chat through ``message_pins`` (idempotent by content)."""
    sid = str(session_id or "").strip()
    refused = _require_chat(sid, "pin_message", allow_new_chat=False)
    if refused:
        return refused
    from core import message_pins

    clean_role = "assistant" if role == "assistant" else "user"
    if clean_role == "assistant":
        if not _assistant_text_servable({}, text):
            return ActionResult(False, "pin_message", session_id=sid, error="message_not_servable")
    already = message_pins.is_pinned(sid, clean_role, str(text or "").strip()[:20000])
    ok, pin = message_pins.pin_message(sid, clean_role, text, ts=ts)
    if not ok:
        return ActionResult(False, "pin_message", session_id=sid, error="pin_refused")
    confirmed = any(p.get("id") == pin.get("id") for p in message_pins.list_pins(sid))
    if not confirmed:
        return ActionResult(False, "pin_message", session_id=sid, error="write_not_confirmed")
    return ActionResult(
        True, "pin_message", session_id=sid,
        before={"pinned": already}, after={"pinned": True, "pin_id": str(pin.get("id") or "")},
        changed=not already,
    )


def find_message(session_id: str, text_hash: str) -> dict[str, Any] | None:
    """The transcript message in ``session_id`` whose text hashes to ``text_hash``.

    Returns {role, text, ts} with governed assistant text refused, or None.
    """
    sid = str(session_id or "").strip()
    wanted = str(text_hash or "").strip()
    if not sid or not wanted:
        return None
    from core.memory.files import conversation_log_path, load_jsonl

    for row in reversed(load_jsonl(conversation_log_path())):
        if str(row.get("session_id") or "") != sid or str(row.get("artifact_kind") or "").strip():
            continue
        for role in ("user", "assistant"):
            text = str(row.get(role) or "").strip()
            if text and _text_hash(text) == wanted:
                if role == "assistant" and not _assistant_text_servable(row, text):
                    return None
                return {"role": role, "text": text, "ts": str(row.get("ts") or "")}
    return None


def last_assistant_message(session_id: str) -> dict[str, Any] | None:
    """The newest servable assistant message in a chat, for "pin your last answer"."""
    from core.memory.entries import recent_conversation_events

    for row in reversed(recent_conversation_events(session_id, limit=6)):
        text = str(row.get("assistant") or "").strip()
        if text and _assistant_text_servable(row, text):
            return {"role": "assistant", "text": text, "ts": str(row.get("ts") or "")}
    return None


# --------------------------------------------------------------------------------------------
# Follow-up memory ("pin 2" after a search) and handled-request ledger
# --------------------------------------------------------------------------------------------


def _load_state() -> dict[str, Any]:
    state = _read_json_object(state_path())
    if not isinstance(state.get("chats"), dict):
        state["chats"] = {}
    if not isinstance(state.get("handled"), list):
        state["handled"] = []
    return state


def remember_results(session_id: str, hits: list[dict[str, Any]], *, query: str) -> None:
    sid = str(session_id or "").strip()
    if not sid:
        return
    with _STORE_LOCK:
        state = _load_state()
        chats = state["chats"]
        chats[sid] = {"at": time.time(), "query": str(query or "")[:200], "hits": list(hits)[:_DEFAULT_HITS * 2]}
        newest = sorted(chats.items(), key=lambda item: float((item[1] or {}).get("at") or 0), reverse=True)
        state["chats"] = dict(newest[:_STATE_CHATS_MAX])
        _write_json_object(state_path(), state)


def recalled_results(session_id: str) -> dict[str, Any] | None:
    """The last search made in this chat, if it is still fresh."""
    entry = (_load_state()["chats"] or {}).get(str(session_id or "").strip())
    if not isinstance(entry, dict):
        return None
    if time.time() - float(entry.get("at") or 0) > _FOLLOWUP_TTL_SECONDS:
        return None
    hits = entry.get("hits")
    if not isinstance(hits, list) or not hits:
        return None
    return entry


def record_handled_request(session_id: str, user_text: str) -> None:
    """Remember a housekeeping request so later searches don't return the request itself."""
    fingerprint = _request_fingerprint(session_id, user_text)
    with _STORE_LOCK:
        state = _load_state()
        handled = [f for f in state["handled"] if isinstance(f, str) and f != fingerprint]
        handled.append(fingerprint)
        state["handled"] = handled[-_HANDLED_TURNS_MAX:]
        _write_json_object(state_path(), state)


def _handled_fingerprints() -> set[str]:
    return {f for f in _load_state()["handled"] if isinstance(f, str)}


# --------------------------------------------------------------------------------------------
# Search
# --------------------------------------------------------------------------------------------


@dataclass
class _ChatScore:
    best_turn: float = 0.0
    turn_hits: int = 0
    matched: set[str] = field(default_factory=set)
    snippet_text: str = ""
    snippet_role: str = ""
    snippet_ts: str = ""
    snippet_restricted: list[str] = field(default_factory=list)
    title_score: float = 0.0


@dataclass
class _Candidate:
    """One chat's matching turns before governance is checked (kept to its best few)."""

    user_terms: set[str] = field(default_factory=set)        # never governed: the operator's words
    rows: list[tuple[float, set[str], set[str], dict[str, Any]]] = field(default_factory=list)
    turn_hits: int = 0

    def bound(self) -> tuple[int, float]:
        """An upper bound on what this chat can still score once governed text is removed."""
        terms = set(self.user_terms)
        best = 0.0
        for score, user_terms, assistant_terms, _row in self.rows:
            terms |= user_terms | assistant_terms
            best = max(best, score)
        return len(terms), best


_ROWS_KEPT_PER_CHAT = 3
_GOVERNANCE_CHECKED_CHATS = 4   # x the result limit: chats whose best turns get an A8 check


def _prefilter_text(text: str) -> str:
    return text.lower() if text.isascii() else _fold(text)


def _term_matcher(terms: tuple[str, ...]):
    """terms found in a text, by the same rule as ``_term_in`` but stemming only candidate words.

    A word can only stem to (or extend) a term if it starts with that term, so a word-prefix scan
    finds every candidate; each candidate word is then stemmed and judged like ``_term_in``.
    """
    pattern = re.compile(r"(?<![^\W_])(?:" + "|".join(re.escape(t) for t in sorted(terms, key=len, reverse=True)) + r")[^\W_]*")

    def found(folded: str) -> set[str]:
        out: set[str] = set()
        for match in pattern.finditer(folded):
            stem = _stem(match.group(0).replace("'", ""))
            for term in terms:
                if term not in out and (stem == term or (len(term) >= 4 and stem.startswith(term) and len(stem) > len(term))):
                    out.add(term)
        return out

    return found


def search_chats(
    query: str,
    *,
    current_session_id: str = "",
    limit: int = _DEFAULT_HITS,
    include_archived: bool = True,
) -> SearchResult:
    """Rank the operator's chats by how well they match ``query``.

    Every non-deleted chat is searched: its transcript turns, its title, its project's name and
    its pinned messages. Scoring is IDF-weighted term overlap per turn (rare words count, filler
    counts ~0), best turn per chat, plus a title/project bonus and a small multi-turn bonus.

    Cost: one pass over the log with a substring prefilter, stemming only rows that can match,
    and an A8 availability check only for the few best turns of the chats that can still make
    the result (governed text is then dropped before anything is ranked or quoted).
    """
    terms = query_terms(query)
    if not terms:
        return SearchResult(str(query or ""), (), (), 0, 0, 0, 0, "", False, error="empty_query")
    from core.finalization import availability_read_scope
    from core.memory.files import MAX_CONVERSATION_LOG_BYTES, conversation_log_path, load_jsonl

    sessions = _live_sessions()
    projects = _projects()
    pins = pinned_chat_ids_from(sessions)
    handled = _handled_fingerprints()
    log_path = conversation_log_path()
    rows = load_jsonl(log_path)
    try:
        log_trimmed = log_path.stat().st_size >= MAX_CONVERSATION_LOG_BYTES // 2
    except OSError:
        log_trimmed = False

    matcher = _term_matcher(terms)
    # Pass 1: which turns can match at all, their stems, and document frequency.
    matches: list[tuple[str, dict[str, Any], set[str], set[str]]] = []
    df: dict[str, int] = {term: 0 for term in terms}
    oldest = ""
    searchable = 0
    for row in rows:
        sid = str(row.get("session_id") or "").strip()
        if not sid or sid not in sessions:
            continue
        if str(row.get("artifact_kind") or "").strip():
            continue
        if not include_archived and sessions[sid].get("archived"):
            continue
        user_text = str(row.get("user") or "")
        if user_text and _request_fingerprint(sid, user_text) in handled:
            continue
        searchable += 1
        ts = str(row.get("ts") or "")
        if ts and (not oldest or ts < oldest):
            oldest = ts
        assistant_text = str(row.get("assistant") or "")
        folded_user = _prefilter_text(user_text)
        folded_assistant = _prefilter_text(assistant_text)
        if not any(term in folded_user or term in folded_assistant for term in terms):
            continue
        user_terms = matcher(folded_user)
        assistant_terms = matcher(folded_assistant)
        if not user_terms and not assistant_terms:
            continue
        for term in user_terms | assistant_terms:
            df[term] += 1
        matches.append((sid, row, user_terms, assistant_terms))
    total = max(1, searchable)
    idf = {term: math.log(1.0 + (total + 1.0) / (df[term] + 0.5)) for term in terms}
    weight_all = sum(idf.values()) or 1.0

    def weight(found: set[str]) -> float:
        return sum(idf[term] for term in found) / weight_all

    # Pass 2: per chat, keep its best few turns (ungoverned bound) and every user-side term.
    candidates: dict[str, _Candidate] = {}
    for sid, row, user_terms, assistant_terms in matches:
        cand = candidates.setdefault(sid, _Candidate())
        cand.turn_hits += 1
        cand.user_terms |= user_terms
        cand.rows.append((weight(user_terms | assistant_terms), user_terms, assistant_terms, row))
        if len(cand.rows) > _ROWS_KEPT_PER_CHAT * 2:
            cand.rows.sort(key=lambda item: item[0], reverse=True)
            del cand.rows[_ROWS_KEPT_PER_CHAT:]
    for cand in candidates.values():
        cand.rows.sort(key=lambda item: item[0], reverse=True)
        del cand.rows[_ROWS_KEPT_PER_CHAT:]

    # Pass 3: governance on the chats that can still make the result, best bound first.
    ordered = sorted(candidates.items(), key=lambda item: item[1].bound(), reverse=True)
    checked = ordered[: max(1, int(limit)) * _GOVERNANCE_CHECKED_CHATS]
    per_chat: dict[str, _ChatScore] = {}
    governed_skipped = 0
    with availability_read_scope():
        for sid, cand in checked:
            entry = per_chat.setdefault(sid, _ChatScore())
            entry.turn_hits = cand.turn_hits
            entry.matched |= cand.user_terms
            title = " ".join(str((sessions.get(sid) or {}).get("title") or "").split())
            for _score, user_terms, assistant_terms, row in cand.rows:
                assistant_text = str(row.get("assistant") or "")
                if assistant_terms and not _assistant_text_servable(row, assistant_text.strip()):
                    governed_skipped += 1
                    assistant_terms = set()
                found = user_terms | assistant_terms
                if not found:
                    continue
                entry.matched |= found
                score = weight(found)
                if score <= entry.best_turn:
                    continue
                entry.best_turn = score
                # Quote the side that carries more of the matched weight; the user's own words win
                # ties, unless they ARE the chat's title (its first message), which the reply names.
                user_weight = weight(user_terms)
                assistant_weight = weight(assistant_terms)
                user_is_title = bool(title) and " ".join(str(row.get("user") or "").split()).startswith(title[:60])
                if user_is_title and assistant_terms:
                    user_weight = -1.0
                if user_weight >= assistant_weight and user_terms:
                    entry.snippet_text, entry.snippet_role = str(row.get("user") or ""), "user"
                else:
                    entry.snippet_text, entry.snippet_role = assistant_text, "assistant"
                entry.snippet_ts = str(row.get("ts") or "")
                entry.snippet_restricted = [str(x) for x in (row.get("restricted_terms") or []) if str(x).strip()]
            if not entry.matched:
                per_chat.pop(sid, None)

    # Titles and project names: a chat called "solana staking notes" is a hit on its own.
    for sid, session in sessions.items():
        if not include_archived and session.get("archived"):
            continue
        project_name = str((projects.get(str(session.get("project_id") or "")) or {}).get("name") or "")
        label_stems = _doc_stems(str(session.get("title") or "") + " " + project_name)
        label_terms = {term for term in terms if _term_in(term, label_stems)}
        if not label_terms:
            continue
        entry = per_chat.setdefault(sid, _ChatScore())
        entry.title_score = weight(label_terms)
        entry.matched |= label_terms
        if not entry.snippet_text:
            entry.snippet_text, entry.snippet_role = str(session.get("title") or ""), "title"

    # Pinned messages survive log trimming; search them for chats the log no longer covers well.
    _score_pinned_messages(per_chat, sessions, terms, idf, weight_all)

    hits: list[SearchHit] = []
    for sid, entry in per_chat.items():
        session = sessions.get(sid) or {}
        coverage = weight(entry.matched)
        score = (
            max(entry.best_turn, entry.title_score)
            + 0.35 * coverage
            + 0.25 * entry.title_score
            + 0.05 * min(entry.turn_hits, 5)
        )
        project = projects.get(str(session.get("project_id") or "")) or {}
        snippet_hash = _text_hash(entry.snippet_text) if entry.snippet_role in ("user", "assistant", "pinned_message") else ""
        hits.append(
            SearchHit(
                session_id=sid,
                title=str(session.get("title") or "New chat"),
                project_id=str(session.get("project_id") or ""),
                project_name=str(project.get("name") or ""),
                archived=bool(session.get("archived")),
                pinned=sid in pins,
                updated_at=str(session.get("updated_at") or ""),
                score=score,
                matched_terms=tuple(term for term in terms if term in entry.matched),
                snippet=_snippet(entry.snippet_text, terms, entry.snippet_restricted),
                snippet_role=entry.snippet_role,
                snippet_ts=entry.snippet_ts,
                snippet_text_hash=snippet_hash,
                is_current_chat=sid == str(current_session_id or ""),
            )
        )
    # Most terms matched first, then score, then recency.
    hits.sort(key=lambda h: (len(h.matched_terms), h.score, h.updated_at), reverse=True)
    if hits:
        best_cover = len(hits[0].matched_terms)
        # With several terms, a chat matching under half of what the best one matched is noise.
        floor = max(1, math.ceil(best_cover / 2)) if len(terms) > 1 else 1
        hits = [h for h in hits if len(h.matched_terms) >= floor]
    return SearchResult(
        query=str(query or ""),
        terms=terms,
        hits=tuple(hits[: max(1, int(limit))]),
        chats_searched=len(sessions),
        turns_searched=searchable,
        projects_searched=len(projects),
        governed_turns_skipped=governed_skipped,
        oldest_turn_ts=oldest,
        log_trimmed=log_trimmed,
        found_terms=tuple(term for term in terms if df.get(term) or any(term in h.matched_terms for h in hits)),
    )


def pinned_chat_ids_from(sessions: dict[str, dict[str, Any]]) -> dict[str, str]:
    return {item["session_id"]: item["pinned_at"] for item in list_pinned_chats(sessions=sessions)}


def _score_pinned_messages(
    per_chat: dict[str, _ChatScore],
    sessions: dict[str, dict[str, Any]],
    terms: tuple[str, ...],
    idf: dict[str, float],
    weight_all: float,
) -> None:
    from core import message_pins

    raw = _read_json_object(message_pins.pins_path())
    for sid, pins in raw.items():
        if sid not in sessions or not isinstance(pins, list):
            continue
        candidate = [
            p for p in pins
            if isinstance(p, dict) and any(_term_in(t, _doc_stems(str(p.get("text") or ""))) for t in terms)
        ]
        if not candidate:
            continue
        servable = {p.get("id"): p for p in message_pins.list_servable_pins(sid)}  # A8 gate
        for pin in candidate:
            if pin.get("id") not in servable:
                continue
            text = str(pin.get("text") or "")
            matched = {t for t in terms if _term_in(t, _doc_stems(text))}
            score = sum(idf[t] for t in matched) / weight_all
            entry = per_chat.setdefault(sid, _ChatScore())
            entry.matched |= matched
            entry.turn_hits += 1
            if score > entry.best_turn:
                entry.best_turn = score
                entry.snippet_text, entry.snippet_role = text, "pinned_message"
                entry.snippet_ts = str(pin.get("ts") or pin.get("pinned_at") or "")
                entry.snippet_restricted = []


__all__ = [
    "SCHEMA",
    "STOPWORDS",
    "ActionResult",
    "SearchHit",
    "SearchResult",
    "chat_pins_path",
    "describe_chat",
    "find_message",
    "last_assistant_message",
    "list_pinned_chats",
    "move_chat_to_project",
    "pin_message",
    "pinned_chat_ids",
    "query_terms",
    "recalled_results",
    "record_handled_request",
    "remember_results",
    "rename_chat",
    "rename_project",
    "resolve_project",
    "search_chats",
    "set_chat_archived",
    "set_chat_pinned",
    "state_path",
    "tokenize",
]
