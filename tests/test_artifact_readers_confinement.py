"""The confinement boundary, asserted on what it actually enforces.

Every test here targets a way a reader could be *unsafe while passing its functional tests*:

* a decoder that runs anyway on a machine with no sandbox;
* a decoder that can read the operator's home, or another extraction's scratch directory;
* a decoder that floods this process before any cap is consulted;
* a decoder that outlives its turn, or that ignores the extraction's overall deadline;
* extracted text that skips the credential policy because it did not arrive as `kind="text"`.

The canaries are synthetic throughout: a file this test writes, and credential-*shaped* strings
assembled at runtime. Nothing here reads a real secret, touches the Keychain, or looks at a live
profile.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest

import core.artifact_readers as readers
from core.artifact_readers import _sandbox
from core.artifact_readers._sandbox import (
    Scratch,
    confinement_available,
    extraction_budget,
    run_confined,
)
from core.artifact_readers._types import ReaderFailed, ReaderRefused, ReaderUnavailable
from tests.reader_fixtures import docx_document, native_text_pdf, pdf_with_text, scanned_pdf, zip_archive

pytestmark = pytest.mark.skipif(not confinement_available(), reason="confinement is macOS-only; these assert what it enforces")

#: Credential-SHAPED, assembled at runtime so this file never contains a scannable literal.
CANARY_AWS = "AKIA" + "Q7X4M2NPLVZW9TCD"
#: AWS's own documented example key. It must keep passing, or every document that explains how to
#: configure credentials becomes unattachable.
PLACEHOLDER_AWS = "AKIAIOSFODNN7EXAMPLE"


# --- 1. missing confinement refuses BEFORE spawning ------------------------------------------


def test_no_sandbox_refuses_before_any_process_is_spawned(monkeypatch: pytest.MonkeyPatch):
    """The refusal must come first. A decoder started and then regretted has already run."""
    spawned: list[object] = []

    def _forbidden(*args, **kwargs):
        spawned.append(args)
        raise AssertionError("a decoder was spawned on a machine that cannot confine it")

    monkeypatch.setattr(_sandbox, "confinement_available", lambda: False)
    monkeypatch.setattr(subprocess, "Popen", _forbidden)
    with Scratch("vool-test-") as scratch, pytest.raises(ReaderUnavailable) as caught:
        run_confined(["/bin/echo", "hello"], scratch=scratch.path, fmt="pdf")
    assert caught.value.code == "confinement_unavailable"
    assert caught.value.remediation
    assert spawned == []


def test_capability_report_blocks_every_external_format_without_confinement(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(_sandbox, "confinement_available", lambda: False)
    report = readers.capability_report()
    assert report["confinement_available"] is False
    assert report["confinement_unavailable_reason"]
    for row in report["formats"]:
        if row["external_decoder"]:
            assert row["available"] is False, f"{row['format']} still offered without confinement"
            assert row["blocked_reason"] == report["confinement_unavailable_reason"]
    # ...and the formats that need no external decoder keep working, so the lane degrades rather
    # than collapsing: every ZIP/TEXT-borne format is still readable with the standard library
    # alone, wherever the bytes are parsed in this process instead of by a spawned decoder.
    # (XLS is in-process too, but only where the optional pinned xlrd package is importable.)
    in_process = {"docx", "xlsx", "pptx", "odt", "epub", "rtf", "zip"}
    from core.artifact_readers import documents as reader_documents

    if reader_documents.xlrd_available():
        in_process.add("xls")
    assert {row["format"] for row in report["formats"] if row["available"]} == in_process
    for extension in (".pdf", ".doc", ".rar", ".mp4"):
        assert extension not in report["available_extensions"]


def test_reading_an_external_format_without_confinement_says_why(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(_sandbox, "confinement_available", lambda: False)
    with pytest.raises(ReaderUnavailable) as caught:
        readers.read(native_text_pdf(), name="report.pdf", extension=".pdf")
    assert caught.value.code == "confinement_unavailable"
    # NOT "install a PDF decoder": the decoder is present and working; confinement is what is not.
    assert "unconfined" in caught.value.message


# --- 2. path isolation ------------------------------------------------------------------------


@pytest.fixture
def outside_canary(tmp_path_factory: pytest.TempPathFactory):
    """A synthetic file in a UNIQUE directory this test owns, outside every granted scope.

    Deliberately not `Path.home()`: an earlier version of this test wrote and unlinked a fixed
    filename in the operator's real home, which both violates the no-live-home rule and can
    clobber an existing file. Nothing here reads, writes or deletes anything the operator owns.
    """
    root = tmp_path_factory.mktemp("reader-scope-canary")
    path = root / "unrelated.txt"
    path.write_text("SYNTHETIC-READER-SCOPE-CANARY\n", encoding="utf-8")
    return path


@pytest.fixture
def var_tmp_canary():
    """The exact path class a review demonstrated readable: an unrelated dir under /private/var/tmp.

    Held in its own TemporaryDirectory and removed with it -- a directory this test created and
    nothing else lives in.
    """
    with tempfile.TemporaryDirectory(dir="/private/var/tmp", prefix="reader-scope-") as root:
        path = Path(root) / "unrelated.txt"
        path.write_text("SYNTHETIC-READER-SCOPE-CANARY\n", encoding="utf-8")
        yield path


def test_a_decoder_cannot_read_an_unrelated_file(outside_canary: Path):
    with Scratch("vool-test-") as scratch:
        result = run_confined(["/bin/cat", str(outside_canary)], scratch=scratch.path, fmt="test")
    assert result.returncode != 0
    assert b"SYNTHETIC-READER-SCOPE-CANARY" not in result.stdout


def test_a_decoder_cannot_read_an_unrelated_file_under_var_tmp(var_tmp_canary: Path):
    """The measured regression: this exact class was read in full before the allowlist landed."""
    with Scratch("vool-test-") as scratch:
        # The positive control runs under the SAME profile, so a refusal here cannot be the
        # decoder simply failing to start -- which is how an over-tight profile fakes a pass.
        (scratch.path / "owned.txt").write_text("OWNED-CONTROL\n", encoding="utf-8")
        control = run_confined(["/bin/cat", str(scratch.path / "owned.txt")], scratch=scratch.path, fmt="test")
        assert control.returncode == 0 and b"OWNED-CONTROL" in control.stdout, "control failed: nothing ran"
        result = run_confined(["/bin/cat", str(var_tmp_canary)], scratch=scratch.path, fmt="test")
    assert result.returncode != 0
    assert b"SYNTHETIC-READER-SCOPE-CANARY" not in result.stdout


def test_a_decoder_cannot_read_or_write_a_sibling_scratch_directory():
    """Two extractions run at once; neither may reach into the other's working directory."""
    with Scratch("vool-test-a-") as mine, Scratch("vool-test-b-") as sibling:
        (sibling.path / "their-input.txt").write_text("another extraction's bytes\n", encoding="utf-8")
        read_back = run_confined(["/bin/cat", str(sibling.path / "their-input.txt")], scratch=mine.path, fmt="test")
        assert read_back.returncode != 0
        assert b"another extraction" not in read_back.stdout
        wrote = run_confined(["/usr/bin/touch", str(sibling.path / "planted.txt")], scratch=mine.path, fmt="test")
        assert wrote.returncode != 0
        assert not (sibling.path / "planted.txt").exists()


