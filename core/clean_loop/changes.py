"""Changes the owner can see first and take back: rehearse, apply what was shown, undo it.

A change is a short list of ops, paths relative to the home and inside the folders VOOL may write
(Desktop, Downloads, Documents):

  {"op": "mkdir", "path": "Desktop/Screenshots"}
  {"op": "move", "src": "Desktop/Screenshot*.png", "dst": "Desktop/Screenshots/"}   also renames; a
      `*` in src expands once, at preview time, and the expanded list is what gets applied
  {"op": "write", "path": "Desktop/shopping.txt", "text": "milk\\nbread"}            new files only

No path may pass through a link (shortcut), at any level: a link could carry the change out of the
write folders. `preview` rehearses the ops on a model of just the paths they name (nothing on disk
is copied or touched) and returns the card: what will happen, with each new file's text, nothing
changed yet. `apply` checks the named paths and their folders still look the same, rechecks each
step just before it, and makes each change through VOOL's authorized machine tools
(`machine.ensure_directory`, `machine.move_path`, `machine.write_file` with create_only), so the
permission decision and receipts are VOOL's own, and keeps the inverse. `undo` puts it back through
the same move tool. Two inverses have no machine tool and run here, on a parent folder opened
without following links: removing a folder this change created only while it is empty, and removing
a file this change wrote only while its bytes are still the ones written. A step that could not be
put back stays on the list, so "undo" can be asked again.

Nothing deletes. A delete is RED and keeps the normal ceremony.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path, PurePosixPath
from typing import Any

_WRITE_ROOTS = ("Desktop", "Downloads", "Documents")
_MAX_OPS = 200
_PENDING_TTL_S = 10 * 60
_DONE_TTL_S = 60 * 60
_LISTING_BUDGET = 20000
_PREVIEW_CHARS = 160
_CARD_MOVES = 30
#: Names a click runs rather than opens: a change never writes one (review 2026-10-28, a long move
#: list could hide a written "Open me.command").
_AUTORUN_SUFFIXES = frozenset({
    ".command", ".tool", ".sh", ".bash", ".zsh", ".csh", ".app", ".workflow", ".scpt", ".applescript",
    ".terminal", ".webloc", ".inetloc", ".fileloc", ".desktop", ".exe", ".bat", ".cmd", ".com", ".ps1",
    ".vbs", ".js", ".jar", ".pkg", ".dmg", ".py", ".pl", ".rb", ".action", ".url", ".lnk", ".scr",
})


class ChangeRefused(ValueError):
    """An op the change will not make, said in plain words."""


class NeedsPermission(ChangeRefused):
    """The authorized boundary wants its own approval for each step: the mode, not the change, said no."""


#: VOOL's permission setting wants its own approval for a step (moves always do, even in Auto). The
#: card does not stand in for that ceremony: nothing changes, the card is kept, and the owner hears it
#: plainly. One approval for a whole card, through VOOL's own approval store, is the next build.
NEEDS_PERMISSION_TEXT = (
    "your permission settings want to approve each file move one by one, and I won't go around that, "
    "so nothing changed. The change is still ready"
)


@dataclass
class Plan:
    plan_id: str
    home: str
    ops: list[dict[str, Any]]
    roots: list[str]
    lines: list[str]
    before_hash: str
    created: float = field(default_factory=time.time)


@dataclass
class Done:
    plan: Plan
    inverse: list[dict[str, Any]]
    matches_preview: bool
    direct: list[str] = field(default_factory=list)
    undone: bool = False
    #: True until another turn happens: only then is a bare "undo" taken at its word.
    fresh: bool = True
    created: float = field(default_factory=time.time)


# --- paths ------------------------------------------------------------------------------------


def _rel_inside(home: Path, raw: Any) -> str:
    """The path relative to home, lexically inside one write folder (not the folder itself)."""
    text = str(raw or "").strip().strip("'\"")
    if text.startswith("~/"):
        text = text[2:]
    if not text:
        raise ChangeRefused("a change needs a path")
    if os.path.isabs(text):
        rel = os.path.relpath(os.path.normpath(text), home)
    else:
        rel = os.path.normpath(text)
    parts = PurePosixPath(rel.replace(os.sep, "/")).parts
    if not parts or parts[0] in {"..", ".", "/"} or os.path.isabs(rel) or parts[0] not in _WRITE_ROOTS:
        raise ChangeRefused(f"I can only change things inside {', '.join(_WRITE_ROOTS)}: {text}")
    if len(parts) == 1:
        raise ChangeRefused(f"{parts[0]} itself cannot be changed")
    return "/".join(parts)


def _lstat(path: Path) -> os.stat_result | None:
    try:
        return os.lstat(path)
    except (FileNotFoundError, NotADirectoryError):
        return None
    except OSError as exc:
        raise ChangeRefused(f"I can't look at {path.name} ({type(exc).__name__})") from exc


def _no_links(home: Path, rel: str) -> None:
    """Refuse when any existing part of the path, the write folder included, is a link or a file
    standing where a folder should be."""
    current = home
    parts = PurePosixPath(rel).parts
    for index, part in enumerate(parts):
        current = current / part
        info = _lstat(current)
        if info is None:
            return
        if stat.S_ISLNK(info.st_mode):
            raise ChangeRefused(f"{'/'.join(parts[:index + 1])} is a shortcut (link), and I don't change things through links")
        if index < len(parts) - 1 and not stat.S_ISDIR(info.st_mode):
            raise ChangeRefused(f"{'/'.join(parts[:index + 1])} is a file, not a folder")


def _checked(home: Path, raw: Any, *, must_exist: bool = False) -> str:
    rel = _rel_inside(home, raw)
    _no_links(home, rel)
    if must_exist and _lstat(home / rel) is None:
        raise ChangeRefused(f"{rel} is not there")
    return rel


def _glob(home: Path, raw_src: str) -> list[str]:
    pattern = _rel_inside(home, raw_src)
    parent, name = PurePosixPath(pattern).parent.as_posix(), PurePosixPath(pattern).name
    if parent in _WRITE_ROOTS:
        _no_links(home, parent + "/x")
    else:
        _no_links(home, parent)
    try:
        with os.scandir(home / parent) as entries:
            found = sorted(
                entry.name for entry in entries
                if not entry.name.startswith(".") and not entry.is_symlink() and fnmatch(entry.name.lower(), name.lower())
            )
    except OSError:
        found = []
    if not found:
        raise ChangeRefused(f"nothing matches {pattern}")
    return [f"{parent}/{entry}" for entry in found]


def _expand(home: Path, ops: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Validate and normalise ops; a `*` in a move's src becomes one move per match (links skipped)."""
    if not isinstance(ops, list) or not ops:
        raise ChangeRefused("there is no change to make")
    out: list[dict[str, Any]] = []
    for op in ops:
        if not isinstance(op, dict):
            raise ChangeRefused("a change step was not understood")
        kind = str(op.get("op") or "").strip().lower()
        if kind == "rename":
            kind = "move"
        if kind == "mkdir":
            out.append({"op": "mkdir", "path": _checked(home, op.get("path"))})
        elif kind == "write":
            path = _checked(home, op.get("path"))
            if PurePosixPath(path).suffix.lower() in _AUTORUN_SUFFIXES:
                raise ChangeRefused(f"{PurePosixPath(path).name} would be a file that runs when opened; I only write plain files")
            out.append({"op": "write", "path": path, "text": str(op.get("text") or "")})
        elif kind == "move":
            raw_src = str(op.get("src") or op.get("source") or "")
            dst = str(op.get("dst") or op.get("destination") or "")
            if any(ch in raw_src for ch in "*?["):
                folder = dst if dst.endswith("/") else dst + "/"
                for src in _glob(home, raw_src):
                    out.append({"op": "move", "src": src, "dst": _checked(home, folder + PurePosixPath(src).name)})
            else:
                src = _checked(home, raw_src, must_exist=True)
                target = _checked(home, dst.rstrip("/") + "/" + PurePosixPath(src).name if dst.endswith("/") else dst)
                out.append({"op": "move", "src": src, "dst": target})
        else:
            raise ChangeRefused(f"'{kind or 'that'}' is not a change I can make here")
        if len(out) > _MAX_OPS:
            raise ChangeRefused(f"that is more than {_MAX_OPS} changes at once")
    return out


