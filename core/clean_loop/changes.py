"""Changes the owner can see first and take back: rehearse on a copy, apply what was shown, undo it.

A change is a short list of ops, paths relative to the home and inside the folders VOOL may write
(Desktop, Downloads, Documents):

  {"op": "mkdir", "path": "Desktop/Screenshots"}
  {"op": "move", "src": "Desktop/Screenshot*.png", "dst": "Desktop/Screenshots/"}   also renames; a
      `*` in src expands once, at preview time, and the expanded list is what gets applied
  {"op": "write", "path": "Desktop/shopping.txt", "text": "milk\\nbread"}            new files only

`preview` copies only the folders the change touches (a whole-home copy hashed ~/Library too, so on
a real Mac apply always refused with "folder changed"), runs the ops on that copy and returns the
card: what will happen, nothing changed yet. `apply` checks those folders still match, then makes
each change through VOOL's authorized machine tools (`machine.ensure_directory`, `machine.move_path`,
`machine.write_file`), so the permission decision and receipts are VOOL's own, and keeps the inverse.
`undo` puts it back through the same move tool. Two inverses have no machine tool and run here,
each guarded: removing a folder this change created only while it is empty, and removing a file
this change wrote only while its bytes are still the ones written.

Nothing deletes. A delete is RED and keeps the normal ceremony.
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_WRITE_ROOTS = ("Desktop", "Downloads", "Documents")
_MAX_OPS = 200
_PENDING_TTL_S = 30 * 60
_DONE_TTL_S = 7 * 24 * 3600


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
    after_hash: str
    created: float = field(default_factory=time.time)


@dataclass
class Done:
    plan: Plan
    inverse: list[dict[str, Any]]
    matches_preview: bool
    undone: bool = False
    created: float = field(default_factory=time.time)


# --- paths ------------------------------------------------------------------------------------


def _inside(home: Path, rel: Any) -> Path:
    text = str(rel or "").strip().strip("'\"")
    if text.startswith("~/"):
        text = text[2:]
    if not text:
        raise ChangeRefused("a change needs a path")
    full = Path(os.path.abspath(home / text.lstrip("/"))) if not Path(text).is_absolute() else Path(os.path.abspath(text))
    real = Path(os.path.realpath(full))
    for root_name in _WRITE_ROOTS:
        root = home / root_name
        if (real == root or root in real.parents) and (full == root or root in full.parents):
            if real == root:
                raise ChangeRefused(f"{root_name} itself cannot be changed")
            return full
    raise ChangeRefused(f"I can only change things inside {', '.join(_WRITE_ROOTS)}: {text}")


def _rel(home: Path, path: Path) -> str:
    return path.relative_to(home).as_posix()


def _root_of(home: Path, path: Path) -> str:
    return path.relative_to(home).parts[0]


def _expand(home: Path, ops: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Validate and normalise ops; a `*` in a move's src becomes one move per match."""
    if not isinstance(ops, list) or not ops:
        raise ChangeRefused("there is no change to make")
    out: list[dict[str, Any]] = []
    for op in ops:
        if not isinstance(op, dict):
            raise ChangeRefused("a change step was not understood")
        kind = str(op.get("op") or "").strip().lower()
        if kind in {"rename"}:
            kind = "move"
        if kind == "mkdir":
            out.append({"op": "mkdir", "path": _rel(home, _inside(home, op.get("path")))})
        elif kind == "write":
            target = _inside(home, op.get("path"))
            out.append({"op": "write", "path": _rel(home, target), "text": str(op.get("text") or "")})
        elif kind == "move":
            raw_src = str(op.get("src") or op.get("source") or "")
            dst = str(op.get("dst") or op.get("destination") or "")
            if any(ch in raw_src for ch in "*?["):
                pattern = _inside(home, raw_src)
                parent = pattern.parent
                try:
                    names = sorted(entry.name for entry in parent.iterdir())
                except OSError:
                    names = []
                matches = [name for name in names if fnmatch.fnmatch(name.lower(), pattern.name.lower())
                           and not name.startswith(".")]
                if not matches:
                    raise ChangeRefused(f"nothing matches {_rel(home, pattern)}")
                folder = dst if dst.endswith("/") else dst + "/"
                for name in matches:
                    out.append({"op": "move", "src": _rel(home, parent / name),
                                "dst": _rel(home, _inside(home, folder + name))})
            else:
                source = _inside(home, raw_src)
                target = _inside(home, dst)
                if dst.endswith("/"):
                    target = target / source.name
                out.append({"op": "move", "src": _rel(home, source), "dst": _rel(home, target)})
        else:
            raise ChangeRefused(f"'{kind or 'that'}' is not a change I can make here")
        if len(out) > _MAX_OPS:
            raise ChangeRefused(f"that is more than {_MAX_OPS} changes at once")
    return out


