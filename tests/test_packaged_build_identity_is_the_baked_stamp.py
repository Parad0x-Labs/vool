"""A packaged bundle reports the commit it was BUILT from, never the enclosing checkout's HEAD.

Found by an identity gate on a real bundle: it baked 9bb9f6920891 into build-source.json,
Info.plist NULLASourceSHA and BUILD_MANIFEST.json, and served /healthz commit_full
9b05dae8807e -- the live HEAD of the worktree the bundle sits inside, two commits later.

The old precedence preferred a git query whenever it was "valid", on the stated assumption that
"a packaged install has no .git". A bundle is built into dist/ INSIDE its own worktree, so
`git rev-parse HEAD` from the bundle answers about the enclosing checkout. That makes every
packaged identity check vacuous while the bundle is tested in-tree -- the only place it is
tested before release -- and reports the developer's uncommitted edits as the artifact's dirty
flag.

config/build-source.json is gitignored and written only by the bundle build, so its presence is
the "I am a packaged artifact" signal.
"""
from __future__ import annotations

import json

import pytest

from core.web.api import runtime as rt

BAKED = "9bb9f6920891a985b60a07be5a2ceb8b8163dc18"
CHECKOUT = "9b05dae8807e1786900925d73d5a6c6755209d80"


def _stamp(root):
    return rt.build_runtime_version_stamp(
        project_root=root, runtime_model_tag="m", workstation_version="w"
    )


def _bake(root, **extra):
    (root / "config").mkdir(parents=True, exist_ok=True)
    payload = {"source_kind": "git", "commit_full": BAKED, "commit": BAKED[:12],
               "branch": "build/x", "dirty_state": False}
    payload.update(extra)
    (root / "config" / "build-source.json").write_text(json.dumps(payload))


def test_the_baked_stamp_wins_over_an_enclosing_checkout(tmp_path, monkeypatch):
    _bake(tmp_path)
    # An enclosing repo that would answer with a DIFFERENT, dirty HEAD.
    monkeypatch.setattr(rt, "git_checkout_state", lambda _root: {
        "valid": True, "branch": "someone-elses-branch",
        "commit": CHECKOUT[:12], "commit_full": CHECKOUT, "dirty": True,
    })
    stamp = _stamp(tmp_path)
    assert stamp["commit_full"] == BAKED, "the artifact reported the enclosing checkout's HEAD"
    assert stamp["commit"] == BAKED[:12]
    assert stamp["dirty"] is False, "the developer's uncommitted edits became the artifact's dirty flag"
    assert BAKED[:12] in stamp["build_id"]
    assert ".dirty" not in stamp["build_id"]


def test_a_source_checkout_is_unaffected(tmp_path, monkeypatch):
    """No baked stamp -> git state, exactly as before. This is the dev-checkout path."""
    monkeypatch.setattr(rt, "git_checkout_state", lambda _root: {
        "valid": True, "branch": "main",
        "commit": CHECKOUT[:12], "commit_full": CHECKOUT, "dirty": True,
    })
    stamp = _stamp(tmp_path)
    assert stamp["commit_full"] == CHECKOUT
    assert stamp["dirty"] is True
    assert stamp["build_id"].endswith(".dirty")


def test_a_dirty_build_is_still_reported_dirty(tmp_path, monkeypatch):
    """The bundle's OWN dirty_state must survive -- an override build stamps release=false."""
    _bake(tmp_path, dirty_state=True)
    monkeypatch.setattr(rt, "git_checkout_state", lambda _root: {
        "valid": True, "branch": "b", "commit": CHECKOUT[:12],
        "commit_full": CHECKOUT, "dirty": False,
    })
    stamp = _stamp(tmp_path)
    assert stamp["commit_full"] == BAKED
    assert stamp["dirty"] is True
    assert stamp["build_id"].endswith(".dirty")


def test_a_packaged_stamp_with_no_git_at_all_still_resolves(tmp_path, monkeypatch):
    _bake(tmp_path)
    monkeypatch.setattr(rt, "git_checkout_state", lambda _root: {
        "valid": False, "branch": "", "commit": "", "commit_full": "", "dirty": False,
    })
    assert _stamp(tmp_path)["commit_full"] == BAKED


def test_an_unabbreviated_commit_field_alone_is_enough(tmp_path, monkeypatch):
    """Older stamps wrote the full sha into `commit` with no commit_full."""
    (tmp_path / "config").mkdir(parents=True)
    (tmp_path / "config" / "build-source.json").write_text(
        json.dumps({"source_kind": "git", "commit": BAKED, "branch": "b"})
    )
    monkeypatch.setattr(rt, "git_checkout_state", lambda _root: {
        "valid": True, "branch": "x", "commit": CHECKOUT[:12],
        "commit_full": CHECKOUT, "dirty": True,
    })
    stamp = _stamp(tmp_path)
    assert stamp["commit_full"] == BAKED
