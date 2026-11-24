"""Complete native packages transported as inert, bounded JSON; no archive extraction."""
from __future__ import annotations

import base64
import hashlib
import json
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
from core.registry.requirements import check_languages, check_needs

SCHEMA = "vool.registry.artifact.v1"
# This cannot collide with a package path: path_name() refuses '@'.
REVIEW_METADATA = "@metadata.json"
_PATH = re.compile(r"^[A-Za-z0-9._/-]+$")
_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_PRIVATE = {".git", ".ssh", ".aws", ".gnupg", "credentials.json", "id_rsa", "id_ed25519"}
_DEVICES = {"con", "prn", "aux", "nul"} | {f"{prefix}{number}" for prefix in ("com", "lpt") for number in range(1, 10)}


def review_files(artifact: dict) -> dict[str, bytes]:
    """Every public author field gets the same checks as the package instructions."""
    return {**artifact["files"], REVIEW_METADATA: metadata_text(artifact).encode("utf-8")}


def metadata_text(artifact: dict, *, checksums: bool = True) -> str:
    info = dict(artifact["metadata"])
    if not checksums:
        # inspect() proved these exact hashes against decoded PNG bytes. They are public content
        # identifiers, not secrets. No prose field or unverified descriptor gets this exception.
        for kind in artifact.get("pictures", {}):
            info[kind] = {**info[kind], "sha256": "verified picture checksum"}
    return json.dumps(info, ensure_ascii=False, sort_keys=True)


def native_identity(package_id: str) -> str:
    """One stable native identity for each publisher/package pair."""
    return "registry-" + hashlib.sha256(package_id.encode("utf-8")).hexdigest()[:24]


def holds_plugin(metadata: dict, manifest: dict) -> bool:
    """Whether this package is a plugin with its access block: code and tools, not a skill's text."""
    return (metadata.get("kind") == "plugin" and isinstance(manifest, dict) and isinstance(manifest.get("vool"), dict)
            and "id" in manifest["vool"])


def keeps_own_id(metadata: dict, manifest: dict) -> bool:
    """Whether the package is a plugin published under its own id: the name part of the package id.

    A skill, and a plugin packaged the earlier way, is named ``native_identity(package id)``. A plugin
    published from VOOL 0.8 keeps the id its maker gave it, because its tools are named after it, and
    the package id must end in that same id so the listing and the installed plugin cannot differ.
    """
    return holds_plugin(metadata, manifest) and manifest.get("name") == str(metadata.get("id", "")).split("/")[-1]


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


def _field_fix(value, needed: set, optional: set) -> str:
    """Which metadata fields are missing and which are not known, for the author."""
    if not isinstance(value, dict):
        return "The metadata must be one JSON object."
    missing, extra = sorted(needed - set(value)), sorted(str(key)[:40] for key in set(value) - needed - optional)
    said = []
    if missing:
        said.append("Missing: " + ", ".join(missing) + ".")
    if extra:
        said.append("Not known, so take out: " + ", ".join(extra[:12]) + ".")
    said.append("Needed: " + ", ".join(sorted(needed)) + ". Optional: " + ", ".join(sorted(optional)) + ".")
    return " ".join(said)


def metadata(value: dict) -> dict:
    needed = {"id", "kind", "version", "summary", "category", "subcategory", "license", "source"}
    optional = {"title", "languages", "needs", "icon", "banner", "release_notes"}
    require(isinstance(value, dict) and set(value) - optional == needed, "invalid package metadata fields",
            _field_fix(value, needed, optional))
    # Optional in signed entries so earlier packages stay valid; new uploads must
    # declare languages (registry intake and the author tool enforce that).
    if "languages" in value:
        check_languages(value["languages"])
    if "needs" in value:
        check_needs(value["needs"])
    if "release_notes" in value:
        text = value["release_notes"]
        require(isinstance(text, str) and text == text.strip() and 0 < len(text) <= 2000 and
                all(ord(char) >= 32 and char not in "<>" for char in text), "invalid release notes",
                "release_notes is plain text of 1 to 2000 characters on one line: no < or >, no line breaks, "
                "no space at the start or end.")
    from core.registry.media import KINDS, reference

    for kind in KINDS:
        if kind in value:
            # Optional pictures inside this package; inspect() checks the bytes.
            reference(kind, value[kind])
    if "title" in value:
        # Optional display name; plain text only.
        title = value["title"]
        require(isinstance(title, str) and title == title.strip() and 0 < len(title) <= 60 and
                all(ord(char) >= 32 and char not in "<>" for char in title), "invalid title",
                "title is plain text of 1 to 60 characters: no < or >, no line breaks, no space at the start or end.")
    parts = str(value["id"]).split("/")
    require(len(parts) == 2 and all(IDENTITY.fullmatch(part) for part in parts), "invalid stable package id",
            "id is publisher/name. Each part starts with a lowercase letter or a digit and holds lowercase letters, "
            "digits and hyphens only, at most 64 characters: for example alice/meeting-notes.")
    require(parts[0] not in {"vool", "parad0x-labs", "official", "native"}, "reserved publisher namespace",
            "vool, parad0x-labs, official and native are kept back. Publish under your own public name.")
    require(value["kind"] in {"skill", "plugin"} and isinstance(value["version"], str) and
            len(value["version"]) <= 64 and bool(VERSION.fullmatch(value["version"])),
            "invalid package kind or version",
            'kind is "skill" or "plugin". version is three numbers such as 1.0.0, optionally with a lowercase '
            "ending such as 1.0.0-beta, at most 64 characters.")
    for field, limit in (("summary", 500), ("license", 80)):
        text = value[field]
        require(isinstance(text, str) and text.strip() and len(text) <= limit and
                all(ord(char) >= 32 for char in text), f"invalid {field}",
                f"{field} is plain text of 1 to {limit} characters on one line.")
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
    for field in ("title", "summary", "license", "release_notes"):
        require(not any(0xD800 <= ord(char) <= 0xDFFF for char in value.get(field, "")),
                "invalid " + field.replace("_", " "))
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
    require(manifest["name"] == native_identity(value["metadata"]["id"]) or keeps_own_id(value["metadata"], manifest),
            "native manifest identity is not bound to the package id",
            "For a plugin, the name part of the package id must be the plugin's own id: a plugin whose name and "
            "vool.id are parcel-desk is published as <your name>/parcel-desk. For a skill, make the package "
            "folder again with init, which writes this name itself.")
    dependencies = manifest.get("requires", [])
    require(isinstance(dependencies, list) and len(dependencies) <= 64 and
            all(isinstance(item, str) and bool(IDENTITY.fullmatch(item)) for item in dependencies) and
            len(set(dependencies)) == len(dependencies), "invalid native dependency declarations")
    require("LICENSE" in content and bool(content["LICENSE"].strip()), "package licence is missing")
    skills = [name for name in content if name.startswith("skills/") and name.endswith("/SKILL.md")]
    require(bool(skills) if value["metadata"]["kind"] == "skill" else True, "skill package has no SKILL.md")
    from core.registry.media import KINDS, describe

    pictures = {}
    for kind in KINDS:
        if kind in value["metadata"]:
            ref = value["metadata"][kind]
            require(ref["path"] in content, f"{kind} file is missing from the package")
            require(hashlib.sha256(content[ref["path"]]).hexdigest() == ref["sha256"], f"{kind} checksum does not match its file")
            pictures[kind] = describe(kind, ref["path"], content[ref["path"]])
    value["pictures"] = pictures
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
