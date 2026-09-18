"""ZIP and RAR: a listing, and the content of members actually asked for.

An archive is a container, not a document. Handing its raw bytes to a model as though they were
text produces confident nonsense about mojibake; extracting all of it produces a decompression
bomb. So an archive is read in two distinct steps, and the first one is always cheap:

* **List** every member with its name, uncompressed size and compression ratio. That alone answers
  "what is in this archive", which is the most common question about one.
* **Read** the content of a bounded number of members -- the ones the question points at, plus the
  small text-shaped ones -- and nothing else. Members that were listed but not read say so, so a
  model can never assume it saw a file it did not.

Every hostile shape a container can take is refused *before* the bytes it wants are produced:

* ``../../etc/passwd`` and absolute paths -- refused on the name, never "sanitized" into acceptance.
* Symlink and hardlink members -- refused; a link is an instruction to read somewhere else.
* Decompression bombs -- refused on the declared ratio and on the running total, and the actual
  decompressed bytes are counted as they arrive, so a lying header changes nothing.
* Member floods -- bounded count, checked against the central directory before any read.
* Nested archives -- listed, never automatically expanded.

RAR is not a format with a standard-library decoder. Where ``libarchive`` (``/usr/bin/bsdtar``) is
present it is used, sandboxed; where it is not, RAR is BLOCKED and says so.
"""

from __future__ import annotations

import hashlib
import json
import os
import posixpath
import zipfile
from dataclasses import dataclass
from pathlib import Path

from . import _sandbox
from ._limits import (
    MAX_ARCHIVE_EXPANSION_RATIO,
    MAX_ARCHIVE_MEMBER_BYTES,
    MAX_ARCHIVE_MEMBERS,
    MAX_ARCHIVE_READ_MEMBERS,
    MAX_ARCHIVE_TOTAL_BYTES,
    MAX_UNIT_CHARS,
)
from ._sandbox import Scratch, extraction_budget, require_confinement, run_confined
from ._types import UNIT_MEMBER, ReaderRefused, ReaderResult, ReaderUnavailable, ReaderUnit

BSDTAR = "/usr/bin/bsdtar"
ZIP_MAGIC = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")
RAR4_MAGIC = b"Rar!\x1a\x07\x00"
RAR5_MAGIC = b"Rar!\x1a\x07\x01\x00"
#: Extensions whose content is worth reading into text without being asked. Everything else is
#: listed only: a 30 MB binary is named, not decoded into replacement characters.
_TEXTUAL_SUFFIXES = frozenset(
    {
        ".txt", ".md", ".markdown", ".rst", ".csv", ".tsv", ".json", ".yaml", ".yml", ".toml",
        ".xml", ".html", ".htm", ".css", ".js", ".mjs", ".ts", ".tsx", ".jsx", ".py", ".rb",
        ".go", ".rs", ".java", ".kt", ".swift", ".c", ".h", ".cpp", ".hpp", ".cs", ".sh",
        ".bash", ".zsh", ".sql", ".log", ".ini", ".cfg", ".conf", ".tex", ".diff", ".patch",
    }
)
_NESTED_SUFFIXES = frozenset({".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz", ".tgz"})


@dataclass(frozen=True)
class Member:
    name: str
    size: int
    compressed: int
    is_dir: bool
    #: Set when the member is refused. The member is still LISTED -- an operator should see that a
    #: hostile entry was present and what happened to it -- but never read.
    refusal: str = ""

    @property
    def ratio(self) -> float:
        return (self.size / self.compressed) if self.compressed > 0 else float(self.size)


def sniff(data: bytes) -> str:
    head = bytes(data[:8])
    if head.startswith(RAR5_MAGIC) or head.startswith(RAR4_MAGIC):
        return "rar"
    if any(head.startswith(magic) for magic in ZIP_MAGIC):
        return "zip"
    return ""


def _unsafe_name(name: str) -> str:
    """The refusal reason for a member name, or '' when the name is safe."""
    if not name:
        return "the member has no name"
    if "\x00" in name:
        return "the member name contains a NUL byte"
    if name.startswith("/") or name.startswith("\\"):
        return "the member name is an absolute path"
    if len(name) > 2 and name[1] == ":" and name[2] in "/\\":
        return "the member name is an absolute Windows path"
    normalized = name.replace("\\", "/")
    parts = normalized.split("/")
    if ".." in parts:
        return "the member name escapes the archive with '..'"
    if posixpath.isabs(posixpath.normpath(normalized)):
        return "the member name resolves outside the archive"
    return ""


