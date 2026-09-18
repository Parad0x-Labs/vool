"""Notes the way the mission's law demands: real files in the real workspace, linked.

A note is a Markdown FILE under the active workspace's ``notes/`` directory — the same
workspace authority the runtime's file tools resolve (`core.runtime_paths.
resolve_workspace_root`), so project isolation is the workspace root's own boundary and no
second notes framework exists. What this module adds on top of the file is only the honest
metadata: title, creating session, timestamps, and — when a note backs a calendar event —
the REAL provider receipt (uid/etag) of that event, written only after the event verifiably
exists.

Truth rules this module keeps:

* create/read/update/search operate on the file's own bytes; a search reports matches,
  never invented content;
* isolation is positional: a different workspace root is a different note universe, and a
* session identity is recorded and reported, not used to hide project-mates' notes;
* the action-to-proposal seam: a note can carry explicit ``[action]`` lines; turning one
  into a calendar event is a PROPOSAL the user must approve — the link is only written
  when the provider receipt exists.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.operator.models import OperatorActionIntent, OperatorActionResult

_MAX_NOTES_LISTED = 50
_SLUG_RE = re.compile(r"[^a-z0-9\-_]+")
_ACTION_LINE_RE = re.compile(r"^\s*(?:\[action\]|action:)\s*(.+)$", re.IGNORECASE | re.MULTILINE)


def _workspace_root() -> Path:
    from core.runtime_paths import resolve_workspace_root

    return Path(resolve_workspace_root()).resolve()


def _confined(relative: str | Path) -> Path:
    """One path in, one verified path out — the EXISTING workspace confinement authority.

    `resolve_workspace_path` is the same hard boundary the file tools use: it resolves
    symlinks and REFUSES anything that lands outside the active project, raising the
    load-bearing "escapes the active workspace" error. Every note operation goes through
    it, so a `notes/` symlink (or any linked file) pointing into another project is
    rejected before a byte is read or written — project isolation is positional AND
    resolved, not just textual.
    """
    from core.execution.workspace_tools import resolve_workspace_path

    return resolve_workspace_path(str(relative), workspace_root=_workspace_root())


def notes_root(*, workspace_root: Path | None = None) -> Path:
    """The notes directory under the ACTIVE workspace root (the real file authority).

    The directory itself must resolve INSIDE the project: a `notes` symlink aimed at
    another project's folder is a confinement breach, not a notes directory, and using
    it is refused with the workspace-authority error.
    """
    if workspace_root is not None:
        base = Path(workspace_root).resolve()
        candidate = base / "notes"
        resolved = candidate.resolve()
        if resolved != base and base not in resolved.parents:
            raise ValueError(
                f"Path escapes the active workspace ({base.name}). "
                f"The notes directory resolves outside the project ({resolved}); "
                "use a real directory inside the bound project folder."
            )
        return candidate
    return _confined("notes")


def _slugify(title: str) -> str:
    slug = _SLUG_RE.sub("-", str(title or "").strip().lower()).strip("-")
    return (slug or "note")[:60]


def _note_path(title: str, *, unique: bool = True) -> Path:
    slug = _slugify(title)
    name = f"{slug}.md" if not unique else f"{slug}-{uuid.uuid4().hex[:8]}.md"
    return _confined(f"notes/{name}")


def _front_matter(title: str, *, session_id: str, linked_event_uid: str = "", linked_action_id: str = "") -> str:
    created = datetime.now(timezone.utc).isoformat(timespec="seconds")
    fields = {
        "title": title,
        "created": created,
        "updated": created,
        "session": session_id,
        "linked_event_uid": linked_event_uid,
        "linked_action_id": linked_action_id,
    }
    body = "\n".join(f"{key}: {value}" for key, value in fields.items() if value != "" or key in {"title", "created", "updated", "session"})
    return f"---\n{body}\n---\n"


def _split_front_matter(text: str) -> tuple[dict[str, str], str]:
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---\n", 4)
    if end < 0:
        return {}, text
    meta: dict[str, str] = {}
    for line in text[4:end].splitlines():
        key, sep, value = line.partition(":")
        if sep:
            meta[key.strip()] = value.strip()
    return meta, text[end + 5:]


def _iter_note_files() -> list[Path]:
    """Real note files inside the confined directory — symlinks that resolve outside the
    project are skipped, never read."""
    root = notes_root()
    if not root.is_dir():
        return []
    files: list[Path] = []
    for path in sorted(root.glob("*.md")):
        try:
            confined = _confined(f"notes/{path.name}")
        except ValueError:
            continue  # a link out of the project is not this project's note
        if confined.is_file():
            files.append(confined)
    return files


def create_note(
    *,
    title: str,
    body: str,
    session_id: str,
    linked_event_uid: str = "",
    linked_action_id: str = "",
) -> dict[str, Any]:
    """Write the note file atomically; return the real artifact reference."""
    from core.execution.artifacts import atomic_write_text

    path = _note_path(title)
    atomic_write_text(path, f"{_front_matter(title, session_id=session_id, linked_event_uid=linked_event_uid, linked_action_id=linked_action_id)}\n{str(body).strip()}\n")
    return {"note_path": str(path), "title": title, "session_id": session_id}


def read_note(*, title: str) -> dict[str, Any] | None:
    """Exact-title read. Front matter and body are the file's own content."""
    candidates = [path for path in _iter_note_files() if _split_front_matter(path.read_text(encoding="utf-8"))[0].get("title", "").casefold() == str(title).casefold()]
    if len(candidates) != 1:
        if not candidates:
            return None
        return {"ambiguous": True, "matches": [str(path) for path in candidates]}
    meta, body = _split_front_matter(candidates[0].read_text(encoding="utf-8"))
    return {"note_path": str(candidates[0]), **meta, "body": body.strip()}