def test_a_decoder_cannot_write_outside_its_scratch(tmp_path: Path):
    target = tmp_path / "outside.txt"
    with Scratch("vool-test-") as scratch:
        result = run_confined(["/usr/bin/touch", str(target)], scratch=scratch.path, fmt="test")
    assert result.returncode != 0
    assert not target.exists()


def test_a_decoder_can_use_its_own_scratch():
    """The control: the same profile that refuses everything above must still let work happen."""
    with Scratch("vool-test-") as scratch:
        (scratch.path / "in.txt").write_text("mine\n", encoding="utf-8")
        result = run_confined(["/bin/cat", str(scratch.path / "in.txt")], scratch=scratch.path, fmt="test")
        assert result.returncode == 0 and b"mine" in result.stdout


def test_a_decoder_cannot_reach_a_loopback_listener_this_test_owns():
    """An actual connect attempt -- against a listener THIS test starts, never the internet.

    A loopback target keeps the assertion honest in both directions: the unconfined control proves
    the listener is reachable and the probe script works, so a refusal inside the sandbox is the
    sandbox and not a network that happened to be down.
    """
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    try:
        with Scratch("vool-test-") as scratch:
            script = scratch.path / "net.py"
            script.write_text(
                "import socket\n"
                "s = socket.socket()\n"
                "s.settimeout(3)\n"
                "try:\n"
                f"    s.connect(('127.0.0.1', {port}))\n"
                "    print('CONNECTED')\n"
                "except Exception as exc:\n"
                "    print('REFUSED', type(exc).__name__)\n",
                encoding="utf-8",
            )
            unconfined = subprocess.run(
                [sys.executable, "-I", "-S", str(script)], capture_output=True, timeout=60, check=False
            )
            assert b"CONNECTED" in unconfined.stdout, "the control could not reach the listener; the probe is invalid"
            result = run_confined(
                [sys.executable, "-I", "-S", str(script)],
                scratch=scratch.path,
                fmt="test",
                extra_read=_interpreter_scope(),
            )
    finally:
        listener.close()
    assert b"CONNECTED" not in result.stdout, "a confined decoder opened a socket to a loopback listener"


