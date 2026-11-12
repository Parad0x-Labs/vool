"""The Linux reader sandbox where bubblewrap cannot start: Landlock plus seccomp (``_landlock.py``).

Stock Ubuntu 24.04 refuses unprivileged user namespaces through AppArmor, so bubblewrap exits before
any decoder runs and every PDF was refused there (ledger V-042). These tests force the Landlock
backend and run a hijacked decoder -- a Python script doing what an exploited parser would -- through
the real ``run_confined`` path. Every attempt must fail confined, and the same attempt made without the
sandbox (the control) must succeed, so each denial is the sandbox's and not the test machine's.
"""
from __future__ import annotations

import contextlib
import json
import os
import platform
import re
import socket
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from core.artifact_readers import _landlock, _sandbox
from core.artifact_readers._types import ReaderFailed, ReaderRefused, ReaderUnavailable

pytestmark = [
    pytest.mark.skipif(platform.system() != "Linux", reason="Linux confinement"),
    pytest.mark.skipif(not _landlock.available(), reason="this kernel has no Landlock ABI 3 or newer"),
]


@pytest.fixture
def landlock(monkeypatch):
    """Only the Landlock backend: bubblewrap is treated as blocked, as Ubuntu 24.04's AppArmor rule does."""
    monkeypatch.setattr(_sandbox, "_bwrap_works", lambda: False)
    monkeypatch.setattr(_sandbox, "_LANDLOCK_WORKS", {})
    monkeypatch.setattr(_sandbox, "_LANDLOCK_CHECKED_AT", {})
    assert _sandbox.confinement_backend() == "landlock"


def _interpreter_scope() -> list[str]:
    from core.artifact_readers import pdf

    return pdf._fallback_read_scope()


#: What an exploited decoder would try. Each probe prints one JSON line: allowed, or denied with errno.
_HIJACKED = r'''
import ctypes, json, os, socket, sys
args = json.loads(sys.argv[1])
libc = ctypes.CDLL(None, use_errno=True)

def syscall(*values):
    result = libc.syscall(*[ctypes.c_long(value) for value in values])
    if result < 0:
        raise OSError(ctypes.get_errno(), "syscall")
    return result

def probe(name, action):
    try:
        action()
        outcome = {"probe": name, "allowed": True}
    except OSError as exc:
        outcome = {"probe": name, "allowed": False, "errno": exc.errno}
    print(json.dumps(outcome), flush=True)

def tcp():
    socket.create_connection(("127.0.0.1", args["tcp"]), timeout=3).close()

def udp():
    sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sender.sendto(b"LEAK", ("127.0.0.1", args["udp"]))
    sender.close()

def unix(address):
    def connect():
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.connect(address)
        client.sendall(b"LEAK")
        client.close()
    return connect

secret, home = args["secret"], args["home"]
probe("read_home_file", lambda: open(secret, "rb").read())
probe("list_home", lambda: os.listdir(home))
probe("read_etc_passwd", lambda: open("/etc/passwd", "rb").read())
probe("read_parent_environ", lambda: open("/proc/%d/environ" % args["parent"], "rb").read())
probe("list_tmp", lambda: os.listdir("/tmp"))
probe("write_home", lambda: open(os.path.join(home, "planted.txt"), "w").write("x"))
probe("write_dev_shm", lambda: open("/dev/shm/vool-planted", "w").write("x"))
probe("tcp_loopback", tcp)
probe("udp_loopback", udp)
probe("unix_socket_path", unix(args["unix_path"]))
probe("unix_socket_abstract", unix("\0" + args["unix_abstract"]))
probe("signal_parent", lambda: os.kill(args["parent"], 0))
probe("signal_everyone", lambda: os.kill(-1, 0))
probe("chmod_home_file", lambda: os.chmod(secret, 0o644))
probe("utime_home_file", lambda: os.utime(secret, (0, 0)))
probe("truncate_home_file", lambda: os.truncate(secret, 0))
probe("shared_memory", lambda: syscall(args["nr"]["shmget"], 0, 4096, 0o1600))
probe("session_keyring", lambda: syscall(args["nr"]["keyctl"], 0, -3, 0))
probe("new_session", lambda: os.setsid())
probe("symlink_in_scratch", lambda: os.symlink(secret, "frame.png"))
probe("write_scratch", lambda: open("result.txt", "w").write("ok"))
'''

