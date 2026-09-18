"""The architecture / deployment gate, proven against REAL Mach-O fixtures it compiles itself.

Why a whole family for this: the 0.5.0 macOS bundles passed every packaging test in the suite
while macOS reported an Intel-based component on the operator's Mac. Nothing here was reachable
by a slice census alone -- the shipped bundle's 96 Mach-O files every one carried arm64. The
Intel part was the LAUNCH CONTRACT: CFBundleExecutable names a shell script, so LaunchServices
has no Mach-O header to read, FORGES ``LSArchitecturePriority`` with x86_64 first, and the app
launches translated. So the first test below is the regression test for a plist KEY, not a file.

Every fixture binary is compiled here with clang. Nothing in these tests reads, mutates or
strips a delivered artifact: a gate proven by editing the user's shipped bundle would be a gate
that had to damage a release to say anything.
"""

from __future__ import annotations

import os
import plistlib
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
BUNDLE_DIR = REPO / "installer" / "bundle"
sys.path.insert(0, str(BUNDLE_DIR))

from macos_arch_census import census, parse_macho
from macos_arch_gate import check

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="macOS bundle contracts")

CLANG = Path("/usr/bin/clang")
requires_clang = pytest.mark.skipif(not CLANG.exists(), reason="needs /usr/bin/clang for Mach-O fixtures")


def _compile(dest: Path, arches: list[str], min_macos: str = "12.0") -> Path:
    """A real Mach-O at *dest* with exactly *arches* and an explicit deployment target."""
    src = dest.parent / f"{dest.name}.c"
    src.write_text("int main(void){return 0;}\n", encoding="utf-8")
    cmd = [str(CLANG)]
    for a in arches:
        cmd += ["-arch", a]
    cmd += [f"-mmacosx-version-min={min_macos}", str(src), "-o", str(dest)]
    done = subprocess.run(cmd, capture_output=True, timeout=180)
    if done.returncode != 0:
        pytest.skip(f"toolchain cannot build {arches} @ {min_macos}: {done.stderr.decode()[:200]}")
    return dest


def _bundle(root: Path, *, arch_priority: list[str] | None, min_os: str = "12.0",
            macho_main: bool = False) -> Path:
    """A minimal .app shaped like the real one: script main executable unless asked otherwise."""
    app = root / "VOOL.app"
    (app / "Contents" / "MacOS").mkdir(parents=True, exist_ok=True)
    (app / "Contents" / "Resources").mkdir(parents=True, exist_ok=True)
    main = app / "Contents" / "MacOS" / "VOOL"
    if macho_main:
        _compile(main, ["arm64"], min_os)
    else:
        main.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        main.chmod(0o755)
    info: dict = {
        "CFBundleExecutable": "VOOL",
        "CFBundleIdentifier": "ai.vool.desktop.test",
        "CFBundlePackageType": "APPL",
        "LSMinimumSystemVersion": min_os,
    }
    if arch_priority is not None:
        info["LSArchitecturePriority"] = arch_priority
    with (app / "Contents" / "Info.plist").open("wb") as fh:
        plistlib.dump(info, fh)
    return app


# ------------------------------------------------------------------------------------------
# The launch contract -- the actual root cause of the operator's Intel warning
# ------------------------------------------------------------------------------------------

def test_a_script_main_executable_without_an_architecture_priority_is_refused(tmp_path: Path) -> None:
    """The shipped defect. With no LSArchitecturePriority, LaunchServices forges x86_64-first.

    Measured on macOS 26.5.1 / M4: two bundles differing only in this key launched
    proc_translated=1 and proc_translated=0 respectively.
    """
    app = _bundle(tmp_path, arch_priority=None)
    fails, report = check(app, "arm64", "12.0")
    assert not report["passed"]
    contract = [f for f in fails if f.startswith("launch-contract:")]
    assert contract, fails
    assert "Info.plist" in contract[0]
    assert "Rosetta" in contract[0]


def test_declaring_the_target_architecture_satisfies_the_launch_contract(tmp_path: Path) -> None:
    app = _bundle(tmp_path, arch_priority=["arm64"])
    fails, report = check(app, "arm64", "12.0")
    assert report["passed"], fails


def test_declaring_the_wrong_architecture_is_refused(tmp_path: Path) -> None:
    """An arm64 artifact that advertises x86_64 first would still translate."""
    app = _bundle(tmp_path, arch_priority=["x86_64", "arm64"])
    fails, _ = check(app, "arm64", "12.0")
    assert any(f.startswith("launch-contract:") for f in fails), fails


