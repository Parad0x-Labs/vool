"""The Linux reader sandbox for systems where bubblewrap cannot start: Landlock plus seccomp.

Stock Ubuntu 24.04 refuses unprivileged user namespaces through AppArmor, so bubblewrap exits
before any decoder runs, and every PDF was refused there. Landlock needs neither a namespace nor
any privilege: a process restricts *itself*, the kernel enforces it, and everything that process
executes or forks inherits the restriction for good. This file is that process.

It runs by path as its own fresh interpreter::

    python -I -S _landlock.py POLICY_JSON DECODER [ARG ...]

``subprocess.Popen`` forks and execs it at once, so no Python runs between fork and exec in the
threaded server (the same reason ``_sandbox._limited_argv`` uses a shell). In this order, and
refusing to run the decoder at all (exit 125) if any step fails, it:

1. sets the decoder's ceilings: CPU seconds, address space, file size, open files, no core file;
2. closes every inherited descriptor above stderr;
3. when started with any capability (root), empties every capability set and the bounding set,
   and locks them empty across exec;
4. sets no_new_privs (a set-user-ID program gains nothing) and a parent-death SIGKILL;
5. restricts the file system with Landlock: files are read only beneath the system program trees
   and the paths the caller names; files are read, written, created and removed only beneath this
   extraction's scratch directory, where no link, device, pipe or socket can be made and nothing
   can be executed; of /dev, only null, zero, full, random and urandom. From kernel ABI 4 no TCP
   bind or connect either, and from ABI 6 no abstract unix socket and no signal outside;
6. installs a seccomp filter for what Landlock does not cover: no socket of any family; no ptrace,
   no other process's memory, no keyring, namespace, mount, io_uring, BPF, perf or userfaultfd; no
   SysV IPC or POSIX message queue (another program's shared memory); no permission, owner,
   timestamp or extended-attribute change and no truncation by path; signals, resource limits and
   scheduling for its own process only; no new session or process group, so the process-group kill
   reaches every child. A syscall of another architecture kills the process;
7. checks that the fence holds (``/`` cannot be listed, a socket cannot be opened), then execs the
   decoder.

Landlock ABI 3 (Linux 6.2) is the floor: before it, opening a readable file with ``O_TRUNC``
emptied it whatever the ruleset said. Below the floor, on an architecture this file has no syscall
table for, or where Landlock is switched off, :func:`abi_version` reports it and nothing runs.

What stays possible inside, stated rather than implied away: ``stat`` of a path that is guessed
(existence, size and times, never content), reading the system trees the decoder starts from, and
lowering nothing but its own priority. Network, home folder, other processes and the scratch
directories of other extractions are out of reach.
"""

from __future__ import annotations

import contextlib
import ctypes
import errno
import json
import os
import platform
import stat
import struct
import sys
from collections.abc import Sequence

#: Landlock's oldest ABI this sandbox accepts. ABI 3 (Linux 6.2) is the first that covers
#: truncation; Ubuntu 24.04's 6.8 kernel has ABI 4.
MIN_ABI = 3
#: The launcher's own exit status when it refused to start the decoder, with MARKER on stderr.
LAUNCH_FAILED = 125
MARKER = "vool-landlock:"
#: The launcher itself: this file, run by path.
LAUNCHER = os.path.abspath(__file__)

# --- Landlock ---------------------------------------------------------------------------------------

_NR_CREATE_RULESET, _NR_ADD_RULE, _NR_RESTRICT_SELF = 444, 445, 446  # the same on every architecture
_CREATE_RULESET_VERSION = 1
_RULE_PATH_BENEATH = 1

FS_EXECUTE = 1 << 0
FS_WRITE_FILE = 1 << 1
FS_READ_FILE = 1 << 2
FS_READ_DIR = 1 << 3
FS_REMOVE_DIR = 1 << 4
FS_REMOVE_FILE = 1 << 5
FS_MAKE_CHAR = 1 << 6
FS_MAKE_DIR = 1 << 7
FS_MAKE_REG = 1 << 8
FS_MAKE_SOCK = 1 << 9
FS_MAKE_FIFO = 1 << 10
FS_MAKE_BLOCK = 1 << 11
FS_MAKE_SYM = 1 << 12
FS_REFER = 1 << 13  # ABI 2
FS_TRUNCATE = 1 << 14  # ABI 3
FS_IOCTL_DEV = 1 << 15  # ABI 5
NET_BIND_TCP = 1 << 0  # ABI 4
NET_CONNECT_TCP = 1 << 1  # ABI 4
SCOPE_ABSTRACT_UNIX_SOCKET = 1 << 0  # ABI 6
SCOPE_SIGNAL = 1 << 1  # ABI 6

