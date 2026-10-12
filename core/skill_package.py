"""Share a skill: one zip out, the same zip in. Also plugin packs (a named bundle of skills).

A skill is exported as ONE zip holding its folder::

    <slug>/SKILL.md      the skill exactly as it is active (frontmatter + instructions)
    <slug>/skill.json    what this package is: format, name, version, and the SKILL.md digest

A plugin pack is the same idea one level up::

    <pack>/.codex-plugin/plugin.json     the manifest the plugin catalog already reads
    <pack>/skills/<slug>/SKILL.md        one folder per skill
    <pack>/plugin.export.json            format, name, and every SKILL.md digest

Import takes that same zip (and a bare ``SKILL.md``-in-a-folder zip made by another tool), checks
it, and STAGES it in the scope the operator named. Nothing an import writes is active: activation
is the same gated ``skill.install`` step a drafted skill goes through, bound to the digest the
operator was shown.

What an import refuses, before anything is written:

* archive dangers, decided on the zip's own metadata before any body is read (the archive law of
  ``core.session_portability.bundle``): traversal, absolute or backslash names, duplicate or
  normalization-colliding names, symlinks and special members, encryption, unknown compression,
  member/total size bounds and compression bombs;
* anything that is not instructions. VOOL imports instruction-only skills: a ``scripts/`` folder,
  a ``bin/`` handler or a manifest declaring ``tools``/``mcpServers``/``hooks`` is runnable code, and
  running someone else's code is not what "take my skill" means. The refusal names the members;
* a package whose recorded digest does not match its ``SKILL.md`` (edited after export).

Export is limited to skills the operator authored (this chat's, this project's, global
``local-skills`` and packs the plugin creator made). First-party skills ship with VOOL already, and
a skill marked ``local-only: true`` in its frontmatter is never packaged.
"""
from __future__ import annotations

import hashlib
import io
import json
import threading
import time
import unicodedata
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

SKILL_FORMAT = "vool.skill.v1"
PLUGIN_FORMAT = "vool.plugin.v1"
SKILL_MANIFEST = "skill.json"
PLUGIN_EXPORT_MANIFEST = "plugin.export.json"
PLUGIN_MANIFEST = ".codex-plugin/plugin.json"
EXPORTS_DIRNAME = "exports"
STAGED_PLUGINS_DIRNAME = "staged-plugins"

#: A fixed member timestamp: the same skill exports to the same bytes.
ZIP_DATE_TIME = (2026, 1, 1, 0, 0, 0)

#: Bounds for an instruction package. A SKILL.md body is capped at 8000 characters by the loader,
#: so these are generous for text and tiny for anything else.
MAX_PACKAGE_BYTES = 4 * 1024 * 1024
MAX_MEMBERS = 128
MAX_MEMBER_BYTES = 256 * 1024
MAX_TOTAL_BYTES = 8 * 1024 * 1024
MAX_RATIO = 200

#: Text that may travel next to a SKILL.md. Everything else is refused, by name.
_SIDE_FILES = frozenset({"readme.md", "readme.txt", "license", "license.md", "license.txt"})
#: Manifest keys that declare runnable surfaces.
_RUNNABLE_MANIFEST_KEYS = ("tools", "mcpServers", "mcp_servers", "hooks", "apps", "commands", "runtime")


