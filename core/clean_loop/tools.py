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

import logging
import os
import re
import stat
import time
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
_NAME_ENTRIES = 3000
_NAME_TTL_S = 60.0
_NAME_CACHE: dict[str, tuple[float, frozenset[str]]] = {}


def file_words(home: Path) -> frozenset[str]:
    """Distinctive words from the names of the owner's files in Desktop, Documents and Downloads
    (three levels, 3000 entries, hidden entries and links skipped), kept for a minute. Names only:
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
    stack = [(home / root, 0) for root in _NAME_ROOTS]
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


def _plain(text: str, home: Path) -> str:
    """Tool prose for the model: home-relative paths, no internal intent names."""
    out = str(text or "").replace(str(home) + os.sep, "").replace(str(home), "~")
    out = re.sub(r"`[a-z_]+\.[a-z_.]+`", "that action", out)
    return out[:_RESULT_CHARS]


def _call(ctx: ToolContext, intent: str, arguments: dict[str, Any]) -> ToolOutcome:
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
    plain = _plain(text, ctx.home) if ok else "not available: " + (_plain(text, ctx.home) or status)
    ctx.receipts.append(
        {
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
        }
    )
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


def _merge(outcomes: list[ToolOutcome], text: str) -> ToolOutcome:
    intents = tuple(i for o in outcomes for i in o.intents)
    ok = any(o.ok for o in outcomes)
    status = "executed" if ok else (outcomes[-1].status if outcomes else "error")
    return ToolOutcome(text=text[:_RESULT_CHARS] or "(empty)", ok=ok, status=status, intents=intents)


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
    if hits:
        return _merge(outcomes, "\n".join(hits[:20]))
    inside = _find_in_text(words, ctx)
    if inside:
        lines = [f"no file name has {', '.join(words)}; these files mention it inside:", *inside]
        return _merge([*outcomes, ToolOutcome(text="", ok=True)], "\n".join(lines))
    return _merge(outcomes, "nothing found for: " + ", ".join(words)
                  + ". Try another word the owner used, a folder name, or list_dir on Downloads or Documents.")


_TEXT_SUFFIXES = frozenset({".txt", ".md", ".csv", ".pdf", ".json", ".eml", ".ics", ".html", ".rtf", ".log"})
_TEXT_MAX_BYTES = 2 * 1024 * 1024
_TEXT_MAX_FILES = 25


def _text_candidates(ctx: ToolContext) -> list[Path]:
    """Document-like files in the readable folders, newest first: no links, no hidden entries, bounded."""
    found: list[tuple[float, Path]] = []
    frontier, seen = _readable_dirs(ctx), 0
    for _depth in range(_RESOLVE_DEPTH):
        nxt: list[Path] = []
        for folder in frontier:
            try:
                entries = list(folder.iterdir())
            except OSError:
                continue
            for entry in entries:
                seen += 1
                if seen > _RESOLVE_ENTRIES:
                    break
                try:
                    if entry.name.startswith(".") or entry.is_symlink():
                        continue
                    if entry.is_dir():
                        nxt.append(entry)
                    elif entry.suffix.lower() in _TEXT_SUFFIXES:
                        info = entry.stat()
                        if info.st_size <= _TEXT_MAX_BYTES:
                            found.append((info.st_mtime, entry))
                except OSError:
                    continue
        frontier = nxt
    return [path for _mtime, path in sorted(found, key=lambda item: -item[0])[:_TEXT_MAX_FILES]]


def _find_in_text(words: list[str], ctx: ToolContext) -> list[str]:
    """No name matched: look inside the owner's documents, through the same read tools ("plumber" is
    in invoice_4471.pdf, never in its name: Mac audit F08). Best match first, with the line it is on."""
    scored: list[tuple[int, str]] = []
    for path in _text_candidates(ctx):
        before = len(ctx.receipts)
        intent = "pdf.extract_text" if path.suffix.lower() == ".pdf" else "machine.read_file"
        arguments = {"path": str(path), "max_pages": 5} if intent == "pdf.extract_text" else {"path": str(path)}
        outcome = _call(ctx, intent, arguments)
        if not outcome.ok:
            continue
        body = outcome.text.lower()
        matched = [word for word in words if word in body]
        if not matched:
            # A read that matched nothing is not this turn's evidence about the ask.
            del ctx.receipts[before:]
            continue
        at = body.find(matched[0])
        start = outcome.text.rfind("\n", 0, at) + 1
        end = outcome.text.find("\n", at)
        snippet = " ".join(outcome.text[start: end if end != -1 else at + 160].split())[:160]
        label = _plain(str(path), ctx.home)
        about = describe(path, ctx.home)
        scored.append((len(matched), f"- {label} ({about}): \"{snippet}\""))
    return [line for _score, line in sorted(scored, key=lambda item: -item[0])[:5]]


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
        extracted = _call(ctx, "pdf.extract_text", {"path": str(target), "max_pages": 5})
        if extracted.ok:
            return extracted
        # The PDF reader can be missing on a machine (the Mac audit: its platform module was not
        # importable). A plain read still shows a simple PDF's text; the failure stays on record.
        plain = _call(ctx, "machine.read_file", {"path": str(target)})
        return plain if plain.ok else extracted
    return _call(ctx, "machine.read_file", {"path": str(target)})


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
    for receipt in ctx.receipts[before:]:
        receipt["response_preview"] = text[:1800]
    return ToolOutcome(text=text, ok=outcome.ok, status=outcome.status, intents=outcome.intents)


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
    ("find", '{"tool": "find", "query": "plumber invoice"}  find files by words in their name, or inside them', find),
    ("read_file", '{"tool": "read_file", "path": "Desktop/todo.txt"}  read a text, csv, md or pdf file', read_file),
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
        return f"{str(answer or '').rstrip()}\n\nHere is what I will do:\n{lines}\n\n{CARD_NOTICE}".strip()
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


def privacy_pre_step(ask: str, ctx: ToolContext) -> str | None:
    """A question about what left this computer is answered from the local meter before the model
    is asked anything, so it never has to guess or look online (Mac demo 2026-10-28)."""
    from core.clean_loop.route import is_privacy_ask

    if not is_privacy_ask(ask):
        return None
    return privacy({}, ctx).text


def install_builtin_tools(registry: Registry) -> None:
    for name, doc, run in BUILTIN_TOOLS:
        registry.register_tool(name, doc, run)
    # Names first, then the card: the card's own lines are never rewritten.
    registry.register_pre_step("privacy", privacy_pre_step)
    registry.register_answer_gate("exact_names", exact_names)
    registry.register_answer_gate("pending_proposal_notice", pending_proposal_notice)


__all__ = [
    "BUILTIN_TOOLS",
    "CARD_NOTICE",
    "PENDING_NOTICE",
    "OutsideHome",
    "home_dir",
    "install_builtin_tools",
    "pending_proposal_notice",
    "scope_path",
]