#: The rights a rule may carry for something that is not a directory.
_FILE_RIGHTS = FS_EXECUTE | FS_WRITE_FILE | FS_READ_FILE | FS_TRUNCATE | FS_IOCTL_DEV
#: Program and library trees, and the paths a caller names: read and run, nothing else.
READ_RIGHTS = FS_READ_FILE | FS_READ_DIR | FS_EXECUTE
#: The scratch directory: its own files, read and written. Deliberately missing: EXECUTE (nothing
#: dropped there runs), MAKE_SYM (a link VOOL would follow out of the scratch -- the 2026-10-29
#: review read ~/.ssh/id_rsa back that way under bubblewrap), MAKE_FIFO (a pipe that hangs the
#: reader), MAKE_SOCK, MAKE_CHAR, MAKE_BLOCK, REFER (no file from elsewhere linked or moved in) and
#: IOCTL_DEV.
SCRATCH_RIGHTS = (FS_READ_FILE | FS_READ_DIR | FS_WRITE_FILE | FS_TRUNCATE | FS_MAKE_REG | FS_MAKE_DIR
                  | FS_REMOVE_FILE | FS_REMOVE_DIR)
#: The device nodes a decoder may open, and how. Nothing else under /dev: not a terminal, not
#: /dev/shm (other programs' shared memory), not a disk.
DEVICES: dict[str, int] = {
    "/dev/null": FS_READ_FILE | FS_WRITE_FILE,
    "/dev/zero": FS_READ_FILE,
    "/dev/full": FS_READ_FILE | FS_WRITE_FILE,
    "/dev/random": FS_READ_FILE,
    "/dev/urandom": FS_READ_FILE,
}


def _handled_fs(abi: int) -> int:
    rights = (1 << 13) - 1  # EXECUTE .. MAKE_SYM, ABI 1
    if abi >= 2:
        rights |= FS_REFER
    if abi >= 3:
        rights |= FS_TRUNCATE
    if abi >= 5:
        rights |= FS_IOCTL_DEV
    return rights


# --- seccomp ----------------------------------------------------------------------------------------

_AUDIT_ARCH = {"x86_64": 0xC000003E, "aarch64": 0xC00000B7}
_X32_SYSCALL_BIT = 0x40000000

