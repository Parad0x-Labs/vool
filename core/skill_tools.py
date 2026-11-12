"""Author, check, and activate a skill.

"can we create a new skill?" was one of the live failures. The runtime could already LOAD skills —
`core/plugin_skills.py` parses `SKILL.md`, ranks them against the turn, and injects the author's
instructions — but nothing could write one, so the only way to add a capability was to leave the
product, hand-author Markdown with correct YAML frontmatter, and put it in exactly the right folder.

Three steps rather than one, because they fail for different reasons and only the last one changes
what the assistant does:

``create``    write a `SKILL.md` into a staging directory. Nothing is loaded yet.
``validate``  parse it with the REAL loader and report what is missing. A skill that parses but has
              no description never ranks, so "it parsed" is not the question worth answering.
``install``   move a validated skill into the active plugins tree, where the loader will find it.

`install` is the only one with a side effect on behaviour, and it re-validates rather than trusting
that `validate` was ever run — the two are separate calls and a model can skip one.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import re
import shutil
import threading
from pathlib import Path
from typing import Any

from core.plugin_skills import parse_skill, parse_skill_content

# Where an unactivated skill is written. Deliberately not under the plugins tree: a half-written
# skill must not be loadable, and `create` is not an authorisation to change behaviour.
STAGING_DIRNAME = "staged-skills"
_MAX_BODY_CHARS = 8000
_SLUG_RE = re.compile(r"[^a-z0-9]+")

# The version store lives NEXT TO the active SKILL.md (versions/v<N>.md + history.json). The
# loader reads only SKILL.md, so versioning adds no second load path: old versions are inert
# bytes until a rollback copies one back over SKILL.md through the gated install path.
VERSIONS_DIRNAME = "versions"
# Left beside a draft brought in from someone's file (core.skill_package.import_package): a skill
# installed from that draft records "import" in its history, so it is never offered as one the
# person made (core.registry.handoff).
IMPORTED_MARK = "imported.json"
_HISTORY_LOCK = threading.Lock()

# Activation-class mutations (install, rollback) serialize the whole check-copy-record
# step on ONE lock. Without it the exists-check and the copy are separate moments: a
# no-overwrite install that passed the check can clobber bytes a racing install already
# landed (the refusal contract bypassed), and two concurrent copies into one SKILL.md can
# interleave open/truncate/write and leave a torn file that is neither skill. In-process
# serialization is the real domain here: installs are gated behind the one daemon's
# approval flow; across processes the history store stays safe on its own (atomic
# .tmp+replace) and SKILL.md degrades to last-writer-wins.
_ACTIVATION_LOCK = threading.Lock()


def _history_path(skill_dir: Path) -> Path:
    return skill_dir / VERSIONS_DIRNAME / "history.json"


def _read_history(skill_dir: Path) -> dict[str, Any]:
    try:
        data = json.loads(_history_path(skill_dir).read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("versions"), list):
            return data
    except (OSError, ValueError):
        pass
    return {"head_version": 0, "versions": []}


def _write_history_atomic(skill_dir: Path, history: dict[str, Any]) -> None:
    path = _history_path(skill_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(history, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def _record_version(skill_dir: Path, *, source: str, restored_from: int | None = None) -> dict[str, Any]:
    """Snapshot the CURRENT SKILL.md as the next immutable version, atomically.

    The snapshot file lands before the history entry names it, so a crash between the two
    leaves an unrecorded file, never a history row pointing at bytes that do not exist.
    Two digests are recorded (recovered skill-identity law): ``sha256`` over the exact raw
    bytes (the package), and ``effective_sha256`` over the parsed manifest + body with
    hidden HTML-comment blocks stripped — the digest of what actually instructs. Two files
    with the same effective digest are the same instructions regardless of packaging.
    """
    active = skill_dir / "SKILL.md"
    content = active.read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    effective = _effective_digest_for_bytes(content)
    instruction = _instruction_digest_for_bytes(content)
    with _HISTORY_LOCK:
        history = _read_history(skill_dir)
        versions = list(history.get("versions") or [])
        entry = {
            "version": len(versions) + 1,
            "sha256": digest,
            "effective_sha256": effective,
            "instruction_sha256": instruction,
            "bytes": len(content),
            "source": str(source),
            "installed_at": _utcnow(),
        }
        if restored_from is not None:
            entry["restored_from"] = int(restored_from)
        versions_dir = skill_dir / VERSIONS_DIRNAME
        versions_dir.mkdir(parents=True, exist_ok=True)
        snapshot = versions_dir / f"v{entry['version']}.md"
        if not snapshot.exists():
            snapshot.write_bytes(content)
        versions.append(entry)
        _write_history_atomic(skill_dir, {"head_version": entry["version"], "versions": versions})
    return entry


def _effective_digest_for_bytes(content: bytes) -> str:
    """The effective-instruction digest of one SKILL.md's bytes (hidden blocks stripped).

    Fail-safe to the package digest: identity enrichment must not make an install fail
    on a file the real loader accepts — the collision gate below is the refusing seam.
    """
    try:
        from core.skill_identity import compute_effective_digest, strip_hidden_blocks

        text = content.decode("utf-8")
        match = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", text, re.DOTALL)
        if match is None:
            return hashlib.sha256(content).hexdigest()
        from core.skill_frontmatter import load_header

        manifest = load_header(match.group(1))
        clean, _ = strip_hidden_blocks(match.group(2) or "")
        return compute_effective_digest(manifest, clean)
    except Exception:
        return hashlib.sha256(content).hexdigest()


def _instruction_digest_for_bytes(content: bytes) -> str:
    """Digest of the INSTRUCTION BODY alone (frontmatter excluded, comments stripped).

    This is the collision-scan key: two skills whose bodies carry the same
    instructions are one instruction set, whatever their names or packaging.
    Distinct from ``effective_sha256`` in the history, which (per the recovered
    identity law) binds the manifest into identity. Fail-safe to the package
    digest so enrichment can never make an install fail on its own.
    """
    try:
        from core.skill_identity import strip_hidden_blocks

        text = content.decode("utf-8")
        match = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", text, re.DOTALL)
        if match is None:
            return hashlib.sha256(content).hexdigest()
        clean, _ = strip_hidden_blocks(match.group(2) or "")
        return hashlib.sha256(clean.encode("utf-8")).hexdigest()
    except Exception:
        return hashlib.sha256(content).hexdigest()


def _installed_effective_digests(exclude_slug: str) -> dict[str, str]:
    """Head instruction digest of every OTHER installed skill: {slug: digest}.

    Reads the recorded history first (cheap) and falls back to hashing the live
    SKILL.md for histories that predate the two-digest law.
    """
    found: dict[str, str] = {}
    try:
        root = plugins_root() / "plugins"
    except Exception:
        return found
    if not root.is_dir():
        return found
    for skill_dir in root.glob("*/skills/*/"):
        slug = skill_dir.name
        if slug == exclude_slug:
            continue
        active = skill_dir / "SKILL.md"
        if not active.is_file():
            continue
        try:
            history = _read_history(skill_dir)
            versions = list(history.get("versions") or [])
            head = versions[-1] if versions else {}
            digest = str(head.get("instruction_sha256") or "").strip()
            if not digest:
                digest = _instruction_digest_for_bytes(active.read_bytes())
            found[slug] = digest
        except Exception:
            continue
    return found


def _scoped_effective_digests(keys: dict[str, str] | None, *, exclude_dir: Path | None = None) -> dict[str, str]:
    """Head instruction digest of every active chat/project skill for these keys: {label: digest}.

    Labelled ``<scope>:<slug>`` so a collision names which scope already holds the instructions.
    The destination being (re)installed is excluded: overwriting a skill with itself is an edit.
    """
    found: dict[str, str] = {}
    excluded = exclude_dir.resolve() if exclude_dir is not None else None
    for scope, active in _scope_active_dirs(keys):
        if not active.is_dir():
            continue
        for skill_dir in sorted(active.iterdir()):
            skill_md = skill_dir / "SKILL.md"
            if not skill_md.is_file():
                continue
            with contextlib.suppress(OSError):
                if excluded is not None and skill_dir.resolve() == excluded:
                    continue
                versions = list(_read_history(skill_dir).get("versions") or [])
                digest = str((versions[-1] if versions else {}).get("instruction_sha256") or "").strip()
                found[f"{scope}:{skill_dir.name}"] = digest or _instruction_digest_for_bytes(skill_md.read_bytes())
    return found


def _ensure_scope_record(root: Path, scope: str, keys: dict[str, str] | None) -> None:
    """Record which chat or project a scope folder belongs to, once, next to its skills.

    The folder name may be a digest of the key; the record keeps the real key so a listing can
    say whose skills these are without reversing anything.
    """
    record = root / "scope.json"
    if record.exists():
        return
    root.mkdir(parents=True, exist_ok=True)
    payload = {"scope": scope, "key": str((keys or {}).get(scope) or ""), "created_at": _utcnow()}
    record.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _utcnow() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _scoped_dirs(scope: str, keys: dict[str, str] | None) -> tuple[Path, Path] | None:
    """(staged root, active root) of one chat's or project's skills, or None when unavailable."""
    from core.skill_scopes import ACTIVE_DIRNAME, STAGED_DIRNAME, scope_root

    root = scope_root(scope, keys)
    if root is None:
        return None
    return root / STAGED_DIRNAME, root / ACTIVE_DIRNAME


def _scope_active_dirs(keys: dict[str, str] | None) -> list[tuple[str, Path]]:
    """The active chat and project skill roots for these keys, narrowest first."""
    found: list[tuple[str, Path]] = []
    for scope in ("chat", "project"):
        dirs = _scoped_dirs(scope, keys)
        if dirs is not None:
            found.append((scope, dirs[1]))
    return found


def _installed_skill_dir_by_name(name: str, keys: dict[str, str] | None = None) -> Path | None:
    """The INSTALLED skill directory this human name or slug means, or None.

    Staging is deliberately not searched: versions exist only for activated skills, and a
    rollback against a draft would silently no-op the operator's intent. With scope keys, this
    chat's and this project's active skills are searched first (narrowest wins), then the global
    plugins tree.
    """
    slug = _slug(name)
    if not slug:
        return None
    for _scope, active in _scope_active_dirs(keys):
        candidate = active / slug
        if (candidate / "SKILL.md").is_file():
            return candidate
    root = plugins_root() / "plugins"
    with contextlib.suppress(OSError):
        for candidate in sorted(root.glob(f"*/skills/{slug}")):
            if (candidate / "SKILL.md").is_file():
                return candidate
    return None


def skill_history(name: str, keys: dict[str, str] | None = None) -> dict[str, Any]:
    """The recorded versions of an installed skill: head, entries, where they live."""
    skill_dir = _installed_skill_dir_by_name(name, keys)
    if skill_dir is None:
        return {
            "status": "not_found",
            "reason": f"no installed skill named {name!r} — versions exist after activation",
        }
    history = _read_history(skill_dir)
    return {
        "status": "ok",
        "name": skill_dir.name,
        "path": str(skill_dir / "SKILL.md"),
        "head_version": int(history.get("head_version") or 0),
        "versions": list(history.get("versions") or []),
    }


def installed_skill_version_for_path(path: str) -> int:
    """The recorded head version of the installed skill this SKILL.md path belongs to.

    Zero when the path carries no version history (native library, staging, unknown trees) —
    callers treat that as "no version recorded", never as version zero of something.
    """
    candidate = Path(str(path or "")).expanduser()
    if candidate.name != "SKILL.md":
        return 0
    skill_dir = candidate.parent
    if not (skill_dir / VERSIONS_DIRNAME / "history.json").is_file():
        return 0
    return int(_read_history(skill_dir).get("head_version") or 0)


def rollback_skill(name: str, *, version: int, keys: dict[str, str] | None = None) -> dict[str, Any]:
    """Restore a prior version of an installed skill, byte for byte, as the new head.

    The bytes come from the immutable snapshot; the restore itself is a new head move recorded
    in the same history, so nothing is rewritten and every state the runtime passed through
    stays reconstructible. Calling this is an ACTIVATION-class change (it changes what future
    turns load) and belongs to `skill.rollback`, gated like `skill.install`.
    """
    name = str(name or "").strip()
    skill_dir = _installed_skill_dir_by_name(name, keys)
    if skill_dir is None:
        return {
            "status": "not_found",
            "reason": f"no installed skill named {name!r} to roll back",
        }
    wanted = int(version)
    with _ACTIVATION_LOCK:
        history = _read_history(skill_dir)
        entry = next(
            (dict(row) for row in history.get("versions") or [] if int(row.get("version") or 0) == wanted),
            None,
        )
        if entry is None:
            known = sorted(int(row.get("version") or 0) for row in history.get("versions") or [])
            return {
                "status": "unknown_version",
                "reason": f"version {wanted} is not in the recorded history {known}",
                "known_versions": known,
            }
        snapshot = skill_dir / VERSIONS_DIRNAME / f"v{wanted}.md"
        if not snapshot.is_file():
            return {
                "status": "error",
                "reason": f"the snapshot for version {wanted} is missing from {snapshot.parent}",
            }
        active = skill_dir / "SKILL.md"
        tmp = active.with_name(active.name + ".tmp")
        try:
            tmp.write_bytes(snapshot.read_bytes())
            tmp.replace(active)
            recorded = _record_version(skill_dir, source="rollback", restored_from=wanted)
        except OSError as exc:
            with contextlib.suppress(OSError):
                tmp.unlink(missing_ok=True)
            return {"status": "error", "reason": f"{type(exc).__name__}: could not restore version {wanted}"}
    return {
        "status": "ok",
        "path": str(active),
        "name": skill_dir.name,
        "restored_from": wanted,
        "version": recorded["version"],
        "active": True,
        "note": "the loader picks this up on the next turn; no restart is needed",
    }


# Russian, Ukrainian and Belarusian letters as Latin ones, the way a passport writes them, so a skill
# named "Список покупок" gets the short name "spisok-pokupok" rather than none at all.
_CYRILLIC = str.maketrans({
    "а": "a", "б": "b", "в": "v", "г": "g", "ґ": "g", "д": "d", "е": "e", "ё": "e", "є": "ie", "ж": "zh",
    "з": "z", "и": "i", "і": "i", "ї": "i", "й": "i", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o",
    "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ў": "u", "ф": "f", "х": "kh", "ц": "ts", "ч": "ch",
    "ш": "sh", "щ": "shch", "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "iu", "я": "ia",
})


def _slug(name: str) -> str:
    """The short name (folder and id) of a skill: lowercase a-z, 0-9 and hyphens.

    A name in Latin letters keeps the slug it always had. A name with no Latin letters at all used to
    get an empty slug, and the skill was refused as having "no name" (audit M6, 2026-11-11): Cyrillic
    letters are now spelled in Latin letters, and any other script (Japanese, Arabic, ...) gets
    ``skill-`` and a short code made from the name, so the same name always gives the same short name.
    The person's own name is kept as the skill's name; only the folder uses the short one.
    """
    text = str(name or "").strip()
    slug = _SLUG_RE.sub("-", text.lower()).strip("-")
    if slug:
        return slug
    import unicodedata

    folded = unicodedata.normalize("NFKC", text).casefold()
    if not any(ch.isalnum() for ch in folded):
        return ""
    spelled = "".join(ch for ch in unicodedata.normalize("NFKD", folded.translate(_CYRILLIC))
                      if not unicodedata.combining(ch))
    latin = _SLUG_RE.sub("-", spelled).strip("-")
    letters = [ch for ch in spelled if ch.isalnum()]
    if latin and all(ch.isascii() for ch in letters):
        return latin[:MAX_NAME_CHARS].strip("-")
    code = hashlib.sha256(" ".join(folded.split()).encode("utf-8")).hexdigest()[:10]
    return f"skill-{code}"


def plugins_root() -> Path:
    """The tree `core/plugin_catalog` scans. Resolved through it, never guessed at separately.

    The docstring's promise was not kept: this read `_DEFAULT_PLUGINS_DIR` directly and so ignored
    the `VOOL_PLUGINS_DIR` override that `plugin_catalog.plugins_root()` honours. With an override
    set, skills installed into the DEFAULT tree while the catalog listed the OVERRIDE tree, and an
    installed skill was invisible with no error anywhere (measured 2026-08-29).

    `plugin_catalog.plugins_root()` returns None until a `plugins/` directory exists, which is
    exactly the state a first install has to be able to create — so the configured root is resolved
    the same way here, and the existence check is left to the caller. That resolver also carries the
    legacy-folder reuse rule (Nulla-skills-plugins before the rename), so staging, install and the
    catalog agree on ONE tree.
    """

    from core.plugin_catalog import configured_plugins_root

    return configured_plugins_root()


def staging_root() -> Path:
    return plugins_root() / STAGING_DIRNAME


# Imports are staged per CHAT, never in the shared ``staging_root()/<slug>``: a second import of the
# same name from another chat (or another tab) used to overwrite the draft a person had just
# reviewed, and "turn it on" then activated the other one (red team t8, 2026-11-10). The leading dot
# keeps the folder out of every ``*/SKILL.md`` scan and can never be a slug.
IMPORTS_DIRNAME = ".imports"


def import_staging_dir(keys: dict[str, str] | None) -> Path | None:
    """This chat's own staging folder for imported skills, or None when the turn names no chat."""
    from core.skill_scopes import _safe_dir

    chat = str((keys or {}).get("chat") or "").strip()
    if not chat:
        return None
    return staging_root() / IMPORTS_DIRNAME / _safe_dir(chat)