def _named(ops: list[dict[str, Any]]) -> list[str]:
    return sorted({op[key] for op in ops for key in ("path", "src", "dst") if op.get(key)})


# --- what apply checks ------------------------------------------------------------------------


def _describe(path: Path) -> str:
    info = _lstat(path)
    if info is None:
        return "absent"
    if stat.S_ISLNK(info.st_mode):
        try:
            return "link:" + os.readlink(path)
        except OSError:
            return "link"
    if stat.S_ISDIR(info.st_mode):
        return "dir"
    return f"file:{info.st_size}:{info.st_mtime_ns}"


def _signature(home: Path, ops: list[dict[str, Any]]) -> str:
    """The named paths and the listings of their folders (hidden entries and links included).
    Not the whole folder tree: a whole-home hash met ~/Library and always differed on a real Mac."""
    state: dict[str, Any] = {}
    parents = set()
    for rel in _named(ops):
        state[rel] = _describe(home / rel)
        parents.add(PurePosixPath(rel).parent.as_posix())
    budget = _LISTING_BUDGET
    for parent in sorted(parents):
        folder = home / parent
        try:
            with os.scandir(folder) as entries:
                rows = sorted(
                    f"{entry.name}:{'link' if entry.is_symlink() else ('dir' if entry.is_dir(follow_symlinks=False) else 'file')}"
                    for entry in entries
                )
        except OSError:
            rows = ["absent"]
        state["listing:" + parent] = rows[:budget]
        budget = max(budget - len(rows), 0)
    return hashlib.sha256(json.dumps(state, sort_keys=True).encode()).hexdigest()