def _zip_members(archive: zipfile.ZipFile) -> list[Member]:
    members: list[Member] = []
    for info in archive.infolist():
        refusal = _unsafe_name(info.filename)
        # The high 16 bits of `external_attr` are the Unix mode on archives written by Unix tools;
        # S_IFLNK there is a symlink member, which must never be followed or written out.
        mode = (info.external_attr >> 16) & 0o170000
        if not refusal and mode == 0o120000:
            refusal = "the member is a symbolic link"
        if not refusal and info.file_size > MAX_ARCHIVE_MEMBER_BYTES:
            refusal = f"the member is {info.file_size / (1024 * 1024):.0f} MB, over the {MAX_ARCHIVE_MEMBER_BYTES // (1024 * 1024)} MB per-member limit"
        member = Member(
            name=info.filename,
            size=int(info.file_size),
            compressed=int(info.compress_size),
            is_dir=info.is_dir(),
            refusal=refusal,
        )
        if not refusal and not member.is_dir and member.compressed > 0 and member.ratio > MAX_ARCHIVE_EXPANSION_RATIO:
            member = Member(
                member.name,
                member.size,
                member.compressed,
                member.is_dir,
                f"the member expands {member.ratio:.0f}x, over the {MAX_ARCHIVE_EXPANSION_RATIO}x limit (a decompression bomb)",
            )
        members.append(member)
    return members


def _bsdtar_available() -> bool:
    """RAR needs an external decoder, so it needs confinement too; unconfinable is unavailable."""
    return os.access(BSDTAR, os.X_OK) and _sandbox.confinement_available()


def _rar_members(path: Path, *, scratch: Path) -> list[Member]:
    result = run_confined([BSDTAR, "-tvf", str(path)], scratch=scratch, fmt="rar")
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", "replace").strip()
        lowered = detail.lower()
        if "passphrase" in lowered or "password" in lowered or "encrypted" in lowered:
            raise ReaderRefused(
                "The RAR archive is password-protected; its contents were not read.",
                code="password_protected",
                remediation="Remove the password, or attach an unprotected archive.",
                fmt="rar",
            )
        raise ReaderRefused(
            "The RAR archive could not be listed; it may be corrupt or use an unsupported RAR feature.",
            code="rar_unreadable",
            remediation="Re-create the archive, or attach a ZIP instead.",
            fmt="rar",
        )
    members: list[Member] = []
    for line in result.stdout.decode("utf-8", "replace").splitlines():
        # `-tvf` prints `mode links owner group size date time name`; the name is what remains
        # after the first eight whitespace-separated fields.
        fields = line.split(maxsplit=8)
        if len(fields) < 9:
            continue
        mode, name = fields[0], fields[8]
        try:
            size = int(fields[4])
        except ValueError:
            continue
        if " -> " in name:  # libarchive renders a link member as "name -> target"
            members.append(Member(name.split(" -> ", 1)[0], size, size, False, "the member is a symbolic link"))
            continue
        refusal = _unsafe_name(name)
        if not refusal and mode.startswith("l"):
            refusal = "the member is a symbolic link"
        if not refusal and size > MAX_ARCHIVE_MEMBER_BYTES:
            refusal = f"the member is {size / (1024 * 1024):.0f} MB, over the {MAX_ARCHIVE_MEMBER_BYTES // (1024 * 1024)} MB per-member limit"
        members.append(Member(name.rstrip("/"), size, size, mode.startswith("d") or name.endswith("/"), refusal))
    return members


def _wanted(members: list[Member], question: str, limit: int) -> list[Member]:
    """Which members to actually read: the ones the question names, then small textual ones.

    Deterministic and stated: the selection is reported on the result, so "why did it read those
    four" always has an answer that does not depend on a model's mood.
    """
    terms = {token.lower() for token in str(question or "").replace("/", " ").replace("\\", " ").split() if len(token) > 2}
    readable = [m for m in members if not m.is_dir and not m.refusal and m.size > 0]

    def named(member: Member) -> bool:
        lowered = member.name.lower()
        base = posixpath.basename(lowered)
        return any(term in lowered or term in base or base.startswith(term) for term in terms)

    def textual(member: Member) -> bool:
        return posixpath.splitext(member.name)[1].lower() in _TEXTUAL_SUFFIXES

    chosen: list[Member] = []
    for predicate in (lambda m: named(m) and textual(m), named, textual):
        for member in readable:
            if member not in chosen and predicate(member):
                chosen.append(member)
            if len(chosen) >= limit:
                return chosen
    return chosen