#: Syscall numbers, from the kernel's own tables (asm/unistd_64.h on x86_64; asm-generic/unistd.h on
#: aarch64). tests/test_linux_reader_landlock.py checks them against the headers where installed.
_NR: dict[str, dict[str, int]] = {
    "x86_64": {
        "socket": 41, "socketpair": 53, "ptrace": 101, "process_vm_readv": 310, "process_vm_writev": 311,
        "kcmp": 312, "pidfd_open": 434, "pidfd_getfd": 438, "pidfd_send_signal": 424, "process_madvise": 440,
        "process_mrelease": 448, "unshare": 272, "setns": 308, "clone": 56, "clone3": 435, "add_key": 248,
        "request_key": 249, "keyctl": 250, "bpf": 321, "perf_event_open": 298, "userfaultfd": 323,
        "io_uring_setup": 425, "io_uring_enter": 426, "io_uring_register": 427, "fanotify_init": 300,
        "inotify_init": 253, "inotify_init1": 294, "modify_ldt": 154, "uselib": 134, "vhangup": 153,
        "lookup_dcookie": 212, "syslog": 103, "mount": 165, "umount2": 166, "pivot_root": 155, "chroot": 161,
        "open_tree": 428, "move_mount": 429, "fsopen": 430, "fsconfig": 431, "fsmount": 432, "fspick": 433,
        "mount_setattr": 442, "kexec_load": 246, "kexec_file_load": 320, "init_module": 175,
        "finit_module": 313, "delete_module": 176, "reboot": 169, "swapon": 167, "swapoff": 168, "acct": 163,
        "iopl": 172, "ioperm": 173, "quotactl": 179, "quotactl_fd": 443, "settimeofday": 164,
        "clock_settime": 227, "clock_adjtime": 305, "adjtimex": 159, "sethostname": 170, "setdomainname": 171,
        "open_by_handle_at": 304, "nfsservctl": 180, "chmod": 90, "fchmod": 91, "fchmodat": 268,
        "fchmodat2": 452, "chown": 92, "fchown": 93, "lchown": 94, "fchownat": 260, "utime": 132, "utimes": 235,
        "futimesat": 261, "utimensat": 280, "truncate": 76, "setxattr": 188, "lsetxattr": 189, "fsetxattr": 190,
        "removexattr": 197, "lremovexattr": 198, "fremovexattr": 199, "getxattr": 191, "lgetxattr": 192,
        "fgetxattr": 193, "listxattr": 194, "llistxattr": 195, "flistxattr": 196, "kill": 62, "tgkill": 234,
        "tkill": 200, "rt_sigqueueinfo": 129, "rt_tgsigqueueinfo": 297, "prlimit64": 302, "setpriority": 141,
        "ioprio_set": 251, "sched_setaffinity": 203, "sched_setscheduler": 144, "sched_setparam": 142,
        "sched_setattr": 314, "setsid": 112, "setpgid": 109, "fcntl": 72, "ioctl": 16, "mq_open": 240,
        "mq_unlink": 241, "mq_timedsend": 242, "mq_timedreceive": 243, "mq_notify": 244, "mq_getsetattr": 245,
        "shmget": 29, "shmat": 30, "shmctl": 31, "shmdt": 67, "msgget": 68, "msgsnd": 69, "msgrcv": 70,
        "msgctl": 71, "semget": 64, "semop": 65, "semctl": 66, "semtimedop": 220, "migrate_pages": 256,
        "move_pages": 279, "lsm_set_self_attr": 460, "capget": 125, "capset": 126,
    },
    "aarch64": {
        "socket": 198, "socketpair": 199, "ptrace": 117, "process_vm_readv": 270, "process_vm_writev": 271,
        "kcmp": 272, "pidfd_open": 434, "pidfd_getfd": 438, "pidfd_send_signal": 424, "process_madvise": 440,
        "process_mrelease": 448, "unshare": 97, "setns": 268, "clone": 220, "clone3": 435, "add_key": 217,
        "request_key": 218, "keyctl": 219, "bpf": 280, "perf_event_open": 241, "userfaultfd": 282,
        "io_uring_setup": 425, "io_uring_enter": 426, "io_uring_register": 427, "fanotify_init": 262,
        "inotify_init1": 26, "vhangup": 58, "lookup_dcookie": 18, "syslog": 116, "mount": 40, "umount2": 39,
        "pivot_root": 41, "chroot": 51, "open_tree": 428, "move_mount": 429, "fsopen": 430, "fsconfig": 431,
        "fsmount": 432, "fspick": 433, "mount_setattr": 442, "kexec_load": 104, "kexec_file_load": 294,
        "init_module": 105, "finit_module": 273, "delete_module": 106, "reboot": 142, "swapon": 224,
        "swapoff": 225, "acct": 89, "quotactl": 60, "quotactl_fd": 443, "settimeofday": 170,
        "clock_settime": 112, "clock_adjtime": 266, "adjtimex": 171, "sethostname": 161, "setdomainname": 162,
        "open_by_handle_at": 265, "nfsservctl": 42, "fchmod": 52, "fchmodat": 53, "fchmodat2": 452,
        "fchown": 55, "fchownat": 54, "utimensat": 88, "truncate": 45, "setxattr": 5, "lsetxattr": 6,
        "fsetxattr": 7, "removexattr": 14, "lremovexattr": 15, "fremovexattr": 16, "getxattr": 8,
        "lgetxattr": 9, "fgetxattr": 10, "listxattr": 11, "llistxattr": 12, "flistxattr": 13, "kill": 129,
        "tgkill": 131, "tkill": 130, "rt_sigqueueinfo": 138, "rt_tgsigqueueinfo": 240, "prlimit64": 261,
        "setpriority": 140, "ioprio_set": 30, "sched_setaffinity": 122, "sched_setscheduler": 119,
        "sched_setparam": 118, "sched_setattr": 274, "setsid": 157, "setpgid": 154, "fcntl": 25, "ioctl": 29,
        "mq_open": 180, "mq_unlink": 181, "mq_timedsend": 182, "mq_timedreceive": 183, "mq_notify": 184,
        "mq_getsetattr": 185, "shmget": 194, "shmat": 196, "shmctl": 195, "shmdt": 197, "msgget": 186,
        "msgsnd": 189, "msgrcv": 188, "msgctl": 187, "semget": 190, "semop": 193, "semctl": 191,
        "semtimedop": 192, "migrate_pages": 238, "move_pages": 239, "lsm_set_self_attr": 460, "capget": 90,
        "capset": 91,
    },
}
#: Every syscall above this number is refused with ENOSYS. 462 is mseal (Linux 6.10); 463-469 are
#: the *at extended-attribute calls, open_tree_attr and file_getattr/file_setattr, which change or
#: read what this filter refuses by their older names; and whatever a newer kernel adds later is
#: refused until it is reviewed here. libc falls back from ENOSYS to the older call.
_HIGHEST_ALLOWED_NR = 462