# Drafts ``skill.create`` writes from a chat are staged per CHAT too (audit M7, 2026-11-11): the old
# shared ``staging_root()/<slug>`` let a same-name draft from another chat (or a model's call there)
# replace the one this chat showed, and what went live was whatever sat in the folder by then. A turn
# that names no chat keeps the shared folder.
DRAFTS_DIRNAME = ".drafts"
#: Left beside every draft ``skill.create`` writes: the digest of the bytes it wrote (and showed), so
#: turning the draft on later, after a restart included, installs those bytes or nothing.
DRAFTED_MARK = "drafted.json"


def draft_staging_dir(keys: dict[str, str] | None) -> Path | None:
    """This chat's own folder for drafts made for all of VOOL, or None when the turn names no chat."""
    from core.skill_scopes import _safe_dir

    chat = str((keys or {}).get("chat") or "").strip()
    if not chat:
        return None
    return staging_root() / DRAFTS_DIRNAME / _safe_dir(chat)


def drafted_mark(skill_dir: Path) -> dict[str, Any] | None:
    """What ``skill.create`` recorded beside its draft (the digest it wrote), or None."""
    mark = Path(skill_dir) / DRAFTED_MARK
    if not mark.is_file():
        return None
    try:
        data = json.loads(mark.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def imported_mark(skill_dir: Path) -> dict[str, Any] | None:
    """What an import recorded beside its draft (the digest of the bytes it staged), or None."""
    mark = Path(skill_dir) / IMPORTED_MARK
    if not mark.is_file():
        return None
    try:
        data = json.loads(mark.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def reserved_skill_names() -> frozenset[str]:
    """Slugs no skill from outside VOOL may take: every built-in skill's folder, id and name.

    A skill named like a built-in could not be removed from chat (the remove lane answers "comes
    with VOOL"), and the on/off setting is keyed by the bare name, so turning the copy off turned
    the built-in off too (red team t9). Read from the shipped library itself, never a list kept here.
    """
    from core.native_skill_library import native_skills_root
    from core.skill_frontmatter import FrontmatterRefused, parse

    names: set[str] = set()
    root = native_skills_root()
    with contextlib.suppress(OSError):
        for skill_md in sorted(root.glob("*/SKILL.md")):
            names.add(_slug(skill_md.parent.name))
            try:
                parsed = parse(skill_md.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, FrontmatterRefused):
                continue
            header = parsed[0] if parsed else {}
            for key in ("id", "name"):
                value = header.get(key)
                if isinstance(value, str) and _slug(value):
                    names.add(_slug(value))
    names.discard("")
    return frozenset(names)


#: A skill's name, as a person writes it: letters of any alphabet (accents included), digits,
#: spaces, hyphens, underscores and apostrophes ("Mum's recipes", "Café notes"), at most 64.
#: No line breaks, double quotes or brackets: the name is shown on screen and in the turn's skill
#: header, and a 240-character name with line breaks forged text outside its block (red team t4).
#: The slug (folder and id) is derived from it and stays strict: lowercase a-z, 0-9 and hyphens.
MAX_NAME_CHARS = 64
_NAME_RE = re.compile(r"[^\W_][\w '\u2019\-]{0,%d}" % (MAX_NAME_CHARS - 1))


def name_problem(name: Any) -> str:
    """The plain reason this skill name cannot be used, or "" when it is fine."""
    text = name if isinstance(name, str) else ""
    if not text.strip():
        return "The skill needs a name at the top of the file (name: ...)."
    if len(text.strip()) > MAX_NAME_CHARS or not _NAME_RE.fullmatch(text.strip()) or len(_slug(text)) > MAX_NAME_CHARS:
        return (f"The skill's name must be one short line of at most {MAX_NAME_CHARS} letters, digits, spaces, "
                "hyphens or apostrophes. Shorten it and try again.")
    return ""


#: Header keys that make a skill a typed VOOL contract (seated by task class, given a doctrine
#: slot). Only VOOL's own, bundled or signed skills may carry them; on an import they would turn
#: someone else's words into a skill that runs on every turn of those kinds (red team t2, t7).
TYPED_KEYS = ("id", "task-families", "task_families", "type", "priority", "incompatible-with", "incompatible_with")


def typed_keys_problem(header: dict[str, Any]) -> str:
    found = [key for key in TYPED_KEYS if key in (header or {})]
    if not found:
        return ""
    return ("This skill sets " + ", ".join(found) + ", which only VOOL's own skills may set. "
            "Take those lines out of the top of the file and try again.")


def reserved_name_reason(name: str) -> str:
    """The plain refusal when ``name`` is a built-in's name (or uses VOOL's own prefix), else ""."""
    slug = _slug(name)
    if not slug:
        return ""
    reserved = reserved_skill_names()
    # A name written in look-alike letters of another alphabet ("Меdіа studio" all in Cyrillic) is
    # read as the Latin name it imitates too, so a short name made from other scripts cannot squat a
    # built-in's (audit M6).
    from core.registry.checks import _LOOKALIKE

    imitated = _slug(str(name or "").translate(_LOOKALIKE))
    if (imitated in reserved or imitated.startswith("vool-")) and not (slug in reserved or slug.startswith("vool-")):
        slug = imitated
    if slug in reserved:
        shown = builtin_display(slug)
        return (
            f'The name "{str(name).strip()}" is taken by one of VOOL\'s own skills ("{shown}"), so this skill needs '
            f'another name. Pick a different one, for example "My {shown.lower()}", and try again.'
        )
    if slug.startswith("vool-"):
        rest = slug[len("vool-"):].replace("-", " ").strip() or "skill"
        return (
            'Names that start with "vool-" are kept for VOOL\'s own skills, so this skill needs another name. '
            f'Pick a different one, for example "My {rest}", and try again.'
        )
    return ""


def builtin_display(slug: str) -> str:
    """A built-in's plain name ("Answer presentation") from any of its slugs."""
    plain = slug[len("vool-"):] if slug.startswith("vool-") else slug
    return plain.replace("-", " ").strip().capitalize() or "VOOL"


def builtin_not_here(slug: str) -> str:
    """The plain answer when a bare name means a built-in, which has no draft or installable copy."""
    shown = builtin_display(slug)
    return (f'"{shown}" is one of VOOL\'s own skills. It is part of VOOL already, so there is nothing here to '
            "check or install: turn it on or off in Skills, under My add-ons.")


def skill_path_allowed(path: Path, keys: dict[str, str] | None = None) -> bool:
    """Whether a SKILL.md path sits in one of VOOL's skill folders this turn may read.

    ``skill.validate`` and ``skill.install`` take a path from the model. Without this they read any
    file on the machine that opened with a ``---`` block and handed its words back (red team t17:
    a private note in the VOOL home), past the file-reading gate that refuses the same path. The
    folders: drafts, this chat's imports, installed packs, the built-in library, and this chat's and
    this project's own skills. Symlinks are resolved first, so a link cannot point out of them.
    """
    from core.native_skill_library import native_skills_root
    from core.skill_scopes import scope_root

    candidate = Path(path)
    if candidate.name != "SKILL.md":
        return False
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError):
        return False
    roots = [staging_root(), plugins_root() / "plugins", native_skills_root()]
    own_folders = {IMPORTS_DIRNAME: import_staging_dir(keys), DRAFTS_DIRNAME: draft_staging_dir(keys)}
    for scope in ("chat", "project"):
        root = scope_root(scope, keys)
        if root is not None:
            roots.append(root)
    for root in roots:
        with contextlib.suppress(OSError, RuntimeError, ValueError):
            if resolved.is_relative_to(root.resolve()):
                # Another chat's imports and drafts live under the drafts folder too; only this chat's count.
                inside = resolved.relative_to(staging_root().resolve()).parts if root == staging_root() else ()
                if inside and inside[0] in own_folders:
                    own = own_folders[inside[0]]
                    if own is None or not resolved.is_relative_to(own.resolve()):
                        continue
                return True
    return False


def _is_native_path(path: Path) -> bool:
    from core.native_skill_library import native_skills_root

    try:
        return Path(path).resolve().is_relative_to(native_skills_root().resolve())
    except (OSError, RuntimeError):
        return False


SAFETY_LEAD = "This skill did not pass VOOL's safety check, so it was not kept. "


def content_problem(markdown: str) -> str:
    """The plain safety-check refusal for one SKILL.md text, or "" when it passes.

    The Skill Codex's own text checks (core.registry.checks.skill_text_findings), run on every way
    a skill comes in, so a zip, a chat import, a drafted skill and an install are never weaker than
    a published one. A header that cannot be read safely is refused here too.
    """
    from core.registry.checks import plain_findings, skill_text_findings
    from core.skill_frontmatter import FrontmatterRefused, parse

    try:
        parsed = parse(markdown)
    except FrontmatterRefused as refused:
        return refused.message
    findings = skill_text_findings(markdown, parsed[0] if parsed else None)
    if not findings:
        return ""
    return SAFETY_LEAD + plain_findings(findings)


_UNKNOWN_TOOLS = "allowed-tools names tools this runtime does not have: "


def plain_problem(problem: str) -> str:
    """One validator finding as a person reads it: what is wrong, why it matters, how to fix it.

    The validator's own words ("allowed-tools names tools this runtime does not have: ...") stay in
    a result's ``problems`` for tools and the publishing checks; a reply to the person uses these.
    """
    text = str(problem or "").strip()
    if text.startswith(SAFETY_LEAD) or not text:
        return text
    if text.startswith(_UNKNOWN_TOOLS):
        names = text[len(_UNKNOWN_TOOLS):].strip()
        return (f"It asks for tools VOOL does not have: {names}. Check each name in its allowed-tools line "
                "against the tool list in VOOL's guide \"Skill tools and limits\" (for example "
                "workspace.write_file), or take it out, and try again.")
    if text.startswith("description is empty"):
        return ("It has no description, so VOOL could never tell when to use it. Add a line "
                "\"description: ...\" at the top saying when the skill applies, and try again.")
    if text.startswith("body is empty"):
        return "It has no instructions. Write its steps below the header, and try again."
    if text == "name is empty":
        return "It has no name. Add a line \"name: ...\" at the top, and try again."
    if text.startswith("body exceeds"):
        return (f"Its instructions are longer than {_MAX_BODY_CHARS:,} characters, so part of them would be "
                "lost. Shorten them, and try again.")
    if text.startswith("no YAML frontmatter"):
        return ("It does not start with the header VOOL needs: a block between two --- lines with its name "
                "and description. Add one, and try again.")
    if text.startswith("tool contracts are unavailable"):
        return "VOOL could not check the tools it asks for just now. Try again in a moment."
    if text.startswith("declared permissions"):
        return ("Its permissions line does not cover what the tools it asks for need (" + text + "). "
                "Add the missing ones or take the permissions line out, and try again.")
    return text


def _yaml_scalar(value: str) -> str:
    """Quote a frontmatter value so a colon or a hash in a description cannot break the header."""

    text = str(value or "").replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ").strip()
    return f'"{text}"'


def _yaml_list(values: tuple[str, ...] | list[str]) -> str:
    items = [str(v).strip() for v in (values or ()) if str(v).strip()]
    return "[" + ", ".join(_yaml_scalar(item) for item in items) + "]"


def render_skill_markdown(
    *,
    name: str,
    description: str,
    body: str,
    allowed_tools: tuple[str, ...] | list[str] = (),
    triggers: tuple[str, ...] | list[str] = (),
) -> str:
    """The exact `SKILL.md` shape the loader already reads, produced rather than hand-written."""

    header = [
        "---",
        f"name: {_yaml_scalar(name)}",
        f"description: {_yaml_scalar(description)}",
    ]
    if allowed_tools:
        header.append(f"allowed-tools: {_yaml_list(allowed_tools)}")
    if triggers:
        header.append(f"triggers: {_yaml_list(triggers)}")
    header.append("---")
    return "\n".join(header) + "\n\n" + str(body or "").strip()[:_MAX_BODY_CHARS] + "\n"


def create_skill(
    *,
    name: str,
    description: str,
    body: str,
    allowed_tools: tuple[str, ...] | list[str] = (),
    triggers: tuple[str, ...] | list[str] = (),
    overwrite: bool = False,
    scope: str = "global",
    keys: dict[str, str] | None = None,
    made_by_person: bool = True,
) -> dict[str, Any]:
    """Write a staged `SKILL.md`. Loads nothing and changes no behaviour.

    ``made_by_person`` says whether the person themselves asked for these bytes (a direct caller in
    VOOL's own code). The ``skill.create`` tool passes False: a model can call it in a turn that is
    reading a web page or someone else's skill, so its drafts are noted as drafted, not as the
    person's own (audit H1).

    ``scope`` picks the staging area: ``global`` drafts where it always has, ``chat`` and
    ``project`` draft inside that chat's or project's own skills folder (``keys`` names which),
    so a draft is installed later into the scope it was written for.
    """

    slug = _slug(name)
    if not slug:
        return {"status": "invalid_arguments", "reason": "name is required"}
    scope = str(scope or "global").strip().lower()
    if scope not in ("chat", "project", "global"):
        return {"status": "invalid_arguments", "reason": f"unknown scope {scope!r}; use chat, project or global"}
    if not str(description or "").strip():
        # Not pedantry: `match_corpus` is name + description + triggers, so a skill without one is a
        # skill that can never rank, and writing it would be writing something inert.
        return {
            "status": "invalid_arguments",
            "reason": "description is required - it is what decides when the skill applies",
        }
    if not str(body or "").strip():
        return {"status": "invalid_arguments", "reason": "body is required - it is the instructions"}
    named = name_problem(name)
    if named:
        return {"status": "invalid_arguments", "reason": named}
    reserved = reserved_name_reason(name)
    if reserved:
        return {"status": "refused", "reason": reserved, "problems": [reserved]}
    rendered = render_skill_markdown(
        name=name, description=description, body=body,
        allowed_tools=allowed_tools, triggers=triggers,
    ).encode("utf-8")
    # The same safety check every imported skill passes, before anything is written: a draft that
    # could never be turned on is not saved, and the person reads why in plain words.
    problem = content_problem(rendered.decode("utf-8"))
    if problem:
        return {"status": "refused", "reason": problem, "problems": [problem]}

    if scope == "global":
        own = draft_staging_dir(keys)
        directory = (own if own is not None else staging_root()) / slug
    else:
        from core.skill_scopes import scope_unavailable_reason

        dirs = _scoped_dirs(scope, keys)
        if dirs is None:
            return {
                "status": "scope_unavailable",
                "reason": scope_unavailable_reason(scope, keys) or f"no {scope} to keep the skill in",
                "scope": scope,
            }
        directory = dirs[0] / slug
    target = directory / "SKILL.md"
    if target.exists() and not overwrite:
        return {
            "status": "exists",
            "reason": f"{target} already exists; pass overwrite to replace it",
            "path": str(target),
        }
    digest = hashlib.sha256(rendered).hexdigest()
    try:
        directory.mkdir(parents=True, exist_ok=True)
        target.write_bytes(rendered)
        # The digest of what was written (and shown) stays beside the draft, so turning it on later,
        # after a restart included, installs these bytes or nothing (audit M7).
        (directory / DRAFTED_MARK).write_text(json.dumps({"sha256": digest, "scope": scope}) + "\n", encoding="utf-8")
    except OSError as exc:
        return {"status": "error", "reason": f"{type(exc).__name__}: could not write the skill"}
    # Whether the PERSON made these bytes is decided by the caller: the skill.create tool notes them
    # as drafted, and the chat lane answering their own "make me a skill that ..." notes them as theirs.
    with contextlib.suppress(Exception):
        from core.skill_origin import note_created_here, note_drafted

        (note_created_here if made_by_person else note_drafted)(digest, slug=slug)
    return {
        "status": "ok",
        "path": str(target),
        "slug": slug,
        "scope": scope,
        # The digest of the exact bytes drafted: an activation can bind to it, so what goes live
        # is what the operator was shown, not whatever sits at this path by then.
        "sha256": hashlib.sha256(rendered).hexdigest(),
        "staged": True,
        "next": "validate it, then install it to make it active",
    }


def resolve_skill_markdown(path: str, keys: dict[str, str] | None = None) -> Path:
    """The `SKILL.md` this identifier means: a file, a directory, or a skill NAME.

    Found by a QA drive on 2026-07-30. ``create`` returns ``slug: "qa-echo"`` and signs off with
    "validate it, then install it" -- and ``validate("qa-echo")`` answered "no SKILL.md at qa-echo",
    because the name was resolved against the process working directory like any relative path. The
    three steps are separate calls precisely so a model makes them one at a time, so the identifier
    the first step hands back has to be one the second step accepts. Staging is searched before the
    installed tree, because a freshly drafted skill is the one being validated.
    """

    raw = str(path or "").strip().strip("`\"'")
    if not raw:
        return Path("")
    candidate = Path(raw).expanduser()
    if candidate.is_file():
        return candidate
    if candidate.is_dir():
        return candidate / "SKILL.md"
    # A bare name, not a path. "qa-echo", "qa echo" and "QA Echo" all mean the same staged skill.
    if len(candidate.parts) == 1 and not raw.startswith(("/", "~", ".")):
        slug = _slug(raw)
        if slug:
            for found in _skill_locations(slug, keys):
                if found.is_file():
                    return found
            # Nothing on disk: name the staging path, so the "not found" says where it would go.
            return staging_root() / slug / "SKILL.md"
    return candidate


def _skill_locations(slug: str, keys: dict[str, str] | None = None) -> list[Path]:
    """Where a skill of this slug could live, IN SEARCH ORDER: staging, then installed.

    Order is behaviour, not tidiness. A redraft of an already-installed skill is the copy the
    operator is validating; resolving to the installed one instead reports on the previous version
    and calls it current. VOOL's built-in library is not searched: a bare name used to resolve to
    the native folder, and "turn on vool-feature-build" copied a developer skill into the person's
    own skills, out of the native lane's audience and trust rules (red team t5). Built-ins are
    turned on and off where they live.

    With scope keys, this chat's skills (draft, then active) come first, then this project's,
    then everything above: the narrowest scope is the one the operator is working in.
    """

    locations: list[Path] = []
    for scope in ("chat", "project"):
        dirs = _scoped_dirs(scope, keys)
        if dirs is not None:
            locations.extend([dirs[0] / slug / "SKILL.md", dirs[1] / slug / "SKILL.md"])
    imports = import_staging_dir(keys)
    if imports is not None:
        locations.append(imports / slug / "SKILL.md")
    drafts = draft_staging_dir(keys)
    if drafts is not None:
        locations.append(drafts / slug / "SKILL.md")
    locations.append(staging_root() / slug / "SKILL.md")
    # Installed skills sit at plugins/<plugin_id>/skills/<slug>/SKILL.md.
    with contextlib.suppress(OSError):
        locations.extend(sorted((plugins_root() / "plugins").glob(f"*/skills/{slug}/SKILL.md")))
    return locations


def validate_skill(
    path: str,
    keys: dict[str, str] | None = None,
    *,
    candidate_contracts: tuple = (),
    content_checks: bool | None = None,
) -> dict[str, Any]:
    """Parse with the REAL loader and report what would stop this skill working.

    "It parsed" is the easy half and the less useful one. A skill with no description never ranks,
    and a skill naming a tool that does not exist will narrow the model to nothing on the turn it
    matches — both parse perfectly.

    ``content_checks`` runs the Skill Codex's text checks (``content_problem``). Left as None they
    run on everything except VOOL's own built-in library, which ships reviewed with the app; a
    caller that has already run the full Codex suite on these bytes passes False.
    """

    candidate = resolve_skill_markdown(path, keys)
    if not candidate.is_file():
        bare = _slug(str(path or "").strip().strip("`\"'"))
        if bare and bare in reserved_skill_names() and not any(ch in str(path) for ch in "/~\\"):
            return {"status": "not_found", "reason": builtin_not_here(bare), "path": ""}
        return {"status": "not_found", "reason": f"no SKILL.md at {candidate}", "path": str(candidate)}

    try:
        with candidate.open("rb") as source:
            raw = source.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise ValueError("skill exceeds the validation byte limit")
        preview_markdown = raw.decode("utf-8")
    except (OSError, UnicodeError, ValueError):
        return {"status": "invalid", "path": str(candidate),
                "problems": ["the complete skill could not be read within the validation limit"]}
    from core.skill_frontmatter import FrontmatterRefused, parse

    # The header is read once, bounded, before anything else looks at it (core.skill_frontmatter).
    try:
        parsed_header = parse(preview_markdown)
    except FrontmatterRefused as refused:
        return {"status": "invalid", "path": str(candidate), "problems": [refused.message],
                "sha256": hashlib.sha256(raw).hexdigest()}
    skill = parse_skill_content(preview_markdown, path=candidate) if parsed_header is not None else None
    if skill is None:
        return {
            "status": "invalid",
            "path": str(candidate),
            "problems": [
                "no YAML frontmatter - a skill must open with a --- block naming it and describing "
                "when it applies"
            ],
        }
    header, full_body = parsed_header[0], parsed_header[1].strip()

    problems: list[str] = []
    from core.registry.rules import analyze

    boundaries = analyze({"SKILL.md": raw})
    if content_checks is None:
        content_checks = not _is_native_path(candidate)
    if content_checks:
        problem = content_problem(preview_markdown)
        if problem:
            problems.append(problem)
    else:
        problems.extend(conflict["reason"] for conflict in boundaries["conflicts"])
    if len(full_body) > _MAX_BODY_CHARS:
        problems.append(f"body exceeds {_MAX_BODY_CHARS} characters; the loader would truncate instructions")
    if not skill.name.strip():
        problems.append("name is empty")
    elif "name" in header:
        named = name_problem(header.get("name"))
        if named:
            problems.append(named)
    if not skill.description.strip():
        problems.append(
            "description is empty - it is most of what decides when the skill applies, so an empty "
            "one means the skill can never rank"
        )
    if not skill.body.strip():
        problems.append("body is empty - there are no instructions to inject")

    unknown_tools: list[str] = []
    required_actions: dict[str, tuple[str, ...]] = {}
    if skill.allowed_tools:
        try:
            from core.runtime_tool_contracts import runtime_tool_contract_map
            from core.tool_registry import registered_tools

            # Plugin tools are dynamic: the canonical registry is their authority. A
            # conversational skill naming a registered pack tool must validate against the
            # same registry that dispatches it — the builtin map alone refused every
            # plugin-named skill (the PB04 gap recorded at the DB source lane), so the only
            # conversational path was a skill with no allowed-tools at all. Registry
            # entries never override a builtin contract of the same name.
            contracts = runtime_tool_contract_map()
            for contract in candidate_contracts:
                if str(contract.source or "").startswith("plugin:"):
                    contracts.setdefault(str(contract.intent), contract)
            for contract in registered_tools():
                if str(contract.source or "").startswith("plugin:"):
                    contracts.setdefault(str(contract.intent), contract)
            known = set(contracts)
            unknown_tools = [t for t in skill.allowed_tools if t not in known]
            for tool in skill.allowed_tools:
                actions = tuple(getattr(contracts.get(tool), "permission_actions", ()) or ())
                if actions:
                    required_actions[tool] = actions
        except Exception:
            problems.append("tool contracts are unavailable; allowed-tools could not be validated")
        if unknown_tools:
            problems.append(
                "allowed-tools names tools this runtime does not have: " + ", ".join(unknown_tools)
            )

    # The permission-consistency law: a package that DECLARES permissions is contractually claiming
    # what its tools need, so an under-declaration is a permission lie in print. Enforced only when
    # permissions are declared at all — packages predating the contract declare none, and the seam
    # that surfaces the contract must not retroactively invalidate them.
    if skill.permissions and required_actions:
        declared = set(skill.permissions)
        for tool, actions in sorted(required_actions.items()):
            missing = set(actions) - declared
            if missing:
                problems.append(
                    f"declared permissions {sorted(declared)} do not cover what {tool} requires: "
                    + ", ".join(sorted(missing))
                )

    # The activation preview: the file's EXACT bytes, so the operator approves what will be
    # loaded rather than a paraphrase of it. These are the bytes read once above and validated.
    preview_bytes = raw
    return {
        "status": "ok" if not problems else "invalid",
        "path": str(candidate),
        "sha256": hashlib.sha256(preview_bytes).hexdigest() if preview_bytes else "",
        "name": skill.name,
        "description": skill.description,
        "body_characters": len(skill.body),
        "full_body_characters": len(full_body),
        "authority_notice": boundaries["authority_notice"],
        "native_control_overlaps": boundaries["native_control_overlaps"],
        "allowed_tools": list(skill.allowed_tools),
        "unknown_tools": unknown_tools,
        "triggers": list(skill.triggers),
        "declared_permissions": list(skill.permissions),
        "preview_markdown": preview_markdown,
        # Every header key, so a preview can name each one (core.agent_runtime.fast_paths_skill).
        "frontmatter_keys": sorted(str(key) for key in header),
        "problems": problems,
    }



def _ensure_plugin_manifest(plugin_dir: Path, plugin_id: str) -> None:
    """Give the destination pack the manifest the catalog requires to see it at all.

    `core/plugin_catalog.py` returns None for any plugin directory without
    `.codex-plugin/plugin.json`, so the default install target (`local-skills`) was invisible:
    every skill the agent authored was written correctly, listed nowhere, and never ranked.
    Measured 2026-08-29 — three plugin directories on disk, `/api/plugins` reported two.

    Only ever CREATES a missing manifest; an existing one is left exactly as the operator wrote it.
    """
    manifest_path = plugin_dir / ".codex-plugin" / "plugin.json"
    if manifest_path.exists():
        return
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "name": plugin_id,
        "version": "0.1.0",
        "description": f"Skills installed locally into {plugin_id}.",
        "skills": "./skills/",
        "interface": {
            "displayName": plugin_id.replace("-", " ").title(),
            "shortDescription": f"Locally installed skills ({plugin_id}).",
        },
    }
    manifest_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    # A new pack directory exists: the catalog's bounded listing must see it on the next read
    # rather than after its age-out (core.plugin_catalog keeps the listing, not this writer).
    try:
        from core.plugin_catalog import invalidate_storage_listing

        invalidate_storage_listing()
    except Exception:
        pass


