"""A killed proof worker may not leave a deliberate defect in tracked source.

`try/finally` covers every failure Python can see. It does not cover the one a proof harness
actually meets: a worker reaped on a timeout, a supervisor's SIGKILL, ctrl-C twice. `finally` never
runs, the mutation stays on disk, and the next run measures a tree nobody intended -- green.

Everything here is executed. The SIGKILL case launches a real child that really mutates a real
tracked file and is really killed with signal 9, which is the one signal a process cannot handle.
"""
from __future__ import annotations

import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tests import _source_guard as guard

# Imported rather than discovered -- see `_fixtures` for why this is not a conftest.
from tests.semantic_phase0._fixtures import (  # noqa: F401
    block_outbound_network,
    keep_the_checkout_clean,
    make_agent_module,
    pin_the_signing_key_passphrase,
    reseal_network_after_function_fixtures,
)

#: A tracked production file with a stable anchor, used as the mutation subject. Restored by every
#: path under test; asserted byte-identical afterwards, never assumed.
_SUBJECT = "core/semantic/reach.py"


@pytest.fixture(autouse=True)
def leave_no_journal_behind():
    """Whatever a case does, the next one starts from a repaired tree and an empty journal dir."""
    yield
    guard.recover_orphaned()


def _subject_bytes() -> bytes:
    return (guard.REPO_ROOT / _SUBJECT).read_bytes()


def test_a_normal_exit_restores_and_clears_the_journal() -> None:
    original = _subject_bytes()
    with guard.mutated_source(_SUBJECT, original + b"\n# mutated\n") as path:
        assert path.read_bytes() != original, "the mutation must really be on disk"
        assert guard._journal_path(_SUBJECT).is_file(), "the journal must exist WHILE mutated"
    assert _subject_bytes() == original
    assert not guard._journal_path(_SUBJECT).exists()


def test_an_exception_restores_and_clears_the_journal() -> None:
    original = _subject_bytes()
    with pytest.raises(RuntimeError, match="boom"), guard.mutated_source(_SUBJECT, original + b"\n# x\n"):
        raise RuntimeError("boom")
    assert _subject_bytes() == original
    assert not guard._journal_path(_SUBJECT).exists()


def test_the_journal_is_written_before_the_mutation_not_after() -> None:
    """Order is the design. Mutating first leaves a window where the defect is on disk and the
    original is nowhere -- exactly the window a kill lands in."""
    original = _subject_bytes()
    seen: list[tuple[bool, bool]] = []

    real_write = guard._write_journal

    def _observing_write(relative_path: str, content: bytes):
        journal = real_write(relative_path, content)
        seen.append((journal.is_file(), _subject_bytes() == original))
        return journal

    guard._write_journal = _observing_write  # type: ignore[assignment]
    try:
        with guard.mutated_source(_SUBJECT, original + b"\n# y\n"):
            pass
    finally:
        guard._write_journal = real_write  # type: ignore[assignment]

    assert seen == [(True, True)], (
        "at the moment the journal is written the file must still hold the ORIGINAL; "
        f"observed {seen!r}"
    )
    assert _subject_bytes() == original


