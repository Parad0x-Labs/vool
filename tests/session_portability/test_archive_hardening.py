"""Safe archive parsing: hostile containers are refused BEFORE any member body is read.

Every structural danger — duplicate/normalizing-colliding names, absolute paths, traversal,
backslashes, symlinks/special members, unsupported compression, zip-encryption flags,
excessive member count, oversized members, expanded-size bombs and compression-ratio bombs —
is refused with a typed error. Encrypted envelopes bound their KDF parameters too.
"""

from __future__ import annotations

import json
import zipfile

import pytest

from tests.session_portability import support
from tests.session_portability.support import SESSION


@pytest.fixture(autouse=True)
def _seeded_home():
    support.seed_turns()
    support.seed_tool_receipt()


def _export(tmp_path, name="plain.voolsession"):
    from core.session_portability import api

    out = tmp_path / name
    api.export_session(SESSION, out)
    return out


def _rewrite(src, out, transform):
    with zipfile.ZipFile(src) as zf:
        infos = zf.infolist()
        members = [(info, zf.read(info.filename)) for info in infos]
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        transform(zf, infos, members)


def _expect_unsafe(path, code="BUNDLE_ARCHIVE_UNSAFE"):
    from core.session_portability import api

    with pytest.raises(api.PortabilityRefused) as err:
        api.inspect_bundle(path)
    assert err.value.code == code, f"expected {code}, got {err.value.code}: {err.value.message}"


def test_duplicate_member_names_refuse(tmp_path):
    src = _export(tmp_path)
    out = tmp_path / "dup.voolsession"

    def transform(zf, infos, members):
        for info, data in members:
            zf.writestr(info, data)
        zf.writestr("bundle.json", members[0][1])

    _rewrite(src, out, transform)
    _expect_unsafe(out)


def test_normalization_colliding_names_refuse(tmp_path):
    src = _export(tmp_path)
    out = tmp_path / "nfc.voolsession"

    def transform(zf, infos, members):
        for info, data in members:
            zf.writestr(info, data)
        # Two members whose NFC normalizations collide with EACH OTHER:
        # "cafe" + combining acute U+0301 vs the precomposed "é" (U+00E9).
        zf.writestr("attachments/cafe\u0301.txt", b"decomposed form")
        zf.writestr("attachments/caf\u00e9.txt", b"precomposed form")

    _rewrite(src, out, transform)
    _expect_unsafe(out)


def test_absolute_and_traversal_names_refuse(tmp_path):
    src = _export(tmp_path)
    for victim in ("/etc/passwd", "../escape.json", "attachments\\..\\win.txt", "a/../../b.json"):
        out = tmp_path / ("trav-" + victim.replace("/", "_").replace("\\", "_") + ".voolsession")

        def transform(zf, infos, members, victim=victim):
            for info, data in members:
                zf.writestr(info, data)
            zf.writestr(victim, b"hostile")

        _rewrite(src, out, transform)
        _expect_unsafe(out)


def test_symlink_and_special_members_refuse(tmp_path):
    src = _export(tmp_path)
    out = tmp_path / "link.voolsession"

    def transform(zf, infos, members):
        for info, data in members:
            zf.writestr(info, data)
        info = zipfile.ZipInfo("attachments/link.bin")
        info.create_system = 3
        info.external_attr = (0o120777 << 16)  # S_IFLNK
        zf.writestr(info, "/etc/passwd")

    _rewrite(src, out, transform)
    _expect_unsafe(out)

    out2 = tmp_path / "device.voolsession"

    def transform2(zf, infos, members):
        for info, data in members:
            zf.writestr(info, data)
        info = zipfile.ZipInfo("attachments/dev.bin")
        info.create_system = 3
        info.external_attr = (0o020600 << 16)  # character device
        zf.writestr(info, b"")

    _rewrite(src, out2, transform2)
    _expect_unsafe(out2)


def test_unsupported_compression_refuses(tmp_path):
    src = _export(tmp_path)
    out = tmp_path / "bzip.voolsession"

    def transform(zf, infos, members):
        for info, data in members:
            zf.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED)
        zf.writestr("attachments/extra.bin", b"abc", compress_type=zipfile.ZIP_BZIP2)

    _rewrite(src, out, transform)
    _expect_unsafe(out)


def test_member_count_bound_refuses(tmp_path, monkeypatch):
    from core.session_portability import bundle as bundle_format

    monkeypatch.setattr(bundle_format, "ARCHIVE_MAX_MEMBERS", 3)
    src = _export(tmp_path)
    out = tmp_path / "many.voolsession"

    def transform(zf, infos, members):
        for info, data in members:
            zf.writestr(info, data)
        for i in range(5):
            zf.writestr(f"attachments/filler{i}.bin", b"x")

    _rewrite(src, out, transform)
    _expect_unsafe(out)


