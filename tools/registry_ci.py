"""Trusted CI entry point: candidate packages are data; missing attestations fail closed."""
from __future__ import annotations

import argparse
import hashlib
import subprocess
import time
import urllib.parse
from pathlib import Path

from core.registry.admission import admit
from core.registry.artifact import inspect
from core.registry.client import fetch
from core.registry.codec import COMMIT, HEX, MAX_DOCUMENT, RegistryError, canonical, decode, require
from core.registry.trust import root_document, verify
from tools.community_skills import preflight


def latest_release(*, request=fetch) -> dict:
    base = "https://api.github.com/repos/Parad0x-Labs/vool/"
    release = decode(request(base + "releases/latest", limit=1024 * 1024), 1024 * 1024)
    require(isinstance(release, dict) and release.get("draft") is False and release.get("prerelease") is False,
            "latest VOOL release is not published and stable")
    tag = release.get("tag_name", "")
    require(isinstance(tag, str) and 0 < len(tag) <= 80, "release tag is missing")
    ref = decode(request(base + "git/ref/tags/" + urllib.parse.quote(tag, safe=""), limit=65536), 65536)
    obj = ref.get("object", {})
    for _ in range(4):
        require(COMMIT.fullmatch(str(obj.get("sha", ""))), "release source commit is missing")
        if obj.get("type") == "commit":
            return {"commit": obj["sha"], "version": tag.removeprefix("v")}
        require(obj.get("type") == "tag", "release ref is not a commit or annotated tag")
        obj = decode(request(base + "git/tags/" + obj["sha"], limit=65536), 65536).get("object", {})
    raise RegistryError("release tag indirection limit exceeded")


def bounded_file(path: Path) -> bytes:
    require(path.is_file() and not path.is_symlink(), "CI input must be a regular file")
    with path.open("rb") as source:
        raw = source.read(MAX_DOCUMENT + 1)
    require(len(raw) <= MAX_DOCUMENT, "CI input exceeds byte limit")
    return raw


def check_candidate(raw: bytes, review: dict, policy: dict, root: dict, *, publication_commit: str,
                    released: dict, scratch: Path, now: int) -> dict:
    artifact = inspect(raw)
    root_document(root, now)
    report = preflight(raw, scratch)
    require(not report["findings"], "native preflight has unresolved findings")
    evidence = verify(review, root, "review")
    require(evidence.get("publication", {}).get("commit") == publication_commit and
            bool(COMMIT.fullmatch(publication_commit)), "review refers to a different candidate commit")
    require(all(target["commit"] == released["commit"] and target["version"] == released["version"]
                for target in policy["targets"]), "policy does not test the latest released VOOL build")
    require(evidence["policy_sha256"] in root["policies"], "policy is not approved by the offline root")
    value = admit(artifact, policy, evidence, now=now)
    return {"status": "eligible_for_admission", "artifact_sha256": artifact["sha256"],
            "native_digest": artifact["native_digest"], "evidence_sha256": value["evidence_sha256"],
            "admitted": False, "notice": "Maintainer merge and offline admission signatures remain required."}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packages", required=True, type=Path)
    parser.add_argument("--reviews", required=True, type=Path)
    parser.add_argument("--policy", required=True, type=Path)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--publication-commit", required=True)
    parser.add_argument("--scratch", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        released = latest_release()
        source_root = Path(__file__).resolve().parents[1]
        actual = subprocess.run(["git", "-C", str(source_root), "rev-parse", "HEAD"], check=True,
                                capture_output=True, text=True, timeout=10).stdout.strip()
        require(actual == released["commit"], "CI tooling is not the actual latest released VOOL source")
        root = decode(bounded_file(args.root))["signed"]
        verify(decode(bounded_file(args.root)), root, "root")
        policy = decode(bounded_file(args.policy))
        require(args.packages.is_dir() and not args.packages.is_symlink(), "candidate package directory missing")
        files = sorted(args.packages.iterdir())
        require(0 < len(files) <= 100, "CI candidate batch must contain 1 to 100 packages")
        for file in files:
            require(file.suffix == ".json" and HEX.fullmatch(file.stem), "package filename must be its checksum")
            raw = bounded_file(file)
            require(hashlib.sha256(raw).hexdigest() == file.stem, "candidate filename checksum mismatch")
            review_path = args.reviews / (file.stem + ".json")
            require(review_path.exists(), "independent signed review missing; submission remains held")
            result = check_candidate(raw, decode(bounded_file(review_path)), policy, root,
                publication_commit=args.publication_commit, released=released, scratch=args.scratch, now=int(time.time()))
            print(canonical(result).decode())
        return 0
    except (RegistryError, OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        print(canonical({"status": "held", "admitted": False,
            "reason": "Missing or invalid published-release pin, complete scanner reports, source binding or review signatures."}).decode())
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