#: Probes the host itself allows, so a control run must show them allowed (or the denial proves nothing).
_HOST_ALLOWS = {"read_home_file", "list_home", "read_etc_passwd", "write_home", "tcp_loopback", "udp_loopback",
                "unix_socket_path", "unix_socket_abstract", "signal_parent", "chmod_home_file", "utime_home_file",
                "truncate_home_file", "shared_memory", "new_session", "symlink_in_scratch", "write_scratch"}


class _Listeners:
    """A TCP, a UDP, a path and an abstract unix listener, each recording whatever reaches it."""

    def __init__(self, directory: Path) -> None:
        self.tcp = socket.socket()
        self.tcp.bind(("127.0.0.1", 0))
        self.tcp.listen()
        self.udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.udp.bind(("127.0.0.1", 0))
        directory.mkdir(parents=True, exist_ok=True)
        self.unix_path = str(directory / "agent.sock")
        self.unix = socket.socket(socket.AF_UNIX)
        self.unix.bind(self.unix_path)
        self.unix.listen()
        self.abstract_name = f"vool-test-{os.getpid()}-{id(self)}"
        self.abstract = socket.socket(socket.AF_UNIX)
        self.abstract.bind("\0" + self.abstract_name)
        self.abstract.listen()
        self.reached: list[str] = []
        self._open = True
        for name, sock in (("tcp", self.tcp), ("udp", self.udp), ("unix", self.unix), ("abstract", self.abstract)):
            sock.settimeout(0.2)
            threading.Thread(target=self._watch, args=(name, sock), daemon=True).start()

    def _watch(self, name: str, sock: socket.socket) -> None:
        while self._open:
            try:
                if name == "udp":
                    sock.recvfrom(64)
                else:
                    sock.accept()[0].close()
                self.reached.append(name)
            except TimeoutError:
                continue
            except OSError:
                return

    def args(self) -> dict:
        return {"tcp": self.tcp.getsockname()[1], "udp": self.udp.getsockname()[1],
                "unix_path": self.unix_path, "unix_abstract": self.abstract_name}

    def close(self) -> None:
        self._open = False
        for sock in (self.tcp, self.udp, self.unix, self.abstract):
            sock.close()


def _outcomes(stdout: bytes) -> dict[str, dict]:
    rows = [json.loads(line) for line in stdout.decode().splitlines() if line.startswith("{")]
    return {row["probe"]: row for row in rows}


def _hijack(tmp_path: Path, *, confined: bool) -> tuple[dict[str, dict], list[str], Path]:
    home = tmp_path / ("home-confined" if confined else "home-control")
    (home / ".ssh").mkdir(parents=True)
    secret = home / ".ssh" / "id_ed25519"
    secret.write_text("-----BEGIN OPENSSH PRIVATE KEY----- test canary\n")
    secret.chmod(0o600)
    listeners = _Listeners(tmp_path / ("c" if confined else "u"))
    machine = {"amd64": "x86_64", "arm64": "aarch64"}.get(platform.machine().lower(), platform.machine().lower())
    args = {"secret": str(secret), "home": str(home), "parent": os.getpid(), "nr": _landlock._NR[machine],
            **listeners.args()}
    try:
        with _sandbox.Scratch("vool-test-") as scratch:
            script = scratch.path / "decoder.py"
            script.write_text(_HIJACKED, encoding="utf-8")
            argv = [sys.executable, "-I", "-S", str(script), json.dumps(args)]
            if confined:
                result = _sandbox.run_confined(argv, scratch=scratch.path, extra_read=_interpreter_scope())
                stdout = result.stdout
            else:
                stdout = subprocess.run(argv, cwd=scratch.path, capture_output=True, timeout=60, check=False).stdout
        import time

        time.sleep(0.5)  # let a datagram or connection that did get through be counted
    finally:
        listeners.close()
    return _outcomes(stdout), listeners.reached, secret