def update_note(*, title: str, new_body: str | None = None, append: str | None = None, new_title: str | None = None) -> dict[str, Any]:
    """Update a note's body (replace or append) and/or retitle it, preserving metadata."""
    from core.execution.artifacts import atomic_write_text

    candidates = [path for path in _iter_note_files() if _split_front_matter(path.read_text(encoding="utf-8"))[0].get("title", "").casefold() == str(title).casefold()]
    if len(candidates) != 1:
        if not candidates:
            return {"ok": False, "status": "not_found"}
        return {"ok": False, "status": "ambiguous", "matches": [str(path) for path in candidates]}
    path = candidates[0]
    meta, body = _split_front_matter(path.read_text(encoding="utf-8"))
    updated_body = body.strip()
    if new_body is not None:
        updated_body = str(new_body).strip()
    elif append is not None:
        updated_body = (updated_body + "\n" + str(append).strip()).strip()
    meta["updated"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    final_title = str(new_title or meta.get("title") or title)
    meta["title"] = final_title
    front = "\n".join(f"{key}: {value}" for key, value in meta.items() if value != "")
    atomic_write_text(path, f"---\n{front}\n---\n\n{updated_body}\n")
    if new_title and _slugify(new_title) != _slugify(title):
        renamed = _note_path(new_title, unique=False)
        if not renamed.exists():
            path.rename(renamed)
            path = renamed
    return {"ok": True, "status": "updated", "note_path": str(path), "title": final_title}


def archive_note(*, title: str) -> dict[str, Any]:
    """A reviewed, recoverable delete: the note moves to notes/.archive/ and stops being
    listed, read, searched or linked; nothing is erased.

    Archived notes keep their front matter (title, session, event links) plus an archived
    timestamp, so a restore returns exactly what was archived.
    """
    candidates = [path for path in _iter_note_files() if _split_front_matter(path.read_text(encoding="utf-8"))[0].get("title", "").casefold() == str(title).casefold()]
    if len(candidates) != 1:
        if not candidates:
            return {"ok": False, "status": "not_found"}
        return {"ok": False, "status": "ambiguous", "matches": [str(path) for path in candidates]}
    path = candidates[0]
    meta, body = _split_front_matter(path.read_text(encoding="utf-8"))
    meta["archived_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    front = "\n".join(f"{key}: {value}" for key, value in meta.items() if value != "")
    archive_dir = notes_root() / ".archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    target = archive_dir / path.name
    if target.exists():
        return {"ok": False, "status": "archive_name_taken", "detail": f"an archived note already occupies {target.name}"}
    from core.execution.artifacts import atomic_write_text

    atomic_write_text(target, f"---\n{front}\n---\n\n{body.strip()}\n")
    path.unlink()
    return {"ok": True, "status": "archived", "note_path": str(target), "title": str(meta.get("title") or title),
            "recovery": f"say: restore the note \"{title}\" to bring it back unchanged"}


def restore_note(*, title: str) -> dict[str, Any]:
    """Bring an archived note back: same front matter (minus the archive stamp), same body."""
    archive_dir = notes_root() / ".archive"
    if not archive_dir.is_dir():
        return {"ok": False, "status": "not_found"}
    candidates = [path for path in sorted(archive_dir.glob("*.md"))
                  if _split_front_matter(path.read_text(encoding="utf-8"))[0].get("title", "").casefold() == str(title).casefold()]
    if len(candidates) != 1:
        if not candidates:
            return {"ok": False, "status": "not_found"}
        return {"ok": False, "status": "ambiguous", "matches": [str(path) for path in candidates]}
    path = candidates[0]
    meta, body = _split_front_matter(path.read_text(encoding="utf-8"))
    meta.pop("archived_at", None)
    meta["updated"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    front = "\n".join(f"{key}: {value}" for key, value in meta.items() if value != "")
    restored = _note_path(str(meta.get("title") or title))
    from core.execution.artifacts import atomic_write_text

    atomic_write_text(restored, f"---\n{front}\n---\n\n{body.strip()}\n")
    path.unlink()
    return {"ok": True, "status": "restored", "note_path": str(restored), "title": str(meta.get("title") or title)}


def search_notes(*, query: str) -> list[dict[str, Any]]:
    """Deterministic text search over title+body. Reports matches; invents nothing."""
    terms = [term.casefold() for term in re.split(r"\s+", str(query or "").strip()) if term]
    results: list[dict[str, Any]] = []
    for path in _iter_note_files():
        try:
            meta, body = _split_front_matter(path.read_text(encoding="utf-8"))
        except OSError:
            continue
        haystack = f"{meta.get('title', '')}\n{body}".casefold()
        if terms and not all(term in haystack for term in terms):
            continue
        results.append(
            {
                "note_path": str(path),
                "title": meta.get("title", path.stem),
                "session": meta.get("session", ""),
                "linked_event_uid": meta.get("linked_event_uid", ""),
                "excerpt": body.strip()[:200],
            }
        )
        if len(results) >= _MAX_NOTES_LISTED:
            break
    return results


def link_note_to_event(note_path: str, *, event_uid: str, action_id: str = "") -> bool:
    """Write the provider receipt into the note AFTER the event verifiably exists.

    Called only from the calendar execution path that just verified the event, so the link
    is evidence, not hope. The stored path is re-confined to the active project before any
    byte is written — a path that resolves outside the workspace is refused, not written.
    """
    try:
        path = _confined(str(Path(str(note_path or "")).expanduser()))
    except ValueError:
        return False
    if not path.is_file():
        return False
    from core.execution.artifacts import atomic_write_text

    meta, body = _split_front_matter(path.read_text(encoding="utf-8"))
    meta["linked_event_uid"] = str(event_uid)
    if action_id:
        meta["linked_action_id"] = str(action_id)
    meta["updated"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    front = "\n".join(f"{key}: {value}" for key, value in meta.items() if value != "")
    atomic_write_text(path, f"---\n{front}\n---\n\n{body.strip()}\n")
    return True


def note_action_items(note_path: str) -> list[str]:
    """The note's EXPLICIT action lines (`[action] ...` or `action: ...`), verbatim."""
    try:
        path = _confined(str(Path(str(note_path or "")).expanduser()))
    except ValueError:
        return []
    if not path.is_file():
        return []
    _meta, body = _split_front_matter(path.read_text(encoding="utf-8"))
    return [match.group(1).strip() for match in _ACTION_LINE_RE.finditer(body)]


def find_named_action(named: str) -> tuple[str, str] | tuple[None, None]:
    """Find a note's action line by name: the exact action the USER quoted.

    Returns (note_path, action_line). The named text must appear as (or inside) one
    explicit action line — a note that merely mentions the words is not an action.
    """
    wanted = str(named or "").strip().casefold()
    if not wanted:
        return None, None
    for path in _iter_note_files():
        for action in note_action_items(str(path)):
            if wanted in action.casefold() or action.casefold() in wanted:
                return str(path), action
    return None, None


_WHEN_TAIL_RE = re.compile(
    r"\s+(?:at|on|in|by)\s+("
    r"(?:20\d{2}-\d{2}-\d{2})(?:[ T]\d{1,2}:\d{2})?"
    r"|(?:today|tomorrow|next\s+)?\s*(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b.*"
    r"|\d+(?:\.\d+)?\s*(?:minutes?|mins?|hours?|hrs?|h|days?|d|weeks?|w)\b.*"
    r"|(?:at\s+)?\d{1,2}(?::\d{2})?\s*(?:am|pm)?\b.*"
    r")$",
    re.IGNORECASE | re.DOTALL,
)


def split_action_time(action_line: str) -> tuple[str, str]:
    """An action line into (title, when-text). The trailing when-clause is the time; the
    rest is the title. No time clause -> the whole line is the title and the caller asks."""
    line = str(action_line or "").strip()
    tail = _WHEN_TAIL_RE.search(line)
    if tail:
        title = line[: tail.start()].strip(" .,:")
        return title, line[tail.start():].strip()
    return line, ""


# ---------------------------------------------------------------------------
# Intent parsing (deterministic; a missing title/body is a typed clarification)
# ---------------------------------------------------------------------------


def parse_note_request(text: str) -> dict[str, Any]:
    """`save a note titled "X" with: body` / `save a note with agenda: body` / etc.

    The title is explicit when quoted or after 'titled/called/about'; otherwise the
    user's own field word before the colon IS the title ("save a note with agenda:
    packaging..." -> title "Agenda"). Never an invented "Note".
    """
    raw = str(text or "").strip()
    quoted = re.findall(r'["\u201c]([^"\u201d]+)["\u201d]', raw)
    title = quoted[0].strip() if quoted else ""
    body = ""
    labeled = re.search(r"\bwith\s+([A-Za-z][\w \-]{0,30}?)\s*:\s*(.+)$", raw, re.IGNORECASE | re.DOTALL)
    bare = re.search(r"\bwith\s*:\s*(.+)$", raw, re.IGNORECASE | re.DOTALL)
    unlabeled = re.search(r"\b(?:saying|that\s+says|containing|with\s+body|with\s+text)\s*:\s*(.+)$", raw, re.IGNORECASE | re.DOTALL)
    if labeled:
        body = labeled.group(2).strip()
        if not title:
            title = labeled.group(1).strip().capitalize()
    elif bare:
        body = bare.group(1).strip()
    elif unlabeled:
        body = unlabeled.group(1).strip()
    elif quoted and len(quoted) >= 2:
        body = quoted[1].strip()
    wants_schedule = bool(re.search(r"\b(?:and\s+)?(?:schedule|add\s+it\s+to\s+(?:the\s+)?calendar|put\s+it\s+on\s+(?:the\s+)?calendar|book\s+it)\b", raw, re.IGNORECASE))
    return {"title": title, "body": body, "wants_schedule": wants_schedule}


def handle_save_note(
    intent: OperatorActionIntent,
    *,
    task_id: str,
    session_id: str,
    evaluate_local_action_fn: Any,
    audit_log_fn: Any,
) -> OperatorActionResult:
    """Save a note in the active workspace. A `[action]`-marked body can be proposed as an
    event, but only through the provider proposal path with its own approval."""
    gate = evaluate_local_action_fn("save_note", destructive=False, user_approved=True, writes_workspace=True)
    if gate.mode not in {"execute", "sandbox"}:
        return OperatorActionResult(ok=False, status="blocked", response_text=f"I can't save notes right now: {gate.reason}", details={"gate_mode": gate.mode})
    parsed = parse_note_request(intent.raw_text)
    title = parsed["title"]
    body = parsed["body"]
    if not title and body:
        title = "Note"
    if not body:
        return OperatorActionResult(
            ok=False,
            status="invalid_request",
            response_text='I can save a note, but I need its content. Use a form like: save a note titled "Agenda" with: packaging and release checks.',
            details={},
        )
    from core.operator.apple_notes import parse_notes_destination

    # The body is data: "with: move it into the archive folder" names no destination.
    destination = parse_notes_destination(intent.raw_text, data=(body,))
    if destination["wants_apple_notes"] != "True":
        record = create_note(title=title, body=body, session_id=session_id)
        audit_log_fn(
            "operator_action_save_note",
            target_id=task_id,
            target_type="task",
            details={"note_path": record["note_path"], "title": title, "requested_destination": "workspace"},
        )
        actions = note_action_items(record["note_path"])
        response = f"Note saved: '{title}' at {record['note_path']}"
        details: dict[str, Any] = {**record, "actions_found": actions}
        if actions and parsed["wants_schedule"]:
            details["schedule_proposal_hint"] = actions[0]
            response += _schedule_hint_text(actions[0])
        return OperatorActionResult(ok=True, status="executed", response_text=response, details=details)

    # Destination truth: a request that NAMES Apple Notes goes through the genuine
    # permission-aware native route, as ONE durable delivery operation for this request
    # (see the delivery section below). On confirmed creation the note exists in Apple Notes.
    # Otherwise the content is kept where this runtime verifiably can -- one clearly-labelled
    # workspace fallback per operation -- and the exact gap is said out loud: a workspace file
    # is never called an Apple Notes note.
    delivered = _deliver_apple_note_effect(
        title=title, body=body, account=destination["account"], folder=destination["folder"],
        task_id=task_id, session_id=session_id,
    )
    operation_id = str(delivered.get("operation_id") or "")
    where = (
        (f" (folder '{destination['folder']}')" if destination["folder"] else "")
        + (f" (account '{destination['account']}')" if destination["account"] else "")
    )
    if delivered.get("ok"):
        audit_log_fn(
            "operator_action_save_note",
            target_id=task_id,
            target_type="task",
            details={"title": title, "requested_destination": "apple_notes", "apple_note_reference": delivered.get("note_reference", ""),
                     "operation_id": operation_id, "replayed": bool(delivered.get("replayed"))},
        )
        response = f"Note saved in Apple Notes: '{title}'{where}. Created through the real Notes app bridge; verify it in Notes."
        if delivered.get("replayed"):
            response += " This same request was already delivered, so I answered from its recorded receipt; nothing was created twice."
        earlier = delivered.get("identical_earlier_note")
        if isinstance(earlier, dict):
            response += (" An earlier, separate request had already created a note with identical content"
                         f" (reference {str(earlier.get('note_reference') or 'unknown')[:80]}); this new request created its own.")
        if delivered.get("receipt_unrecorded"):
            response += (" WARNING: the note was created, but its delivery record could not be saved. Repeating this same"
                         " request will not send it again; check Notes before asking for another copy.")
        return OperatorActionResult(
            ok=True,
            status="executed",
            response_text=response,
            details={"destination": "apple_notes", "note_reference": delivered.get("note_reference", ""), "title": title,
                     "operation_id": operation_id, "replayed": bool(delivered.get("replayed")),
                     "receipt_unrecorded": bool(delivered.get("receipt_unrecorded"))},
        )
    reason = str(delivered.get("reason") or "unavailable")
    detail = str(delivered.get("detail") or "")
    delivery_state = str(delivered.get("delivery_state") or "")
    if reason == "delivery_in_progress":
        return OperatorActionResult(
            ok=False,
            status="delivery_in_progress",
            response_text=("That Apple Notes delivery is being carried out right now by another request. Nothing was sent twice"
                           " and no extra copy was saved. " + detail).strip(),
            details={"destination": "apple_notes", "requested_destination": "apple_notes", "destination_partial": True,
                     "destination_reason": reason, "delivery_state": delivery_state, "operation_id": operation_id, "title": title},
        )
    record = _fallback_note_for_operation(title=title, body=body, session_id=session_id, operation_id=operation_id)
    audit_log_fn(
        "operator_action_save_note",
        target_id=task_id,
        target_type="task",
        details={"note_path": record["note_path"], "title": title, "requested_destination": "apple_notes",
                 "destination_reason": reason, "delivery_state": delivery_state, "operation_id": operation_id},
    )
    actions = note_action_items(record["note_path"])
    response = (
        f"Note saved: '{title}' at {record['note_path']}"
        f"\n\nDestination note: you asked for Apple Notes{where} and that destination is NOT resolved ({reason}). {detail}"
        " A file under workspace/notes is NOT an Apple Notes note — what I saved is a clearly-labelled workspace fallback"
        " artifact, and the Apple Notes obligation remains open. " + _destination_guidance(delivery_state)
    )
    details_out: dict[str, Any] = {**record, "actions_found": actions, "destination": "workspace_fallback", "requested_destination": "apple_notes",
                                   "destination_partial": True, "destination_reason": reason, "delivery_state": delivery_state,
                                   "operation_id": operation_id,
                                   # The fallback write is an executed effect of this failed request (see
                                   # `operator_step_executed`); a reused fallback wrote nothing this time.
                                   "files_modified": not bool(record.get("reused"))}
    if actions and parsed["wants_schedule"]:
        details_out["schedule_proposal_hint"] = actions[0]
        response += _schedule_hint_text(actions[0])
    return OperatorActionResult(ok=False, status=reason, response_text=response, details=details_out)


def _schedule_hint_text(action: str) -> str:
    return (
        "\n\nThe note marks an action: \"" + action + "\". Say 'propose an event for that action' "
        "(with a time) and I will prepare a calendar proposal you can approve."
    )


def _destination_guidance(delivery_state: str) -> str:
    """What the user can do next, given the delivery operation's recorded state."""
    if delivery_state == "refused":
        return ("Nothing reached Notes. Fix the cause (for Automation access: System Settings > Privacy & Security >"
                " Automation), then ask again — a new request makes one new attempt.")
    if delivery_state == "unresolved":
        return ("Whether the note reached Notes is not established, so I will not send it again on my own — that could"
                " create a duplicate. Check Apple Notes for it.")
    if delivery_state == "not_dispatched":
        return "Nothing was sent to Notes."
    return "Check Notes yourself before asking again."


_NAMED_ACTION_RE = re.compile(
    r'["“](?P<action>[^"”]+)["”]\s+action\s+from\s+(?:my\s+)?notes?\b'
    r'|\baction\s+["“](?P<action2>[^"”]+)["”]\s+from\s+(?:my\s+)?notes?\b',
    re.IGNORECASE,
)


def named_action_reference(text: str) -> tuple[str, str] | None:
    """A request that names a note's action ('the "call the venue" action from my notes'): (the named action, the
    request's words after the reference), or None."""
    raw = str(text or "")
    match = _NAMED_ACTION_RE.search(raw)
    if not match:
        return None
    return match.group("action") or match.group("action2") or "", raw[match.end():].strip()


def find_notes_query(intent: OperatorActionIntent) -> str:
    """What a notes search looks for: the parsed query, or the request with its leading 'find my notes about' removed."""
    return intent.target_label or re.sub(r"^\s*(?:find|search|show|list)\s+(?:my\s+)?notes?\s+(?:about|on|for|containing|matching|with)?\s*", "", intent.raw_text, flags=re.IGNORECASE).strip()


def handle_find_notes(
    intent: OperatorActionIntent,
    *,
    task_id: str,
    session_id: str,
    audit_log_fn: Any,
) -> OperatorActionResult:
    del session_id
    query = find_notes_query(intent)
    if not query:
        rows = search_notes(query="")
        lines = [f"Notes in this workspace ({len(rows)}):"] if rows else ["There are no notes in this workspace yet."]
        for row in rows:
            lines.append(f"- {row['title']} [{row['note_path']}]" + (f" (event {row['linked_event_uid'][:8]})" if row["linked_event_uid"] else ""))
        return OperatorActionResult(ok=True, status="reported", response_text="\n".join(lines), details={"count": len(rows), "notes": rows})
    rows = search_notes(query=query)
    audit_log_fn(
        "operator_action_find_notes",
        target_id=task_id,
        target_type="task",
        details={"query": query, "matches": len(rows)},
    )
    if not rows:
        return OperatorActionResult(ok=True, status="reported", response_text=f"No notes in this workspace match {query!r}.", details={"query": query, "count": 0})
    lines = [f"Notes matching {query!r} ({len(rows)}):"]
    for row in rows:
        lines.append(f"- {row['title']}: {row['excerpt']}" + (f" — linked event {row['linked_event_uid'][:8]}" if row["linked_event_uid"] else ""))
        lines.append(f"  {row['note_path']}")
    return OperatorActionResult(ok=True, status="reported", response_text="\n".join(lines), details={"query": query, "count": len(rows), "notes": rows})


def handle_show_note(
    intent: OperatorActionIntent,
    *,
    task_id: str,
    session_id: str,
    audit_log_fn: Any,
) -> OperatorActionResult:
    del session_id, task_id
    title = intent.target_label or ""
    record = read_note(title=title)
    if record is None:
        return OperatorActionResult(ok=False, status="not_found", response_text=f"No note titled {title!r} in this workspace.", details={"title": title})
    if record.get("ambiguous"):
        return OperatorActionResult(ok=False, status="ambiguous", response_text=f"Several notes share that title: {', '.join(record['matches'])}. Retitle or point me at the file.", details={"matches": record["matches"]})
    audit = record.get("note_path", "")
    lines = [
        f"Note: {record.get('title', audit)}",
        f"Saved by session {record.get('session', 'unknown')} · created {record.get('created', 'unknown')} · updated {record.get('updated', 'unknown')}",
    ]
    if record.get("linked_event_uid"):
        lines.append(f"Linked calendar event: uid {record['linked_event_uid']}")
    lines.append("")
    lines.append(str(record.get("body", "")))
    return OperatorActionResult(ok=True, status="reported", response_text="\n".join(lines), details={"note_path": audit, "title": record.get("title", "")})


# ---------------------------------------------------------------------------
# Native Apple Notes delivery: one durable operation per explicit request
# ---------------------------------------------------------------------------
#
# The shared lifecycle law is core.operator.effect_lifecycle; this is its native-notes store.
# A delivery operation's identity is the REQUEST (session + task) plus the exact content and
# destination: replaying one request reaches its recorded outcome, while a new explicit
# request is a new operation judged against its identical-content siblings. A mutation's
# content is the request AS ASKED (kind, title, the account/folder it named, payload); the note
# its resolution found is recorded beside it as provenance, never as its identity. States:
#
#   dispatching  reserved durably BEFORE the bridge is invoked; the reserving worker holds the
#                operation's owner lock while it crosses the native boundary
#   confirmed    the bridge returned a real note reference (acceptance: this runtime has no
#                native Notes read-back, so that reference is the strongest evidence available)
#   refused      the bridge proved nothing reached Notes (Automation denial, missing executable,
#                unaddressable app/folder, a script osascript would not compile): known unsent,
#                so a NEW explicit request may make one new attempt
#   ambiguous    timeout, empty reference, a bridge that failed or was terminated after it
#                started, or the reserving worker vanished mid-dispatch: never re-invoked, and it
#                keeps identical content from being sent again
#
# The journal stays workspace-scoped (a project's deliveries are its own). It also carries a
# revision-4-compatible index at top level, so an older build reading it never re-sends a
# confirmed or unresolved note (see _journal_document).

_JOURNAL_SCHEMA = "vool.apple_notes_delivery_journal.v2"
_JOURNAL_FILE = ".apple-notes-effects.json"
_JOURNAL_LOCK_FILE = ".apple-notes-effects.lock"
_OWNER_LOCK_DIR = ".apple-notes-effects.owners"
_QUARANTINE_PREFIX = ".apple-notes-effects.quarantine-"
#: WALL-CLOCK bound on waiting for the journal lock. Its critical sections only read and write a
#: small JSON file; a holder past this bound is wedged, so the caller fails closed (nothing sent).
_JOURNAL_LOCK_WAIT_SECONDS = 2.0
_DELIVERY_STATES = frozenset({"dispatching", "confirmed", "refused", "ambiguous"})
#: Bridge reasons that PROVE nothing reached Notes (see ``apple_notes._classify``). A failure after the
#: bridge started is never one of them, whatever its exit code or exception type.
_KNOWN_UNSENT_REASONS = frozenset({"os_permission_denied", "notes_app_unavailable", "osascript_unavailable", "notes_script_rejected"})
_V4_STATUS_TO_STATE = {
    "executed": "confirmed",
    "failed": "refused",
    "outcome_unproven": "ambiguous",
    "effect_unrecorded": "ambiguous",
    "executing": "ambiguous",
    "pending": "ambiguous",
}
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
#: A current operation: ONE explicit request's delivery (see ``_apple_note_operation_id``).
_CURRENT_OPERATION_ID = re.compile(r"^op-[0-9a-f]{40}$")
#: An imported revision-4 content-only record (see ``_normalize_journal``): named for its content key and the
#: state it was imported in.
_LEGACY_OPERATION_ID = re.compile(r"^legacy-(?P<content>[0-9a-f]{40})-(?P<state>confirmed|refused|ambiguous)$")
#: Fields only a request's own reservation writes. An imported content-only record never carries them.
_REQUEST_IDENTITY_FIELDS = ("session_id", "task_id", "attempt", "owner", "reserved_at")


class _JournalUnavailableError(Exception):
    """The delivery journal cannot be trusted right now; nothing may be dispatched."""

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(detail)
        self.reason = reason
        self.detail = detail


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _apple_note_effect_key(*, title: str, body: str, account: str, folder: str) -> str:
    """The content + destination digest of a requested native note (identical content matches)."""
    return hashlib.sha256("\0".join([title, body, account, folder]).encode("utf-8")).hexdigest()


def _apple_note_operation_id(*, session_id: str, task_id: str, content_key: str) -> str:
    """ONE explicit request's delivery operation.

    The request identity (session + task) is part of it: a replay of the same request reaches
    the same recorded outcome, while a new explicit request -- even with identical text -- is a
    new operation. Content alone is never the operation, so a definitive refusal cannot become
    a permanent answer for that text.
    """
    seed = "\0".join(["apple_notes.create", str(session_id or ""), str(task_id or ""), content_key])
    return "op-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:40]


def _operation_id_derives(op_id: str, *, session_id: str, task_id: str, content_key: str) -> bool:
    """Whether a request identity and a content key derive exactly this operation id.

    The one statement of that relationship, applied where a journal is loaded (to the identity a record
    carries) and where a replay selects a record (to the identity of the request being answered).
    """
    return _apple_note_operation_id(session_id=session_id, task_id=task_id, content_key=content_key) == op_id


def _apple_note_effects_journal() -> Path:
    """The workspace-scoped journal of native-notes delivery operations.

    Deliberately under the ACTIVE WORKSPACE (through the confinement authority), not the shared
    runtime home: a delivery belongs to the project that asked for it, sibling projects never
    see each other's delivery state, and a fresh project starts with a clean slate -- the same
    isolation the notes themselves have.
    """
    return _confined(f"notes/{_JOURNAL_FILE}")


def _journal_lock():
    from core.operator.effect_lifecycle import OwnerLock

    return OwnerLock(_confined(f"notes/{_JOURNAL_LOCK_FILE}"))


def _owner_lock_for(operation_id: str):
    from core.operator.effect_lifecycle import OwnerLock, owner_lock_path

    return OwnerLock(owner_lock_path(_confined(f"notes/{_OWNER_LOCK_DIR}"), operation_id))


def _quarantined_journals() -> list[Path]:
    root = notes_root()
    if not root.is_dir():
        return []
    return sorted(path for path in root.glob(f"{_QUARANTINE_PREFIX}*") if path.is_file())


def _empty_journal() -> dict[str, Any]:
    return {"schema": _JOURNAL_SCHEMA, "operations": {}, "quarantined": []}


#: Text fields an operation or a revision-4 entry may carry. A value of any other type is damage: an answer
#: built from it (a note reference rebuilt with str()) would claim a delivery nobody recorded.
_TEXT_FIELDS = ("note_reference", "title", "reason", "detail", "attempt", "account", "folder", "session_id",
                "task_id", "reserved_at", "completed_at", "recorded_detail", "reclassified_from",
                "effect_kind", "note_title", "note_id", "effect_key")
#: The resolved note a mutation record names beside its request identity (provenance, never identity).
_TARGET_FIELDS = ("note_id", "title", "account", "folder")


def _field_problem(label: str, record: dict[str, Any]) -> str:
    """The first field of ``record`` no answer may be built from ("" when every field is usable)."""
    for name in _TEXT_FIELDS:
        if name in record and not isinstance(record[name], str):
            return f"{label} field {name} is not text"
    if "owner" in record and not isinstance(record["owner"], dict):
        return f"{label} field owner is not an object"
    if "target" in record and (not isinstance(record["target"], dict)
                               or any(not isinstance(record["target"].get(name), str) for name in _TARGET_FIELDS)):
        return f"{label} field target is not a resolved note's id, title, account and folder"
    for name in ("legacy", "_v5_index"):
        if name in record and not isinstance(record[name], bool):
            return f"{label} field {name} is not true or false"
    return ""


def _record_variant_problem(label: str, op_id: str, op: dict[str, Any]) -> str:
    """Why an operation is not the record variant it claims to be ("" when it is one).

    A CURRENT operation is one request's delivery: keyed ``op-`` + 40 hex and never flagged legacy. A LEGACY
    operation is a revision-4 content-only entry as ``_normalize_journal`` imports it: flagged, keyed for its
    content key and the state it was imported in (a refusal without proof that nothing was sent is read as
    ambiguous afterwards and records ``reclassified_from``), and carrying none of the request identity a
    reservation writes. Only a legacy record is never a replay target, so only it may be a confirmed receipt
    without a reference; the flag by itself proves nothing. A current record that carries its request identity
    (session and task) must derive its id from its content key; an older record without that identity is checked
    against the request when a replay selects it (``_selected_receipt_problem``).
    """
    legacy_id = _LEGACY_OPERATION_ID.match(op_id)
    if op.get("legacy") is True:
        if legacy_id is None:
            return f"{label} is flagged legacy but is not an imported content-only record"
        if legacy_id.group("content") != op["content_key"][:40]:
            return f"{label} is an imported legacy record whose id names other content"
        imported = legacy_id.group("state")
        reclassified = imported == "refused" and op["state"] == "ambiguous" and op.get("reclassified_from") == "refused"
        if op["state"] != imported and not reclassified:
            return f"{label} is an imported legacy record whose state is not the state it was imported in"
        for name in _REQUEST_IDENTITY_FIELDS:
            if name in op:
                return f"{label} is an imported legacy record carrying request identity ({name})"
        return ""
    if legacy_id is not None or not _CURRENT_OPERATION_ID.match(op_id):
        return f"{label} has an id that is neither a request operation nor an imported legacy record"
    if "session_id" in op and "task_id" in op and not _operation_id_derives(
        op_id, session_id=op["session_id"], task_id=op["task_id"], content_key=op["content_key"],
    ):
        return f"{label} records a request identity or content that does not derive its id"
    # A mutation recorded from correction 3 on names its resolved note (target) and that change's effect key beside the
    # request as asked; a record carrying one without the other, or for no known kind, is not that variant.
    if ("target" in op or "effect_key" in op) and not (
        "target" in op and _HEX64.match(str(op.get("effect_key") or "")) and op.get("effect_kind") in _MUTATION_KINDS
    ):
        return f"{label} records a resolved target without its effect key and kind"
    return ""


def _journal_problem(data: Any) -> str:
    """Why decoded bytes are not a trustworthy journal ("" when they are).

    Structure, then meaning: every field an answer is built from has its type, every operation is the
    record variant it claims to be, a confirmed receipt carries the note reference it would replay, and an
    in-flight reservation carries the attempt token that fences its outcome. An imported revision-4 receipt
    may lack a reference: it is never replayed, only consulted as an identical-content sibling.
    """
    if not isinstance(data, dict):
        return "the journal is not a JSON object"
    for key, value in data.items():
        if key in {"schema", "operations", "quarantined"}:
            continue
        if not _HEX64.match(str(key)) or not isinstance(value, dict) or str(value.get("status") or "") not in _V4_STATUS_TO_STATE:
            return f"unrecognized journal entry {str(key)[:24]!r}"
        problem = _field_problem(f"entry {str(key)[:12]!r}", value)
        if problem:
            return problem
    if "schema" in data:
        operations = data.get("operations")
        if not isinstance(operations, dict):
            return "operations is not an object"
        for op_id, op in operations.items():
            label = f"operation {str(op_id)[:24]!r}"
            if not isinstance(op, dict) or op.get("op_id") != op_id:
                return f"{label} is malformed"
            if str(op.get("state") or "") not in _DELIVERY_STATES or not _HEX64.match(str(op.get("content_key") or "")):
                return f"{label} has an invalid state or content key"
            problem = _field_problem(label, op) or _record_variant_problem(label, op_id, op)
            if problem:
                return problem
            if op["state"] == "confirmed" and not op.get("legacy") and not str(op.get("note_reference") or "").strip():
                return f"{label} is confirmed without the note_reference it would replay"
            if op["state"] == "dispatching" and not str(op.get("attempt") or "").strip():
                return f"{label} is in flight without the attempt token that fences its outcome"
        if not isinstance(data.get("quarantined", []), list):
            return "quarantined is not a list"
    return ""


def _content_keys_named_in(raw: bytes) -> list[str]:
    """Every content key damaged journal bytes name: revision-4 keys and operations' content keys when the
    bytes decode, else every 64-hex token in them. A superset only holds an older reader back."""
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return sorted({token.decode("ascii") for token in re.findall(rb"(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])", raw)})
    keys: set[str] = set()
    if isinstance(data, dict):
        keys.update(str(key) for key in data if _HEX64.match(str(key)))
        operations = data.get("operations")
        if isinstance(operations, dict):
            keys.update(str(op["content_key"]) for op in operations.values()
                        if isinstance(op, dict) and _HEX64.match(str(op.get("content_key") or "")))
    return sorted(keys)


def _rollback_marker_document(content_keys: list[str]) -> dict[str, Any]:
    """The journal left in place of a quarantined one: every named content unresolved to an older build.

    A revision-4 reader consults only the top-level index, so without this it would take the quarantined
    contents as never attempted and send them again. This build skips ``_v5_index`` entries, so the
    owner's resume step alone decides when native delivery continues, and its next save rebuilds the
    index from live operations.
    """
    document: dict[str, Any] = {"schema": _JOURNAL_SCHEMA, "operations": {}}
    for key in content_keys:
        document[key] = {"status": "outcome_unproven", "reason": "delivery_journal_quarantined", "_v5_index": True,
                         "detail": "this content's Apple Notes delivery record was damaged and preserved aside; it was not re-attempted"}
    return document


def _quarantine_journal(path: Path, raw: bytes, problem: str) -> _JournalUnavailableError:
    """Move untrustworthy journal bytes aside, byte-for-byte, and fail the request closed.

    Reading them as an empty journal would forget reservations and unresolved deliveries and
    let a duplicate through, so that is never done. While a quarantined journal exists, native
    delivery stays refused; the owner resumes it by checking Apple Notes and then removing the
    preserved file. Meanwhile every content the bytes name stays unresolved to an older build.
    """
    digest = hashlib.sha256(raw).hexdigest()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    target = path.with_name(f"{_QUARANTINE_PREFIX}{stamp}-{digest[:12]}.json")
    try:
        os.replace(path, target)
    except OSError as exc:
        return _JournalUnavailableError(
            "effect_journal_unwritable",
            f"the Apple Notes delivery journal is damaged ({problem}) and could not be moved aside ({type(exc).__name__}); nothing was sent to Notes.",
        )
    marker = ""
    named = _content_keys_named_in(raw)
    if named:
        try:
            _save_apple_note_effects(_rollback_marker_document(named))
        except Exception as exc:
            marker = f" The marker that keeps older builds from sending these notes again could not be written ({type(exc).__name__})."
    return _JournalUnavailableError(
        "effect_journal_quarantined",
        f"the Apple Notes delivery journal was damaged ({problem}). Its exact bytes were preserved at {target.name} "
        f"(sha256 {digest[:16]}); nothing was sent to Notes.{marker}",
    )


def _load_apple_note_effects() -> dict[str, Any]:
    """The delivery journal as a validated, normalized v2 document.

    Missing -> an empty journal. Unreadable bytes raise; empty, undecodable, structurally or
    semantically invalid bytes are quarantined and raise -- never read as an empty map. A journal from an
    unknown (newer) schema is refused and left untouched. Callers hold the journal lock.
    """
    path = _apple_note_effects_journal()
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return _empty_journal()
    except OSError as exc:
        raise _JournalUnavailableError(
            "effect_journal_unreadable",
            f"the Apple Notes delivery journal could not be read ({type(exc).__name__}); nothing was sent to Notes.",
        ) from exc
    if not raw.strip():
        raise _quarantine_journal(path, raw, "empty journal file")
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _quarantine_journal(path, raw, f"undecodable ({type(exc).__name__})") from exc
    if isinstance(data, dict) and "schema" in data and data.get("schema") != _JOURNAL_SCHEMA:
        raise _JournalUnavailableError(
            "effect_journal_version_unsupported",
            f"the Apple Notes delivery journal uses schema {str(data.get('schema'))[:60]!r}, which this build does not"
            " understand; it was left untouched and nothing was sent to Notes.",
        )
    problem = _journal_problem(data)
    if problem:
        raise _quarantine_journal(path, raw, problem)
    return _normalize_journal(data)


def _normalize_journal(data: dict[str, Any]) -> dict[str, Any]:
    """v2 operations, plus any entry an older build wrote imported as a legacy operation.

    Revision-4 keyed outcomes by content only and recorded no request identity, so a legacy
    entry can never be a replay target; it acts as an identical-content sibling instead:
    unresolved blocks, refused and confirmed do not.
    """
    journal = {"schema": _JOURNAL_SCHEMA, "operations": {}, "quarantined": list(data.get("quarantined") or [])}
    for op_id, op in dict(data.get("operations") or {}).items():
        journal["operations"][op_id] = dict(op)
    for key, entry in data.items():
        if not _HEX64.match(str(key)) or entry.get("_v5_index"):
            continue
        state = _V4_STATUS_TO_STATE[str(entry.get("status"))]
        legacy_id = f"legacy-{key[:40]}-{state}"
        journal["operations"].setdefault(legacy_id, {
            "op_id": legacy_id,
            "content_key": key,
            "state": state,
            "legacy": True,
            "title": str(entry.get("title") or ""),
            "note_reference": str(entry.get("note_reference") or ""),
            "reason": str(entry.get("reason") or ""),
            "detail": str(entry.get("detail") or ""),
        })
    for op in journal["operations"].values():
        if op.get("state") == "refused" and str(op.get("reason") or "") not in _KNOWN_UNSENT_REASONS:
            # Builds before revision 6 recorded every non-zero bridge exit and every bridge exception
            # as refused (`osascript_failed`), including failures after the Apple event may already
            # have created the note. A refusal now needs affirmative evidence that nothing reached
            # Notes, so such a record is unresolved: it blocks identical content instead of
            # authorizing another send. What the earlier build wrote is kept beside it.
            op["recorded_detail"] = str(op.get("detail") or "")
            op.update({
                "state": "ambiguous",
                "reclassified_from": "refused",
                "detail": "an earlier build recorded this delivery as refused without evidence that nothing reached Notes;"
                          " whether the note was created is not established.",
            })
    return journal


def _journal_document(journal: dict[str, Any]) -> dict[str, Any]:
    """The v2 document plus a revision-4-compatible index at top level.

    Rollback safety: revision-4 code reads ``effects[content_key]`` and replays ``executed``,
    refuses to re-invoke ``outcome_unproven`` and replays ``failed``. Every content key this
    build knows is published in exactly that vocabulary (unresolved wins, then confirmed, then
    refused), so an older build reading this journal never re-sends a confirmed or unresolved
    note. Entries carry ``_v5_index`` so this build never re-imports its own index.
    """
    document: dict[str, Any] = {"schema": _JOURNAL_SCHEMA, "operations": journal["operations"]}
    if journal.get("quarantined"):
        document["quarantined"] = journal["quarantined"]
    by_content: dict[str, list[dict[str, Any]]] = {}
    for op in journal["operations"].values():
        by_content.setdefault(str(op["content_key"]), []).append(op)
    for key, ops in sorted(by_content.items()):
        states = {str(op.get("state")) for op in ops}
        if states & {"dispatching", "ambiguous"}:
            entry: dict[str, Any] = {"status": "outcome_unproven", "reason": "delivery_unknown",
                                     "detail": "an Apple Notes delivery for this content is unresolved; it was not re-attempted"}
        elif "confirmed" in states:
            latest = max((op for op in ops if op.get("state") == "confirmed"), key=lambda op: str(op.get("completed_at") or ""))
            entry = {"status": "executed", "note_reference": str(latest.get("note_reference") or ""), "title": str(latest.get("title") or "")}
        else:
            latest = max((op for op in ops if op.get("state") == "refused"), key=lambda op: str(op.get("completed_at") or ""))
            entry = {"status": "failed", "reason": str(latest.get("reason") or "osascript_failed"), "detail": str(latest.get("detail") or "")}
        entry["_v5_index"] = True
        document[key] = entry
    return document


def _save_apple_note_effects(effects: dict[str, Any]) -> None:
    from core.execution.artifacts import atomic_write_text

    journal = _apple_note_effects_journal()
    atomic_write_text(journal, json.dumps(effects, sort_keys=True, indent=2))


def _delivery_state_of(outcome: dict[str, Any]) -> str:
    if outcome.get("ok") and str(outcome.get("note_reference") or "").strip():
        return "confirmed"
    if str(outcome.get("reason") or "") in _KNOWN_UNSENT_REASONS:
        return "refused"
    return "ambiguous"


_USER_STATE = {"confirmed": "confirmed", "refused": "refused", "ambiguous": "unresolved"}


def _mark_owner_lost(journal: dict[str, Any], op: dict[str, Any]) -> bool:
    """A reservation whose owner lock is free: the reserving worker is gone mid-dispatch."""
    op.update({
        "state": "ambiguous",
        "reason": "owner_lost_during_dispatch",
        "detail": "the worker that reserved this delivery stopped before recording its outcome; the note may or may not exist in Apple Notes.",
        "completed_at": _utc_stamp(),
    })
    try:
        _save_apple_note_effects(_journal_document(journal))
    except Exception:
        return False
    return True


#: The request fields a reservation records. A receipt selected for a request that carries one must carry that
#: request's value.
_RECORDED_REQUEST_FIELDS = ("session_id", "task_id", "title", "account", "folder", "effect_kind", "note_title")


def _selected_receipt_problem(prior: dict[str, Any], *, operation_id: str, request: dict[str, str]) -> str:
    """Why the operation recorded under this request's id cannot answer for this request ("" when it can).

    The id is derived from the request, but the record under it is only bytes. Before any answer is built from
    it, the record passes the same per-operation rules a load applies (so it is a whole current receipt), its
    content key derives this id under THIS request's identity, and every request field it records is this
    request's. Older records record fewer fields; an absent field is neither invented nor required.
    """
    label = f"operation {operation_id[:24]!r}"
    problem = _journal_problem({"schema": _JOURNAL_SCHEMA, "operations": {operation_id: prior}})
    if problem:
        return problem
    if not _operation_id_derives(operation_id, session_id=request["session_id"], task_id=request["task_id"], content_key=prior["content_key"]):
        return f"{label} records different content than this request"
    for name in _RECORDED_REQUEST_FIELDS:
        if name in prior and prior[name] != request.get(name):
            return f"{label} records a different {name} than this request"
    return ""


def _quarantine_loaded_journal(problem: str) -> _JournalUnavailableError:
    """Quarantine the journal this request loaded, when its replay selection finds it cannot be trusted.

    Callers still hold the journal lock taken before the load, so the bytes on disk are the bytes that were
    validated and nothing has been written since.
    """
    path = _apple_note_effects_journal()
    try:
        raw = path.read_bytes()
    except OSError as exc:
        return _JournalUnavailableError(
            "effect_journal_unreadable",
            f"the Apple Notes delivery journal could not be read again to preserve it ({type(exc).__name__}); nothing was sent to Notes.",
        )
    return _quarantine_journal(path, raw, problem)


def _answer_recorded_operation(journal: dict[str, Any], prior: dict[str, Any], base: dict[str, Any]) -> dict[str, Any]:
    """A replay of the same request: answer from the record; the bridge is never re-invoked."""
    state = str(prior.get("state") or "")
    if state == "confirmed":
        return {**base, "ok": True, "reason": "", "detail": "", "note_reference": str(prior.get("note_reference") or ""),
                "replayed": True, "delivery_state": "confirmed"}
    if state == "refused":
        return {**base, "ok": False, "reason": str(prior.get("reason") or "osascript_failed"), "detail": str(prior.get("detail") or ""),
                "replayed": True, "delivery_state": "refused"}
    recorded = True
    if state == "dispatching":
        probe = _owner_lock_for(str(prior["op_id"]))
        if not probe.try_acquire():
            return {**base, "ok": False, "reason": "delivery_in_progress", "delivery_state": "in_progress", "replayed": True,
                    "detail": "This same request is being delivered right now; it was not sent a second time."}
        try:
            recorded = _mark_owner_lost(journal, prior)
        finally:
            probe.release()
    answer = {**base, "ok": False, "reason": str(prior.get("reason") or "delivery_unknown"),
              "detail": (str(prior.get("detail") or "").strip() + " It was not attempted again.").strip(),
              "replayed": True, "delivery_state": "unresolved"}
    if not recorded:
        answer["transition_unrecorded"] = True
    return answer


def _identical_content_blocker(journal: dict[str, Any], effect_key: str, operation_id: str) -> dict[str, Any] | None:
    """A NEW request may not overlap an in-flight or unresolved delivery of the identical effect.

    A create's effect is its content and destination; a mutation's is the change to the note its resolution found
    (``_effect_key_of``), so requests that name that note differently still block one another.
    """
    for op in list(journal["operations"].values()):
        if _effect_key_of(op) != effect_key or op.get("op_id") == operation_id:
            continue
        state = str(op.get("state") or "")
        if state == "dispatching" and not op.get("legacy"):
            probe = _owner_lock_for(str(op["op_id"]))
            if not probe.try_acquire():
                return {"ok": False, "reason": "delivery_in_progress", "delivery_state": "in_progress",
                        "detail": "Another request is delivering a note with identical content to Apple Notes right now; this one was not sent."}
            try:
                _mark_owner_lost(journal, op)
            finally:
                probe.release()
            state = "ambiguous"
        if state in {"ambiguous", "dispatching"}:
            return {"ok": False, "reason": "earlier_attempt_unresolved", "delivery_state": "unresolved",
                    "detail": ("An earlier request for a note with identical content has an unresolved Apple Notes outcome"
                               f" ({op.get('reason') or 'delivery_unknown'!s}); sending it again could create a duplicate,"
                               " so this request was not sent.")}
    return None


def _record_delivery_outcome(operation_id: str, attempt: str, outcome: dict[str, Any]) -> bool:
    """Record the bridge's answer for THIS attempt only (fenced by the attempt token)."""
    lock = _journal_lock()
    if not lock.acquire_within(_JOURNAL_LOCK_WAIT_SECONDS):
        return False
    try:
        try:
            journal = _load_apple_note_effects()
        except _JournalUnavailableError:
            return False
        op = journal["operations"].get(operation_id)
        if op is None or op.get("attempt") != attempt or op.get("state") != "dispatching":
            return False
        state = _delivery_state_of(outcome)
        op.update({"state": state, "completed_at": _utc_stamp(), "reason": str(outcome.get("reason") or ""),
                   "detail": str(outcome.get("detail") or "")[:400]})
        if state == "confirmed":
            op["note_reference"] = str(outcome.get("note_reference") or "")
        try:
            _save_apple_note_effects(_journal_document(journal))
        except Exception:
            return False
        return True
    finally:
        lock.release()


_MUTATION_KINDS = frozenset({"append", "rename", "delete"})


def _mutation_request(*, kind: str, title: str, account: str, folder: str, session_id: str, task_id: str) -> dict[str, str]:
    """The request fields a mutation's reservation records and its replay compares: the request AS ASKED.

    ``account`` and ``folder`` are the scope the request named, empty when it named none. They are never the scope a
    resolution found, which changes as soon as the title is reused in another folder or account.
    """
    return {"session_id": str(session_id or ""), "task_id": str(task_id or ""), "effect_kind": str(kind),
            "note_title": str(title or ""), "account": str(account or ""), "folder": str(folder or "")}


def _mutation_request_key(*, kind: str, title: str, payload: str, account: str, folder: str) -> str:
    """The content key of ONE mutation request: its kind, the title and scope as asked, and its payload.

    It is computable before anything is looked up, so the replay lookup, the reservation, the invocation and the
    receipt carry one identity. Given a RESOLVED title and scope instead, it is the key builds before correction 3
    derived a mutation's operation from (see ``_resolved_scope_records``).
    """
    return _apple_note_effect_key(title=f"{kind}:{title}", body=str(payload or ""), account=str(account or ""), folder=str(folder or ""))


def _mutation_effect_key(*, kind: str, target: dict[str, str], payload: str) -> str:
    """The EFFECT of a mutation: its kind, the resolved note's own title and scope, and its payload.

    Two different requests that make the same change to the same note share it, so an unresolved one blocks the other.
    """
    return _mutation_request_key(kind=kind, title=target["title"], payload=payload, account=target["account"], folder=target["folder"])


def _effect_key_of(op: dict[str, Any]) -> str:
    """The effect an operation delivers: its recorded effect key, else its content key. A create's content is its
    effect, and a mutation recorded before correction 3 was keyed by its resolved scope, which is its effect."""
    return str(op.get("effect_key") or op.get("content_key") or "")


def _recorded_note_id(op: dict[str, Any]) -> str:
    """The note a recorded mutation acted on: its target provenance, else the id an older record carries."""
    target = op.get("target") if isinstance(op.get("target"), dict) else {}
    return str(target.get("note_id") or op.get("note_id") or "")


def _resolved_scope_records(journal: dict[str, Any], *, request: dict[str, str], payload: str) -> list[dict[str, Any]]:
    """This request's records written before correction 3, keyed by the scope its resolution FOUND.

    Such a record does not carry the scope the request named, so it is matched by what it does carry: the same
    session, task and effect kind; a recorded title and scope this request's own resolution could have produced (the
    title and every scope part the request named are equal regardless of case, as ``apple_notes.resolve_note``
    compares them); and a content key derived from that recorded title and scope with THIS request's payload.
    A record carrying ``target`` holds its own request identity and is never matched here.
    """
    kind = request["effect_kind"]
    found: list[dict[str, Any]] = []
    for op in journal["operations"].values():
        if op.get("legacy") or "target" in op or op.get("effect_kind") != kind:
            continue
        if op.get("session_id") != request["session_id"] or op.get("task_id") != request["task_id"]:
            continue
        recorded = {name: str(op.get(name) or "") for name in ("note_title", "account", "folder")}
        if recorded["note_title"].casefold() != request["note_title"].casefold():
            continue
        if any(request[name] and recorded[name].casefold() != request[name].casefold() for name in ("account", "folder")):
            continue
        if _mutation_request_key(kind=kind, title=recorded["note_title"], payload=payload,
                                 account=recorded["account"], folder=recorded["folder"]) != op.get("content_key"):
            continue
        found.append(op)
    return found


def _recorded_answer_for_request(journal: dict[str, Any], *, operation_id: str, request: dict[str, str], base: dict[str, Any],
                                 payload: str | None = None) -> dict[str, Any] | None:
    """This request's answer from the journal, or None when the journal holds no record of it.

    The operation the request's identity derives answers first, and only as a whole receipt of THIS request (otherwise
    the journal is quarantined). For a mutation (``payload`` given) a record an earlier build keyed by its resolved
    scope answers next. Exactly one such record is this request's. Several mean the note it meant cannot be
    determined: the request is refused, because sending it would be a guess that could retarget it.
    """
    prior = journal["operations"].get(operation_id)
    if prior is not None:
        problem = _selected_receipt_problem(prior, operation_id=operation_id, request=request)
        if problem:
            refusal = _quarantine_loaded_journal(problem)
            return {**base, "ok": False, "reason": refusal.reason, "detail": refusal.detail, "delivery_state": "not_dispatched"}
        answer = _answer_recorded_operation(journal, prior, base)
        if payload is not None:
            answer["note_id"] = _recorded_note_id(prior)
        return answer
    if payload is None:
        return None
    earlier = _resolved_scope_records(journal, request=request, payload=payload)
    if not earlier:
        return None
    if len(earlier) > 1:
        where = "; ".join(f"{op.get('folder') or '?'} ({op.get('account') or '?'}), {op.get('state')}" for op in earlier[:4])
        return {**base, "ok": False, "reason": "request_identity_undetermined", "delivery_state": "not_dispatched",
                "detail": (f"{len(earlier)} earlier delivery records of this same request name different notes ({where}); the build"
                           " that wrote them did not record the scope the request itself named, so the note it meant cannot be"
                           " determined. Nothing was sent to Notes. Check Notes, then ask again as a new request.")}
    op = earlier[0]
    answer = _answer_recorded_operation(journal, op, {**base, "operation_id": str(op["op_id"])})
    answer["note_id"] = _recorded_note_id(op)
    answer["identity_recovered_from"] = "resolved_scope_record"
    return answer


def _deliver_apple_note_effect(*, title: str, body: str, account: str, folder: str, task_id: str, session_id: str,
                               mutation: dict[str, Any] | None = None,
                               invoke: Any = None) -> dict[str, Any]:
    """Deliver ONE explicit request's native note effect: reserve durably, cross once, record.

    A create is the default. A MUTATION (append/rename/delete of one resolved note) passes ``mutation`` -- the
    request as asked (``kind``, ``title``, ``account``, ``folder``, ``payload``) plus the ``target`` its resolution
    found (note id, title, account, folder) -- and ``invoke`` (its bridge call). Its identity is the request as
    asked; the target is recorded beside it as provenance, and its effect key blocks an identical change. It gets
    the SAME custody: durable reservation, replay answered from the record without re-invoking the bridge,
    identical-effect in-flight/unresolved blocking, owner-lost ambiguity, and fail-closed reservation.

    * a replay of the same request answers from its record, only when that record is a whole receipt of
      THIS request (otherwise the journal is quarantined), and never re-invokes the bridge;
    * a reservation without a live owner is an interrupted dispatch -> ambiguous, not resent;
    * a new explicit request is refused only while an identical-effect sibling is in flight
      or unresolved -- after a definitive refusal it makes exactly one new attempt;
    * a reservation that cannot be persisted refuses before the bridge (fail closed);
    * an outcome that cannot be persisted leaves the reservation, so no replay re-sends it.
    """
    from core.operator.effect_lifecycle import owner_provenance

    payload: str | None = None
    provenance: dict[str, Any] = {}
    if mutation is None:
        content_key = _apple_note_effect_key(title=title, body=body, account=account, folder=folder)
        effect_key = content_key
        request = {"session_id": str(session_id or ""), "task_id": str(task_id or ""), "title": title, "account": account, "folder": folder}
    else:
        # ONE identity through lookup, reservation, invocation and receipt: the REQUEST as asked. The note its
        # resolution found is provenance recorded beside it, so a replay after the title is reused elsewhere reaches
        # this record, never a new operation for whichever note carries the title by then.
        payload = str(mutation.get("payload") or "")
        target = {name: str(mutation["target"].get(name) or "") for name in _TARGET_FIELDS}
        request = _mutation_request(kind=str(mutation["kind"]), title=str(mutation.get("title") or ""),
                                    account=str(mutation.get("account") or ""), folder=str(mutation.get("folder") or ""),
                                    session_id=session_id, task_id=task_id)
        content_key = _mutation_request_key(kind=request["effect_kind"], title=request["note_title"], payload=payload,
                                            account=request["account"], folder=request["folder"])
        effect_key = _mutation_effect_key(kind=request["effect_kind"], target=target, payload=payload)
        provenance = {"note_id": target["note_id"], "target": target, "effect_key": effect_key}
    operation_id = _apple_note_operation_id(session_id=session_id, task_id=task_id, content_key=content_key)
    base: dict[str, Any] = {"destination": "apple_notes", "operation_id": operation_id}
    journal_lock = _journal_lock()
    if not journal_lock.acquire_within(_JOURNAL_LOCK_WAIT_SECONDS):
        return {**base, "ok": False, "reason": "delivery_journal_busy", "delivery_state": "not_dispatched",
                "detail": "the Apple Notes delivery journal stayed locked by another request; nothing was sent to Notes."}
    owner = None
    try:
        try:
            quarantined = _quarantined_journals()
            if quarantined:
                raise _JournalUnavailableError(
                    "effect_journal_quarantined",
                    "a damaged Apple Notes delivery journal is preserved in the notes folder ("
                    + ", ".join(path.name for path in quarantined[:3])
                    + "); native delivery stays paused until you check Apple Notes and remove that preserved file. Nothing was sent to Notes.",
                )
            journal = _load_apple_note_effects()
        except _JournalUnavailableError as exc:
            return {**base, "ok": False, "reason": exc.reason, "detail": exc.detail, "delivery_state": "not_dispatched"}
        operations = journal["operations"]
        answered = _recorded_answer_for_request(journal, operation_id=operation_id, request=request, base=base, payload=payload)
        if answered is not None:
            return answered
        blocker = _identical_content_blocker(journal, effect_key, operation_id)
        if blocker is not None:
            return {**base, **blocker}
        owner = _owner_lock_for(operation_id)
        if not owner.try_acquire():
            owner = None
            return {**base, "ok": False, "reason": "delivery_in_progress", "delivery_state": "in_progress",
                    "detail": "This same request is being delivered right now; it was not sent a second time."}
        confirmed_earlier = sorted(
            (op for op in operations.values() if _effect_key_of(op) == effect_key and op.get("state") == "confirmed"),
            key=lambda op: str(op.get("completed_at") or ""),
        )
        attempt = secrets.token_hex(16)
        operations[operation_id] = {
            "op_id": operation_id, "content_key": content_key, "state": "dispatching", "attempt": attempt,
            **request, **provenance, "reserved_at": _utc_stamp(), "owner": owner_provenance(),
        }
        try:
            _save_apple_note_effects(_journal_document(journal))
        except Exception as exc:
            owner.release()
            owner = None
            return {**base, "ok": False, "reason": "effect_journal_unwritable", "delivery_state": "not_dispatched",
                    "detail": f"the delivery reservation could not be saved ({type(exc).__name__}); the note was NOT sent to Notes, so nothing can be duplicated."}
    finally:
        journal_lock.release()
    try:
        from core.operator import apple_notes

        try:
            if mutation is not None:
                outcome = dict(invoke())
            else:
                outcome = dict(apple_notes.create_apple_note(title=title, body=body, account=account, folder=folder))
        except Exception as exc:  # the bridge maps its own failures; an escape is not proof of absence
            outcome = {"ok": False, "reason": "delivery_unknown",
                       "detail": f"the Notes bridge raised {type(exc).__name__} after the delivery was reserved; the effect may or may not have completed."}
        recorded = _record_delivery_outcome(operation_id, attempt, outcome)
    finally:
        owner.release()
    result = {**base, **outcome, "delivery_state": _USER_STATE[_delivery_state_of(outcome)]}
    if mutation is not None:
        result["note_id"] = provenance["note_id"]  # the resolved target of THIS dispatch
    if confirmed_earlier and result.get("ok"):
        latest = confirmed_earlier[-1]
        result["identical_earlier_note"] = {"note_reference": str(latest.get("note_reference") or ""), "completed_at": str(latest.get("completed_at") or "")}
    if not recorded:
        result["receipt_unrecorded"] = True
    return result


def _mutation_replay_answer(*, kind: str, title: str, payload: str = "", new_title: str = "",
                            folder: str = "", account: str = "",
                            task_id: str, session_id: str) -> dict[str, Any] | None:
    """The record of THIS exact request, answered before anything is resolved.

    The identity is the request as asked -- the one its reservation records -- so a replay reaches its record however
    the title has been reused since; a record an earlier build keyed by its resolved scope is recovered or refused
    (``_recorded_answer_for_request``). Returns None when this request has no record: a new explicit request, whose
    target is resolved afresh.
    """
    effect_payload = str(payload or new_title or "")
    request = _mutation_request(kind=kind, title=title, account=account, folder=folder, session_id=session_id, task_id=task_id)
    content_key = _mutation_request_key(kind=kind, title=title, payload=effect_payload, account=account, folder=folder)
    operation_id = _apple_note_operation_id(session_id=session_id, task_id=task_id, content_key=content_key)
    base: dict[str, Any] = {"destination": "apple_notes", "operation_id": operation_id}
    journal_lock = _journal_lock()
    if not journal_lock.acquire_within(_JOURNAL_LOCK_WAIT_SECONDS):
        return None  # the reservation takes the lock again and selects the same records before anything is sent
    try:
        try:
            journal = _load_apple_note_effects()
        except _JournalUnavailableError as exc:
            return {**base, "ok": False, "reason": exc.reason, "detail": exc.detail, "delivery_state": "not_dispatched"}
        return _recorded_answer_for_request(journal, operation_id=operation_id, request=request, base=base, payload=effect_payload)
    finally:
        journal_lock.release()


def deliver_apple_note_mutation(*, kind: str, title: str, payload: str = "", new_title: str = "",
                                folder: str = "", account: str = "",
                                task_id: str = "", session_id: str = "",
                                runner: Any = None) -> dict[str, Any]:
    """Resolve ONE note by title, revalidate it, then mutate it under journal custody.

    The TARGET is the note's stable id resolved from the listing (a title alone is not unique) and is revalidated
    (same id, same name as reviewed) before anything is sent. The OPERATION is the request as asked -- kind, title,
    the account/folder it named (often none) and payload -- from the replay lookup through the receipt, with the
    resolved target recorded beside it. The mutation crosses the bridge through the SAME durable
    reservation/replay/unknown custody a create uses, so an unresolved append is never automatically re-attempted.
    """
    from core.operator import apple_notes

    if kind not in _MUTATION_KINDS:
        raise ValueError(f"unknown apple note mutation kind: {kind!r}")
    effect_payload = str(payload or new_title or "")
    # Replay identity comes BEFORE any title lookup: the same request answers from its own
    # record (its original resolved target), so a title later reused by a different note is
    # never retargeted, and an unresolved earlier attempt is never re-sent blindly.
    replay = _mutation_replay_answer(kind=kind, title=title, payload=effect_payload,
                                     folder=folder, account=account, task_id=task_id, session_id=session_id)
    if replay is not None:
        return replay
    resolved = apple_notes.resolve_note(title=title, folder=folder, account=account, runner=runner)
    if not resolved.get("ok"):
        return resolved
    note = resolved["note"]
    rechecked = apple_notes.revalidate_note(note_id=note["id"], expected_title=note["title"], runner=runner)
    if not rechecked.get("ok"):
        return rechecked
    def _with_reference(outcome: dict[str, Any]) -> dict[str, Any]:
        # The journal records a completed effect by its reference; a mutation's reference is
        # the stable id it acted on.
        outcome.setdefault("note_reference", note["id"])
        return outcome

    if kind == "append":
        def invoke() -> dict[str, Any]:
            return _with_reference(apple_notes.append_apple_note(text=payload, note_id=note["id"], runner=runner))
    elif kind == "rename":
        def invoke() -> dict[str, Any]:
            return _with_reference(apple_notes.rename_apple_note(new_title=new_title, note_id=note["id"], runner=runner))
    else:
        def invoke() -> dict[str, Any]:
            return _with_reference(apple_notes.delete_apple_note(note_id=note["id"], runner=runner))
    return _deliver_apple_note_effect(
        title=title, body="", account=account, folder=folder,
        task_id=task_id, session_id=session_id,
        mutation={"kind": kind, "title": title, "account": account, "folder": folder, "payload": effect_payload,
                  "target": {"note_id": note["id"], "title": note["title"], "account": note["account"], "folder": note["folder"]}},
        invoke=invoke,
    )


def _fallback_note_for_operation(*, title: str, body: str, session_id: str, operation_id: str) -> dict[str, Any]:
    """The workspace fallback artifact for ONE delivery operation.

    Named by the operation, so every replay of the same request reuses the same file instead of
    multiplying copies; a different request gets its own.
    """
    from core.execution.artifacts import atomic_write_text

    suffix = hashlib.sha256(operation_id.encode("utf-8")).hexdigest()[:8] if operation_id else uuid.uuid4().hex[:8]
    path = _confined(f"notes/{_slugify(title)}-{suffix}.md")
    if path.is_file():
        return {"note_path": str(path), "title": title, "session_id": session_id, "reused": True}
    atomic_write_text(path, f"{_front_matter(title, session_id=session_id)}\n{str(body).strip()}\n")
    return {"note_path": str(path), "title": title, "session_id": session_id}
