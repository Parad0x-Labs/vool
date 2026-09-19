"""macOS native-app packaging contracts, proven against PRODUCED ARTIFACTS, never prose greps.

On 2026-08-27 a default (wrapper) VOOL.app was built in a fresh exact-SHA worktree whose
`Start_VOOL.sh` (gitignored installer output) could not exist there. The build printed
`OK:` and promised a native window; the app then spent 180 s polling a server nothing had
started, waited 90 s more, opened Chrome `--app`, and exited 0 — LaunchServices saw appDeath,
no crash report, because this was a clean exit. Every test here executes the shipped scripts in
isolated fixtures (tmp HOME, synthetic interpreters, recorded-but-not-launched browsers):

- a green verdict means THIS bundle mode's launch contract was mechanically satisfied;
- an incapable interpreter never wins by version alone;
- a deterministic startup failure never buys minutes of silence;
- a browser window is NEVER classified as a native-app success unless explicitly opted into.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
BUILD_SCRIPT = REPO / "installer" / "bundle" / "build_macos_app.sh"
WINDOW_SCRIPT = REPO / "installer" / "bundle" / "vool_window.py"
SUPERVISOR_SCRIPT = REPO / "installer" / "bundle" / "native_runtime_supervisor.py"
# The build refuses to finish without its architecture/deployment gate (and the census the gate
# reads), so a fixture install that omits them is not a fixture of the real build.
ARCH_GATE_SCRIPT = REPO / "installer" / "bundle" / "macos_arch_gate.py"
ARCH_CENSUS_SCRIPT = REPO / "installer" / "bundle" / "macos_arch_census.py"

# Minimal, quiet PATH: core tools + the candidates we control come FIRST via the shim dir, so the
# host machine's actual interpreters (~/.local/bin/python3.12 without webview, /usr/bin/python3 with)
# cannot leak environment luck into these assertions.
BASE_PATH = "/usr/bin:/bin"


# ------------------------------------------------------------------------------------------
# Fixture machinery: a synthetic install root + synthetic interpreters
# ------------------------------------------------------------------------------------------


def _py_shim(shim_dir: Path, name: str, *, webview: bool, registry: bool = True) -> Path:
    """An interpreter stand-in that grades two questions: `import webview`, and whether the
    served command surface's import closure builds.

    The second is what the runtime's own boot gate asks (core/runtime_dependency_preflight.py).
    A shim that answered 0 to everything else would make the build's conformance check vacuous —
    it would pass no matter what the lean dependency list omitted.
    """
    shim_dir.mkdir(parents=True, exist_ok=True)
    path = shim_dir / name
    path.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "-c" ] && [ "$2" = "import webview" ]; then\n'
        f"  exit {'0' if webview else '1'}\n"
        "fi\n"
        'case "$2" in\n'
        "  *command_registry*)\n"
        + (
            "    exit 0 ;;\n"
            if registry
            else "    echo \"ModuleNotFoundError: No module named 'zstandard'\" >&2; exit 1 ;;\n"
        )
        + "esac\n"
        "exit 0\n",
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path


def _install_pythons(shim_dir: Path, versions: dict[str, bool]) -> None:
    for name, ok in versions.items():
        _py_shim(shim_dir, name, webview=ok)


def _fixture_install(
    tmp_path: Path,
    *,
    with_start: bool = True,
    stop_script: str | None = None,
    venv_webview: bool | None = None,
) -> Path:
    """A minimal local-install tree shaped like what the wrapper launcher drives."""
    root = tmp_path / "install"
    (root / "config" / "release").mkdir(parents=True)
    (root / "config" / "release" / "update_channel.json").write_text('{"release_version": "9.9.9-repair"}')
    bundle_dir = root / "installer" / "bundle"
    bundle_dir.mkdir(parents=True)
    shutil.copy2(BUILD_SCRIPT, bundle_dir / BUILD_SCRIPT.name)
    shutil.copy2(WINDOW_SCRIPT, bundle_dir / WINDOW_SCRIPT.name)
    shutil.copy2(SUPERVISOR_SCRIPT, bundle_dir / SUPERVISOR_SCRIPT.name)
    shutil.copy2(ARCH_GATE_SCRIPT, bundle_dir / ARCH_GATE_SCRIPT.name)
    shutil.copy2(ARCH_CENSUS_SCRIPT, bundle_dir / ARCH_CENSUS_SCRIPT.name)
    (bundle_dir / "vool-open.ps1").write_text("# unused on posix")
    if with_start:
        start = root / "Start_VOOL.sh"
        start.write_text("#!/usr/bin/env bash\nexit 0\n")
        start.chmod(0o755)
    stop_body = stop_script or "#!/usr/bin/env bash\nexit 0\n"
    stop = root / "Stop_VOOL.sh"
    stop.write_text(stop_body)
    stop.chmod(0o755)
    if venv_webview is not None:
        venv_bin = root / ".venv" / "bin"
        venv_bin.mkdir(parents=True)
        _py_shim(venv_bin, "python", webview=venv_webview)
    return root


def _build(root: Path, out: Path, *, pythons: dict[str, bool], extra_env: dict[str, str] | None = None,
           args: list[str] | None = None) -> subprocess.CompletedProcess:
    """Run the fixture's copy of the real build script; no network, no uv, no user state.

    NOTE: interpreters are ALWAYS synthetic here. A real `import webview` under a synthetic $HOME
    resolves the wrong user site-packages, so machine interpreters must never be candidates.
    """
    shim_dir = out / "shims"
    shim_dir.mkdir(parents=True, exist_ok=True)
    for name, ok in pythons.items():
        _py_shim(shim_dir, name, webview=ok)
    env = {
        "PATH": f"{shim_dir}:{BASE_PATH}",
        "HOME": str(out),
        **(extra_env or {}),
    }
    cmd = ["bash", str(root / "installer" / "bundle" / BUILD_SCRIPT.name), "--out", str(out / "VOOL.app")]
    if args:
        cmd += args
    return subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=120)


def _plist_value(app: Path, key: str) -> str:
    done = subprocess.run(
        ["plutil", "-extract", key, "raw", "-o", "-", str(app / "Contents" / "Info.plist")],
        capture_output=True, text=True,
    )
    assert done.returncode == 0, f"{key} unreadable from Info.plist: {done.stderr}"
    return done.stdout.strip()


def _launchers() -> dict[str, str]:
    """The exact text each launcher receives, applying bash <<EOF unescaping (see the
    staleness-guard suite); both launchers must stay shell-parseable."""
    source = BUILD_SCRIPT.read_text(encoding="utf-8")
    quoted = re.search(r"<<'LAUNCHER'\n(.*?)\nLAUNCHER\n", source, re.DOTALL)
    unquoted = re.search(r"<<LAUNCHER\n(.*?)\nLAUNCHER\n", source, re.DOTALL)
    assert quoted and unquoted

    def unescape(body: str) -> str:
        out, i = [], 0
        while i < len(body):
            ch = body[i]
            if ch == "\\" and i + 1 < len(body) and body[i + 1] in "$`\\\n":
                nxt = body[i + 1]
                if nxt != "\n":
                    out.append(nxt)
                i += 2
                continue
            out.append(ch)
            i += 1
        return "".join(out)

    return {"self_contained": quoted.group(1), "wrapper": unescape(unquoted.group(1))}


# ------------------------------------------------------------------------------------------
# Produced-artifact contracts for the DEFAULT wrapper build
# ------------------------------------------------------------------------------------------

_COMPLETE_PYTHONS = {"python3.13": True, "python3.12": True, "python3.11": True,
                     "python3.10": True, "python3": True}
_INCAPABLE_PYTHONS = {name: False for name in _COMPLETE_PYTHONS}


def test_wrapper_build_passes_and_stamps_mode_when_the_install_is_complete(tmp_path: Path) -> None:
    root = _fixture_install(tmp_path, venv_webview=True)
    done = _build(root, tmp_path, pythons=_COMPLETE_PYTHONS)
    assert done.returncode == 0, done.stderr
    app = tmp_path / "VOOL.app" / "VOOL.app"
    assert "OK:" in done.stdout and "[wrapper]" in done.stdout
    assert _plist_value(app, "NULLABundleMode") == "wrapper"
    assert _plist_value(app, "CFBundleIdentifier") == "ai.nulla.desktop.local"
    assert _plist_value(app, "CFBundleURLTypes.0.CFBundleURLSchemes.0") == "vool-local"
    assert _plist_value(app, "NULLASourceSHA") == "nogit"
    assert (app / "Contents" / "MacOS" / "VOOL").stat().st_mode & 0o111


def test_a_completed_build_carries_a_build_manifest_stamping_sha_version_and_time(
    tmp_path: Path,
) -> None:
    """BUILD_MANIFEST.json is the machine-readable provenance the demo gate verifies:
    exact SHA, release version, UTC build time, mode and clean-tree flag. A fixture
    install has no git repo, so the honest sha there is 'nogit' — the manifest must
    still exist, parse, and carry every required field."""
    root = _fixture_install(tmp_path, venv_webview=True)
    done = _build(root, tmp_path, pythons=_COMPLETE_PYTHONS)
    assert done.returncode == 0, done.stderr
    app = tmp_path / "VOOL.app" / "VOOL.app"
    manifest_path = app / "Contents" / "Resources" / "BUILD_MANIFEST.json"
    assert manifest_path.is_file(), "built bundle carries no BUILD_MANIFEST.json"
    m = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert m["schema"] == "vool-build-manifest/1"
    assert m["exact_sha"] == "nogit"  # fixture install builds outside any git repo
    assert m["bundle_mode"] == "wrapper"
    assert m["platform"] == "macos"
    assert isinstance(m["source_tree_clean"], bool)
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", m["build_time_utc"]), m
    assert m["version"] == "" or re.fullmatch(r"[\d.]+(-\w+)?", m["version"]), m


def test_wrapper_build_never_claims_success_without_the_runtime_starter(tmp_path: Path) -> None:
    """The incident, reduced to an assertion: fresh worktree, no gitignored Start_VOOL.sh."""
    root = _fixture_install(tmp_path, with_start=False)
    done = _build(root, tmp_path, pythons=_COMPLETE_PYTHONS)
    assert done.returncode != 0, "the false green: OK printed over a bundle that cannot start its runtime"
    assert "Start_VOOL.sh" in done.stderr
    assert "install_vool.sh" in done.stderr, "the remedy must be named"
    assert "--self-contained" in done.stderr
    assert "OK:" not in done.stdout
    assert "Double-click" not in done.stdout


def test_wrapper_build_dies_when_no_interpreter_can_import_webview(tmp_path: Path) -> None:
    root = _fixture_install(tmp_path, with_start=True)  # starter present, only incapable pythons
    done = _build(root, tmp_path, pythons=_INCAPABLE_PYTHONS)
    assert done.returncode != 0
    assert "import webview" in done.stderr, "the failure must name the capability that was missing"


def test_a_deliberately_broken_required_reference_makes_the_rebuild_red(tmp_path: Path) -> None:
    """GREEN with a complete install -> break the prerequisite -> RED -> restore byte-identically."""
    root = _fixture_install(tmp_path, with_start=True, venv_webview=True)
    done = _build(root, tmp_path, pythons=_COMPLETE_PYTHONS)
    assert done.returncode == 0, done.stderr

    victim = root / "Start_VOOL.sh"
    saved_bytes = victim.read_bytes()
    saved_hash = __import__("hashlib").sha256(saved_bytes).hexdigest()
    try:
        victim.unlink()
        broken = _build(root, tmp_path, pythons=_COMPLETE_PYTHONS)
        assert broken.returncode != 0 and "Start_VOOL.sh" in broken.stderr
    finally:
        victim.write_bytes(saved_bytes)
    assert __import__("hashlib").sha256(victim.read_bytes()).hexdigest() == saved_hash, \
        "restore must be byte-identical"
    healed = _build(root, tmp_path, pythons=_COMPLETE_PYTHONS)
    assert healed.returncode == 0, healed.stderr


# ------------------------------------------------------------------------------------------
# The verify contract, executed verbatim against SYNTHETIC bundles (hermetic self-contained arm)
# ------------------------------------------------------------------------------------------


def _verify_harness(app: Path, project_root: Path, *, self_contained: bool) -> tuple[int, str]:
    """Extract verify_bundle() + its selectors from the real script and run them unchanged."""

    def fn(pattern: str) -> str:
        m = re.search(pattern, BUILD_SCRIPT.read_text(encoding="utf-8"), re.DOTALL | re.MULTILINE)
        assert m, f"cannot locate {pattern} in build script"
        return m.group(0)

    parts = [
        'say() { printf "%s\\n" "$*"; }',
        'die() { printf "ERROR: %s\\n" "$*" >&2; exit 7; }',
        fn(r"^webview_capable\(\)[^\n]*\n"),
        fn(r"^select_capable_wrapper_python\(\) \{\n.*?^\}$"),
        fn(r"^verify_bundle\(\) \{\n.*?^\}$"),
        f'APP="{app}"',
        f'PROJECT_ROOT="{project_root}"',
        f'SELF_CONTAINED={"1" if self_contained else "0"}',
        f'BUNDLE_MODE="{"self-contained" if self_contained else "wrapper"}"',
        # verify_bundle now ends in the architecture/deployment gate, which the real build
        # reaches through these three build-wide values. Supplying them keeps the harness a
        # harness of the real function rather than of a reduced one.
        f'SCRIPT_DIR="{BUILD_SCRIPT.parent}"',
        'TARGET_ARCH="arm64"',
        'MACOS_MIN_VERSION="14.0"',
        "verify_bundle",
        "echo VERIFY-PASSED",
    ]
    done = subprocess.run(["bash", "-c", "\n".join(parts)], capture_output=True, text=True,
                          env={"PATH": BASE_PATH, "HOME": "/tmp"})
    return done.returncode, done.stdout + done.stderr


def _minimal_plist(app: Path) -> None:
    contents = app / "Contents"
    (contents / "MacOS").mkdir(parents=True)
    (contents / "Resources").mkdir(parents=True)
    (contents / "Info.plist").write_text(textwrap.dedent(
        """\
        <?xml version="1.0" encoding="UTF-8"?>
        <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
        <plist version="1.0"><dict>
          <key>CFBundleName</key><string>t</string>
          <key>CFBundleExecutable</key><string>VOOL</string>
          <key>CFBundleIdentifier</key><string>ai.nulla.desktop</string>
          <key>CFBundlePackageType</key><string>APPL</string>
          <key>LSMinimumSystemVersion</key><string>14.0</string>
          <key>LSArchitecturePriority</key><array><string>arm64</string></array>
        </dict></plist>
        """
    ))
    exe = contents / "MacOS" / "VOOL"
    exe.write_text("#!/usr/bin/env bash\n")
    exe.chmod(0o755)


def _staged_self_contained(tmp_path: Path, embedded: dict[str, object]) -> tuple[Path, Path]:
    """A synthetic staged bundle; `embedded` keys control what exists inside Resources/app."""
    app = tmp_path / "fake-self-contained.app"
    _minimal_plist(app)
    res = app / "Contents" / "Resources"
    if embedded.get("window", False):
        win_src = res / "app" / "installer" / "bundle" / "vool_window.py"
        win_src.parent.mkdir(parents=True, exist_ok=True)
        win_src.write_text("# window source stands-in\n")
    if embedded.get("supervisor", False):
        supervisor_src = res / "app" / "installer" / "bundle" / "native_runtime_supervisor.py"
        supervisor_src.parent.mkdir(parents=True, exist_ok=True)
        supervisor_src.write_text("# runtime supervisor stands-in\n")
    if embedded.get("python") is not None:
        bin_dir = res / "python" / "bin"
        bin_dir.mkdir(parents=True, exist_ok=True)
        _py_shim(
            bin_dir,
            "python3",
            webview=bool(embedded["python"]),
            registry=bool(embedded.get("registry", True)),
        )
    return app, tmp_path / "wrapped-install"


@pytest.mark.parametrize(
    ("embedded", "expect_ok", "needle"),
    [
        ({}, False, "missing its embedded python"),
        ({"python": False, "window": True, "supervisor": True}, False, "cannot import webview"),
        ({"python": True}, False, "missing"),  # python OK but no bundled window source
        ({"python": True, "window": True}, False, "native_runtime_supervisor.py"),
        # An embedded runtime that opens a window but cannot build the Command Registry would
        # install fine and then refuse to serve at boot. The build must catch that here.
        (
            {"python": True, "window": True, "supervisor": True, "registry": False},
            False,
            "cannot build the Command Registry",
        ),
        ({"python": True, "window": True, "supervisor": True}, True, "VERIFY-PASSED"),
    ],
)
def test_self_contained_verify_arm_keeps_its_stronger_contract(
    tmp_path: Path, embedded: dict[str, object], expect_ok: bool, needle: str
) -> None:
    app, root = _staged_self_contained(tmp_path, embedded)
    code, out = _verify_harness(app, root, self_contained=True)
    assert needle in out
    assert (code == 0) is expect_ok


def test_the_self_contained_launcher_never_writes_bytecode_into_the_bundle() -> None:
    """A self-contained bundle must not rewrite itself at runtime.

    The app tree is compiled at build time with hash-based .pyc, so runtime bytecode writing
    gains nothing -- and it costs integrity: measured on a real build, driving the app wrote 73
    __pycache__ directories and 1,475 .pyc files into Contents/Resources/app, changing the
    bundle's digest after first launch. Once signed that is a broken seal, and it makes
    "same commit -> same bundle" untrue for anyone verifying a shipped artifact.
    """
    src = BUILD_SCRIPT.read_text(encoding="utf-8")
    # The SELF-CONTAINED launcher only (the quoted heredoc at `<<'LAUNCHER'`). The wrapper
    # launcher drives an external install, where .pyc beside the source is ordinary Python
    # behaviour and touches no bundle.
    start = src.index("cat >\"${APP}/Contents/MacOS/VOOL\" <<'LAUNCHER'")
    end = src.index("cat >\"${APP}/Contents/MacOS/VOOL\" <<LAUNCHER", start)
    launcher = src[start:end]
    assert 'export VOOL_RUNTIME_MODE="self-contained"' in launcher, "wrong heredoc extracted"
    assert "export PYTHONDONTWRITEBYTECODE=1" in launcher, (
        "the self-contained launcher lost its bytecode guard; the bundle will mutate itself "
        "on first run and its digest will no longer match the one that was built"
    )


def test_info_plist_declares_the_tcc_purpose_strings_and_a_minimum_system() -> None:
    """macOS terminates a process that reaches a TCC-gated API with no usage description.

    The voice lane reaches the microphone and speech recognition, so their absence is a crash
    on first use rather than a prompt. LSMinimumSystemVersion is what stops the bundle from
    launching on a macOS too old for the APIs it links.
    """
    src = BUILD_SCRIPT.read_text(encoding="utf-8")
    for key in (
        "NSMicrophoneUsageDescription",
        "NSSpeechRecognitionUsageDescription",
        "LSMinimumSystemVersion",
    ):
        assert f"<key>{key}</key>" in src, f"Info.plist lost {key}"


def test_the_build_emits_a_dependency_provenance_manifest() -> None:
    """Every bundle records how its dependencies were vouched for, and a release build refuses
    anything not graded RELEASE against uv.lock."""
    src = BUILD_SCRIPT.read_text(encoding="utf-8")
    assert "ops.bundle_dependency_provenance" in src
    assert "config/dependency-provenance.json" in src
    assert "VOOL_REQUIRE_RELEASE_PROVENANCE" in src


def test_the_build_never_expands_a_possibly_empty_array_under_set_u() -> None:
    """macOS ships bash 3.2, where `"${arr[@]}"` on an EMPTY array trips `set -u` as an unbound
    variable and kills the build mid-stage. Measured: a `prov_args=()` that stayed empty unless
    a release flag was set died with `prov_args[@]: unbound variable` after the bundle version
    had already been stamped.

    Every array expansion in this script must therefore be reachable only when the array is
    non-empty, or be written with the `${arr[@]+...}` guard.
    """
    import re

    src = BUILD_SCRIPT.read_text(encoding="utf-8")
    assert "set -u" in src or "set -euo pipefail" in src, "this pin assumes set -u"
    expansions = set(re.findall(r'\$\{([A-Za-z_][A-Za-z0-9_]*)\[@\]\}', src))
    for name in sorted(expansions):
        # The array must be assigned non-empty on every path that reaches its expansion.
        # wheelhouse_args is only expanded inside the branch that fills it.
        assert name in {"wheelhouse_args", "missing_pkgs", "SRC_PACKAGES"}, (
            f"unreviewed array expansion ${{{name}[@]}} -- prove it can never be empty under set -u"
        )


def test_the_lean_dependency_list_carries_what_the_served_surface_imports() -> None:
    """zstandard is imported by the Command Registry's logs group through core.liquefy.

    It sat in requirements.txt but only in an OPTIONAL extra of pyproject.toml, so the bundle's
    hand-mirrored lean list legitimately lacked it while the served command door hard-required
    it at import time. The behavioural guard is the conformance check in verify_bundle(); this
    pin names the specific distribution whose absence caused the observed HTTP 500.
    """
    src = BUILD_SCRIPT.read_text(encoding="utf-8")
    # Anchor on the real command, not the first textual match: the word also appears in a
    # comment above it, and a `|| die` inside an intervening guard would truncate a naive slice.
    marker = 'uv pip install --python "${embedded}"'
    assert marker in src, "the lean dependency install command moved or was renamed"
    install = src.split(marker, 1)[1].split("lean dependency install failed", 1)[0]
    assert "zstandard" in install, "the lean dependency install lost zstandard"


def test_both_modes_are_declared_in_info_plist_and_none_is_implicit() -> None:
    src = BUILD_SCRIPT.read_text(encoding="utf-8")
    assert "<key>NULLABundleMode</key>" in src
    assert "<string>${BUNDLE_MODE}</string>" in src
    assert 'BUNDLE_MODE="wrapper"' in src and 'BUNDLE_MODE="self-contained"' in src
    assert 'BUNDLE_IDENTIFIER="ai.nulla.desktop.local"' in src
    assert 'BUNDLE_IDENTIFIER="ai.nulla.desktop"' in src
    assert "<key>NULLASourceSHA</key>" in src


# ------------------------------------------------------------------------------------------
# The EMITTED launcher's interpreter resolver: capability beats version
# ------------------------------------------------------------------------------------------


def _resolver_fragment(launcher_text: str) -> str:
    match = re.search(
        r'(py_ok\(\) \{.*?\nfi)\n\n# The long-lived native host owns', launcher_text, re.DOTALL
    )
    assert match, "emitted wrapper launcher lost its capability resolver"
    return match.group(1)


@pytest.fixture(scope="module")
def built_wrapper_launcher(tmp_path_factory) -> str:
    """The actual Contents/MacOS/VOOL text from a produced wrapper bundle, not the source heredoc."""
    tmp = tmp_path_factory.mktemp("artifact-build")
    root = _fixture_install(tmp, with_start=True, venv_webview=True)
    done = _build(root, tmp, pythons=_COMPLETE_PYTHONS)
    assert done.returncode == 0, done.stderr
    return (tmp / "VOOL.app" / "VOOL.app" / "Contents" / "MacOS" / "VOOL").read_text(encoding="utf-8")


def test_resolver_rejects_a_higher_version_that_cannot_import_webview(
    built_wrapper_launcher: str, tmp_path: Path
) -> None:
    """The measured live-machine failure: capable 3.9-like python3 sits below version-blind 3.12."""
    # Build the mixed scenario explicitly: everything incumbent is incapable except bare python3.
    frag_dir = tmp_path / "frag"
    for name in ("python3.13", "python3.12", "python3.11", "python3.10"):
        _py_shim(frag_dir, name, webview=False)
    _py_shim(frag_dir, "python3", webview=True)  # the low-version capable candidate
    env = {"PATH": f"{frag_dir}:{BASE_PATH}", "HOME": str(tmp_path),
           "PROJECT_ROOT": str(tmp_path / "no-venv")}
    done = subprocess.run(
        ["bash", "-c", _resolver_fragment(built_wrapper_launcher) + '\nprintf "PICKED=%s" "${PY:-UNSET}"'],
        capture_output=True, text=True, env=env, timeout=60,
    )
    assert f"PICKED={frag_dir}/python3" == done.stdout, (
        "version-first selection is back: the resolver picked a webview-less newer python"
    )


def test_resolver_prefers_the_install_venv_when_it_is_capable(
    built_wrapper_launcher: str, tmp_path: Path
) -> None:
    venv_root = tmp_path / "venvy-root"
    venv_bin = venv_root / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    _py_shim(venv_bin, "python", webview=True)
    env = {"PATH": BASE_PATH, "HOME": str(tmp_path), "PROJECT_ROOT": str(venv_root)}
    done = subprocess.run(
        ["bash", "-c", _resolver_fragment(built_wrapper_launcher) + '\nprintf "PICKED=%s" "${PY:-UNSET}"'],
        capture_output=True, text=True, env=env, timeout=60,
    )
    assert done.stdout == f"PICKED={venv_bin}/python"


def test_resolver_fails_honestly_when_nothing_can_open_a_native_window(
    built_wrapper_launcher: str, tmp_path: Path
) -> None:
    frag_dir = tmp_path / "frag"
    frag_dir.mkdir()
    _install_pythons(frag_dir, _INCAPABLE_PYTHONS)
    env = {"PATH": f"{frag_dir}:{BASE_PATH}", "HOME": str(tmp_path),
           "PROJECT_ROOT": str(tmp_path / "empty")}
    (tmp_path / "empty").mkdir(exist_ok=True)
    done = subprocess.run(
        ["bash", "-c", _resolver_fragment(built_wrapper_launcher) + '; printf "PICKED=%s" "${PY:-UNSET}"'],
        capture_output=True, text=True, env=env, timeout=60,
    )
    assert done.returncode != 0, "an incapable-only host must not yield a runnable-looking launch"
    # The launcher runs with stdout+stderr redirected into app.log in real launches, so its ERROR
    # lines are plain `echo` — assert against the combined stream here.
    combined = done.stdout + done.stderr
    assert "no interpreter that can 'import webview'" in combined
    assert "ERROR:" in combined


def test_resolver_degraded_dev_optin_still_selects_something(
    built_wrapper_launcher: str, tmp_path: Path
) -> None:
    frag_dir = tmp_path / "frag"
    frag_dir.mkdir()
    _install_pythons(frag_dir, _INCAPABLE_PYTHONS)
    env = {"PATH": f"{frag_dir}:{BASE_PATH}", "HOME": str(tmp_path),
           "PROJECT_ROOT": str(tmp_path / "empty"), "VOOL_ALLOW_BROWSER_FALLBACK": "1"}
    (tmp_path / "empty").mkdir(exist_ok=True)
    done = subprocess.run(
        ["bash", "-c", _resolver_fragment(built_wrapper_launcher) + '; printf "PICKED=%s" "${PY:-UNSET}"'],
        capture_output=True, text=True, env=env, timeout=60,
    )
    assert done.returncode == 0
    assert "WARNING: no interpreter with pywebview found" in done.stdout + done.stderr
    assert "PICKED=" in done.stdout and "UNSET" not in done.stdout


# ------------------------------------------------------------------------------------------
# The EMITTED launcher delegates lifecycle to the long-lived native host
# ------------------------------------------------------------------------------------------


def test_shell_launcher_has_no_detached_api_or_readiness_poll(built_wrapper_launcher: str) -> None:
    assert "nohup" not in built_wrapper_launcher
    assert "is_healthy" not in built_wrapper_launcher
    assert "seq 1 90" not in built_wrapper_launcher
    assert 'VOOL_NATIVE_REQUIRE_OWNED_RUNTIME="1"' in built_wrapper_launcher
    assert 'exec "${PY}" "${PROJECT_ROOT}/installer/bundle/vool_window.py"' in built_wrapper_launcher


# ------------------------------------------------------------------------------------------
# vool_window: packaged native launches fail CLOSED; browsers require explicit opt-in
# ------------------------------------------------------------------------------------------


_WINDOW_DRIVER = textwrap.dedent(
    """
    import importlib.util, sys, os
    spec = importlib.util.spec_from_file_location("nw", "__WINDOW_SCRIPT__")
    nw = importlib.util.module_from_spec(spec); spec.loader.exec_module(nw)
    import installer.bundle.native_runtime_supervisor as nrs
    class FakeSupervisor:
        owns_runtime = True
        def ensure_ready(self): return {"commit_full": "a" * 40, "pid": 42}
        def shutdown(self): pass
    nrs.NativeRuntimeSupervisor.from_environment = classmethod(lambda cls, **kwargs: FakeSupervisor())
    launched = []
    class FakePopen:
        def __init__(self, argv, *a, **k): launched.append(list(argv))
    nw.subprocess.Popen = FakePopen              # NO real browser is ever launched here
    nw._wait_for_server = lambda timeout_s=None: False
    if sys.argv[1] == "force-webview-failure":
        sys.modules["webview"] = None            # the real ImportError branch, deterministic
    rc = nw.main()
    print("RC=%d" % rc)
    print("LAUNCHED=%r" % (launched,))
    """
)


def _drive_window(tmp_path: Path, mode: str, extra_env: dict[str, str]) -> tuple[dict, str]:
    home = tmp_path / ("home-" + mode.replace("/", "_"))
    home.mkdir(exist_ok=True)
    driver = (tmp_path / ("driver_" + mode.replace("/", "_") + ".py"))
    driver.write_text(_WINDOW_DRIVER.replace("__WINDOW_SCRIPT__", str(WINDOW_SCRIPT)))
    env = {"PATH": BASE_PATH, "HOME": str(home), **extra_env}
    done = subprocess.run([sys.executable, str(driver), mode], capture_output=True,
                          text=True, env=env, timeout=120)
    assert done.returncode in (0, 1)
    log_file = home / "Library" / "Application Support" / "VOOL" / "open.log"
    log = log_file.read_text() if log_file.exists() else ""
    fields = dict(pair.split("=", 1) for pair in done.stdout.splitlines() if "=" in pair)
    return fields, log


def test_missing_pywebview_on_packaged_native_launch_fails_closed(tmp_path: Path) -> None:
    fields, log = _drive_window(tmp_path, "force-webview-failure", {})
    assert fields["RC"] == "1", "a clean exit 0 made the Chrome fallback look like native success"
    assert fields["LAUNCHED"] == "[]", "no browser may be substituted"
    assert "refusing silent browser fallback" in log
    assert "opened via Google Chrome" not in log


def test_browser_fallback_returns_only_behind_the_explicit_flag(tmp_path: Path) -> None:
    fields, log = _drive_window(tmp_path, "force-webview-failure",
                                {"VOOL_ALLOW_BROWSER_FALLBACK": "1"})
    assert fields["RC"] == "0"
    assert "--app=" in fields["LAUNCHED"], "dev workflow preserved behind the opt-in"
    assert "fallback" in log


def test_native_start_failure_also_fails_closed(tmp_path: Path) -> None:
    """webview.start() blowing up must not fall through to a browser either."""
    home = tmp_path / "home-startfail"
    home.mkdir()
    driver = tmp_path / "driver_startfail.py"
    boom = textwrap.dedent(
        f"""
        import importlib.util, sys, os
        spec = importlib.util.spec_from_file_location("nw", {str(WINDOW_SCRIPT)!r})
        nw = importlib.util.module_from_spec(spec); spec.loader.exec_module(nw)
        import installer.bundle.native_runtime_supervisor as nrs
        class FakeSupervisor:
            owns_runtime = True
            def ensure_ready(self): return {{"commit_full": "a" * 40, "pid": 42}}
            def shutdown(self): pass
        nrs.NativeRuntimeSupervisor.from_environment = classmethod(lambda cls, **kwargs: FakeSupervisor())
        launched = []
        class FakePopen:
            def __init__(self, argv, *a, **k): launched.append(list(argv))
        nw.subprocess.Popen = FakePopen
        nw._wait_for_server = lambda timeout_s=None: True
        import types
        fake = types.ModuleType("webview")
        fake.create_window = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("webview exploded"))
        fake.start = lambda *a, **k: None
        sys.modules["webview"] = fake
        rc = nw.main()
        print("RC=%d" % rc)
        print("LAUNCHED=%r" % (launched,))
        """
    )
    driver.write_text(boom)
    done = subprocess.run([sys.executable, str(driver)], capture_output=True, text=True,
                          env={"PATH": BASE_PATH, "HOME": str(home)}, timeout=120)
    log_file = home / "Library" / "Application Support" / "VOOL" / "open.log"
    log = log_file.read_text() if log_file.exists() else ""
    assert "RC=1" in done.stdout
    assert "LAUNCHED=[]" in done.stdout
    assert "refusing silent browser fallback" in log


def test_windows_edge_lane_is_untouched_by_the_posix_fail_closed_gate(tmp_path: Path) -> None:
    """WebView2-absent Edge fallback remains the documented Windows contract."""
    home = tmp_path / "home-win32"
    home.mkdir()
    driver = tmp_path / "driver_win32.py"
    driver.write_text(textwrap.dedent(
        f"""
        import importlib.util, sys, os
        spec = importlib.util.spec_from_file_location("nw", {str(WINDOW_SCRIPT)!r})
        nw = importlib.util.module_from_spec(spec); spec.loader.exec_module(nw)
        import installer.bundle.native_runtime_supervisor as nrs
        class FakeSupervisor:
            owns_runtime = True
            def ensure_ready(self): return {{"commit_full": "a" * 40, "pid": 42}}
            def shutdown(self): pass
        nrs.NativeRuntimeSupervisor.from_environment = classmethod(lambda cls, **kwargs: FakeSupervisor())
        nw.sys.platform = "win32"
        nw._has_webview2 = lambda: False
        nw._wait_for_server = lambda timeout_s=None: False
        launched = []
        class FakePopen:
            def __init__(self, argv, *a, **k): launched.append(list(argv))
        nw.subprocess.Popen = FakePopen
        rc = nw.main()
        print("RC=%d" % rc)
        print("LAUNCHED=%r" % (launched,))
        """
    ))
    done = subprocess.run([sys.executable, str(driver)], capture_output=True, text=True,
                          env={"PATH": BASE_PATH, "HOME": str(home)}, timeout=120)
    assert "RC=0" in done.stdout
    assert "powershell" in done.stdout and "vool-open.ps1" in done.stdout


# ------------------------------------------------------------------------------------------
# Mode distinctness + launcher shell validity (guards both heredocs after the surgery)
# ------------------------------------------------------------------------------------------


def test_both_emitted_launchers_remain_valid_shell_and_distinct_contracts() -> None:
    bodies = _launchers()
    assert bodies["self_contained"] != bodies["wrapper"]
    for name, body in bodies.items():
        with __import__("tempfile").NamedTemporaryFile("w", suffix=".sh", delete=False) as fh:
            fh.write(body)
            p = fh.name
        checked = subprocess.run(["bash", "-n", p], capture_output=True, text=True)
        assert checked.returncode == 0, f"{name}: {checked.stderr}"
    assert 'VOOL_RUNTIME_MODE="self-contained"' in bodies["self_contained"]
    assert "Start_VOOL.sh" not in bodies["self_contained"]
    assert "Start_VOOL.sh" not in bodies["wrapper"]
    assert 'VOOL_RUNTIME_MODE="wrapper"' in bodies["wrapper"]
    assert 'VOOL_NATIVE_REQUIRE_OWNED_RUNTIME="1"' in bodies["self_contained"]
    assert 'VOOL_NATIVE_REQUIRE_OWNED_RUNTIME="1"' in bodies["wrapper"]
    # The API origin is a RUN-TIME default, not a baked constant: a launch that names another origin
    # (`open --env VOOL_NATIVE_API_URL=http://127.0.0.1:11535`, an acceptance instance beside a live
    # window) must keep it, and a plain double-click must still get 11435. Measured 2026-09-06: the
    # baked export overwrote the passed value and the second instance came up on 11435.
    for name, body in bodies.items():
        assert 'VOOL_NATIVE_API_URL="${VOOL_NATIVE_API_URL:-http://127.0.0.1:11435}"' in body, name
        assert 'VOOL_NATIVE_API_URL="http://127.0.0.1:11435"' not in body, name


# ------------------------------------------------------------------------------------------
# Build provenance: a dirty tree is refused unless the developer override marks the artifact
# NON-RELEASE. The 2026-09-02 demo shipped source_tree_clean:false — the flag was recorded but
# nothing ever acted on it, so uncommitted source rode into a "release" bundle.
# ------------------------------------------------------------------------------------------

_GIT_IDENTITY = ("-c", "user.email=fixture@local", "-c", "user.name=fixture")


def _git(root: Path, home: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True,
        env={"PATH": BASE_PATH, "HOME": str(home)}, timeout=60,
    )
    assert done.returncode == 0, f"git {' '.join(args)} failed: {done.stderr}"
    return done.stdout.strip()


def _tracked_fixture_install(tmp_path: Path) -> tuple[Path, str]:
    """A fixture install whose every file is committed; returns (root, head_sha)."""
    root = _fixture_install(tmp_path, venv_webview=True)
    _git(root, tmp_path, "init", "-q")
    _git(root, tmp_path, *_GIT_IDENTITY, "add", "-A")
    _git(root, tmp_path, *_GIT_IDENTITY, "commit", "-q", "-m", "fixture")
    return root, _git(root, tmp_path, "rev-parse", "HEAD")


def _manifest(app: Path) -> dict:
    return json.loads(
        (app / "Contents" / "Resources" / "BUILD_MANIFEST.json").read_text(encoding="utf-8")
    )


def test_dirty_tracked_tree_is_refused_without_explicit_override(tmp_path: Path) -> None:
    root, _sha = _tracked_fixture_install(tmp_path)
    channel = root / "config" / "release" / "update_channel.json"
    channel.write_text('{"release_version": "9.9.9-dirty"}')
    done = _build(root, tmp_path, pythons=_COMPLETE_PYTHONS)
    assert done.returncode != 0, "a dirty tree must not produce an app bundle"
    assert "dirty" in done.stderr.lower()
    assert "VOOL_ALLOW_DIRTY_BUILD" in done.stderr, "the override must be named"
    assert "OK:" not in done.stdout
    assert not (tmp_path / "VOOL.app" / "VOOL.app").exists(), "no partial bundle may be left behind"


def test_uncommitted_new_files_are_dirty_too(tmp_path: Path) -> None:
    root, _sha = _tracked_fixture_install(tmp_path)
    (root / "UNCOMMITTED_SOURCE.py").write_text("X = 1\n")
    done = _build(root, tmp_path, pythons=_COMPLETE_PYTHONS)
    assert done.returncode != 0, "untracked uncommitted source must count as dirty"
    assert "VOOL_ALLOW_DIRTY_BUILD" in done.stderr


def test_override_build_proceeds_but_marks_the_artifact_non_release(tmp_path: Path) -> None:
    root, _sha = _tracked_fixture_install(tmp_path)
    channel = root / "config" / "release" / "update_channel.json"
    channel.write_text('{"release_version": "9.9.9-dirty"}')
    done = _build(root, tmp_path, pythons=_COMPLETE_PYTHONS,
                  extra_env={"VOOL_ALLOW_DIRTY_BUILD": "1"})
    assert done.returncode == 0, done.stderr
    m = _manifest(tmp_path / "VOOL.app" / "VOOL.app")
    assert m["source_tree_clean"] is False
    assert m["release"] is False, "the override must stamp the artifact non-release"
    assert "non-release" in done.stdout.lower()


def test_clean_tree_build_stamps_exact_sha_version_and_release(tmp_path: Path) -> None:
    root, sha = _tracked_fixture_install(tmp_path)
    # Venv-only shims: the wrapper capability probe uses the .venv interpreter, leaving `python3`
    # to resolve to the real system interpreter — whose JSON parse must populate the manifest's
    # version from the SAME update_channel.json the fixture committed.
    done = _build(root, tmp_path, pythons={"python3.13": True})
    assert done.returncode == 0, done.stderr
    app = tmp_path / "VOOL.app" / "VOOL.app"
    m = _manifest(app)
    assert m["exact_sha"] == sha
    assert m["source_tree_clean"] is True
    assert m["release"] is True
    assert m["version"] == "9.9.9-repair"
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", m["build_time_utc"]), m


def test_release_staging_bytes_come_from_the_commit_not_the_worktree(tmp_path: Path) -> None:
    """Structural pin: a clean release build stages its source with `git archive <sha>`, so bundle
    bytes are exactly the commit's bytes even if the working tree changes mid-build."""
    src = BUILD_SCRIPT.read_text(encoding="utf-8")
    assert "git archive" in src, "release staging must extract the commit, not copy the worktree"
    assert "VOOL_ALLOW_DIRTY_BUILD" in src
    assert "NON-RELEASE" in src.upper()


