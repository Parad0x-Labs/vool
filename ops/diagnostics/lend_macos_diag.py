"""Diagnostic only (not a test): why the lend worker dies inside the macOS sandbox on a hosted runner."""
import os, subprocess, sys, sysconfig, tempfile
from pathlib import Path

sys.path.insert(0, os.getcwd())
from core.artifact_readers import _sandbox as sb
from core.lend.worker import runtime_reads

print("executable", sys.executable)
print("_base_executable", sys._base_executable, "->", Path(sys._base_executable).resolve())
print("base_prefix", sys.base_prefix, "prefix", sys.prefix)
for k in ("stdlib", "platstdlib", "purelib", "platlib"):
    print(k, sysconfig.get_path(k), "->", Path(sysconfig.get_path(k)).resolve())
reads = runtime_reads()
print("runtime_reads", reads)
print("resolved", sb._resolved(reads))
print("framework", sysconfig.get_config_var("PYTHONFRAMEWORK"), sysconfig.get_config_var("PYTHONFRAMEWORKPREFIX"))

exe = str(Path(sys._base_executable).resolve())
cases = {
    "print": "print('hi')",
    "import_ctypes": "import ctypes; print('ok')",
    "libproc": "import ctypes,os; p=ctypes.CDLL('/usr/lib/libproc.dylib', use_errno=True); print('ok')",
    "limit_memory": f"import sys; sys.path.insert(0, {sysconfig.get_path('purelib')!r}); "
                    "import resource, ctypes, os\n"
                    "info=(ctypes.c_uint64*12)(); p=ctypes.CDLL('/usr/lib/libproc.dylib', use_errno=True)\n"
                    "n=p.proc_pidinfo(os.getpid(),4,ctypes.c_uint64(0),info,ctypes.sizeof(info)); print('pidinfo',n, info[0])\n"
                    "c=info[0]+512*1024*1024; resource.setrlimit(resource.RLIMIT_AS,(c,c)); print('set', resource.getrlimit(resource.RLIMIT_AS))\n"
                    "x=bytearray(64*1024*1024); print('alloc ok')\n"
                    "import pypdf, PIL; print('imports ok')",
    "imports": f"import sys; sys.path.insert(0, {sysconfig.get_path('purelib')!r}); import pypdf, PIL, reportlab; print('imports ok')",
}
for mach in (False, True):
    for name, code in cases.items():
        with tempfile.TemporaryDirectory() as d:
            scratch = str(Path(d).resolve())
            prof = sb._profile(scratch=scratch, extra_read=sb._resolved(reads), allow_gpu=False,
                               allow_mach_lookup=mach, allow_child_signals=True)
            env = {"PATH": "/usr/bin:/bin", "TMPDIR": scratch, "LC_ALL": "C.UTF-8", "HOME": scratch}
            r = subprocess.run(["/usr/bin/sandbox-exec", "-p", prof, exe, "-I", "-c", code],
                               capture_output=True, text=True, cwd=scratch, env=env, timeout=60)
            print(f"== mach={mach} {name}: rc={r.returncode}\n  out={r.stdout.strip()[:400]}\n  err={r.stderr.strip()[-1500:]}")
        r2 = subprocess.run([exe, "-I", "-c", code], capture_output=True, text=True, timeout=60)
        if mach is False:
            print(f"   (unsandboxed rc={r2.returncode} out={r2.stdout.strip()[:200]} err={r2.stderr.strip()[-300:]})")