# --- the rehearsal ----------------------------------------------------------------------------


def _manifest(base: Path, roots: list[str]) -> dict[str, str]:
    """relpath -> sha256 for files, "dir" for folders, over the touched roots only; no links followed."""
    out: dict[str, str] = {}
    for root_name in roots:
        root = base / root_name
        if not root.is_dir():
            continue
        for current, dirs, files in os.walk(root, followlinks=False):
            dirs[:] = sorted(d for d in dirs if not d.startswith("."))
            rel_dir = Path(current).relative_to(base).as_posix()
            if Path(current) != root:
                out[rel_dir] = "dir"
            for name in sorted(files):
                if name.startswith("."):
                    continue
                path = Path(current) / name
                if path.is_symlink():
                    out[f"{rel_dir}/{name}"] = "link:" + os.readlink(path)
                    continue
                digest = hashlib.sha256()
                try:
                    with path.open("rb") as handle:
                        for chunk in iter(lambda: handle.read(1 << 20), b""):
                            digest.update(chunk)
                except OSError:
                    continue
                out[f"{rel_dir}/{name}"] = digest.hexdigest()
    return out


def _hash(manifest: dict[str, str]) -> str:
    return hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()


def _clone(src: Path, dst: Path) -> None:
    if sys.platform == "darwin":
        subprocess.run(["cp", "-c", "-R", str(src), str(dst)], check=True, capture_output=True)
    else:
        shutil.copytree(src, dst, symlinks=True)


def _rehearse(base: Path, ops: list[dict[str, Any]]) -> None:
    """Run ops on the copy. Same refusals the real apply would hit."""
    for op in ops:
        if op["op"] == "mkdir":
            (base / op["path"]).mkdir(parents=True, exist_ok=True)
        elif op["op"] == "write":
            target = base / op["path"]
            if target.exists():
                raise ChangeRefused(f"{op['path']} already exists; I only write new files")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(op["text"], encoding="utf-8")
        elif op["op"] == "move":
            source, target = base / op["src"], base / op["dst"]
            if not source.exists() and not source.is_symlink():
                raise ChangeRefused(f"{op['src']} is not there")
            if target.exists():
                raise ChangeRefused(f"{op['dst']} already exists")
            target.parent.mkdir(parents=True, exist_ok=True)
            os.rename(source, target)


def _card(before: dict[str, str], after: dict[str, str], ops: list[dict[str, Any]]) -> list[str]:
    lines = []
    new_dirs = sorted(k for k in after if k not in before and after[k] == "dir")
    lines += [f"new folder: {path}" for path in new_dirs]
    lines += [f"move: {op['src']} -> {op['dst']}" for op in ops if op["op"] == "move"]
    lines += [f"new file: {op['path']}" for op in ops if op["op"] == "write"]
    return lines


def preview(home: Path, ops: list[dict[str, Any]], *, workdir: str | None = None) -> Plan:
    home = Path(os.path.abspath(home))
    expanded = _expand(home, ops)
    roots = sorted({_root_of(home, home / p) for op in expanded for p in (op.get("path"), op.get("src"), op.get("dst")) if p})
    before = _manifest(home, roots)
    sandbox = Path(tempfile.mkdtemp(prefix="vool-preview-", dir=workdir))
    try:
        for root_name in roots:
            if (home / root_name).is_dir():
                _clone(home / root_name, sandbox / root_name)
        _rehearse(sandbox, expanded)
        after = _manifest(sandbox, roots)
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)
    return Plan(
        plan_id=uuid.uuid4().hex[:12], home=str(home), ops=expanded, roots=roots,
        lines=_card(before, after, expanded), before_hash=_hash(before), after_hash=_hash(after),
    )


# --- the real change, through VOOL's own tools ------------------------------------------------

Call = Any  # (intent, arguments) -> ToolOutcome-like with .ok and .text


def _missing_dirs(home: Path, target: Path) -> list[str]:
    made, probe = [], target
    while not probe.exists() and probe != home:
        made.append(_rel(home, probe))
        probe = probe.parent
    return made