def test_a_hijacked_decoder_reaches_nothing_outside_its_scratch(tmp_path, landlock):
    control, control_reached, _ = _hijack(tmp_path, confined=False)
    host_allows = {name for name in _HOST_ALLOWS if control.get(name, {}).get("allowed")}
    # Without these the test proves nothing about the sandbox: the host itself would be refusing.
    assert {"read_home_file", "list_home", "write_home", "tcp_loopback", "unix_socket_path"} <= host_allows, control
    listener_of = {"tcp_loopback": "tcp", "udp_loopback": "udp", "unix_socket_path": "unix", "unix_socket_abstract": "abstract"}
    assert {listener_of[name] for name in listener_of if name in host_allows} <= set(control_reached), control_reached

    confined, confined_reached, secret = _hijack(tmp_path, confined=True)
    assert set(confined) == set(control), confined
    allowed = {name for name, row in confined.items() if row["allowed"]}
    assert allowed == {"write_scratch"}, confined
    assert confined_reached == [], f"a confined decoder reached a listener: {confined_reached}"
    assert secret.read_text().startswith("-----BEGIN OPENSSH"), "the key was truncated"
    assert oct(secret.stat().st_mode & 0o777) == "0o600", "the key's permissions changed"
    assert secret.stat().st_mtime > 0, "the key's timestamp was rewritten"
    assert not (secret.parent.parent / "planted.txt").exists()


def test_a_child_the_decoder_leaves_running_dies_with_the_read(landlock, monkeypatch):
    # Landlock shares the process table. A decoder that forked a child, closed its pipes and exited
    # must not leave that child behind: nothing confines its CPU or wall time once the read is over.
    spawned: list[int] = []
    real_popen = subprocess.Popen

    class _Recording(real_popen):  # type: ignore[misc,valid-type]
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            spawned.append(self.pid)

    monkeypatch.setattr(subprocess, "Popen", _Recording)
    with _sandbox.Scratch("vool-test-") as scratch:
        result = _sandbox.run_confined(["/bin/sh", "-c", "(exec 1>&- 2>&-; /bin/sleep 600) & echo started"],
                                       scratch=scratch.path)
    monkeypatch.undo()
    assert result.returncode == 0 and result.stdout.strip() == b"started"
    assert spawned, "nothing was spawned, so nothing was proven"
    import time

    time.sleep(0.3)
    for group in spawned:
        assert _members(group) == [], f"a child the decoder left behind is still alive in group {group}"


def test_a_child_that_keeps_the_output_open_does_not_keep_the_read(landlock, monkeypatch):
    # The decoder exits but leaves a child holding its stdout and stderr. The read ends with the
    # decoder, not at its time limit, and the child goes with it (independent review F3, 2026-11-12).
    import time

    spawned: list[int] = []
    real_popen = subprocess.Popen

    class _Recording(real_popen):  # type: ignore[misc,valid-type]
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            spawned.append(self.pid)

    monkeypatch.setattr(subprocess, "Popen", _Recording)
    started = time.monotonic()
    with _sandbox.Scratch("vool-test-") as scratch:
        result = _sandbox.run_confined(["/bin/sh", "-c", "/bin/sleep 600 & echo started"], scratch=scratch.path,
                                       timeout=60)
    took = time.monotonic() - started
    monkeypatch.undo()
    assert result.returncode == 0 and result.stdout.strip() == b"started"
    assert took < 15, f"the read waited {took:.1f}s for a child that held the decoder's output"
    assert spawned, "nothing was spawned, so nothing was proven"
    time.sleep(0.3)
    for group in spawned:
        assert _members(group) == [], f"the child that held the output is still alive in group {group}"