def _clear_staged_drafts(slug: str, keys: dict[str, str] | None) -> tuple[list[str], str]:
    """Remove every staged draft of one just-installed skill: ``(paths removed, error or "")``.

    Install COPIES the draft into the active tree and historically left the original in staging,
    so the one skill was listed twice — staged ("drafted, not active") and installed ("active") —
    and, because staging is deliberately searched before the installed tree, every later name
    lookup kept answering with the leftover draft instead of the live copy (measured 2026-10-28:
    draft a skill, turn it on, ask which skills exist — the same skill named twice). The fix
    belongs to the install, not the listing: the moment a draft becomes active it stops being a
    draft, so the install retires it.

    Every staging home of the slug is cleared — the global staging tree always, this chat's and
    this project's staged folders when their keys are present — because a copy surviving in any
    one of them keeps the duplicate alive. Only the exact ``<slug>`` directory inside a staging
    root is removed: the slug goes through ``_slug``, which cannot emit a path separator, so the
    deletion cannot be steered to any folder but that one. A draft of a DIFFERENT skill is never
    touched.

    An unremovable leftover is reported, never raised: the skill is already active, and a stale
    draft is a wrong listing, not a failed activation. Callers run this only AFTER the copy and
    the version record have succeeded, so a failed install never costs the operator their draft.
    """
    clean = _slug(slug)
    if not clean:
        return [], ""
    candidates = [staging_root() / clean]
    for own in (import_staging_dir(keys), draft_staging_dir(keys)):
        if own is not None:
            candidates.append(own / clean)
    for scope in ("chat", "project"):
        dirs = _scoped_dirs(scope, keys)
        if dirs is not None:
            candidates.append(dirs[0] / clean)
    removed: list[str] = []
    error = ""
    for candidate in candidates:
        # rmtree, not unlink: a staged draft is its own folder (SKILL.md, maybe imported.json
        # beside it), and an emptied folder would still read as the draft's home on the next
        # name lookup.
        try:
            if candidate.is_dir():
                shutil.rmtree(candidate)
                removed.append(str(candidate))
        except OSError as exc:
            if not error:
                error = f"{type(exc).__name__}: could not remove the staged draft"
    return removed, error