#: Refused outright: (syscall, errno). EPERM unless a caller is better served by another errno.
_DENIED: tuple[tuple[str, int], ...] = (
    # no network of any kind, and no local socket either (D-Bus, X11, the owner's agents)
    ("socket", errno.EPERM), ("socketpair", errno.EPERM),
    # other processes: their memory, their descriptors, their signals by handle
    ("ptrace", errno.EPERM), ("process_vm_readv", errno.EPERM), ("process_vm_writev", errno.EPERM),
    ("kcmp", errno.EPERM), ("pidfd_open", errno.EPERM), ("pidfd_getfd", errno.EPERM),
    ("pidfd_send_signal", errno.EPERM), ("process_madvise", errno.EPERM), ("process_mrelease", errno.EPERM),
    ("migrate_pages", errno.EPERM), ("move_pages", errno.EPERM),
    # namespaces (the entry point of most unprivileged kernel exploits); clone3 hides its flags in
    # memory a filter cannot read, so it answers ENOSYS and libc falls back to clone, checked below
    ("unshare", errno.EPERM), ("setns", errno.EPERM), ("clone3", errno.ENOSYS),
    # the owner's keyrings
    ("add_key", errno.EPERM), ("request_key", errno.EPERM), ("keyctl", errno.EPERM),
    # kernel attack surface a decoder never needs
    ("bpf", errno.EPERM), ("perf_event_open", errno.EPERM), ("userfaultfd", errno.EPERM),
    ("io_uring_setup", errno.EPERM), ("io_uring_enter", errno.EPERM), ("io_uring_register", errno.EPERM),
    ("fanotify_init", errno.EPERM), ("inotify_init", errno.EPERM), ("inotify_init1", errno.EPERM),
    ("modify_ldt", errno.EPERM), ("uselib", errno.EPERM), ("vhangup", errno.EPERM),
    ("lookup_dcookie", errno.EPERM), ("syslog", errno.EPERM), ("lsm_set_self_attr", errno.EPERM),
    # the file system's shape, the machine's state
    ("mount", errno.EPERM), ("umount2", errno.EPERM), ("pivot_root", errno.EPERM), ("chroot", errno.EPERM),
    ("open_tree", errno.EPERM), ("move_mount", errno.EPERM), ("fsopen", errno.EPERM),
    ("fsconfig", errno.EPERM), ("fsmount", errno.EPERM), ("fspick", errno.EPERM),
    ("mount_setattr", errno.EPERM), ("kexec_load", errno.EPERM), ("kexec_file_load", errno.EPERM),
    ("init_module", errno.EPERM), ("finit_module", errno.EPERM), ("delete_module", errno.EPERM),
    ("reboot", errno.EPERM), ("swapon", errno.EPERM), ("swapoff", errno.EPERM), ("acct", errno.EPERM),
    ("iopl", errno.EPERM), ("ioperm", errno.EPERM), ("quotactl", errno.EPERM), ("quotactl_fd", errno.EPERM),
    ("settimeofday", errno.EPERM), ("clock_settime", errno.EPERM), ("clock_adjtime", errno.EPERM),
    ("adjtimex", errno.EPERM), ("sethostname", errno.EPERM), ("setdomainname", errno.EPERM),
    ("open_by_handle_at", errno.EPERM), ("nfsservctl", errno.EPERM),
    # what Landlock does not cover: metadata of files outside the scratch, and truncation by path
    ("chmod", errno.EPERM), ("fchmod", errno.EPERM), ("fchmodat", errno.EPERM), ("fchmodat2", errno.EPERM),
    ("chown", errno.EPERM), ("fchown", errno.EPERM), ("lchown", errno.EPERM), ("fchownat", errno.EPERM),
    ("utime", errno.EPERM), ("utimes", errno.EPERM), ("futimesat", errno.EPERM), ("utimensat", errno.EPERM),
    ("truncate", errno.EPERM),
    ("setxattr", errno.EOPNOTSUPP), ("lsetxattr", errno.EOPNOTSUPP), ("fsetxattr", errno.EOPNOTSUPP),
    ("removexattr", errno.EOPNOTSUPP), ("lremovexattr", errno.EOPNOTSUPP), ("fremovexattr", errno.EOPNOTSUPP),
    ("getxattr", errno.EOPNOTSUPP), ("lgetxattr", errno.EOPNOTSUPP), ("fgetxattr", errno.EOPNOTSUPP),
    ("listxattr", errno.EOPNOTSUPP), ("llistxattr", errno.EOPNOTSUPP), ("flistxattr", errno.EOPNOTSUPP),
    # other programs' shared memory, semaphores and message queues: SysV IPC and POSIX queues are not
    # paths, so Landlock never sees them
    ("shmget", errno.EPERM), ("shmat", errno.EPERM), ("shmctl", errno.EPERM), ("shmdt", errno.EPERM),
    ("msgget", errno.EPERM), ("msgsnd", errno.EPERM), ("msgrcv", errno.EPERM), ("msgctl", errno.EPERM),
    ("semget", errno.EPERM), ("semop", errno.EPERM), ("semctl", errno.EPERM), ("semtimedop", errno.EPERM),
    ("mq_open", errno.EPERM), ("mq_unlink", errno.EPERM), ("mq_timedsend", errno.EPERM),
    ("mq_timedreceive", errno.EPERM), ("mq_notify", errno.EPERM), ("mq_getsetattr", errno.EPERM),
    # a new session or process group would slip out of the process-group kill; tkill names a thread of
    # any process
    ("setsid", errno.EPERM), ("setpgid", errno.EPERM), ("tkill", errno.EPERM),
)