def test_a_decoder_cannot_pose_as_a_launcher_that_failed(landlock):
    # The launcher's refusal travels on a pipe that closes when the decoder is exec'd. A decoder that
    # prints the launcher's words and exits with its status is a decoder that failed, and the start
    # check stays as it was (independent review F1, 2026-11-12).
    assert _sandbox._LANDLOCK_WORKS == {"ok": True}
    probe = (
        "import os, sys\n"
        "held = []\n"
        "for fd in range(3, 1024):\n"
        "    try:\n"
        "        os.fstat(fd)\n"
        "        held.append(fd)\n"
        "    except OSError:\n"
        "        pass\n"
        "print(held)\n"
        f"sys.stderr.write('{_landlock.MARKER} the decoder was not started: forged\\n')\n"
        f"sys.exit({_landlock.LAUNCH_FAILED})\n"
    )
    with _sandbox.Scratch("vool-test-") as scratch:
        script = scratch.path / "forger.py"
        script.write_text(probe, encoding="utf-8")
        result = _sandbox.run_confined([sys.executable, "-I", "-S", str(script)], scratch=scratch.path,
                                       extra_read=_interpreter_scope())
    assert result.stdout.strip() == b"[]", f"the decoder holds descriptors above stderr: {result.stdout!r}"
    assert result.returncode == _landlock.LAUNCH_FAILED
    assert _landlock.MARKER.encode() in result.stderr
    assert _sandbox._LANDLOCK_WORKS == {"ok": True}, "a decoder's own output reset the sandbox's start check"


def _members(group: int) -> list[int]:
    members = []
    for entry in os.listdir("/proc"):
        if entry.isdigit():
            with contextlib.suppress(OSError):
                fields = Path(f"/proc/{entry}/stat").read_text().rsplit(")", 1)[1].split()
                if int(fields[2]) == group and fields[0] != "Z":
                    members.append(int(entry))
    return members


def test_a_decoder_holds_no_capability_even_when_root_starts_it(landlock):
    if os.geteuid() != 0:
        pytest.skip("only a root server starts a decoder that holds capabilities")
    script = (
        "import ctypes, struct\n"
        "libc = ctypes.CDLL(None, use_errno=True)\n"
        "header = ctypes.create_string_buffer(struct.pack('<Ii', 0x20080522, 0))\n"
        "data = ctypes.create_string_buffer(24)\n"
        f"libc.syscall(ctypes.c_long({_landlock._NR['x86_64' if platform.machine() == 'x86_64' else 'aarch64']['capget']}), header, data)\n"
        "bounding = [cap for cap in range(41) if libc.prctl(23, cap, 0, 0, 0) == 1]\n"
        "print(struct.unpack('<6I', data.raw), bounding)\n"
    )
    with _sandbox.Scratch("vool-test-") as scratch:
        (scratch.path / "caps.py").write_text(script)
        result = _sandbox.run_confined([sys.executable, "-I", "-S", str(scratch.path / "caps.py")],
                                       scratch=scratch.path, extra_read=_interpreter_scope())
    assert result.stdout.strip() == b"(0, 0, 0, 0, 0, 0) []", result.stdout + result.stderr


def test_another_architecture_s_syscall_kills_the_decoder(landlock):
    if platform.machine() != "x86_64":
        pytest.skip("the i386 entry point exists on x86_64 only")
    # mov eax, 20 (getpid on i386); int 0x80; ret -- a 32-bit syscall from a 64-bit process, the
    # classic way around a filter that checks numbers but not the architecture.
    script = (
        "import ctypes, mmap\n"
        "code = b'\\xb8\\x14\\x00\\x00\\x00\\xcd\\x80\\xc3'\n"
        "page = mmap.mmap(-1, 4096, prot=mmap.PROT_READ | mmap.PROT_WRITE | mmap.PROT_EXEC)\n"
        "page.write(code)\n"
        "address = ctypes.addressof(ctypes.c_char.from_buffer(page))\n"
        "print('PID', ctypes.CFUNCTYPE(ctypes.c_long)(address)(), flush=True)\n"
    )
    with _sandbox.Scratch("vool-test-") as scratch:
        (scratch.path / "i386.py").write_text(script)
        control = subprocess.run([sys.executable, "-I", "-S", str(scratch.path / "i386.py")], capture_output=True, timeout=30)
        if not control.stdout.startswith(b"PID"):
            pytest.skip("this kernel has no i386 entry point, so there is nothing to refuse")
        with pytest.raises(ReaderFailed) as caught:
            _sandbox.run_confined([sys.executable, "-I", "-S", str(scratch.path / "i386.py")],
                                  scratch=scratch.path, extra_read=_interpreter_scope())
    assert caught.value.code == "decoder_killed" and "signal 31" in caught.value.message