# --- the rehearsal ----------------------------------------------------------------------------


class _Model:
    """Just enough of the disk to rehearse the ops: each named path is "dir", "file" or None."""

    def __init__(self, home: Path):
        self.home = home
        self.state: dict[str, str | None] = {}
        self.moved_folders: list[str] = []
        self.new_folders: list[str] = []

    def _under(self, rel: str, prefix: str) -> bool:
        return rel == prefix or rel.startswith(prefix + "/")

    def kind(self, rel: str) -> str | None:
        if rel in self.state:
            return self.state[rel]
        if any(self._under(rel, folder) for folder in self.moved_folders):
            raise ChangeRefused(f"{rel} is inside a folder this change moves; let's do that in two steps")
        info = _lstat(self.home / rel)
        if info is None:
            return None
        if stat.S_ISLNK(info.st_mode):
            raise ChangeRefused(f"{rel} is a shortcut (link), and I don't change things through links")
        return "dir" if stat.S_ISDIR(info.st_mode) else "file"

    def make_dirs(self, rel: str) -> None:
        parts = PurePosixPath(rel).parts
        for end in range(2, len(parts) + 1):
            prefix = "/".join(parts[:end])
            current = self.kind(prefix)
            if current == "file":
                raise ChangeRefused(f"{prefix} is a file, not a folder")
            if current is None:
                self.state[prefix] = "dir"
                self.new_folders.append(prefix)


