"""Checksum or seal a VOOL release directory (OpenPGP detached signatures, signed SHA256SUMS).

Release engineering tool. FAIL-CLOSED: it signs nothing unless every input is releasable,
and a failure part-way removes every signature it wrote, so a directory is either fully
sealed or not sealed at all.

  source  writes vool-<version>-source.tar.gz (`git archive` of an exact ref): the Linux
          release artifact while Linux installs from source.
  checksums  writes SHA256SUMS and a <file>.sha256 sidecar per artifact, no signing: the
          download path while signing credentials do not exist yet (ad-hoc DMGs allowed).
  seal    signs every file in a release directory with the release OpenPGP key:
            <file>.asc        detached, ASCII-armoured signature per artifact
            SHA256SUMS        sha256sum-format digests of every artifact
            SHA256SUMS.asc    detached signature over SHA256SUMS
          A macOS DMG is accepted only with the receipt sign_macos_release.sh writes after
          Apple accepted and stapled it; on macOS the staple and Gatekeeper verdict are
          re-checked live; --allow-unnotarized accepts an ad-hoc DMG instead. Afterwards everything is verified against the PINNED public key
          alone, the way a user would verify it.
  verify  checks a sealed directory against the pinned public key (any OS with gpg).

The signing key never touches this tool: gpg uses the owner's own keyring, agent or
smartcard. The key is named by fingerprint in VOOL_RELEASE_GPG_KEY and must belong to the
public key pinned at config/release/release-signing-key.asc. No pin, no seal.

Usage:
    python -m installer.release_sign source --ref v0.7.0 --version 0.7.0 --out artifacts/release/0.7.0
    python -m installer.release_sign checksums --dir artifacts/release/0.7.0
    python -m installer.release_sign seal --dir artifacts/release/0.7.0
    python -m installer.release_sign verify --dir artifacts/release/0.7.0
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PINNED_KEY = REPO_ROOT / "config" / "release" / "release-signing-key.asc"
SUMS = "SHA256SUMS"
SIG_SUFFIX = ".asc"
SIDECAR_SUFFIX = ".sha256"
RECEIPT_SUFFIX = ".signing.json"
_FPR = re.compile(r"^[0-9A-F]{40}$")
_VERSION = re.compile(r"^[0-9A-Za-z.+-]+$")


class Refused(Exception):
    """A release input or credential is not acceptable; nothing may be signed."""


def _gpg() -> str:
    found = shutil.which("gpg")
    if not found:
        raise Refused("gpg (GnuPG 2.2 or later) is not installed")
    return found


def _run(cmd: list[str], *, env: dict[str, str] | None = None, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=cwd, check=False, timeout=600)


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


class PinnedKeyring:
    """A throwaway GNUPGHOME holding ONLY the pinned public key: what a user verifies with."""

    def __init__(self, pinned: Path) -> None:
        if not pinned.is_file():
            raise Refused(
                f"no pinned release public key at {pinned}; publish the release key's public half there first"
            )
        self._dir = tempfile.TemporaryDirectory(prefix="vool-pinned-gpg-")
        self.home = self._dir.name
        os.chmod(self.home, 0o700)
        self.env = {**os.environ, "GNUPGHOME": self.home}
        imported = _run([_gpg(), "--batch", "--import", str(pinned)], env=self.env)
        if imported.returncode != 0:
            raise Refused(f"the pinned public key does not import: {imported.stderr.strip()}")
        listing = _run([_gpg(), "--batch", "--with-colons", "--list-keys"], env=self.env).stdout
        primaries = [ln for ln in listing.splitlines() if ln.startswith("pub:")]
        if len(primaries) != 1:
            raise Refused(f"the pinned key file must hold exactly one public key (found {len(primaries)})")
        self.fingerprints = {ln.split(":")[9] for ln in listing.splitlines() if ln.startswith("fpr:")}
        self.primary = next(ln.split(":")[9] for ln in listing.splitlines() if ln.startswith("fpr:"))
        # A sealed release lives longer than any one signing session: refuse a key that is
        # already revoked or expired rather than ship signatures users will see as invalid.
        validity = primaries[0].split(":")[1]
        if validity in {"r", "e"}:
            raise Refused("the pinned release key is revoked or expired")

    def verify(self, signature: Path, data: Path) -> None:
        done = _run([_gpg(), "--batch", "--status-fd", "1", "--verify", str(signature), str(data)], env=self.env)
        status = done.stdout.splitlines()
        valid = [ln.split() for ln in status if ln.startswith("[GNUPG:] VALIDSIG ")]
        if done.returncode != 0 or not any(ln.startswith("[GNUPG:] GOODSIG ") for ln in status) or not valid:
            raise Refused(f"{signature.name} does not verify against the pinned release key")
        # VALIDSIG <signing-key-fpr> ... <primary-key-fpr> (last field)
        if valid[0][-1] != self.primary:
            raise Refused(f"{signature.name} was made by a key other than the pinned release key")

    def close(self) -> None:
        self._dir.cleanup()


def _signing_fingerprint(env: dict[str, str]) -> str:
    fpr = str(env.get("VOOL_RELEASE_GPG_KEY") or "").replace(" ", "").upper()
    if not fpr:
        raise Refused("VOOL_RELEASE_GPG_KEY is not set (the release signing key's 40-hex fingerprint)")
    if not _FPR.match(fpr):
        raise Refused("VOOL_RELEASE_GPG_KEY must be a full 40-hex fingerprint, not a short id or a name")
    return fpr


def _artifacts(release_dir: Path) -> list[Path]:
    """Every file to be sealed; refuses anything that would make the sealed set ambiguous."""
    if not release_dir.is_dir():
        raise Refused(f"{release_dir} is not a directory")
    files: list[Path] = []
    for entry in sorted(release_dir.iterdir()):
        name = entry.name
        if name.startswith("."):
            raise Refused(f"hidden file {name} in the release directory")
        if entry.is_symlink() or not entry.is_file():
            raise Refused(f"{name} is not a regular file; a release directory holds files only")
        if name == SUMS or name.endswith((SIG_SUFFIX, SIDECAR_SUFFIX, ".sig", ".gpg")):
            raise Refused(f"{name} is already a signature or checksum file; seal an unsealed directory")
        if name.endswith(".log"):
            raise Refused(f"{name} is a log (a rejected notarization?); remove it before sealing")
        files.append(entry)
    if not files:
        raise Refused(f"{release_dir} is empty")
    return files


def _check_macos_artifacts(files: list[Path]) -> None:
    names = {f.name for f in files}
    for f in files:
        if f.name.endswith(RECEIPT_SUFFIX) and f.name[: -len(RECEIPT_SUFFIX)] not in names:
            raise Refused(f"{f.name} has no matching artifact")
        if f.name.endswith(RECEIPT_SUFFIX):
            continue
        if f.suffix.lower() in {".dmg", ".pkg"} or "macos" in f.name.lower():
            receipt_path = f.with_name(f.name + RECEIPT_SUFFIX)
            if not receipt_path.is_file():
                raise Refused(f"{f.name} has no signing receipt: only sign_macos_release.sh output can be released")
            try:
                receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise Refused(f"{receipt_path.name} is unreadable: {exc}") from exc
            if receipt.get("schema") != "vool-macos-signing-receipt/1" or receipt.get("artifact") != f.name:
                raise Refused(f"{receipt_path.name} is not a signing receipt for {f.name}")
            if receipt.get("sha256") != sha256_of(f):
                raise Refused(f"{f.name} changed after it was notarized (digest differs from its receipt)")
            notarization = receipt.get("notarization") or {}
            stapled = receipt.get("stapled") or {}
            if notarization.get("status") != "Accepted" or not stapled.get("app") or not stapled.get("dmg"):
                raise Refused(f"{f.name} is not recorded as notarized and stapled")
            if receipt.get("gatekeeper") != "Notarized Developer ID" or not receipt.get("hardened_runtime"):
                raise Refused(f"{f.name} is not recorded as a hardened, Gatekeeper-accepted Developer ID build")
            if platform.system() == "Darwin":
                _live_macos_check(f)


def _live_macos_check(dmg: Path) -> None:
    staple = _run(["xcrun", "stapler", "validate", str(dmg)])
    if staple.returncode != 0:
        raise Refused(f"{dmg.name}: stapled ticket does not validate: {staple.stdout.strip()} {staple.stderr.strip()}")
    gate = _run(["spctl", "--assess", "--type", "open", "--context", "context:primary-signature", "-vv", str(dmg)])
    if gate.returncode != 0 or "source=Notarized Developer ID" not in (gate.stderr + gate.stdout):
        raise Refused(f"{dmg.name}: Gatekeeper does not accept it as Notarized Developer ID")


def _write_sums(release_dir: Path, files: list[Path]) -> list[Path]:
    """SHA256SUMS plus one `<file>.sha256` sidecar per artifact (the website serves sidecars)."""
    written: list[Path] = []
    lines = []
    for f in sorted(files, key=lambda p: p.name):
        line = f"{sha256_of(f)}  {f.name}\n"
        lines.append(line)
        sidecar = f.with_name(f.name + SIDECAR_SUFFIX)
        sidecar.write_text(line, encoding="utf-8")
        written.append(sidecar)
    sums = release_dir / SUMS
    sums.write_text("".join(lines), encoding="utf-8")
    written.append(sums)
    return written


def checksums(release_dir: Path) -> list[str]:
    """Unsigned download path: digests only. Ad-hoc DMGs are allowed; nothing claims a signature."""
    files = _artifacts(release_dir)
    written: list[Path] = []
    try:
        written = _write_sums(release_dir, files)
        _check_sums(release_dir)
    except BaseException:
        for path in written:
            path.unlink(missing_ok=True)
        raise
    return sorted(f.name for f in files)


def _check_sums(release_dir: Path) -> dict[str, str]:
    """Every listed file present with its digest, every sidecar agreeing, nothing unlisted."""
    sums = release_dir / SUMS
    listed: dict[str, str] = {}
    for line in sums.read_text(encoding="utf-8").splitlines():
        match = re.fullmatch(r"([0-9a-f]{64})  ([^/\\]+)", line)
        if not match or match.group(2) in listed:
            raise Refused(f"malformed or duplicate {SUMS} line: {line!r}")
        listed[match.group(2)] = match.group(1)
    for name, digest in sorted(listed.items()):
        artifact = release_dir / name
        if not artifact.is_file():
            raise Refused(f"{name} is listed in {SUMS} but missing")
        if sha256_of(artifact) != digest:
            raise Refused(f"{name} does not match its {SUMS} digest")
        sidecar = release_dir / (name + SIDECAR_SUFFIX)
        if sidecar.is_file() and sidecar.read_text(encoding="utf-8") != f"{digest}  {name}\n":
            raise Refused(f"{sidecar.name} disagrees with {SUMS}")
    return listed


def _detach_sign(path: Path, fpr: str, env: dict[str, str]) -> Path:
    out = path.with_name(path.name + SIG_SUFFIX)
    done = _run(
        [
            _gpg(),
            "--batch",
            "--yes",
            "--local-user",
            fpr + "!",
            "--armor",
            "--detach-sign",
            "--digest-algo",
            "SHA512",
            "--output",
            str(out),
            str(path),
        ],
        env=env,
    )
    if done.returncode != 0 or not out.is_file():
        raise Refused(f"gpg could not sign {path.name}: {done.stderr.strip()}")
    return out


def verify(release_dir: Path, pinned: Path = PINNED_KEY) -> list[str]:
    """Verify a sealed directory; returns the artifact names. Raises Refused on any defect."""
    sums = release_dir / SUMS
    sums_sig = release_dir / (SUMS + SIG_SUFFIX)
    if not sums.is_file() or not sums_sig.is_file():
        raise Refused(f"{release_dir} is not sealed: {SUMS} and {SUMS}{SIG_SUFFIX} are both required")
    keyring = PinnedKeyring(pinned)
    try:
        keyring.verify(sums_sig, sums)
        listed = _check_sums(release_dir)
        present = {p.name for p in release_dir.iterdir()}
        expected = (
            set(listed)
            | {n + SIG_SUFFIX for n in listed}
            | {n + SIDECAR_SUFFIX for n in listed}
            | {SUMS, SUMS + SIG_SUFFIX}
        )
        unlisted = sorted(present - expected)
        if unlisted:
            raise Refused(f"files not covered by the signed {SUMS}: {', '.join(unlisted)}")
        for name in sorted(listed):
            keyring.verify(release_dir / (name + SIG_SUFFIX), release_dir / name)
        return sorted(listed)
    finally:
        keyring.close()


def seal(
    release_dir: Path,
    *,
    env: dict[str, str] | None = None,
    pinned: Path = PINNED_KEY,
    allow_unnotarized: bool = False,
) -> list[str]:
    env = dict(os.environ if env is None else env)
    fpr = _signing_fingerprint(env)
    files = _artifacts(release_dir)
    keyring = PinnedKeyring(pinned)
    try:
        if fpr not in keyring.fingerprints:
            raise Refused("VOOL_RELEASE_GPG_KEY is not the pinned release key (or one of its subkeys)")
    finally:
        keyring.close()
    if not allow_unnotarized:
        _check_macos_artifacts(files)

    written: list[Path] = []
    try:
        for f in files:
            written.append(_detach_sign(f, fpr, env))
        written.extend(_write_sums(release_dir, files))
        written.append(_detach_sign(release_dir / SUMS, fpr, env))
        return verify(release_dir, pinned)
    except BaseException:
        for path in written:
            path.unlink(missing_ok=True)
        (release_dir / SUMS).unlink(missing_ok=True)
        raise


def source_archive(ref: str, version: str, out_dir: Path) -> Path:
    if not _VERSION.match(version):
        raise Refused(f"invalid version {version!r}")
    resolved = _run(["git", "-C", str(REPO_ROOT), "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"])
    if resolved.returncode != 0:
        raise Refused(f"{ref!r} does not name a commit in this repository")
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"vool-{version}-source.tar.gz"
    if target.exists():
        raise Refused(f"{target.name} already exists; a release artifact is never overwritten")
    done = _run(
        [
            "git",
            "-C",
            str(REPO_ROOT),
            "archive",
            "--format=tar.gz",
            f"--prefix=vool-{version}/",
            "--output",
            str(target),
            resolved.stdout.strip(),
        ]
    )
    if done.returncode != 0:
        target.unlink(missing_ok=True)
        raise Refused(f"git archive failed: {done.stderr.strip()}")
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="vool-release-sign", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    src = sub.add_parser("source", help="write the source tarball for an exact ref")
    src.add_argument("--ref", required=True)
    src.add_argument("--version", required=True)
    src.add_argument("--out", required=True, type=Path)
    sub.add_parser("checksums", help="write SHA256SUMS and .sha256 sidecars (no signing)").add_argument(
        "--dir", required=True, type=Path
    )
    for name in ("seal", "verify"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--dir", required=True, type=Path)
        if name == "seal":
            cmd.add_argument(
                "--allow-unnotarized",
                action="store_true",
                help="accept ad-hoc (not notarized) macOS DMGs; the release then says so",
            )
        cmd.add_argument(
            "--pinned",
            type=Path,
            default=PINNED_KEY,
            help="pinned release public key (default: config/release/release-signing-key.asc)",
        )
    args = parser.parse_args(argv)
    try:
        if args.command == "source":
            print(f"OK: {source_archive(args.ref, args.version, args.out)}")
        elif args.command == "checksums":
            names = checksums(args.dir.resolve())
            print(f"CHECKSUMS: {len(names)} artifacts in {SUMS} with .sha256 sidecars (unsigned)")
        elif args.command == "seal":
            names = seal(args.dir.resolve(), pinned=args.pinned, allow_unnotarized=args.allow_unnotarized)
            print(f"SEALED: {len(names)} artifacts signed, {SUMS} signed, all verified against the pinned key")
        else:
            names = verify(args.dir.resolve(), pinned=args.pinned)
            print(f"VERIFIED: {len(names)} artifacts and {SUMS} against the pinned key")
    except Refused as exc:
        print(f"RELEASE SIGNING REFUSED: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
