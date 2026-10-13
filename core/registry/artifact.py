"""Complete native packages transported as inert, bounded JSON; no archive extraction."""
from __future__ import annotations

import base64
import hashlib
import re
from pathlib import Path, PurePosixPath

from core.plugin_package import DIGEST_SCHEMA, MAX_FILES, capture_package
from core.registry.categories import is_listed
from core.registry.codec import (
    COMMIT,
    HEX,
    IDENTITY,
    MAX_PACKAGE,
    VERSION,
    RegistryError,
    canonical,
    decode,
    digest,
    require,
    unbase64,
)

SCHEMA = "vool.registry.artifact.v1"
_PATH = re.compile(r"^[A-Za-z0-9._/-]+$")
_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_PRIVATE = {".git", ".ssh", ".aws", ".gnupg", "credentials.json", "id_rsa", "id_ed25519"}
_DEVICES = {"con", "prn", "aux", "nul"} | {f"{prefix}{number}" for prefix in ("com", "lpt") for number in range(1, 10)}


def native_identity(package_id: str) -> str:
    """One stable native identity for each publisher/package pair."""
    return "registry-" + hashlib.sha256(package_id.encode("utf-8")).hexdigest()[:24]


def path_name(value: str) -> str:
    require(isinstance(value, str) and 0 < len(value) <= 512 and bool(_PATH.fullmatch(value)),
            "invalid package path")
    path = PurePosixPath(value)
    require(not path.is_absolute() and str(path) == value and
            all(part not in {".", ".."} and len(part) <= 200 for part in path.parts),
            "ambiguous package path")
    require(all(not part.endswith(".") and part.split(".")[0].casefold() not in _DEVICES for part in path.parts),
            "path is ambiguous on supported platforms")
    require(not any(part.casefold() in _PRIVATE or part.casefold().startswith(".env") or
                    part.casefold().endswith((".pem", ".key", ".p12", ".pfx"))
                    for part in path.parts), "private credential path refused")
    return value


def metadata(value: dict) -> dict:
    require(isinstance(value, dict) and set(value) - {"title"} == {
        "id", "kind", "version", "summary", "category", "subcategory", "license", "source"},
        "invalid package metadata fields")
    if "title" in value:
        # Optional display name; plain text only.
        title = value["title"]
        require(isinstance(title, str) and title == title.strip() and 0 < len(title) <= 60 and
                all(ord(char) >= 32 and char not in "<>" for char in title), "invalid title")
    parts = str(value["id"]).split("/")
    require(len(parts) == 2 and all(IDENTITY.fullmatch(part) for part in parts), "invalid stable package id")
    require(parts[0] not in {"vool", "parad0x-labs", "official", "native"}, "reserved publisher namespace")
    require(value["kind"] in {"skill", "plugin"} and isinstance(value["version"], str) and
            len(value["version"]) <= 64 and bool(VERSION.fullmatch(value["version"])),
            "invalid package kind or version")
    for field, limit in (("summary", 500), ("license", 80)):
        text = value[field]
        require(isinstance(text, str) and text.strip() and len(text) <= limit and
                all(ord(char) >= 32 for char in text), f"invalid {field}")
    require(is_listed(value["category"], value["subcategory"]),
            "category and subcategory must come from the registry category list")
    source = value["source"]
    require(isinstance(source, dict), "invalid source provenance")
    if set(source) == {"upload_id", "namespace"}:
        require(bool(re.fullmatch(r"[a-f0-9]{32}", str(source["upload_id"]))) and source["namespace"] == parts[0],
                "invalid private-upload provenance")
    else:
        require(set(source) == {"repository", "commit", "path"} and
                bool(_REPO.fullmatch(str(source["repository"]))) and
                bool(COMMIT.fullmatch(str(source["commit"]))), "source must name an exact GitHub commit")
        path_name(source["path"])
    return value


def pack(root: Path, info: dict, scratch: Path) -> bytes:
    """Capture once into an owned scratch snapshot; never execute submitted files."""
    from core.plugin_package import execution_directory

    metadata(info)
    # Reject credential filenames before capture reads their bytes. No exclusions:
    # a refused path requires the author to fix the submitted distribution.
    import os

    count = 0
    for directory, folders, files in os.walk(root, followlinks=False):
        for name in folders + files:
            count += 1
            require(count <= MAX_FILES, "package file limit exceeded")
            path_name((Path(directory) / name).relative_to(root).as_posix())
    with execution_directory(scratch) as owned:
        snapshot = owned / "package"
        identity = capture_package(root, destination=snapshot)
        entries = []
        for entry in identity.entries:
            item = dict(entry)
            if item["type"] == "file":
                item["content"] = base64.b64encode((snapshot / item["path"]).read_bytes()).decode("ascii")
            entries.append(item)
        result = canonical({"schema": SCHEMA, "metadata": info, "native_digest": identity.digest,
                            "entries": entries})
        inspect(result)
        return result