def test_a_macho_main_executable_needs_no_priority_key(tmp_path: Path) -> None:
    """The key exists to replace a header LaunchServices cannot read; with a real Mach-O it can."""
    app = _bundle(tmp_path, arch_priority=None, macho_main=True)
    fails, report = check(app, "arm64", "12.0")
    assert report["passed"], fails


# ------------------------------------------------------------------------------------------
# Slice sabotage: a controlled mismatched binary must fail, naming its exact path
# ------------------------------------------------------------------------------------------

@requires_clang
def test_an_intel_only_dependency_fails_the_gate_naming_its_exact_path(tmp_path: Path) -> None:
    app = _bundle(tmp_path, arch_priority=["arm64"])
    saboteur = app / "Contents" / "Resources" / "libsabotage.dylib"
    _compile(saboteur, ["x86_64"])

    fails, report = check(app, "arm64", "12.0")

    assert not report["passed"], "an x86_64-only dependency must not pass an arm64 gate"
    slice_fails = [f for f in fails if f.startswith("slice:")]
    assert len(slice_fails) == 1, fails
    assert str(saboteur) in slice_fails[0], slice_fails
    assert "x86_64" in slice_fails[0]


@requires_clang
def test_a_universal_dependency_carrying_the_target_slice_passes(tmp_path: Path) -> None:
    """An x86_64 slice ALONGSIDE arm64 is not a defect -- ollama ships exactly this shape."""
    app = _bundle(tmp_path, arch_priority=["arm64"])
    universal = app / "Contents" / "Resources" / "libuniversal.dylib"
    _compile(universal, ["arm64", "x86_64"])

    fails, report = check(app, "arm64", "12.0")

    assert report["passed"], fails
    assert set(parse_macho(universal).archs) == {"arm64", "x86_64"}


@requires_clang
def test_the_same_fixture_flips_the_verdict_when_only_the_slice_changes(tmp_path: Path) -> None:
    """Controls for the sabotage: same path, same name, same gate -- only the slice differs."""
    good = _bundle(tmp_path / "good", arch_priority=["arm64"])
    bad = _bundle(tmp_path / "bad", arch_priority=["arm64"])
    _compile(good / "Contents" / "Resources" / "dep.dylib", ["arm64"])
    _compile(bad / "Contents" / "Resources" / "dep.dylib", ["x86_64"])

    assert check(good, "arm64", "12.0")[1]["passed"]
    assert not check(bad, "arm64", "12.0")[1]["passed"]


@requires_clang
def test_an_arm64_gate_and_an_x86_64_gate_disagree_about_the_same_bundle(tmp_path: Path) -> None:
    """The gate reads its target from the caller, not from the host it happens to run on."""
    app = _bundle(tmp_path, arch_priority=["arm64"])
    _compile(app / "Contents" / "Resources" / "dep.dylib", ["arm64"])
    assert check(app, "arm64", "12.0")[1]["passed"]
    assert not check(app, "x86_64", "12.0")[1]["passed"]


# ------------------------------------------------------------------------------------------
# Deployment floor: the advertised minimum must be one the bytes can actually reach
# ------------------------------------------------------------------------------------------

@requires_clang
def test_a_dependency_requiring_a_newer_macos_than_advertised_is_refused(tmp_path: Path) -> None:
    """The shipped vool-devauth defect: compiled with no -target, it inherited macOS 26.0
    inside a bundle advertising 12.0, so on an older Mac it is a dyld failure."""
    app = _bundle(tmp_path, arch_priority=["arm64"], min_os="12.0")
    helper = app / "Contents" / "Resources" / "vool-devauth"
    _compile(helper, ["arm64"], min_macos="15.0")

    fails, report = check(app, "arm64", "12.0")

    assert not report["passed"]
    dep = [f for f in fails if f.startswith("deployment:") and str(helper) in f]
    assert dep, fails
    assert "15.0" in dep[0] and "12.0" in dep[0]


@requires_clang
def test_the_same_helper_passes_once_it_is_built_for_the_advertised_floor(tmp_path: Path) -> None:
    app = _bundle(tmp_path, arch_priority=["arm64"], min_os="12.0")
    _compile(app / "Contents" / "Resources" / "vool-devauth", ["arm64"], min_macos="12.0")
    fails, report = check(app, "arm64", "12.0")
    assert report["passed"], fails


