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
BUILD_SCRIPT = REPO / "installer" / "bundle" / "build_macos_app.sh"


def lean_requirements() -> list[str]:
    """The bundle's lean pins, read from the build script so there is ONE source of truth.

    A second hand-maintained copy here would drift from the installer exactly the way the
    installer drifted from the runtime's real import closure.
    """
    src = BUILD_SCRIPT.read_text(encoding="utf-8")
    marker = 'uv pip install --python "${embedded}"'
    if marker not in src:
        raise SystemExit("could not find the lean dependency install in the build script")
    body = src.split(marker, 1)[1].split("lean dependency install failed", 1)[0]
    return lean_requirements_from_text(body)


def _strip_shell_expansions(body: str) -> str:
    """Remove every `${...}` (nesting and quotes inside honoured) and `$name` expansion.

    The bash-3.2 guard `${arr[@]+"${arr[@]}"}` nests a quoted expansion inside an expansion; a
    regex that stops at the first closing brace left a stray quote that paired with the next
    quoted specifier and swallowed a whole line of packages (measured 2026-09-07).
    """
    out: list[str] = []
    i = 0
    while i < len(body):
        if body.startswith("${", i):
            depth = 0
            j = i
            while j < len(body):
                if body.startswith("${", j):
                    depth += 1
                    j += 2
                    continue
                if body[j] == "}":
                    depth -= 1
                    j += 1
                    if depth == 0:
                        break
                    continue
                j += 1
            out.append(" ")
            i = j
            continue
        if body[i] == "$" and i + 1 < len(body) and (body[i + 1].isalpha() or body[i + 1] == "_"):
            j = i + 1
            while j < len(body) and (body[j].isalnum() or body[j] == "_"):
                j += 1
            out.append(" ")
            i = j
            continue
        out.append(body[i])
        i += 1
    return "".join(out)


def lean_requirements_from_text(body: str) -> list[str]:
    """The package names in one `uv pip install` invocation's argument text."""
    # Drop shell noise BEFORE tokenising: option flags (--quiet, --no-compile-bytecode), shell
    # variable expansions (nested ones included), line continuations, the `||` that opens the
    # failure branch. Reading a flag's name as a package produced three phantom "unpinned
    # dependencies" on the first run of this tool.
    body = re.sub(r"--[A-Za-z0-9-]+", " ", body)
    body = _strip_shell_expansions(body)
    body = body.replace("\\\n", " ").replace("||", " ")
    names: list[str] = []
    for quoted, bare in re.findall(r'"([^"]+)"|(\b[A-Za-z][A-Za-z0-9_.-]*\b)', body):
        raw = (quoted or bare).strip()
        if not raw or raw.startswith("-"):
            continue
        name = re.split(r"[<>=!~\[ ]", raw, 1)[0].strip()
        if not name or name.startswith("$"):
            continue
        if name in {"die", "uv", "pip", "install", "python", "embedded", "lean", "dependency", "failed"}:
            continue
        if name not in names:
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
        entry = {u.rsplit("/", 1)[-1]: h for u, h in zip(urls, hashes)}
        entry["__version__"] = version.group(1) if version else ""
        out[_norm(m.group(1))] = entry
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


def audit(wheelhouse: Path) -> dict:
    lock = lock_artifacts()
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
        entries.append({
            "file": wheel.name,
            "package": pkg,
            "locked_version": pinned.get("__version__", ""),
            "sha256": digest,
            "uv_lock_expected_sha256": expected or "",
            "artifact_matches_lock": bool(expected and digest == expected) or digest_is_pinned,
            "record_verified": content_ok,
            "record_detail": record,
            "grade": (
                "RELEASE" if ((expected and digest == expected) or digest_is_pinned) and content_ok
                else "DIAGNOSTIC" if content_ok
                else "REJECTED"
            ),
        })
    lean = lean_requirements()
    unpinned = [n for n in lean if _norm(n) not in lock]
    return {
        "schema": "vool.bundle_dependency_provenance/1",
        "wheelhouse": str(wheelhouse),
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
        print(f"{e['grade']:<11} {e['file']}  artifact_matches_lock={e['artifact_matches_lock']} record_verified={e['record_verified']}")
    if report["lean_requirements_absent_from_uv_lock"]:
        print("NOT PINNED IN uv.lock: " + ", ".join(report["lean_requirements_absent_from_uv_lock"]))
    print(f"\nrelease_grade: {report['release_grade']}")
    if args.strict and not report["release_grade"]:
        print("STRICT: refusing -- not every artifact is release-grade", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