def _decode(payload: bytes, name: str) -> tuple[str, str]:
    if b"\x00" in payload[:8192]:
        return "", "binary content, not shown"
    try:
        return payload.decode("utf-8"), ""
    except UnicodeDecodeError:
        return payload.decode("utf-8", "replace"), "not valid UTF-8; undecodable bytes shown as �"


def read(
    data: bytes,
    *,
    name: str = "archive.zip",
    question: str = "",
    max_read_members: int = MAX_ARCHIVE_READ_MEMBERS,
) -> ReaderResult:
    kind = sniff(data)
    if not kind:
        raise ReaderRefused(f"{name} is not a ZIP or RAR archive.", code="not_an_archive", fmt="archive")
    digest = hashlib.sha256(data).hexdigest()
    warnings: list[str] = []
    omitted: list[str] = []

    if kind == "rar":
        require_confinement("rar")
    with Scratch("vool-archive-") as scratch, extraction_budget(scratch=scratch.path):
        source = scratch.path / ("input.rar" if kind == "rar" else "input.zip")
        source.write_bytes(data)
        if kind == "zip":
            try:
                handle = zipfile.ZipFile(source)
            except (zipfile.BadZipFile, OSError) as exc:
                raise ReaderRefused(
                    f"{name} could not be opened as a ZIP; the archive is damaged.",
                    code="archive_corrupt",
                    remediation="Re-create the archive and attach it again.",
                    fmt="zip",
                ) from exc
            with handle:
                members = _zip_members(handle)
                if len(members) > MAX_ARCHIVE_MEMBERS:
                    omitted.append(f"{len(members) - MAX_ARCHIVE_MEMBERS} members beyond the first {MAX_ARCHIVE_MEMBERS:,}")
                    warnings.append(f"This archive holds {len(members):,} members; the first {MAX_ARCHIVE_MEMBERS:,} were listed.")
                    members = members[:MAX_ARCHIVE_MEMBERS]
                encrypted = [m for m in handle.infolist() if m.flag_bits & 0x1]
                if encrypted and len(encrypted) == len([m for m in handle.infolist() if not m.is_dir()]):
                    raise ReaderRefused(
                        f"{name} is password-protected; its contents were not read.",
                        code="password_protected",
                        remediation="Remove the password, or attach an unprotected archive.",
                        fmt="zip",
                    )
                if encrypted:
                    warnings.append(f"{len(encrypted)} member(s) are password-protected and were not read.")
                encrypted_names = {m.filename for m in encrypted}
                chosen = [m for m in _wanted(members, question, max_read_members) if m.name not in encrypted_names]
                contents = _read_zip_contents(handle, chosen, warnings)
        else:
            if not _bsdtar_available():
                raise ReaderUnavailable(
                    f"{name} is a RAR archive and this machine has no RAR decoder, so it was not read.",
                    code="rar_decoder_unavailable",
                    remediation="Attach a ZIP archive instead, or install a RAR-capable libarchive build.",
                    fmt="rar",
                )
            members = _rar_members(source, scratch=scratch.path)
            if len(members) > MAX_ARCHIVE_MEMBERS:
                omitted.append(f"{len(members) - MAX_ARCHIVE_MEMBERS} members beyond the first {MAX_ARCHIVE_MEMBERS:,}")
                members = members[:MAX_ARCHIVE_MEMBERS]
            chosen = _wanted(members, question, max_read_members)
            contents = _read_rar_contents(source, chosen, scratch=scratch.path, warnings=warnings)

    units: list[ReaderUnit] = []
    listing_lines = [f"{len(members)} member(s) in {name}:"]
    total_size = 0
    nested: list[str] = []
    for member in members:
        total_size += member.size
        suffix = posixpath.splitext(member.name)[1].lower()
        if suffix in _NESTED_SUFFIXES and not member.is_dir:
            nested.append(member.name)
        marker = "  " if not member.refusal else "  REFUSED: "
        shape = "directory" if member.is_dir else f"{member.size:,} bytes"
        listing_lines.append(f"{marker}{member.name} ({shape})" + (f" — {member.refusal}" if member.refusal else ""))
    read_names = {member.name for member, _ in contents}
    unread = [m.name for m in members if not m.is_dir and not m.refusal and m.name not in read_names]
    if unread:
        listing_lines.append("")
        listing_lines.append(f"{len(unread)} member(s) were listed but NOT read: " + ", ".join(unread[:40]) + ("…" if len(unread) > 40 else ""))
        omitted.extend(f"member {n}" for n in unread)
    if nested:
        warnings.append(f"{len(nested)} nested archive(s) were listed but not expanded: " + ", ".join(nested[:10]))
    units.append(
        ReaderUnit(
            locator="archive listing",
            kind=UNIT_MEMBER,
            text="\n".join(listing_lines),
            meta={"members": len(members), "total_uncompressed_bytes": total_size, "read": len(contents), "unread": len(unread)},
        )
    )
    for member, text in contents:
        note: list[str] = []
        if len(text) > MAX_UNIT_CHARS:
            text = text[:MAX_UNIT_CHARS]
            note.append(f"cut at {MAX_UNIT_CHARS:,} characters")
        units.append(
            ReaderUnit(
                locator=f"member {member.name}",
                kind=UNIT_MEMBER,
                text=text,
                warnings=tuple(note),
                meta={"member": member.name, "size_bytes": member.size},
            )
        )
    refused = [m for m in members if m.refusal]
    if refused:
        warnings.append(f"{len(refused)} member(s) were refused and never extracted; each is named in the listing with its reason.")
    return ReaderResult(
        fmt=kind,
        extractor=("vool.zip 1.0.0 (stdlib zipfile)" if kind == "zip" else "libarchive via bsdtar, vool.rar 1.0.0"),
        units=tuple(units),
        warnings=tuple(warnings),
        omitted=tuple(omitted),
        source_sha256=digest,
        meta={
            "members": len(members),
            "members_read": len(contents),
            "members_refused": len(refused),
            "nested_archives": nested,
            "total_uncompressed_bytes": total_size,
            "selection": [m.name for m, _ in contents],
        },
    )