def install_skill(
    path: str,
    *,
    plugin_id: str = "local-skills",
    overwrite: bool = False,
    scope: str = "global",
    keys: dict[str, str] | None = None,
    expected_sha256: str = "",
    origin: str = "install",
) -> dict[str, Any]:
    """Move a validated skill into the active tree of its scope, where the loader will find it.

    Re-validates rather than trusting that `validate` was called: they are separate tool calls and a
    model can skip one. Installing something that can never rank is worse than refusing, because the
    operator is then told a capability exists that will never fire.

    ``scope`` ``global`` installs into the plugins tree exactly as before; ``chat`` and ``project``
    install into that chat's or project's own skills folder, which only turns in that chat (or in
    chats bound to that project) load. ``expected_sha256`` binds the activation to the bytes the
    operator was shown: when the draft at that path no longer has that digest, nothing is installed.

    A successful install also retires the draft it came from: the staged copies of the same skill
    are removed from every staging area (``_clear_staged_drafts``), so the skill exists once
    instead of being listed as both a draft and installed. The copy and the version record land
    BEFORE anything is removed, so a failed install never loses the draft, and a removal that
    fails leaves the install active and says so in the result.
    """

    scope = str(scope or "global").strip().lower()
    if scope not in ("chat", "project", "global"):
        return {"status": "invalid_arguments", "reason": f"unknown scope {scope!r}; use chat, project or global"}
    scoped_dirs = None
    if scope != "global":
        from core.skill_scopes import scope_unavailable_reason

        scoped_dirs = _scoped_dirs(scope, keys)
        if scoped_dirs is None:
            return {
                "status": "scope_unavailable",
                "reason": scope_unavailable_reason(scope, keys) or f"no {scope} to install the skill into",
                "scope": scope,
            }

    source = resolve_skill_markdown(path, keys)
    expected = str(expected_sha256 or "").strip().lower()
    recorded_import = imported_mark(source.parent) if source.is_file() else None
    if recorded_import is not None:
        # Someone else's file is turned on only as the exact bytes the person was shown: the
        # digest from that showing must come with the request, and must still be the digest the
        # import recorded when it staged the file. Without it, a draft swapped after the review
        # (or a restart that lost the record of what was shown) went live unseen (red team t8).
        staged_digest = str(recorded_import.get("sha256") or "").strip().lower()
        if not expected:
            return {
                "status": "refused",
                "reason": "This skill came from a file, so I can only turn it on as the exact version you "
                          "were shown, and I no longer know which version that was. Ask me to show it "
                          "again, then say turn it on.",
                "path": str(source),
                "problems": ["an imported skill needs the digest of the version that was shown"],
            }
        if expected != staged_digest:
            return {
                "status": "refused",
                "reason": "The skill file changed after I showed it to you, so I did not turn it on. "
                          "Import it again to see the new version.",
                "path": str(source),
                "problems": ["draft digest differs from the one that was reviewed"],
            }
    drafted = drafted_mark(source.parent) if source.is_file() and recorded_import is None else None
    if drafted and not expected:
        # A draft skill.create wrote turns on as the bytes it wrote (and showed), or not at all: the
        # recorded digest stands in for the one the caller did not send (audit M7).
        expected = str(drafted.get("sha256") or "").strip().lower()
        if expected:
            try:
                now = hashlib.sha256(source.read_bytes()).hexdigest()
            except OSError:
                now = ""
            if now != expected:
                return {
                    "status": "refused",
                    "reason": "This draft changed after it was made, so I did not turn it on: it is no longer "
                              "the version that was shown. Ask me to make or show it again.",
                    "path": str(source),
                    "problems": ["draft digest differs from the one skill.create recorded"],
                }
    if expected and source.is_file():
        try:
            actual = hashlib.sha256(source.read_bytes()).hexdigest()
        except OSError:
            actual = ""
        if actual != expected:
            return {
                "status": "refused",
                "reason": "The skill file changed after I showed it to you, so I did not turn it on. "
                          "Ask me to show it again.",
                "path": str(source),
                "problems": ["draft digest differs from the one that was reviewed"],
            }
    if source.is_file() and _is_native_path(source):
        return {
            "status": "refused",
            "reason": builtin_not_here(_slug(source.parent.name)),
            "path": "",
            "problems": ["built-in skills have no installable copy"],
        }
    verdict = validate_skill(str(source))
    # A skill that is not THERE has not failed validation. Saying "the skill does not validate" about
    # a missing file names the wrong problem and sends the reader to fix the skill's contents.
    if verdict.get("status") == "not_found":
        bare = _slug(str(path or "").strip().strip("`\"'"))
        if bare and bare in reserved_skill_names() and not any(ch in str(path) for ch in "/~\\"):
            plain = builtin_not_here(bare)  # a built-in by name: it has no copy to install
            return {"status": "not_found", "reason": plain, "path": "", "problems": [plain]}
        return {
            "status": "not_found",
            "reason": f"no SKILL.md at {source} - create or stage it first",
            "path": str(source),
            "problems": [verdict.get("reason") or "not found"],
        }
    if verdict.get("status") != "ok":
        problems = [str(item).strip() for item in (verdict.get("problems") or []) if str(item).strip()]
        problems = problems or [str(verdict.get("reason") or "").strip() or "the file could not be read"]
        # The refusal carries the validator's own findings, so the author reads WHAT to fix
        # (an unknown tool name, an empty description) in the same line that says it was refused.
        if len(problems) == 1 and problems[0].startswith(SAFETY_LEAD):
            # The safety check already says what was refused, on which line, why and what to write.
            reason = ("This skill did not pass VOOL's safety check, so it was not installed. "
                      + problems[0][len(SAFETY_LEAD):])
        else:
            reason = "This skill was not installed because it needs fixing first. " + " ".join(
                plain_problem(item) for item in problems)
        return {
            "status": "refused",
            "reason": reason,
            "path": str(source),
            "problems": problems,
        }

    slug = _slug(str(verdict.get("name") or source.parent.name))
    # Built-in names stay the built-ins': a copy under one could not be removed from chat and shared
    # their on/off switch. That holds for a built-in's own folder too, so it is never copied out of
    # the library into the person's skills.
    reserved = reserved_name_reason(slug)
    if reserved:
        return {"status": "refused", "reason": reserved, "path": str(source), "problems": [reserved]}
    clean_plugin = _slug(plugin_id) or "local-skills"
    if scoped_dirs is None:
        refused_pack = pack_name_problem(clean_plugin, installing_into=True)
        if refused_pack:
            return {"status": "refused", "reason": refused_pack, "path": str(source), "problems": [refused_pack]}
    if scoped_dirs is not None:
        destination_dir = scoped_dirs[1] / slug
    else:
        destination_dir = plugins_root() / "plugins" / clean_plugin / "skills" / slug
    destination = destination_dir / "SKILL.md"
    with _ACTIVATION_LOCK:
        if destination.exists() and not overwrite:
            return {
                "status": "exists",
                "reason": f"a skill is already installed at {destination}; pass overwrite to replace it",
                "path": str(destination),
            }
        # IDENTITY-COLLISION GATE (recovered skill-identity law). The effective
        # digest names the INSTRUCTIONS a skill carries, independent of packaging.
        # Another installed skill with the same effective digest means this install
        # would put one instruction set behind two names — a rename the operator
        # cannot see, and the classic smuggling shape (repackage a refused skill
        # under a fresh slug). Refused typed, with the collision named.
        try:
            incoming_effective = _instruction_digest_for_bytes(Path(source).read_bytes())
            installed = _installed_effective_digests(slug)
            # This chat's and this project's skills are installed skills too: the same
            # instructions behind a chat name and a global name are still one set behind two.
            installed.update(_scoped_effective_digests(keys, exclude_dir=destination_dir))
            collisions = {
                other: digest
                for other, digest in installed.items()
                if digest and digest == incoming_effective
            }
        except OSError:
            collisions = {}
        if collisions:
            other = sorted(collisions)[0]
            shown = other.split(":", 1)[-1].replace("-", " ").strip()
            shown = shown[:1].upper() + shown[1:]
            return {
                "status": "refused",
                "reason": (
                    f'This skill has the same instructions as your skill "{shown}", which is already on, so I '
                    "did not add it a second time under another name. Keep using that one, or change the "
                    "instructions and try again."
                ),
                "path": str(source),
                "collision_with": other,
                "effective_sha256": incoming_effective,
                "problems": [f"identity collision: effective digest equals installed skill {other!r}"],
            }
        try:
            destination_dir.mkdir(parents=True, exist_ok=True)
            # The copy lands beside the destination first and its digest is checked THERE: the
            # bytes verified are the bytes that go live, so a rewrite of the draft between the
            # check and the copy cannot slip through.
            tmp = destination.with_name(destination.name + ".tmp")
            shutil.copyfile(source, tmp)
            data = tmp.read_bytes()
            if expected and hashlib.sha256(data).hexdigest() != expected:
                with contextlib.suppress(OSError):
                    tmp.unlink()
                return {
                    "status": "refused",
                    "reason": "The skill file changed after I showed it to you, so I did not turn it on. "
                          "Ask me to show it again.",
                    "path": str(source),
                    "problems": ["draft digest differs from the one that was reviewed"],
                }
            tmp.replace(destination)
            if scoped_dirs is None:
                _ensure_plugin_manifest(plugins_root() / "plugins" / clean_plugin, clean_plugin)
            else:
                _ensure_scope_record(scoped_dirs[1].parent, scope, keys)
        except OSError as exc:
            return {"status": "error", "reason": f"{type(exc).__name__}: could not install the skill"}

        # Every activation is a version: the bytes just installed are snapshotted and recorded, so
        # the next edit of this skill can be rolled back to exactly this state. The record is
        # verified BY READING IT BACK — an install whose version did not persist is an error, not
        # an unversioned success (an activation the history cannot account for is unauditable).
        if (source.parent / IMPORTED_MARK).is_file():
            origin = "import"
        try:
            recorded = _record_version(destination_dir, source=str(origin or "install"))
            persisted = _read_history(destination_dir)
            head = int(persisted.get("head_version") or 0)
            if head != int(recorded.get("version") or 0):
                raise OSError("version history did not persist")
        except OSError as exc:
            return {
                "status": "error",
                "reason": f"{type(exc).__name__}: installed but could not record the version",
                "path": str(destination),
            }

        # Where it came from, recorded by the install itself so every caller is covered: only
        # bytes skill.create wrote count as the person's own (core.skill_origin).
        try:
            from core.skill_origin import decide_install_origin, record_install

            installed_digest = hashlib.sha256(data).hexdigest()
            record_install(destination, sha256=installed_digest, origin=decide_install_origin(
                installed_digest, history_source="import" if origin == "import" else ""))
        except Exception:  # noqa: BLE001 - a missing record reads as "not the person's own" (fails closed)
            pass
        result: dict[str, Any] = {
            "status": "ok",
            "path": str(destination),
            "plugin_id": clean_plugin if scoped_dirs is None else f"{scope}-skills",
            "scope": scope,
            "name": verdict.get("name"),
            "slug": slug,
            "sha256": hashlib.sha256(data).hexdigest(),
            "version": recorded["version"],
            "active": True,
            "note": "the loader picks this up on the next turn; no restart is needed",
        }
        # The duplicate listing this fixes: install copied the draft into the active tree and left
        # the staged original behind, so one skill showed up as both a draft and installed. Cleared
        # only here, on the success path — a refused, existing or failed install keeps the draft.
        result["cleared_staged_drafts"], clear_error = _clear_staged_drafts(slug, keys)
        if clear_error:
            result["staged_clear_error"] = clear_error
        return result


