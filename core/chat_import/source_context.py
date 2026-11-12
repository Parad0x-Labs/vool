"""Bounded source context, collected only for an explicit working-context review.

Configuration projection decodes allowlisted values. Credential fields and
environment references are never resolved. Nothing here writes or executes.
"""
from __future__ import annotations

import contextlib
import json
import os
import re
import stat
from pathlib import Path
from typing import Any

from core.chat_import.model import ImportedChat

MAX_FILE = 1 << 20
MAX_TEXT = 20_000
MAX_ITEMS = 100
_SERVER = {"command": True, "args": [True], "cwd": True, "url": True}
_JOB = {"id": True, "name": True, "prompt": True, "next_run_at": True,
        "schedule": {"kind": True, "expr": True, "minutes": True, "run_at": True,
                     "at": True, "everyMs": True, "tz": True},
        "payload": {"kind": True, "text": True, "message": True}, "agentId": True}
_CONFIG = {"model": {"@scalar": True, "default": True, "provider": True}, "terminal": {"cwd": True},
           "providers": {"*": False}, "models": {"providers": {"*": False}},
           "mcpServers": {"*": _SERVER}, "mcp_servers": {"*": _SERVER},
           "agents": {"defaults": {"workspace": True, "model": {"primary": True, "fallbacks": [True]}}, "list": [{"id": True, "workspace": True}]},
           "version": True, "jobs": [_JOB]}


def project_json(raw: bytes, schema: Any) -> Any:
    """Parse structure while skipping unselected scalar values without decoding them."""
    if len(raw) > MAX_FILE:
        raise ValueError("context file too large")
    index = 0
    nodes = 0

    def whitespace():
        nonlocal index
        while index < len(raw) and raw[index] in b" \r\n\t":
            index += 1

    def string(decode: bool):
        nonlocal index
        start = index
        if raw[index:index + 1] != b'"':
            raise ValueError("invalid configuration")
        index += 1
        while index < len(raw):
            char = raw[index]
            index += 1
            if char == 34:
                return json.loads(raw[start:index]) if decode else None
            if char == 92:
                escape = raw[index:index + 1]
                if escape == b'u':
                    if not re.fullmatch(rb"[0-9A-Fa-f]{4}", raw[index + 1:index + 5]):
                        raise ValueError("invalid configuration")
                    index += 5
                elif escape in (b'"', b'\\', b'/', b'b', b'f', b'n', b'r', b't'):
                    index += 1
                else:
                    raise ValueError("invalid configuration")
            elif char < 32:
                raise ValueError("invalid configuration")
        raise ValueError("invalid configuration")

    def value(shape: Any, depth: int = 0):
        nonlocal index, nodes
        nodes += 1
        if depth > 32 or nodes > 20_000:
            raise ValueError("configuration limit")
        whitespace()
        char = raw[index:index + 1]
        if char == b'{':
            index += 1
            result = {}
            whitespace()
            while raw[index:index + 1] != b'}':
                key = string(True)
                whitespace()
                if raw[index:index + 1] != b':':
                    raise ValueError("invalid configuration")
                index += 1
                child = shape.get(key, shape.get("*")) if isinstance(shape, dict) else None
                item = value(child, depth + 1)
                if child is not None:
                    result[key] = item
                whitespace()
                if raw[index:index + 1] == b'}':
                    break
                if raw[index:index + 1] != b',':
                    raise ValueError("invalid configuration")
                index += 1
                whitespace()
            index += 1
            return result if isinstance(shape, dict) else None
        if char == b'[':
            index += 1
            result = []
            whitespace()
            while raw[index:index + 1] != b']':
                item = value(shape[0] if isinstance(shape, list) else None, depth + 1)
                if isinstance(shape, list):
                    result.append(item)
                whitespace()
                if raw[index:index + 1] == b']':
                    break
                if raw[index:index + 1] != b',':
                    raise ValueError("invalid configuration")
                index += 1
            index += 1
            return result if isinstance(shape, list) else None
        if char == b'"':
            return string(shape is True or (isinstance(shape, dict) and shape.get("@scalar") is True))
        start = index
        while index < len(raw) and raw[index] not in b",]} \t\r\n":
            index += 1
        token = raw[start:index]
        if token not in (b"true", b"false", b"null") and not re.fullmatch(
                rb"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?", token):
            raise ValueError("invalid configuration")
        return json.loads(token) if shape is True else None

    result = value(schema)
    whitespace()
    if index != len(raw):
        raise ValueError("invalid configuration")
    return result