def _rehearse(home: Path, ops: list[dict[str, Any]]) -> list[str]:
    """Run the ops on the model, with the same refusals the real apply would hit. Returns the new
    folders, in the order they appear."""
    model = _Model(home)
    for op in ops:
        if op["op"] == "mkdir":
            model.make_dirs(op["path"])
        elif op["op"] == "write":
            if model.kind(op["path"]) is not None:
                raise ChangeRefused(f"{op['path']} already exists; I only write new files")
            model.make_dirs(PurePosixPath(op["path"]).parent.as_posix())
            model.state[op["path"]] = "file"
        else:
            src, dst = op["src"], op["dst"]
            kind = model.kind(src)
            if kind is None:
                raise ChangeRefused(f"{src} is not there")
            if dst == src or dst.startswith(src + "/"):
                raise ChangeRefused(f"{src} cannot go inside itself")
            if model.kind(dst) is not None:
                raise ChangeRefused(f"{dst} already exists")
            model.make_dirs(PurePosixPath(dst).parent.as_posix())
            model.state[src] = None
            model.state[dst] = kind
            if kind == "dir":
                model.moved_folders += [src, dst]
    return model.new_folders


def _card(new_folders: list[str], ops: list[dict[str, Any]]) -> list[str]:
    """New files first, each with its size and opening text, then new folders, then moves. Files
    and folders are never cut; a long list of moves is."""
    lines = []
    for op in ops:
        if op["op"] == "write":
            size = len(op["text"].encode("utf-8"))
            shown = " / ".join(line.strip() for line in op["text"].splitlines() if line.strip())
            if len(shown) > _PREVIEW_CHARS:
                shown = shown[:_PREVIEW_CHARS].rstrip() + "..."
            lines.append(f"new file: {op['path']} ({size} bytes): \"{shown}\"")
    lines += [f"new folder: {path}" for path in new_folders]
    moves = [f"move: {op['src']} -> {op['dst']}" for op in ops if op["op"] == "move"]
    if len(moves) > _CARD_MOVES:
        moves = moves[:_CARD_MOVES] + [f"...and {len(moves) - _CARD_MOVES} more moves into the same folders"]
    return lines + moves


def preview(home: Path, ops: list[dict[str, Any]], *, workdir: str | None = None) -> Plan:
    """What the change would do. Reads only the paths it names; writes nothing anywhere."""
    del workdir  # (the old copy-based rehearsal's scratch folder; kept so callers don't break)
    home = Path(os.path.abspath(home))
    expanded = _expand(home, ops)
    new_folders = _rehearse(home, expanded)
    roots = sorted({PurePosixPath(rel).parts[0] for rel in _named(expanded)})
    return Plan(
        plan_id=uuid.uuid4().hex[:12], home=str(home), ops=expanded, roots=roots,
        lines=_card(new_folders, expanded), before_hash=_signature(home, expanded),
    )


# --- the real change, through VOOL's own tools ------------------------------------------------

Call = Any  # (intent, arguments) -> ToolOutcome-like with .ok, .text and .status


def _missing_dirs(home: Path, rel: str) -> list[str]:
    made, probe = [], PurePosixPath(rel)
    while len(probe.parts) > 1 and _lstat(home / probe.as_posix()) is None:
        made.append(probe.as_posix())
        probe = probe.parent
    return made


def _recheck(home: Path, op: dict[str, Any]) -> None:
    """Just before a step: no links on the way, the source is still there, the target still free."""
    for key in ("path", "src", "dst"):
        if op.get(key):
            _no_links(home, op[key])
    if op["op"] == "write" and _lstat(home / op["path"]) is not None:
        raise ChangeRefused(f"{op['path']} appeared since I showed you the change")
    if op["op"] == "move":
        if _lstat(home / op["src"]) is None:
            raise ChangeRefused(f"{op['src']} is gone since I showed you the change")
        if _lstat(home / op["dst"]) is not None:
            raise ChangeRefused(f"{op['dst']} appeared since I showed you the change")