class PackageRefused(ValueError):
    """A package VOOL will not export or import, with the reason the operator is shown."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _utcnow() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _exports_dir() -> Path:
    from core.runtime_paths import data_path
    from core.skill_scopes import USER_SKILLS_DIRNAME

    return data_path(USER_SKILLS_DIRNAME, EXPORTS_DIRNAME)


def _zip_bytes(members: list[tuple[str, bytes]]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(members):
            info = zipfile.ZipInfo(name, date_time=ZIP_DATE_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, data)
    return buffer.getvalue()


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


def _frontmatter(text: str) -> dict[str, Any]:
    from core.plugin_skills import _FRONTMATTER_RE

    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}
    try:
        import yaml

        loaded = yaml.safe_load(match.group(1))
    except Exception:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _is_local_only(skill_md: bytes) -> bool:
    try:
        front = _frontmatter(skill_md.decode("utf-8"))
    except UnicodeDecodeError:
        return False
    value = front.get("local-only", front.get("local_only"))
    return value is True or str(value).strip().lower() in {"true", "yes", "1"}


# --------------------------------------------------------------------------------------------
# Export
# --------------------------------------------------------------------------------------------


def _user_skill_dir(name: str, keys: dict[str, str] | None, scope: str = "") -> tuple[Path, str]:
    """The active, operator-authored skill folder this name means, and its scope."""
    from core import skill_tools

    slug = skill_tools._slug(name)
    if not slug:
        raise PackageRefused("invalid_arguments", "name the skill to export")
    wanted = str(scope or "").strip().lower()
    for candidate_scope, active in skill_tools._scope_active_dirs(keys):
        if wanted and candidate_scope != wanted:
            continue
        if (active / slug / "SKILL.md").is_file():
            return active / slug, candidate_scope
    if wanted in ("", "global"):
        packs = skill_tools.plugins_root() / "plugins"
        for candidate in sorted(packs.glob(f"*/skills/{slug}")):
            if not (candidate / "SKILL.md").is_file():
                continue
            if skill_tools._removable_global_pack(candidate.parent.parent):
                return candidate, "global"
            raise PackageRefused(
                "not_user_skill",
                f"`{slug}` comes with the {candidate.parent.parent.name!r} package, not from you; "
                "only skills you made can be exported",
            )
    try:
        from core.native_skill_library import native_skills_root

        if (native_skills_root() / slug / "SKILL.md").is_file():
            raise PackageRefused(
                "not_user_skill",
                f"`{slug}` is a first-party VOOL skill; it ships with VOOL, so there is nothing to export",
            )
    except PackageRefused:
        raise
    except Exception:
        pass
    raise PackageRefused("not_found", f"no active skill named {name!r} that you made")


def export_skill(
    name: str,
    *,
    keys: dict[str, str] | None = None,
    scope: str = "",
    out_dir: Path | None = None,
) -> dict[str, Any]:
    """Package one active skill as ``<slug>-v<version>.zip``. Never writes into the skill itself."""
    from core import skill_tools

    try:
        skill_dir, found_scope = _user_skill_dir(name, keys, scope)
        skill_md = (skill_dir / "SKILL.md").read_bytes()
    except PackageRefused as refused:
        return {"status": refused.code, "reason": refused.message}
    except OSError as exc:
        return {"status": "error", "reason": f"{type(exc).__name__}: could not read the skill"}
    if _is_local_only(skill_md):
        return {
            "status": "refused",
            "reason": f"`{skill_dir.name}` is marked local-only, so it is never packaged for sharing",
        }
    skill = skill_tools.parse_skill(skill_dir / "SKILL.md")
    history = skill_tools._read_history(skill_dir)
    version = int(history.get("head_version") or 0) or 1
    manifest = {
        "format": SKILL_FORMAT,
        "kind": "skill",
        "name": str(getattr(skill, "name", "") or skill_dir.name),
        "slug": skill_dir.name,
        "description": str(getattr(skill, "description", "") or ""),
        "version": version,
        "files": {"SKILL.md": _sha256(skill_md)},
        "exported_at": _utcnow(),
    }
    archive = _zip_bytes(
        [
            (f"{skill_dir.name}/SKILL.md", skill_md),
            (f"{skill_dir.name}/{SKILL_MANIFEST}", (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8")),
        ]
    )
    target = Path(out_dir) if out_dir is not None else _exports_dir()
    path = target / f"{skill_dir.name}-v{version}.zip"
    try:
        _write_atomic(path, archive)
    except OSError as exc:
        return {"status": "error", "reason": f"{type(exc).__name__}: could not write the export"}
    return {
        "status": "ok",
        "kind": "skill",
        "name": manifest["name"],
        "slug": skill_dir.name,
        "scope": found_scope,
        "version": version,
        "path": str(path),
        "bytes": len(archive),
        "sha256": _sha256(archive),
        "skill_sha256": manifest["files"]["SKILL.md"],
    }


def _user_pack_dir(name: str) -> Path:
    from core import skill_tools

    slug = skill_tools._slug(name)
    if not slug:
        raise PackageRefused("invalid_arguments", "name the plugin to export")
    pack = skill_tools.plugins_root() / "plugins" / slug
    if not (pack / ".codex-plugin" / "plugin.json").is_file():
        raise PackageRefused("not_found", f"no installed plugin named {name!r}")
    if not skill_tools._removable_global_pack(pack):
        raise PackageRefused(
            "not_user_skill",
            f"`{slug}` is not a plugin you made; only plugins you made can be exported",
        )
    return pack


def export_plugin(name: str, *, out_dir: Path | None = None) -> dict[str, Any]:
    """Package an installed, operator-made plugin pack (manifest + skills, no version history)."""
    try:
        pack = _user_pack_dir(name)
        manifest_bytes = (pack / ".codex-plugin" / "plugin.json").read_bytes()
        manifest = json.loads(manifest_bytes.decode("utf-8"))
    except PackageRefused as refused:
        return {"status": refused.code, "reason": refused.message}
    except (OSError, ValueError) as exc:
        return {"status": "error", "reason": f"{type(exc).__name__}: could not read the plugin manifest"}
    try:
        _refuse_runnable_manifest(manifest, pack.name)
    except PackageRefused as refused:
        return {"status": refused.code, "reason": refused.message}
    members: list[tuple[str, bytes]] = [(f"{pack.name}/{PLUGIN_MANIFEST}", manifest_bytes)]
    skills: dict[str, str] = {}
    for skill_md in sorted((pack / "skills").glob("*/SKILL.md")):
        data = skill_md.read_bytes()
        if _is_local_only(data):
            return {
                "status": "refused",
                "reason": f"`{skill_md.parent.name}` in {pack.name!r} is marked local-only, so the "
                "plugin is not packaged for sharing",
            }
        members.append((f"{pack.name}/skills/{skill_md.parent.name}/SKILL.md", data))
        skills[skill_md.parent.name] = _sha256(data)
    if not skills:
        return {"status": "refused", "reason": f"{pack.name!r} holds no skills to share"}
    export_manifest = {
        "format": PLUGIN_FORMAT,
        "kind": "plugin",
        "name": pack.name,
        "version": str(manifest.get("version") or "0.1.0"),
        "skills": skills,
        "manifest_sha256": _sha256(manifest_bytes),
        "exported_at": _utcnow(),
    }
    members.append(
        (f"{pack.name}/{PLUGIN_EXPORT_MANIFEST}", (json.dumps(export_manifest, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    )
    archive = _zip_bytes(members)
    target = Path(out_dir) if out_dir is not None else _exports_dir()
    path = target / f"{pack.name}-plugin-{export_manifest['version']}.zip"
    try:
        _write_atomic(path, archive)
    except OSError as exc:
        return {"status": "error", "reason": f"{type(exc).__name__}: could not write the export"}
    return {
        "status": "ok",
        "kind": "plugin",
        "name": pack.name,
        "skills": sorted(skills),
        "path": str(path),
        "bytes": len(archive),
        "sha256": _sha256(archive),
    }


# --------------------------------------------------------------------------------------------
# Reading a package (import)
# --------------------------------------------------------------------------------------------


def _member_name_safe(name: str) -> bool:
    if not name or name.startswith(("/", "\\")) or "\\" in name or "\x00" in name:
        return False
    parts = PurePosixPath(name).parts
    if not parts or any(part in ("", ".", "..") for part in parts):
        return False
    return not (len(parts[0]) == 2 and parts[0][1] == ":")  # "C:" drive prefix


def _precheck(infolist: list[zipfile.ZipInfo]) -> None:
    """Structural safety on metadata only; no member body has been read when this runs."""
    if len(infolist) > MAX_MEMBERS:
        raise PackageRefused("archive_unsafe", f"the zip has {len(infolist)} entries, over the bound of {MAX_MEMBERS}")
    seen: dict[str, str] = {}
    total = 0
    for info in infolist:
        name = info.filename
        if not _member_name_safe(name):
            raise PackageRefused(
                "archive_unsafe",
                f"entry {name!r} has an absolute, traversing or backslash path",
            )
        canonical = unicodedata.normalize("NFC", name).casefold()
        if canonical in seen:
            raise PackageRefused("archive_unsafe", f"entry {name!r} collides with {seen[canonical]!r}")
        seen[canonical] = name
        if info.is_dir():
            continue
        if info.flag_bits & 0x1:
            raise PackageRefused("archive_unsafe", f"entry {name!r} is encrypted")
        if info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
            raise PackageRefused("archive_unsafe", f"entry {name!r} uses an unsupported compression method")
        file_type = (info.external_attr >> 16) & 0o170000
        if file_type not in (0, 0o100000):
            raise PackageRefused("archive_unsafe", f"entry {name!r} is a link or special file")
        if info.file_size > MAX_MEMBER_BYTES:
            raise PackageRefused("archive_unsafe", f"entry {name!r} is {info.file_size} bytes, over the {MAX_MEMBER_BYTES}-byte bound")
        total += info.file_size
        if total > MAX_TOTAL_BYTES:
            raise PackageRefused("archive_unsafe", f"the zip expands past {MAX_TOTAL_BYTES} bytes")
        if info.file_size > 64 * 1024 and info.file_size // max(1, info.compress_size) > MAX_RATIO:
            raise PackageRefused("archive_unsafe", f"entry {name!r} has a compression ratio over {MAX_RATIO}:1")


def _read_members(raw: bytes) -> dict[str, bytes]:
    if len(raw) > MAX_PACKAGE_BYTES:
        raise PackageRefused("archive_unsafe", f"the file is {len(raw)} bytes, over the {MAX_PACKAGE_BYTES}-byte bound")
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            infolist = archive.infolist()
            _precheck(infolist)
            if archive.testzip() is not None:
                raise PackageRefused("archive_malformed", "the zip is corrupt")
            return {info.filename: archive.read(info.filename) for info in infolist if not info.is_dir()}
    except zipfile.BadZipFile as exc:
        raise PackageRefused("archive_malformed", "this file is not a readable zip") from exc


def _strip_common_root(members: dict[str, bytes]) -> tuple[str, dict[str, bytes]]:
    """Drop one shared top folder (``<slug>/...``), so both zip layouts read the same."""
    tops = {PurePosixPath(name).parts[0] for name in members}
    if len(tops) == 1:
        top = next(iter(tops))
        if all(len(PurePosixPath(name).parts) > 1 for name in members):
            return top, {str(PurePosixPath(*PurePosixPath(name).parts[1:])): data for name, data in members.items()}
    return "", dict(members)


def _refuse_runnable_manifest(manifest: Any, label: str) -> None:
    if not isinstance(manifest, dict):
        raise PackageRefused("invalid_manifest", f"{label}: the plugin manifest is not a JSON object")
    declared = [key for key in _RUNNABLE_MANIFEST_KEYS if manifest.get(key)]
    if declared:
        raise PackageRefused(
            "runnable_code",
            f"{label} declares {', '.join(declared)}, which run code; VOOL imports and shares "
            "instruction-only skills and plugins",
        )


def _recorded_manifest(raw: bytes, label: str, *, digests: str, strings: tuple[str, ...] = ()) -> dict[str, Any]:
    """A recorded export manifest as a JSON object, or a refusal naming what is wrong with it.

    Valid JSON of the wrong shape (a list, a number, a string, a digest map that is not a map of
    strings) is refused exactly like invalid JSON: it is not a manifest this reader can check."""
    try:
        recorded = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise PackageRefused("invalid_manifest", f"{label} is not valid JSON") from exc
    if not isinstance(recorded, dict):
        raise PackageRefused("invalid_manifest", f"{label} is not a JSON object")
    mapping = recorded.get(digests)
    if mapping is not None and not (
        isinstance(mapping, dict) and all(isinstance(key, str) and isinstance(value, str) for key, value in mapping.items())
    ):
        raise PackageRefused("invalid_manifest", f"{label}: {digests!r} is not a map of names to digests")
    for key in strings:
        if recorded.get(key) is not None and not isinstance(recorded.get(key), str):
            raise PackageRefused("invalid_manifest", f"{label}: {key!r} is not text")
    return recorded


def _decode_skill(data: bytes, label: str) -> str:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PackageRefused("invalid_skill", f"{label} is not UTF-8 text") from exc
    from core.plugin_skills import _FRONTMATTER_RE

    if not _FRONTMATTER_RE.match(text):
        raise PackageRefused("invalid_skill", f"{label} has no frontmatter block naming the skill")
    return text


def read_package(raw: bytes) -> dict[str, Any]:
    """Classify and check a package. Returns its skills' exact bytes; writes nothing.

    ``{"kind": "skill"|"plugin", "name": str, "skills": [{"slug", "bytes", "sha256"}], ...}``
    """
    members = _read_members(raw)
    if not members:
        raise PackageRefused("invalid_package", "the zip is empty")
    top, inner = _strip_common_root(members)

    if PLUGIN_MANIFEST in inner:
        return _read_plugin(top, inner)

    skill_names = [name for name in inner if PurePosixPath(name).name == "SKILL.md"]
    if len(skill_names) != 1 or "/" in skill_names[0]:
        if len(skill_names) > 1:
            raise PackageRefused(
                "invalid_package",
                "the zip holds several SKILL.md files but no plugin manifest; export them as a plugin "
                "or one skill per zip",
            )
        raise PackageRefused("invalid_package", "no SKILL.md at the top of the skill folder")
    extra = [
        name for name in inner
        if name not in ("SKILL.md", SKILL_MANIFEST) and name.lower() not in _SIDE_FILES
    ]
    if extra:
        raise PackageRefused(
            "runnable_code" if any(_looks_runnable(name) for name in extra) else "unsupported_files",
            "VOOL imports instruction-only skills; this zip also carries: " + ", ".join(sorted(extra)[:12]),
        )
    data = inner["SKILL.md"]
    _decode_skill(data, "SKILL.md")
    digest = _sha256(data)
    recorded: dict[str, Any] = {}
    if SKILL_MANIFEST in inner:
        recorded = _recorded_manifest(inner[SKILL_MANIFEST], "skill.json", digests="files",
                                      strings=("slug", "name", "format"))
        expected = str((recorded.get("files") or {}).get("SKILL.md") or "")
        if expected and expected != digest:
            raise PackageRefused(
                "digest_mismatch",
                "SKILL.md was changed after this package was exported (its digest no longer matches skill.json)",
            )
    slug = str(recorded.get("slug") or top or "")
    return {
        "kind": "skill",
        "name": str(recorded.get("name") or slug),
        "slug_hint": slug,
        "skills": [{"slug": slug, "bytes": data, "sha256": digest}],
        "format": str(recorded.get("format") or ""),
    }


def _looks_runnable(name: str) -> bool:
    lowered = name.lower()
    return lowered.startswith(("bin/", "scripts/", "hooks/")) or "/bin/" in lowered or "/scripts/" in lowered or lowered.endswith(
        (".py", ".sh", ".js", ".mjs", ".ts", ".rb", ".pl", ".exe", ".bat", ".cmd", ".ps1", ".so", ".dylib", ".dll", ".jar", ".command")
    )


def _read_plugin(top: str, inner: dict[str, bytes]) -> dict[str, Any]:
    try:
        manifest = json.loads(inner[PLUGIN_MANIFEST].decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise PackageRefused("invalid_manifest", "the plugin manifest is not valid JSON") from exc
    name = str(manifest.get("name") or top or "") if isinstance(manifest, dict) else ""
    _refuse_runnable_manifest(manifest, name or "this plugin")
    skills: list[dict[str, Any]] = []
    extra: list[str] = []
    for member, data in sorted(inner.items()):
        parts = PurePosixPath(member).parts
        if member in (PLUGIN_MANIFEST, PLUGIN_EXPORT_MANIFEST) or member.lower() in _SIDE_FILES:
            continue
        if len(parts) == 3 and parts[0] == "skills" and parts[2] == "SKILL.md":
            _decode_skill(data, member)
            skills.append({"slug": parts[1], "bytes": data, "sha256": _sha256(data)})
            continue
        if len(parts) == 3 and parts[0] == "skills" and (parts[2] == SKILL_MANIFEST or parts[2].lower() in _SIDE_FILES):
            continue
        extra.append(member)
    if extra:
        raise PackageRefused(
            "runnable_code" if any(_looks_runnable(name) for name in extra) else "unsupported_files",
            f"VOOL imports instruction-only plugins; {name or 'this plugin'} also carries: " + ", ".join(extra[:12]),
        )
    if not skills:
        raise PackageRefused("invalid_package", f"{name or 'this plugin'} holds no skills/<name>/SKILL.md")
    if PLUGIN_EXPORT_MANIFEST in inner:
        recorded = _recorded_manifest(inner[PLUGIN_EXPORT_MANIFEST], "plugin.export.json", digests="skills")
        expected = dict(recorded.get("skills") or {})
        for skill in skills:
            if expected.get(skill["slug"]) and expected[skill["slug"]] != skill["sha256"]:
                raise PackageRefused(
                    "digest_mismatch",
                    f"skills/{skill['slug']}/SKILL.md was changed after this plugin was exported",
                )
    return {"kind": "plugin", "name": name, "manifest": manifest, "skills": skills}


# --------------------------------------------------------------------------------------------
# Holding an attached package across an approval
# --------------------------------------------------------------------------------------------
#
# A chat attachment's bytes are deleted when its turn ends (core.chat_attachments.release_turn). In
# a mode that asks before writing, the import's first turn ends at the approval prompt, and the
# approved turn is resent WITHOUT the attachment -- so there was nothing left to import (measured
# live 2026-10-05). The skill lane holds the package bytes it already read, in memory, keyed by
# the chat and the attachment id, for one hour or until the import runs. Nothing is written to
# disk and no other chat can name a held package.

_HOLD_SECONDS = 3600.0
_HOLD_MAX = 16
_HOLD: dict[tuple[str, str], dict[str, Any]] = {}
_HOLD_LOCK = threading.Lock()


def hold_package(session_id: str, attachment_id: str, label: str, raw: bytes) -> None:
    if not session_id or not attachment_id or len(raw) > MAX_PACKAGE_BYTES:
        return
    with _HOLD_LOCK:
        now = time.monotonic()
        for key in [k for k, v in _HOLD.items() if now - v["at"] > _HOLD_SECONDS]:
            _HOLD.pop(key, None)
        while len(_HOLD) >= _HOLD_MAX:
            _HOLD.pop(min(_HOLD, key=lambda k: _HOLD[k]["at"]))
        _HOLD[(session_id, attachment_id)] = {"raw": bytes(raw), "label": str(label or ""), "at": now}


def held_package(session_id: str, attachment_id: str) -> tuple[bytes, str] | None:
    with _HOLD_LOCK:
        entry = _HOLD.get((session_id, attachment_id))
        if entry is None or time.monotonic() - entry["at"] > _HOLD_SECONDS:
            return None
        return entry["raw"], entry["label"]


def held_for_session(session_id: str) -> list[tuple[str, str]]:
    """(attachment_id, label) of every package held for this chat, oldest first."""
    with _HOLD_LOCK:
        now = time.monotonic()
        rows = sorted(
            ((k[1], v["label"], v["at"]) for k, v in _HOLD.items() if k[0] == session_id and now - v["at"] <= _HOLD_SECONDS),
            key=lambda row: row[2],
        )
    return [(attachment_id, label) for attachment_id, label, _at in rows]


def release_package(session_id: str, attachment_id: str) -> None:
    with _HOLD_LOCK:
        _HOLD.pop((session_id, attachment_id), None)


# --------------------------------------------------------------------------------------------
# Staging an import / a new plugin, and activating a plugin
# --------------------------------------------------------------------------------------------


def _staged_plugins_root() -> Path:
    from core import skill_tools

    return skill_tools.plugins_root() / STAGED_PLUGINS_DIRNAME


def pack_digest(pack_dir: Path) -> str:
    """One digest over a staged pack: its manifest and every SKILL.md, by relative path."""
    rows: list[str] = []
    for path in sorted(pack_dir.rglob("*")):
        if path.is_file() and "versions" not in path.relative_to(pack_dir).parts:
            rows.append(f"{path.relative_to(pack_dir).as_posix()}:{_sha256(path.read_bytes())}")
    return _sha256("\n".join(rows).encode("utf-8"))


def import_package(
    raw: bytes,
    *,
    scope: str = "global",
    keys: dict[str, str] | None = None,
    source_name: str = "",
) -> dict[str, Any]:
    """Check a package and STAGE it. Nothing becomes active here."""
    from core import skill_tools

    try:
        package = read_package(raw)
    except PackageRefused as refused:
        return {"status": "refused", "code": refused.code, "reason": refused.message}

    if package["kind"] == "skill":
        skill = package["skills"][0]
        parsed_name = ""
        try:
            from core.plugin_skills import parse_skill_content

            parsed = parse_skill_content(skill["bytes"].decode("utf-8"), path=Path(skill["slug"] or "skill") / "SKILL.md")
            front_name = str(_frontmatter(skill["bytes"].decode("utf-8")).get("name") or "")
            parsed_name = front_name or str(getattr(parsed, "name", "") or "")
        except Exception:
            parsed_name = ""
        slug = skill_tools._slug(parsed_name or skill["slug"] or Path(source_name).stem)
        if not slug:
            return {"status": "refused", "code": "invalid_skill", "reason": "the skill has no name"}
        scope = str(scope or "global").strip().lower()
        if scope == "global":
            staged_dir = skill_tools.staging_root() / slug
        else:
            from core.skill_scopes import scope_unavailable_reason

            dirs = skill_tools._scoped_dirs(scope, keys)
            if dirs is None:
                return {
                    "status": "scope_unavailable",
                    "reason": scope_unavailable_reason(scope, keys) or f"no {scope} to keep the skill in",
                }
            staged_dir = dirs[0] / slug
        try:
            _write_atomic(staged_dir / "SKILL.md", skill["bytes"])
        except OSError as exc:
            return {"status": "error", "reason": f"{type(exc).__name__}: could not stage the skill"}
        verdict = skill_tools.validate_skill(str(staged_dir / "SKILL.md"))
        return {
            "status": "ok" if verdict.get("status") == "ok" else "invalid",
            "kind": "skill",
            "name": verdict.get("name") or slug,
            "slug": slug,
            "scope": scope,
            "path": str(staged_dir / "SKILL.md"),
            "sha256": skill["sha256"],
            "description": verdict.get("description") or "",
            "problems": list(verdict.get("problems") or []),
            "preview_markdown": verdict.get("preview_markdown") or "",
            "staged": True,
        }

    # A plugin pack is global by nature: the plugin catalog is one tree.
    name = skill_tools._slug(package["name"]) or skill_tools._slug(Path(source_name).stem)
    if not name:
        return {"status": "refused", "code": "invalid_manifest", "reason": "the plugin has no name"}
    return _stage_pack(name, package["manifest"], [(s["slug"], s["bytes"]) for s in package["skills"]], origin="import")


def _stage_pack(name: str, manifest: dict[str, Any], skills: list[tuple[str, bytes]], *, origin: str) -> dict[str, Any]:
    from core import skill_tools

    staged = _staged_plugins_root() / name
    manifest = dict(manifest or {})
    manifest["name"] = name
    manifest.setdefault("version", "0.1.0")
    manifest["skills"] = "./skills/"
    manifest.setdefault(
        "interface",
        {"displayName": name.replace("-", " ").title(), "shortDescription": str(manifest.get("description") or "")[:120]},
    )
    manifest["vool"] = {**dict(manifest.get("vool") or {}), "origin": "user", "staged_by": origin}
    problems: dict[str, list[str]] = {}
    try:
        if staged.exists():
            import shutil

            shutil.rmtree(staged)
        for slug, data in skills:
            clean = skill_tools._slug(slug)
            if not clean:
                return {"status": "refused", "code": "invalid_skill", "reason": f"a skill folder name {slug!r} is not usable"}
            _write_atomic(staged / "skills" / clean / "SKILL.md", data)
        _write_atomic(
            staged / ".codex-plugin" / "plugin.json",
            (json.dumps(manifest, indent=2, ensure_ascii=False) + "\n").encode("utf-8"),
        )
    except OSError as exc:
        return {"status": "error", "reason": f"{type(exc).__name__}: could not stage the plugin"}
    for skill_md in sorted((staged / "skills").glob("*/SKILL.md")):
        verdict = skill_tools.validate_skill(str(skill_md))
        if verdict.get("status") != "ok":
            problems[skill_md.parent.name] = list(verdict.get("problems") or ["does not validate"])
    return {
        "status": "ok" if not problems else "invalid",
        "kind": "plugin",
        "name": name,
        "path": str(staged),
        "skills": sorted(p.parent.name for p in (staged / "skills").glob("*/SKILL.md")),
        "sha256": pack_digest(staged),
        "problems": [f"{slug}: {'; '.join(items)}" for slug, items in sorted(problems.items())],
        "staged": True,
    }


def create_plugin(
    name: str,
    *,
    description: str,
    skill_names: list[str] | tuple[str, ...],
    keys: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Stage a new plugin pack bundling skills the operator made. Nothing becomes active here."""
    from core import skill_tools

    slug = skill_tools._slug(name)
    if not slug:
        return {"status": "invalid_arguments", "reason": "name the plugin"}
    wanted = [str(item).strip() for item in (skill_names or ()) if str(item).strip()]
    if not wanted:
        return {"status": "invalid_arguments", "reason": "name at least one of your skills to put in the plugin"}
    gathered: list[tuple[str, bytes]] = []
    for item in wanted:
        try:
            skill_dir, _scope = _user_skill_dir(item, keys)
            data = (skill_dir / "SKILL.md").read_bytes()
        except PackageRefused as refused:
            return {"status": refused.code, "reason": refused.message}
        except OSError as exc:
            return {"status": "error", "reason": f"{type(exc).__name__}: could not read {item!r}"}
        if _is_local_only(data):
            return {"status": "refused", "reason": f"`{skill_dir.name}` is marked local-only and cannot go in a plugin"}
        gathered.append((skill_dir.name, data))
    existing = skill_tools.plugins_root() / "plugins" / slug
    if (existing / ".codex-plugin" / "plugin.json").is_file() and not skill_tools._removable_global_pack(existing):
        return {"status": "refused", "reason": f"a plugin named {slug!r} already exists and is managed by VOOL"}
    manifest = {
        "name": slug,
        "version": "0.1.0",
        "description": str(description or "").strip() or f"Skills bundled as {slug}.",
        "interface": {
            "displayName": slug.replace("-", " ").title(),
            "shortDescription": (str(description or "").strip() or f"Skills bundled as {slug}.")[:120],
        },
    }
    return _stage_pack(slug, manifest, gathered, origin="create")