def test_the_staged_source_carries_the_native_skill_library_and_bundled_plugins(tmp_path: Path) -> None:
    """`skills/` and `plugins/` are runtime CONTENT, and SRC_PACKAGES decides what ships.

    core.native_skill_library resolves the native library from the APPLICATION SOURCE ROOT's
    skills/ directory and core.plugin_catalog reads the bundled packs from the same root's
    plugins/ -- a bundle that stages neither ships a runtime whose library and catalog are
    silently empty (measured on the e1034c9b Mac bundle: neither directory present, while the
    commit carried 26 native skills and the first-party vool-database pack).
    """
    import re

    src = BUILD_SCRIPT.read_text(encoding="utf-8")
    match = re.search(r"^SRC_PACKAGES=\(([^)]*)\)", src, re.MULTILINE)
    assert match, "SRC_PACKAGES declaration not found in the build script"
    staged = set(match.group(1).split())
    assert "skills" in staged, "the native skill library no longer ships inside the app"
    assert "plugins" in staged, "the bundled first-party packs no longer ship inside the app"
    # What ships must exist in the repo at this commit: the build's own pre-package check
    # enforces it for every name, so a stale name here fails the BUILD, not the first launch.
    for package in staged:
        assert (REPO / package).is_dir(), f"SRC_PACKAGES names {package}, absent from the repo"


