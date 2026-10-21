"""Count app installer downloads across every published release.

Writes shields.io endpoint badges (https://shields.io/badges/endpoint-badge) for
the three apps and their total. Only installers count: checksum files, update
zips, feed manifests and source tarballs are fetched by scripts and the updater,
so counting them would inflate the numbers people read in the README.

    python ops/release_download_counts.py --repo Parad0x-Labs/vool --out site/badges

Any API error or an empty release list exits non-zero without writing, so the
last published badges stay up instead of being replaced by wrong numbers.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request
from collections.abc import Iterable
from pathlib import Path

API = "https://api.github.com"
COLOR = "eee8d5"

# Badge file stem -> (badge label, installer asset name pattern).
APPS: dict[str, tuple[str, re.Pattern[str]]] = {
    "macos-arm64": ("macOS Apple silicon, all versions", re.compile(r"-macos-arm64\.dmg$")),
    "macos-intel": ("macOS Intel, all versions", re.compile(r"-macos-x86_64\.dmg$")),
    "linux": ("Linux, all versions", re.compile(r"-linux-x86_64\.tar\.gz$")),
}
TOTAL_STEM = "apps"
TOTAL_LABEL = "apps, all versions"


def count_installers(releases: Iterable[dict]) -> dict[str, int]:
    """Sum installer downloads per app over published releases (drafts are skipped)."""
    counts = dict.fromkeys(APPS, 0)
    for release in releases:
        if release.get("draft"):
            continue
        for asset in release.get("assets", []):
            name = str(asset.get("name", ""))
            for stem, (_, pattern) in APPS.items():
                if pattern.search(name):
                    counts[stem] += int(asset.get("download_count", 0))
    return counts


def badge(label: str, downloads: int) -> dict:
    return {"schemaVersion": 1, "label": label, "message": f"{downloads} downloads", "color": COLOR}


def badges(counts: dict[str, int]) -> dict[str, dict]:
    out = {stem: badge(APPS[stem][0], counts[stem]) for stem in APPS}
    out[TOTAL_STEM] = badge(TOTAL_LABEL, sum(counts.values()))
    return out


def fetch_releases(repo: str, token: str | None) -> list[dict]:
    releases: list[dict] = []
    page = 1
    while True:
        request = urllib.request.Request(f"{API}/repos/{repo}/releases?per_page=100&page={page}")
        request.add_header("Accept", "application/vnd.github+json")
        if token:
            request.add_header("Authorization", f"Bearer {token}")
        with urllib.request.urlopen(request, timeout=30) as response:
            batch = json.load(response)
        if not isinstance(batch, list):
            raise ValueError(f"unexpected releases payload on page {page}")
        releases.extend(batch)
        if len(batch) < 100:
            return releases
        page += 1


def write_badges(out_dir: Path, rendered: dict[str, dict]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for stem, payload in rendered.items():
        (out_dir / f"{stem}.json").write_text(json.dumps(payload) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", required=True)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    releases = fetch_releases(args.repo, os.environ.get("GITHUB_TOKEN"))
    if not any(not release.get("draft") for release in releases):
        print("no published releases returned; leaving badges unchanged", file=sys.stderr)
        return 1
    counts = count_installers(releases)
    write_badges(args.out, badges(counts))
    print(json.dumps({**counts, TOTAL_STEM: sum(counts.values())}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