def _interpreter_scope() -> list[str]:
    """The interpreter's own installation tree -- NOT `sys.path`, which holds the repository root."""
    import sysconfig

    return [
        str(Path(path).resolve())
        for path in (sys.base_prefix, sysconfig.get_paths().get("stdlib"), sysconfig.get_paths().get("platstdlib"))
        if path and os.path.isdir(path)
    ]


def test_every_real_decoder_still_works_under_the_content_allowlist():
    """The positive control for the whole read-scope change.

    A profile tight enough to refuse every canary is easy; one that ALSO decodes real formats is
    the actual requirement. Without this, an over-tight profile would look like a security win
    while quietly reading nothing.
    """
    import core.artifact_readers as registry
    from tests.reader_fixtures import (
        docx_document,
        legacy_doc,
        rar_archive,
        sample_video,
        scanned_pdf,
        text_image_png,
        zip_archive,
    )

    assert registry.read(native_text_pdf(), name="r.pdf", extension=".pdf").meta["pages_read"] == 3
    assert "INVOICE 88213" in registry.read(scanned_pdf(["INVOICE 88213"]), name="s.pdf", extension=".pdf").units[0].text
    assert registry.read(docx_document(), name="d.docx", extension=".docx").meta["tables"] == 1
    assert registry.read(zip_archive({"a.txt": b"hi\n"}), name="a.zip", extension=".zip").meta["members"] == 1
    assert registry.read(rar_archive({"a.txt": b"hi\n"}), name="a.rar", extension=".rar").meta["members"] == 1
    assert "TOKEN 4471" in registry.image.read(text_image_png(["TOKEN 4471"]), name="i.png").units[0].text

    document = legacy_doc()
    if document:
        # DOC converts via textutil -stdout with scratch-only writes. No per-user-temp write
        # grant exists; this control proves the decoder still works without widening its scope.
        assert "Regional Performance Review" in registry.read(document, name="l.doc", extension=".doc").units[0].text

    clip = sample_video(seconds=4, fps=5)
    if clip:
        assert registry.read(clip, name="c.mp4", extension=".mp4").meta["frames_sampled"] >= 4


def test_the_pypdf_fallback_works_while_confined():
    """The in-process parser was moved into the sandbox; prove it still reads a real PDF there."""
    from unittest import mock

    from core.artifact_readers import pdf as pdf_reader

    if not pdf_reader._pypdf_importable():
        pytest.skip("the pypdf fallback is not installed on this machine")
    with mock.patch.object(pdf_reader, "toolchain_available", lambda: False):
        result = pdf_reader.read(native_text_pdf(), name="fallback.pdf")
    assert result.meta["pages_read"] == 3
    assert "GRANITE-7741" in result.units[-1].text, "the fallback lost the last page"
    assert "pypdf" in result.extractor