def test_the_windows_staging_list_carries_the_same_content_trees(tmp_path: Path) -> None:
    """build_bundle.ps1 stages the same runtime content trees, and a listed package that is
    missing from the repo fails the build instead of silently shipping without it."""
    ps1 = REPO / "installer" / "bundle" / "build_bundle.ps1"
    src = ps1.read_text(encoding="utf-8")
    for name in ('"skills"', '"plugins"'):
        assert name in src, f"{name} missing from the Windows staging list"
    assert '"channels"' not in src, "the deleted channels package is still listed (stale staging list)"


def test_build_manifest_and_runtime_stamp_share_one_build_identity(tmp_path: Path) -> None:
    """Invariant: /healthz and BUILD_MANIFEST agree byte-for-byte on SHA/build identity. The
    bundled config/build-source.json is the runtime's SHA source, so the build must verify the
    stamp it writes equals the manifest's exact_sha and version."""
    src = BUILD_SCRIPT.read_text(encoding="utf-8")
    assert "commit_full" in src, "the build must cross-check the bundled build-source stamp"
    assert "release_version" in src or "RELEASE_VERSION" in src


# ------------------------------------------------------------------------------------------
# Deterministic rebuilds: compare_app_bundles.py separates the one KNOWN-VARIABLE field
# (build_time_utc) from real byte drift, so two builds of one SHA can be judged mechanically.
# ------------------------------------------------------------------------------------------

