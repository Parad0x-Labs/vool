"""Compare two built VOOL.app bundles and judge determinism mechanically.

Two builds of the SAME commit must be byte-identical except for the build timestamp: re-staging
is `git archive`-based (see build_macos_app.sh), the Info.plist carries no timestamps, and the
launcher is emitted from static text. The one KNOWN-VARIABLE field is BUILD_MANIFEST.json's
``build_time_utc``. Anything else that differs is NONDETERMINISTIC drift (or a different commit).

Usage:
    python3 installer/bundle/compare_app_bundles.py PATH_A PATH_B

Exit 0 iff every difference is the known-variable timestamp (differences are named either way).
Unavoidable non-byte variation is documented rather than normalized: directory mtimes, extended
attributes, DMG container UUIDs/timestamps (hdiutil), and any code signature are out of scope —
this tool compares regular-file BYTES only.

Caveat (path-dependence, not time-dependence): dependency console scripts (``python/bin/*``) and
the wheels' ``RECORD`` indexes embed the ABSOLUTE interpreter path inside the bundle, so two builds
placed at DIFFERENT output paths differ in exactly those bytes. The canonical determinism check is
therefore: build, move the artifact aside, rebuild into the SAME path, compare.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

KNOWN_VARIABLE_FILES = {
    # Manifest rewrites its UTC build time per run; everything else in it must match byte for byte.
    "Contents/Resources/BUILD_MANIFEST.json": ("build_time_utc",),
    # The bundled build-source stamp records when the build ran (informational built_at); the
    # identity fields (commit/commit_full/dirty_state) must match byte for byte.
    "Contents/Resources/app/config/build-source.json": ("built_at",),
}

# Default when a differing file is not listed above: no field is known-variable.
_NO_KNOWN_VARIABLES: tuple[str, ...] = ()


def _known_variables_for(rel: str) -> tuple[str, ...]:
    return KNOWN_VARIABLE_FILES.get(rel, _NO_KNOWN_VARIABLES)


def _hash_tree(root: Path) -> dict[str, str]:
    """Relative posix path -> sha256 of every regular file under root."""
    hashes: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        hashes[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return hashes


def compare_bundles(path_a: Path, path_b: Path) -> tuple[bool, list[str]]:
    """Return (equivalent, findings). equivalent=True iff only known-variable fields differ."""
    a_root, b_root = Path(path_a), Path(path_b)
    findings: list[str] = []
    a_files, b_files = _hash_tree(a_root), _hash_tree(b_root)

    equivalent = True
    for rel in sorted(set(a_files) | set(b_files)):
        if rel not in b_files:
            equivalent = False
            findings.append(f"NONDETERMINISTIC: only in {a_root.name}: {rel}")
            continue
        if rel not in a_files:
            equivalent = False
            findings.append(f"NONDETERMINISTIC: only in {b_root.name}: {rel}")
            continue
        if a_files[rel] == b_files[rel]:
            continue
        fields = _known_variables_for(rel)
        if fields and _known_variable_only(a_root / rel, b_root / rel, fields):
            findings.append(f"KNOWN-VARIABLE: {rel} differs only in {', '.join(fields)}")
            continue
        equivalent = False
        findings.append(f"NONDETERMINISTIC: bytes differ: {rel}")
    return equivalent, findings


def _known_variable_only(file_a: Path, file_b: Path, fields: tuple[str, ...]) -> bool:
    """True when two differing JSON files match once the given known-variable fields are stripped."""

    def strip(path: Path) -> dict:
        payload = json.loads(path.read_text(encoding="utf-8"))
        for field in fields:
            payload.pop(field, None)
        return payload

    try:
        return strip(file_a) == strip(file_b)
    except Exception:
        return False


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__)
        return 2
    equivalent, findings = compare_bundles(Path(argv[1]), Path(argv[2]))
    for line in findings:
        print(line)
    if equivalent:
        print("DETERMINISTIC: all compared bytes identical (known-variable fields aside)")
        return 0
    print("NONDETERMINISTIC: bundles are not byte-equivalent — see findings above")
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
