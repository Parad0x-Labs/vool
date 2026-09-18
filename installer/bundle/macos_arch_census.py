"""Architecture / minimum-OS census for a macOS .app bundle.

Why this exists
---------------
A macOS app is not "arm64" because its files are arm64. The 0.5.0 bundles carried 238
Mach-O files that every one advertised an arm64 slice -- and macOS still launched the app
through Rosetta, because the bundle's main executable is a shell script and LaunchServices
forges ``LSArchitecturePriority = (x86_64, arm64)`` for a bundle whose main executable has no
Mach-O header to read. A census that only looks at file slices cannot see that; this one
grades the launch contract in the Info.plist as well.

Pure stdlib on purpose: this runs as a build-time gate inside the bundle build, where no
third-party module is available and shelling out to the Xcode toolchain is not guaranteed.
"""

from __future__ import annotations

import json
import plistlib
import struct
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path

# --- Mach-O constants ---------------------------------------------------------------------
FAT_MAGIC, FAT_CIGAM = 0xCAFEBABE, 0xBEBAFECA
FAT_MAGIC_64, FAT_CIGAM_64 = 0xCAFEBABF, 0xBFBAFECA
MH_MAGIC, MH_CIGAM = 0xFEEDFACE, 0xCEFAEDFE
MH_MAGIC_64, MH_CIGAM_64 = 0xFEEDFACF, 0xCFFAEDFE

CPU_ARCH_ABI64 = 0x01000000
CPU_TYPE_X86 = 7
CPU_TYPE_ARM = 12
CPU_TYPE_POWERPC = 18

LC_VERSION_MIN_MACOSX = 0x24
LC_BUILD_VERSION = 0x32
LC_LOAD_DYLIB = 0x0C
LC_LOAD_WEAK_DYLIB = 0x80000018
LC_REEXPORT_DYLIB = 0x8000001F
LC_RPATH = 0x8000001C
LC_CODE_SIGNATURE = 0x1D

# Code-signing SuperBlob, as laid out by cs_blobs.h. Parsed directly rather than shelled out to
# `codesign`, so the census keeps working as a build gate with no Xcode toolchain present.
CSMAGIC_EMBEDDED_SIGNATURE = 0xFADE0CC0
CSMAGIC_CODEDIRECTORY = 0xFADE0C02
CSSLOT_CODEDIRECTORY = 0
CS_ADHOC = 0x0000002
CS_LINKER_SIGNED = 0x0020000
CS_RUNTIME = 0x0010000  # hardened runtime

PLATFORM_MACOS = 1

MH_EXECUTE, MH_DYLIB, MH_BUNDLE, MH_DYLINKER, MH_OBJECT = 2, 6, 8, 7, 1
FILETYPE_NAMES = {
    MH_OBJECT: "object",
    MH_EXECUTE: "executable",
    MH_DYLIB: "dylib",
    MH_DYLINKER: "dylinker",
    MH_BUNDLE: "bundle/extension",
}


def arch_name(cputype: int, cpusubtype: int) -> str:
    masked = cpusubtype & 0x00FFFFFF
    if cputype == (CPU_TYPE_X86 | CPU_ARCH_ABI64):
        return "x86_64h" if masked == 8 else "x86_64"
    if cputype == CPU_TYPE_X86:
        return "i386"
    if cputype == (CPU_TYPE_ARM | CPU_ARCH_ABI64):
        return "arm64e" if masked == 2 else "arm64"
    if cputype == CPU_TYPE_ARM:
        return "arm"
    if cputype in (CPU_TYPE_POWERPC, CPU_TYPE_POWERPC | CPU_ARCH_ABI64):
        return "ppc"
    return f"cputype:{cputype}"


def _ver(packed: int) -> str:
    return f"{packed >> 16}.{(packed >> 8) & 0xFF}.{packed & 0xFF}"


@dataclass
class Signature:
    """What the embedded code signature says about this slice."""
    signed: bool = False
    identifier: str | None = None
    team_id: str | None = None
    adhoc: bool = False
    linker_signed: bool = False
    hardened_runtime: bool = False
    flags: str | None = None


@dataclass
class Slice:
    arch: str
    filetype: str
    platform: str | None = None
    minos: str | None = None
    sdk: str | None = None
    linked: list[str] = field(default_factory=list)
    rpaths: list[str] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)
    signature: Signature = field(default_factory=Signature)


@dataclass
class MachOFile:
    path: str
    universal: bool
    slices: list[Slice]
    symlink_to: str | None = None
    runtime_reachable: bool = True

    @property
    def archs(self) -> list[str]:
        return [s.arch for s in self.slices]