_CLONE_NEWNS = 0x00020000
_CLONE_PARENT = 0x00008000
_CLONE_NAMESPACES = 0x7E020000  # NEWNS, NEWCGROUP, NEWUTS, NEWIPC, NEWUSER, NEWPID, NEWNET
_TIOCSTI, _TIOCLINUX = 0x5412, 0x541C
_F_SETOWN, _F_SETSIG, _F_SETOWN_EX = 8, 10, 15
_PRIO_PROCESS, _IOPRIO_WHO_PROCESS = 0, 1

_SECCOMP_RET_KILL_PROCESS = 0x80000000
_SECCOMP_RET_ERRNO = 0x00050000
_SECCOMP_RET_ALLOW = 0x7FFF0000
_BPF_LD_W_ABS = 0x20
_BPF_JEQ_K = 0x15
_BPF_JGE_K = 0x35
_BPF_JGT_K = 0x25
_BPF_JSET_K = 0x45
_BPF_RET_K = 0x06
_OFF_NR, _OFF_ARCH = 0, 4


def _arg_low(index: int) -> int:
    """Offset of the low 32 bits of syscall argument `index` in seccomp_data (little-endian).

    Every value checked here is a 32-bit int or unsigned int in the kernel (pid_t, the ioctl and
    fcntl commands, clone's namespace bits), and the kernel uses only those 32 bits. So the low
    word is exactly what the kernel acts on, whatever a caller leaves in the high word."""
    return 16 + 8 * index


def _ld(offset: int) -> tuple[int, int, int, int]:
    return (_BPF_LD_W_ABS, 0, 0, offset)


def _ret(value: int) -> tuple[int, int, int, int]:
    return (_BPF_RET_K, 0, 0, value)


def _jump(code: int, value: int, true: int, false: int) -> tuple[int, int, int, int]:
    return (code, true, false, value & 0xFFFFFFFF)


def _only_values(nr: int, arg: int, allowed: Sequence[int], err: int) -> list[tuple[int, int, int, int]]:
    """Allow syscall `nr` only when argument `arg` is one of `allowed`; otherwise fail with `err`."""
    body = [_ld(_arg_low(arg))]
    for index, value in enumerate(allowed):
        body.append(_jump(_BPF_JEQ_K, value, len(allowed) - index, 0))
    body += [_ret(_SECCOMP_RET_ERRNO | err), _ret(_SECCOMP_RET_ALLOW)]
    return [_jump(_BPF_JEQ_K, nr, 0, len(body)), *body]


def _never_values(nr: int, arg: int, denied: Sequence[int], err: int) -> list[tuple[int, int, int, int]]:
    """Fail syscall `nr` with `err` when argument `arg` is one of `denied`; otherwise allow it."""
    body = [_ld(_arg_low(arg))]
    for index, value in enumerate(denied):
        body.append(_jump(_BPF_JEQ_K, value, len(denied) - index, 0))
    body += [_ret(_SECCOMP_RET_ALLOW), _ret(_SECCOMP_RET_ERRNO | err)]
    return [_jump(_BPF_JEQ_K, nr, 0, len(body)), *body]


def _own_only(nr: int, which_arg: int, which: int, who_arg: int, pid: int) -> list[tuple[int, int, int, int]]:
    """setpriority/ioprio_set: only `which` == a single process, and only this one (0 or its pid)."""
    body = [
        _ld(_arg_low(which_arg)),
        _jump(_BPF_JEQ_K, which, 0, 3),
        _ld(_arg_low(who_arg)),
        _jump(_BPF_JEQ_K, 0, 2, 0),
        _jump(_BPF_JEQ_K, pid, 1, 0),
        _ret(_SECCOMP_RET_ERRNO | errno.EPERM),
        _ret(_SECCOMP_RET_ALLOW),
    ]
    return [_jump(_BPF_JEQ_K, nr, 0, len(body)), *body]


