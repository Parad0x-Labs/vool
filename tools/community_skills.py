"""Author SDK for native registry packages. No installs, scans-for-hire or remote writes."""
from __future__ import annotations

import argparse
import json
import re
import urllib.parse
from pathlib import Path

from core.plugin_package import PackageError, execution_directory
from core.registry.artifact import inspect, materialize, metadata, native_identity, pack
from core.registry.codec import RegistryError, canonical, decode, require


def initialize(destination: Path, info: dict, license_bytes: bytes) -> None:
    metadata(info)
    require(bool(license_bytes.strip()) and len(license_bytes) <= 1024 * 1024, "licence text required")
    destination.mkdir()  # Never overwrite an existing project.
    publisher, name = info["id"].split("/")
    plugin_id = native_identity(info["id"])
    (destination / ".codex-plugin").mkdir()
    manifest = {"name": plugin_id, "version": info["version"], "description": info["summary"],
                "skills": "./skills/", "requires": [], "permissions": [],
                "author": {"name": publisher}, "interface": {"displayName": info.get("title", name)}}
    (destination / ".codex-plugin" / "plugin.json").write_bytes(canonical(manifest))
    skill = destination / "skills" / name
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: " + name + "\ndescription: " + json.dumps(info["summary"]) +
                                  "\n---\n\nUse only information the user provides. Ask before taking an external action.\n", encoding="utf-8")
    (destination / "LICENSE").write_bytes(license_bytes)
    (destination / "registry.json").write_bytes(canonical(info))


def separate(source: Path, *paths: Path) -> None:
    """Refuse scratch/output inside the package before anything is created there."""
    root = source.resolve()
    for path in paths:
        location = path.resolve()
        require(not location.is_relative_to(root),
                "scratch and output must be outside the package directory")


def preflight(raw: bytes, scratch: Path) -> dict:
    from core.registry.intake import secret_findings
    from core.registry.rules import analyze
    from core.registry.validation import native_format_findings

    artifact = inspect(raw)
    findings = secret_findings(artifact["files"])
    boundaries = analyze(artifact["files"])
    findings.extend(boundaries["conflicts"])
    with execution_directory(scratch) as owned:
        root = owned / "package"
        materialize(raw, root)
        findings.extend(native_format_findings(root, artifact))
    return {"status": "blocked" if findings else "preflight_passed", "artifact_sha256": artifact["sha256"],
            "native_digest": artifact["native_digest"], "findings": findings,
            "admitted": False, **boundaries,
            "notice": "Local format and secret preflight only; required independent review and release CI remain."}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init")
    init.add_argument("destination", type=Path)
    init.add_argument("--metadata", required=True, type=Path)
    init.add_argument("--license-file", required=True, type=Path)
    for name in ("lint", "pack"):
        command = commands.add_parser(name)
        command.add_argument("source", type=Path)
        command.add_argument("--scratch", required=True, type=Path)
        if name == "pack":
            command.add_argument("--output", required=True, type=Path)
    check = commands.add_parser("check")
    check.add_argument("artifact", type=Path)
    check.add_argument("--scratch", required=True, type=Path)
    submit = commands.add_parser("submission-link")
    submit.add_argument("artifact", type=Path)
    submit.add_argument("--github-user", required=True)
    submit.add_argument("--branch", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            initialize(args.destination, decode(args.metadata.read_bytes(), 32768), args.license_file.read_bytes())
            print(canonical({"status": "created", "next": "Edit instructions and native manifest, then lint and pack."}).decode())
            return 0
        if args.command == "submission-link":
            artifact = inspect(args.artifact.read_bytes())
            require(bool(re.fullmatch(r"[A-Za-z0-9-]{1,39}", args.github_user)) and
                    bool(re.fullmatch(r"[A-Za-z0-9/_-]{1,100}", args.branch)), "invalid contributor branch")
            repository = "vool-skills" if artifact["metadata"]["kind"] == "skill" else "vool-plugins"
            query = urllib.parse.urlencode({"quick_pull": "1", "title": "Submit " + artifact["metadata"]["id"] + " " + artifact["metadata"]["version"]})
            print("https://github.com/Parad0x-Labs/" + repository + "/compare/main..." + args.github_user + ":" + args.branch + "?" + query)
            return 0
        if args.command == "check":
            raw = args.artifact.read_bytes()
        else:
            separate(args.source, args.scratch, *([args.output] if args.command == "pack" else []))
            info = decode((args.source / "registry.json").read_bytes(), 32768)
            raw = pack(args.source, info, args.scratch)
        report = preflight(raw, args.scratch)
        if report["findings"]:
            print(canonical(report).decode())
            return 1
        if args.command == "pack":
            with args.output.open("xb") as output:
                output.write(raw)
            artifact = inspect(raw)
            report.update(id=artifact["metadata"]["id"], kind=artifact["metadata"]["kind"],
                          version=artifact["metadata"]["version"], artifact_length=len(raw),
                          package_path="packages/" + artifact["sha256"] + ".json",
                          output_file=str(args.output.resolve()))
        print(canonical(report).decode())
        return 0
    except (RegistryError, PackageError, OSError, ValueError) as exc:
        print(canonical({"status": "refused", "reason": str(exc)}).decode())
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
