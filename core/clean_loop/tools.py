"""The clean loop's tool table: a short vocabulary over VOOL's real machine intents.

Every call crosses `core.authorized_tool_execution.execute_authorized_runtime_tool`, the one
authorized boundary (permission decision, cancellation, receipts). Paths are resolved here first and
must land inside the owner's home folder; a home that resolves to a filesystem root is refused
outright, so no tool in this loop can ever walk "/". The runtime's own handlers then apply their
narrower rules on top (today: reads in Desktop, Downloads, Documents and the media folders), and a refusal from them is passed
to the model as a plain "not available" line.

Nothing here writes, moves or deletes. `propose_change` records a pending proposal and returns
"waiting for your approval"; the change itself is left to VOOL's normal approval ceremony.
"""
from __future__ import annotations

import itertools
import logging
import os
import re
import stat
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any

from core.clean_loop.registry import Registry, ToolContext, ToolOutcome

_RESULT_CHARS = 3000
_FIND_WORDS = 3
_STOP = frozenset(
    {
        "the", "a", "an", "and", "or", "to", "of", "in", "on", "for", "is", "it", "my", "me", "i", "you",
        "your", "this", "that", "what", "where", "which", "how", "find", "file", "files", "folder",
        "folders", "doc", "docs", "document", "documents",
    }
)


_LOG = logging.getLogger(__name__)


class OutsideHome(ValueError):
    """A path the loop refuses before any tool runs."""


def home_dir() -> Path:
    """The owner's home, resolved the way the runtime's machine tools resolve it."""
    try:
        from core.runtime_execution_tools import _machine_home

        home = Path(_machine_home()).expanduser().resolve()
    except Exception:
        home = Path.home().resolve()
    if home == Path(home.anchor) or str(home) in {"", "/"}:
        raise OutsideHome("the home folder resolves to a filesystem root")
    return home


_NAME_ROOTS = ("Desktop", "Documents", "Downloads")
_NAME_DEPTH = 3
_NAME_ENTRIES = 5000
_NAME_TTL_S = 60.0
_NAME_CACHE: dict[str, tuple[float, frozenset[str]]] = {}


def _name_roots(home: Path) -> list[Path]:
    """Every folder the loop may read, in reverse order of priority (the walk takes the last first):
    the media folders, then the owner's other visible home folders ("Projects", "Taxes"), then Desktop,
    Documents and Downloads. The same roots the read tools use (R4-CL review 2026-10-31, R39: a
    "Projects/garage sale" folder never let "garage sale" in)."""
    named = [home / root for root in _NAME_ROOTS]
    try:
        from core.runtime_execution_tools import _READ_ONLY_MACHINE_DIRECTORY_NAMES, _other_home_folders

        media = [home / name for name in _READ_ONLY_MACHINE_DIRECTORY_NAMES]
        media = [root for root in media if not root.is_symlink()]
        others = list(_other_home_folders(home, tuple(named + media)))
    except Exception:
        return named
    return media + others + named


def file_words(home: Path) -> frozenset[str]:
    """Distinctive words from the names of the owner's files in every readable folder (Desktop,
    Documents, Downloads, the media folders and the other visible home folders; three levels, 5000
    entries, hidden entries and links skipped), kept for a minute. Names only:
    nothing is opened. The route uses it to tell "which flat had the balcony" (there is a "flat
    viewing notes.md") from a general question."""
    from core.clean_loop.route import ask_words

    key = str(home)
    cached = _NAME_CACHE.get(key)
    now = time.monotonic()
    if cached is not None and now - cached[0] < _NAME_TTL_S:
        return cached[1]
    words: set[str] = set()
    budget = _NAME_ENTRIES
    stack = [(root, 0) for root in _name_roots(home)]
    while stack and budget > 0:
        folder, depth = stack.pop()
        try:
            with os.scandir(folder) as entries:
                for entry in entries:
                    if budget <= 0:
                        break
                    if entry.name.startswith(".") or entry.is_symlink():
                        continue
                    budget -= 1
                    words |= ask_words(Path(entry.name).stem.replace("_", " ").replace("-", " "))
                    if depth + 1 < _NAME_DEPTH and entry.is_dir(follow_symlinks=False):
                        stack.append((Path(entry.path), depth + 1))
        except OSError:
            continue
    result = frozenset(words)
    _NAME_CACHE[key] = (now, result)
    return result


def scope_path(raw: Any, home: Path) -> Path:
    """`raw` (relative to home, `~/...`, or absolute) as a resolved path inside `home`, or OutsideHome."""
    if home == Path(home.anchor):
        raise OutsideHome("the home folder resolves to a filesystem root")
    text = str(raw or "").strip().strip("'\"")
    if text in {"", ".", "~", "~/"}:
        return home
    if text.startswith("~/") or text.startswith("~\\"):
        text = text[2:]
    candidate = Path(text)
    full = (candidate if candidate.is_absolute() else home / candidate).resolve()
    if full != home and home not in full.parents:
        raise OutsideHome("that path is outside your home folder")
    return full


def _plain(text: str, home: Path, *, limit: int = _RESULT_CHARS) -> str:
    """Tool prose for the model: home-relative paths, no internal intent names."""
    out = str(text or "").replace(str(home) + os.sep, "").replace(str(home), "~")
    out = re.sub(r"`[a-z_]+\.[a-z_.]+`", "that action", out)
    return out[:limit]


def _call(
    ctx: ToolContext, intent: str, arguments: dict[str, Any], *, record_receipt: bool = True
) -> ToolOutcome:
    execute = ctx.execute
    if execute is None:
        from core.authorized_tool_execution import execute_authorized_runtime_tool as execute
    try:
        result = execute(intent, arguments, task_id=ctx.task_id, source_context=ctx.source_context)
    except Exception as exc:  # a tool fault is evidence for the model, never a crash of the turn
        _LOG.exception("clean loop: %s failed", intent)
        result = None
        status, ok, text = "error", False, f"that did not work ({error_text(exc, ctx.home)})"
    else:
        if result is None:
            status, ok, text = "unsupported", False, "that is not available on this computer"
        else:
            status = str(getattr(result, "status", "") or "")
            ok = bool(getattr(result, "ok", False))
            text = str(getattr(result, "response_text", "") or "")
    details = dict(getattr(result, "details", {}) or {}) if result is not None else {}
    if status == "pending_approval" and ctx.withdraw_pending_approvals:
        token = str((details.get("approval_request") or {}).get("approval_id") or "")
        if token:
            try:
                from core.mode_permission_policy import resolve_approval

                resolve_approval(token, decision="deny")
            except Exception:
                _LOG.exception("clean loop: could not withdraw a parked approval")
    cut_notice = "\n[Cut; continue with read_file using start_line or from_page.]"
    was_cut = len(text) > _RESULT_CHARS
    if ok:
        plain = _plain(text, ctx.home, limit=_RESULT_CHARS - len(cut_notice) if was_cut else _RESULT_CHARS)
        if was_cut:
            plain = plain.rstrip() + cut_notice
    else:
        plain = "not available: " + (_plain(text, ctx.home) or status)
    if record_receipt:
        ctx.receipts.append({
            "tool_name": intent,
            "mode": "tool_executed" if ok else "tool_failed",
            "status": status or ("executed" if ok else "failed"),
            "execution": {"executed": ok, "ok": ok, "status": status},
            "observation": details.get("observation") or {},
            "permission": details.get("permission") or {},
            "lane": "clean_local_loop",
            # What the model was shown, in the field the tool-history writer uses: the hook
            # mints it as this turn's own observation so the grounding gate can match claims to it.
            "response_preview": plain[:1800],
        })
    return ToolOutcome(text=plain or "(empty)", ok=ok, status=status, intents=(intent,))


