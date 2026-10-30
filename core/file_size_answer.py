"""A size-of-a-thing ask is answered with the size of that thing only.

Measured 2026-10-30: "how big is the holiday photos zip" (a real file in Downloads) was answered
with the file's size AND the machine's disk fill, because no deterministic lane owned the ask, the
model fished for a number, and a mid-turn drive read rode the turn to the provider. This module is
that lane: it resolves the NAMED thing under the runtime's safe roots (or a path the person typed,
which the same law as list_directory allows reading), stats it, and answers with nothing but that
thing's size. Drive totals, free space and percent-full never appear here -- not in the reply, not
in the details -- because the ask was never about them. A miss fails closed in plain words: never
a guess, never a number about the machine.
"""
from __future__ import annotations

import os
import re
import stat as stat_module
import time

# A folder walk that sums sizes is bounded twice: by the same clock budget the folder-search walk
# uses (imported at call time with its module, so this file never cycles), and by an entry count,
# so a pathological tree (a deep node_modules, a mounted backup) cannot hold the turn even on a
# fast disk. A walk that hits either bound has UNDER-counted, which is why the answer then says
# "at least" rather than presenting a partial sum as the whole size.
_SIZE_WALK_ENTRY_CAP = 20_000
# How many matches get a size in the reply. The count is always named; the rest are counted only.
_SIZE_LIST_CAP = 3


def _human_size(num_bytes: int) -> str:
    """Bytes the way a person reads them: binary steps, one decimal, B/KB/MB/GB/TB.

    Bytes stay whole ("248 B") because they are exact; every larger unit gets the one decimal
    ("12.0 MB") so a size never reads more precise than the measurement.
    """
    size = float(num_bytes)
    units = ("B", "KB", "MB", "GB", "TB")
    index = 0
    while size >= 1024.0 and index < len(units) - 1:
        size /= 1024.0
        index += 1
    if units[index] == "B":
        return f"{int(size)} B"
    return f"{size:.1f} {units[index]}"


def _walk_folder_size(start: str, *, deadline: float, entry_cap: int) -> tuple[int, bool]:
    """Sum ``st_size`` under ``start``; ``(bytes, cut_short)``.

    Symlinks are never followed (a link's own bytes count; its target is another thing, and
    following one can loop), a directory already visited under another name is skipped (macOS
    firmlinks mount the data volume twice), and any unreadable entry marks the walk cut short --
    a sum with holes is a lower bound, and the reply must say so.
    """
    total = 0
    seen_entries = 0
    cut_short = False
    visited: set[tuple[int, int]] = set()
    pending = [start]
    while pending:
        if time.monotonic() >= deadline or seen_entries >= entry_cap:
            cut_short = True
            break
        current = pending.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    seen_entries += 1
                    if seen_entries >= entry_cap:
                        cut_short = True
                        break
                    try:
                        info = entry.stat(follow_symlinks=False)
                        if stat_module.S_ISDIR(info.st_mode):
                            key = (info.st_dev, info.st_ino)
                            if key not in visited:
                                visited.add(key)
                                pending.append(entry.path)
                        else:
                            total += info.st_size
                    except OSError:
                        cut_short = True
        except OSError:
            cut_short = True
    return total, cut_short


def _base_name(path: str) -> str:
    """The thing's own name, never a path the person did not give.

    Splits on BOTH separators before taking the last part: real trees exist whose directory names
    carry literal backslashes (a Windows tree copied as-is), and those would otherwise be paraded
    through a reply as a whole path.
    """
    parts = [part for part in re.split(r"[\\/]", str(path).rstrip("/\\")) if part]
    return parts[-1] if parts else str(path)


def _folder_display(name: str) -> str:
    """The folder's name as it is said in a reply: "recipes folder", but never "packed folder
    folder" when the directory is itself called "packed folder"."""
    if name.lower().endswith((" folder", " directory")):
        return name
    return f"{name} folder"