def _fail(home: Path, inverse: list[dict[str, Any]], call: Call, direct: list[str], why: str, status: str = "") -> None:
    try:
        problems = _roll_back(home, inverse, call, direct)
    except Exception as exc:  # never lose the record of what is still out
        problems = [f"putting it back stopped ({type(exc).__name__})"]
    if problems:
        refusal: ChangeRefused = ChangeRefused(
            f"a step did not happen ({why}), and I could not put all of it back: " + "; ".join(problems[:5])
        )
    elif status == "pending_approval":
        refusal = NeedsPermission(NEEDS_PERMISSION_TEXT)
    else:
        refusal = ChangeRefused(f"that step did not happen, so I put everything back: {why}")
    refusal.direct = list(direct)  # type: ignore[attr-defined]
    refusal.left = list(inverse)  # type: ignore[attr-defined]
    raise refusal


def apply(plan: Plan, call: Call) -> Done:
    """Make the previewed change. Refuses when the named paths or their folders changed since the
    card; on a failed step, puts back what already happened."""
    home = Path(plan.home)
    if _signature(home, plan.ops) != plan.before_hash:
        raise ChangeRefused("those folders changed since I showed you the change; ask me again and I'll show it fresh")
    inverse: list[dict[str, Any]] = []
    direct: list[str] = []
    for op in plan.ops:
        try:
            _apply_step(home, op, inverse, direct, call)
        except ChangeRefused:
            raise
        except Exception as exc:  # a tool fault mid-change still puts back what happened
            _fail(home, inverse, call, direct, f"{type(exc).__name__}")
    return Done(plan=plan, inverse=inverse, matches_preview=_matches(home, plan.ops), direct=direct)


def _apply_step(home: Path, op: dict[str, Any], inverse: list[dict[str, Any]], direct: list[str], call: Call) -> None:
    """One step through VOOL's own tool, its inverse kept; a failed step puts back what happened."""
    try:
        _recheck(home, op)
    except ChangeRefused as exc:
        _fail(home, inverse, call, direct, str(exc))
    if op["op"] == "mkdir":
        made = _missing_dirs(home, op["path"])
        outcome = call("machine.ensure_directory", {"path": str(home / op["path"])})
        step: dict[str, Any] = {"rmdirs": made}
    elif op["op"] == "write":
        made = _missing_dirs(home, PurePosixPath(op["path"]).parent.as_posix())
        outcome = call(
            "machine.write_file",
            {"path": str(home / op["path"]), "content": op["text"], "create_only": True},
        )
        step = {"remove": op["path"], "sha256": hashlib.sha256(op["text"].encode("utf-8")).hexdigest(), "rmdirs": made}
    else:
        parent = PurePosixPath(op["dst"]).parent.as_posix()
        made = _missing_dirs(home, parent)
        if made:
            made_out = call("machine.ensure_directory", {"path": str(home / parent)})
            if not getattr(made_out, "ok", False):
                inverse.append({"rmdirs": made})
                _fail(home, inverse, call, direct, f"could not make {made[-1]}: {getattr(made_out, 'text', '')}",
                      getattr(made_out, "status", ""))
        outcome = call("machine.move_path", {"source": str(home / op["src"]), "destination": str(home / op["dst"])})
        step = {"move": [op["src"], op["dst"]], "rmdirs": made}
    if not getattr(outcome, "ok", False):
        if step.get("rmdirs"):
            inverse.append({"rmdirs": step["rmdirs"]})
        _fail(home, inverse, call, direct, str(getattr(outcome, "text", "")), getattr(outcome, "status", ""))
    if op["op"] == "write":
        info = _lstat(home / op["path"])
        step["ino"] = [info.st_dev, info.st_ino] if info is not None else None
    inverse.append(step)