def test_the_fallback_read_scope_excludes_the_repository():
    """`sys.path` holds the repo root; granting it would open the working tree to a hostile PDF.

    Asserted on the scope actually HANDED to `run_confined` during a real fallback read, not on
    the helper in isolation -- a call site that ignores the helper and passes `sys.path` would
    otherwise sail past this test.
    """
    from unittest import mock

    from core.artifact_readers import _sandbox as sandbox_module
    from core.artifact_readers import pdf as pdf_reader

    if not pdf_reader._pypdf_importable():
        pytest.skip("the pypdf fallback is not installed on this machine")
    granted: list[list[str]] = []
    real_run = sandbox_module.run_confined

    def _record(argv, **kwargs):
        granted.append([str(entry) for entry in (kwargs.get("extra_read") or [])])
        return real_run(argv, **kwargs)

    with mock.patch.object(pdf_reader, "toolchain_available", lambda: False), \
         mock.patch.object(pdf_reader, "run_confined", _record):
        pdf_reader.read(native_text_pdf(), name="fallback.pdf")

    assert granted, "the fallback never ran, so no scope was observed"
    repo_root = Path(__file__).resolve().parents[1]
    for scope in granted:
        for entry in scope:
            resolved = Path(entry).resolve()
            assert resolved != repo_root, f"{entry} is the repository root"
            assert repo_root.parent != resolved, f"{entry} contains the repository"
            assert not str(repo_root).startswith(str(resolved) + "/"), f"{entry} would expose the repository"


# --- 3. output caps, deadline, quota, process-group cleanup -----------------------------------


def test_stdout_flood_is_stopped_while_it_streams(monkeypatch: pytest.MonkeyPatch):
    """The cap must bite DURING the read; a decoder must never get to buffer a flood in here."""
    with Scratch("vool-test-") as scratch, pytest.raises(ReaderRefused) as caught:
        run_confined(["/usr/bin/yes", "flood"], scratch=scratch.path, fmt="test", max_stdout=256 * 1024, timeout=30)
    assert caught.value.code == "decoder_output_too_large"


def test_stderr_flood_is_bounded_too():
    with Scratch("vool-test-") as scratch:
        script = scratch.path / "noisy.sh"
        script.write_text("#!/bin/sh\nwhile :; do printf 'x%.0s' $(seq 1 1000) >&2; done\n", encoding="utf-8")
        script.chmod(0o700)
        with pytest.raises(ReaderRefused) as caught:
            run_confined([str(script)], scratch=scratch.path, fmt="test", max_stderr=128 * 1024, timeout=30)
    assert caught.value.code == "decoder_output_too_large"


def _pids_in_group(pgid: int) -> list[int]:
    """PIDs in ONE process group -- this test's own, never every process named like ours.

    A global `ps | grep sleep 600` cannot tell this test's leftovers from a sibling worker's live
    child, so it either flakes under parallel runs or asserts about processes nobody here owns.
    """
    listing = subprocess.run(["/bin/ps", "-eo", "pid=,pgid="], capture_output=True, text=True, check=False).stdout
    found: list[int] = []
    for line in listing.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].isdigit() and int(parts[1]) == pgid:
            found.append(int(parts[0]))
    return found


def test_a_hung_decoder_is_killed_and_leaves_no_process_behind(monkeypatch: pytest.MonkeyPatch):
    """Capture the group this run creates, then assert THAT group is empty afterwards."""
    groups: list[int] = []
    real_popen = subprocess.Popen

    class _Recording(real_popen):  # type: ignore[misc,valid-type]
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            with contextlib.suppress(OSError):
                groups.append(os.getpgid(self.pid))

    monkeypatch.setattr(subprocess, "Popen", _Recording)
    with Scratch("vool-test-") as scratch:
        with pytest.raises(ReaderFailed) as caught:
            run_confined(["/bin/sleep", "600"], scratch=scratch.path, fmt="test", timeout=2)
        assert caught.value.code == "decoder_timeout"
    monkeypatch.undo()
    assert groups, "the run never spawned a process, so nothing was proven"
    time.sleep(0.5)
    for pgid in groups:
        assert _pids_in_group(pgid) == [], f"process group {pgid} still has live members"