def error_text(exc: BaseException, home: Path) -> str:
    """The real reason, short and home-relative: "TypeError" alone gave the model nothing to fix (F25)."""
    detail = " ".join(str(exc).split())[:200]
    return _plain(f"{type(exc).__name__}: {detail}" if detail else type(exc).__name__, home)


def _human_size(size: int) -> str:
    value = float(size)
    for unit in ("bytes", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{int(value)} bytes" if unit == "bytes" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


def _plain_pdf_fallback(text: str) -> str:
    """Keep only obviously printable text from a legacy machine read of a PDF."""
    value = str(text or "")
    low = value.lower()
    if any(marker in low for marker in ("stream", "endobj", "/flatedecode", "/filter", "xref")):
        return ""
    if not value or sum(1 for ch in value if ord(ch) < 32 and ch not in "\n\r\t") / len(value) > 0.001:
        return ""
    clean = re.sub(r"^\s*%PDF-[^\r\n]*(?:\r?\n)?", "", value, count=1)
    return clean.strip()


def _age(mtime: float, now: float) -> str:
    days = int(max(0.0, now - mtime) // 86400)
    when = time.strftime("%Y-%m-%d", time.localtime(mtime))
    ago = "today" if days == 0 else ("yesterday" if days == 1 else f"{days} days ago")
    return f"modified {when}, {ago}"


def describe(path: Path, home: Path, *, now: float | None = None) -> str:
    """ "1.2 MB, modified 2026-10-20, 8 days ago" for a path inside home; "" when it cannot be seen.

    Size and age come from the entry itself (never through a link), and only for names a tool has
    already shown: the plain loop's listings carry both, and without them "the newest" and "the
    biggest" were guesses (Mac audit F18, F19, F49)."""
    try:
        full = Path(os.path.abspath(path))
        if full != home and home not in full.parents:
            return ""
        info = full.lstat()
    except (OSError, ValueError):
        return ""
    now = time.time() if now is None else now
    if stat.S_ISDIR(info.st_mode):
        return "folder, " + _age(info.st_mtime, now)
    if not stat.S_ISREG(info.st_mode):
        return ""
    return f"{_human_size(info.st_size)}, {_age(info.st_mtime, now)}"


def _annotate(text: str, resolve, home: Path) -> str:
    """Append size and age to each "- name" line whose entry `resolve` can place inside home."""
    out = []
    for line in str(text or "").splitlines():
        if line.startswith("- "):
            name = line[2:].strip()
            try:
                about = describe(resolve(name.rstrip("/")), home)
            except Exception:
                about = ""
            if about:
                line = f"{line} ({about})"
        out.append(line)
    return "\n".join(out)


def _home_path(label: str, home: Path) -> Path:
    label = label.strip()
    if label.startswith("~/"):
        return home / label[2:]
    candidate = Path(label)
    return candidate if candidate.is_absolute() else home / candidate


def _merge(outcomes: list[ToolOutcome], text: str, *, status: str | None = None) -> ToolOutcome:
    intents = tuple(i for o in outcomes for i in o.intents)
    ok = any(o.ok for o in outcomes)
    outcome_status = status or ("executed" if ok else (outcomes[-1].status if outcomes else "error"))
    return ToolOutcome(text=text[:_RESULT_CHARS] or "(empty)", ok=ok, status=outcome_status, intents=intents)


def _scoped(args: dict[str, Any], ctx: ToolContext, key: str = "path") -> Path | ToolOutcome:
    try:
        return scope_path(args.get(key), ctx.home)
    except OutsideHome as exc:
        return ToolOutcome(text=f"not available: {exc}", ok=False, status="outside_home")


def _safe_roots(ctx: ToolContext) -> list[Path]:
    try:
        from core.runtime_execution_tools import _readable_machine_roots

        roots = [Path(root) for root in _readable_machine_roots()]
    except Exception:
        roots = [ctx.home / name for name in ("Desktop", "Downloads", "Documents")]
    return [root for root in roots if root.is_dir() and (ctx.home in root.parents)]


_RESOLVE_DEPTH = 4
_RESOLVE_ENTRIES = 4000
#: The owner says "my videos"; a Mac keeps them in Movies (and Linux the other way round).
_FOLDER_SYNONYMS = {"videos": "Movies", "movies": "Videos", "photos": "Pictures"}


def _readable_dirs(ctx: ToolContext) -> list[Path]:
    """Every readable root that exists (Desktop, Downloads, Documents and the media folders)."""
    return [root for root in _safe_roots(ctx)]


def _find_folders(name: str, ctx: ToolContext) -> list[Path]:
    """Folders under the readable roots whose name contains `name` (case-insensitive), breadth first.

    Bounded (depth, entries), never follows a link and skips hidden folders: this only turns the
    owner's "the Lisbon folder" into a path, and the listing itself still crosses the boundary.
    """
    needle = str(name or "").strip().lower()
    if not needle:
        return []
    hits: list[Path] = []
    frontier, seen = _readable_dirs(ctx), 0
    for _depth in range(_RESOLVE_DEPTH):
        nxt: list[Path] = []
        for folder in frontier:
            try:
                entries = sorted(folder.iterdir(), key=lambda p: p.name.lower())
            except OSError:
                continue
            for entry in entries:
                seen += 1
                if seen > _RESOLVE_ENTRIES:
                    return hits
                try:
                    if entry.name.startswith(".") or entry.is_symlink() or not entry.is_dir():
                        continue
                except OSError:
                    continue
                if needle in entry.name.lower():
                    hits.append(entry)
                nxt.append(entry)
        frontier = nxt
    return hits


def _resolve_folder(target: Path, ctx: ToolContext) -> Path | None:
    """A folder the owner named that is not where they said: the synonym root, or a single folder by that name."""
    try:
        relative = target.relative_to(ctx.home)
    except ValueError:
        return None
    parts = relative.parts
    if not parts:
        return None
    synonym = _FOLDER_SYNONYMS.get(parts[0].lower())
    if synonym and (ctx.home / synonym).is_dir():
        candidate = (ctx.home / synonym).joinpath(*parts[1:])
        if candidate.is_dir():
            return candidate
    matches = _find_folders(parts[-1], ctx)
    return matches[0] if len(matches) == 1 else None


def list_dir(args: dict[str, Any], ctx: ToolContext) -> ToolOutcome:
    target = _scoped(args, ctx)
    if isinstance(target, ToolOutcome):
        return target
    if target != ctx.home and not target.is_dir():
        resolved = _resolve_folder(target, ctx)
        if resolved is not None:
            outcome = _listing(ctx, resolved)
            label = _plain(str(resolved), ctx.home)
            return ToolOutcome(text=f"(that folder is {label})\n{outcome.text}", ok=outcome.ok,
                               status=outcome.status, intents=outcome.intents)
    if target == ctx.home:
        parts, outcomes = [], []
        for root in _safe_roots(ctx):
            outcome = _listing(ctx, root)
            outcomes.append(outcome)
            parts.append(f"[{root.name}]\n{outcome.text[:1200]}")
        if not outcomes:
            return ToolOutcome(text="no readable folders in your home", ok=False, status="no_results")
        return _merge(outcomes, "\n\n".join(parts))
    return _listing(ctx, target)


def _listing(ctx: ToolContext, folder: Path) -> ToolOutcome:
    """One folder's listing with size and age on every line, the way the plain loop shows it."""
    return _annotated(ctx, "machine.list_directory", {"path": str(folder)}, lambda name: folder / name)


def _annotated(ctx: ToolContext, intent: str, arguments: dict[str, Any], resolve) -> ToolOutcome:
    """`_call`, with size and age added to the "- name" lines before the model (and the receipt) sees them."""
    before = len(ctx.receipts)
    outcome = _call(ctx, intent, arguments)
    if not outcome.ok:
        return outcome
    text = _annotate(outcome.text, resolve, ctx.home)
    for receipt in ctx.receipts[before:]:
        receipt["response_preview"] = text[:1800]
    return ToolOutcome(text=text, ok=outcome.ok, status=outcome.status, intents=outcome.intents)


def _query_words(query: str) -> list[str]:
    words = [w for w in re.findall(r"[\w\-]+", str(query or "").lower()) if len(w) > 1 and w not in _STOP]
    seen: list[str] = []
    for word in sorted(words, key=len, reverse=True):
        if word not in seen:
            seen.append(word)
    return seen[:_FIND_WORDS]


def _content_index_complete(ctx: ToolContext) -> bool:
    """Every document in the readable folders has been looked inside: read into the index, or tried
    and found unopenable (an unopenable file is reported, never waited on forever)."""
    state = _TEXT_SCANS.get(str(ctx.home.resolve()), {})
    return bool(state) and _state_complete(state)


def _state_complete(state: dict[str, Any]) -> bool:
    metadata = state.get("metadata", {})
    unreadable = state.get("unreadable", set())
    if state.get("left_out"):
        return False
    return int(state.get("cursor", 0)) >= len(state.get("candidates", ())) and all(
        str(path) in unreadable or (str(path), *metadata[str(path)]) in _TEXT_CACHE
        for path in state.get("candidates", ())
        if str(path) in metadata
    )


def _unopened_note(ctx: ToolContext) -> str:
    state = _TEXT_SCANS.get(str(ctx.home.resolve()), {})
    notes = []
    count = len(state.get("unreadable", ()))
    if count:
        notes.append(f"{count} document{'s' if count != 1 else ''} could not be opened on this computer")
    left = len(state.get("left_out", ()))
    if left:
        notes.append(f"{left} document{'s' if left != 1 else ''} did not fit in the search index")
    return f" ({'; '.join(notes)}, so their text was not searched.)" if notes else ""


def find(args: dict[str, Any], ctx: ToolContext) -> ToolOutcome:
    words = _query_words(args.get("query") or args.get("name") or "")
    if not words:
        return ToolOutcome(text="tell me a word from the file name to look for", ok=False, status="missing_argument")
    outcomes = [
        _annotated(ctx, "machine.find_file", {"name": word}, lambda label: _home_path(label, ctx.home))
        for word in words
    ]
    hits: list[str] = []
    for outcome in outcomes:
        for line in outcome.text.splitlines():
            if line.startswith("- ") and line not in hits:
                hits.append(line)
    inside = [line for line in _find_in_text(words, ctx) if line.split(" (", 1)[0] not in "\n".join(hits)]
    complete = _content_index_complete(ctx)
    status = "content_search_complete" if complete else "content_search_incomplete"
    if not complete and not inside:
        return _merge(
            outcomes,
            "I have not searched all document contents yet. Search again before saying it was not found.",
            status=status,
        )
    if not complete and (hits or inside):
        status = "content_search_partial_match"
    if hits and inside:
        lines = [*hits, "these files also mention it inside:", *inside]
        return _merge([*outcomes, ToolOutcome(text="", ok=True)], "\n".join(lines), status=status)
    if hits:
        return _merge(outcomes, "\n".join(hits[:20]), status=status)
    if inside:
        lines = [f"no file name has {', '.join(words)}; these files mention it inside:", *inside]
        return _merge([*outcomes, ToolOutcome(text="", ok=True)], "\n".join(lines), status=status)
    return _merge(
        outcomes,
        f"I didn't find it in your files. Search result: nothing found for: {', '.join(words)} in file "
        f"names or inside documents.{_unopened_note(ctx)} Try another word you used or a folder name.",
        status=status,
    )


_TEXT_SUFFIXES = frozenset({".txt", ".md", ".csv", ".pdf", ".json", ".eml", ".ics", ".html", ".rtf", ".log"})
_TEXT_MAX_BYTES = 2 * 1024 * 1024
# The index is process memory only. Metadata in the key invalidates changed files; the byte budget
# bounds both source text and its searchable words. Route work is sliced so a cold turn stays short.
_TEXT_SCAN_S = 1.7
_TEXT_FIND_SCAN_S = 4.0
_TEXT_INDEX_BYTES = 48 * 1024 * 1024
_TEXT_MAX_ENTRIES = 20_000


class _IndexedDocument:
    """One document's searchable words, held in process memory only. The text itself is not kept:
    a match is re-read when it is shown.

    Each field is one space-separated string (" word word "), compact and fast to test with `in`.
    `words`: every word (and its plural-less form). `caps`: words the document writes capitalised
    away from the start of a sentence or line, or as an owner ("Ilse", "Tidewell", "Marisol's").
    `lows`: words it writes in lower case there ("rice", "key"). `pairs`: neighbouring distinctive
    words ("spare_key", "road_test"). Admission uses these to tell the owner's own people, places
    and things apart from everyday vocabulary every household has."""

    __slots__ = ("caps", "cost", "lows", "pairs", "words")

    def __init__(self, text: str) -> None:
        from core.clean_loop.route import _WORD_RE, _stem

        raw = set(_WORD_RE.findall(text.lower()))
        words = raw | {_stem(word) for word in raw}
        caps: set[str] = set()
        lows: set[str] = set()
        pairs: set[str] = set()
        for line in text.splitlines():
            tokens = _LINE_WORD_RE.findall(line)
            alpha = [t for t in tokens if t[0].isalpha()]
            title_line = len(alpha) >= 3 and sum(t[0].isupper() for t in alpha) * 10 >= len(alpha) * 6
            previous = ""
            sentence_start = True
            for match in _LINE_WORD_RE.finditer(line):
                token = match.group(0)
                word = _stem(token.lower())
                # A capitalised word that owns something ("Marisol's", "Ilse's") is a name even
                # where it opens a sentence.
                owner_form = token[0].isupper() and line[match.end(): match.end() + 2] in ("'s", "\u2019s")
                if (owner_form or not sentence_start) and not title_line and token[0].isalpha():
                    (caps if token[0].isupper() else lows).add(word)
                tail = line[match.end(): match.end() + 2]
                sentence_start = bool(re.match(r"[.!?](?:\s|$)", tail))
                if _distinctive(token.lower()):
                    if previous:
                        pairs.add(f"{min(previous, word)}_{max(previous, word)}")
                    previous = word
        self.words = " " + " ".join(sorted(words)) + " "
        self.caps = " " + " ".join(sorted(caps)) + " "
        self.lows = " " + " ".join(sorted(lows)) + " "
        self.pairs = " " + " ".join(sorted(pairs)) + " "
        self.cost = len(self.words) + len(self.caps) + len(self.lows) + len(self.pairs) + 200

    def has(self, word: str) -> bool:
        return f" {word} " in self.words


_LINE_WORD_RE = re.compile(r"[A-Za-z0-9]+")


def _distinctive(word: str) -> bool:
    from core.clean_loop.route import ask_words

    return bool(ask_words(word))
_TEXT_CACHE: OrderedDict[tuple[str, int, int], _IndexedDocument] = OrderedDict()
_TEXT_CACHE_BYTES = 0
_TEXT_SCANS: dict[str, dict[str, Any]] = {}
# One index advance or match at a time: the route and `find` can run in two sessions at once.
_INDEX_LOCK = threading.RLock()


def _inside_vool_data(path: Path) -> bool:
    try:
        from core.runtime_paths import active_vool_home

        data = active_vool_home().resolve()
    except Exception:
        return False
    return path == data or data in path.parents


def _text_candidates(ctx: ToolContext, metadata: dict[str, tuple[int, int]] | None = None) -> list[Path]:
    """All bounded document candidates in the readable roots, without following links. With
    `metadata`, each one's (size, mtime_ns) from the same walk is filled in."""
    found: list[Path] = []
    try:
        from core.runtime_paths import active_vool_home

        vool_prefix = str(active_vool_home().resolve())
    except Exception:
        vool_prefix = ""
    frontier = _readable_dirs(ctx)
    seen = 0
    for _depth in range(_RESOLVE_DEPTH):
        nxt: list[Path] = []
        for folder in frontier:
            try:
                entries = os.scandir(folder)
            except OSError:
                continue
            with entries:
                for entry in entries:
                    seen += 1
                    if seen > _TEXT_MAX_ENTRIES:
                        return found
                    if entry.name.startswith(".") or entry.is_symlink():
                        continue
                    if entry.name.casefold() in {"library", "appdata"} or (
                        vool_prefix and (entry.path == vool_prefix or entry.path.startswith(vool_prefix + os.sep))
                    ):
                        continue
                    path = Path(entry.path)
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            nxt.append(path)
                        elif entry.is_file(follow_symlinks=False) and path.suffix.lower() in _TEXT_SUFFIXES:
                            info = entry.stat(follow_symlinks=False)
                            if info.st_size <= _TEXT_MAX_BYTES:
                                found.append(path)
                                if metadata is not None:
                                    metadata[entry.path] = (int(info.st_size), int(info.st_mtime_ns))
                    except OSError:
                        continue
        frontier = nxt
    return found


def _drop_text_key(key: tuple[str, int, int]) -> None:
    global _TEXT_CACHE_BYTES
    old = _TEXT_CACHE.pop(key, None)
    if old is not None:
        _TEXT_CACHE_BYTES = max(0, _TEXT_CACHE_BYTES - old.cost)


def _cache_document(path: Path, key: tuple[str, int, int], text: str) -> bool:
    """Index one document's words. False when the byte budget is full: the index then stops
    growing (it never evicts and re-reads in a loop) and searches report the documents left out."""
    global _TEXT_CACHE_BYTES
    document = _IndexedDocument(text)
    for old_key in [item for item in _TEXT_CACHE if item[0] == key[0] and item != key]:
        _drop_text_key(old_key)
    _drop_text_key(key)
    if _TEXT_CACHE_BYTES + document.cost > _TEXT_INDEX_BYTES:
        return False
    _TEXT_CACHE[key] = document
    _TEXT_CACHE_BYTES += document.cost
    return True


#: Pages of a PDF the index reads (the PDF reader caps its own text length too).
_INDEX_PDF_PAGES = 20


class _IndexPermit:
    """The owner's permission for the index's reads, taken from VOOL's one authority
    (`authorize_runtime_tool`, the decision every boundary read takes) once per intent per index
    slice. Read permission is decided by the mode and the action class, not the path; a decision that
    is not a plain allow (a deny, an approval request, no decision) stops the index reading at all,
    and a parked approval request is withdrawn so the owner never sees a card for it."""

    def __init__(self, ctx: ToolContext) -> None:
        self.ctx = ctx
        self.allowed: dict[str, bool] = {}

    def allows(self, intent: str, path: Path) -> bool:
        if intent not in self.allowed:
            self.allowed[intent] = self._decide(intent, path)
        return self.allowed[intent]

    def _decide(self, intent: str, path: Path) -> bool:
        try:
            from core.authorized_tool_execution import authorize_runtime_tool
            from core.mode_permission_policy import PermissionEffect

            decision = authorize_runtime_tool(
                intent, {"path": str(path)}, task_id=self.ctx.task_id, source_context=self.ctx.source_context
            )
        except Exception:
            _LOG.exception("clean loop: no permission decision for the document index")
            return False
        if decision is None:
            return False
        if decision.effect is PermissionEffect.ALLOW:
            return True
        token = str((dict(getattr(decision, "approval_request", None) or {})).get("approval_id") or "")
        if token:
            try:
                from core.mode_permission_policy import resolve_approval

                resolve_approval(token, decision="deny")
            except Exception:
                _LOG.exception("clean loop: could not withdraw an index approval request")
        return False


def _index_read(ctx: ToolContext, path: Path, permit: _IndexPermit) -> str | None:
    """A document's whole text for the in-memory index, by the same readers the model's reads use:
    the machine reader's own confinement (readable roots, never VOOL's own state, no binary) for
    text, and `pdf_tools.extract_text` for a PDF. It is not cut to the model's 3000-character view,
    so a fact deep in a long file is still found. No receipt and nothing shown: the owner's text
    reaches the model only when `find` or `read_file` reads it through the boundary."""
    intent = "pdf.extract_text" if path.suffix.lower() == ".pdf" else "machine.read_file"
    if not permit.allows(intent, path):
        return None
    try:
        from core.runtime_execution_tools import _is_probably_text, _resolve_machine_directory

        target = _resolve_machine_directory(str(path))
        if path.is_symlink() or target != path.resolve() or not target.is_file():
            return None
        info = target.stat(follow_symlinks=False)
        if not stat.S_ISREG(info.st_mode) or info.st_size > _TEXT_MAX_BYTES:
            return None
        if intent == "pdf.extract_text":
            from core.pdf_tools import extract_text

            data = extract_text(str(target), max_pages=_INDEX_PDF_PAGES)
            return str(data.get("text") or "") if data.get("status") == "ok" else None
        if not _is_probably_text(target):
            return None
        return target.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None


def _scan_document_index(ctx: ToolContext, *, budget_s: float) -> tuple[list[Path], bool]:
    """Advance this home's in-memory document index, stopping within a per-turn work slice."""
    with _INDEX_LOCK:
        return _scan_document_index_locked(ctx, budget_s=budget_s)


def _scan_document_index_locked(ctx: ToolContext, *, budget_s: float) -> tuple[list[Path], bool]:
    global _TEXT_CACHE_BYTES
    deadline = time.monotonic() + max(0.0, budget_s)
    _TEXT_CACHE_BYTES = sum(item.cost for item in _TEXT_CACHE.values())
    home_key = str(ctx.home.resolve())
    metadata: dict[str, tuple[int, int]] = {}
    candidates = _text_candidates(ctx, metadata)
    current = set(metadata)
    state = _TEXT_SCANS.get(home_key)
    if state is None or state.get("metadata") != metadata:
        home = ctx.home.resolve()
        for key in [
            key for key in _TEXT_CACHE
            if (Path(key[0]) == home or home in Path(key[0]).parents)
            and (key[0] not in current or (key[1], key[2]) != metadata.get(key[0]))
        ]:
            _drop_text_key(key)
        candidates = [path for path in candidates if str(path) in metadata]
        old_meta = (state or {}).get("metadata", {})

        def unchanged(previous: set[str]) -> set[str]:
            # A file that did not change keeps its status: one changing log file never makes the
            # index retry every unopenable or left-out document on every turn.
            return {item for item in previous if item in metadata and old_meta.get(item) == metadata[item]}

        state = {
            "candidates": candidates, "metadata": metadata, "cursor": 0,
            "unreadable": unchanged(set((state or {}).get("unreadable", ()))),
            "left_out": unchanged(set((state or {}).get("left_out", ()))),
        }
        _TEXT_SCANS[home_key] = state
    else:
        candidates = state["candidates"]
    candidates = state["candidates"]
    if state["cursor"] >= len(candidates):
        for index, path in enumerate(candidates):
            size, mtime_ns = state["metadata"][str(path)]
            if (str(path), size, mtime_ns) not in _TEXT_CACHE and str(path) not in state["unreadable"] | state["left_out"]:
                state["cursor"] = index
                break
    permit = _IndexPermit(ctx)
    while state["cursor"] < len(candidates):
        path = candidates[state["cursor"]]
        state["cursor"] += 1
        try:
            if path.is_symlink():
                continue
            info = path.stat(follow_symlinks=False)
            if not stat.S_ISREG(info.st_mode) or info.st_size > _TEXT_MAX_BYTES:
                continue
        except OSError:
            continue
        key = (str(path), int(info.st_size), int(info.st_mtime_ns))
        if key in _TEXT_CACHE:
            _TEXT_CACHE.move_to_end(key)
            continue
        if str(path) in state["unreadable"] or str(path) in state["left_out"]:
            continue
        if time.monotonic() >= deadline:
            state["cursor"] -= 1
            break
        if not permit.allows("pdf.extract_text" if path.suffix.lower() == ".pdf" else "machine.read_file", path):
            # Not permitted now (the mode may change): leave it unread, not "unopenable".
            state["cursor"] -= 1
            break
        text = _index_read(ctx, path, permit)
        if text is None:
            state["unreadable"].add(str(path))
        elif not _cache_document(path, key, text):
            state["left_out"].add(str(path))
        if time.monotonic() >= deadline:
            break
    return candidates, _state_complete(state)


# A question that asks for one specific fact: when, where, who, which, how much/many/often/long, or
# "what <thing>" ("what time", "what size"). "what is ...", "how do i ...", "explain ..." are not.
_FACT_QUESTION_RE = re.compile(
    r"^\s*(?:when|where|who|whose|which|how\s+(?:much|many|often|long|old|far|big)|"
    r"what(?!'s)\s+(?!(?:is|are|was|were|do|does|did|can|could|should|would|will|if|about|a|an|the|kind)\b)[a-z]+)\b",
    re.I,
)
#: A name in more than this share of the owner's documents (from 10 documents up) is too common there
#: to say a question is about the owner's own things.
_NAME_SHARE_MAX = 0.3


def _ask_pairs(ask: str) -> set[tuple[str, str]]:
    from core.clean_loop.route import _stem

    words = [_stem(t.lower()) for t in _LINE_WORD_RE.findall(str(ask or "")) if _distinctive(t.lower())]
    return {f"{min(a, b)}_{max(a, b)}" for a, b in itertools.pairwise(words)}


def document_matches(ask: str, ctx: ToolContext) -> bool:
    """Whether a question is about something in the owner's own documents, from the bounded,
    in-memory document index (no model call, nothing written to disk).

    Two kinds of evidence count. A question that names something the owner's documents write as a
    name (capitalised at least as often as not, away from sentence starts) ("when is ilse's eye check", "when does the tidewell membership renew"), unless
    that name is in a large share of the documents. Or a question for one specific fact that shares
    two neighbouring words with a document ("where is the spare key", "who fitted the solar panels").
    A general question that merely shares an everyday word with some document ("how do i cook fluffy
    rice" in a home with a recipe file) is not admitted by this."""
    from core.clean_loop.route import ask_words, is_question

    words = ask_words(ask)
    if not words or not is_question(ask):
        return False
    with _INDEX_LOCK:
        candidates, _complete = _scan_document_index(ctx, budget_s=_TEXT_SCAN_S)
        paths = {str(path) for path in candidates}
        documents = [item for key, item in _TEXT_CACHE.items() if key[0] in paths]
    if not documents:
        return False
    # Counted in documents: a word some documents write as a name and at least as many never
    # write in lower case is a name ("Mosslight" in one note and "mosslight.com" in the same one
    # still counts; "rice" written in lower case in a recipe does not).
    limit = len(documents) if len(documents) < 10 else _NAME_SHARE_MAX * len(documents)
    for word in words:
        needle = f" {word} "
        caps = sum(1 for doc in documents if needle in doc.caps)
        if not caps:
            continue
        lows = sum(1 for doc in documents if needle in doc.lows)
        holders = sum(1 for doc in documents if needle in doc.words)
        if caps >= lows and holders <= limit:
            return True
    if _FACT_QUESTION_RE.search(ask):
        needles = [f" {pair} " for pair in _ask_pairs(ask)]
        if needles and any(needle in doc.pairs for doc in documents for needle in needles):
            return True
    return False


def _find_in_text(words: list[str], ctx: ToolContext) -> list[str]:
    """Look inside the owner's documents (the word the owner uses is often inside a file, never in
    its name: Mac audit F08), over the whole in-memory index rather than the newest few files. Best match first,
    with the line it is on. The five best are read again through the read tools at the matching
    line or page, so what the model is shown is this turn's own read evidence."""
    with _INDEX_LOCK:
        candidates, _complete = _scan_document_index(ctx, budget_s=_TEXT_FIND_SCAN_S)
        cache = dict(_TEXT_CACHE)
    scored: list[tuple[int, Path, _IndexedDocument, str]] = []
    for path in candidates:
        try:
            info = path.stat(follow_symlinks=False)
        except OSError:
            continue
        cached = cache.get((str(path), int(info.st_size), int(info.st_mtime_ns)))
        if cached is None:
            continue
        matched = [word for word in words if cached.has(word)]
        if matched:
            scored.append((len(matched), path, cached, matched[0]))
    scored.sort(key=lambda item: -item[0])
    lines: list[str] = []
    permit = _IndexPermit(ctx)
    for _score, path, _cached, word in scored[:5]:
        body = _index_read(ctx, path, permit) or ""
        at = body.lower().find(word)
        if at < 0:
            continue
        start = body.rfind("\n", 0, at) + 1
        end = body.find("\n", at)
        snippet = " ".join(body[start: end if end != -1 else at + 160].split())[:160]
        if path.suffix.lower() == ".pdf":
            page = re.findall(r"\[page (\d+)\]", body[:at])
            arguments = {"path": str(path), "start_page": int(page[-1]) if page else 1, "max_pages": 1}
            shown = _call(ctx, "pdf.extract_text", arguments)
        else:
            line_no = body.count("\n", 0, at) + 1
            arguments = {"path": str(path), "start_line": max(1, line_no - 3), "max_lines": 20}
            shown = _call(ctx, "machine.read_file", arguments)
        if not shown.ok:
            continue
        label = _plain(str(path), ctx.home)
        lines.append(f"- {label} ({describe(path, ctx.home)}): \"{snippet}\"")
    return lines


def read_file(args: dict[str, Any], ctx: ToolContext) -> ToolOutcome:
    target = _scoped(args, ctx)
    if isinstance(target, ToolOutcome):
        return target
    if not target.exists():
        # A guessed name ("Desktop/flat_viewing_notes.txt") is the small model's most common miss and it
        # rarely searches afterwards on its own (Mac audit F16, F39): search the name's words for it.
        words = " ".join(re.split(r"[_\-.\s]+", target.stem))
        hits = find({"query": words}, ctx)
        if hits.ok and hits.text.startswith("- "):
            return ToolOutcome(
                text=f"{_plain(str(target), ctx.home)} does not exist. Files with a similar name:\n{hits.text}",
                ok=False, status="not_found_with_suggestions", intents=hits.intents,
            )
    if target.suffix.lower() == ".pdf":
        extracted = _call(ctx, "pdf.extract_text", {
            "path": str(target),
            "start_page": max(1, int(args.get("from_page") or args.get("start_page") or 1)),
            "max_pages": max(1, min(int(args.get("max_pages") or 5), 200)),
        })
        if extracted.ok:
            return extracted
        plain = _call(ctx, "machine.read_file", {"path": str(target)})
        recovered = _plain_pdf_fallback(plain.text) if plain.ok else ""
        if recovered:
            return ToolOutcome(
                text=recovered[:_RESULT_CHARS], ok=True, status="text_recovered",
                intents=(*extracted.intents, *plain.intents),
            )
        label = _plain(str(target), ctx.home)
        if extracted.status == "no_text_layer":
            message = f"I found {label}, but it has no text layer and appears scanned. Keep looking in other files; it is not empty."
        else:
            reason = extracted.text.removeprefix("not available:").strip() or extracted.status
            message = f"I found {label} but could not open its text on this computer ({reason}). Keep looking in other files."
        return ToolOutcome(
            text=message, ok=False, status=extracted.status,
            intents=(*extracted.intents, *plain.intents),
        )
    arguments = {"path": str(target)}
    if args.get("start_line") or args.get("from_line"):
        arguments["start_line"] = max(1, int(args.get("start_line") or args.get("from_line") or 1))
    if args.get("max_lines"):
        arguments["max_lines"] = max(1, min(int(args["max_lines"]), 400))
    return _call(ctx, "machine.read_file", arguments)


def largest(args: dict[str, Any], ctx: ToolContext) -> ToolOutcome:
    target = _scoped(args, ctx)
    if isinstance(target, ToolOutcome):
        return target
    if target != ctx.home and not target.is_dir():
        target = _resolve_folder(target, ctx) or target
    # The contract's argument is `drive` (a `path` key is refused as invalid_arguments).
    before = len(ctx.receipts)
    outcome = _call(ctx, "machine.find_largest", {"drive": str(target), "kind": "files"})
    if not outcome.ok:
        return outcome
    text = _largest_lines(outcome.text, target, ctx.home)
    if target != ctx.home and target.is_dir():
        text = text + "\n" + _folder_total(target, ctx.home)
    for receipt in ctx.receipts[before:]:
        receipt["response_preview"] = text[:1800]
    return ToolOutcome(text=text, ok=outcome.ok, status=outcome.status, intents=outcome.intents)


_TOTAL_ENTRIES = 50000
_TOTAL_S = 3.0


def _folder_total(folder: Path, home: Path) -> str:
    """"How big is my Downloads folder" needs the folder's total, not its top files (R4-CL review
    2026-10-31, R29). Sizes only: nothing is opened; links are not followed; bounded, and says so."""
    total, files, seen = 0, 0, 0
    deadline = time.monotonic() + _TOTAL_S
    stack, partial = [folder], False
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    seen += 1
                    if seen > _TOTAL_ENTRIES or time.monotonic() > deadline:
                        partial = True
                        break
                    try:
                        if entry.is_symlink():
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(Path(entry.path))
                        elif entry.is_file(follow_symlinks=False):
                            total += entry.stat(follow_symlinks=False).st_size
                            files += 1
                    except OSError:
                        continue
        except OSError:
            continue
        if partial:
            break
    label = _plain(str(folder), home)
    size = _human_size(total)
    if partial:
        return f"Total of {label}: at least {size} in {files} files (stopped counting; the folder is very large)"
    return f"Total of {label}: {size} in {files} files (measured, all subfolders)"


_LARGEST_LINE = re.compile(r"^- (?P<name>.+?) — (?P<gb>[\d.]+) GB \[(?P<kind>[^\]]*)\](?P<partial>[^·]*)(?: · .*)?$")


def _largest_lines(text: str, root: Path, home: Path) -> str:
    """Real sizes ("0.0 GB" told the model nothing about a 2.5 MB video, F49) and no delete advice on a
    read-only question (the model offered to delete a file, F27)."""
    out = []
    for line in str(text or "").splitlines():
        if line.startswith("I won't delete anything"):
            continue
        match = _LARGEST_LINE.match(line)
        if match:
            name = match.group("name").strip()
            path = _home_path(name, home)
            if not path.exists():
                path = root / name
            about = describe(path, home)
            partial = match.group("partial").rstrip()
            line = f"- {name} ({about}){partial}" if about else f"- {name} — {match.group('gb')} GB{partial}"
        out.append(line)
    return "\n".join(out)


def disk_usage(args: dict[str, Any], ctx: ToolContext) -> ToolOutcome:
    return _call(ctx, "machine.disk_usage", {})


def system_info(args: dict[str, Any], ctx: ToolContext) -> ToolOutcome:
    return _call(ctx, "machine.inspect_specs", {})


def processes(args: dict[str, Any], ctx: ToolContext) -> ToolOutcome:
    """Its own tool: merged under the specs, the process list was cut out of what the model saw (F30, F31)."""
    sort = "cpu" if str(args.get("sort") or "").lower() == "cpu" else "memory"
    return _call(ctx, "machine.list_processes", {"sort": sort, "limit": 15})


def privacy(args: dict[str, Any], ctx: ToolContext) -> ToolOutcome:
    """What left this computer for online AI models today. Read locally; nothing is sent to find out."""
    from core import privacy_meter

    state = privacy_meter.today()
    ctx.receipts.append(
        {
            "tool_name": "privacy_meter.today",
            "mode": "tool_executed",
            "status": "executed",
            "execution": {"executed": True, "ok": True, "status": "executed"},
            "lane": "clean_local_loop",
            "response_preview": privacy_meter.plain_line(state),
        }
    )
    return ToolOutcome(text=privacy_meter.plain_line(state), ok=True, intents=("privacy_meter.today",))


def propose_change(args: dict[str, Any], ctx: ToolContext) -> ToolOutcome:
    """Never executes. With `ops` the change is rehearsed on a copy and the owner sees exactly what
    will happen; it waits for their yes either way, and the loop's answer says so."""
    what = " ".join(str(args.get("what") or args.get("change") or "").split())[:400]
    ops = args.get("ops")
    from core.clean_loop import route

    if ops is not None and route.changes_enabled():
        from core.clean_loop import changes

        try:
            plan = changes.preview(ctx.home, ops if isinstance(ops, list) else [ops])
        except changes.ChangeRefused as exc:
            return ToolOutcome(text=f"not possible: {exc}", ok=False, status="change_refused")
        except Exception as exc:
            _LOG.exception("clean loop: preview failed")
            return ToolOutcome(text=f"not possible: {error_text(exc, ctx.home)}", ok=False, status="error")
        ctx.plans.append(plan)
        proposal = {"what": what or "; ".join(plan.lines)[:400], "status": "pending_approval", "executed": False,
                    "plan_id": plan.plan_id, "card": list(plan.lines)}
        ctx.proposals.append(proposal)
        ctx.receipts.append(
            {
                "tool_name": "propose_change",
                "mode": "pending_approval",
                "status": "pending_approval",
                "execution": {"executed": False, "ok": True, "status": "pending_approval"},
                "lane": "clean_local_loop",
            }
        )
        card = "\n".join(f"- {line}" for line in plan.lines)
        return ToolOutcome(
            text=(
                f"ready; nothing changed yet. This list is shown under your answer:\n{card}\n"
                "In your answer, tell them in one short sentence what you will do, speaking to them as \"you\"."
            ),
            ok=True, status="pending_approval", proposal=proposal,
        )
    proposal = {"what": what or "(unspecified change)", "status": "pending_approval", "executed": False}
    ctx.proposals.append(proposal)
    ctx.receipts.append(
        {
            "tool_name": "propose_change",
            "mode": "pending_approval",
            "status": "pending_approval",
            "execution": {"executed": False, "ok": True, "status": "pending_approval"},
            "lane": "clean_local_loop",
        }
    )
    return ToolOutcome(
        text="proposal shown to the owner; waiting for their approval (nothing changed yet)",
        ok=True,
        status="pending_approval",
        proposal=proposal,
    )


BUILTIN_TOOLS = (
    ("list_dir", '{"tool": "list_dir", "path": "Downloads"}  list a folder (path relative to home; "" = home)', list_dir),
    ("find", '{"tool": "find", "query": "plumber invoice"}  search file names and document contents; repeat if the result says the content search is incomplete', find),
    ("read_file", '{"tool": "read_file", "path": "Documents/notes.txt", "start_line": 40}  read text from a line, or a PDF with "from_page" and "max_pages"', read_file),
    ("largest", '{"tool": "largest", "path": ""}  biggest files under a folder', largest),
    ("disk_usage", '{"tool": "disk_usage"}  how full the disk is', disk_usage),
    ("system_info", '{"tool": "system_info"}  computer specs: model, chip, memory, disk', system_info),
    ("processes", '{"tool": "processes", "sort": "cpu"}  what is running now, by cpu or memory', processes),
    ("privacy", '{"tool": "privacy"}  how much of the owner\'s text left this computer for online AI models today', privacy),
    ("propose_change", '{"tool": "propose_change", "what": "...", "ops": [{"op": "mkdir", "path": "Desktop/Screenshots"}, '
     '{"op": "move", "src": "Desktop/Screenshot*", "dst": "Desktop/Screenshots/"}, {"op": "write", "path": "Desktop/list.txt", '
     '"text": "..."}]}  show the owner a change (new folder, move, rename, new file) to approve; it is undoable', propose_change),
)


PENDING_NOTICE = "This is waiting for your approval; nothing has been changed yet."
CARD_NOTICE = "Nothing has changed yet. Say yes to do it, and afterwards \"undo\" puts it all back."


_FUTURE_CHANGE_RE = re.compile(
    r"\b(?:i['’]?ll|i\s+will|let\s+me|i['’]?m\s+going\s+to|i\s+am\s+going\s+to|i['’]?m)\s+"
    r"(?:now\s+)?(?:go\s+ahead\s+and\s+)?(?:creat|mak|writ|sav|mov|renam|put|add|organi[sz]|tidy|tidi|sort)\w*\b",
    re.I,
)


def _without_promises(answer: str) -> str:
    """The card is the promise, said exactly and waiting for a yes. A model sentence such as "I'll
    create the file on your desktop" says it as if under way, and the publication check (rightly)
    tags it as unbacked; the card already says what will happen, so that sentence goes."""
    text = str(answer or "").strip()
    if not text:
        return ""
    try:
        from core.agent_runtime.action_honesty_validator import _DELIVERABLE_PROMISE_RE as PROMISE_RE
    except Exception:
        PROMISE_RE = _FUTURE_CHANGE_RE

    def promised(part: str) -> bool:
        return bool(PROMISE_RE.search(part) or _FUTURE_CHANGE_RE.search(part))

    lines = []
    for line in text.split("\n"):
        kept = [part for part in re.split(r"(?<=[.!?:])\s+", line) if not promised(part)]
        if kept or not line.strip():
            lines.append(" ".join(kept))
    return "\n".join(lines).strip()


def pending_proposal_notice(answer: str, result: Any) -> str:
    """Answer gate: a turn that proposed a change always says the change has not happened, and a
    rehearsed change shows its card: exactly what will happen."""
    proposals = list(getattr(result, "proposals", None) or [])
    if not proposals:
        return answer
    cards = [p for p in proposals if p.get("card")]
    if cards:
        # The card is shown whole: a change keeps its own list short (moves are summed up past 30)
        # and never cuts a new file or folder, so nothing it will do can scroll out of sight.
        lines = "\n".join(f"- {line}" for line in cards[-1]["card"])
        return f"{_without_promises(answer)}\n\nHere is what I will do:\n{lines}\n\n{CARD_NOTICE}".strip()
    if "approv" in str(answer or "").lower():
        return answer
    return f"{str(answer or '').rstrip()}\n\n{PENDING_NOTICE}".strip()


_ABOUT_TAIL = re.compile(r" \((?:folder|[\d.]+ (?:bytes|KB|MB|GB|TB)), modified [^()]*\)$")
_EXT_RE = re.compile(r"\.[A-Za-z0-9]{1,5}\b")


def _seen_names(texts: list[str]) -> list[str]:
    names: list[str] = []
    for text in texts:
        for line in str(text or "").splitlines():
            if not line.startswith("- "):
                continue
            name = _ABOUT_TAIL.sub("", line[2:].strip()).rstrip("/")
            name = name.rsplit("/", 1)[-1].strip()
            if name and "." in name and name not in names:
                names.append(name)
    return names


def _shortened(fragment: str, name: str) -> bool:
    """`fragment` is a cut-down `name`: its tail ("notes.md" for "flat viewing notes.md") or its head
    with the same ending ("Screenshot 2026-10-20.png" for "Screenshot 2026-10-20 at 11.40.png")."""
    if fragment == name or len(fragment) < 5:
        return False
    if name.endswith(" " + fragment) or name.endswith("_" + fragment) or name.endswith("-" + fragment):
        return True
    stem, dot, ext = fragment.rpartition(".")
    return bool(dot and stem and name.endswith("." + ext) and name.startswith(stem + " "))


def exact_names(answer: str, result: Any) -> str:
    """Put back file names the model shortened: when a name in the answer is a cut-down form of
    exactly one name the tools showed, the full name replaces it (Mac clean-7 F07, F50: "notes.md"
    for "flat viewing notes.md"). Anything ambiguous is left as written."""
    names = _seen_names(list(getattr(result, "seen", None) or []))
    if not names or not answer:
        return answer
    out = answer
    for match in reversed(list(_EXT_RE.finditer(answer))):
        end = match.end()
        head = answer[:match.start()]
        starts = [m.start() for m in re.finditer(r"(?:^|(?<=[\s\"'`*(\[]))\S", head)][-8:]
        for start in starts:  # longest first
            fragment = answer[start:end]
            if fragment in names:
                break
            owners = [name for name in names if _shortened(fragment, name)]
            if len(owners) == 1 and owners[0] not in answer:
                out = out[:start] + owners[0] + out[end:]
                break
    return out


_ABSENCE_CLAIM_RE = re.compile(
    r"\b(?:not found|nothing found|couldn't find|could not find|can't find|cannot find|didn't find|did not find|"
    r"isn't there|is not there|not there|doesn't exist|does not exist|don't have(?: one)?|do not have(?: one)?|"
    r"no record|nothing saved|not saved|not in your files|no file)\b", re.I
)


# "It isn't there" and "you don't have one" claim more than a search can show.
_HARD_ABSENCE_RE = re.compile(
    r"\b(?:isn't there|is not there|aren't there|are not there|doesn't exist|does not exist|"
    r"don't exist|do not exist|(?:you )?(?:don't|do not) have (?:one|any|it|that|this)|"
    r"(?:there is |there's )?nothing (?:is )?saved|(?:was |is )?never saved|(?:it )?(?:was )?not saved)\b",
    re.I,
)
NOT_FOUND_TEXT = "I didn't find it in your files."
UNSEARCHED_TEXT = "I have not completed a search inside your files, so I can't say whether it is there."


def content_absence_gate(answer: str, result: Any) -> str:
    """An absence answer stands only after a content search ran this turn, and it says what a search
    can show: "I didn't find it in your files", never "it isn't there" or "you don't have one"."""
    text = str(answer or "")
    if not _ABSENCE_CLAIM_RE.search(text):
        return answer
    steps = list(getattr(result, "steps", None) or [])
    searched = any(
        step.get("tool") == "find" and step.get("status") == "content_search_complete"
        for step in steps
    )
    if not searched:
        return UNSEARCHED_TEXT
    if _HARD_ABSENCE_RE.search(text):
        questions = [part.strip() for part in re.findall(r"[^.!?]*\?", text) if part.strip()]
        return NOT_FOUND_TEXT + (" " + questions[-1] if questions else "")
    return answer


def content_absence_claim(answer: str) -> bool:
    return bool(_ABSENCE_CLAIM_RE.search(str(answer or "")))


def privacy_pre_step(ask: str, ctx: ToolContext) -> str | None:
    """A question about what left this computer is answered from the local meter before the model
    is asked anything, so it never has to guess or look online (Mac demo 2026-10-28)."""
    from core.clean_loop.route import is_privacy_ask

    if not is_privacy_ask(ask):
        return None
    return privacy({}, ctx).text


# --- evidence staged before the first model call ------------------------------------------------
#
# A deterministic own-computer ask (how full the disk is, what is running, what sits in the folder
# the ask points at) has one piece of evidence
# behind it. Staging that one piece here lets the model answer in its first call instead of
# spending one call choosing a tool, another reading it and a third answering.

_LOOK_CHARS = 2200
#: A write, a tidy or a bare reply to a card is never pre-answered: its turn shape is the card.
_LOOK_WRITE_RE = re.compile(
    r"\b(?:make|create|save|write|stick|put|move|tidy|organi[sz]e|sort|delete|remove|"
    r"clear(?:\s+out)?|get\s+rid|rename|set\s+up|undo|yes|ok)\b"
)
#: The disk stages only on the explicit how-much shape: a bare "full" or "filling" rides asks
#: about folders too, and a disk word alone does not say the answer is a measurement.
_LOOK_DISK_ASK_RE = re.compile(
    r"\bhow\s+full\b|\bhow\s+much\s+(?:space|room|storage)\b|\benough\s+(?:space|room|storage)\b"
)
#: "how much space do my holiday photos take" asks for the size of some files, not for the drive:
#: staging the drive figure there invited a made-up folder size (fresh-45 jury, a confident
#: "12.3 GB" for a few KB of photos, with nothing but the drive total in front of the model). The
#: object decides, not the verb: "how much storage am i using" is still the drive.
_LOOK_SIZE_OF_RE = re.compile(
    r"\b(?:photos?|pictures?|pics|images?|videos?|movies?|films?|music|songs?|downloads|desktop|"
    r"documents|docs|folders?|files?|apps?|applications|games?|emails?|mail|backups?|projects?)\b"
)
#: ...unless the ask is about room left for them ("enough space for a 20 GB game?").
_LOOK_ROOM_FOR_RE = re.compile(r"\b(?:left|available|spare|enough|(?:space|room|storage)\s+for)\b")
_LOOK_SPECS_RE = re.compile(r"\b(?:ram|specs|how\s+much\s+memory|what\s+chip|which\s+chip)\b")
_LOOK_PROC_RE = re.compile(r"\b(?:running|slowing|sluggish|cpu)\b")
_LOOK_BIG_RE = re.compile(r"\b(?:biggest|largest)\b")
#: The whats-in/inside/show shape, and — separately — the folder must be what the ask points at
#: ("in my downloads", "inside the lisbon folder"). A folder word sitting elsewhere in the ask
#: ("my downloads is full of them") is not a request for that folder's contents.
_LOOK_IN_RE = re.compile(
    r"\b(?:what'?s\s+(?:sitting\s+)?(?:in|on)|what\s+is\s+(?:in|on)|inside|show\s+me|"
    r"anything|any\s+new|what\s+do\s+i\s+have)\b"
)
_LOOK_WHERE_RE = re.compile(
    r"\b(?:in|on|inside)\s+(?:my\s+|the\s+)?(downloads|desktop|documents|pictures|photos|movies|videos)\b"
)
_LOOK_FOLDER_NAME_RE = re.compile(
    r"\b(?:in|on|inside)\s+(?:my\s+|the\s+)?([a-z0-9]+(?:\s+[a-z0-9]+){0,2})\s+folders?\b"
)
_LOOK_FILLER = frozenset({"the", "my", "a", "an", "in", "inside", "on", "at", "to", "from", "of"})


def _look_root(word: str, ctx: ToolContext) -> str:
    """The real label of a folder word the ask points at, when this loop can read it ("videos" is
    Movies here, "photos" is Pictures)."""
    for label in (word.capitalize(), _FOLDER_SYNONYMS.get(word, "")):
        if not label:
            continue
        try:
            if scope_path(label, ctx.home) in _safe_roots(ctx):
                return label
        except OutsideHome:
            return ""
    return ""


def _look_folder(low: str, ctx: ToolContext) -> str:
    """The folder an ask points at with in/on/inside: a named root, or the one readable folder
    whose name carries the words before "folder" ("whats inside the lisbon folder")."""
    where = _LOOK_WHERE_RE.search(low)
    if where is not None:
        return _look_root(where.group(1), ctx)
    named = _LOOK_FOLDER_NAME_RE.search(low)
    if named is None:
        return ""
    words = [word for word in named.group(1).split() if word not in _LOOK_FILLER]
    if not words:
        return ""
    hits = _find_folders(" ".join(words), ctx)
    return _plain(str(hits[0]), ctx.home) if len(hits) == 1 else ""


def look_first_pre_step(ask: str, ctx: ToolContext) -> str | None:
    """Stage the one piece of read-only evidence a deterministic own-computer ask needs before the
    first model call, so the answer can come in that single call. Everything staged went through
    the real tools, so the receipts say what truly ran; when nothing clearly matches, or the
    staging comes back empty, nothing is staged and the turn runs exactly as before."""
    from core.clean_loop.route import _RECALL_RE

    low = str(ask or "").lower()
    if _LOOK_WRITE_RE.search(_RECALL_RE.sub(" ", low)):
        return None
    before = len(ctx.receipts)

    def settle(outcome: ToolOutcome) -> str | None:
        if not outcome.ok or not str(outcome.text).strip():
            del ctx.receipts[before:]  # a staging that failed or came back empty never burdens the turn
            return None
        return outcome.text[:_LOOK_CHARS]

    if _LOOK_DISK_ASK_RE.search(low):
        if "how full" in low or not _LOOK_SIZE_OF_RE.search(low) or _LOOK_ROOM_FOR_RE.search(low):
            return settle(disk_usage({}, ctx))
        # The size of some files is measured by the model's own reads, never by the drive figure.
        return None
    if _LOOK_SPECS_RE.search(low):
        return settle(system_info({}, ctx))
    if _LOOK_PROC_RE.search(low):
        return settle(processes({"sort": "cpu" if "cpu" in low else "memory"}, ctx))
    if _LOOK_BIG_RE.search(low):
        return settle(largest({"path": _look_folder(low, ctx)}, ctx))
    if _LOOK_IN_RE.search(low):
        folder = _look_folder(low, ctx)
        return settle(list_dir({"path": folder}, ctx)) if folder else None
    # A find-shaped ask is never staged: its name hits are not the answer (the amount, the date,
    # the flight is inside the file), and a staged hit tempted the model to answer unread
    # (clean-loop-speed HEAD run, F08/F16/F17 confident-wrong). The model finds and reads itself.
    return None


def install_builtin_tools(registry: Registry) -> None:
    for name, doc, run in BUILTIN_TOOLS:
        registry.register_tool(name, doc, run)
    # Names first, then the card: the card's own lines are never rewritten.
    registry.register_pre_step("privacy", privacy_pre_step)
    registry.register_pre_step("look_first", look_first_pre_step)
    registry.register_answer_gate("exact_names", exact_names)
    registry.register_answer_gate("content_absence", content_absence_gate)
    registry.register_answer_gate("pending_proposal_notice", pending_proposal_notice)


__all__ = [
    "BUILTIN_TOOLS",
    "CARD_NOTICE",
    "PENDING_NOTICE",
    "OutsideHome",
    "content_absence_claim",
    "content_absence_gate",
    "document_matches",
    "home_dir",
    "install_builtin_tools",
    "pending_proposal_notice",
    "scope_path",
]