def seccomp_program(machine: str, pid: int) -> bytes:
    """The filter, as the kernel's sock_filter array. `pid` is the decoder's pid (exec keeps it)."""
    table = _NR[machine]
    program: list[tuple[int, int, int, int]] = [
        _ld(_OFF_ARCH),
        _jump(_BPF_JEQ_K, _AUDIT_ARCH[machine], 1, 0),
        _ret(_SECCOMP_RET_KILL_PROCESS),  # i386 via int 0x80, or any other ABI: killed
        _ld(_OFF_NR),
    ]
    if machine == "x86_64":
        program += [_jump(_BPF_JGE_K, _X32_SYSCALL_BIT, 0, 1), _ret(_SECCOMP_RET_KILL_PROCESS)]
    program += [_jump(_BPF_JGT_K, _HIGHEST_ALLOWED_NR, 0, 1), _ret(_SECCOMP_RET_ERRNO | errno.ENOSYS)]
    for name, err in _DENIED:
        if name in table:
            program += [_jump(_BPF_JEQ_K, table[name], 0, 1), _ret(_SECCOMP_RET_ERRNO | err)]
    own = (0, pid)
    program += _only_values(table["kill"], 0, (0, pid, -pid), errno.EPERM)  # itself or its own group
    program += _only_values(table["tgkill"], 0, (pid,), errno.EPERM)
    program += _only_values(table["rt_sigqueueinfo"], 0, (pid,), errno.EPERM)
    program += _only_values(table["rt_tgsigqueueinfo"], 0, (pid,), errno.EPERM)
    program += _only_values(table["prlimit64"], 0, own, errno.EPERM)
    for name in ("sched_setaffinity", "sched_setscheduler", "sched_setparam", "sched_setattr"):
        program += _only_values(table[name], 0, own, errno.EPERM)
    program += _own_only(table["setpriority"], 0, _PRIO_PROCESS, 1, pid)
    program += _own_only(table["ioprio_set"], 0, _IOPRIO_WHO_PROCESS, 1, pid)
    # A pending SIGIO signals whoever owns the descriptor: F_SETOWN would aim it at another process.
    program += _never_values(table["fcntl"], 1, (_F_SETOWN, _F_SETSIG, _F_SETOWN_EX), errno.EPERM)
    # Typing into, or taking over, a terminal the decoder somehow holds.
    program += _never_values(table["ioctl"], 1, (_TIOCSTI, _TIOCLINUX), errno.EPERM)
    clone_body = [
        _ld(_arg_low(0)),
        _jump(_BPF_JSET_K, _CLONE_NAMESPACES | _CLONE_PARENT, 1, 0),
        _ret(_SECCOMP_RET_ALLOW),
        _ret(_SECCOMP_RET_ERRNO | errno.EPERM),
    ]
    program += [_jump(_BPF_JEQ_K, table["clone"], 0, len(clone_body)), *clone_body]
    program.append(_ret(_SECCOMP_RET_ALLOW))
    for code, true, false, _value in program:
        if not (0 <= true <= 255 and 0 <= false <= 255):
            raise ValueError(f"seccomp jump out of range in instruction {code:#x}")
    return b"".join(struct.pack("<HBBI", code, true, false, value) for code, true, false, value in program)


# --- the syscalls themselves ------------------------------------------------------------------------


def _machine() -> str:
    machine = platform.machine().lower()
    return {"amd64": "x86_64", "arm64": "aarch64"}.get(machine, machine)


def _libc() -> ctypes.CDLL:
    return ctypes.CDLL(None, use_errno=True)


def _syscall(number: int, *args: int | ctypes.Array | None) -> int:
    call = _libc().syscall
    call.restype = ctypes.c_long
    converted = [ctypes.c_long(arg) if isinstance(arg, int) else arg for arg in args]
    result = call(ctypes.c_long(number), *converted)
    if result < 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code))
    return int(result)


def _prctl(option: int, *args: int) -> int:
    call = _libc().prctl
    call.restype = ctypes.c_int
    padded = [*args, 0, 0, 0, 0][:4]
    result = call(ctypes.c_int(option), *(ctypes.c_ulong(value) for value in padded))
    if result < 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code))
    return int(result)


def abi_version() -> int:
    """This kernel's Landlock ABI, or 0 when it has none this sandbox can use (not Linux, no syscall
    table for this architecture, Landlock not built in or switched off at boot)."""
    if platform.system() != "Linux" or _machine() not in _NR:
        return 0
    try:
        return _syscall(_NR_CREATE_RULESET, None, 0, _CREATE_RULESET_VERSION)
    except (OSError, AttributeError):
        return 0


def available() -> bool:
    """Whether this kernel can hold a decoder in this sandbox, and this server can start the launcher."""
    return bool(sys.executable) and os.path.isabs(sys.executable) and abi_version() >= MIN_ABI


# --- building the command (in the server) ------------------------------------------------------------