def _project_yaml(raw: bytes, schema: dict | None = None) -> dict:
    """Block-mapping subset used by the documented profile settings.

    Unknown branches (notably env/auth/headers) are skipped as bytes, not loaded
    into a general YAML object. Complex selected mappings require manual review.
    """
    import yaml

    result: dict = {}
    stack: list[tuple[int, dict, dict]] = [(-1, _CONFIG if schema is None else schema, result)]
    for line in raw.splitlines():
        match = re.fullmatch(rb"( *)([A-Za-z0-9_.-]+):[ \t]*(.*)", line)
        if not match:
            continue
        indent, key_raw, tail = match.groups()
        level = len(indent)
        while len(stack) > 1 and stack[-1][0] >= level:
            stack.pop()
        shape, target = stack[-1][1:]
        key = key_raw.decode("ascii")
        child = shape.get(key, shape.get("*"))
        if isinstance(child, dict) and child.get("@scalar") and tail.strip():
            parsed = yaml.safe_load(tail)
            if not isinstance(parsed, str):
                raise ValueError("unsupported context mapping")
            target[key] = parsed
        elif isinstance(child, dict):
            if tail.strip() and not tail.lstrip().startswith(b"#"):
                raise ValueError("unsupported context mapping")
            nested: dict = {}
            target[key] = nested
            stack.append((level, child, nested))
        elif child is False:
            target[key] = None
            stack.append((level, {}, {}))
        elif child is True or isinstance(child, list):
            parsed = yaml.safe_load(tail)
            if isinstance(parsed, (str, int, float, bool)) or (
                    isinstance(child, list) and isinstance(parsed, list)
                    and all(isinstance(item, str) for item in parsed)):
                target[key] = parsed
        else:
            stack.append((level, {}, {}))
    return result


def _read(root: Path, relative: str) -> bytes | None:
    """Read regular files through directory fds; reject links at every component."""
    parts = Path(relative).parts
    if not parts or any(part in {"", ".", ".."} for part in parts) or Path(relative).is_absolute():
        raise ValueError("unsafe context path")
    descriptors = []
    try:
        directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0))
        descriptors.append(directory)
        for part in parts[:-1]:
            directory = os.open(part, os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0), dir_fd=directory)
            descriptors.append(directory)
        descriptor = os.open(parts[-1], os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                             | getattr(os, "O_NONBLOCK", 0), dir_fd=directory)
        descriptors.append(descriptor)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_FILE:
            raise ValueError("context file limit")
        chunks = []
        total = 0
        while chunk := os.read(descriptor, min(65536, MAX_FILE + 1 - total)):
            chunks.append(chunk)
            total += len(chunk)
            if total > MAX_FILE:
                raise ValueError("context file limit")
        return b"".join(chunks)
    except FileNotFoundError:
        return None
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def clean_text(value: Any) -> str:
    from core.session_portability.redaction import RedactionReport, scrub_secret_text

    if not isinstance(value, str) or not value.strip():
        return ""
    if len(value) > MAX_TEXT:
        raise ValueError("context text limit")
    return scrub_secret_text(value.strip(), RedactionReport(), residue_marker="[hidden]")


def export_context(messages: list[dict]) -> dict:
    result: dict = {"instructions": [], "memories": [], "models": [], "notes": [], "file_references": []}
    for message in messages:
        content = message.get("content")
        if isinstance(content, dict) and content.get("content_type") == "user_editable_context":
            for key, category in (("user_instructions", "instructions"), ("user_profile", "memories")):
                text = content.get(key)
                if isinstance(text, str) and text.strip():
                    result[category].append({"text": text, "source": key, "scope": "chat"})
        if isinstance(content, dict) and content.get("content_type") == "model_editable_context":
            text = content.get("model_set_context")
            if isinstance(text, str) and text.strip():
                result["notes"].append({"text": text, "source": "conversation_context", "scope": "chat"})
        result["file_references"].extend(tool_paths(message.get("tool_calls")))
        metadata = message.get("metadata")
        model = metadata.get("model_slug") if isinstance(metadata, dict) else None
        if isinstance(model, str) and model not in result["models"]:
            result["models"].append(model)
    return result


