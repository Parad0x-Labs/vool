#!/usr/bin/env python3
"""The macOS app's Python dependencies, exactly as ``uv.lock`` pins them, and a drift check.

WHY THIS EXISTS. Up to 0.7.7 the macOS build installed a hand-written list of package names and
loose specifiers (``pydantic``, ``"starlette>=1.3.1,<2.0"`` ...) with ``uv pip install``, which
resolves against the live index. The lock was never consulted: the 0.7.7 arm64 app shipped 32 of its
80 packages at versions other than the lock's (starlette 1.10.0 for the lock's 1.3.1, uvicorn 0.54.0
for 0.49.0, pyobjc 13.0 beside the lock's 12.2.2 ...). Those versions were never tested here.

The bundle's set is now the lock's own export for the project's base dependencies plus the extras
the app ships (``EXTRAS``), installed with ``uv pip sync --require-hashes``: every artifact must be
one the lock names by digest, and nothing outside the export is installed.

Wheels this project builds itself, because no published wheel exists for the target
(``installer/bundle/built_wheels.json``), are accepted only at the lock's version and only by their
own recorded sha256, and each is listed in the export report.

Stdlib only, Python 3.9+: the build runs it before any dependency exists.

Usage:
    python ops/macos_locked_requirements.py export --arch arm64 --out requirements.txt [--report built-wheels.json]
    python ops/macos_locked_requirements.py check --site-packages DIR --requirements requirements.txt --arch arm64
    python ops/macos_locked_requirements.py check-app VOOL.app --arch arm64
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

REPO = Path(__file__).resolve().parents[1]
BUILT_WHEELS = REPO / "installer" / "bundle" / "built_wheels.json"

#: The optional-dependency groups the app ships beside the base dependencies. companion carries
#: zstandard (the command registry's codec), evm and x402 the wallet's Base/Ethereum lanes, desktop
#: the native window (pywebview, pinned) and Apple Calendar/Contacts (pyobjc EventKit/Contacts), keys the
#: wallet's security-key support (fido2).
EXTRAS = ("companion", "evm", "x402", "desktop", "keys")
UV_PLATFORMS = {"arm64": "aarch64-apple-darwin", "x86_64": "x86_64-apple-darwin"}


def norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


@dataclass
class Pin:
    name: str
    version: str
    marker: str = ""
    hashes: list[str] = field(default_factory=list)

    def render(self) -> str:
        head = f"{self.name}=={self.version}"
        if self.marker:
            head += f" ; {self.marker}"
        lines = [head] + [f"    --hash=sha256:{h}" for h in self.hashes]
        return " \\\n".join(lines)


def export_command(extras: tuple[str, ...] = EXTRAS) -> list[str]:
    """`uv export` of the lock, never a resolve. --locked fails if the lock is stale."""
    cmd = ["uv", "export", "--locked", "--no-dev", "--no-default-groups", "--no-emit-project",
           "--no-header", "--no-annotate"]
    for extra in extras:
        cmd += ["--extra", extra]
    return cmd


def run_export(repo: Path = REPO, extras: tuple[str, ...] = EXTRAS) -> str:
    if shutil.which("uv") is None:
        raise SystemExit("uv is required to export the locked requirements")
    with tempfile.TemporaryDirectory(prefix="vool-locked-") as scratch:
        out = Path(scratch) / "requirements.txt"
        done = subprocess.run([*export_command(extras), "--output-file", str(out)], cwd=str(repo),
                              capture_output=True, text=True)
        if done.returncode != 0:
            raise SystemExit(f"uv export --locked failed (is uv.lock current?):\n{done.stderr.strip()}")
        return out.read_text(encoding="utf-8")


_PIN = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s;\\]+)\s*(?:;\s*(.*?))?\s*$")
_HASH = re.compile(r"^--hash=sha256:([0-9a-f]{64})$")


def parse(text: str) -> list[Pin]:
    """Parse a hashed requirements file as `uv export` writes it. Anything else is refused."""
    pins: list[Pin] = []
    for logical in re.sub(r"\\\n", " ", text).splitlines():
        line = logical.strip()
        if not line or line.startswith("#"):
            continue
        tokens = line.split()
        hashes = [t for t in tokens if t.startswith("--hash=")]
        head = " ".join(t for t in tokens if not t.startswith("--hash="))
        match = _PIN.match(head)
        if not match:
            raise ValueError(f"not an exact pin: {line!r}")
        digests = []
        for h in hashes:
            m = _HASH.match(h)
            if not m:
                raise ValueError(f"not a sha256 hash: {h!r} in {line!r}")
            digests.append(m.group(1))
        if not digests:
            raise ValueError(f"pin without a hash: {line!r}")
        pins.append(Pin(match.group(1), match.group(2), (match.group(3) or "").strip(), digests))
    return pins


def target_environment(arch: str, python_version: str = "3.11") -> dict[str, str]:
    """The PEP 508 marker environment of the app's embedded CPython on one Mac architecture."""
    if arch not in UV_PLATFORMS:
        raise ValueError(f"unsupported architecture {arch!r}")
    if not re.fullmatch(r"3\.\d+", python_version):
        raise ValueError(f"python version must be major.minor, got {python_version!r}")
    return {
        "sys_platform": "darwin", "platform_system": "Darwin", "os_name": "posix",
        "platform_machine": arch, "implementation_name": "cpython",
        "platform_python_implementation": "CPython",
        "python_version": python_version, "python_full_version": python_version,
    }


