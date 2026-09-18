from __future__ import annotations

from pathlib import Path

from installer.patch_openclaw_session_retry import (
    apply,
    is_patchable,
    is_patched,
    patch_text,
    unpatch_text,
)

# A faithful slice of OpenClaw's get-reply bundle around the conflict site.
_SNIPPET = (
    "async function initSessionState(params) {\n"
    "\treturn await initSessionStateAttempt(params, false);\n"
    "}\n"
    "async function initSessionStateAttempt(params, staleSnapshotRetried) {\n"
    "\tconst committed = await commit();\n"
    "\tif (!committed.ok) {\n"
    "\t\tif (!staleSnapshotRetried) return await initSessionStateAttempt(params, true);\n"
    "\t\tthrow new Error(`reply session initialization conflicted for ${sessionKey}`);\n"
    "\t}\n"
    "\treturn sessionEntry;\n"
    "}\n"
)


def test_patch_adds_bounded_retry_and_counter() -> None:
    out = patch_text(_SNIPPET)
    assert is_patched(out)
    assert "staleSnapshotRetried < 6" in out
    assert "setTimeout" in out and "Math.pow(2, staleSnapshotRetried)" in out
    assert "initSessionStateAttempt(params, 0)" in out              # entry became a counter
    assert "initSessionStateAttempt(params, staleSnapshotRetried + 1)" in out
    # The original single-retry boolean forms are gone.
    assert "initSessionStateAttempt(params, false)" not in out
    assert "initSessionStateAttempt(params, true)" not in out
    # The throw is preserved as the final give-up.
    assert "reply session initialization conflicted" in out


def test_patch_is_idempotent() -> None:
    once = patch_text(_SNIPPET)
    twice = patch_text(once)
    assert twice == once


def test_unpatch_restores_original_exactly() -> None:
    assert unpatch_text(patch_text(_SNIPPET)) == _SNIPPET


def test_is_patchable_detects_both_states() -> None:
    assert is_patchable(_SNIPPET)
    assert is_patchable(patch_text(_SNIPPET))
    assert not is_patchable("some unrelated bundle without the conflict site")


def test_apply_round_trip_on_file(tmp_path: Path) -> None:
    f = tmp_path / "get-reply-abc123.js"
    f.write_text(_SNIPPET, encoding="utf-8")
    assert apply(f) == "patched"
    assert apply(f) == "already-correct"
    assert "/*vool-session-retry*/" in f.read_text(encoding="utf-8")
    assert apply(f, remove=True) == "removed"
    assert f.read_text(encoding="utf-8") == _SNIPPET
    assert apply(f, remove=True) == "already-correct"