def _read_zip_contents(handle: zipfile.ZipFile, chosen: list[Member], warnings: list[str]) -> list[tuple[Member, str]]:
    """Decompress the chosen members, counting real bytes as they arrive.

    The count is what matters: a ZIP header may declare any ``file_size`` it likes, so the ratio
    check on the declared value is a fast reject, not the guarantee. The guarantee is that the
    stream is read in chunks and abandoned the moment it exceeds what it promised.
    """
    produced = 0
    out: list[tuple[Member, str]] = []
    for member in chosen:
        ceiling = min(MAX_ARCHIVE_MEMBER_BYTES, MAX_ARCHIVE_TOTAL_BYTES - produced)
        if ceiling <= 0:
            warnings.append(f"The {MAX_ARCHIVE_TOTAL_BYTES // (1024 * 1024)} MB extraction budget was reached; {member.name} was not read.")
            break
        try:
            with handle.open(member.name) as stream:
                payload = stream.read(ceiling + 1)
        except (RuntimeError, zipfile.BadZipFile, OSError) as exc:
            message = str(exc).lower()
            reason = "it is password-protected" if "password" in message or "encrypted" in message else "it could not be decompressed"
            warnings.append(f"{member.name} was not read: {reason}.")
            continue
        if len(payload) > ceiling:
            warnings.append(
                f"{member.name} produced more bytes than its header declared ({member.size:,}); it was abandoned as a decompression bomb."
            )
            continue
        produced += len(payload)
        text, note = _decode(payload, member.name)
        if note and not text:
            warnings.append(f"{member.name} was not shown: {note}.")
            continue
        if note:
            warnings.append(f"{member.name}: {note}.")
        out.append((member, text))
    return out


def _read_rar_contents(source: Path, chosen: list[Member], *, scratch: Path, warnings: list[str]) -> list[tuple[Member, str]]:
    """One member at a time to stdout, so a member is never written into the filesystem at all."""
    out: list[tuple[Member, str]] = []
    produced = 0
    for member in chosen:
        if produced >= MAX_ARCHIVE_TOTAL_BYTES:
            warnings.append(f"The extraction budget was reached; {member.name} was not read.")
            break
        result = run_confined([BSDTAR, "-xOf", str(source), "--", member.name], scratch=scratch, fmt="rar")
        if result.returncode != 0:
            warnings.append(f"{member.name} could not be extracted from the RAR archive.")
            continue
        payload = result.stdout[: MAX_ARCHIVE_MEMBER_BYTES + 1]
        if len(payload) > MAX_ARCHIVE_MEMBER_BYTES:
            warnings.append(f"{member.name} exceeded the per-member limit and was abandoned.")
            continue
        produced += len(payload)
        text, note = _decode(payload, member.name)
        if note and not text:
            warnings.append(f"{member.name} was not shown: {note}.")
            continue
        if note:
            warnings.append(f"{member.name}: {note}.")
        out.append((member, text))
    return out


def decoders_available() -> dict[str, bool]:
    return {"zip": True, "rar": _bsdtar_available()}


__all__ = ["Member", "decoders_available", "json", "read", "sniff"]
