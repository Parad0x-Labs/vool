"""Build-time provenance stamp: a bundled install (no .git) must still report its exact commit.

Before this, config/build-source.json was never written, so build_source_metadata returned {} and
/api/runtime/version reported commit:"" — a stale build was indistinguishable from a fresh one.
"""
from __future__ import annotations

from pathlib import Path

from core.build_provenance import capture_build_source, write_build_source
from core.web.api.runtime import build_source_metadata

REPO = Path(__file__).resolve().parents[1]


def test_capture_reports_a_real_commit_in_a_git_checkout() -> None:
    src = capture_build_source(REPO)
    assert src.get("source_kind") == "git"
    assert len(str(src.get("commit") or "")) >= 7
    assert len(str(src.get("commit_full") or "")) == 40
    assert str(src["commit_full"]).startswith(str(src["commit"]))
    assert "built_at" in src


def test_capture_never_raises_outside_git(tmp_path) -> None:
    # tmp_path is not a git repo -> unknown source, no exception, no fabricated commit.
    src = capture_build_source(tmp_path)
    assert src.get("source_kind") == "unknown"
    assert not src.get("commit")


def test_write_then_runtime_reader_surfaces_the_commit(tmp_path) -> None:
    # Stamp the repo's SHA into a fake bundle root's config/, then read it back through the SAME
    # reader the runtime version stamp uses. This is the bundle path (no .git under tmp_path).
    write_build_source(REPO, tmp_path / "config" / "build-source.json")
    metadata = build_source_metadata(tmp_path)
    assert metadata.get("source_kind") == "git"
    assert len(str(metadata.get("commit") or "")) >= 7
    stamped = (tmp_path / "config" / "build-source.json").read_text(encoding="utf-8")
    assert '"commit_full"' in stamped
    # dirty_state round-trips as a real bool
    assert isinstance(metadata.get("dirty_state"), bool)