def review(chat: ImportedChat, *, root: str, additional_instructions: str = "", private: bool = False,
           old_root: str = "", additional_memories: str = "") -> dict:
    """Return a sanitized, inert review. Source metadata cannot grant a folder."""
    from core.project_store import validate_project_root
    from core.secret_redaction import contains_secret

    if contains_secret(root) or contains_secret(old_root):
        raise ValueError("context path contains a secret")
    valid, resolved = validate_project_root(root)
    if not valid:
        raise ValueError(resolved)
    workspace = Path(resolved)
    info = workspace.stat()
    context = chat.context
    result: dict = {"root": resolved, "root_identity": [info.st_dev, info.st_ino],
                    "name": clean_text(workspace.name), "private": bool(private or context.get("private")),
                    "instructions": [], "memories": [], "skills": [], "tools": [], "schedules": [],
                    "files": [], "models": [], "providers": [], "warnings": [], "notices": [], "notes": []}
    budget = 0

    def text_item(text: Any, category: str, source: str, scope: str = "chat"):
        nonlocal budget
        cleaned = clean_text(text)
        budget += len(cleaned)
        if budget > 500_000 or len(result[category]) >= MAX_ITEMS:
            raise ValueError("context total limit")
        if cleaned:
            result[category].append({"text": cleaned, "source": source, "scope": scope})

    for category in ("instructions", "memories", "notes"):
        for item in context.get(category, []):
            text_item(item.get("text"), category, str(item.get("source") or chat.source_format),
                      str(item.get("scope") or "chat"))
    text_item(additional_instructions, "instructions", "review", "chat")
    text_item(additional_memories, "memories", "review", "chat")

    roots = [(workspace, "project")]
    profile = context.get("profile_root")
    if profile:
        roots.append((Path(str(profile)), "chat"))
    for source_root, scope in roots:
        instructions = ["AGENTS.md", "CLAUDE.md", "SOUL.md", ".cursorrules"]
        if chat.source_format == "hermes_state_db":
            # Global identity is independent of project-file precedence. Never
            # traverse parents outside the explicitly reviewed folder grant.
            instructions = ["SOUL.md"] if scope == "chat" else [
                ".hermes.md", "HERMES.md", "AGENTS.override.md", "AGENTS.md", "CLAUDE.md", ".cursorrules"]
        for filename in instructions:
            try:
                raw = _read(source_root, filename)
                if raw is not None:
                    text_item(raw.decode("utf-8"), "instructions", filename, scope)
                    if chat.source_format == "hermes_state_db" and scope == "project":
                        break
            except (OSError, UnicodeError, ValueError):
                result["warnings"].append("context_unreadable")
                # A higher-priority file exists but is unreadable. Do not
                # silently substitute a contradictory lower-priority file.
                if chat.source_format == "hermes_state_db" and scope == "project":
                    break
        for filename, category in (("MEMORY.md", "memories"),
                                   ("USER.md", "memories"), ("memories/MEMORY.md", "memories"),
                                   ("memories/USER.md", "memories")):
            try:
                raw = _read(source_root, filename)
                if raw:
                    text_item(raw.decode("utf-8"), category, filename, scope)
            except (OSError, UnicodeError, ValueError):
                result["warnings"].append("context_unreadable")
        _daily_memories(source_root, scope, result, text_item)
        _editor_rules(source_root, scope, result, text_item)
        for filename in ("config.yaml", "openclaw.json", ".cursor/mcp.json", "mcp.json"):
            try:
                raw = _read(source_root, filename)
                if not raw:
                    continue
                config = _project_yaml(raw) if filename.endswith(".yaml") else project_json(raw, _CONFIG)
                _config_items(config, result, filename)
            except (OSError, ValueError):
                result["warnings"].append("context_unreadable")
        for folder in ("skills", ".cursor/skills"):
            directory = source_root / folder
            if directory.is_dir() and not directory.is_symlink():
                for path in sorted(directory.iterdir())[:MAX_ITEMS]:
                    if path.is_dir() and not path.is_symlink():
                        try:
                            raw = _read(source_root, f"{folder}/{path.name}/SKILL.md")
                            if raw:
                                result["skills"].append({"name": clean_text(path.name), "body": clean_text(raw.decode("utf-8")),
                                                         "source": f"{folder}/{path.name}/SKILL.md"})
                        except (OSError, UnicodeError, ValueError):
                            result["warnings"].append("context_unreadable")
        try:
            raw = _read(source_root, "cron/jobs.json")
            if raw:
                shape = [_JOB] if raw.lstrip().startswith(b"[") else {"version": True, "jobs": [_JOB]}
                jobs = project_json(raw, shape)
                jobs = jobs if isinstance(jobs, list) else jobs.get("jobs", [])
                for job in jobs[:MAX_ITEMS]:
                    prompt = job.get("prompt") or (job.get("payload") or {}).get("text") or (job.get("payload") or {}).get("message")
                    if prompt:
                        result["schedules"].append({"note": clean_text(prompt), "schedule": job.get("schedule") or {},
                                                    "name": clean_text(job.get("name") or ""), "source": "cron/jobs.json"})
        except (OSError, ValueError):
            result["warnings"].append("context_unreadable")
    for source_root, _scope in roots:
        _shared_schedules(source_root, result)
    result["models"] = list(dict.fromkeys([*result["models"], *(clean_text(x) for x in context.get("models", []))]))
    references = [*context.get("file_references", []), *(name for message in chat.messages for name in message.attachment_names)]
    for item in [*result["instructions"], *result["memories"], *result["notes"]]:
        references.extend(re.findall(r"`([^`\n]+[./\\][^`\n]*)`", item["text"]))
    for message in chat.messages:
        references.extend(re.findall(r"`([^`\n]+[./\\][^`\n]*)`", message.text))
    old_root = old_root or str(context.get("workspace_hint") or "")
    for reference in dict.fromkeys(references):
        if len(result["files"]) >= MAX_ITEMS:
            raise ValueError("file reference limit")
        if contains_secret(reference):
            result["files"].append({"source": "[hidden]", "path": "", "status": "refused"})
            continue
        from urllib.parse import unquote, urlsplit

        if reference.startswith("file:"):
            parsed = urlsplit(reference)
            if parsed.netloc not in {"", "localhost"}:
                result["files"].append({"source": reference, "path": "", "status": "outside"})
                continue
            reference_path = unquote(parsed.path)
        else:
            reference_path = reference
        if len(reference_path) > 4096 or "\0" in reference_path:
            result["files"].append({"source": reference[:MAX_TEXT], "path": "", "status": "refused"})
            continue
        path = Path(reference_path)
        if path.is_absolute() and old_root:
            with contextlib.suppress(ValueError):
                path = workspace / path.relative_to(old_root)
        elif not path.is_absolute():
            path = workspace / path
        try:
            path = path.resolve()
        except (OSError, ValueError, RuntimeError):
            result["files"].append({"source": reference, "path": "", "status": "refused"})
            continue
        allowed = path.is_relative_to(workspace) and path.name not in {".env", "auth.json", "credentials.json"}
        result["files"].append({"source": reference, "path": str(path),
                                "status": "outside" if not allowed else "exists" if path.is_file() else "missing"})
    result["warnings"] = sorted(set(result["warnings"]))
    result["notices"] = sorted(set(result["notices"]))
    # Scrub every selected configuration leaf, including timing strings. The
    # serialized bound also covers tools, paths and jobs, not just prose files.
    def scrub(value):
        if isinstance(value, str):
            return clean_text(value)
        if isinstance(value, dict):
            return {key: scrub(item) for key, item in value.items()}
        if isinstance(value, list):
            return [scrub(item) for item in value]
        return value

    result = scrub(result)
    if len(json.dumps(result, ensure_ascii=False).encode()) > 1 << 20:
        raise ValueError("context total limit")
    return result


