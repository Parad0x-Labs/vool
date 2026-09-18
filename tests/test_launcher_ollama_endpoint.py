"""The macOS launcher resolves the local Ollama endpoint the way the runtime does, and starts the bundled server only
for the default local endpoint when nothing answers there. Nothing here touches a real Ollama: `curl` and `ollama`
are fakes on a private PATH that record their arguments.
"""
from __future__ import annotations

import os
import re
import stat
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "installer" / "bundle" / "launcher_ollama.sh"
BUILD = ROOT / "installer" / "bundle" / "build_macos_app.sh"


def _bash(script: str, env: dict[str, str]) -> subprocess.CompletedProcess:
    base_env = {"PATH": env.pop("PATH", "/usr/bin:/bin"), "HOME": os.environ.get("HOME", "/tmp")}
    base_env.update(env)
    return subprocess.run(["/bin/bash", "-c", f'source "{HELPER}"; {script}'], capture_output=True, text=True, env=base_env, timeout=60)


def _fakes(tmp_path: Path, *, curl_exit: int) -> tuple[Path, Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True)
    log = tmp_path / "calls.log"
    (bin_dir / "curl").write_text(f'#!/bin/bash\necho "curl $*" >> "{log}"\nexit {curl_exit}\n')
    (bin_dir / "ollama").write_text(f'#!/bin/bash\necho "ollama $*" >> "{log}"\nsleep 0.2\n')
    (bin_dir / "seq").write_text('#!/bin/bash\necho 1\n')  # one wait-loop iteration keeps the test quick
    (bin_dir / "sleep").write_text('#!/bin/bash\nexit 0\n')
    for f in bin_dir.iterdir():
        f.chmod(f.stat().st_mode | stat.S_IEXEC)
    return bin_dir, log


def test_the_helper_resolves_in_the_runtimes_order():
    assert _bash("vool_ollama_base", {}).stdout == "http://127.0.0.1:11434"
    assert _bash("vool_ollama_base", {"OLLAMA_HOST": "127.0.0.1:9"}).stdout == "http://127.0.0.1:9"
    assert _bash("vool_ollama_base", {"VOOL_OLLAMA_URL": "http://127.0.0.1:7/"}).stdout == "http://127.0.0.1:7"
    assert _bash("vool_ollama_base", {"VOOL_RAW_OLLAMA_API_URL": "http://a:1", "OLLAMA_HOST": "http://b:2"}).stdout == "http://a:1"
    assert _bash("vool_ollama_is_default_local && echo yes || echo no", {}).stdout.strip() == "yes"
    assert _bash("vool_ollama_is_default_local && echo yes || echo no", {"OLLAMA_HOST": "http://127.0.0.1:9"}).stdout.strip() == "no"


def test_a_dead_port_launch_neither_probes_nor_starts_a_server(tmp_path):
    bin_dir, log = _fakes(tmp_path, curl_exit=22)
    out = _bash(f'vool_ensure_bundled_ollama "{bin_dir}/ollama" "{tmp_path}/ollama.log"; echo "rc=$?"', {"PATH": f"{bin_dir}:/usr/bin:/bin", "OLLAMA_HOST": "http://127.0.0.1:9"})
    assert "rc=0" in out.stdout and "not probing" in out.stdout, out
    assert not log.exists(), f"the launcher reached out although the endpoint was configured elsewhere: {log.read_text() if log.exists() else ''}"


def test_the_default_endpoint_starts_the_bundled_server_only_when_nothing_answers(tmp_path):
    bin_dir, log = _fakes(tmp_path, curl_exit=22)
    out = _bash(f'vool_ensure_bundled_ollama "{bin_dir}/ollama" "{tmp_path}/ollama.log"', {"PATH": f"{bin_dir}:/usr/bin:/bin"})
    calls = log.read_text().splitlines()
    assert "starting bundled ollama" in out.stdout
    assert calls[0].startswith("curl") and "http://127.0.0.1:11434/api/tags" in calls[0], calls
    assert any(c.startswith("ollama serve") for c in calls), calls
    bin_dir2, log2 = _fakes(tmp_path / "second", curl_exit=0)
    out2 = _bash(f'vool_ensure_bundled_ollama "{bin_dir2}/ollama" "{tmp_path}/ollama2.log"', {"PATH": f"{bin_dir2}:/usr/bin:/bin"})
    calls2 = log2.read_text().splitlines()
    assert "starting bundled ollama" not in out2.stdout and not any(c.startswith("ollama") for c in calls2), calls2


def test_the_launcher_template_sources_the_helper_and_carries_no_literal_probe():
    src = BUILD.read_text(encoding="utf-8")
    start = src.index("cat > \"${MACOS}/VOOL\" <<'LAUNCHER'") if "cat > \"${MACOS}/VOOL\" <<'LAUNCHER'" in src else src.index("<<'LAUNCHER'")
    end = src.index("\nLAUNCHER\n", start)
    launcher = src[start:end]
    assert 'source "${RES}/app/installer/bundle/launcher_ollama.sh"' in launcher
    assert "vool_ensure_bundled_ollama" in launcher
    assert not re.search(r"127\.0\.0\.1:11434|localhost:11434", launcher), "the launcher probes a literal endpoint again"
