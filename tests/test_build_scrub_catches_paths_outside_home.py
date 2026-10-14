"""The bundle's footprint scrub and gate cover builder roots OUTSIDE the home directory.

Release builds run from an external disk (/Volumes/...), a staging folder or a python outside
${HOME}: the checkout, the output dir, the staged CPython's original prefix and TMPDIR are all
builder footprints that the home-only scrub never touched — reproduced in job v072-u (2026-10):
the verbatim home gate passed on a bundle whose every file carried a /Volumes/... path.

Every test here drives the REAL scrub and gate functions extracted from build_macos_app.sh
against synthetic bundles. The gate, the root list and the text scrubs run on every platform;
only the libpython install-name rewrite is macOS-only (otool/install_name_tool exist nowhere
else), and its skip carries that reason. The in-place sed the script uses is BSD-shaped
(`sed -i ''`); on non-Darwin hosts the driver shims exactly that argument form onto GNU sed so
the real expressions still execute — the rewrite logic under test is the script's own.
"""
from __future__ import annotations

import os
import py_compile
import re
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
BUILD_SCRIPT = REPO / "installer" / "bundle" / "build_macos_app.sh"
BASE_PATH = "/usr/bin:/bin"


def _scrub_section() -> str:
    """The builder-root functions from the real script: the section comment through the last
    function before stage_self_contained, verbatim."""
    source = BUILD_SCRIPT.read_text(encoding="utf-8")
    match = re.search(
        r"\n(# A distributed bundle must not carry ANY absolute path.*?\n)stage_self_contained\(\) \{",
        source, re.DOTALL,
    )
    assert match, (
        "the builder-root scrub section (builder_root_list / scrub_builder_footprint / "
        "builder_footprint_gate) is missing from the build script"
    )
    return match.group(1)


def _run(section_body: str, body: str, tmp_path: Path, *, extra_env: dict[str, str] | None = None,
         timeout: int = 120) -> subprocess.CompletedProcess:
    """Run the real scrub functions under bash with a controlled builder environment."""
    driver = textwrap.dedent(f"""
        set -euo pipefail
        say() {{ printf '%s\\n' "$*"; }}
        die() {{ printf 'ERROR: %s\\n' "$*" >&2; exit 1; }}
        # The script's in-place sed is BSD-shaped (`sed -i ''`). Shim exactly that argument
        # form onto GNU sed on non-Darwin hosts; every other invocation passes through.
        if [[ "$(uname)" != "Darwin" ]]; then
          sed() {{
            if [[ "${{1:-}}" == "-i" && "${{2:-}}" == "" ]]; then shift 2; command sed -i "$@"; return; fi
            command sed "$@"
          }}
        fi
        {_scrub_section()}
        {body}
        echo DONE-OK
    """)
    app = tmp_path / "out" / "VOOL.app"
    env = {
        "PATH": BASE_PATH,
        "HOME": str(tmp_path / "builder-home"),
        "PROJECT_ROOT": str(tmp_path / "checkout"),
        "OUT_DIR": str(tmp_path / "out"),
        "TMPDIR": str(tmp_path / "tmp-env"),
        "APP": str(app),
        "PYTHONDONTWRITEBYTECODE": "",
        **(extra_env or {}),
    }
    (tmp_path / "tmp-env").mkdir(exist_ok=True)
    (tmp_path / "builder-home").mkdir(exist_ok=True)
    (tmp_path / "checkout").mkdir(exist_ok=True)
    return subprocess.run(
        ["bash", "-c", driver], capture_output=True, text=True, env=env, timeout=timeout,
        cwd=str(tmp_path),
    )


def _builder_root(tmp_path: Path) -> Path:
    """A build location that is under NO environment root (an external disk, not $HOME)."""
    root = tmp_path / "external-disk" / "v072-builder-root"
    root.mkdir(parents=True)
    home = tmp_path / "builder-home"
    assert not str(root).startswith(str(home)), "fixture must build outside the home"
    return root


