"""Public repository projection of already listed skills, never private intake or findings."""
from __future__ import annotations

import hashlib
from pathlib import Path

from core.registry.codec import HEX, canonical, decode, require, version_key
from registry_service.mac_authority import _write


def publish(directory: Path, entries: list, revoked: list, marks: dict, *, artifact, presentation: dict) -> None:
    require(isinstance(presentation, dict) and set(presentation) == {"marks", "metrics"} and
            isinstance(presentation["metrics"], list) and len(presentation["metrics"]) <= 4096, "invalid public presentation")
    metrics = {}
    for item in presentation["metrics"]:
        require(isinstance(item, dict) and set(item) == {"id", "stars", "download_requests"} and
                isinstance(item["id"], str) and all(type(item[field]) is int and 0 <= item[field] <= 10**12
                for field in ("stars", "download_requests")), "invalid public metrics")
        metrics[item["id"]] = {field: item[field] for field in ("stars", "download_requests")}
    require(directory.is_dir() and not directory.is_symlink(), "invalid publication directory")
    packages = directory / "packages"
    require(not packages.is_symlink(), "invalid publication package directory")
    packages.mkdir(exist_ok=True)
    path = directory / "registry.json"
    previous = decode(path.read_bytes()) if path.exists() else {"entries": []}
    old = {item["sha256"] for item in previous["entries"]}
    previous_rows = {item["id"]: item for item in previous["entries"]}
    require(all(isinstance(sha, str) and HEX.fullmatch(sha) for sha in old), "invalid previous publication")
    latest = {}
    for envelope in entries:
        entry = envelope["signed"]
        if entry["artifact_sha256"] in revoked:
            continue
        info = entry["metadata"]
        prior = latest.get(info["id"])
        if prior is None or version_key(info["version"]) > version_key(prior["metadata"]["version"]):
            latest[info["id"]] = entry
    curated = marks.get("curation", {})
    rows = []
    for package_id, entry in sorted(latest.items()):
        sha = entry["artifact_sha256"]
        file = packages / (sha + ".json")
        require(not file.is_symlink(), "invalid public artifact")
        raw = file.read_bytes() if file.exists() else artifact(sha)
        require(hashlib.sha256(raw).hexdigest() == sha and len(raw) == entry["artifact_length"],
                "public artifact differs from admission")
        require(not file.is_symlink() and (not file.exists() or file.read_bytes() == raw), "public artifact was changed")
        if not file.exists():
            _write(file, raw)
        rows.append({**entry["metadata"], "sha256": sha, "path": "packages/" + sha + ".json", "targets": entry["targets"],
                     "tested": package_id + "@" + sha in marks["tested"],
                     "editors_pick": package_id in curated.get("editors_picks", []),
                     "featured": package_id in curated.get("featured", []),
                     **metrics.get(package_id, {key: previous_rows[package_id][key] for key in ("stars", "download_requests")
                                               if key in previous_rows.get(package_id, {})})})
    value = canonical({"schema": "vool.registry.directory.v1", "entries": rows})
    if not path.exists() or path.read_bytes() != value:
        _write(path, value)
    for sha in old - {item["sha256"] for item in rows}:
        file = packages / (sha + ".json")
        require(not file.is_symlink(), "invalid previous public artifact")
        if file.exists():
            require(hashlib.sha256(file.read_bytes()).hexdigest() == sha, "previous public artifact was changed")
            file.unlink()

    def plain(text):
        text = " ".join(str(text).split())
        return "".join("\\" + char if char in "\\`*_{}[]<>|#" else char for char in text)

    lines = ["# Skill Codex", "", "Only skills on the current signed list appear here. Open the Skill Codex in VOOL to inspect and install.",
             "", "Automatic compatibility and known-threat checks can miss malicious behavior. Community skills are provided as is, "
             "without warranty; review their files and licence before use.", "", "| Skill | Version | Category | Website stars | Download requests | Selection |",
             "| --- | --- | --- | ---: | ---: | --- |"]
    for row in rows:
        selection = "Editor's pick" if row["editors_pick"] else "Featured" if row["featured"] else ""
        lines.append("| " + " | ".join(plain(item) for item in (row["id"], row["version"],
            row["category"] + " / " + row["subcategory"], row.get("stars", "Unavailable"),
            row.get("download_requests", "Unavailable"), selection)) + " |")
    text = ("\n".join(lines) + "\n").encode()
    page = directory / "CATALOG.md"
    if not page.exists() or page.read_bytes() != text:
        _write(page, text)