def list_skills(workspace: str = "", keys: dict[str, str] | None = None) -> dict[str, Any]:
    """Every skill this runtime can see, grounded in the files that define them.

    Four places a skill lives, reported separately because they mean different things:
    the bound WORKSPACE (the operator's own project -- source of truth for what they are building),
    NATIVE (first-party packages the runtime itself ships), STAGED (drafted here, not active), and
    INSTALLED (active; the loader scans it every turn).

    Shipped 2026-07-31 because the live product answered "what exact skills do we have in our
    workspace folder?" with "I have no tool for listing them" while the bound workspace held ten
    SKILL.md files. A question the disk can answer deterministically must never be refused.
    """

    def _scan(root: Path, patterns: tuple[str, ...], *, limit: int = 200) -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = []
        seen: set[Path] = set()
        for pattern in patterns:
            for candidate in sorted(root.glob(pattern)):
                if candidate in seen or not candidate.is_file():
                    continue
                seen.add(candidate)
                skill = parse_skill(candidate)
                found.append({
                    "name": (skill.name if skill else candidate.parent.name) or candidate.parent.name,
                    "description": (skill.description if skill else "").strip(),
                    "path": str(candidate),
                    "parses": skill is not None,
                })
                if len(found) >= limit:
                    return found
        return found

    workspace_skills: list[dict[str, Any]] = []
    workspace_root = Path(str(workspace or "").strip()).expanduser()
    if str(workspace or "").strip() and workspace_root.is_dir():
        # Depth-bounded on purpose: SKILL.md is a convention with a shallow layout
        # (<root>/SKILL.md, <family>/<slug>/SKILL.md). An unbounded rglob walks node_modules.
        workspace_skills = _scan(
            workspace_root,
            ("SKILL.md", "*/SKILL.md", "*/*/SKILL.md", "*/*/*/SKILL.md"),
        )

    staged = [dict(row, scope="global") for row in _scan(staging_root(), ("*/SKILL.md",))]
    imports = import_staging_dir(keys)
    if imports is not None:
        for row in _scan(imports, ("*/SKILL.md",)):
            recorded = imported_mark(Path(row["path"]).parent) or {}
            staged.append(dict(row, scope=str(recorded.get("scope") or "global"), imported=True))
    drafts = draft_staging_dir(keys)
    if drafts is not None:
        staged.extend(dict(row, scope="global") for row in _scan(drafts, ("*/SKILL.md",)))
    installed = [dict(row, scope="global") for row in _scan(plugins_root() / "plugins", ("*/skills/*/SKILL.md",))]
    # This chat's and this project's own skills: active ones are reported per scope, drafts join
    # the staged list carrying the scope they will be installed into.
    scoped: dict[str, list[dict[str, Any]]] = {"chat": [], "project": []}
    for scope in ("chat", "project"):
        dirs = _scoped_dirs(scope, keys)
        if dirs is None:
            continue
        staged.extend(dict(row, scope=scope) for row in _scan(dirs[0], ("*/SKILL.md",)))
        scoped[scope] = [dict(row, scope=scope) for row in _scan(dirs[1], ("*/SKILL.md",))]
    # Every skill the ONE authority loads (native + plugin-typed + MCP), through the canonical
    # inventory — this reader projects it, it does not keep a second loader.
    from core.native_skill_library import skill_inventory as _canonical_inventory

    native = [
        {
            "name": row["id"],
            "description": row.get("description") or "",
            "version": row.get("version") or "",
            "source": row.get("source") or "native",
            "path": "",
            "parses": bool(row.get("available") or row.get("enabled")),
        }
        for row in _canonical_inventory()
    ]

    return {
        "status": "ok",
        "workspace": str(workspace_root) if workspace_skills else str(workspace or ""),
        "workspace_skills": workspace_skills,
        "native_skills": native,
        "chat_skills": scoped["chat"],
        "project_skills": scoped["project"],
        "staged_skills": staged,
        "installed_skills": installed,
        "total": len(workspace_skills) + len(native) + len(staged) + len(installed)
        + len(scoped["chat"]) + len(scoped["project"]),
    }