# ------------------------------------------------------------------------------------------
# The root list: one list, longest first, short shared directories dropped
# ------------------------------------------------------------------------------------------


def test_the_root_list_covers_every_builder_root_longest_first(tmp_path: Path) -> None:
    body = textwrap.dedent("""
        builder_root_list "/Users/somebuilder/.local/share/uv/python/cpython-3.11" "/Volumes/DISK/vool-out"
    """)
    done = _run(_scrub_section(), body, tmp_path, extra_env={
        "HOME": "/Users/somebuilder",
        "PROJECT_ROOT": "/Users/somebuilder/work/vool",
        "OUT_DIR": "/Volumes/DISK/vool-out",
        "TMPDIR": "/var/folders/yy/T9/",
    })
    assert done.returncode == 0, done.stderr
    lines = done.stdout.splitlines()
    # Longest first (a nested root is rewritten whole before its parent), trailing slashes
    # normalised away, duplicates collapsed, every distinct builder root present exactly once.
    assert lines == [
        "/Users/somebuilder/.local/share/uv/python/cpython-3.11",
        "/Users/somebuilder/work/vool",
        "/Volumes/DISK/vool-out",
        "/var/folders/yy/T9",
        "/Users/somebuilder",
        "DONE-OK",
    ], done.stdout
    assert [len(p) for p in lines[:-1]] == sorted(
        [len(p) for p in lines[:-1]], reverse=True
    ), "roots must be ordered longest first"


def test_the_root_list_drops_root_and_shared_short_directories(tmp_path: Path) -> None:
    """"/" matches every absolute path; "/tmp"-class roots (under 5 characters) are shared
    system directories whose bytes a legitimately bundled file may carry."""
    body = textwrap.dedent("""
        out="$(builder_root_list "/")"
        [[ -z "${out}" ]] || { echo "UNEXPECTED:${out}"; }
    """)
    done = _run(_scrub_section(), body, tmp_path, extra_env={
        "HOME": "/tmp", "PROJECT_ROOT": "/var", "OUT_DIR": "/", "TMPDIR": "",
    })
    assert done.returncode == 0, done.stderr
    assert "UNEXPECTED" not in done.stdout, done.stdout
    assert done.stdout.strip() == "DONE-OK", done.stdout


def test_the_scrub_section_records_the_ssl_exception_next_to_the_root_list() -> None:
    """The allowed exception is written once, beside the list, with its reason: the rebuilt
    cryptography wheel legitimately carries /private/etc/ssl (its own OpenSSL prefix)."""
    section = _scrub_section()
    assert "/private/etc/ssl" in section
    assert "built_wheels.json" in section


# ------------------------------------------------------------------------------------------
# The gate: binary-safe, names the root by its last part, files relative to the bundle,
# never the builder's full home path
# ------------------------------------------------------------------------------------------


def test_the_gate_fails_on_a_non_home_root_naming_it_and_the_files(tmp_path: Path) -> None:
    app = tmp_path / "out" / "VOOL.app"
    (app / "Contents" / "Resources" / "app").mkdir(parents=True)
    root = _builder_root(tmp_path)
    for i in range(7):
        (app / "Contents" / "Resources" / "app" / f"file{i}.txt").write_text(f"leak {root}/python\n")
    body = textwrap.dedent(f"""
        printf '%s\n' "{tmp_path / 'builder-home'}" "{root}" | builder_footprint_gate "{app}"
    """)
    done = _run(_scrub_section(), body, tmp_path)
    assert done.returncode != 0, "a non-home builder root in the bundle must fail the build"
    combined = done.stdout + done.stderr
    assert root.name in combined, "the failing root must be named"
    assert str(root) not in combined, "the full builder root must not be printed"
    assert str(tmp_path / "builder-home") not in combined, "the builder home must not be printed"
    assert "Contents/Resources/app/file0.txt" in done.stderr, "files must be bundle-relative"
    assert "refusing to ship" in combined
    listed = [ln for ln in done.stderr.splitlines() if "Contents/Resources" in ln]
    assert len(listed) == 5, f"at most 5 files may be listed, got {len(listed)}"