def test_oversized_member_and_expanded_total_refuse(tmp_path, monkeypatch):
    from core.session_portability import bundle as bundle_format

    monkeypatch.setattr(bundle_format, "ARCHIVE_MAX_MEMBER_BYTES", 16)
    src = _export(tmp_path)
    out = tmp_path / "big.voolsession"

    def transform(zf, infos, members):
        for info, data in members:
            zf.writestr(info, data)
        zf.writestr("attachments/big.bin", b"y" * 128)

    _rewrite(src, out, transform)
    _expect_unsafe(out)


def test_compression_ratio_bomb_refuses(tmp_path, monkeypatch):
    from core.session_portability import bundle as bundle_format

    monkeypatch.setattr(bundle_format, "ARCHIVE_MAX_RATIO", 20)
    src = _export(tmp_path)
    out = tmp_path / "bomb.voolsession"

    def transform(zf, infos, members):
        for info, data in members:
            zf.writestr(info, data)
        # 2 MiB of zeros compresses to a few KB — far over the ratio cap, and above the
        # 1 MiB small-member floor where ratios are meaningless.
        zf.writestr("attachments/zeros.bin", bytes(2 * 1024 * 1024), compress_type=zipfile.ZIP_DEFLATED)

    _rewrite(src, out, transform)
    _expect_unsafe(out)


def test_zip_encryption_flag_refuses(tmp_path):
    """Python's zip writer clears the encryption bit on write, so the hostile flag is set
    directly in the raw local + central headers of the finished archive."""
    import struct

    src = _export(tmp_path)
    out = tmp_path / "zipcrypt.voolsession"
    victim = "attachments/enc.bin"
    with zipfile.ZipFile(src) as zf:
        members = [(info.filename, zf.read(info.filename)) for info in zf.infolist()]
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in members:
            zf.writestr(name, data)
        zf.writestr(victim, b"ciphertext")

    raw = bytearray(out.read_bytes())
    patched = 0

    def patch_headers(header_sig: bytes, flag_offset: int, name_offset: int, name_len_offset: int):
        nonlocal patched
        pos = 0
        while True:
            pos = raw.find(header_sig, pos)
            if pos < 0:
                return
            name_len = struct.unpack_from("<H", raw, pos + name_len_offset)[0]
            name = bytes(raw[pos + name_offset : pos + name_offset + name_len])
            if name == victim.encode():
                struct.pack_into("<H", raw, pos + flag_offset, 0x0001)
                patched += 1
            pos += 4

    patch_headers(b"PK\x03\x04", 6, 30, 26)  # local file header
    patch_headers(b"PK\x01\x02", 8, 46, 28)  # central directory entry
    assert patched >= 2, "expected to patch both the local and central headers"
    out.write_bytes(bytes(raw))
    _expect_unsafe(out)


def test_unknown_member_names_refuse(tmp_path):
    src = _export(tmp_path)
    out = tmp_path / "smuggled.voolsession"

    def transform(zf, infos, members):
        for info, data in members:
            zf.writestr(info, data)
        zf.writestr("innocent.txt", b"smuggled member")

    _rewrite(src, out, transform)
    _expect_unsafe(out)


def test_envelope_kdf_bounds_refuse(tmp_path):
    """Absurd KDF parameters are refused BEFORE any key derivation: an envelope that would
    burn minutes of CPU per attempt is itself the denial-of-service."""
    from core.session_portability import api


    enc = tmp_path / "enc.voolsession"
    api.export_session(SESSION, enc, passphrase="kdf-probe")
    envelope = json.loads(enc.read_text())
    envelope["kdf"]["iterations"] = 250_000_000
    hostile = tmp_path / "hostile-kdf.voolsession"
    hostile.write_text(json.dumps(envelope))

    with pytest.raises(api.PortabilityRefused) as err:
        api.inspect_bundle(hostile, passphrase="anything")
    assert err.value.code == "BUNDLE_ARCHIVE_UNSAFE"

    enc2 = tmp_path / "enc2.voolsession"
    api.export_session(SESSION, enc2, passphrase="kdf-probe")
    envelope2 = json.loads(enc2.read_text())
    envelope2["nonce"] = "00" * 4  # 4-byte nonce: outside the AES-GCM norm
    hostile2 = tmp_path / "hostile-nonce.voolsession"
    hostile2.write_text(json.dumps(envelope2))
    with pytest.raises(api.PortabilityRefused) as err:
        api.inspect_bundle(hostile2, passphrase="anything")
    assert err.value.code == "BUNDLE_ARCHIVE_UNSAFE"