def test_a_plist_floor_that_disagrees_with_the_build_is_refused(tmp_path: Path) -> None:
    app = _bundle(tmp_path, arch_priority=["arm64"], min_os="12.0")
    fails, _ = check(app, "arm64", "14.0")
    assert any(f.startswith("deployment:") and "LSMinimumSystemVersion" in f for f in fails), fails


# ------------------------------------------------------------------------------------------
# Bundle self-containment
# ------------------------------------------------------------------------------------------

@requires_clang
def test_a_symlink_leaving_the_bundle_is_refused(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    _compile(outside / "host.dylib", ["arm64"])
    app = _bundle(tmp_path / "b", arch_priority=["arm64"])
    (app / "Contents" / "Resources" / "host.dylib").symlink_to(outside / "host.dylib")

    fails, report = check(app, "arm64", "12.0")

    assert not report["passed"]
    assert any(f.startswith("symlink-escape:") for f in fails), fails


# ------------------------------------------------------------------------------------------
# The census itself: it must agree with the platform's own reading of the bytes
# ------------------------------------------------------------------------------------------

@requires_clang
def test_the_census_reads_slices_and_deployment_targets_the_way_lipo_and_vtool_do(tmp_path: Path) -> None:
    for arches, floor in ((["arm64"], "12.0"), (["x86_64"], "13.0"), (["arm64", "x86_64"], "14.0")):
        target = _compile(tmp_path / ("x" + "_".join(arches)), arches, min_macos=floor)
        parsed = parse_macho(target)
        lipo = subprocess.run(["/usr/bin/lipo", "-archs", str(target)],
                              capture_output=True, text=True, timeout=60).stdout.split()
        assert sorted(parsed.archs) == sorted(lipo), (parsed.archs, lipo)
        assert {s.minos for s in parsed.slices} == {f"{floor}.0"}, parsed.slices


def test_the_census_does_not_mistake_a_script_for_a_macho(tmp_path: Path) -> None:
    script = tmp_path / "launcher"
    script.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    assert parse_macho(script) is None
    app = _bundle(tmp_path / "b", arch_priority=["arm64"])
    data = census(app)
    assert data["launch_contract"]["main_executable_is_macho"] is False
    assert data["macho_count"] == 0


# ------------------------------------------------------------------------------------------
# The gate's own CLI: a defaulted floor once made it check the wrong number
# ------------------------------------------------------------------------------------------

def test_the_gate_refuses_to_assume_a_deployment_floor(tmp_path: Path) -> None:
    """Regression: the build called the gate with --min-macos while it parsed --min-os, so the
    check silently ran against a hardcoded 12.0 and reported the bundle's honest 14.0 as a
    mismatch. A missing floor must be an error, never an assumption."""
    app = _bundle(tmp_path, arch_priority=["arm64"], min_os="14.0")
    done = subprocess.run([sys.executable, str(BUNDLE_DIR / "macos_arch_gate.py"), str(app), "--arch", "arm64"],
                          capture_output=True, text=True, timeout=120)
    assert done.returncode == 2, done.stdout + done.stderr
    assert "--min-os is required" in done.stderr


@pytest.mark.parametrize("flag", ["--min-os", "--min-macos"])
def test_the_gate_accepts_both_spellings_of_the_floor(tmp_path: Path, flag: str) -> None:
    app = _bundle(tmp_path, arch_priority=["arm64"], min_os="14.0")
    done = subprocess.run([sys.executable, str(BUNDLE_DIR / "macos_arch_gate.py"), str(app),
                           "--arch", "arm64", flag, "14.0"],
                          capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "PASS" in done.stdout


# ------------------------------------------------------------------------------------------
# The translation detector behind the root-cause proof
# ------------------------------------------------------------------------------------------

def test_the_translation_detector_reads_this_process_correctly() -> None:
    """The p_flag offset is the load-bearing assumption of the whole Rosetta diagnosis.

    A wrong offset would read garbage and silently answer "not translated" for everything --
    which is exactly the answer that makes the defect invisible. This pins the offset against
    facts that must hold for the running interpreter: it is a 64-bit exec'd process, and it is
    not translated (the suite does not run under Rosetta).
    """
    from macos_rosetta_probe import P_EXEC, P_LP64, is_translated, process_flags

    flags = process_flags(os.getpid())
    assert flags is not None, "could not read this process's flags"
    assert flags & P_LP64, f"p_flag 0x{flags:08x} does not look 64-bit -- offset is wrong"
    assert flags & P_EXEC, f"p_flag 0x{flags:08x} does not look exec'd -- offset is wrong"
    assert is_translated(os.getpid()) is False


def test_the_translation_detector_reports_a_dead_process_as_unknown_not_native() -> None:
    """A pid that cannot be read must be None, never False -- "not sampled" is not "native"."""
    from macos_rosetta_probe import is_translated, process_flags

    dead = 0x7FFFFFFE  # a pid that cannot exist
    assert process_flags(dead) is None
    assert is_translated(dead) is None


# ------------------------------------------------------------------------------------------
# Census fields the mission names by word: code signing, reachability, resolved load paths
# ------------------------------------------------------------------------------------------

@requires_clang
def test_the_census_reads_code_signing_the_way_codesign_does(tmp_path: Path) -> None:
    """Checkpoint 2 asks for "relevant code-signing information". Parsed from the CodeDirectory
    rather than shelled out, so it still works as a build gate with no Xcode toolchain -- and
    cross-checked here against codesign(1), which is the only thing that makes that safe."""
    target = _compile(tmp_path / "signed", ["arm64"])
    parsed = parse_macho(target).slices[0].signature
    assert parsed.signed, "clang ad-hoc signs on Apple Silicon; the census saw nothing"

    truth = subprocess.run(["/usr/bin/codesign", "-dv", "--verbose=4", str(target)],
                           capture_output=True, text=True, timeout=60).stderr
    if "flags=" in truth:
        flags = truth.split("flags=")[1].split("(")[0].split()[0]
        assert parsed.flags == f"0x{int(flags, 16):08x}", (parsed.flags, flags)
    if "adhoc" in truth:
        assert parsed.adhoc


def test_test_only_binaries_do_not_set_the_advertised_floor(tmp_path: Path) -> None:
    """A binary the product never loads must not fail the gate for the product that never loads it."""
    from macos_arch_census import _reachable

    app = Path("/x/VOOL.app")
    assert _reachable(app / "Contents/Resources/python/lib/PyObjCTest/_tools.so", app) is False
    assert _reachable(app / "Contents/Resources/python/lib/lib-dynload/_ctypes_test.so", app) is False
    assert _reachable(app / "Contents/Resources/python/lib/nacl/_sodium.so", app) is True
    # the directory the bundle SITS IN must not decide reachability
    under_test_dir = Path("/tmp/test_something_/VOOL.app")
    assert _reachable(under_test_dir / "Contents/Resources/nacl/_sodium.so", under_test_dir) is True


@requires_clang
def test_a_test_only_binary_above_the_floor_does_not_fail_the_gate(tmp_path: Path) -> None:
    app = _bundle(tmp_path, arch_priority=["arm64"], min_os="12.0")
    live = app / "Contents" / "Resources" / "live.dylib"
    dead = app / "Contents" / "Resources" / "PyObjCTest" / "dead.dylib"
    dead.parent.mkdir(parents=True, exist_ok=True)
    _compile(live, ["arm64"], min_macos="12.0")
    _compile(dead, ["arm64"], min_macos="15.0")   # above the floor, but never loaded

    fails, report = check(app, "arm64", "12.0")
    assert report["passed"], fails

    # ...and the same binary in a REACHABLE location still fails, so this is not a blanket excuse.
    reachable_copy = app / "Contents" / "Resources" / "reachable.dylib"
    _compile(reachable_copy, ["arm64"], min_macos="15.0")
    fails2, report2 = check(app, "arm64", "12.0")
    assert not report2["passed"]
    assert any(str(reachable_copy) in f for f in fails2), fails2


@requires_clang
def test_a_dependency_that_resolves_to_nothing_in_the_bundle_fails_the_gate(tmp_path: Path) -> None:
    """Checkpoint 2: "Resolve load paths and ensure the advertised target has a compatible
    dependency closure." A dangling @rpath is a dependency on the BUILD machine."""
    app = _bundle(tmp_path, arch_priority=["arm64"])
    lib = app / "Contents" / "Resources" / "libdep.dylib"
    consumer = app / "Contents" / "Resources" / "consumer"
    src = tmp_path / "d.c"
    src.write_text("int dep(void){return 1;}\n", encoding="utf-8")
    # -mmacosx-version-min is load-bearing in the FIXTURE too: without it clang stamps the build
    # host's macOS and the bundle fails the DEPLOYMENT check, so this test would go red for a
    # reason that has nothing to do with the closure it is meant to prove.
    subprocess.run([str(CLANG), "-arch", "arm64", "-dynamiclib", "-install_name",
                    "@rpath/libdep.dylib", "-mmacosx-version-min=12.0", str(src), "-o", str(lib)],
                   capture_output=True, timeout=180)
    main = tmp_path / "m.c"
    main.write_text("int dep(void); int main(void){return dep();}\n", encoding="utf-8")
    built = subprocess.run([str(CLANG), "-arch", "arm64", "-mmacosx-version-min=12.0", str(main), str(lib),
                            "-Wl,-rpath,@loader_path", "-o", str(consumer)], capture_output=True, timeout=180)
    if built.returncode != 0:
        pytest.skip(f"toolchain could not build the rpath fixture: {built.stderr.decode()[:200]}")

    assert check(app, "arm64", "12.0")[1]["passed"], "the dependency is present; this must pass"

    lib.unlink()   # the ONLY change: the dependency leaves the bundle
    fails, report = check(app, "arm64", "12.0")
    assert not report["passed"]
    closure = [f for f in fails if f.startswith("closure:")]
    assert closure and "libdep.dylib" in closure[0], fails


# ------------------------------------------------------------------------------------------
# Checkpoint 3: separate per-architecture artifacts must be separately LABELLED
# ------------------------------------------------------------------------------------------

def _build_script_ids(arch: str) -> tuple[str, str]:
    """Run the build script's own id/name derivation for `arch`, with nothing else executed."""
    script = (BUNDLE_DIR / "build_macos_app.sh").read_text(encoding="utf-8")
    body = "\n".join([
        'RELEASE_VERSION="9.9.9-test"', 'GIT_COMMIT="abcdef123456"',
        f'TARGET_ARCH="{arch}"', 'OUT_DIR="/out"',
        [ln for ln in script.splitlines() if ln.startswith('VOOL_BUILD_ID="${RELEASE_VERSION')][-1],
        next(ln for ln in script.splitlines() if 'DMG="${OUT_DIR}' in ln).strip(),
        'echo "$VOOL_BUILD_ID"', 'echo "$DMG"',
    ])
    out = subprocess.run(["bash", "-c", body], capture_output=True, text=True, timeout=60).stdout.split()
    return out[0], out[1]


def test_two_architectures_of_one_commit_are_labelled_differently() -> None:
    """Separate per-arch artifacts that share a build id and a DMG filename are not separately
    labelled -- an operator holding both cannot tell them apart from the artifact alone."""
    arm_id, arm_dmg = _build_script_ids("arm64")
    x64_id, x64_dmg = _build_script_ids("x86_64")

    assert "arm64" in arm_id and "x86_64" in x64_id, (arm_id, x64_id)
    assert arm_id != x64_id
    assert arm_dmg != x64_dmg, (arm_dmg, x64_dmg)
    assert arm_dmg.endswith("VOOL-arm64.dmg") and x64_dmg.endswith("VOOL-x86_64.dmg")


def test_an_explicit_arch_flag_reaches_the_build_id() -> None:
    """Regression: the id was first computed near the top of the script, BEFORE --arch was parsed,
    so `--arch x86_64` stamped the build host's architecture into the id, plist and manifest."""
    script = (BUNDLE_DIR / "build_macos_app.sh").read_text(encoding="utf-8")
    first = script.index('VOOL_BUILD_ID="${RELEASE_VERSION')
    argparse_at = script.index('--arch) TARGET_ARCH=')
    last = script.rindex('VOOL_BUILD_ID="${RELEASE_VERSION')
    assert last > argparse_at, "the build id is never recomputed after --arch is parsed"
    assert first < argparse_at, "unexpected script shape; the early assignment moved"


def test_every_value_derived_from_the_target_arch_is_recomputed_after_parsing() -> None:
    """Regression, found twice: values derived from TARGET_ARCH are first computed near the top of
    the build script, BEFORE --arch is parsed. The build id was one; the uv interpreter/wheel
    spellings were another, and that one sent `--arch x86_64` to uv as the host's aarch64 request,
    so uv returned an arm64 CPython for an Intel build. Every such derivation must be recomputed
    after argument parsing."""
    script = (BUNDLE_DIR / "build_macos_app.sh").read_text(encoding="utf-8")
    argparse_at = script.index('--arch) TARGET_ARCH=')
    for marker in ('VOOL_BUILD_ID="${RELEASE_VERSION', 'UV_ARCH="aarch64"'):
        first, last = script.index(marker), script.rindex(marker)
        assert first < argparse_at, f"{marker}: unexpected script shape"
        assert last > argparse_at, f"{marker} is never recomputed after --arch is parsed"
