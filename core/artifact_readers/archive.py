"""ZIP, RAR, 7z, TAR and compressed streams: a listing, and the content of members actually asked for.

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

RAR and 7z are not formats with a standard-library decoder. Where ``libarchive``
(``/usr/bin/bsdtar``) is present it is used, sandboxed; where it is not, they are BLOCKED and say so.

TAR (plain, gzip, bzip2 or xz compressed) and a single compressed file (``notes.log.gz``) are read in
process by the standard library, streamed and never written to disk. A compressed stream has no
per-member ratio to check, so the WHOLE stream is bounded instead: decompressed bytes are counted as
they arrive and the read is abandoned past the expansion ratio or the total budget, whichever comes
first -- a lying header cannot change what is counted.
"""

from __future__ import annotations

import bz2
import gzip
import hashlib
import io
import json
import lzma
import os
import posixpath
import tarfile
import zipfile
import zlib
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
from ._types import UNIT_MEMBER, ReaderError, ReaderRefused, ReaderResult, ReaderUnavailable, ReaderUnit

BSDTAR = "/usr/bin/bsdtar"
ZIP_MAGIC = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")
RAR4_MAGIC = b"Rar!\x1a\x07\x00"
RAR5_MAGIC = b"Rar!\x1a\x07\x01\x00"
SEVEN_ZIP_MAGIC = b"7z\xbc\xaf\x27\x1c"
GZIP_MAGIC = b"\x1f\x8b"
BZIP2_MAGIC = b"BZh"
XZ_MAGIC = b"\xfd7zXZ\x00"
#: Below this many decompressed bytes a compressed stream is never called a bomb, whatever its
#: ratio. A tar of three small files is mostly zero padding and compresses far past any sane ratio;
#: refusing it would refuse ordinary archives to protect against nothing.
COMPRESSED_RATIO_FLOOR_BYTES = 4 * 1024 * 1024
#: Formats read through the confined libarchive decoder, and what each is called to a person.
_LIBARCHIVE_FORMATS = {"rar": "RAR", "7z": "7z"}
#: Formats read in process from a (possibly compressed) stream.
STREAM_FORMATS = frozenset({"tar", "gz", "bz2", "xz"})
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
    if head.startswith(SEVEN_ZIP_MAGIC):
        return "7z"
    if _compression(data):
        return "compressed"
    if _is_tar_header(bytes(data[:512])):
        return "tar"
    return ""


def _compression(data: bytes) -> str:
    """'gz', 'bz2' or 'xz' when the bytes open a compressed stream; '' otherwise."""
    head = bytes(data[:6])
    if head.startswith(GZIP_MAGIC) and len(data) > 10 and data[2] == 8:  # 8 = deflate, the only gzip method
        return "gz"
    if head.startswith(BZIP2_MAGIC) and len(head) > 3 and 0x31 <= head[3] <= 0x39:
        return "bz2"
    if head.startswith(XZ_MAGIC):
        return "xz"
    return ""


def _is_tar_header(block: bytes) -> bool:
    """A real tar header: the checksum the format defines must hold, not merely a magic string."""
    if len(block) < 512 or not block.strip(b"\x00"):
        return False
    try:
        tarfile.TarInfo.frombuf(block, "utf-8", "surrogateescape")
    except tarfile.HeaderError:
        return False
    return True


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