_TOKEN = re.compile(r"\s*(\(|\)|==|!=|<=|>=|<|>|\bnot in\b|\bin\b|\band\b|\bor\b|'[^']*'|\"[^\"]*\"|[A-Za-z_]+)")
_VERSION_VARS = {"python_version", "python_full_version"}


def _version(text: str) -> tuple[int, ...]:
    if not re.fullmatch(r"\d+(\.\d+)*", text):
        raise ValueError(f"cannot compare version {text!r}")
    return tuple(int(x) for x in text.split("."))


def _compare(variable: str, op: str, literal: str, env: dict[str, str]) -> bool:
    if variable not in env:
        raise ValueError(f"unknown marker variable {variable!r}")
    value = env[variable]
    if op in ("in", "not in"):
        return (value in literal) == (op == "in")
    if variable in _VERSION_VARS and op in ("<", "<=", ">", ">=", "==", "!="):
        left, right = _version(value), _version(literal)
        if variable == "python_full_version" and len(right) > 2 and right[:2] == left[:2]:
            # The patch release of the embedded interpreter is not part of the target; a marker
            # that splits on it cannot be decided here, so it is refused rather than guessed.
            raise ValueError(f"marker splits on a patch release of {value}: {variable} {op} {literal!r}")
        width = max(len(left), len(right))
        left, right = left + (0,) * (width - len(left)), right + (0,) * (width - len(right))
    else:
        left, right = value, literal  # type: ignore[assignment]
        if op not in ("==", "!="):
            raise ValueError(f"cannot order non-version marker {variable} {op} {literal!r}")
    return {"==": left == right, "!=": left != right, "<": left < right, "<=": left <= right,
            ">": left > right, ">=": left >= right}[op]


def evaluate(marker: str, env: dict[str, str]) -> bool:
    """Evaluate a PEP 508 environment marker as `uv export` writes them. Unknown syntax is an error."""
    if not marker.strip():
        return True
    tokens: list[str] = []
    pos = 0
    while pos < len(marker):
        if marker[pos:].strip() == "":
            break
        m = _TOKEN.match(marker, pos)
        if not m:
            raise ValueError(f"cannot parse marker {marker!r} at {marker[pos:]!r}")
        tokens.append(m.group(1))
        pos = m.end()

    def take(expected: Optional[str] = None) -> str:
        if not tokens:
            raise ValueError(f"marker ended early: {marker!r}")
        tok = tokens.pop(0)
        if expected is not None and tok != expected:
            raise ValueError(f"expected {expected!r}, found {tok!r} in {marker!r}")
        return tok

    def atom() -> bool:
        if tokens and tokens[0] == "(":
            take("(")
            value = disjunction()
            take(")")
            return value
        left, op, right = take(), take(), take()
        if op not in ("==", "!=", "<", "<=", ">", ">=", "in", "not in"):
            raise ValueError(f"unknown operator {op!r} in {marker!r}")
        if left[:1] in "'\"" and right[:1] not in "'\"":
            left, right = right, left
            op = {"<": ">", "<=": ">=", ">": "<", ">=": "<="}.get(op, op)
        if right[:1] not in "'\"" or left[:1] in "'\"":
            raise ValueError(f"marker compares two variables or two literals: {marker!r}")
        return _compare(left, op, right[1:-1], env)

    def conjunction() -> bool:
        value = atom()
        while tokens and tokens[0] == "and":
            take("and")
            value = atom() and value
        return value

    def disjunction() -> bool:
        value = conjunction()
        while tokens and tokens[0] == "or":
            take("or")
            value = conjunction() or value
        return value

    result = disjunction()
    if tokens:
        raise ValueError(f"trailing marker text {tokens!r} in {marker!r}")
    return result