def _disambiguator(path: str, listed_names: list[str]) -> str:
    """" (in Downloads)" only when two listed files share one name; a lone name stays clean."""
    base = _base_name(path)
    if listed_names.count(base) < 2:
        return ""
    parent = _base_name(os.path.dirname(path))
    return f" (in {parent})" if parent else ""


def _miss(response_text: str, **details: object) -> dict:
    return {
        "handled": True,
        "ok": False,
        "response_text": response_text,
        "details": {"intent": "machine.file_size", "ok": False, **details},
    }


def _answer_for_path(raw_path: str) -> dict:
    """A typed path is allowed to be read for size, the same law as list_directory."""
    from core.runtime_execution_tools import _expand_machine_user

    target = str(raw_path or "").strip().strip("'\"")
    try:
        expanded = str(_expand_machine_user(target))
        info = os.stat(expanded)
    except (OSError, ValueError, RuntimeError):
        return _miss(f"I couldn't find anything at {raw_path} to measure.", explicit_path=raw_path)
    if stat_module.S_ISDIR(info.st_mode):
        return _folder_answer(expanded, [_base_name(expanded)], [expanded], place="")
    size = info.st_size
    return {
        "handled": True,
        "ok": True,
        "response_text": f"{_base_name(expanded)} is {_human_size(size)}.",
        "details": {
            "intent": "machine.file_size",
            "kind": "file",
            "explicit_path": raw_path,
            "match_count": 1,
            "matches": [expanded],
            "sizes_bytes": {expanded: size},
            "cut_short": False,
        },
    }


def _folder_answer(label_name: str, display_names: list[str], paths: list[str], *, place: str) -> dict:
    """Size one or more resolved folders under one shared clock, and answer with sizes only."""
    from core.runtime_execution_tools import _FIND_FOLDER_TIME_BUDGET_S

    deadline = time.monotonic() + _FIND_FOLDER_TIME_BUDGET_S
    sizes: dict[str, int] = {}
    cut_short = False
    for path in paths[:_SIZE_LIST_CAP]:
        size, short = _walk_folder_size(path, deadline=deadline, entry_cap=_SIZE_WALK_ENTRY_CAP)
        sizes[path] = size
        cut_short = cut_short or short
    qualifier = "at least " if cut_short else ""
    if len(paths) == 1:
        response_text = f"The {_folder_display(display_names[0])} is {qualifier}{_human_size(sizes[paths[0]])}."
    else:
        measured = zip(display_names, paths[:_SIZE_LIST_CAP])
        listed = "; ".join(f"{_folder_display(name)} is {qualifier}{_human_size(sizes[path])}" for name, path in measured)
        response_text = f"I found {len(paths)} folders called '{label_name}': {listed}."
        if len(paths) > _SIZE_LIST_CAP:
            response_text += f" ({len(paths) - _SIZE_LIST_CAP} more not measured.)"
    return {
        "handled": True,
        "ok": True,
        "response_text": response_text,
        "details": {
            "intent": "machine.file_size",
            "kind": "folder",
            "place": place,
            "query": label_name,
            "match_count": len(paths),
            "matches": paths[:_SIZE_LIST_CAP],
            "sizes_bytes": sizes,
            "cut_short": cut_short,
        },
    }


def _answer_for_folder_root(place: str) -> dict:
    """The subject was one of the safe roots themselves ("how big is my documents folder")."""
    from core.runtime_execution_tools import _resolved_existing_directory, _safe_machine_roots

    root = next((candidate for candidate in _safe_machine_roots() if candidate.name == place), None)
    resolved = str(root) if root is not None else _resolved_existing_directory(f"~/{place}")
    if resolved is None or not os.path.isdir(resolved):
        return _miss(f"I couldn't read the {place} folder to measure it.", place=place)
    answer = _folder_answer(place, [place], [resolved], place=place)
    answer["response_text"] = answer["response_text"].replace(f"The {place} folder is", f"Your {place} folder is", 1)
    return answer