_LOAD_CHECKS: dict[str, str] = {}
_LOAD_LOCK = threading.Lock()
#: Origins whose text VOOL ships or verifies by digest: their check ran before they shipped or were
#: installed, and their bytes cannot change without the digest failing (core.skill_origin).
_CHECKED_ELSEWHERE = frozenset({"native", "bundled", "registry"})


def load_problem(skill: Any) -> str:
    """The safety-check refusal for a skill about to reach a turn, or "" when it may (audit M1).

    Every way IN runs the check (``content_problem``); a skill an MCP server serves, or a file copied
    by hand into a skill folder, never comes in that way, so it is checked here, on the text the turn
    would carry, cached by that text's digest. A skill that fails is kept off, never fenced and used.
    VOOL's own library, the packs it ships, signed packages and Browse add-ons are checked before
    they are installed and bound to their digest, so they are never re-read here.
    """
    try:
        from core.skill_origin import origin_of

        origin = origin_of(skill)
        plugin_id = str(getattr(skill, "plugin_id", "") or "").removeprefix("plugin:")
        if origin in _CHECKED_ELSEWHERE or plugin_id.startswith("discover-"):
            return ""
        if plugin_id and plugin_id not in ("local-skills", "chat-skills", "project-skills"):
            # A plugin pack admitted through the plugin lifecycle is bound to its verified digest
            # (an edited pack stops loading), so it is not re-read here; a pack with no record
            # got into the folders by hand and is checked like any hand-copied skill.
            from core.plugin_lifecycle import record_for

            if record_for(plugin_id) is not None:
                return ""
        path = str(getattr(skill, "path", "") or "")
        text = ""
        if path and not path.startswith("skill://"):
            with contextlib.suppress(OSError, UnicodeDecodeError):
                text = Path(path).read_text(encoding="utf-8")
        if not text:
            text = render_skill_markdown(
                name=str(getattr(skill, "name", "") or ""), description=str(getattr(skill, "description", "") or ""),
                body=str(getattr(skill, "body", "") or ""), triggers=tuple(getattr(skill, "triggers", ()) or ()),
            )
        key = hashlib.sha256(text.encode("utf-8")).hexdigest()
        with _LOAD_LOCK:
            if key in _LOAD_CHECKS:
                return _LOAD_CHECKS[key]
        problem = content_problem(text)
        with _LOAD_LOCK:
            if len(_LOAD_CHECKS) > 2048:
                _LOAD_CHECKS.clear()
            _LOAD_CHECKS[key] = problem
        return problem
    except Exception:  # a check that cannot run keeps the skill out (fails closed)
        return "VOOL could not check this skill's file."