def _libarchive_members(path: Path, *, scratch: Path, fmt: str = "rar") -> list[Member]:
    label = _LIBARCHIVE_FORMATS.get(fmt, fmt.upper())
    result = run_confined([BSDTAR, "-tvf", str(path)], scratch=scratch, fmt=fmt)
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", "replace").strip()
        lowered = detail.lower()
        if "passphrase" in lowered or "password" in lowered or "encrypted" in lowered:
            raise ReaderRefused(
                f"The {label} archive is password-protected; its contents were not read.",
                code="password_protected",
                remediation="Remove the password, or attach an unprotected archive.",
                fmt=fmt,
            )
        raise ReaderRefused(
            f"The {label} archive could not be listed; it may be corrupt or use an unsupported {label} feature.",
            code=f"{fmt}_unreadable",
            remediation="Re-create the archive, or attach a ZIP instead.",
            fmt=fmt,
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
        raise ReaderRefused(f"{name} is not a ZIP, RAR, 7z or TAR archive.", code="not_an_archive", fmt="archive")
    digest = hashlib.sha256(data).hexdigest()
    warnings: list[str] = []
    omitted: list[str] = []
    extractor = ""

    if kind in {"tar", "compressed"}:
        return _read_stream_archive(data, name=name, question=question, max_read_members=max_read_members, digest=digest)
    if kind in _LIBARCHIVE_FORMATS:
        require_confinement(kind)
    with Scratch("vool-archive-") as scratch, extraction_budget(scratch=scratch.path):
        source = scratch.path / f"input.{kind}"
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
            label = _LIBARCHIVE_FORMATS[kind]
            if not _bsdtar_available():
                raise ReaderUnavailable(
                    f"{name} is a {label} archive and this machine has no {label} decoder, so it was not read.",
                    code=f"{kind}_decoder_unavailable",
                    remediation=f"Attach a ZIP archive instead, or install a {label}-capable libarchive build.",
                    fmt=kind,
                )
            members = _libarchive_members(source, scratch=scratch.path, fmt=kind)
            if len(members) > MAX_ARCHIVE_MEMBERS:
                omitted.append(f"{len(members) - MAX_ARCHIVE_MEMBERS} members beyond the first {MAX_ARCHIVE_MEMBERS:,}")
                members = members[:MAX_ARCHIVE_MEMBERS]
            chosen = _wanted(members, question, max_read_members)
            contents = _read_libarchive_contents(source, chosen, scratch=scratch.path, warnings=warnings, fmt=kind)
            extractor = f"libarchive via bsdtar, vool.{kind} 1.0.0"

    return _archive_result(
        kind=kind,
        name=name,
        members=members,
        contents=contents,
        warnings=warnings,
        omitted=omitted,
        digest=digest,
        extractor=extractor or "vool.zip 1.0.0 (stdlib zipfile)",
    )


def _archive_result(
    *,
    kind: str,
    name: str,
    members: list[Member],
    contents: list[tuple[Member, str]],
    warnings: list[str],
    omitted: list[str],
    digest: str,
    extractor: str,
    listed_note: str = "",
) -> ReaderResult:
    """The listing unit, one unit per member read, and the result -- the same shape for every container."""
    units: list[ReaderUnit] = []
    listing_lines = [f"{len(members)} member(s) in {name}{listed_note}:"]
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
        extractor=extractor,
        units=tuple(units),
        warnings=tuple(warnings),
        omitted=tuple(omitted),
        source_sha256=digest,
        meta={
            "members": len(members),
            "members_read": len(contents),
            "members_refused": len(refused),
            "nested_archives": nested,
            # Every listed file member by its in-archive path. Routing reads this to recognise a
            # path the user typed as a member of the attachment rather than a file on this disk.
            "member_names": [m.name for m in members if not m.is_dir],
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


def _read_libarchive_contents(
    source: Path, chosen: list[Member], *, scratch: Path, warnings: list[str], fmt: str = "rar"
) -> list[tuple[Member, str]]:
    """One member at a time to stdout, so a member is never written into the filesystem at all."""
    label = _LIBARCHIVE_FORMATS.get(fmt, fmt.upper())
    out: list[tuple[Member, str]] = []
    produced = 0
    for member in chosen:
        if produced >= MAX_ARCHIVE_TOTAL_BYTES:
            warnings.append(f"The extraction budget was reached; {member.name} was not read.")
            break
        result = run_confined([BSDTAR, "-xOf", str(source), "--", member.name], scratch=scratch, fmt=fmt)
        if result.returncode != 0:
            lowered = result.stderr.decode("utf-8", "replace").lower()
            reason = "it is password-protected" if ("passphrase" in lowered or "encrypted" in lowered) else f"it could not be extracted from the {label} archive"
            warnings.append(f"{member.name} was not read: {reason}.")
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


# --- TAR and single compressed streams, in process ---------------------------------------------


class _Expanded(Exception):
    """A compressed stream produced more than it is allowed to. Carries the refusal to raise."""

    def __init__(self, refusal: ReaderError) -> None:
        super().__init__(refusal.message)
        self.refusal = refusal


class _BoundedStream(io.RawIOBase):
    """Decompressed bytes, counted as they arrive and abandoned past the ceiling.

    The ceiling is the smaller of the absolute budget and ``ratio x compressed size`` (above a small
    floor), so a 80 KB gzip of zeros is refused after a few MB instead of after 256 MB. The extraction
    deadline is checked every chunk, so a slow, pathological stream cannot outlive the attachment's
    time budget either.
    """

    def __init__(self, raw: io.BufferedIOBase, *, compressed_size: int, limit: int, fmt: str, name: str) -> None:
        super().__init__()
        self._raw = raw
        self._fmt = fmt
        self._name = name
        self.produced = 0
        ratio_ceiling = max(COMPRESSED_RATIO_FLOOR_BYTES, MAX_ARCHIVE_EXPANSION_RATIO * max(1, compressed_size))
        self._ratio_ceiling = ratio_ceiling
        self._limit = limit

    def readable(self) -> bool:
        return True

    def readinto(self, buffer) -> int:  # type: ignore[override]
        budget = _sandbox.current_budget()
        if budget is not None:
            budget.check(self._fmt)
        chunk = self._raw.read(len(buffer))
        size = len(chunk)
        self.produced += size
        if self.produced > self._ratio_ceiling and self._ratio_ceiling < self._limit:
            raise _Expanded(
                ReaderRefused(
                    f"{self._name} expands more than {MAX_ARCHIVE_EXPANSION_RATIO}x its compressed size "
                    "(a decompression bomb); it was not read.",
                    code="decompression_bomb",
                    remediation="Attach the files uncompressed, or as a ZIP.",
                    fmt=self._fmt,
                )
            )
        if self.produced > self._limit:
            raise _Expanded(
                ReaderRefused(
                    f"{self._name} decompresses to more than {self._limit // (1024 * 1024)} MB; it was not read.",
                    code="archive_too_large",
                    remediation="Attach a smaller archive, or only the files you need.",
                    fmt=self._fmt,
                )
            )
        buffer[:size] = chunk
        return size


def _decompressor(data: bytes, compression: str) -> io.BufferedIOBase:
    source = io.BytesIO(data)
    if compression == "gz":
        return gzip.GzipFile(fileobj=source, mode="rb")
    if compression == "bz2":
        return bz2.BZ2File(source, mode="rb")
    if compression == "xz":
        return lzma.LZMAFile(source, mode="rb")
    return source


def _open_stream(data: bytes, *, compression: str, limit: int, fmt: str, name: str) -> io.BufferedReader:
    bounded = _BoundedStream(_decompressor(data, compression), compressed_size=len(data), limit=limit, fmt=fmt, name=name)
    return io.BufferedReader(bounded, buffer_size=64 * 1024)


#: What a damaged or truncated stream raises from the stdlib decompressors and tarfile.
_STREAM_ERRORS = (OSError, EOFError, lzma.LZMAError, zlib.error, tarfile.TarError, ValueError)


def _damaged(name: str, fmt: str, label: str) -> ReaderRefused:
    return ReaderRefused(
        f"{name} could not be read as {label}; the file is damaged or truncated.",
        code="archive_corrupt",
        remediation="Re-create the archive and attach it again.",
        fmt=fmt,
    )


def _gzip_original_name(data: bytes) -> str:
    """The FNAME field of a gzip header, when the writer recorded one. Only ever used as a label."""
    if len(data) < 10 or not data.startswith(GZIP_MAGIC) or not data[3] & 0x08:
        return ""
    offset = 10
    if data[3] & 0x04:  # FEXTRA
        if len(data) < 12:
            return ""
        offset += 2 + int.from_bytes(data[10:12], "little")
    end = data.find(b"\x00", offset, offset + 1024)
    if end <= offset:
        return ""
    return data[offset:end].decode("latin-1")


def _tar_member(info: tarfile.TarInfo) -> Member:
    name = info.name
    refusal = _unsafe_name(name)
    if not refusal and (info.issym() or info.islnk()):
        refusal = "the member is a link" + (f" to {info.linkname}" if info.linkname else "")
    if not refusal and (info.ischr() or info.isblk() or info.isfifo()):
        refusal = "the member is a device or special file"
    if not refusal and info.isfile() and info.size > MAX_ARCHIVE_MEMBER_BYTES:
        refusal = f"the member is {info.size / (1024 * 1024):.0f} MB, over the {MAX_ARCHIVE_MEMBER_BYTES // (1024 * 1024)} MB per-member limit"
    return Member(name.rstrip("/"), int(info.size), int(info.size), info.isdir(), refusal)


def _read_stream_archive(data: bytes, *, name: str, question: str, max_read_members: int, digest: str) -> ReaderResult:
    """TAR (any of the stdlib compressions) or one compressed file. Nothing is written to disk."""
    compression = _compression(data)
    with extraction_budget():
        # Is there a tar inside? One header block decides, read through the same bound.
        try:
            head = _open_stream(data, compression=compression, limit=MAX_ARCHIVE_TOTAL_BYTES, fmt="tar", name=name).read(512)
        except _Expanded as exc:
            raise exc.refusal from None
        except _STREAM_ERRORS as exc:
            raise _damaged(name, compression or "tar", f"a {compression or 'tar'} stream") from exc
        if _is_tar_header(head):
            return _read_tar(data, compression=compression, name=name, question=question, max_read_members=max_read_members, digest=digest)
        if not compression:
            raise _damaged(name, "tar", "a TAR archive")
        return _read_single_compressed(data, compression=compression, name=name, digest=digest)


def _read_tar(data: bytes, *, compression: str, name: str, question: str, max_read_members: int, digest: str) -> ReaderResult:
    warnings: list[str] = []
    omitted: list[str] = []
    members: list[Member] = []
    total_members = 0
    # Pass 1: list. Streaming mode ("r|") never seeks, so the bound sees every decompressed byte.
    try:
        with tarfile.open(fileobj=_open_stream(data, compression=compression, limit=MAX_ARCHIVE_TOTAL_BYTES, fmt="tar", name=name), mode="r|") as handle:
            for info in handle:
                total_members += 1
                if len(members) < MAX_ARCHIVE_MEMBERS:
                    members.append(_tar_member(info))
    except _Expanded as exc:
        raise exc.refusal from None
    except _STREAM_ERRORS as exc:
        if not members:
            raise _damaged(name, "tar", "a TAR archive") from exc
        warnings.append(f"The archive ends early or is damaged after {len(members)} member(s); the rest could not be listed.")
    if total_members > MAX_ARCHIVE_MEMBERS:
        omitted.append(f"{total_members - MAX_ARCHIVE_MEMBERS} members beyond the first {MAX_ARCHIVE_MEMBERS:,}")
        warnings.append(f"This archive holds {total_members:,} members; the first {MAX_ARCHIVE_MEMBERS:,} were listed.")
    chosen = _wanted(members, question, max_read_members)
    wanted = {member.name: member for member in chosen}
    found: dict[str, str] = {}
    # Pass 2: read only the chosen regular files, into memory, each under its own ceiling.
    produced = 0
    if wanted:
        try:
            with tarfile.open(fileobj=_open_stream(data, compression=compression, limit=MAX_ARCHIVE_TOTAL_BYTES, fmt="tar", name=name), mode="r|") as handle:
                for info in handle:
                    key = info.name.rstrip("/")
                    if key not in wanted or key in found or not info.isfile():
                        continue
                    ceiling = min(MAX_ARCHIVE_MEMBER_BYTES, MAX_ARCHIVE_TOTAL_BYTES - produced)
                    if ceiling <= 0:
                        warnings.append(f"The extraction budget was reached; {key} was not read.")
                        break
                    stream = handle.extractfile(info)
                    payload = stream.read(ceiling + 1) if stream is not None else b""
                    if len(payload) > ceiling:
                        warnings.append(f"{key} exceeded the per-member limit and was abandoned.")
                        continue
                    produced += len(payload)
                    text, note = _decode(payload, key)
                    if note and not text:
                        warnings.append(f"{key} was not shown: {note}.")
                        found[key] = ""
                        continue
                    if note:
                        warnings.append(f"{key}: {note}.")
                    found[key] = text
                    if len(found) >= len(wanted):
                        break
        except _Expanded as exc:
            raise exc.refusal from None
        except _STREAM_ERRORS:
            warnings.append("The archive is damaged partway through; members after the damage were not read.")
    contents = [(wanted[key], text) for key, text in found.items() if text]
    label = {"gz": "gzip", "bz2": "bzip2", "xz": "xz"}.get(compression, "")
    return _archive_result(
        kind="tar",
        name=name,
        members=members,
        contents=contents,
        warnings=warnings,
        omitted=omitted,
        digest=digest,
        extractor="vool.tar 1.0.0 (stdlib tarfile" + (f", {label}" if label else "") + ", streamed, never written to disk)",
    )


def _read_single_compressed(data: bytes, *, compression: str, name: str, digest: str) -> ReaderResult:
    """One file compressed on its own (``server.log.gz``): decompressed in memory, bounded, decoded if text."""
    stem = posixpath.basename(_gzip_original_name(data).replace("\\", "/")) if compression == "gz" else ""
    if not stem:
        base = posixpath.basename(name.replace("\\", "/"))
        stem = base.rsplit(".", 1)[0] if "." in base else base
    stem = stem or "content"
    warnings: list[str] = []
    try:
        payload = _open_stream(data, compression=compression, limit=MAX_ARCHIVE_MEMBER_BYTES, fmt=compression, name=name).read()
    except _Expanded as exc:
        raise exc.refusal from None
    except _STREAM_ERRORS as exc:
        raise _damaged(name, compression, f"a {compression} stream") from exc
    member = Member(stem, len(payload), len(data), False, "")
    contents: list[tuple[Member, str]] = []
    nested = sniff(payload)
    if nested:
        warnings.append(f"{stem} is itself an archive ({nested}); it was listed, not expanded.")
    else:
        text, note = _decode(payload, stem)
        if note and not text:
            warnings.append(f"{stem} was not shown: {note}.")
        else:
            if note:
                warnings.append(f"{stem}: {note}.")
            contents.append((member, text))
    label = {"gz": "gzip", "bz2": "bzip2", "xz": "xz"}[compression]
    return _archive_result(
        kind=compression,
        name=name,
        members=[member],
        contents=contents,
        warnings=warnings,
        omitted=[] if contents else [f"member {stem}"],
        digest=digest,
        extractor=f"vool.{compression} 1.0.0 (stdlib {label}, in memory)",
    )


def decoders_available() -> dict[str, bool]:
    available = _bsdtar_available()
    return {"zip": True, "tar": True, "rar": available, "7z": available}


__all__ = ["Member", "decoders_available", "json", "read", "sniff"]