def _parse_thin(data: memoryview, off: int, size: int) -> Slice | None:
    if off + 32 > len(data):
        return None
    magic = struct.unpack_from(">I", data, off)[0]
    if magic in (MH_MAGIC_64, MH_MAGIC):
        endian, is64 = ">", magic == MH_MAGIC_64
    else:
        magic_le = struct.unpack_from("<I", data, off)[0]
        if magic_le in (MH_MAGIC_64, MH_MAGIC):
            endian, is64 = "<", magic_le == MH_MAGIC_64
        else:
            return None
    cputype, cpusubtype, filetype, ncmds, _sizeofcmds, _flags = struct.unpack_from(
        endian + "iiIIII", data, off + 4
    )
    lc = off + (32 if is64 else 28)
    sl = Slice(
        arch=arch_name(cputype, cpusubtype),
        filetype=FILETYPE_NAMES.get(filetype, f"filetype:{filetype}"),
    )
    for _ in range(ncmds):
        if lc + 8 > len(data):
            break
        cmd, cmdsize = struct.unpack_from(endian + "II", data, lc)
        if cmdsize < 8:
            break
        if cmd == LC_BUILD_VERSION and lc + 24 <= len(data):
            plat, minos, sdk = struct.unpack_from(endian + "III", data, lc + 8)
            sl.platform = "macos" if plat == PLATFORM_MACOS else f"platform:{plat}"
            sl.minos, sl.sdk = _ver(minos), _ver(sdk)
        elif cmd == LC_VERSION_MIN_MACOSX and lc + 16 <= len(data):
            minos, sdk = struct.unpack_from(endian + "II", data, lc + 8)
            sl.platform, sl.minos, sl.sdk = "macos", _ver(minos), _ver(sdk)
        elif cmd in (LC_LOAD_DYLIB, LC_LOAD_WEAK_DYLIB, LC_REEXPORT_DYLIB) and lc + 24 <= len(data):
            name_off = struct.unpack_from(endian + "I", data, lc + 8)[0]
            start = lc + name_off
            if 0 < name_off < cmdsize and start < len(data):
                raw = bytes(data[start : lc + cmdsize]).split(b"\x00", 1)[0]
                sl.linked.append(raw.decode("utf-8", "replace"))
        elif cmd == LC_RPATH and lc + 12 <= len(data):
            path_off = struct.unpack_from(endian + "I", data, lc + 8)[0]
            start = lc + path_off
            if 0 < path_off < cmdsize and start < len(data):
                raw = bytes(data[start : lc + cmdsize]).split(b"\x00", 1)[0]
                sl.rpaths.append(raw.decode("utf-8", "replace"))
        elif cmd == LC_CODE_SIGNATURE and lc + 16 <= len(data):
            # dataoff is relative to the START OF THIS SLICE, not the start of the file. In a fat
            # binary those differ, and reading it as a file offset lands on unrelated bytes: every
            # slice of the universal ollama then reported "unsigned" while codesign(1) showed it
            # signed with a real team id. (Also note this must not shadow `off`, the slice base.)
            sig_off, sig_size = struct.unpack_from(endian + "II", data, lc + 8)
            sl.signature = _parse_signature(data, off + sig_off, sig_size)
        lc += cmdsize
    return sl


def _cstr(data: memoryview, at: int) -> str | None:
    if at <= 0 or at >= len(data):
        return None
    raw = bytes(data[at : at + 256]).split(b"\x00", 1)[0]
    return raw.decode("utf-8", "replace") or None


def _parse_signature(data: memoryview, off: int, size: int) -> Signature:
    """Read the embedded signature's CodeDirectory: identity, team, and the flags that matter.

    Only the CodeDirectory is read -- enough to answer "is this signed, by whom, adhoc or real,
    hardened or not", which is the code-signing information the architecture question needs. It
    deliberately does NOT verify the signature; that is `codesign -v`'s job and needs the toolchain.
    """
    sig = Signature()
    # The signature blob is big-endian regardless of the Mach-O's own byte order.
    if off + 12 > len(data) or size < 12:
        return sig
    magic, _total, count = struct.unpack_from(">III", data, off)
    if magic != CSMAGIC_EMBEDDED_SIGNATURE:
        return sig
    sig.signed = True
    for i in range(min(count, 32)):
        ent = off + 12 + i * 8
        if ent + 8 > len(data):
            break
        slot, blob_off = struct.unpack_from(">II", data, ent)
        if slot != CSSLOT_CODEDIRECTORY:
            continue
        cd = off + blob_off
        if cd + 44 > len(data):
            break
        cd_magic, _cd_len, version, flags = struct.unpack_from(">IIII", data, cd)
        if cd_magic != CSMAGIC_CODEDIRECTORY:
            break
        sig.flags = f"0x{flags:08x}"
        sig.adhoc = bool(flags & CS_ADHOC)
        sig.linker_signed = bool(flags & CS_LINKER_SIGNED)
        sig.hardened_runtime = bool(flags & CS_RUNTIME)
        ident_off = struct.unpack_from(">I", data, cd + 20)[0]
        sig.identifier = _cstr(data, cd + ident_off)
        # CodeDirectory layout (cs_blobs.h): magic 0, length 4, version 8, flags 12, hashOffset 16,
        # identOffset 20, nSpecialSlots 24, nCodeSlots 28, codeLimit 32, hashSize 36, hashType 37,
        # platform 38, pageSize 39, spare2 40, scatterOffset 44 (v0x20100), teamOffset 48 (v0x20200).
        # teamOffset is at 48; reading it at 88 returned bytes from the hash array, which rendered
        # as a garbage "team id" for the one binary that actually has one.
        if version >= 0x20200 and cd + 52 <= len(data):
            team_off = struct.unpack_from(">I", data, cd + 48)[0]
            if team_off:
                sig.team_id = _cstr(data, cd + team_off)
        break
    return sig