def test_a_decoder_cannot_take_more_memory_than_its_ceiling(landlock):
    with _sandbox.Scratch("vool-test-") as scratch:
        (scratch.path / "mem.py").write_text("try:\n    b = bytearray(6 * 1024 ** 3); print('GOT')\nexcept MemoryError:\n    print('REFUSED')\n")
        probe = _sandbox.run_confined([sys.executable, "-I", "-S", str(scratch.path / "mem.py")], scratch=scratch.path,
                                      extra_read=_interpreter_scope())
    assert probe.stdout.strip() == b"REFUSED", probe.stdout


def test_the_scratch_quota_and_output_caps_still_hold(landlock):
    with _sandbox.Scratch("vool-test-") as scratch, _sandbox.extraction_budget(scratch=scratch.path, seconds=60) as budget:
        budget.quota_bytes = 4096
        with pytest.raises(ReaderRefused) as quota:
            _sandbox.run_confined(["/bin/sh", "-c", 'exec 1>&- 2>&-; /bin/dd if=/dev/zero of="$1/big" bs=8192 count=1; /bin/sleep 5',
                                   "sh", str(scratch.path)], scratch=scratch.path, timeout=30)
    assert quota.value.code == "scratch_quota_exceeded"
    with _sandbox.Scratch("vool-test-") as scratch, pytest.raises(ReaderRefused) as flood:
        _sandbox.run_confined(["/usr/bin/yes", "flood"], scratch=scratch.path, max_stdout=256 * 1024, timeout=30)
    assert flood.value.code == "decoder_output_too_large"


def test_a_pdf_is_read_where_bubblewrap_is_blocked(tmp_path, landlock):
    from core.pdf_tools import extract_text
    from tests.reader_fixtures import native_text_pdf

    path = tmp_path / "report.pdf"
    path.write_bytes(native_text_pdf(pages=3, marker="BASALT-2290"))
    result = extract_text(str(path))
    assert result["status"] == "ok", result
    assert result["pages_total"] == 3 and "BASALT-2290" in result["text"]


def test_real_decoders_work_under_landlock(tmp_path, landlock):
    import shutil

    import core.artifact_readers as readers
    from tests.reader_fixtures import sample_video, zip_archive

    archive = zip_archive({"notes/plan.txt": b"Ship the Riga build on Friday."})
    read = readers.read(archive, name="bundle.zip", extension=".zip")
    assert "Ship the Riga build on Friday." in read.rendered_text()
    if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        pytest.skip("ffmpeg is not installed here; the archive above ran through bsdtar")
    video = readers.read(sample_video(seconds=4), name="clip.mp4", extension=".mp4")
    assert video.units, "no frame came back from ffmpeg under Landlock"


def test_a_launch_step_that_fails_runs_nothing(tmp_path):
    marker = tmp_path / "decoder-ran"
    decoder = ["/bin/sh", "-c", 'echo ran > "$1"', "sh", str(marker)]
    # A rule that would cover the whole file system is refused before the decoder exists.
    whole = _landlock.command(decoder, scratch="/", read=["/usr"], cpu_seconds=5, address_space=1 << 32,
                              file_size=1 << 20, open_files=64)
    refused = subprocess.run(whole, capture_output=True, timeout=30)
    assert refused.returncode == _landlock.LAUNCH_FAILED and _landlock.MARKER.encode() in refused.stderr
    # So is a launch whose parent is not the server that asked for it.
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    command = _landlock.command(decoder, scratch=str(scratch), read=["/usr", "/bin", "/lib", "/lib64"], cpu_seconds=5,
                                address_space=1 << 32, file_size=1 << 20, open_files=64)
    orphaned = subprocess.run(["/bin/sh", "-c", '"$@"; true', "sh", *command], capture_output=True, timeout=30)
    assert _landlock.MARKER.encode() in orphaned.stderr
    assert not marker.exists()