def command(
    argv: Sequence[str],
    *,
    scratch: str,
    read: Sequence[str],
    cpu_seconds: int,
    address_space: int,
    file_size: int,
    open_files: int,
) -> list[str]:
    """The argv that runs `argv` (an absolute, resolved decoder path first) inside this sandbox."""
    policy = {
        "parent": os.getpid(),
        "scratch": scratch,
        "read": list(read),
        "limits": {"cpu": int(cpu_seconds), "as": int(address_space), "fsize": int(file_size),
                   "nofile": int(open_files)},
    }
    return [sys.executable, "-I", "-S", LAUNCHER, json.dumps(policy), *argv]


# --- the launcher (in the child, before the decoder exists) ------------------------------------------


class _Refused(Exception):
    """A step of the launch failed; the decoder must not run."""


def _limit(resource_module, which: int, value: int) -> None:
    _soft, hard = resource_module.getrlimit(which)
    ceiling = value if hard == resource_module.RLIM_INFINITY else min(value, hard)
    resource_module.setrlimit(which, (ceiling, ceiling))


def _set_limits(limits: dict) -> None:
    import resource  # loaded by launch() before any restriction, so this reads no file

    _limit(resource, resource.RLIMIT_CORE, 0)
    _limit(resource, resource.RLIMIT_CPU, max(1, int(limits["cpu"])))
    _limit(resource, resource.RLIMIT_AS, int(limits["as"]))
    _limit(resource, resource.RLIMIT_FSIZE, int(limits["fsize"]))
    _limit(resource, resource.RLIMIT_NOFILE, int(limits["nofile"]))


_CAP_VERSION_3 = 0x20080522
_CAP_SETPCAP = 8
_PR_SET_PDEATHSIG, _PR_SET_NO_NEW_PRIVS, _PR_GET_NO_NEW_PRIVS = 1, 38, 39
_PR_CAPBSET_DROP, _PR_SET_SECUREBITS, _PR_CAP_AMBIENT, _PR_CAP_AMBIENT_CLEAR_ALL = 24, 28, 47, 4
_PR_SET_SECCOMP, _SECCOMP_MODE_FILTER = 22, 2
#: NOROOT, NO_SETUID_FIXUP, NO_CAP_AMBIENT_RAISE and the lock bits of those and of KEEP_CAPS: root
#: regains no capability on exec, and nothing can undo that.
_SECUREBITS_LOCKED_EMPTY = 0xEF


def _capabilities(table: dict[str, int]) -> tuple[int, int, int]:
    header = ctypes.create_string_buffer(struct.pack("<Ii", _CAP_VERSION_3, 0))
    data = ctypes.create_string_buffer(24)
    _syscall(table["capget"], header, data)
    eff0, prm0, inh0, eff1, prm1, inh1 = struct.unpack("<6I", data.raw)
    return eff0 | eff1 << 32, prm0 | prm1 << 32, inh0 | inh1 << 32


def _drop_capabilities(table: dict[str, int]) -> None:
    """A process holding any capability (root) keeps none of them, now or after exec.

    Emptying the effective, permitted and inheritable sets is enough on its own: with no_new_privs
    set, exec intersects the new permitted set with the old one, so even root gains nothing back.
    Where CAP_SETPCAP is held, the bounding set is emptied and the securebits are locked as well."""
    effective, permitted, inheritable = _capabilities(table)
    if (effective, permitted, inheritable) == (0, 0, 0):
        return  # nothing held, and no_new_privs keeps it that way across exec
    _prctl(_PR_CAP_AMBIENT, _PR_CAP_AMBIENT_CLEAR_ALL)
    if effective & (1 << _CAP_SETPCAP):
        for capability in range(64):
            try:
                _prctl(_PR_CAPBSET_DROP, capability)
            except OSError as exc:
                if exc.errno == errno.EINVAL:
                    break  # past the last capability this kernel knows
                raise
        _prctl(_PR_SET_SECUREBITS, _SECUREBITS_LOCKED_EMPTY)
    header = ctypes.create_string_buffer(struct.pack("<Ii", _CAP_VERSION_3, 0))
    _syscall(table["capset"], header, ctypes.create_string_buffer(24))
    if _capabilities(table) != (0, 0, 0):
        raise _Refused("capabilities are still held after dropping them")


def _add_rule(ruleset: int, path: str, rights: int, handled: int) -> None:
    try:
        handle = os.open(path, os.O_PATH | os.O_CLOEXEC)
    except FileNotFoundError:
        return  # a tree this system does not have (/lib32, /sbin as a link...) grants nothing
    try:
        if not stat.S_ISDIR(os.fstat(handle).st_mode):
            rights &= _FILE_RIGHTS
        rule = ctypes.create_string_buffer(struct.pack("<Qi", rights & handled, handle))
        _syscall(_NR_ADD_RULE, ruleset, _RULE_PATH_BENEATH, rule, 0)
    finally:
        os.close(handle)