def test_the_gate_passes_a_clean_bundle_and_ignores_the_ssl_exception(tmp_path: Path) -> None:
    app = tmp_path / "out" / "VOOL.app"
    (app / "Contents" / "Resources" / "app").mkdir(parents=True)
    (app / "Contents" / "Resources" / "app" / "openssl.txt").write_text(
        "openssl_prefix=/private/etc/ssl\n"
    )
    root = _builder_root(tmp_path)
    body = textwrap.dedent(f"""
        printf '%s\n' "{tmp_path / 'builder-home'}" "{root}" | builder_footprint_gate "{app}"
    """)
    done = _run(_scrub_section(), body, tmp_path)
    assert done.returncode == 0, done.stderr
    assert "DONE-OK" in done.stdout


# ------------------------------------------------------------------------------------------
# The whole scrub, on a synthetic bundle whose leaks are exactly the repairable categories
# ------------------------------------------------------------------------------------------


def _synthetic_bundle(tmp_path: Path, *, unrepairable_leak: bool = False) -> tuple[Path, Path]:
    """A staged bundle whose every leak is a category the scrub repairs:
    _sysconfigdata's prefix, a uv #!/bin/sh exec-wrapper, and a .pyc compiled at a builder
    path (regenerated by the recompile). With unrepairable_leak, an arbitrary text file also
    carries the root — no repair may touch it, only the gate can answer it."""
    root = _builder_root(tmp_path)
    app = tmp_path / "out" / "VOOL.app"
    res = app / "Contents" / "Resources"

    sysconf = res / "python" / "lib" / "_sysconfigdata_darwin.py"
    sysconf.parent.mkdir(parents=True)
    sysconf.write_text(f'prefix = "{root}/python"\n')

    wrapper = res / "python" / "bin" / "uv-tool"
    wrapper.parent.mkdir(parents=True)
    wrapper.write_text(f"#!/bin/sh\n'''exec' '{root}/python/bin/python3' \"$0\" \"$@\"\n'''\n")

    source = res / "app" / "mod.py"
    source.parent.mkdir(parents=True)
    source.write_text("VALUE = 1\n")
    staged_src = root / "staging" / "mod.py"
    staged_src.parent.mkdir(parents=True)
    staged_src.write_text("VALUE = 1\n")
    pyc = res / "app" / "__pycache__" / f"mod.{sys.implementation.cache_tag}.pyc"
    py_compile.compile(str(staged_src), cfile=str(pyc), doraise=True)

    ssl_note = res / "app" / "openssl.txt"
    ssl_note.write_text("openssl_prefix=/private/etc/ssl\n")

    if unrepairable_leak:
        notes = res / "app" / "notes.txt"
        notes.write_text(f"compiled from {root}/work/notes\n")

    # The fixture must be a real leak before the scrub runs, or the pass below is vacuous.
    for probe in (sysconf, wrapper, pyc):
        assert str(root).encode() in Path(probe).read_bytes(), f"{probe} carries no builder bytes"
    return root, app