def test_a_sigkilled_worker_leaves_a_journal_that_recovery_repairs() -> None:
    """The case `finally` cannot reach, driven end to end with a real signal 9."""
    original = _subject_bytes()
    marker = "vool-kill-test-marker"
    child = subprocess.Popen(
        [sys.executable, "-m", "tests._source_guard", _SUBJECT, marker],
        cwd=str(guard.REPO_ROOT), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        deadline = time.monotonic() + 30
        line = ""
        while time.monotonic() < deadline:
            line = child.stdout.readline() if child.stdout else ""
            if line.strip() == "MUTATED":
                break
            if child.poll() is not None:
                break
        assert line.strip() == "MUTATED", f"the child never reported mutating (rc={child.poll()})"

        # The defect is genuinely on disk right now. Without this the rest proves nothing.
        assert marker.encode() in _subject_bytes(), "the child's mutation is not visible to us"
        assert guard._journal_path(_SUBJECT).is_file()

        child.kill()  # SIGKILL: no handler, no atexit, no `finally`
        child.wait(timeout=30)
        assert child.returncode != 0
    finally:
        if child.poll() is None:  # pragma: no cover - only on an unexpected path
            child.kill()
            child.wait(timeout=30)
        if child.stdout:
            child.stdout.close()
        if child.stderr:
            child.stderr.close()

    # Still mutated -- which is the actual state of the world, and the point.
    assert marker.encode() in _subject_bytes(), (
        "a SIGKILLed child cannot restore anything; if this passes the test is not testing a kill"
    )

    repaired = guard.recover_orphaned()
    assert _SUBJECT in repaired, f"recovery did not repair the subject: {repaired!r}"
    assert _subject_bytes() == original, "recovery must restore the file byte for byte"
    assert not guard._journal_path(_SUBJECT).exists()


def test_the_controller_fails_closed_on_a_dirty_tree() -> None:
    """A green proof over a modified tracked tree is not a result.

    Asserted against the SUBJECT rather than against a globally clean checkout: this has to hold
    while a developer has unrelated uncommitted work, and a test that only passes on a pristine tree
    would be one more thing to disable.
    """
    original = _subject_bytes()
    before = set(guard.dirty_tracked_paths())
    with guard.mutated_source(_SUBJECT, original + b"\n# dirty\n"):
        dirty = guard.dirty_tracked_paths()
        assert _SUBJECT in dirty, f"git must see the modification: {dirty!r}"
        with pytest.raises(AssertionError, match="tracked source is modified") as excinfo:
            guard.assert_tree_is_clean("probe")
        assert _SUBJECT in str(excinfo.value), "failing closed must name what is dirty"
    # Back to whatever git said before, and byte-identical. Comparing to the pre-existing set
    # rather than to "empty" is what lets this run while other work is uncommitted.
    assert set(guard.dirty_tracked_paths()) == before, "the mutation must leave no trace behind it"
    assert _subject_bytes() == original


def test_recovery_is_idempotent_and_quiet_when_nothing_is_wrong() -> None:
    original = _subject_bytes()
    assert guard.recover_orphaned() == []
    assert guard.recover_orphaned() == []
    assert _subject_bytes() == original


def test_the_mutation_matrix_installs_signal_restoration_and_checks_the_tree() -> None:
    """The controller wiring, asserted where it would otherwise be assumed."""
    source = Path(guard.REPO_ROOT / "scripts" / "semantic_phase0_mutation_matrix.py").read_text(encoding="utf-8")
    assert "install_restore_on_signal" in source, "SIGTERM must restore, not abandon the tree"
    assert "executable_only=True" in source, (
        "the pre-run check must refuse on modified .py files; a doc edit cannot change what a "
        "mutation measures, a modified module can"
    )
    assert "recover_orphaned" in source, "a previous run's wreckage must be repaired at startup"
    assert "assert_tree_is_clean" in source, "success may not be declared over a dirty tree"


def test_a_truncated_journal_fails_closed_rather_than_being_guessed_at() -> None:
    """The crash window Codex found: the matrix wrote the journal TWICE.

    The second write truncated a good journal while the source on disk was already mutated, so a
    kill inside that window left a zero-byte journal beside a modified tracked file and
    `recover_orphaned()` had nothing to recover from. Writes are atomic now, and a journal that
    cannot be read is never repaired by guessing -- it makes the whole run refuse.
    """
    original = _subject_bytes()
    with guard.mutated_source(_SUBJECT, original + b"\n# mid-flight\n"):
        journal = guard._journal_path(_SUBJECT)
        assert journal.is_file()
        good = journal.read_bytes()
        try:
            journal.write_bytes(b"")  # exactly what a kill during the old second write produced
            problems = guard.unrecoverable_journals()
            assert problems and "is empty" in problems[0], (
                "the known truncation shape must be diagnosed before generic JSON parsing"
            )
            with pytest.raises(AssertionError, match="UNKNOWN"):
                guard.assert_tree_is_clean("probe", executable_only=True)
            assert guard.recover_orphaned() == [], "recovery must not invent contents for it"
            assert journal.is_file(), "an unreadable journal must STAY so the run keeps refusing"
        finally:
            journal.write_bytes(good)
    assert _subject_bytes() == original
    assert not guard.unrecoverable_journals()


def test_a_journal_that_disagrees_with_its_own_checksum_is_refused() -> None:
    original = _subject_bytes()
    with guard.mutated_source(_SUBJECT, original + b"\n# tampered\n"):
        journal = guard._journal_path(_SUBJECT)
        good = journal.read_bytes()
        try:
            import base64
            import json

            payload = json.loads(good.decode("utf-8"))
            payload["original_b64"] = base64.b64encode(b"not the original at all").decode("ascii")
            journal.write_text(json.dumps(payload), encoding="utf-8")
            broken = guard.unrecoverable_journals()
            assert broken and "checksum" in broken[0], broken
            assert guard.recover_orphaned() == [], "a journal that fails its checksum restores nothing"
        finally:
            journal.write_bytes(good)
    assert _subject_bytes() == original


def test_the_journal_write_is_atomic_and_leaves_no_partial_behind() -> None:
    """There is no moment where the journal exists and is incomplete."""
    original = _subject_bytes()
    with guard.mutated_source(_SUBJECT, original + b"\n# atomic\n"):
        journal = guard._journal_path(_SUBJECT)
        assert journal.is_file() and journal.stat().st_size > 0
        assert not list(guard.JOURNAL_DIR.glob("*.partial")), "a completed write leaves no temp file"
        assert not guard.unrecoverable_journals()
    assert _subject_bytes() == original


def test_a_stranded_partial_is_debris_and_is_swept(tmp_path) -> None:
    """`os.replace` is atomic, so a `.partial` means the destination was never touched."""
    guard.JOURNAL_DIR.mkdir(parents=True, exist_ok=True)
    stranded = guard.JOURNAL_DIR / "core__semantic__reach.py.json.partial"
    stranded.write_bytes(b'{"path": "core/semantic/reach.py", "original_b64": "")')
    try:
        assert guard.unrecoverable_journals() == [], "a partial is not a journal and must not block"
        guard.recover_orphaned()
        assert not stranded.exists(), "recovery must sweep stranded partials"
    finally:
        stranded.unlink(missing_ok=True)


def test_the_matrix_writes_each_journal_exactly_once() -> None:
    """The double write WAS the window. Counted rather than described."""
    from pathlib import Path

    source = Path(guard.REPO_ROOT / "scripts" / "semantic_phase0_mutation_matrix.py").read_text(encoding="utf-8")
    assert source.count("_write_journal(") == 1, (
        "the journal is written more than once per mutation; the second write truncates a good "
        "journal while the source is already changed, which is the crash window"
    )


def test_stale_self_consistent_journal_cannot_clobber_trusted_source(tmp_path, monkeypatch) -> None:
    root = tmp_path / "repo"
    target = root / "core" / "truth.py"
    target.parent.mkdir(parents=True)
    trusted = b"correct-head\n"
    target.write_bytes(trusted)
    monkeypatch.setattr(guard, "REPO_ROOT", root)
    monkeypatch.setattr(guard, "JOURNAL_DIR", root / ".journal")
    monkeypatch.setattr(guard, "_trusted_head_bytes", lambda _path: trusted)

    journal = guard._write_journal("core/truth.py", b"stale-wrong\n")
    assert guard.recover_orphaned() == []
    assert target.read_bytes() == trusted, "journal self-consistency must never outrank git HEAD"
    broken = guard.unrecoverable_journals()
    assert broken and "disagrees with git HEAD" in broken[0]
    assert journal.exists(), "unknown provenance must stay visible and fail closed"


def test_two_journals_for_one_path_fail_before_either_is_applied(tmp_path, monkeypatch) -> None:
    root = tmp_path / "repo"
    target = root / "core" / "truth.py"
    target.parent.mkdir(parents=True)
    trusted = b"correct-head\n"
    target.write_bytes(b"mutated\n")
    monkeypatch.setattr(guard, "REPO_ROOT", root)
    monkeypatch.setattr(guard, "JOURNAL_DIR", root / ".journal")
    monkeypatch.setattr(guard, "_trusted_head_bytes", lambda _path: trusted)

    first = guard._write_journal("core/truth.py", trusted)
    first.rename(guard.JOURNAL_DIR / "first.json")
    second = guard._write_journal("core/truth.py", b"conflicting\n")
    second.rename(guard.JOURNAL_DIR / "second.json")
    assert guard.recover_orphaned() == []
    assert target.read_bytes() == b"mutated\n", "recovery must not guess which journal wins"
    problems = guard.unrecoverable_journals()
    assert any("multiple journals" in item for item in problems)
    assert any("disagrees with git HEAD" in item for item in problems)


@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGINT])
def test_signal_handler_restores_before_reraising_the_signal(signum) -> None:
    """Exercise the handler caller itself, not just mutation-matrix source wiring."""
    original = _subject_bytes()
    marker = f"signal-{int(signum)}-marker"
    script = """
import sys, time
from tests import _source_guard as guard
relative, marker = sys.argv[1], sys.argv[2]
path = guard.REPO_ROOT / relative
original = path.read_bytes()
guard._write_journal(relative, original)
path.write_bytes(original + ('\\n# ' + marker + '\\n').encode())
guard.install_restore_on_signal(lambda: path.write_bytes(original))
print('READY', flush=True)
while True:
    time.sleep(0.05)
"""
    child = subprocess.Popen(
        [sys.executable, "-c", script, _SUBJECT, marker],
        cwd=str(guard.REPO_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert child.stdout and child.stdout.readline().strip() == "READY"
        assert marker.encode() in _subject_bytes()
        child.send_signal(signum)
        child.wait(timeout=30)
        assert child.returncode != 0
        assert _subject_bytes() == original
        assert not guard._journal_path(_SUBJECT).exists()
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=30)
        if child.stdout:
            child.stdout.close()
        if child.stderr:
            child.stderr.close()