def for_target(pins: list[Pin], env: dict[str, str]) -> list[Pin]:
    """The pins that apply on the target: exactly one per package, or an error."""
    chosen: list[Pin] = []
    seen: dict[str, str] = {}
    for pin in pins:
        if not evaluate(pin.marker, env):
            continue
        key = norm(pin.name)
        if key in seen:
            raise ValueError(f"the lock applies {key} twice on this target ({seen[key]} and {pin.version})")
        seen[key] = pin.version
        chosen.append(pin)
    return chosen


def built_wheels(path: Path = BUILT_WHEELS) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return [w for w in data.get("wheels", []) if isinstance(w, dict)]


def apply_built_wheels(pins: list[Pin], arch: str, records: list[dict]) -> list[dict]:
    """Admit this project's own built wheels for the target, by digest. Returns what was admitted."""
    platform = UV_PLATFORMS[arch]
    by_name: dict[str, Pin] = {}
    for p in pins:
        if norm(p.name) in by_name:
            raise ValueError(f"{p.name} is pinned twice; filter the pins for the target first")
        by_name[norm(p.name)] = p
    admitted = []
    for record in records:
        if record.get("target") not in ("any", platform):
            continue
        digest = str(record.get("sha256", ""))
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError(f"built wheel {record.get('file')} has no sha256")
        pin = by_name.get(norm(str(record.get("package", ""))))
        if pin is None:
            raise ValueError(f"built wheel {record.get('file')} is for a package the lock does not ship")
        if record.get("version") != pin.version:
            # The version is the lock's decision, never the wheel record's. A record at another
            # version means the lock and the recorded build disagree; fix one of them.
            raise ValueError(
                f"built wheel {record.get('file')} is version {record.get('version')}, but uv.lock pins "
                f"{pin.name}=={pin.version} for {arch}"
            )
        # The lock lists the sdist (or published wheels for other targets); the wheel built from that
        # sdist by the recorded recipe joins the accepted digests for this target only.
        if digest not in pin.hashes:
            pin.hashes.append(digest)
        admitted.append({
            "package": norm(pin.name),
            "version": pin.version,
            "artifact": record.get("file"),
            "sha256": digest,
            "reason": "built here from the locked sdist: no published wheel for this target "
                      "(installer/bundle/built_wheels.json)",
        })
    return admitted


def locked_requirements(arch: str, text: Optional[str] = None, python_version: str = "3.11",
                        records: Optional[list[dict]] = None) -> tuple[list[Pin], list[dict]]:
    """The exact pins for one Mac architecture: the lock's export, evaluated for that target."""
    env = target_environment(arch, python_version)
    pins = for_target(parse(text if text is not None else run_export()), env)
    admitted = apply_built_wheels(pins, arch, built_wheels() if records is None else records)
    return pins, admitted


def render(pins: list[Pin]) -> str:
    return "\n".join(p.render() for p in pins) + "\n"


def installed(site_packages: Path) -> dict[str, str]:
    """{normalized name: version} from every *.dist-info METADATA in a site-packages directory."""
    found: dict[str, str] = {}
    for meta in sorted(site_packages.glob("*.dist-info/METADATA")):
        name = version = ""
        for line in meta.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line:
                break
            if line.startswith("Name:"):
                name = line.split(":", 1)[1].strip()
            elif line.startswith("Version:"):
                version = line.split(":", 1)[1].strip()
        if not name or not version:
            raise ValueError(f"unreadable distribution metadata: {meta}")
        key = norm(name)
        if key in found and found[key] != version:
            raise ValueError(f"{key} is installed twice ({found[key]} and {version})")
        found[key] = version
    return found