def test_a_decoder_that_forks_does_not_outlive_its_turn(monkeypatch: pytest.MonkeyPatch):
    """A grandchild must die with the group, or a document upload leaks a daemon."""
    groups: list[int] = []
    real_popen = subprocess.Popen

    class _Recording(real_popen):  # type: ignore[misc,valid-type]
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            with contextlib.suppress(OSError):
                groups.append(os.getpgid(self.pid))

    monkeypatch.setattr(subprocess, "Popen", _Recording)
    with Scratch("vool-test-") as scratch:
        script = scratch.path / "forker.sh"
        script.write_text("#!/bin/sh\n/bin/sleep 600 &\n/bin/sleep 600\n", encoding="utf-8")
        script.chmod(0o700)
        with pytest.raises(ReaderFailed):
            run_confined([str(script)], scratch=scratch.path, fmt="test", timeout=2)
    monkeypatch.undo()
    assert groups, "the run never spawned a process, so nothing was proven"
    time.sleep(0.5)
    for pgid in groups:
        assert _pids_in_group(pgid) == [], f"a forked grandchild survived in group {pgid}"


def test_the_extraction_deadline_bounds_the_whole_attachment_not_one_call():
    """Per-call timeouts cannot bound 24 frames. The budget is what makes the total real."""
    with Scratch("vool-test-") as scratch, extraction_budget(scratch=scratch.path, seconds=2):
        run_confined(["/bin/sleep", "0.2"], scratch=scratch.path, fmt="test", timeout=30)
        time.sleep(2.1)
        with pytest.raises(ReaderFailed) as caught:
            run_confined(["/bin/echo", "late"], scratch=scratch.path, fmt="test", timeout=30)
    assert caught.value.code == "extraction_timeout"


def test_the_quota_stops_the_child_while_it_is_still_running():
    """The measured regression: the quota used to be checked only after the decoder returned.

    The child writes far past the quota, then sleeps, then writes a completion marker. If the
    watchdog works, the marker never appears -- the process group was killed mid-sleep. If the
    quota is only checked on return, the marker exists and the over-budget decoder finished.
    """
    with Scratch("vool-test-") as scratch, extraction_budget(scratch=scratch.path, seconds=60) as budget:
        script = scratch.path / "over.sh"
        script.write_text(
            "#!/bin/sh\n"
            '/bin/dd if=/dev/zero of="$1/big" bs=1024 count=64 2>/dev/null\n'
            "/bin/sleep 1.5\n"
            'echo done > "$1/marker"\n',
            encoding="utf-8",
        )
        script.chmod(0o700)
        # The entry check must PASS, or the refusal proves nothing about enforcement while running.
        budget.quota_bytes = scratch.bytes_used() + 4096
        started = time.monotonic()
        with pytest.raises(ReaderRefused) as caught:
            run_confined([str(script), str(scratch.path)], scratch=scratch.path, fmt="test", timeout=30)
        elapsed = time.monotonic() - started
        assert caught.value.code == "scratch_quota_exceeded"
        assert not (scratch.path / "marker").exists(), "the over-budget decoder ran to completion"
        assert elapsed < 1.4, f"the refusal took {elapsed:.2f}s -- that is after the child finished, not during"


def test_the_quota_scan_does_not_follow_symlinks_out_of_the_scratch(tmp_path: Path):
    """A link must count as a link, and must not turn the watchdog into a filesystem walk."""
    from core.artifact_readers._sandbox import _directory_bytes

    big = tmp_path / "elsewhere"
    big.mkdir()
    (big / "payload.bin").write_bytes(b"\0" * (256 * 1024))
    with Scratch("vool-test-") as scratch:
        (scratch.path / "link-to-dir").symlink_to(big)
        (scratch.path / "link-to-file").symlink_to(big / "payload.bin")
        measured = _directory_bytes(scratch.path)
    assert measured < 64 * 1024, f"the scan followed a symlink out of the scratch ({measured} bytes)"


def test_the_quota_scan_is_bounded_and_fails_closed():
    """Too many entries to measure means over budget, not 'measured zero'."""
    from core.artifact_readers._sandbox import _directory_bytes

    with Scratch("vool-test-") as scratch:
        for index in range(40):
            (scratch.path / f"f{index}").write_bytes(b"x")
        assert _directory_bytes(scratch.path, max_entries=10) == float("inf")