def parse_macho(path: Path) -> MachOFile | None:
    """Return a MachOFile, or None when *path* is not Mach-O."""
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    if len(raw) < 8:
        return None
    data = memoryview(raw)
    magic = struct.unpack_from(">I", data, 0)[0]
    if magic in (FAT_MAGIC, FAT_CIGAM, FAT_MAGIC_64, FAT_CIGAM_64):
        wide = magic in (FAT_MAGIC_64, FAT_CIGAM_64)
        nfat = struct.unpack_from(">I", data, 4)[0]
        slices, entry, esize = [], 8, 32 if wide else 20
        for _ in range(min(nfat, 64)):
            if entry + esize > len(data):
                break
            if wide:
                _ct, _cs, off, size = struct.unpack_from(">iiQQ", data, entry)
            else:
                _ct, _cs, off, size = struct.unpack_from(">iiII", data, entry)
            sl = _parse_thin(data, off, size)
            if sl:
                slices.append(sl)
            entry += esize
        return MachOFile(str(path), True, slices) if slices else None
    sl = _parse_thin(data, 0, len(raw))
    return MachOFile(str(path), False, [sl]) if sl else None


# --- bundle-level census ------------------------------------------------------------------

def _launch_contract(app: Path) -> dict:
    """Grade the Info.plist launch contract.

    This is the half a file-slice census cannot see. When ``CFBundleExecutable`` names a
    script rather than a Mach-O, LaunchServices has no header to read and forges an
    architecture priority that puts x86_64 first -- so the app launches under Rosetta no
    matter how native every file in it is.
    """
    info = app / "Contents" / "Info.plist"
    out: dict = {"info_plist": str(info)}
    if not info.is_file():
        out["error"] = "no Info.plist"
        return out
    try:
        with info.open("rb") as fh:
            pl = plistlib.load(fh)
    except Exception as exc:  # noqa: BLE001 - report, never crash the gate
        out["error"] = f"unreadable Info.plist: {exc}"
        return out

    exec_name = pl.get("CFBundleExecutable")
    out["cfbundle_executable"] = exec_name
    out["lsminimum_system_version"] = pl.get("LSMinimumSystemVersion")
    out["lsarchitecture_priority"] = pl.get("LSArchitecturePriority")

    main = app / "Contents" / "MacOS" / exec_name if exec_name else None
    out["main_executable"] = str(main) if main else None
    if main and main.exists():
        mo = parse_macho(main)
        out["main_executable_is_macho"] = mo is not None
        out["main_executable_archs"] = mo.archs if mo else []
    else:
        out["main_executable_is_macho"] = False
        out["main_executable_archs"] = []
        out["main_executable_missing"] = True
    return out


# Paths whose binaries are never loaded by the running product. Their deployment targets must not
# set the bundle's advertised floor: a test-only extension built against a newer SDK would
# otherwise fail the gate for a product that never loads it.
_TEST_ONLY_MARKERS = ("/PyObjCTest/", "/test/", "/tests/", "_ctypes_test", "_test_", "/idlelib/")


def _reachable(path: Path, app: Path | None = None) -> bool:
    """Is this binary on a path the running product would ever load?

    Judged on the path RELATIVE TO THE BUNDLE. Matching the absolute path lets the directory the
    bundle happens to sit in decide the answer -- a bundle built under any directory containing
    "test" would have marked its own live binaries unreachable, silently excusing them from the
    deployment floor.
    """
    rel = str(path)
    if app is not None:
        try:
            rel = "/" + str(Path(path).relative_to(app))
        except ValueError:
            rel = str(path)
    return not any(m in rel for m in _TEST_ONLY_MARKERS)


