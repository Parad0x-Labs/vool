"""Refuse to test the wrong build.

Every test of a running VOOL (a sweep, an acceptance run, a phone pairing check, a person asked to
try something) first proves the running build IS the build the test means. The running build says
what it is at ``/api/runtime/version`` (commit, dirty flag, diff fingerprint, release). The build a
test means comes from ONE heads manifest that lists the current head of every lane. Anything else
is refused before a single question is asked:

* an unidentified build (a source copy with no ``.git`` and no ``config/build-source.json``),
* a commit other than the lane's head,
* uncommitted edits the manifest does not list by fingerprint,
* a release other than the lane's release,
* a lane built on another lane's head that has since moved (for example a phone lane built on
  an older chat line head: the exact way an old phone test build passed for the current one).

Stdlib only apart from ``core.build_provenance``, so any Python 3 can run it next to a test
script. The manifest path comes from ``--manifest`` or ``VOOL_HEADS_MANIFEST``; there is no
built-in default location.

    build_identity.py show
    build_identity.py check --url http://127.0.0.1:11535 --lane phone-078
    build_identity.py check-tree <source folder> --lane chat-078
    build_identity.py stamp-copy --from <git checkout> --to <plain copy>
    build_identity.py set-head --lane chat-078 --commit <sha> [--based-on chat-078 ...] [--repo <checkout>]
    build_identity.py stale [--repo <checkout>]

Exit codes: 0 ok, 3 refused (wrong or unknown build, stale lane), 2 usage or manifest error,
4 target not reachable.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.build_provenance import capture_build_source

MANIFEST_SCHEMA = "vool-current-heads/1"
MANIFEST_ENV = "VOOL_HEADS_MANIFEST"
BUILD_SOURCE_RELPATH = Path("config") / "build-source.json"
EXIT_OK = 0
EXIT_USAGE = 2
EXIT_REFUSED = 3
EXIT_UNREACHABLE = 4
_SHA40 = re.compile(r"[0-9a-f]{40}")
_SHA64 = re.compile(r"[0-9a-f]{64}")


class UsageError(Exception):
    pass


# ---------------------------------------------------------------- manifest


def manifest_path(arg: str | None) -> Path:
    value = (arg or os.environ.get(MANIFEST_ENV) or "").strip()
    if not value:
        raise UsageError(f"No heads manifest: pass --manifest or set {MANIFEST_ENV}.")
    return Path(value).expanduser()


def load_manifest(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise UsageError(f"Heads manifest not found: {path}") from exc
    except (OSError, ValueError) as exc:
        raise UsageError(f"Heads manifest unreadable: {path}: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("schema") != MANIFEST_SCHEMA:
        raise UsageError(f"Heads manifest {path} is not a {MANIFEST_SCHEMA} file.")
    lanes = payload.get("lanes")
    if not isinstance(lanes, dict):
        raise UsageError(f"Heads manifest {path} has no lanes.")
    for name, lane in lanes.items():
        head = str((lane or {}).get("head") or "")
        if not _SHA40.fullmatch(head):
            raise UsageError(f"Lane {name} in {path} has no full 40-character head.")
    return payload


def write_manifest(path: Path, payload: dict[str, Any]) -> None:
    """Atomic replace, so a reader never sees half a manifest."""
    payload["schema"] = MANIFEST_SCHEMA
    payload["updated_at"] = _utc_now()
    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".heads-", suffix=".json", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def stale_reasons(manifest: dict[str, Any], lane_name: str) -> list[str]:
    """A lane is stale when a lane it was built on has moved past the head it was built on."""
    lanes = manifest.get("lanes") or {}
    lane = lanes.get(lane_name) or {}
    reasons: list[str] = []
    for base_name, built_on in sorted((lane.get("based_on") or {}).items()):
        base = lanes.get(base_name)
        if base is None:
            reasons.append(f"lane {lane_name} is built on lane {base_name}, which the manifest no longer lists")
            continue
        if str(base.get("head")) != str(built_on):
            reasons.append(
                f"lane {lane_name} is STALE: built on {base_name} {str(built_on)[:8]}, "
                f"but {base_name} is now {str(base.get('head'))[:8]}; rebuild {lane_name} on it first"
            )
    return reasons


# ---------------------------------------------------------------- identity of a target


def evaluate(
    observed: dict[str, Any],
    *,
    expected_commit: str,
    expected_release: str = "",
    allowed_diffs: tuple[str, ...] = (),
    lane_label: str = "",
    stale: list[str] | None = None,
) -> list[str]:
    """Every reason the observed build is not the expected one. Empty list = the right build."""
    reasons: list[str] = list(stale or [])
    label = lane_label or "the expected build"
    commit_full = str(observed.get("commit_full") or "").strip().lower()
    commit = str(observed.get("commit") or "").strip().lower()
    expected = expected_commit.strip().lower()
    if not commit_full and not commit:
        reasons.append(
            "the build is UNIDENTIFIED (no commit stamp: a source copy without .git or "
            "config/build-source.json); stamp it with stamp-copy and restart it"
        )
    elif commit_full:
        if commit_full != expected:
            reasons.append(f"it runs {commit_full[:8]}, but {label} is {expected[:8]}")
    elif len(commit) < 7 or not expected.startswith(commit):
        reasons.append(f"it runs {commit[:8]}, but {label} is {expected[:8]}")
    release = str(observed.get("release_version") or "").strip()
    if expected_release and release != expected_release:
        reasons.append(f"it is release {release or 'unknown'}, but {label} is release {expected_release}")
    if observed.get("dirty"):
        diff = str(observed.get("diff_sha256") or "").strip().lower()
        if not diff:
            reasons.append("it has uncommitted edits with no fingerprint (diff_sha256), so they cannot be checked")
        elif diff not in {d.lower() for d in allowed_diffs}:
            reasons.append(f"it has uncommitted edits (fingerprint {diff[:12]}) that {label} does not list")
    return reasons


def fetch_runtime_identity(url: str, *, timeout: float = 5.0) -> dict[str, Any]:
    base = url.rstrip("/")
    last_error: Exception | None = None
    for path, pick in (("/api/runtime/version", lambda p: p), ("/healthz", lambda p: p.get("runtime") or {})):
        try:
            with urllib.request.urlopen(base + path, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
            stamp = pick(payload)
            if isinstance(stamp, dict) and ("commit" in stamp or "build_id" in stamp):
                return stamp
        except (OSError, ValueError, urllib.error.URLError) as exc:
            last_error = exc
    raise ConnectionError(f"no build stamp at {base} ({last_error})")


def _git(root: Path, *args: str) -> str:
    try:
        return subprocess.run(
            ["git", "-C", str(root), *args], capture_output=True, text=True, timeout=20, check=True
        ).stdout.strip()
    except Exception:
        return ""


def tree_identity(root: Path) -> dict[str, Any]:
    """What a source folder WOULD report if started, using the runtime's own precedence:
    a stamped config/build-source.json first, then git only when the folder is its own repo."""
    root = root.resolve()
    stamp_path = root / BUILD_SOURCE_RELPATH
    if stamp_path.is_file():
        try:
            stamp = json.loads(stamp_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            stamp = {}
        commit = str(stamp.get("commit_full") or stamp.get("commit") or "").strip()
        if commit:
            return {
                "commit_full": str(stamp.get("commit_full") or "") or (commit if _SHA40.fullmatch(commit) else ""),
                "commit": commit[:12],
                "dirty": bool(stamp.get("dirty_state")),
                "diff_sha256": str(stamp.get("diff_sha256") or ""),
                "source": "stamp",
            }
    toplevel = _git(root, "rev-parse", "--show-toplevel")
    if not toplevel or Path(toplevel).resolve() != root:
        return {"commit_full": "", "commit": "", "dirty": False, "source": "none"}
    source = capture_build_source(root)
    return {
        "commit_full": str(source.get("commit_full") or ""),
        "commit": str(source.get("commit") or ""),
        "dirty": bool(source.get("dirty_state")),
        "diff_sha256": str(source.get("diff_sha256") or ""),
        "source": "git",
    }


def _release_of_tree(root: Path) -> str:
    # Same order as core.release_channel.load_release_manifest.
    for rel in (Path("config") / "release" / "update_channel.json", Path("config") / "release" / "update_channel.sample.json"):
        try:
            payload = json.loads((root / rel).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        value = str(payload.get("release_version") or "").strip()
        if value:
            return value
    return ""


# ---------------------------------------------------------------- commands


def _expectation(args: argparse.Namespace) -> tuple[str, str, tuple[str, ...], str, list[str]]:
    """(commit, release, allowed diffs, label, stale reasons) from --expect-commit or the manifest lane."""
    allowed = tuple(args.allow_diff or ())
    if args.expect_commit:
        if not _SHA40.fullmatch(args.expect_commit.lower()):
            raise UsageError("--expect-commit needs the full 40-character commit.")
        return args.expect_commit.lower(), args.expect_release or "", allowed, f"commit {args.expect_commit[:8]}", []
    if not args.lane:
        raise UsageError("Name the lane (--lane) or the exact commit (--expect-commit).")
    manifest = load_manifest(manifest_path(args.manifest))
    lane = (manifest.get("lanes") or {}).get(args.lane)
    if lane is None:
        known = ", ".join(sorted(manifest.get("lanes") or {})) or "none"
        raise UsageError(f"Lane {args.lane} is not in the heads manifest (lanes: {known}).")
    allowed = allowed + tuple(str(d) for d in (lane.get("allowed_diffs") or ()))
    release = args.expect_release or str(lane.get("release") or "")
    return str(lane["head"]), release, allowed, f"lane {args.lane} head", stale_reasons(manifest, args.lane)


def _report(target: str, observed: dict[str, Any], reasons: list[str], args: argparse.Namespace) -> int:
    commit = str(observed.get("commit_full") or observed.get("commit") or "")[:8] or "no-commit"
    what = f"{commit}{'+dirty' if observed.get('dirty') else ''} ({observed.get('release_version') or 'release unknown'})"
    if args.json:
        print(json.dumps({"ok": not reasons, "target": target, "observed": observed, "refusals": reasons}, indent=2, sort_keys=True))
    elif reasons:
        print(f"REFUSED {target} runs {what}. Do not test this build:")
        for reason in reasons:
            print(f"  - {reason}")
    else:
        print(f"OK {target} runs {what}, the expected build.")
    return EXIT_REFUSED if reasons else EXIT_OK


def cmd_check(args: argparse.Namespace) -> int:
    commit, release, allowed, label, stale = _expectation(args)
    try:
        observed = fetch_runtime_identity(args.url, timeout=args.timeout)
    except ConnectionError as exc:
        print(f"UNREACHABLE {exc}", file=sys.stderr)
        return EXIT_UNREACHABLE
    reasons = evaluate(observed, expected_commit=commit, expected_release=release, allowed_diffs=allowed, lane_label=label, stale=stale)
    return _report(args.url, observed, reasons, args)


def cmd_check_tree(args: argparse.Namespace) -> int:
    commit, release, allowed, label, stale = _expectation(args)
    root = Path(args.path)
    if not root.is_dir():
        raise UsageError(f"Not a folder: {root}")
    observed = tree_identity(root)
    observed["release_version"] = _release_of_tree(root.resolve())
    reasons = evaluate(observed, expected_commit=commit, expected_release=release, allowed_diffs=allowed, lane_label=label, stale=stale)
    return _report(str(root), observed, reasons, args)


def copy_differences(src: Path, dst: Path) -> list[str]:
    """Source files that differ between a checkout and its copy: every tracked file as it is on disk
    now (edits included, deletions must be deleted in the copy too) and every untracked, non-ignored
    file. A stamp on a copy that drifted from its checkout would be a lie, so any difference refuses."""
    listed = subprocess.run(
        ["git", "-C", str(src), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        capture_output=True, timeout=60, check=True,
    ).stdout.decode("utf-8", "surrogateescape")
    differing: list[str] = []
    for name in sorted({n for n in listed.split("\0") if n}):
        a, b = src / name, dst / name
        a_exists, b_exists = a.is_file() or a.is_symlink(), b.is_file() or b.is_symlink()
        if not a_exists and not b_exists:
            continue
        if a_exists != b_exists:
            differing.append(f"{name} ({'missing in the copy' if a_exists else 'deleted in the checkout, present in the copy'})")
            continue
        try:
            same = (os.readlink(a) == os.readlink(b)) if a.is_symlink() else (a.read_bytes() == b.read_bytes())
        except OSError:
            same = False
        if not same:
            differing.append(f"{name} (content differs)")
    return differing


def cmd_stamp_copy(args: argparse.Namespace) -> int:
    src = Path(args.src).resolve()
    dst = Path(args.dst).resolve()
    if not dst.is_dir():
        raise UsageError(f"Copy folder does not exist: {dst}")
    if (dst / ".git").exists():
        raise UsageError(f"{dst} is a git checkout; it identifies itself, so it needs no stamp.")
    toplevel = _git(src, "rev-parse", "--show-toplevel")
    if not toplevel or Path(toplevel).resolve() != src:
        raise UsageError(f"{src} is not the top of a git checkout, so it cannot vouch for the copy.")
    source = capture_build_source(src)
    if not _SHA40.fullmatch(str(source.get("commit_full") or "")):
        raise UsageError(f"Could not read the commit of {src}.")
    differing = copy_differences(src, dst)
    if differing:
        print(f"REFUSED {dst} does not hold the same files as {src}, so it cannot carry its stamp:")
        for name in differing[:20]:
            print(f"  - {name}")
        if len(differing) > 20:
            print(f"  - ... and {len(differing) - 20} more")
        return EXIT_REFUSED
    target = dst / BUILD_SOURCE_RELPATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(source, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    dirty = f" with uncommitted edits {source.get('diff_sha256', '')[:12]}" if source.get("dirty_state") else ""
    print(f"STAMPED {dst} as {source['commit_full'][:8]}{dirty}; every source file matches the checkout.")
    return EXIT_OK


def cmd_set_head(args: argparse.Namespace) -> int:
    path = manifest_path(args.manifest)
    payload = load_manifest(path) if path.exists() else {"schema": MANIFEST_SCHEMA, "lanes": {}}
    lanes = payload.setdefault("lanes", {})
    commit = args.commit.lower()
    if not _SHA40.fullmatch(commit):
        raise UsageError("--commit needs the full 40-character commit.")
    for diff in args.allow_diff or ():
        if not _SHA64.fullmatch(diff.lower()):
            raise UsageError(f"--allow-diff needs a 64-character diff_sha256, got {diff!r}.")
    based_on: dict[str, str] = {}
    for base_name in args.based_on or ():
        base = lanes.get(base_name)
        if base is None:
            raise UsageError(f"--based-on lane {base_name} is not in the manifest.")
        base_head = str(base["head"])
        if args.repo:
            repo = Path(args.repo)
            ok = subprocess.run(
                ["git", "-C", str(repo), "merge-base", "--is-ancestor", base_head, commit], capture_output=True
            ).returncode == 0
            if not ok:
                print(
                    f"REFUSED {commit[:8]} does not contain {base_name} head {base_head[:8]}; "
                    f"merge {base_name} into this lane first.",
                )
                return EXIT_REFUSED
        based_on[base_name] = base_head
    lane = dict(lanes.get(args.lane) or {})
    lane["head"] = commit
    for key in ("branch", "bundle", "release", "note"):
        value = getattr(args, key)
        if value is not None:
            lane[key] = value
    if based_on or args.based_on is not None:
        lane["based_on"] = based_on
    # New head, new candidate: fingerprints listed for the old head never carry over.
    lane["allowed_diffs"] = [d.lower() for d in (args.allow_diff or ())]
    lane["updated_at"] = _utc_now()
    lanes[args.lane] = lane
    write_manifest(path, payload)
    print(f"SET lane {args.lane} head {commit[:8]} in {path}")
    for name in sorted(lanes):
        for reason in stale_reasons(payload, name):
            print(f"  - {reason}")
    return EXIT_OK


def cmd_show(args: argparse.Namespace) -> int:
    payload = load_manifest(manifest_path(args.manifest))
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return EXIT_OK
    print(f"heads manifest, updated {payload.get('updated_at', 'unknown')}")
    for name, lane in sorted((payload.get("lanes") or {}).items()):
        extra = []
        if lane.get("release"):
            extra.append(str(lane["release"]))
        if lane.get("branch"):
            extra.append(str(lane["branch"]))
        if lane.get("based_on"):
            extra.append("on " + ", ".join(f"{k} {str(v)[:8]}" for k, v in sorted(lane["based_on"].items())))
        print(f"  {name:<22} {str(lane['head'])[:12]}  {' | '.join(extra)}")
        for reason in stale_reasons(payload, name):
            print(f"      - {reason}")
    return EXIT_OK


def cmd_stale(args: argparse.Namespace) -> int:
    payload = load_manifest(manifest_path(args.manifest))
    reasons = [r for name in sorted(payload.get("lanes") or {}) for r in stale_reasons(payload, name)]
    for reason in reasons:
        print(f"STALE {reason}")
    if not reasons:
        print("OK no lane is stale.")
    return EXIT_REFUSED if reasons else EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Refuse to test the wrong VOOL build.")
    parser.add_argument("--manifest", help=f"heads manifest (default: ${MANIFEST_ENV})")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    sub = parser.add_subparsers(dest="command", required=True)

    def expectation_args(p: argparse.ArgumentParser) -> None:
        p.add_argument("--lane", help="lane in the heads manifest")
        p.add_argument("--expect-commit", help="exact 40-character commit instead of a lane")
        p.add_argument("--expect-release", help="release_version the build must report")
        p.add_argument("--allow-diff", action="append", help="diff_sha256 of uncommitted edits to accept (repeatable)")

    check = sub.add_parser("check", help="check a running VOOL")
    check.add_argument("--url", required=True)
    check.add_argument("--timeout", type=float, default=5.0)
    expectation_args(check)
    check.set_defaults(func=cmd_check)

    tree = sub.add_parser("check-tree", help="check a source folder before starting it")
    tree.add_argument("path")
    expectation_args(tree)
    tree.set_defaults(func=cmd_check_tree)

    stamp = sub.add_parser("stamp-copy", help="make a plain source copy identifiable")
    stamp.add_argument("--from", dest="src", required=True, help="the git checkout the copy was made from")
    stamp.add_argument("--to", dest="dst", required=True, help="the copy")
    stamp.set_defaults(func=cmd_stamp_copy)

    head = sub.add_parser("set-head", help="record a lane's new head")
    head.add_argument("--lane", required=True)
    head.add_argument("--commit", required=True)
    head.add_argument("--branch")
    head.add_argument("--bundle")
    head.add_argument("--release")
    head.add_argument("--note")
    head.add_argument("--based-on", action="append", help="lane this head was built on (repeatable)")
    head.add_argument("--allow-diff", action="append", help="diff_sha256 of accepted uncommitted edits")
    head.add_argument("--repo", help="checkout holding both commits, to prove the head contains each base")
    head.set_defaults(func=cmd_set_head)

    show = sub.add_parser("show", help="list lanes and stale warnings")
    show.set_defaults(func=cmd_show)

    stale = sub.add_parser("stale", help="exit 3 when any lane is stale")
    stale.set_defaults(func=cmd_stale)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except UsageError as exc:
        print(f"ERROR {exc}", file=sys.stderr)
        return EXIT_USAGE


if __name__ == "__main__":
    raise SystemExit(main())