def _config_items(config: dict, result: dict, source: str) -> None:
    from core.secret_redaction import contains_secret

    for mapping in (config.get("mcpServers"), config.get("mcp_servers")):
        if not isinstance(mapping, dict):
            continue
        for name, server in list(mapping.items())[:MAX_ITEMS]:
            if not isinstance(server, dict):
                continue
            command, args = server.get("command"), server.get("args") or []
            if isinstance(command, str) and isinstance(args, list) and all(isinstance(x, str) for x in args):
                if any(contains_secret(x) or "${" in x for x in [str(name), command, *args]):
                    result["warnings"].append("credentials_omitted")
                    continue
                result["tools"].append({"name": clean_text(name), "command": command, "args": args, "source": source})
            elif server.get("url"):
                result["warnings"].append("tool_transport_unsupported")
    model = config.get("model") or {}
    if isinstance(model, str) and not contains_secret(model):
        result["models"].append(model)
    if isinstance(model, dict):
        if model.get("default") and not contains_secret(str(model["default"])):
            result["models"].append(clean_text(model["default"]))
        if model.get("provider") and not contains_secret(str(model["provider"])):
            result["providers"].append(clean_text(model["provider"]))
    default_model = ((config.get("agents") or {}).get("defaults") or {}).get("model") or {}
    if isinstance(default_model, dict):
        result["models"].extend(value for value in [default_model.get("primary"), *default_model.get("fallbacks", [])]
                                if isinstance(value, str) and not contains_secret(value))
    for providers in (config.get("providers"), (config.get("models") or {}).get("providers")):
        if isinstance(providers, dict):
            result["providers"].extend(clean_text(name) for name in providers if not contains_secret(str(name)))