def inspect(raw: bytes) -> dict:
    value = decode(raw)
    require(isinstance(value, dict) and set(value) == {"schema", "metadata", "native_digest", "entries"}
            and value["schema"] == SCHEMA, "unknown artifact format")
    require(canonical(value) == raw, "artifact must use canonical encoding")
    metadata(value["metadata"])
    require(bool(HEX.fullmatch(str(value["native_digest"]))), "invalid native digest")
    entries = value["entries"]
    require(isinstance(entries, list) and 0 < len(entries) <= MAX_FILES, "package file limit exceeded")
    seen, table, content, total = {}, [], {}, 0
    for item in entries:
        require(isinstance(item, dict), "invalid package entry")
        name = path_name(item.get("path"))
        folded = name.casefold()
        require(folded not in seen, "duplicate or case-colliding path")
        parent = PurePosixPath(name).parent.as_posix()
        require(parent == "." or seen.get(parent.casefold()) == "directory", "missing parent directory")
        if item.get("type") == "directory":
            require(set(item) == {"path", "type"}, "unexpected directory data")
            table.append(dict(item))
        else:
            require(item.get("type") == "file" and set(item) == {
                "path", "type", "bytes", "sha256", "executable", "content"}, "unexpected file data")
            data = unbase64(item["content"])
            total += len(data)
            require(total <= MAX_PACKAGE and type(item["bytes"]) is int and item["bytes"] == len(data),
                    "package byte limit or length mismatch")
            require(type(item["executable"]) is int and item["executable"] >= 0 and
                    item["executable"] & ~0o111 == 0, "invalid executable permissions")
            require(hashlib.sha256(data).hexdigest() == item["sha256"], "file checksum mismatch")
            table.append({key: val for key, val in item.items() if key != "content"})
            content[name] = data
        seen[folded] = item["type"]
    require(digest({"schema": DIGEST_SCHEMA, "entries": table}) == value["native_digest"],
            "complete native package digest mismatch")
    manifest = decode(content.get(".codex-plugin/plugin.json", b""), limit=1024 * 1024)
    require(isinstance(manifest, dict) and bool(IDENTITY.fullmatch(str(manifest.get("name", "")))) and
            manifest.get("version") == value["metadata"]["version"], "native manifest identity mismatch")
    require(manifest["name"] == native_identity(value["metadata"]["id"]),
            "native manifest identity is not bound to the package id")
    dependencies = manifest.get("requires", [])
    require(isinstance(dependencies, list) and len(dependencies) <= 64 and
            all(isinstance(item, str) and bool(IDENTITY.fullmatch(item)) for item in dependencies) and
            len(set(dependencies)) == len(dependencies), "invalid native dependency declarations")
    require("LICENSE" in content and bool(content["LICENSE"].strip()), "package licence is missing")
    skills = [name for name in content if name.startswith("skills/") and name.endswith("/SKILL.md")]
    require(bool(skills) if value["metadata"]["kind"] == "skill" else True, "skill package has no SKILL.md")
    value["manifest"] = manifest
    value["files"] = content
    value["sha256"] = hashlib.sha256(raw).hexdigest()
    value["raw_length"] = len(raw)
    return value


def materialize(raw: bytes, destination: Path) -> dict:
    """Write only into a new private directory owned by the caller, then re-hash."""
    value = inspect(raw)
    destination.mkdir(mode=0o700)  # refuse existing roots, including links
    try:
        for item in value["entries"]:
            target = destination / item["path"]
            if item["type"] == "directory":
                target.mkdir(mode=0o700)
            else:
                with target.open("xb") as handle:
                    handle.write(value["files"][item["path"]])
                target.chmod(0o444 | item["executable"])
        require(capture_package(destination).digest == value["native_digest"], "materialized package changed")
        return value
    except (OSError, ValueError) as exc:
        raise RegistryError(f"package materialization refused ({type(exc).__name__})") from None
