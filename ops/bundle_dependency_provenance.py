#!/usr/bin/env python3
"""Release-grade dependency provenance for the macOS/Windows bundle.

WHY THIS EXISTS. The bundle installs a hand-listed "lean" set into its embedded CPython. A
build can be made to succeed with artifacts that no authority vouches for -- during this lane's
preflight, two pins were supplied from wheels RE-ZIPPED out of unpacked ``~/.cache/uv``
directories. Their CONTENTS were intact (every file matched its RECORD digest) but their own
sha256 matched nothing in ``uv.lock``, because re-zipping changes container bytes. `uv` and
`pip` accepted them anyway. **A package manager accepting a wheel is not provenance.**

So this module answers one question per artifact, from the lock rather than from the installer:

    is this exact byte sequence the artifact ``uv.lock`` pins for this package?

Two independent checks, both required for a RELEASE verdict:

* ARTIFACT identity -- the wheel's own sha256 appears in ``uv.lock`` for that package.
* CONTENT integrity -- every file listed in the wheel's ``RECORD`` is present in the zip with
  the recorded digest and size.

Content integrity alone is DIAGNOSTIC, never release: it proves the payload was not corrupted,
not that the payload is the one the lock pins.

Usage:
    python -m ops.bundle_dependency_provenance --wheelhouse DIR [--out manifest.json] [--strict]

``--strict`` exits non-zero unless every artifact in the wheelhouse is release-grade.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import sys
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
LOCK = REPO / "uv.lock"
PYPROJECT = REPO / "pyproject.toml"
#: Wheels this project builds itself because no published wheel exists for a target (cryptography
#: >= 49 ships no Intel macOS wheel). Each entry is reviewed and committed with what it was built
#: from: the wheel's own sha256, the source sdist and its sha256, and the toolchain inputs.
BUILT_WHEELS = REPO / "installer" / "bundle" / "built_wheels.json"


def lean_requirements() -> list[str]:
    """The bundle's top-level requirements: the project's base dependencies plus the extras the
    macOS app ships (ops.macos_locked_requirements.EXTRAS), by name.

    The build installs exactly the lock's export of this same declaration, so this list and the
    installed set cannot disagree about what the bundle asks for. It used to be parsed out of a
    hand-written `uv pip install` line in the build script, which resolved those names against the
    live index instead of the lock (0.7.7 shipped 32 packages at versions the lock does not pin).
    """
    try:
        import tomllib
    except ImportError as exc:  # pragma: no cover - the build's helper interpreter is 3.11+
        raise SystemExit("reading pyproject.toml needs Python 3.11+ (set SYS_PY to one)") from exc
    from ops.macos_locked_requirements import EXTRAS
    project = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]
    specs = list(project["dependencies"])
    for extra in EXTRAS:
        specs.extend(project["optional-dependencies"][extra])
    names: list[str] = []
    for spec in specs:
        name = re.split(r"[<>=!~\[ ;]", spec.strip(), maxsplit=1)[0]
        if name and name not in names:
            names.append(name)
    return names


def lock_artifacts() -> dict[str, dict[str, str]]:
    """{normalized package name: {wheel filename: sha256}} from uv.lock."""
    src = LOCK.read_text(encoding="utf-8")
    out: dict[str, dict[str, str]] = {}
    for block in re.findall(r"\[\[package\]\](.*?)(?=\n\[\[package\]\]|\Z)", src, re.S):
        m = re.search(r'^name = "([^"]+)"', block, re.M)
        if not m:
            continue
        urls = re.findall(r'url = "([^"]+)"', block)
        hashes = re.findall(r'hash = "sha256:([0-9a-f]{64})"', block)
        version = re.search(r'^version = "([^"]+)"', block, re.M)
        # No zip(strict=...): the bundle build runs this under macOS's system python3.9, which has no
        # such keyword. Indexing keeps the same pairing (a missing hash leaves its wheel out).
        entry = {urls[i].rsplit("/", 1)[-1]: hashes[i] for i in range(min(len(urls), len(hashes)))}
        # A package the lock pins at more than one version (a marker fork: toolz for the `pay`
        # extra, ckzg and cryptography for Intel macOS) keeps every version's artifacts. Keying on
        # the name alone let the later block overwrite the earlier one's digests.
        merged = out.setdefault(_norm(m.group(1)), {"__version__": ""})
        versions = [v for v in merged["__version__"].split(" or ") if v]
        if version and version.group(1) not in versions:
            versions.append(version.group(1))
        merged.update(entry)
        merged["__version__"] = " or ".join(versions)
    return out


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def verify_record(path: Path) -> dict:
    """Every file the wheel's RECORD lists must be present with the recorded digest."""
    result = {"ok": 0, "mismatched": [], "missing": [], "no_digest": 0}
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        records = [n for n in names if n.endswith(".dist-info/RECORD")]
        if not records:
            result["error"] = "no RECORD in wheel"
            return result
        for line in z.read(records[0]).decode("utf-8", "replace").splitlines():
            parts = line.rsplit(",", 2)
            if len(parts) != 3:
                continue
            member, digest, _size = parts
            if not digest:
                result["no_digest"] += 1
                continue
            if member not in names:
                result["missing"].append(member)
                continue
            algo, _, b64 = digest.partition("=")
            try:
                want = base64.urlsafe_b64decode(b64 + "=" * (-len(b64) % 4))
                got = hashlib.new(algo, z.read(member)).digest()
            except Exception as exc:  # unreadable member or unknown algorithm
                result["mismatched"].append(f"{member}: {type(exc).__name__}")
                continue
            if want == got:
                result["ok"] += 1
            else:
                result["mismatched"].append(member)
    return result