def _answer_for_folder_name(name: str, place: str) -> dict:
    """Resolve a folder NAME the folder family's way, then size what it found."""
    from core.authorized_tool_execution import execute_authorized_runtime_tool
    from core.runtime_execution_tools import _find_folder_name_from_phrase

    # The detector hands over the RAW phrase; reduction ("my notes folder on the desktop") is
    # done here because its helpers live in the module this file must not import at top level.
    phrase_name, phrase_scope = _find_folder_name_from_phrase(name)
    if phrase_scope and not phrase_name:
        # The phrase named only a location ("the download folder in documents" reduced to nothing
        # but the scope): that place IS the subject.
        from core.runtime_execution_tools import _resolved_existing_directory

        resolved = _resolved_existing_directory(phrase_scope)
        if resolved is None:
            return _miss("I couldn't read that folder to measure it.", query=name)
        return _folder_answer(name, [_base_name(resolved)], [resolved], place=place)
    needle = phrase_name or name
    execution = execute_authorized_runtime_tool("machine.find_folder", {"name": needle}, source_context={})
    if execution is None or not getattr(execution, "ok", False):
        return _miss(f"I couldn't look for a folder called '{needle}' right now.", query=needle)
    matches = [path for path in list((getattr(execution, "details", None) or {}).get("matches") or []) if os.path.isdir(path)]
    if not matches:
        return _miss(f"I couldn't find a folder called '{needle}' in the folders I can look in.", query=needle)
    return _folder_answer(needle, [_base_name(path) for path in matches], matches, place=place)


def _answer_for_file_name(name: str, place: str) -> dict:
    """Resolve a file NAME with the file-search fast path, then answer sizes only."""
    from core.agent_runtime.fast_paths_machine import find_named_files

    found = find_named_files(("", (name,), place), source_context=None)
    if found is None or not getattr(found, "ok", False):
        return _miss(f"I couldn't look for a file called '{name}' right now.", query=name)
    matches = list((getattr(found, "details", None) or {}).get("matches") or [])
    if not matches:
        return _miss(f"I couldn't find a file called '{name}' in the folders I can look in.", query=name)
    sizes: dict[str, int] = {}
    for path in matches[:_SIZE_LIST_CAP]:
        try:
            sizes[path] = os.stat(path).st_size
        except OSError:
            continue
    if not sizes:
        return _miss(f"I found a file called '{name}' but couldn't read its size.", query=name)
    if len(matches) == 1:
        path, size = next(iter(sizes.items()))
        response_text = f"{_base_name(path)} is {_human_size(size)}."
    else:
        names = [_base_name(path) for path in sizes]
        listed = "; ".join(
            f"{_base_name(path)}{_disambiguator(path, names)} is {_human_size(size)}"
            for path, size in sizes.items()
        )
        response_text = f"I found {len(matches)} files matching '{name}'. {listed}"
        if len(matches) > len(sizes):
            response_text += f"; and {len(matches) - len(sizes)} more"
        response_text += "."
    return {
        "handled": True,
        "ok": True,
        "response_text": response_text,
        "details": {
            "intent": "machine.file_size",
            "kind": "file",
            "place": place,
            "query": name,
            "match_count": len(matches),
            "matches": list(sizes),
            "sizes_bytes": sizes,
            "cut_short": False,
        },
    }


def size_answer_for_ask(user_text: str) -> dict | None:
    """The fast-path answer for a size-of-a-thing ask, or ``None`` when the ask is not one.

    ``machine_size_query_intent`` decides ownership; this resolves the named thing under the safe
    roots (or stats a typed path), and returns ``{"handled": True, "ok": bool, "response_text":
    str, "details": {...}}``. The response text carries only the asked-about thing's size (and
    match counts); it never carries a drive total, free space or percent-full, because those are
    answers to a different question -- the disk family's, which the detector has already stood
    down for. Nothing here prints or logs the user's words.
    """
    from core.execution.constants import machine_size_query_intent

    claimed = machine_size_query_intent(user_text)
    if claimed is None:
        return None
    kind, name, place, explicit_path = claimed
    if explicit_path:
        return _answer_for_path(explicit_path)
    if kind == "folder":
        if not name and place:
            return _answer_for_folder_root(place)
        return _answer_for_folder_name(name, place)
    return _answer_for_file_name(name, place)