def passing_load_check(skills: tuple[Any, ...] | list[Any]) -> tuple[Any, ...]:
    """The skills that pass ``load_problem``, in order."""
    return tuple(skill for skill in skills if not load_problem(skill))


def scoped_skills(keys: dict[str, str] | None) -> tuple[Any, ...]:
    """Every ACTIVE skill of this chat and of its project, parsed by the real loader.

    Chat skills first, then project skills: on a name collision the narrower one is the one the
    operator meant for this conversation. ``plugin_id`` carries the scope (``chat-skills`` /
    ``project-skills``) so provenance says where an influence came from.
    """
    found: list[Any] = []
    for scope, active in _scope_active_dirs(keys):
        if not active.is_dir():
            continue
        with contextlib.suppress(OSError):
            for skill_dir in sorted(active.iterdir()):
                skill_md = skill_dir / "SKILL.md"
                if not skill_md.is_file():
                    continue
                skill = _parse_cached(skill_md, f"{scope}-skills")
                if skill is not None:
                    found.append(skill)
    # A file copied by hand into a chat's or project's skills folder meets the safety check too
    # (audit M1): one that fails is kept off.
    return passing_load_check(found)


_PARSE_CACHE: dict[tuple[str, float, str], Any] = {}
_PARSE_LOCK = threading.Lock()