def built_wheels() -> dict[str, dict]:
    """{wheel sha256: entry} from the committed built-wheel record; empty when there is none."""
    try:
        data = json.loads(BUILT_WHEELS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out: dict[str, dict] = {}
    for entry in data.get("wheels", []) if isinstance(data, dict) else []:
        digest = str(entry.get("sha256", "")) if isinstance(entry, dict) else ""
        if re.fullmatch(r"[0-9a-f]{64}", digest) and re.fullmatch(r"[0-9a-f]{64}", str(entry.get("source_sdist_sha256", ""))):
            out[digest] = entry
    return out


def _recorded_wheelhouse(wheelhouse: Path) -> str:
    """The wheelhouse as the shipped record names it: relative to the build root, never absolute.

    This record is copied into the app (Contents/Resources/app/config/dependency-provenance.json), so an
    absolute path shipped the builder's own disk layout to every user (release dry run, 2026-10-07). A
    wheelhouse outside the build root is named by its directory name alone; the artifacts themselves
    are identified by their digests, never by where they sat.
    """
    resolved = Path(wheelhouse).resolve()
    try:
        return resolved.relative_to(REPO).as_posix()
    except ValueError:
        return f"<outside the build root>/{resolved.name}"


def audit(wheelhouse: Path) -> dict:
    lock = lock_artifacts()
    built = built_wheels()
    entries = []
    for wheel in sorted(wheelhouse.glob("*.whl")):
        pkg = _norm(wheel.name.split("-", 1)[0])
        digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
        pinned = lock.get(pkg, {})
        expected = pinned.get(wheel.name)
        # A wheel whose FILENAME is unknown to the lock may still be a pinned artifact under a
        # different name; identity is decided by the digest, never by the filename.
        digest_is_pinned = digest in {v for k, v in pinned.items() if k != "__version__"}
        record = verify_record(wheel)
        content_ok = not record.get("error") and not record["mismatched"] and not record["missing"]
        # A wheel we built ourselves is identified the same way -- by its own digest -- against the
        # committed built-wheel record, and only under the filename and package that record names.
        built_entry = built.get(digest)
        built_here = bool(built_entry and built_entry.get("file") == wheel.name and _norm(str(built_entry.get("package", ""))) == pkg)
        entries.append({
            "file": wheel.name,
            "package": pkg,
            "locked_version": pinned.get("__version__", ""),
            "sha256": digest,
            "uv_lock_expected_sha256": expected or "",
            "artifact_matches_lock": bool(expected and digest == expected) or digest_is_pinned,
            "built_here": built_here,
            "built_from": (built_entry or {}) if built_here else {},
            "record_verified": content_ok,
            "record_detail": record,
            "grade": (
                "RELEASE" if ((expected and digest == expected) or digest_is_pinned or built_here) and content_ok
                else "DIAGNOSTIC" if content_ok
                else "REJECTED"
            ),
        })
    lean = lean_requirements()
    unpinned = [n for n in lean if _norm(n) not in lock]
    return {
        "schema": "vool.bundle_dependency_provenance/1",
        "wheelhouse": _recorded_wheelhouse(wheelhouse),
        "lean_requirements": lean,
        "lean_requirements_absent_from_uv_lock": unpinned,
        "artifacts": entries,
        "release_grade": bool(entries) and all(e["grade"] == "RELEASE" for e in entries) and not unpinned,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--wheelhouse", required=True)
    ap.add_argument("--out", default="")
    ap.add_argument("--strict", action="store_true")
    args = ap.parse_args()
    report = audit(Path(args.wheelhouse))
    text = json.dumps(report, indent=2) + "\n"
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    print(text)
    for e in report["artifacts"]:
        print(f"{e['grade']:<11} {e['file']}  artifact_matches_lock={e['artifact_matches_lock']} built_here={e['built_here']} record_verified={e['record_verified']}")
    if report["lean_requirements_absent_from_uv_lock"]:
        print("NOT PINNED IN uv.lock: " + ", ".join(report["lean_requirements_absent_from_uv_lock"]))
    print(f"\nrelease_grade: {report['release_grade']}")
    if args.strict and not report["release_grade"]:
        print("STRICT: refusing -- not every artifact is release-grade", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