def apply(plan: Plan, call: Call) -> Done:
    """Make the previewed change. Refuses when the touched folders changed since the card; on a
    failed step, puts back what already happened."""
    home = Path(plan.home)
    if _hash(_manifest(home, plan.roots)) != plan.before_hash:
        raise ChangeRefused("those folders changed since I showed you the change; ask me again and I'll show it fresh")
    inverse: list[dict[str, Any]] = []
    for op in plan.ops:
        if op["op"] == "mkdir":
            made = _missing_dirs(home, home / op["path"])
            outcome = call("machine.ensure_directory", {"path": str(home / op["path"])})
            step = {"rmdirs": made}
        elif op["op"] == "write":
            made = _missing_dirs(home, (home / op["path"]).parent)
            outcome = call("machine.write_file", {"path": str(home / op["path"]), "content": op["text"]})
            step = {"remove": op["path"], "sha256": hashlib.sha256(op["text"].encode("utf-8")).hexdigest(), "rmdirs": made}
        else:
            made = _missing_dirs(home, (home / op["dst"]).parent)
            if made:
                made_out = call("machine.ensure_directory", {"path": str((home / op["dst"]).parent)})
                if not getattr(made_out, "ok", False):
                    problems = _roll_back(home, inverse, call)
                    if problems:
                        raise ChangeRefused("a step did not happen, and I could not put all of it back: " + "; ".join(problems[:5]))
                    if getattr(made_out, "status", "") == "pending_approval":
                        raise NeedsPermission(NEEDS_PERMISSION_TEXT)
                    raise ChangeRefused(f"could not make {made[-1]}: {getattr(made_out, 'text', '')}")
            outcome = call("machine.move_path", {"source": str(home / op["src"]), "destination": str(home / op["dst"])})
            step = {"move": [op["src"], op["dst"]], "rmdirs": made}
        if not getattr(outcome, "ok", False):
            if step.get("rmdirs"):
                inverse.append({"rmdirs": step["rmdirs"]})
            problems = _roll_back(home, inverse, call)
            if problems:
                raise ChangeRefused(
                    f"a step did not happen ({getattr(outcome, 'text', '')}), and I could not put all of it back: "
                    + "; ".join(problems[:5])
                )
            if getattr(outcome, "status", "") == "pending_approval":
                raise NeedsPermission(NEEDS_PERMISSION_TEXT)
            raise ChangeRefused(f"that step did not happen, so I put everything back: {getattr(outcome, 'text', '')}")
        inverse.append(step)
    matches = _hash(_manifest(home, plan.roots)) == plan.after_hash
    return Done(plan=plan, inverse=inverse, matches_preview=matches)


def _roll_back(home: Path, inverse: list[dict[str, Any]], call: Call) -> list[str]:
    problems: list[str] = []
    for step in reversed(inverse):
        if "move" in step:
            src, dst = step["move"]
            if (home / src).exists():
                problems.append(f"something new is at {src}, so {dst} stayed where it is")
                continue
            outcome = call("machine.move_path", {"source": str(home / dst), "destination": str(home / src)})
            if not getattr(outcome, "ok", False):
                problems.append(f"{dst} could not be moved back: {getattr(outcome, 'text', '')}")
        if "remove" in step:
            path = home / step["remove"]
            try:
                if hashlib.sha256(path.read_bytes()).hexdigest() == step["sha256"] and not path.is_symlink():
                    path.unlink()
                else:
                    problems.append(f"{step['remove']} was edited after I wrote it, so I left it")
            except FileNotFoundError:
                pass
            except OSError as exc:
                problems.append(f"{step['remove']} could not be removed ({type(exc).__name__})")
        for rel in step.get("rmdirs", []):
            folder = home / rel
            try:
                if folder.is_dir() and not folder.is_symlink() and not any(folder.iterdir()):
                    folder.rmdir()
            except OSError:
                continue
    return problems


def undo(done: Done, call: Call) -> list[str]:
    """Put the change back. Returns what could not be put back (empty when all of it was)."""
    if done.undone:
        return []
    problems = _roll_back(Path(done.plan.home), done.inverse, call)
    done.undone = True
    return problems


# --- per-session state ------------------------------------------------------------------------

_LOCK = threading.Lock()
_PENDING: OrderedDict[str, Plan] = OrderedDict()
_DONE: OrderedDict[str, Done] = OrderedDict()


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


def drop_pending(session_id: str) -> bool:
    with _LOCK:
        return _PENDING.pop(session_id, None) is not None


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


__all__ = [
    "ChangeRefused",
    "NEEDS_PERMISSION_TEXT",
    "NeedsPermission",
    "Done",
    "Plan",
    "apply",
    "drop_pending",
    "has_pending",
    "last_done",
    "preview",
    "remember_done",
    "remember_pending",
    "reset_for_tests",
    "take_pending",
    "undo",
]