def test_the_scratch_quota_is_aggregate_not_per_file():
    """RLIMIT_FSIZE bounds ONE file; a decoder writing a thousand small files needs this."""
    with Scratch("vool-test-") as scratch, extraction_budget(scratch=scratch.path, seconds=60) as budget:
        budget.quota_bytes = 512 * 1024
        script = scratch.path / "spray.sh"
        script.write_text(
            "#!/bin/sh\nfor i in $(seq 1 40); do /bin/dd if=/dev/zero of=\"$1/f$i\" bs=32k count=1 2>/dev/null; done\n",
            encoding="utf-8",
        )
        script.chmod(0o700)
        # The quota is checked when the call that filled the scratch RETURNS, so the very run that
        # overspent is the one that fails -- the reader never gets to keep going on a scratch it
        # has already blown past.
        with pytest.raises(ReaderRefused) as caught:
            run_confined([str(script), str(scratch.path)], scratch=scratch.path, fmt="test", timeout=30)
    assert caught.value.code == "scratch_quota_exceeded"
    assert "KB of intermediate data" in caught.value.message


def test_confined_runs_are_safe_from_several_threads_at_once():
    """The served runtime is threaded; the spawn path must not do Python work between fork+exec."""
    errors: list[BaseException] = []
    outputs: list[bytes] = []

    def worker(index: int) -> None:
        try:
            with Scratch(f"vool-thread-{index}-") as scratch:
                result = run_confined(["/bin/echo", f"thread-{index}"], scratch=scratch.path, fmt="test", timeout=30)
                outputs.append(result.stdout.strip())
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(index,)) for index in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=120)
    assert not errors, f"threaded confined runs failed: {errors[:2]}"
    assert sorted(outputs) == sorted(f"thread-{index}".encode() for index in range(8))


# --- 4. the credential policy follows the extraction ------------------------------------------