def _restrict_files_and_network(policy: dict, abi: int) -> None:
    handled = _handled_fs(abi)
    attr = struct.pack("<QQQ", handled, NET_BIND_TCP | NET_CONNECT_TCP if abi >= 4 else 0,
                       SCOPE_ABSTRACT_UNIX_SOCKET | SCOPE_SIGNAL if abi >= 6 else 0)
    size = 24 if abi >= 6 else 16 if abi >= 4 else 8
    ruleset = _syscall(_NR_CREATE_RULESET, ctypes.create_string_buffer(attr[:size]), size, 0)
    try:
        scratch = os.path.realpath(str(policy["scratch"]))
        roots = [os.path.realpath(str(entry)) for entry in policy["read"]]
        if "/" in (scratch, *roots) or not os.path.isabs(scratch):
            raise _Refused("a rule would cover the whole file system")
        for root in roots:
            _add_rule(ruleset, root, READ_RIGHTS, handled)
        for device, rights in DEVICES.items():
            _add_rule(ruleset, device, rights, handled)
        if not os.path.isdir(scratch):
            raise _Refused("the scratch directory is missing")
        _add_rule(ruleset, scratch, SCRATCH_RIGHTS, handled)
        _syscall(_NR_RESTRICT_SELF, ruleset, 0)
    finally:
        os.close(ruleset)


def _install_seccomp(machine: str) -> None:
    program = seccomp_program(machine, os.getpid())
    filters = ctypes.create_string_buffer(program)

    class _Prog(ctypes.Structure):
        _fields_ = [("len", ctypes.c_ushort), ("filter", ctypes.c_void_p)]

    prog = _Prog(len(program) // 8, ctypes.cast(filters, ctypes.c_void_p))
    call = _libc().prctl
    call.restype = ctypes.c_int
    if call(ctypes.c_int(_PR_SET_SECCOMP), ctypes.c_ulong(_SECCOMP_MODE_FILTER), ctypes.byref(prog),
            ctypes.c_ulong(0), ctypes.c_ulong(0)) != 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code))


def _check_fence() -> None:
    """Measure the fence from inside before the decoder starts. A kernel that accepted every call and
    enforced nothing must not get a decoder."""
    try:
        os.close(os.open("/", os.O_RDONLY | os.O_DIRECTORY))
    except PermissionError:
        pass
    else:
        raise _Refused("the root directory is still listable")
    import socket  # loaded by launch() before any restriction, so this reads no file

    for family in (socket.AF_INET, socket.AF_UNIX):
        try:
            socket.socket(family, socket.SOCK_STREAM).close()
        except PermissionError:
            continue
        raise _Refused("a socket could still be opened")


def launch(arguments: Sequence[str]) -> int:
    if len(arguments) < 2:
        raise _Refused("usage: _landlock.py POLICY DECODER [ARG ...]")
    # Every module the steps below use is loaded now, while the interpreter can still read its own
    # library: after the Landlock step, an import that has to open a file would fail.
    import resource  # noqa: F401
    import socket  # noqa: F401

    policy = json.loads(arguments[0])
    decoder = [str(part) for part in arguments[1:]]
    machine = _machine()
    if platform.system() != "Linux" or machine not in _NR:
        raise _Refused(f"no syscall table for {platform.system()} {machine}")
    abi = abi_version()
    if abi < MIN_ABI:
        raise _Refused(f"Landlock ABI {abi} is below {MIN_ABI}")
    _set_limits(policy["limits"])
    os.closerange(3, 1 << 16)
    _drop_capabilities(_NR[machine])
    _prctl(_PR_SET_NO_NEW_PRIVS, 1)
    _prctl(_PR_SET_PDEATHSIG, 9)
    if os.getppid() != int(policy["parent"]):
        raise _Refused("the server that started this decoder is gone")
    _restrict_files_and_network(policy, abi)
    _install_seccomp(machine)
    _check_fence()
    os.execve(decoder[0], decoder, dict(os.environ))
    return LAUNCH_FAILED  # not reached: execve either replaces this process or raises


def main() -> int:
    try:
        return launch(sys.argv[1:])
    except (_Refused, OSError, ValueError, KeyError, TypeError) as exc:
        detail = f"{type(exc).__name__}: {exc}" if not isinstance(exc, _Refused) else str(exc)
        with contextlib.suppress(OSError):
            os.write(2, f"{MARKER} the decoder was not started: {detail}\n".encode("utf-8", "replace"))
        return LAUNCH_FAILED


if __name__ == "__main__":
    sys.exit(main())