def install_plugin(name: str, *, expected_sha256: str = "", overwrite: bool = False) -> dict[str, Any]:
    """Activate a staged plugin pack: it moves into the plugins tree the catalog and loader read."""
    import shutil

    from core import skill_tools

    slug = skill_tools._slug(name)
    staged = _staged_plugins_root() / slug
    if not slug or not (staged / ".codex-plugin" / "plugin.json").is_file():
        return {"status": "not_found", "reason": f"no staged plugin named {name!r}; create or import it first"}
    destination = skill_tools.plugins_root() / "plugins" / slug
    with skill_tools._ACTIVATION_LOCK:
        digest = pack_digest(staged)
        if expected_sha256 and digest != str(expected_sha256).strip().lower():
            return {
                "status": "refused",
                "reason": "the staged plugin changed after it was shown, so it was not activated; review it again",
            }
        problems: list[str] = []
        for skill_md in sorted((staged / "skills").glob("*/SKILL.md")):
            verdict = skill_tools.validate_skill(str(skill_md))
            if verdict.get("status") != "ok":
                problems.append(f"{skill_md.parent.name}: {'; '.join(verdict.get('problems') or ['does not validate'])}")
        if problems:
            return {"status": "refused", "reason": "a skill in the plugin does not validate", "problems": problems}
        if destination.exists():
            if not skill_tools._removable_global_pack(destination):
                return {"status": "refused", "reason": f"a plugin named {slug!r} is managed by VOOL and is not replaced"}
            if not overwrite:
                return {"status": "exists", "reason": f"a plugin named {slug!r} is already active; say to replace it"}
            stamp = _utcnow().replace(":", "").replace("-", "")
            retired = skill_tools.plugins_root() / "removed-plugins" / f"{slug}-{stamp}"
            retired.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(destination), str(retired))
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(staged, destination)
            for skill_dir in sorted((destination / "skills").iterdir()):
                if (skill_dir / "SKILL.md").is_file():
                    skill_tools._record_version(skill_dir, source="plugin")
        except OSError as exc:
            return {"status": "error", "reason": f"{type(exc).__name__}: could not activate the plugin"}
    try:
        from core.plugin_catalog import invalidate_storage_listing

        invalidate_storage_listing()
    except Exception:
        pass
    return {
        "status": "ok",
        "kind": "plugin",
        "name": slug,
        "path": str(destination),
        "skills": sorted(p.parent.name for p in (destination / "skills").glob("*/SKILL.md")),
        "sha256": digest,
        "active": True,
        "note": "its skills load from the next turn; no restart is needed",
    }


__all__ = [
    "PLUGIN_FORMAT",
    "SKILL_FORMAT",
    "PackageRefused",
    "create_plugin",
    "export_plugin",
    "export_skill",
    "held_for_session",
    "held_package",
    "hold_package",
    "import_package",
    "install_plugin",
    "pack_digest",
    "read_package",
    "release_package",
]