def drift(site_packages: Path, pins: list[Pin]) -> list[str]:
    """The installed set must equal the pinned set: same packages, each at exactly its pin."""
    wanted: dict[str, str] = {}
    for p in pins:
        key = norm(p.name)
        if key in wanted:
            raise ValueError(f"{key} is pinned twice; filter the pins for the target first")
        wanted[key] = p.version
    have = installed(site_packages)
    if not have:
        return [f"no installed distributions found under {site_packages}"]
    problems = []
    for name, version in sorted(have.items()):
        if name not in wanted:
            problems.append(f"{name}=={version} is installed but not in the locked set")
        elif version != wanted[name]:
            problems.append(f"{name}=={version} is installed; the lock pins {wanted[name]}")
    for name in sorted(set(wanted) - set(have)):
        problems.append(f"{name}=={wanted[name]} is pinned but not installed")
    return problems


def app_site_packages(app: Path) -> tuple[Path, str]:
    """The embedded interpreter's site-packages and its major.minor version."""
    candidates = sorted((app / "Contents" / "Resources" / "python" / "lib").glob("python3.*/site-packages"))
    if len(candidates) != 1:
        raise SystemExit(f"expected one embedded site-packages in {app}, found {len(candidates)}")
    return candidates[0], candidates[0].parent.name[len("python"):]


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    ex = sub.add_parser("export", help="write the locked, hashed requirements for one architecture")
    ex.add_argument("--arch", required=True, choices=sorted(UV_PLATFORMS))
    ex.add_argument("--out", required=True, type=Path)
    ex.add_argument("--python-version", default="3.11", help="the embedded CPython's major.minor")
    ex.add_argument("--report", type=Path, help="write the admitted built wheels as JSON")
    ex.add_argument("--built-wheels", type=Path, default=BUILT_WHEELS,
                    help="the built-wheel record (default: the committed installer/bundle/built_wheels.json)")
    ck = sub.add_parser("check", help="fail when a site-packages differs from an exported requirements file")
    ck.add_argument("--site-packages", required=True, type=Path)
    ck.add_argument("--requirements", required=True, type=Path)
    ck.add_argument("--arch", required=True, choices=sorted(UV_PLATFORMS))
    ck.add_argument("--python-version", default="3.11")
    ca = sub.add_parser("check-app", help="fail when a built VOOL.app differs from this checkout's lock")
    ca.add_argument("app", type=Path)
    ca.add_argument("--arch", required=True, choices=sorted(UV_PLATFORMS))
    args = ap.parse_args(argv)

    if args.cmd == "export":
        pins, admitted = locked_requirements(args.arch, python_version=args.python_version,
                                             records=built_wheels(args.built_wheels))
        args.out.write_text(render(pins), encoding="utf-8")
        if args.report:
            args.report.write_text(json.dumps({"arch": args.arch, "built_wheels": admitted}, indent=2) + "\n",
                                   encoding="utf-8")
        print(f"locked requirements: {len(pins)} pins for {args.arch} -> {args.out}")
        for d in admitted:
            print(f"  built here (accepted by digest): {d['package']}=={d['version']} ({d['artifact']})")
        return 0

    if args.cmd == "check":
        env = target_environment(args.arch, args.python_version)
        pins = for_target(parse(args.requirements.read_text(encoding="utf-8")), env)
        site = args.site_packages
    else:
        site, python_version = app_site_packages(args.app)
        pins, _ = locked_requirements(args.arch, python_version=python_version)
    problems = drift(site, pins)
    print(f"dependency drift check: {site}")
    print(f"  installed {len(installed(site))}; pinned {len(pins)}; drift {len(problems)}")
    for p in problems:
        print(f"  DRIFT {p}")
    if problems:
        print("  FAIL: the bundle does not match uv.lock", file=sys.stderr)
        return 1
    print("  PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