def _matches(home: Path, ops: list[dict[str, Any]]) -> bool:
    final: dict[str, str | None] = {}
    for op in ops:
        if op["op"] == "mkdir":
            final[op["path"]] = "dir"
        elif op["op"] == "write":
            final[op["path"]] = "file:" + hashlib.sha256(op["text"].encode("utf-8")).hexdigest()
        else:
            final[op["src"]], final[op["dst"]] = None, "there"
    for rel, want in final.items():
        info = _lstat(home / rel)
        if want is None:
            if info is not None:
                return False
        elif info is None or stat.S_ISLNK(info.st_mode):
            return False
        elif want == "dir" and not stat.S_ISDIR(info.st_mode):
            return False
        elif want.startswith("file:"):
            try:
                if hashlib.sha256((home / rel).read_bytes()).hexdigest() != want[5:]:
                    return False
            except OSError:
                return False
    return True


# --- putting it back --------------------------------------------------------------------------


def _pinned_parent(home: Path, rel: str) -> tuple[int, str]:
    """A descriptor for the folder holding `rel`, opened one part at a time without following links,
    and the last name. A link swapped in anywhere on the way fails here instead of being followed."""
    parts = PurePosixPath(rel).parts
    fd = os.open(home, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in parts[:-1]:
            nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = nxt
    except BaseException:
        os.close(fd)
        raise
    return fd, parts[-1]


def _remove_written(home: Path, rel: str, sha256: str, direct: list[str], ino: list[int] | None = None) -> str:
    """"" when the file is gone (or was never there); otherwise why it stayed."""
    try:
        fd, name = _pinned_parent(home, rel)
    except FileNotFoundError:
        return ""
    except OSError as exc:
        return f"{rel} could not be removed ({type(exc).__name__})"
    try:
        try:
            handle = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fd)
        except FileNotFoundError:
            return ""
        try:
            info = os.fstat(handle)
            if not stat.S_ISREG(info.st_mode) or (ino and [info.st_dev, info.st_ino] != list(ino)):
                return f"{rel} was replaced after I wrote it, so I left it"
            digest = hashlib.sha256()
            for chunk in iter(lambda: os.read(handle, 1 << 20), b""):
                digest.update(chunk)
        finally:
            os.close(handle)
        if digest.hexdigest() != sha256:
            return f"{rel} was edited after I wrote it, so I left it"
        now = os.lstat(name, dir_fd=fd)
        if [now.st_dev, now.st_ino] != [info.st_dev, info.st_ino]:
            return f"{rel} was replaced after I wrote it, so I left it"
        os.unlink(name, dir_fd=fd)
        direct.append(f"removed file {rel}")
        return ""
    except OSError as exc:
        return f"{rel} could not be removed ({type(exc).__name__})"
    finally:
        os.close(fd)


def _remove_empty_dirs(home: Path, rels: list[str], direct: list[str]) -> list[str]:
    """Folders this change made, deepest first, only while empty. Returns the ones still there."""
    left = []
    for rel in rels:
        try:
            fd, name = _pinned_parent(home, rel)
        except FileNotFoundError:
            continue
        except OSError:
            left.append(rel)
            continue
        try:
            os.rmdir(name, dir_fd=fd)
            direct.append(f"removed empty folder {rel}")
        except FileNotFoundError:
            pass
        except OSError:
            left.append(rel)
        finally:
            os.close(fd)
    return left


def _roll_back(home: Path, inverse: list[dict[str, Any]], call: Call, direct: list[str]) -> list[str]:
    """Undo the steps newest first. A step that was put back leaves the list; one that could not
    stays, so a later "undo" tries it again. Returns what could not be put back."""
    problems: list[str] = []
    for step in list(reversed(inverse)):
        if "move" in step:
            src, dst = step["move"]
            try:
                _no_links(home, src)
                _no_links(home, dst)
            except ChangeRefused as exc:
                problems.append(f"{dst} stayed where it is: {exc}")
                continue
            if _lstat(home / src) is not None:
                problems.append(f"something new is at {src}, so {dst} stayed where it is")
                continue
            if _lstat(home / dst) is not None:
                outcome = call("machine.move_path", {"source": str(home / dst), "destination": str(home / src)})
                if not getattr(outcome, "ok", False):
                    problems.append(f"{dst} could not be moved back: {getattr(outcome, 'text', '')}")
                    continue
        if "remove" in step:
            why = _remove_written(home, step["remove"], step["sha256"], direct, step.get("ino"))
            if why:
                problems.append(why)
                continue
        left = _remove_empty_dirs(home, list(step.get("rmdirs") or []), direct)
        if left and problems:
            # Still holding something this undo could not put back yet: try the folder again next time.
            step.pop("move", None)
            step.pop("remove", None)
            step["rmdirs"] = left
            continue
        # (A folder that now holds the owner's own new things simply stays.)
        inverse.remove(step)
    return problems


