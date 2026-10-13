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
        import yaml

        manifest = yaml.safe_load(match.group(1))
        if not isinstance(manifest, dict):
            manifest = {}
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


def _slug(name: str) -> str:
    return _SLUG_RE.sub("-", str(name or "").strip().lower()).strip("-")


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
) -> dict[str, Any]:
    """Write a staged `SKILL.md`. Loads nothing and changes no behaviour.

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

    if scope == "global":
        directory = staging_root() / slug
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
    try:
        directory.mkdir(parents=True, exist_ok=True)
        rendered = render_skill_markdown(
            name=name, description=description, body=body,
            allowed_tools=allowed_tools, triggers=triggers,
        ).encode("utf-8")
        target.write_bytes(rendered)
    except OSError as exc:
        return {"status": "error", "reason": f"{type(exc).__name__}: could not write the skill"}
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
    """Where a skill of this slug could live, IN SEARCH ORDER: staging, native, then installed.

    Order is behaviour, not tidiness. A redraft of an already-installed skill is the copy the
    operator is validating; resolving to the installed one instead reports on the previous version
    and calls it current. The native root sits between them: a first-party package is the shipped
    source of truth, so a bare name resolves to it in preference to a stale installed copy.

    With scope keys, this chat's skills (draft, then active) come first, then this project's,
    then everything above: the narrowest scope is the one the operator is working in.
    """

    from core.native_skill_library import native_skills_root

    locations: list[Path] = []
    for scope in ("chat", "project"):
        dirs = _scoped_dirs(scope, keys)
        if dirs is not None:
            locations.extend([dirs[0] / slug / "SKILL.md", dirs[1] / slug / "SKILL.md"])
    locations.append(staging_root() / slug / "SKILL.md")
    locations.append(native_skills_root() / slug / "SKILL.md")
    # Installed skills sit at plugins/<plugin_id>/skills/<slug>/SKILL.md.
    with contextlib.suppress(OSError):
        locations.extend(sorted((plugins_root() / "plugins").glob(f"*/skills/{slug}/SKILL.md")))
    return locations


def validate_skill(path: str, keys: dict[str, str] | None = None, *, candidate_contracts: tuple = ()) -> dict[str, Any]:
    """Parse with the REAL loader and report what would stop this skill working.

    "It parsed" is the easy half and the less useful one. A skill with no description never ranks,
    and a skill naming a tool that does not exist will narrow the model to nothing on the turn it
    matches — both parse perfectly.
    """

    candidate = resolve_skill_markdown(path, keys)
    if not candidate.is_file():
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
    skill = parse_skill_content(preview_markdown, path=candidate)
    if skill is None:
        return {
            "status": "invalid",
            "path": str(candidate),
            "problems": [
                "no YAML frontmatter - a skill must open with a --- block naming it and describing "
                "when it applies"
            ],
        }

    problems: list[str] = []
    from core.registry.rules import analyze

    boundaries = analyze({"SKILL.md": raw})
    problems.extend(conflict["reason"] for conflict in boundaries["conflicts"])
    full_match = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", preview_markdown, re.DOTALL)
    full_body = full_match.group(2).strip() if full_match else ""
    if len(full_body) > _MAX_BODY_CHARS:
        problems.append(f"body exceeds {_MAX_BODY_CHARS} characters; the loader would truncate instructions")
    try:
        import yaml

        if not isinstance(yaml.safe_load(full_match.group(1)), dict):
            problems.append("frontmatter must be a YAML object")
    except Exception:
        problems.append("frontmatter could not be validated as YAML")
    if not skill.name.strip():
        problems.append("name is empty")
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
    if expected and source.is_file():
        try:
            actual = hashlib.sha256(source.read_bytes()).hexdigest()
        except OSError:
            actual = ""
        if actual != expected:
            return {
                "status": "refused",
                "reason": "the draft changed after it was shown, so it was not activated; review it again",
                "path": str(source),
                "problems": ["draft digest differs from the one that was reviewed"],
            }
    verdict = validate_skill(str(source))
    # A skill that is not THERE has not failed validation. Saying "the skill does not validate" about
    # a missing file names the wrong problem and sends the reader to fix the skill's contents.
    if verdict.get("status") == "not_found":
        return {
            "status": "not_found",
            "reason": f"no SKILL.md at {source} - create or stage it first",
            "path": str(source),
            "problems": [verdict.get("reason") or "not found"],
        }
    if verdict.get("status") != "ok":
        return {
            "status": "refused",
            "reason": "the skill does not validate, so installing it would add a capability that "
                      "cannot work",
            "path": str(source),
            "problems": verdict.get("problems") or [verdict.get("reason") or "unreadable"],
        }

    slug = _slug(str(verdict.get("name") or source.parent.name))
    clean_plugin = _slug(plugin_id) or "local-skills"
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
            return {
                "status": "refused",
                "reason": (
                    f"identity collision: the instructions being installed are already active as "
                    f"{other!r} (same effective digest); a skill set cannot live behind two names"
                ),
                "path": str(source),
                "collision_with": other,
                "effective_sha256": incoming_effective,
                "problems": [f"effective digest equals installed skill {other!r}"],
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
                    "reason": "the draft changed after it was shown, so it was not activated; review it again",
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

        return {
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
    return tuple(found)


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