COMPARE_TOOL = REPO / "installer" / "bundle" / "compare_app_bundles.py"


def _synthetic_bundle(root: Path, *, build_time: str, launcher: str) -> Path:
    (root / "Contents" / "Resources").mkdir(parents=True)
    (root / "Contents" / "MacOS").mkdir(parents=True)
    (root / "Contents" / "MacOS" / "VOOL").write_text(launcher, encoding="utf-8")
    (root / "Contents" / "Resources" / "BUILD_MANIFEST.json").write_text(json.dumps({
        "schema": "vool-build-manifest/1",
        "exact_sha": "a" * 40,
        "build_time_utc": build_time,
    }), encoding="utf-8")
    return root


def _compare(tmp_path: Path, a: Path, b: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(COMPARE_TOOL), str(a), str(b)],
                          capture_output=True, text=True, timeout=120)


def test_two_builds_differing_only_in_build_time_compare_clean(tmp_path: Path) -> None:
    a = _synthetic_bundle(tmp_path / "A", build_time="2026-09-02T00:00:00Z", launcher="#!/bin/sh\n")
    b = _synthetic_bundle(tmp_path / "B", build_time="2026-09-02T00:00:07Z", launcher="#!/bin/sh\n")
    done = _compare(tmp_path, a, b)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "build_time_utc" in done.stdout, "the known-variable difference must be named, not hidden"