def undo(done: Done, call: Call) -> list[str]:
    """Put the change back. Returns what could not be put back (empty when all of it was); what is
    left can be tried again."""
    with _UNDO_LOCK:  # two "undo" turns at once never run the inverse twice
        if done.undone:
            return []
        problems = _roll_back(Path(done.plan.home), done.inverse, call, done.direct)
        done.undone = not done.inverse
        return problems


# --- per-session state ------------------------------------------------------------------------

_LOCK = threading.Lock()
_UNDO_LOCK = threading.Lock()
_PENDING: OrderedDict[str, Plan] = OrderedDict()
_DONE: OrderedDict[str, Done] = OrderedDict()
_UNDO_ASKED: dict[str, Done] = {}


def remember_pending(session_id: str, plan: Plan) -> None:
    with _LOCK:
        _PENDING[session_id] = plan
        _PENDING.move_to_end(session_id)
        while len(_PENDING) > 64:
            _PENDING.popitem(last=False)


def take_pending(session_id: str) -> Plan | None:
    with _LOCK:
        plan = _PENDING.pop(session_id, None)
    if plan is None or time.time() - plan.created > _PENDING_TTL_S:
        return None
    return plan


def has_pending(session_id: str) -> bool:
    with _LOCK:
        plan = _PENDING.get(session_id)
    return plan is not None and time.time() - plan.created <= _PENDING_TTL_S


def forget_session_turn(session_id: str) -> bool:
    """A card answers only the very next turn: any other turn ends it, so a later "yes" to something
    else never applies it. A change made earlier then needs its undo confirmed (`stale_done`)."""
    with _LOCK:
        _UNDO_ASKED.pop(session_id, None)
        done = _DONE.get(session_id)
        if done is not None:
            done.fresh = False
        return _PENDING.pop(session_id, None) is not None


def ask_undo(session_id: str, done: Done) -> None:
    """A bare "undo" turns after other turns: ask first; only the next turn's "yes" undoes."""
    with _LOCK:
        _UNDO_ASKED[session_id] = done


def take_undo_asked(session_id: str) -> Done | None:
    with _LOCK:
        done = _UNDO_ASKED.pop(session_id, None)
    return done if done is not None and not done.undone else None


def remember_done(session_id: str, done: Done) -> None:
    with _LOCK:
        _DONE[session_id] = done
        _DONE.move_to_end(session_id)
        while len(_DONE) > 64:
            _DONE.popitem(last=False)


def last_done(session_id: str) -> Done | None:
    with _LOCK:
        done = _DONE.get(session_id)
    if done is None or done.undone or time.time() - done.created > _DONE_TTL_S:
        return None
    return done


def reset_for_tests() -> None:
    with _LOCK:
        _PENDING.clear()
        _DONE.clear()
        _UNDO_ASKED.clear()


__all__ = [
    "ChangeRefused",
    "NEEDS_PERMISSION_TEXT",
    "NeedsPermission",
    "Done",
    "Plan",
    "apply",
    "ask_undo",
    "forget_session_turn",
    "has_pending",
    "last_done",
    "preview",
    "remember_done",
    "remember_pending",
    "reset_for_tests",
    "take_pending",
    "take_undo_asked",
    "undo",
]