def tool_paths(value: Any) -> list[str]:
    """Keep inert path arguments; never copy commands, environment or tool output."""
    paths: list[str] = []
    if isinstance(value, str):
        try:
            value = project_json(value.encode(), [{"function": {"arguments": True}, "arguments": True,
                                                   "input": {"path": True, "file_path": True}}])
        except ValueError:
            return []
    for call in value if isinstance(value, list) else []:
        if not isinstance(call, dict):
            continue
        arguments = (call.get("function") or {}).get("arguments") if isinstance(call.get("function"), dict) else None
        arguments = arguments or call.get("arguments") or call.get("input")
        if isinstance(arguments, str):
            try:
                arguments = project_json(arguments.encode(), {"path": True, "file_path": True, "target_file": True})
            except ValueError:
                continue
        if isinstance(arguments, dict):
            paths.extend(text for key, text in arguments.items() if key in {"path", "file_path", "target_file"}
                         and isinstance(text, str) and len(text) <= MAX_TEXT)
    return paths[:MAX_ITEMS]


def _daily_memories(root: Path, scope: str, result: dict, text_item) -> None:
    directory = root / "memory"
    if not directory.exists():
        return
    try:
        if directory.is_symlink():
            raise ValueError("linked memory directory")
        # Names only until a documented daily-note name has been selected.
        names = []
        with os.scandir(directory) as entries:
            for entry in entries:
                if re.fullmatch(r"\d{4}-\d{2}-\d{2}\.md", entry.name):
                    names.append(entry.name)
                    if len(names) > MAX_ITEMS:
                        result["warnings"].append("context_limit")
                        break
        for name in sorted(names[:MAX_ITEMS]):
            try:
                raw = _read(root, "memory/" + name)
                if raw:
                    text_item(raw.decode("utf-8"), "memories", "memory/" + name, scope)
            except (OSError, UnicodeError, ValueError):
                result["warnings"].append("context_unreadable")
    except (OSError, ValueError):
        result["warnings"].append("context_unreadable")