def test_real_byte_drift_between_rebuilds_fails_the_compare(tmp_path: Path) -> None:
    a = _synthetic_bundle(tmp_path / "A", build_time="2026-09-02T00:00:00Z", launcher="#!/bin/sh\n")
    b = _synthetic_bundle(tmp_path / "B", build_time="2026-09-02T00:00:07Z",
                          launcher="#!/bin/sh\n# drifted\n")
    done = _compare(tmp_path, a, b)
    assert done.returncode != 0, "undeterministic bundle bytes must not compare clean"
    assert "Contents/MacOS/VOOL" in done.stdout
    assert "NONDETERMINISTIC" in done.stdout.upper()


def test_bundled_build_source_stamp_built_at_is_known_variable_too(tmp_path: Path) -> None:
    """The bundled config/build-source.json carries an informational built_at timestamp — the
    same known-variable class as the manifest's build_time_utc. Its IDENTITY fields must match."""
    a = _synthetic_bundle(tmp_path / "A", build_time="2026-09-02T00:00:00Z", launcher="#!/bin/sh\n")
    b = _synthetic_bundle(tmp_path / "B", build_time="2026-09-02T00:00:07Z", launcher="#!/bin/sh\n")
    for root, stamp_time in ((a, "2026-09-02T00:00:01Z"), (b, "2026-09-02T00:00:09Z")):
        stamp = root / "Contents" / "Resources" / "app" / "config" / "build-source.json"
        stamp.parent.mkdir(parents=True, exist_ok=True)
        stamp.write_text(json.dumps({
            "source_kind": "git", "commit": "a" * 12, "commit_full": "a" * 40,
            "dirty_state": False, "built_at": stamp_time,
        }), encoding="utf-8")
    done = _compare(tmp_path, a, b)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "build-source.json" in done.stdout
    # identity drift in the same file is NOT known-variable
    stamp = b / "Contents" / "Resources" / "app" / "config" / "build-source.json"
    stamp.write_text(json.dumps({
        "source_kind": "git", "commit": "b" * 12, "commit_full": "b" * 40,
        "dirty_state": False, "built_at": "2026-09-02T00:00:09Z",
    }), encoding="utf-8")
    drifted = _compare(tmp_path, a, b)
    assert drifted.returncode != 0
    assert "build-source.json" in drifted.stdout


