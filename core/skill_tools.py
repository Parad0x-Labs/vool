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

from core.plugin_skills import parse_skill

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


def _utcnow() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _installed_skill_dir_by_name(name: str) -> Path | None:
    """The INSTALLED skill directory this human name or slug means, or None.

    Staging is deliberately not searched: versions exist only for activated skills, and a
    rollback against a draft would silently no-op the operator's intent.
    """
    slug = _slug(name)
    if not slug:
        return None
    root = plugins_root() / "plugins"
    with contextlib.suppress(OSError):
        for candidate in sorted(root.glob(f"*/skills/{slug}")):
            if (candidate / "SKILL.md").is_file():
                return candidate
    return None


def skill_history(name: str) -> dict[str, Any]:
    """The recorded versions of an installed skill: head, entries, where they live."""
    skill_dir = _installed_skill_dir_by_name(name)
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


def rollback_skill(name: str, *, version: int) -> dict[str, Any]:
    """Restore a prior version of an installed skill, byte for byte, as the new head.

    The bytes come from the immutable snapshot; the restore itself is a new head move recorded
    in the same history, so nothing is rewritten and every state the runtime passed through
    stays reconstructible. Calling this is an ACTIVATION-class change (it changes what future
    turns load) and belongs to `skill.rollback`, gated like `skill.install`.
    """
    name = str(name or "").strip()
    skill_dir = _installed_skill_dir_by_name(name)
    if skill_dir is None:
        return {
            "status": "not_found",
            "reason": f"no installed skill named {name!r} to roll back",
        }
    wanted = int(version)
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
    the same way here, and the existence check is left to the caller.
    """

    import os

    from core.plugin_catalog import _DEFAULT_PLUGINS_DIR

    override = str(os.environ.get("VOOL_PLUGINS_DIR") or "").strip()
    root = Path(override) if override else Path(_DEFAULT_PLUGINS_DIR)
    return root.expanduser()


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
) -> dict[str, Any]:
    """Write a staged `SKILL.md`. Loads nothing and changes no behaviour."""

    slug = _slug(name)
    if not slug:
        return {"status": "invalid_arguments", "reason": "name is required"}
    if not str(description or "").strip():
        # Not pedantry: `match_corpus` is name + description + triggers, so a skill without one is a
        # skill that can never rank, and writing it would be writing something inert.
        return {
            "status": "invalid_arguments",
            "reason": "description is required - it is what decides when the skill applies",
        }
    if not str(body or "").strip():
        return {"status": "invalid_arguments", "reason": "body is required - it is the instructions"}

    directory = staging_root() / slug
    target = directory / "SKILL.md"
    if target.exists() and not overwrite:
        return {
            "status": "exists",
            "reason": f"{target} already exists; pass overwrite to replace it",
            "path": str(target),
        }
    try:
        directory.mkdir(parents=True, exist_ok=True)
        target.write_text(
            render_skill_markdown(
                name=name, description=description, body=body,
                allowed_tools=allowed_tools, triggers=triggers,
            ),
            encoding="utf-8",
        )
    except OSError as exc:
        return {"status": "error", "reason": f"{type(exc).__name__}: could not write the skill"}
    return {
        "status": "ok",
        "path": str(target),
        "slug": slug,
        "staged": True,
        "next": "validate it, then install it to make it active",
    }


def resolve_skill_markdown(path: str) -> Path:
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
            for found in _skill_locations(slug):
                if found.is_file():
                    return found
            # Nothing on disk: name the staging path, so the "not found" says where it would go.
            return staging_root() / slug / "SKILL.md"
    return candidate


def _skill_locations(slug: str) -> list[Path]:
    """Where a skill of this slug could live, IN SEARCH ORDER: staging, native, then installed.

    Order is behaviour, not tidiness. A redraft of an already-installed skill is the copy the
    operator is validating; resolving to the installed one instead reports on the previous version
    and calls it current. The native root sits between them: a first-party package is the shipped
    source of truth, so a bare name resolves to it in preference to a stale installed copy.
    """

    from core.native_skill_library import native_skills_root

    locations = [staging_root() / slug / "SKILL.md"]
    locations.append(native_skills_root() / slug / "SKILL.md")
    # Installed skills sit at plugins/<plugin_id>/skills/<slug>/SKILL.md.
    with contextlib.suppress(OSError):
        locations.extend(sorted((plugins_root() / "plugins").glob(f"*/skills/{slug}/SKILL.md")))
    return locations


def validate_skill(path: str) -> dict[str, Any]:
    """Parse with the REAL loader and report what would stop this skill working.

    "It parsed" is the easy half and the less useful one. A skill with no description never ranks,
    and a skill naming a tool that does not exist will narrow the model to nothing on the turn it
    matches — both parse perfectly.
    """

    candidate = resolve_skill_markdown(path)
    if not candidate.is_file():
        return {"status": "not_found", "reason": f"no SKILL.md at {candidate}", "path": str(candidate)}

    skill = parse_skill(candidate)
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
            unknown_tools = []
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
    # loaded rather than a paraphrase of it. Read after validation, never reconstructed.
    try:
        preview_markdown = candidate.read_text(encoding="utf-8")
    except OSError:
        preview_markdown = ""

    return {
        "status": "ok" if not problems else "invalid",
        "path": str(candidate),
        "name": skill.name,
        "description": skill.description,
        "body_characters": len(skill.body),
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


def install_skill(path: str, *, plugin_id: str = "local-skills", overwrite: bool = False) -> dict[str, Any]:
    """Move a validated skill into the active plugins tree, where the loader will find it.

    Re-validates rather than trusting that `validate` was called: they are separate tool calls and a
    model can skip one. Installing something that can never rank is worse than refusing, because the
    operator is then told a capability exists that will never fire.
    """

    source = resolve_skill_markdown(path)
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
    destination_dir = plugins_root() / "plugins" / clean_plugin / "skills" / slug
    destination = destination_dir / "SKILL.md"
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
        collisions = {
            other: digest
            for other, digest in _installed_effective_digests(slug).items()
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
        shutil.copyfile(source, destination)
        _ensure_plugin_manifest(plugins_root() / "plugins" / clean_plugin, clean_plugin)
    except OSError as exc:
        return {"status": "error", "reason": f"{type(exc).__name__}: could not install the skill"}

    # Every activation is a version: the bytes just installed are snapshotted and recorded, so
    # the next edit of this skill can be rolled back to exactly this state. The record is
    # verified BY READING IT BACK — an install whose version did not persist is an error, not
    # an unversioned success (an activation the history cannot account for is unauditable).
    try:
        recorded = _record_version(destination_dir, source="install")
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
        "plugin_id": clean_plugin,
        "name": verdict.get("name"),
        "version": recorded["version"],
        "active": True,
        "note": "the loader picks this up on the next turn; no restart is needed",
    }


def list_skills(workspace: str = "") -> dict[str, Any]:
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

    staged = _scan(staging_root(), ("*/SKILL.md",))
    installed = _scan(plugins_root() / "plugins", ("*/skills/*/SKILL.md",))
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
        "staged_skills": staged,
        "installed_skills": installed,
        "total": len(workspace_skills) + len(native) + len(staged) + len(installed),
    }