@pytest.fixture
def door(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("VOOL_DATA_DIR", str(tmp_path))
    from core import chat_attachments

    return chat_attachments


def _staged_files(door) -> set[Path]:
    """Files the staging area holds right now.

    Compared as a DELTA rather than against an empty set: the staging directory belongs to the
    runtime's data home, which the test session shares, so an absolute "nothing here" assertion
    would pass or fail on test ordering rather than on the behaviour under test.
    """
    root = door.stage_dir()
    return set(root.glob("*")) if root.exists() else set()


def test_a_credential_inside_a_pdf_is_refused_and_nothing_is_stored(door):
    before = _staged_files(door)
    with pytest.raises(door.AttachmentRefused) as caught:
        door.stage_attachment(
            session_id="s-pdf-secret",
            declared_name="deploy.pdf",
            declared_type="application/pdf",
            data=pdf_with_text([f"AWS_ACCESS_KEY_ID={CANARY_AWS}"]),
        )
    assert caught.value.code == "secret_detected"
    assert "page 1" in caught.value.message, "the refusal must say where the credential was found"
    assert _staged_files(door) == before, "bytes or a derivative were written for a refused document"


def test_a_credential_inside_a_docx_is_refused(door):
    before = _staged_files(door)
    with pytest.raises(door.AttachmentRefused) as caught:
        door.stage_attachment(
            session_id="s-docx-secret",
            declared_name="runbook.docx",
            declared_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            data=docx_document(marker=f"key {CANARY_AWS}"),
        )
    assert caught.value.code == "secret_detected"
    assert _staged_files(door) == before


def test_a_credential_inside_an_archive_member_is_refused(door):
    before = _staged_files(door)
    with pytest.raises(door.AttachmentRefused) as caught:
        door.stage_attachment(
            session_id="s-zip-secret",
            declared_name="bundle.zip",
            declared_type="application/zip",
            data=zip_archive({"readme.md": b"harmless\n", "deploy/env.sh": f"export AWS_ACCESS_KEY_ID={CANARY_AWS}\n".encode()}),
        )
    assert caught.value.code == "secret_detected"
    assert "deploy/env.sh" in caught.value.message
    assert _staged_files(door) == before


def test_a_credential_read_by_ocr_out_of_a_scanned_page_is_refused(door):
    before = _staged_files(door)
    from core.artifact_readers import pdf as pdf_reader

    if not pdf_reader.decoders_available()["ocr"]:
        pytest.skip("no OCR engine on this machine; scanned PDFs are BLOCKED here")
    # The credential exists in this file only as PIXELS. Reaching it requires OCR -- which is
    # exactly why a scan that only looked at `kind == "text"` never saw material like this.
    with pytest.raises(door.AttachmentRefused) as caught:
        door.stage_attachment(
            session_id="s-ocr-secret",
            declared_name="screenshot.pdf",
            declared_type="application/pdf",
            data=scanned_pdf([CANARY_AWS], width=1400, height=340),
        )
    assert caught.value.code == "secret_detected"
    assert _staged_files(door) == before


def test_the_documented_placeholder_key_still_attaches(door):
    """The policy must not make every document that EXPLAINS credentials unattachable."""
    record = door.stage_attachment(
        session_id="s-placeholder",
        declared_name="guide.pdf",
        declared_type="application/pdf",
        data=pdf_with_text([f"Set AWS_ACCESS_KEY_ID={PLACEHOLDER_AWS}", "then run the deploy script."]),
    )
    assert record["kind"] == "artifact"
    assert record["reader_format"] == "pdf"


def test_a_refused_document_leaves_neither_bytes_nor_derivative(door):
    """No bytes, no manifest, no derivative -- and the refused ORIGINAL is not retained anywhere."""
    before = _staged_files(door)
    blob = pdf_with_text([f"token {CANARY_AWS}"])
    with pytest.raises(door.AttachmentRefused):
        door.stage_attachment(session_id="s-none-left", declared_name="x.pdf", declared_type="application/pdf", data=blob)
    new_files = _staged_files(door) - before
    assert new_files == set(), f"a refused upload left {sorted(new_files)} behind"
    # Nor is the original hiding elsewhere in the staging area under some other name.
    digest = hashlib.sha256(blob).hexdigest()
    for path in _staged_files(door):
        if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == digest:
            raise AssertionError(f"the refused original was retained as {path}")


# --- 5. job-lifetime supervision: closed pipes must not end the watch -------------------------


def _closed_pipe_script(body: str) -> list[str]:
    """A child that closes stdout AND stderr before doing anything else."""
    return ["/bin/sh", "-c", f"exec 1>&- 2>&-; {body}", "review"]


def test_a_child_that_closes_its_pipes_is_still_stopped_on_quota_overflow():
    """The measured regression: monitoring used to end at EOF, not when the job ended.

    With both pipes closed the selector empties immediately, so the old loop exited and the
    subsequent wait() let the child run on unwatched -- it overspent, slept, and still wrote its
    completion marker. The marker is the assertion: it must not exist.
    """
    with Scratch("vool-test-") as scratch, extraction_budget(scratch=scratch.path, seconds=60) as budget:
        budget.quota_bytes = 4096
        argv = [
            *_closed_pipe_script(
                '/bin/dd if=/dev/zero of="$1/overflow.bin" bs=8192 count=1; '
                "/bin/sleep 1; "
                'printf COMPLETED > "$1/completion.txt"'
            ),
            str(scratch.path),
        ]
        started = time.monotonic()
        with pytest.raises(ReaderRefused) as caught:
            run_confined(argv, scratch=scratch.path, fmt="test", timeout=30)
        elapsed = time.monotonic() - started
        assert caught.value.code == "scratch_quota_exceeded"
        assert not (scratch.path / "completion.txt").exists(), "the child ran to completion unwatched"
        assert elapsed < 0.9, f"refused after {elapsed:.3f}s -- that is after the child's sleep, not during"


def test_a_child_that_closes_its_pipes_is_still_stopped_on_the_deadline():
    """The same hole would have let a silent decoder ignore the wall clock."""
    with Scratch("vool-test-") as scratch:
        argv = [*_closed_pipe_script('/bin/sleep 30; printf COMPLETED > "$1/completion.txt"'), str(scratch.path)]
        with pytest.raises(ReaderFailed) as caught:
            run_confined(argv, scratch=scratch.path, fmt="test", timeout=2)
        assert caught.value.code == "decoder_timeout"
        assert not (scratch.path / "completion.txt").exists()


def test_a_valid_child_that_closes_its_pipes_still_completes():
    """No stdout heartbeat is required. A silent, well-behaved decoder must finish normally.

    This is the control that stops the fix above from becoming "kill anything quiet".
    """
    with Scratch("vool-test-") as scratch, extraction_budget(scratch=scratch.path, seconds=60):
        argv = [*_closed_pipe_script('/bin/sleep 0.6; printf FINISHED > "$1/result.txt"'), str(scratch.path)]
        result = run_confined(argv, scratch=scratch.path, fmt="test", timeout=30)
        assert result.returncode == 0
        assert (scratch.path / "result.txt").read_text(encoding="utf-8") == "FINISHED"


def test_a_closed_pipe_child_that_forks_leaves_no_process_behind(monkeypatch: pytest.MonkeyPatch):
    """Group cleanup must still hold when there were never any pipes to notice EOF on."""
    groups: list[int] = []
    real_popen = subprocess.Popen

    class _Recording(real_popen):  # type: ignore[misc,valid-type]
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            with contextlib.suppress(OSError):
                groups.append(os.getpgid(self.pid))

    monkeypatch.setattr(subprocess, "Popen", _Recording)
    with Scratch("vool-test-") as scratch:
        argv = _closed_pipe_script("/bin/sleep 600 & /bin/sleep 600")
        with pytest.raises(ReaderFailed):
            run_confined(argv, scratch=scratch.path, fmt="test", timeout=2)
    monkeypatch.undo()
    assert groups, "nothing was spawned, so nothing was proven"
    time.sleep(0.5)
    for pgid in groups:
        assert _pids_in_group(pgid) == [], f"a forked grandchild survived in group {pgid}"


def test_output_caps_still_apply_to_a_child_that_keeps_its_pipes_open():
    """Job-lifetime supervision must not have cost the stream caps."""
    with Scratch("vool-test-") as scratch, pytest.raises(ReaderRefused) as caught:
        run_confined(["/usr/bin/yes", "flood"], scratch=scratch.path, fmt="test", max_stdout=256 * 1024, timeout=30)
    assert caught.value.code == "decoder_output_too_large"


# --- 6. writes are scratch-only, by construction ----------------------------------------------


def test_a_decoder_cannot_write_a_sibling_under_a_synthetic_temp_parent(tmp_path: Path):
    """The DOC grant's actual reach, on a synthetic root -- never the real user temp.

    Read denial does not prevent overwrite or deletion, and writes outside the scratch are not
    covered by the quota scan either, so a write grant over a shared directory was strictly worse
    than it looked. There is now no way to ask for one.
    """
    parent = tmp_path / "synthetic-usertemp"
    parent.mkdir()
    sibling = parent / "sibling-fixture.txt"
    sibling.write_text("ORIGINAL-BYTES\n", encoding="utf-8")
    with Scratch("vool-test-") as scratch:
        result = run_confined(
            ["/bin/sh", "-c", 'printf MUTATED > "$1"', "review", str(sibling)],
            scratch=scratch.path,
            fmt="test",
        )
    assert result.returncode != 0
    assert sibling.read_text(encoding="utf-8") == "ORIGINAL-BYTES\n", "an unrelated file was mutated"


def test_the_sandbox_offers_no_way_to_widen_writes():
    """`extra_write` is gone. A future caller cannot re-open this hole by passing a parameter."""
    import inspect

    from core.artifact_readers import _sandbox as sandbox_module

    assert "extra_write" not in inspect.signature(sandbox_module.run_confined).parameters
    assert "extra_write" not in inspect.signature(sandbox_module._profile).parameters
    assert not hasattr(sandbox_module, "darwin_user_temp"), "the per-user temp helper should be gone"


def test_legacy_doc_converts_with_no_write_grant_at_all():
    """A REAL valid .doc, converted through stdout, under scratch-only writes."""
    from core.artifact_readers import office as office_reader
    from tests.reader_fixtures import legacy_doc

    document = legacy_doc()
    if not document:
        pytest.skip("no legacy .doc converter on this machine; DOC is BLOCKED here, not implemented")
    result = office_reader.read_doc(document, name="legacy.doc")
    assert "Regional Performance Review" in result.units[0].text
    assert "OBSIDIAN-5512" in result.units[0].text
    assert result.meta["network_confined"] is True


def test_a_corrupt_doc_is_refused_rather_than_reported_empty():
    """The stdout path must not turn an unreadable document into a silent empty success."""
    from core.artifact_readers import office as office_reader

    if not office_reader.doc_converter_available():
        pytest.skip("no legacy .doc converter on this machine; DOC is BLOCKED here, not implemented")
    broken = office_reader.OLE_MAGIC + b"\x00" * 4096
    with pytest.raises(ReaderRefused) as caught:
        office_reader.read_doc(broken, name="broken.doc")
    assert caught.value.code == "doc_conversion_failed"
    assert caught.value.remediation