# --- the wallet's EVM dependencies ride IN the artifact (crypto lane, 2026-09-06) ---------------------
# core/wallet/evm.py resolves eth-abi/eth-utils/eth-account AT USE and answers a typed
# wallet_dependency_unavailable refusal when any is missing: in an artifact without them every
# Base/Ethereum journey is a refusal. The lean install list must carry them, and the refusal
# must stay the truthful runtime answer when a dependency genuinely goes missing.

def test_the_lean_bundle_install_list_ships_the_evm_signing_dependencies() -> None:
    script = BUILD_SCRIPT.read_text(encoding="utf-8")
    lean = script.split("lean dependency install failed", 1)[0].rsplit("uv pip install", 1)[-1]
    for package in ("eth-abi", "eth-utils", "eth-account"):
        assert re.search(rf'["\s]{package}>=', lean), f"the bundle's lean install dropped {package}: the artifact's EVM lanes would be dependency-unavailable"


def test_a_missing_evm_dependency_still_answers_the_typed_refusal_not_a_crash(monkeypatch, tmp_path: Path) -> None:
    """Declared-but-absent must be a truthful capability state: with the import machinery
    reporting the packages missing, every EVM entry point refuses typed (L0/L1 proof; the
    artifact-level import check runs in the L4 packaged-app proof)."""
    import importlib.util
    import sys

    from core.wallet import evm
    from core.wallet.errors import WalletFault

    real = importlib.util.find_spec

    def hidden(name: str, *args, **kwargs):
        if name in {"eth_abi", "eth_utils", "eth_account"}:
            return None
        return real(name, *args, **kwargs)

    monkeypatch.setattr(importlib.util, "find_spec", hidden)
    monkeypatch.setattr(sys, "modules", {k: v for k, v in sys.modules.items() if not k.startswith(("eth_abi", "eth_utils", "eth_account"))})
    with pytest.raises(WalletFault) as exc:
        evm.require_evm_dependencies("packaging-test")
    assert exc.value.code == "wallet_dependency_unavailable"
