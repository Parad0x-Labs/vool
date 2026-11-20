"""Optional local Linux container boundary for hosts without process isolation.

Provisioning is an owner's separate local action. This module never starts a VM,
pulls an image, forwards an agent, or exposes the container daemon to a package.
Only a local Unix socket and this pinned, already present runtime are accepted.
"""
from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path

IMAGE = "python@sha256:a0f57064cbdcd6285fa45f51237ab0b35ea8ebaed06a1f5973e89e19dfb86704"
_LOCK = threading.Lock()
_PROBE: dict[tuple, tuple[float, bool]] = {}

# An additional filter narrows the runtime's default seccomp policy. Normal
# threads remain possible, but a service package cannot detach a new process.
# clone3's pointed-to flags cannot be inspected by BPF, so return ENOSYS and
# let libc use clone, whose flags are a scalar syscall argument.
_BOOTSTRAP = r'''
import ctypes,json,os,platform,sys
if sys.argv[1] == "1":
    machine=platform.machine()
    if machine == "aarch64": arch,clone,forks=0xc00000b7,220,()
    elif machine == "x86_64": arch,clone,forks=0xc000003e,56,(57,58)
    else: raise RuntimeError("This runtime architecture cannot isolate plugin child processes.")
    class Instruction(ctypes.Structure):
        _fields_=[("code",ctypes.c_ushort),("jt",ctypes.c_ubyte),("jf",ctypes.c_ubyte),("k",ctypes.c_uint)]
    class Program(ctypes.Structure):
        _fields_=[("len",ctypes.c_ushort),("filter",ctypes.POINTER(Instruction))]
    rules=[(0x20,0,0,4),(0x15,1,0,arch),(0x06,0,0,0x80000000),(0x20,0,0,0),
           (0x45,0,1,0x40000000),(0x06,0,0,0x80000000),
           (0x15,0,1,435),(0x06,0,0,0x00050000|38)]
    for number in forks: rules += [(0x15,0,1,number),(0x06,0,0,0x00050001)]
    rules += [(0x15,0,3,clone),(0x20,0,0,16),(0x45,1,0,0x10000),
              (0x06,0,0,0x00050001),(0x06,0,0,0x7fff0000)]
    instructions=(Instruction*len(rules))(*(Instruction(*row) for row in rules))
    program=Program(len(rules),instructions)
    libc=ctypes.CDLL(None,use_errno=True)
    libc.prctl.argtypes=[ctypes.c_int,ctypes.c_ulong,ctypes.c_ulong,ctypes.c_ulong,ctypes.c_ulong]
    if libc.prctl(38,1,0,0,0) or libc.prctl(22,2,ctypes.cast(ctypes.byref(program),ctypes.c_void_p).value,0,0):
        raise RuntimeError("The plugin child-process boundary could not be installed.")
args=json.loads(sys.argv[2])
environment={name:os.environ[name] for name in json.loads(sys.argv[3]) if name in os.environ}
line=b""
while not line.endswith(b"\n"):
    part=os.read(0,1)
    if not part or len(line)>65536: raise SystemExit("The plugin's private settings did not arrive.")
    line+=part
environment.update({str(k):str(v) for k,v in json.loads(line).items()})
os.execvpe(args[0],args,environment)
'''


def _runtime():
    docker = shutil.which("docker")
    value = os.environ.get("VOOL_PLUGIN_DOCKER_SOCKET", "")
    if not docker or not value or not Path(value).is_absolute():
        return None
    try:
        path = Path(value).resolve(strict=True)
        if not stat.S_ISSOCK(path.stat().st_mode):
            return None
    except OSError:
        return None
    return docker, str(path)


def _control_env():
    return {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "LANG": "C.UTF-8"}


def available() -> bool:
    runtime = _runtime()
    if runtime is None:
        return False
    with _LOCK:
        seen = _PROBE.get(runtime)
        if seen and time.monotonic() - seen[0] < 10:
            return seen[1]
        docker, endpoint = runtime
        try:
            result = subprocess.run([docker, "--host", "unix://" + endpoint,
                                     "image", "inspect", "--format", "{{.Os}}", IMAGE],
                                    env=_control_env(), capture_output=True, timeout=5, check=False)
            ready = result.returncode == 0 and result.stdout.strip() == b"linux"
        except (OSError, subprocess.TimeoutExpired):
            ready = False
        _PROBE[runtime] = (time.monotonic(), ready)
        return ready


class ContainerCommand(list):
    """A launch command plus cleanup of only its recorded container identity.

    The child's service keys never travel in a host environment or argv: the starter writes
    them as the first line of the child's input (``private_input_line``), which passes through
    this launcher and the container client unread and is consumed by the bootstrap inside.
    """
    def __init__(self, config, directory):
        self.config = config
        self.directory = directory
        self.lock = threading.Lock()
        wire = {key: value for key, value in config.items() if key != "args"}
        super().__init__([sys.executable, "-I", str(Path(__file__).resolve()), json.dumps(wire), *config["args"]])

    @staticmethod
    def private_input_line(values) -> str:
        return json.dumps({str(name): str(value) for name, value in dict(values or {}).items()}) + "\n"

    def close(self):
        with self.lock:
            config = self.config
            control = [config["docker"], "--config", config["config"], "--host", "unix://" + config["socket"]]
            try:
                result = subprocess.run([*control, "container", "inspect", config["name"]],
                                        env=_control_env(), capture_output=True, timeout=5, check=False)
                if result.returncode == 0:
                    rows = json.loads(result.stdout)
                    row = rows[0]
                    identity = row["Id"]
                    if (re.fullmatch(r"[a-f0-9]{64}", identity) and
                            row["Config"]["Labels"].get("vool.invocation") == config["nonce"]):
                        subprocess.run([*control, "container", "rm", "--force", identity],
                                       env=_control_env(), capture_output=True, timeout=5, check=False)
            except (OSError, ValueError, KeyError, IndexError, TypeError, subprocess.TimeoutExpired):
                pass
            shutil.rmtree(self.directory, ignore_errors=True)


