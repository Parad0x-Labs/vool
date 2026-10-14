"""TAR, compressed files and 7z: read as archives, and every hostile shape refused before it costs.

Before these readers existed, ``.tar``, ``.tar.gz``, ``.tgz``, ``.gz`` and ``.7z`` were refused at
the attachment door as unsupported. The tests below pin what each now delivers (a listing plus the
members a question points at) and, as importantly, what each refuses: decompression bombs, links,
device nodes, traversal names, truncation and member floods.
"""

from __future__ import annotations

import gzip
import io
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest

from core import artifact_readers as readers
from core.artifact_readers import archive as archive_reader
from core.artifact_readers._limits import MAX_ARCHIVE_MEMBERS
from core.artifact_readers._types import ReaderRefused


def _tar(members: dict[str, bytes], *, mode: str = "w") -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode=mode) as handle:
        for name, payload in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            handle.addfile(info, io.BytesIO(payload))
    return buffer.getvalue()


MEMBERS = {"docs/readme.txt": b"Release key phrase: IRON-8831\n", "data.csv": b"a,b\n1,2\n"}


@pytest.mark.parametrize(("name", "mode"), [("b.tar", "w"), ("b.tar.gz", "w:gz"), ("b.tgz", "w:gz"), ("b.tar.bz2", "w:bz2"), ("b.tar.xz", "w:xz")])
def test_a_tar_in_any_stdlib_compression_lists_and_reads_its_members(name, mode):
    data = _tar(MEMBERS, mode=mode)
    spec = readers.detect(data, extension=Path(name).suffix)
    assert spec is not None and spec.fmt == "tar"
    result = readers.read(data, name=name, extension=Path(name).suffix, question="readme key phrase")
    assert result.fmt == "tar"
    rendered = result.rendered_text()
    assert "2 member(s) in " + name in rendered
    assert "[member docs/readme.txt]" in rendered and "IRON-8831" in rendered
    assert result.meta["member_names"] == ["docs/readme.txt", "data.csv"]


def test_a_single_compressed_file_is_read_under_its_own_name():
    data = gzip.compress(b"service started\nincident id LEAD-9902\n")
    result = readers.read(data, name="server.log.gz", extension=".gz")
    assert result.fmt == "gz"
    assert "[member server.log]" in result.rendered_text() and "LEAD-9902" in result.rendered_text()


def test_a_compressed_stream_bomb_is_refused_on_the_ratio_long_before_the_budget():
    bomb = gzip.compress(b"\x00" * (80 * 1024 * 1024), 9)
    for name, data in (("zeros.gz", bomb), ("zeros.tar.gz", _tar({"big.bin": b"\x00" * (60 * 1024 * 1024)}, mode="w:gz"))):
        with pytest.raises(ReaderRefused) as caught:
            readers.read(data, name=name, extension=".gz")
        assert caught.value.code == "decompression_bomb"


def test_links_devices_and_escaping_names_are_listed_as_refused_and_never_read():
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as handle:
        for name, payload in (("ok.txt", b"fine\n"), ("../../etc/evil.txt", b"x"), ("/abs.txt", b"y")):
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            handle.addfile(info, io.BytesIO(payload))
        for name, kind, target in (("link", tarfile.SYMTYPE, "/etc/passwd"), ("hard", tarfile.LNKTYPE, "ok.txt"), ("dev", tarfile.CHRTYPE, "")):
            info = tarfile.TarInfo(name)
            info.type = kind
            info.linkname = target
            handle.addfile(info)
    result = readers.read(buffer.getvalue(), name="hostile.tar.gz", extension=".gz", question="evil link passwd hard dev")
    rendered = result.rendered_text()
    for name in ("../../etc/evil.txt", "/abs.txt", "link", "hard", "dev"):
        assert f"REFUSED: {name}" in rendered
    assert "root:" not in rendered and "[member ok.txt]" in rendered
    assert result.meta["members_read"] == 1 and result.meta["members_refused"] == 5


def test_a_truncated_or_garbage_stream_is_a_typed_refusal_not_a_crash():
    good = _tar(MEMBERS, mode="w:gz")
    for name, data in (("cut.tar.gz", good[: len(good) // 2]), ("junk.gz", b"\x1f\x8b\x08" + b"\x55" * 500)):
        with pytest.raises(ReaderRefused) as caught:
            readers.read(data, name=name, extension=".gz")
        assert caught.value.code == "archive_corrupt"


def test_a_member_flood_is_listed_up_to_the_bound_and_the_rest_declared():
    data = _tar({f"f/{i:05d}.txt": b"m\n" for i in range(MAX_ARCHIVE_MEMBERS + 300)}, mode="w:xz")
    result = readers.read(data, name="flood.tar.xz", extension=".xz")
    assert result.meta["members"] == MAX_ARCHIVE_MEMBERS
    assert any("300 members beyond" in entry for entry in result.omitted)


def test_bytes_decide_a_gzip_named_zip_is_not_read_as_either():
    data = _tar(MEMBERS, mode="w:gz")
    assert readers.detect(data, extension=".zip").fmt == "tar"


_bsdtar_7z = archive_reader._bsdtar_available() and shutil.which("bsdtar") is not None


@pytest.mark.skipif(not _bsdtar_7z, reason="7z is read (and here written) by the confined libarchive decoder; the format reports itself unavailable without it")
def test_a_7z_archive_lists_and_reads_its_members(tmp_path):
    source = tmp_path / "src"
    (source / "docs").mkdir(parents=True)
    (source / "docs" / "readme.txt").write_text("Release key phrase: ZINC-6648\n", encoding="utf-8")
    (source / "data.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    target = tmp_path / "bundle.7z"
    subprocess.run(["bsdtar", "--format", "7zip", "-cf", str(target), "docs/readme.txt", "data.csv"], cwd=source, check=True)
    data = target.read_bytes()
    result = readers.read(data, name="bundle.7z", extension=".7z", question="readme")
    assert result.fmt == "7z"
    assert "ZINC-6648" in result.rendered_text()
    with pytest.raises(ReaderRefused) as caught:
        readers.read(data[: len(data) // 2], name="cut.7z", extension=".7z")
    assert caught.value.code == "7z_unreadable"