def test_the_scrub_cleans_every_root_category_and_the_gate_passes(tmp_path: Path) -> None:
    root, app = _synthetic_bundle(tmp_path)
    res = app / "Contents" / "Resources"
    body = textwrap.dedent(f"""
        scrub_builder_footprint "{res}" "{sys.executable}" "{root}"
    """)
    done = _run(_scrub_section(), body, tmp_path, timeout=180)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "builder-footprint scrub verified" in done.stdout

    hits = subprocess.run(["grep", "-r", "-a", "-l", "-F", str(root), str(app)],
                          capture_output=True, text=True)
    assert hits.returncode != 0, f"builder bytes survived the scrub:\n{hits.stdout}"

    sysconf = (res / "python" / "lib" / "_sysconfigdata_darwin.py").read_text()
    assert 'prefix = "/install/python"' in sysconf, sysconf
    wrapper = (res / "python" / "bin" / "uv-tool").read_text()
    assert "'python3'" in wrapper and str(root) not in wrapper, wrapper
    pyc = res / "app" / "__pycache__" / f"mod.{sys.implementation.cache_tag}.pyc"
    assert str(root).encode() not in pyc.read_bytes(), "the recompile left the old path in the .pyc"
    assert b"/Applications/VOOL.app" in pyc.read_bytes(), "the recompile lost its -p install stamp"
    assert "/private/etc/ssl" in (res / "app" / "openssl.txt").read_text(), "ssl exception altered"


def test_a_leak_no_scrub_step_repairs_still_fails_the_build(tmp_path: Path) -> None:
    """The gate runs INSIDE the scrub: a category no repair touches (an arbitrary text file)
    must fail the build and name the root — the scrub can never widen into a vacuous pass."""
    root, app = _synthetic_bundle(tmp_path, unrepairable_leak=True)
    res = app / "Contents" / "Resources"
    body = textwrap.dedent(f"""
        scrub_builder_footprint "{res}" "{sys.executable}" "{root}"
    """)
    done = _run(_scrub_section(), body, tmp_path, timeout=180)
    assert done.returncode != 0, "an unrepairable builder path shipped"
    combined = done.stdout + done.stderr
    assert root.name in combined and str(root) not in combined
    assert "Contents/Resources/app/notes.txt" in done.stderr
    assert "refusing to ship" in combined


@pytest.mark.skipif(
    sys.platform != "darwin" or not shutil.which("otool") or not shutil.which("clang"),
    reason="otool/install_name_tool (the Mach-O install-name rewrite) exist only on macOS; "
           "the fixture dylib is linked with clang",
)
def test_libpython_install_name_is_rewritten_for_a_non_home_prefix(tmp_path: Path) -> None:
    """Scrub step (b): the embedded CPython's install name is rewritten to @rpath when its
    original prefix sits outside the home — the home-only prefix test used to skip it. The
    fixture links a real dylib AT the builder root: its install name is then genuinely that
    path, exactly like a CPython staged from an external disk."""
    root, app = _synthetic_bundle(tmp_path)
    res = app / "Contents" / "Resources"
    dylib = res / "python" / "lib" / "libpython3.11.dylib"
    built_at_root = root / "python" / "lib" / "libpython3.11.dylib"
    built_at_root.parent.mkdir(parents=True, exist_ok=True)
    probe = subprocess.run(
        ["clang", "-dynamiclib", "-o", str(built_at_root), "-xc", "-"],
        input="int marker(void) { return 7; }", capture_output=True, text=True,
    )
    assert probe.returncode == 0, probe.stderr
    shutil.move(str(built_at_root), str(dylib))
    assert f"{root}/python/lib/libpython3.11.dylib" in subprocess.run(
        ["otool", "-D", str(dylib)], capture_output=True, text=True
    ).stdout, "fixture must carry the builder root as its install name"
    body = textwrap.dedent(f"""
        scrub_builder_footprint "{res}" "{sys.executable}" "{root}"
    """)
    done = _run(_scrub_section(), body, tmp_path, timeout=180)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "rewrote libpython install name to @rpath" in done.stdout
    shown = subprocess.run(["otool", "-D", str(dylib)], capture_output=True, text=True)
    assert shown.stdout.strip().splitlines()[-1] == "@rpath/libpython3.11.dylib", shown.stdout


def test_the_build_script_still_parses_under_macos_bash() -> None:
    bash = "/bin/bash" if os.path.exists("/bin/bash") else shutil.which("bash")
    assert bash, "no bash"
    done = subprocess.run([bash, "-n", str(BUILD_SCRIPT)], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