def command(argv, *, working_directory, writable_roots, readable_roots=(), allow_network=False, proxy_port=0,
            isolate_process_tree=False):
    from core.plugin_executor import ConfinementUnavailableError
    from core.runtime_paths import runtime_child_scratch_root

    if not available():
        raise ConfinementUnavailableError("Plugins that run their own programs are off on this computer, because it can't keep them walled off from your saved keys. Plugins that only connect a service still work.")
    if allow_network or proxy_port:
        raise ConfinementUnavailableError("Plugin process network access is unavailable on this device. It remains off.")
    root = Path(working_directory).resolve()
    writable = {Path(path).resolve() for path in writable_roots}
    readable = {root, *(Path(path).resolve() for path in readable_roots if Path(path).exists()), *writable}
    args = list(argv)
    program = Path(args[0])
    # Python packages use the pinned guest interpreter, never a host executable
    # or a mounted host environment. Other bundled runtimes must run on Linux.
    outside_package = not program.resolve().is_relative_to(root)
    if outside_package and program.name.startswith("python"):
        if isolate_process_tree:
            raise ConfinementUnavailableError("This plugin tries to run something outside its own files, so VOOL won't start it.")
        args[0] = "/usr/local/bin/python3"
        for token in args[1:]:
            if token.startswith("-"):
                continue
            candidate = Path(token)
            if candidate.is_absolute() and candidate.is_file():
                readable.add(candidate.parent.resolve())
                break
    elif program.is_file():
        readable.add(program.parent.resolve())
        header = program.read_bytes()[:256].split(b"\n", 1)[0]
        if header.startswith(b"#!") and b"python" in header:
            args.insert(0, "/usr/local/bin/python3")
    forbidden = {Path.home().resolve(), Path(os.environ.get("VOOL_HOME") or os.environ.get("NULLA_HOME") or Path.home()).resolve()}
    if any(str(path) == path.anchor or any(home.is_relative_to(path) for home in forbidden) for path in readable):
        raise ConfinementUnavailableError("The plugin requested a folder containing private application or home data.")
    scratch = runtime_child_scratch_root("plugin-container")
    scratch.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="invocation-", dir=scratch))
    (directory / "config").mkdir()
    nonce = uuid.uuid4().hex
    docker, endpoint = _runtime()
    config = {"docker": docker, "socket": endpoint, "config": str(directory / "config"),
              "nonce": nonce, "name": "vool-plugin-" + nonce, "cwd": str(root), "args": args,
              "no_fork": bool(isolate_process_tree),
              "mounts": [{"path": str(path), "write": path in writable} for path in sorted(readable)]}
    return ContainerCommand(config, directory), "kernel:container"


def _run(config):
    control = [config["docker"], "--config", config["config"], "--host", "unix://" + config["socket"]]
    argv = [*control, "run", "--rm", "--pull=never", "--name", config["name"],
            "--label", "vool.invocation=" + config["nonce"], "--network=none", "--read-only", "--log-driver=none",
            "--cap-drop=ALL", "--security-opt=no-new-privileges", "--pids-limit=64", "--memory=256m",
            "--cpus=1", "--user", f"{os.getuid()}:{os.getgid()}", "--ipc=private",
            "--tmpfs", "/tmp:rw,noexec,nosuid,size=16m", "--workdir", config["cwd"], "-i"]
    for mount in config["mounts"]:
        path = mount["path"]
        argv += ["--mount", f"type=bind,source={path},target={path}" + ("" if mount["write"] else ",readonly")]
    # Only the executor's already-minimal environment, without service keys, reaches
    # this trusted launcher. Names go in argv; their values travel only through the
    # environment. Keys arrive inside the container on the child's input.
    env = dict(os.environ)
    env["PATH"] = "/usr/local/bin:/usr/bin:/bin"
    env["TMPDIR"] = "/tmp"
    for name in sorted(env):
        argv += ["--env", name]
    argv += ["--entrypoint", "/usr/local/bin/python3", IMAGE, "-I", "-c", _BOOTSTRAP,
             "1" if config["no_fork"] else "0", json.dumps(config["args"]), json.dumps(sorted(env))]
    try:
        return subprocess.call(argv, env=env)
    finally:
        with contextlib.suppress(Exception):
            ContainerCommand(config, Path(config["config"]).parent).close()


if __name__ == "__main__":
    configuration = json.loads(sys.argv[1])
    configuration["args"] = sys.argv[2:]
    raise SystemExit(_run(configuration))
