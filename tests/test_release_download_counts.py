"""The README download badges count app installers only, summed over every release."""

from __future__ import annotations

import json

from ops import release_download_counts as counts_mod

# Asset names as published on the v0.6.0 to v0.7.2 releases.
_V072 = [
    ("VOOL-0.7.2-beta-macos-arm64.dmg", 2),
    ("VOOL-0.7.2-beta-macos-arm64.dmg.sha256", 1),
    ("VOOL-0.7.2-beta-macos-arm64-update.zip", 5),
    ("VOOL-0.7.2-beta-macos-x86_64.dmg", 2),
    ("VOOL-0.7.2-beta-macos-x86_64-update.zip", 4),
    ("vool-0.7.2-beta-linux-x86_64.tar.gz", 2),
    ("vool-0.7.2-beta-linux-x86_64.tar.gz.sha256", 3),
    ("vool-0.7.2-beta-linux-update.zip", 6),
    ("manifest.json", 2),
]
_V060 = [
    ("VOOL-0.6.0-beta-macos-arm64.dmg", 1),
    ("VOOL-source-e1034c9b.tar.gz", 1),
    ("SHA256SUMS", 2),
    ("BUILD_MANIFEST.json", 2),
]


def _release(assets, *, draft=False):
    return {"draft": draft, "assets": [{"name": n, "download_count": c} for n, c in assets]}


def test_only_installers_count_and_versions_add_up():
    counts = counts_mod.count_installers([_release(_V072), _release(_V060)])
    assert counts == {"macos-arm64": 3, "macos-intel": 2, "linux": 2}


def test_draft_release_downloads_are_not_counted():
    counts = counts_mod.count_installers([_release(_V072), _release(_V072, draft=True)])
    assert counts == {"macos-arm64": 2, "macos-intel": 2, "linux": 2}


def test_total_badge_is_the_sum_of_the_three_app_badges():
    rendered = counts_mod.badges({"macos-arm64": 24, "macos-intel": 19, "linux": 26})
    assert rendered["apps"]["message"] == "69 downloads"
    assert rendered["linux"] == {
        "schemaVersion": 1,
        "label": "Linux, all versions",
        "message": "26 downloads",
        "color": "eee8d5",
    }


def test_writes_one_endpoint_file_per_badge(tmp_path, monkeypatch):
    monkeypatch.setattr(counts_mod, "fetch_releases", lambda repo, token: [_release(_V072), _release(_V060)])
    assert counts_mod.main(["--repo", "o/r", "--out", str(tmp_path)]) == 0
    files = sorted(p.name for p in tmp_path.iterdir())
    assert files == ["apps.json", "linux.json", "macos-arm64.json", "macos-intel.json"]
    assert json.loads((tmp_path / "apps.json").read_text())["message"] == "7 downloads"


def test_no_published_release_writes_nothing_and_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(counts_mod, "fetch_releases", lambda repo, token: [_release(_V072, draft=True)])
    out = tmp_path / "badges"
    assert counts_mod.main(["--repo", "o/r", "--out", str(out)]) == 1
    assert not out.exists()
