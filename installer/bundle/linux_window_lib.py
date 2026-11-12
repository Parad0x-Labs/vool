"""The Linux window's library folder: pywebview and its dependencies, exactly as uv.lock pins them.

The window runs on the system python3 (installer/bundle/linux/window_host.py), from 3.10 (Ubuntu 22.04)
on, so every package here must be pure Python: a ``none-any`` wheel, installed from wheels only and by
digest. GTK and WebKitGTK themselves are the distribution's (python3-gi, gir1.2-webkit2-4.1); nothing
in this folder is compiled. proxy-tools publishes no wheel at all: its wheel is the one this project
builds from the locked sdist by the recorded recipe (installer/bundle/built_wheels.json,
``ops/macos_build_inputs.py proxy-wheel``), accepted by its recorded sha256 only.

Standard library only: the bundle build runs it with the package's interpreter before anything else.

Usage (installer/bundle/build_linux_bundle.sh):
    python linux_window_lib.py requirements --lock uv.lock --built built_wheels.json --out requirements-window.txt
    python linux_window_lib.py check --target window-lib --requirements requirements-window.txt
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

#: pywebview's whole closure on Linux (its other requirements are for Windows, macOS, OpenBSD, old
#: Pythons or optional extras; ``check`` verifies that against the installed metadata).
WINDOW_PACKAGES = ("pywebview", "bottle", "proxy-tools", "typing-extensions")
_NATIVE_SUFFIXES = (".so", ".pyd", ".dylib", ".dll", ".exe", ".jar")
_OLDEST_WINDOW_PYTHON_MINOR = 10


def norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _toml(path: Path) -> dict:
    import tomllib

    return tomllib.loads(path.read_text(encoding="utf-8"))


def locked_pins(lock: dict, built: list[dict]) -> list[tuple[str, str, list[str]]]:
    """(name, version, sha256 digests) for each window package: the lock's ``none-any`` wheels, or
    this project's own built wheel for a package that publishes none."""
    pins = []
    for name in WINDOW_PACKAGES:
        entries = [p for p in lock.get("package", []) if norm(str(p.get("name", ""))) == name]
        if len(entries) != 1:
            raise ValueError(f"uv.lock must pin exactly one {name}, found {len(entries)}")
        entry = entries[0]
        version = str(entry.get("version") or "")
        digests = []
        for wheel in entry.get("wheels", []):
            url = str(wheel.get("url") or "")
            algorithm, _, digest = str(wheel.get("hash") or "").partition(":")
            if url.endswith("-none-any.whl") and algorithm == "sha256" and re.fullmatch(r"[0-9a-f]{64}", digest):
                digests.append(digest)
        if not digests:
            records = [r for r in built if norm(str(r.get("package", ""))) == name and r.get("target") == "any"]
            if len(records) != 1:
                raise ValueError(f"{name} publishes no pure-Python wheel and has no recorded built wheel")
            record = records[0]
            if record.get("version") != version:
                raise ValueError(f"built wheel {record.get('file')} is {record.get('version')}, uv.lock pins {version}")
            if not str(record.get("file", "")).endswith("-none-any.whl"):
                raise ValueError(f"built wheel {record.get('file')} is not pure Python")
            digest = str(record.get("sha256") or "")
            if not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise ValueError(f"built wheel {record.get('file')} has no sha256")
            digests.append(digest)
        pins.append((name, version, digests))
    return pins


def render(pins: list[tuple[str, str, list[str]]]) -> str:
    lines = []
    for name, version, digests in pins:
        lines.append(" \\\n".join([f"{name}=={version}", *(f"    --hash=sha256:{d}" for d in digests)]))
    return "\n".join(lines) + "\n"


def _marker_is_false_on_linux(marker: str) -> bool:
    """The only conditions pywebview's metadata may put on a requirement outside this folder:
    another operating system, a Python older than the window's oldest, or an optional extra.
    Anything else is refused, so a new requirement is looked at by a person, not guessed."""
    marker = marker.strip()
    platform = re.fullmatch(r'sys_platform\s*==\s*"([^"]+)"', marker)
    if platform:
        return not platform.group(1).startswith("linux")
    if re.fullmatch(r'extra\s*==\s*"[^"]+"', marker):
        return True
    older = re.fullmatch(r'python_version\s*<\s*"3\.(\d+)"', marker)
    if older:
        return int(older.group(1)) <= _OLDEST_WINDOW_PYTHON_MINOR
    raise ValueError(f"unrecognised requirement condition in the window's packages: {marker!r}")


def check(target: Path, pins: list[tuple[str, str, list[str]]]) -> list[str]:
    """Problems with an installed window-lib folder; an empty list when it is exactly the pins."""
    problems = []
    for path in sorted(target.rglob("*")):
        if path.is_symlink():
            problems.append(f"symlink in window-lib: {path.relative_to(target)}")
        elif path.is_file() and path.name.lower().endswith(_NATIVE_SUFFIXES):
            problems.append(f"compiled file in window-lib: {path.relative_to(target)}")
    installed: dict[str, tuple[str, Path]] = {}
    for meta in sorted(target.glob("*.dist-info/METADATA")):
        fields = {}
        for line in meta.read_text(encoding="utf-8", errors="replace").splitlines():
            key, _, value = line.partition(": ")
            if key in ("Name", "Version") and key not in fields:
                fields[key] = value.strip()
        installed[norm(fields.get("Name", ""))] = (fields.get("Version", ""), meta)
    wanted = {name: version for name, version, _digests in pins}
    if set(installed) != set(wanted):
        problems.append(f"window-lib holds {sorted(installed)}, expected {sorted(wanted)}")
    for name, version in wanted.items():
        found = installed.get(name)
        if found is not None and found[0] != version:
            problems.append(f"{name} {found[0]} installed, uv.lock pins {version}")
    for name, (_version, meta) in installed.items():
        for line in meta.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.startswith("Requires-Dist: "):
                continue
            requirement, _, marker = line[len("Requires-Dist: "):].partition(";")
            needed = norm(re.split(r"[\s<>=!~\[(]", requirement.strip(), maxsplit=1)[0])
            try:
                if marker and _marker_is_false_on_linux(marker):
                    continue
            except ValueError as exc:
                problems.append(f"{name}: {exc}")
                continue
            if needed not in wanted:
                problems.append(f"{name} requires {needed}, which window-lib does not carry")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    requirements = commands.add_parser("requirements", help="write the hashed requirements for window-lib")
    requirements.add_argument("--lock", type=Path, required=True)
    requirements.add_argument("--built", type=Path, required=True)
    requirements.add_argument("--out", type=Path, required=True)
    verify = commands.add_parser("check", help="verify an installed window-lib folder")
    verify.add_argument("--target", type=Path, required=True)
    verify.add_argument("--requirements", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "requirements":
        built = json.loads(args.built.read_text(encoding="utf-8")).get("wheels", [])
        args.out.write_text(render(locked_pins(_toml(args.lock), built)), encoding="utf-8")
        return 0
    pins = []
    for logical in re.sub(r"\\\n", " ", args.requirements.read_text(encoding="utf-8")).splitlines():
        head = logical.split()[0] if logical.strip() else ""
        if "==" in head:
            name, _, version = head.partition("==")
            pins.append((norm(name), version, []))
    problems = check(args.target, pins)
    for problem in problems:
        print(f"window-lib: {problem}", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