def test_a_launcher_failure_is_a_typed_refusal_and_is_checked_again(landlock, monkeypatch):
    with _sandbox.Scratch("vool-test-") as scratch:
        real = _sandbox._landlock_command
        monkeypatch.setattr(_sandbox, "_landlock_command",
                            lambda argv, **kwargs: real(argv, **{**kwargs, "scratch": "/"}))
        with pytest.raises(ReaderFailed) as caught:
            _sandbox.run_confined(["/bin/cat", "/etc/hostname"], scratch=scratch.path)
    assert caught.value.code == "sandbox_start_failed"
    assert _sandbox._LANDLOCK_WORKS == {}, "a failed launch must send the next read back through the start check"


def test_the_start_check_catches_a_kernel_that_enforces_nothing(monkeypatch):
    monkeypatch.setattr(_sandbox, "_LANDLOCK_WORKS", {})
    assert _sandbox._landlock_works()
    # The same check, with a launcher that confines nothing: the canary is read, so it must fail.
    monkeypatch.setattr(_sandbox, "_LANDLOCK_WORKS", {})
    monkeypatch.setattr(_sandbox._landlock, "command", lambda argv, **_kwargs: list(argv))
    assert not _sandbox._landlock_works()


def test_without_landlock_or_bubblewrap_nothing_runs(tmp_path, monkeypatch):
    monkeypatch.setattr(_sandbox, "_bwrap_works", lambda: False)
    monkeypatch.setattr(_sandbox, "_bwrap", lambda: "")
    monkeypatch.setattr(_sandbox, "_LANDLOCK_WORKS", {})
    monkeypatch.setattr(_landlock, "abi_version", lambda: 2)  # Linux 5.19 to 6.1: truncation is not covered

    def never(*_args, **_kwargs):
        raise AssertionError("nothing may be spawned without a sandbox")

    monkeypatch.setattr(subprocess, "Popen", never)
    assert _sandbox.confinement_backend() == ""
    assert "Linux 6.2" in _sandbox.confinement_unavailable_reason()
    with _sandbox.Scratch("vool-test-") as scratch, pytest.raises(ReaderUnavailable) as caught:
        _sandbox.run_confined(["/bin/cat", "/etc/hostname"], scratch=scratch.path)
    assert caught.value.code == "confinement_unavailable"


@pytest.mark.parametrize(("machine", "header"), [
    ("x86_64", "/usr/include/x86_64-linux-gnu/asm/unistd_64.h"),
    ("aarch64", "/usr/include/asm-generic/unistd.h"),
])
def test_the_syscall_numbers_are_the_kernel_s_own(machine, header):
    if not os.path.exists(header):
        pytest.skip(f"{header} is not installed here")
    text = Path(header).read_text()
    known = {name: int(number) for name, number in re.findall(r"#define __NR_(\w+)\s+(\d+)", text)}
    known.update({name: int(number) for name, number in re.findall(r"#define __NR3264_(\w+)\s+(\d+)", text)
                  if name not in known})
    table = _landlock._NR[machine]
    compared = {name for name in table if name in known}
    assert len(compared) >= len(table) - 2, sorted(set(table) - compared)
    assert {name: table[name] for name in compared} == {name: known[name] for name in compared}


def test_no_scope_ever_covers_the_home_folder_or_vool_s_data(tmp_path, monkeypatch):
    # An interpreter installed with --prefix=$HOME would make sys.base_prefix the home folder itself,
    # and the PDF fallback grants base_prefix. Installation trees inside the home folder stay grantable.
    home = tmp_path / "home" / "owner"
    data = home / ".vool_runtime"
    interpreter = home / ".local" / "share" / "uv" / "python" / "cpython-3.12" / "lib"
    for folder in (data, interpreter):
        folder.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr("core.runtime_paths.active_vool_home", lambda *_args, **_kwargs: data)
    granted = _sandbox._resolved(["/", str(tmp_path / "home"), str(home), str(data), str(interpreter), "/usr"])
    assert granted == [str(interpreter.resolve()), "/usr"]