def _editor_rules(root: Path, scope: str, result: dict, text_item) -> None:
    # Nested rule folders apply only to their own subtree. Retain their scope
    # in an inert draft; the destination has no equivalent conditional owner.
    excluded = {".git", "node_modules", ".venv", "venv", "__pycache__", "state", "agents", "memories", "memory", "skills"}
    walked = 0
    def unreadable(_error):
        result["warnings"].append("context_unreadable")

    for directory, children, filenames in os.walk(root, followlinks=False, onerror=unreadable):
        walked += 1
        relative = Path(directory).relative_to(root)
        if walked > 256:
            result["warnings"].append("context_limit")
            break
        if len(relative.parts) >= 10 and children:
            result["warnings"].append("context_limit")
            children[:] = []
        linked = [name for name in children if name not in excluded and (Path(directory) / name).is_symlink()]
        if linked:
            result["warnings"].append("context_unreadable")
        children[:] = sorted(name for name in children if name not in excluded and name not in linked)
        if ".cursor" not in relative.parts:
            continue
        cursor_index = relative.parts.index(".cursor")
        if relative.parts[cursor_index:cursor_index + 2] != (".cursor", "rules"):
            continue
        for filename in sorted(filenames):
            if not filename.endswith(".mdc"):
                continue
            source = str(relative / filename)
            try:
                raw = _read(root, source)
                if not raw:
                    continue
                text = raw.decode("utf-8")
                metadata, body = {}, text
                if text.startswith("---\n"):
                    front, separator, body = text[4:].partition("\n---\n")
                    if not separator:
                        raise ValueError("unterminated rule metadata")
                    metadata = _project_yaml(front.encode(), {"alwaysApply": True, "globs": True, "description": True})
                # Only a root rule explicitly marked always applies globally.
                # Unknown/conditional/manual semantics are never broadened.
                if cursor_index == 0 and metadata.get("alwaysApply") is True:
                    text_item(body, "instructions", source, scope)
                else:
                    if len(result["skills"]) >= MAX_ITEMS:
                        raise ValueError("context item limit")
                    result["skills"].append({"name": clean_text(source), "body": clean_text(source + "\n\n" + text), "source": source})
                    result["notices"].append("conditional_rules_staged")
            except (OSError, UnicodeError, ValueError):
                result["warnings"].append("context_unreadable")


def _shared_schedules(root: Path, result: dict) -> None:
    """Verified shared-state v28 job projection; no credentials or delivery authority."""
    import sqlite3

    from core.chat_import.sqlite_snapshot import SnapshotError, snapshot_connection, table_columns

    database = root / "state" / "openclaw.sqlite"
    if not database.exists():
        return
    try:
        if database.is_symlink() or database.parent.is_symlink():
            raise ValueError("linked source store")
        with snapshot_connection(database, max_bytes=256 << 20, work_dir=None) as connection:
            if connection.execute("PRAGMA user_version").fetchone()[0] != 28:
                raise ValueError("unknown source state version")
            if not {"job_json", "store_key", "job_id"} <= table_columns(connection, "cron_jobs"):
                raise ValueError("unknown source schedule schema")
            connection.text_factory = bytes
            # Project only job definitions for the default partition. An
            # alternate partition must be exported explicitly, never guessed.
            partition = str(root / "cron" / "jobs.json")
            if connection.execute("SELECT 1 FROM cron_jobs WHERE store_key IS NULL OR store_key != ? LIMIT 1", (partition,)).fetchone():
                result["warnings"].append("schedule_partition_unsupported")
            for raw, in connection.execute(
                    "SELECT job_json FROM cron_jobs WHERE store_key=? ORDER BY job_id LIMIT ?",
                    (partition, MAX_ITEMS + 1)):
                if len(result["schedules"]) >= MAX_ITEMS:
                    raise ValueError("schedule limit")
                job = project_json(raw, _JOB)
                prompt = (job.get("payload") or {}).get("text") or (job.get("payload") or {}).get("message") or job.get("prompt")
                if prompt:
                    result["schedules"].append({"note": clean_text(prompt), "schedule": job.get("schedule") or {},
                                                "name": clean_text(job.get("name") or ""), "source": "shared_state"})
    except (OSError, sqlite3.DatabaseError, SnapshotError, ValueError):
        result["warnings"].append("schedule_store_unreadable")