def _parse_cached(path: Path, plugin_id: str) -> Any:
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None
    key = (str(path), mtime, plugin_id)
    with _PARSE_LOCK:
        cached = _PARSE_CACHE.get(key)
    if cached is not None:
        return cached
    skill = parse_skill(path, plugin_id=plugin_id)
    if skill is not None:
        with _PARSE_LOCK:
            if len(_PARSE_CACHE) > 512:
                _PARSE_CACHE.clear()
            _PARSE_CACHE[key] = skill
    return skill


#: Pack names VOOL gives its own kinds of packs. A pack someone imports or makes cannot take one: a
#: "registry-..." pack was labelled a signed Skill Codex package, and an install into a signed or
#: Browse pack's folder changed its files so it stopped loading (audit L3, 2026-11-11).
_RESERVED_PACK_PREFIXES = ("registry-", "discover-", "vool-")
_RESERVED_PACK_NAMES = frozenset({"local-skills", "chat-skills", "project-skills"})


def pack_name_problem(name: str, *, installing_into: bool = False) -> str:
    """The plain refusal when ``name`` is a pack name VOOL keeps for itself, or "".

    ``installing_into`` is the ``plugin_id`` of a skill install: the person's own ``local-skills`` and
    packs they made are fine there; a pack VOOL or Browse manages, or a reserved name, is not.
    """
    slug = _slug(name)
    if not slug:
        return ""
    if installing_into and slug == "local-skills":
        return ""
    reserved = slug in _RESERVED_PACK_NAMES or slug.startswith(_RESERVED_PACK_PREFIXES)
    if not reserved:
        with contextlib.suppress(Exception):
            from core.plugin_catalog import bundled_plugin_dirs

            reserved = slug in {_slug(Path(folder).name) for folder in bundled_plugin_dirs()}
    if not reserved and installing_into:
        existing = plugins_root() / "plugins" / slug
        reserved = (existing / ".codex-plugin" / "plugin.json").is_file() and not _removable_global_pack(existing)
    if not reserved:
        return ""
    return (f'The name "{slug}" is kept for packs that VOOL or Browse manage, so I did not use it. '
            'Pick another name, for example "my-' + slug.split("-", 1)[-1] + '", and try again.')


def _removable_global_pack(pack_dir: Path) -> bool:
    """Whether a global pack holds skills the operator authored (and may therefore remove).

    ``local-skills`` is where chat-authored skills have always been installed; a pack the plugin
    creator made carries ``"vool": {"origin": "user"}`` in its manifest. Bundled first-party packs
    and add-on-store packs are not the operator's drafts: they are managed elsewhere.
    """
    if pack_dir.name == "local-skills":
        return True
    try:
        manifest = json.loads((pack_dir / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(manifest, dict) and dict(manifest.get("vool") or {}).get("origin") == "user"


def remove_skill(name: str, *, scope: str = "", keys: dict[str, str] | None = None) -> dict[str, Any]:
    """Turn an installed user skill off by moving it out of the loader's tree. Nothing is deleted.

    The whole skill folder (SKILL.md and its version history) moves to a ``removed`` folder of its
    scope with a timestamp, so it can be brought back by hand and its history is intact. With no
    ``scope`` the narrowest match wins (this chat, this project, then global). First-party and
    add-on-store skills are refused: they are not the operator's drafts to remove.
    """
    slug = _slug(name)
    if not slug:
        return {"status": "invalid_arguments", "reason": "name is required"}
    wanted = str(scope or "").strip().lower()
    if wanted and wanted not in ("chat", "project", "global"):
        return {"status": "invalid_arguments", "reason": f"unknown scope {wanted!r}; use chat, project or global"}

    from core.skill_scopes import REMOVED_DIRNAME

    target: Path | None = None
    target_scope = ""
    removed_root: Path | None = None
    for candidate_scope, active in _scope_active_dirs(keys):
        if wanted and candidate_scope != wanted:
            continue
        if (active / slug / "SKILL.md").is_file():
            target, target_scope, removed_root = active / slug, candidate_scope, active.parent / REMOVED_DIRNAME
            break
    if target is None and wanted in ("", "global"):
        with contextlib.suppress(OSError):
            for candidate in sorted((plugins_root() / "plugins").glob(f"*/skills/{slug}")):
                if not (candidate / "SKILL.md").is_file():
                    continue
                pack = candidate.parent.parent
                if not _removable_global_pack(pack):
                    return {
                        "status": "refused",
                        "reason": f"`{slug}` belongs to the {pack.name!r} package, which VOOL manages; "
                        "turn it off in the skills settings instead",
                    }
                target, target_scope, removed_root = candidate, "global", plugins_root() / "removed-skills"
                break
    if target is None or removed_root is None:
        where = f"in {wanted}" if wanted else "in this chat, this project or globally"
        return {"status": "not_found", "reason": f"no installed skill named {name!r} {where}"}

    stamp = _utcnow().replace(":", "").replace("-", "")
    destination = removed_root / f"{slug}-{stamp}"
    with _ACTIVATION_LOCK:
        try:
            removed_root.mkdir(parents=True, exist_ok=True)
            shutil.move(str(target), str(destination))
        except OSError as exc:
            return {"status": "error", "reason": f"{type(exc).__name__}: could not move the skill out"}
    if target_scope == "global":
        with contextlib.suppress(Exception):
            from core.plugin_catalog import invalidate_storage_listing

            invalidate_storage_listing()
    return {
        "status": "ok",
        "name": slug,
        "scope": target_scope,
        "moved_to": str(destination),
        "active": False,
        "note": "it no longer loads from the next turn; its files and versions were kept",
    }