def _resolve_loads(app: Path, mo: "MachOFile") -> None:
    """Expand @rpath / @loader_path / @executable_path and record what stays unresolved.

    Checkpoint 2 asks for load paths resolved and a compatible dependency closure. Recording the
    raw LC_LOAD_DYLIB strings is not that: '@rpath/libpython3.11.dylib' names nothing until the
    slice's own LC_RPATH entries are applied.
    """
    binary = Path(mo.path)
    loader = binary.parent
    # @executable_path is the directory of the MAIN EXECUTABLE OF THE PROCESS, which is not one
    # fixed place in a bundle like this: the app's launcher runs Contents/MacOS, but the embedded
    # interpreter and the helper binaries are executed directly, so for them it is their own
    # directory. Resolving it only against Contents/MacOS reported the embedded python's
    # @rpath/libpython3.11.dylib as unresolved while the dylib sits right there in python/lib.
    # Both interpretations are tried, and a dependency is unresolved only if neither finds it.
    exe_paths = [app / "Contents" / "MacOS", loader]
    for sl in mo.slices:
        unresolved = []
        for dep in sl.linked:
            if dep.startswith("/"):
                # System libraries live in the dyld shared cache and have no file on disk; that is
                # expected, not unresolved.
                continue
            candidates = []
            if dep.startswith("@rpath/"):
                tail = dep[len("@rpath/"):]
                for rp in sl.rpaths:
                    for exe in exe_paths:
                        base = (rp.replace("@loader_path", str(loader))
                                  .replace("@executable_path", str(exe)))
                        candidates.append(Path(base) / tail)
            elif dep.startswith("@loader_path/"):
                candidates.append(loader / dep[len("@loader_path/"):])
            elif dep.startswith("@executable_path/"):
                candidates.extend(exe / dep[len("@executable_path/"):] for exe in exe_paths)
            else:
                candidates.append(loader / dep)
            if not any(c.exists() for c in candidates):
                unresolved.append(dep)
        sl.unresolved = unresolved


def census(app: Path) -> dict:
    macho, skipped_dsym = [], 0
    for p in sorted(app.rglob("*")):
        if p.is_symlink():
            continue
        if not p.is_file():
            continue
        if ".dSYM/" in str(p):
            skipped_dsym += 1
            continue
        mo = parse_macho(p)
        if mo:
            mo.runtime_reachable = _reachable(p, app)
            _resolve_loads(app, mo)
            macho.append(mo)

    symlinks = []
    for p in sorted(app.rglob("*")):
        if not p.is_symlink():
            continue
        try:
            target = p.resolve()
            escapes = not str(target).startswith(str(app.resolve()))
        except OSError:
            target, escapes = Path("<broken>"), False
        symlinks.append(
            {"path": str(p), "target": str(target), "escapes_bundle": escapes}
        )

    return {
        "app": str(app),
        "launch_contract": _launch_contract(app),
        "macho_count": len(macho),
        "dsym_files_skipped": skipped_dsym,
        "symlinks": symlinks,
        "symlinks_escaping_bundle": [s for s in symlinks if s["escapes_bundle"]],
        "macho": [asdict(m) for m in macho],
    }


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: macos_arch_census.py <VOOL.app> [--json out.json]", file=sys.stderr)
        return 2
    app = Path(argv[1]).resolve()
    if not app.is_dir():
        print(f"not a directory: {app}", file=sys.stderr)
        return 2
    data = census(app)

    out_path = None
    if "--json" in argv:
        out_path = Path(argv[argv.index("--json") + 1])

    lc = data["launch_contract"]
    arch_hist: dict[str, int] = {}
    minos_hist: dict[str, int] = {}
    for m in data["macho"]:
        key = "+".join(sorted({s["arch"] for s in m["slices"]}))
        arch_hist[key] = arch_hist.get(key, 0) + 1
        for s in m["slices"]:
            if s["minos"]:
                minos_hist[s["minos"]] = minos_hist.get(s["minos"], 0) + 1

    print(f"app                     {data['app']}")
    print(f"Mach-O files            {data['macho_count']}  (dSYM files skipped: {data['dsym_files_skipped']})")
    print(f"main executable         {lc.get('cfbundle_executable')}  Mach-O={lc.get('main_executable_is_macho')}")
    print(f"LSArchitecturePriority  {lc.get('lsarchitecture_priority')}")
    print(f"LSMinimumSystemVersion  {lc.get('lsminimum_system_version')}")
    print("slice histogram         " + ", ".join(f"{k}={v}" for k, v in sorted(arch_hist.items())))
    print("minos histogram         " + ", ".join(f"{k}={v}" for k, v in sorted(minos_hist.items(), key=lambda kv: -kv[1])[:12]))
    if data["symlinks_escaping_bundle"]:
        print(f"symlinks escaping       {len(data['symlinks_escaping_bundle'])}")
    if out_path:
        out_path.write_text(json.dumps(data, indent=2))
        print(f"json                    {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
