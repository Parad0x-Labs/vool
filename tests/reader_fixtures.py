"""Deterministic reader fixtures: real files, built here, byte-stable across runs.

Every fixture is *generated*, not checked in as an opaque blob, for two reasons. A generated
fixture can be read by anyone auditing the test -- the expected numbers are visible in the source
that produced them, so a test cannot quietly assert something the file does not contain. And a
generated fixture is honest about what it is: `native_text_pdf` really does carry a PDF text layer
and `scanned_pdf` really does not, so a test that claims OCR ran can only pass if OCR ran.

Nothing here needs a third-party PDF writer; the PDF bytes are assembled directly, which is also
what makes the malformed and hostile variants possible to build at all.
"""

from __future__ import annotations

import io
import struct
import zipfile
import zlib
from pathlib import Path

# --- minimal PDF assembly -------------------------------------------------------------------------


def _pdf(objects: list[bytes], *, root: int = 1) -> bytes:
    """Assemble numbered objects into a valid PDF with a correct xref table and trailer."""
    out = bytearray(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for index, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{index} 0 obj\n".encode("ascii") + body + b"\nendobj\n"
    xref_at = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode("ascii")
    out += b"0000000000 65535 f \n"
    for offset in offsets[1:]:
        out += f"{offset:010d} 00000 n \n".encode("ascii")
    out += f"trailer\n<< /Size {len(objects) + 1} /Root {root} 0 R >>\nstartxref\n{xref_at}\n%%EOF\n".encode("ascii")
    return bytes(out)


def _stream(dictionary: str, payload: bytes, *, compress: bool = True) -> bytes:
    data = zlib.compress(payload) if compress else payload
    filt = " /Filter /FlateDecode" if compress else ""
    return f"<< {dictionary} /Length {len(data)}{filt} >>\nstream\n".encode("ascii") + data + b"\nendstream"


def _escape(text: str) -> str:
    return text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


#: The quarterly table every "extracted table arithmetic" test reasons over. The numbers live
#: here once: the fixture and the expected sums cannot drift apart.
QUARTERLY_ROWS: tuple[tuple[str, int, int, int], ...] = (
    ("Northbridge", 41250, 38400, 52310),
    ("Eastgate", 27890, 31025, 29740),
    ("Southwold", 19430, 22110, 24875),
    ("Westmere", 33600, 30980, 41220),
)
QUARTERLY_HEADERS = ("Region", "Q1", "Q2", "Q3")


def quarterly_total(column: str) -> int:
    index = {"Q1": 1, "Q2": 2, "Q3": 3}[column]
    return sum(int(row[index]) for row in QUARTERLY_ROWS)


def quarterly_row_total(region: str) -> int:
    for row in QUARTERLY_ROWS:
        if row[0] == region:
            return int(row[1]) + int(row[2]) + int(row[3])
    raise KeyError(region)


def _table_lines() -> list[str]:
    widths = [12, 8, 8, 8]
    header = "".join(name.ljust(width) for name, width in zip(QUARTERLY_HEADERS, widths, strict=False))
    lines = [header]
    for row in QUARTERLY_ROWS:
        lines.append("".join(str(cell).ljust(width) for cell, width in zip(row, widths, strict=False)))
    return lines


def native_text_pdf(*, pages: int = 3, marker: str = "GRANITE-7741") -> bytes:
    """A PDF with a real text layer: a table on page 1, a unique marker on the LAST page.

    The last-page marker is what proves middle/end evidence actually reached the model: a reader
    that only delivers page 1 cannot answer for it.
    """
    objects: list[bytes] = []
    page_ids = [4 + index * 2 for index in range(pages)]
    kids = " ".join(f"{pid} 0 R" for pid in page_ids)
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {pages} >>".encode("ascii"))
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier /Encoding /WinAnsiEncoding >>")
    for index in range(pages):
        content_id = page_ids[index] + 1
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 3 0 R >> >> "
            f"/Contents {content_id} 0 R >>".encode("ascii")
        )
        if index == 0:
            lines = ["Quarterly revenue by region", "", *_table_lines()]
        elif index == pages - 1:
            lines = [f"Page {index + 1} of {pages}", "", f"Closing reference: {marker}", "Prepared by the regional desk."]
        else:
            lines = [f"Page {index + 1} of {pages}", "", "Notes continue on the following page."]
        drawn = ["BT", "/F1 11 Tf", "13 TL", "54 720 Td"]
        for line in lines:
            drawn.append(f"({_escape(line)}) Tj T*")
        drawn.append("ET")
        objects.append(_stream("", "\n".join(drawn).encode("latin-1")))
    return _pdf(objects)


def pdf_with_text(lines: list[str], *, pages: int = 1) -> bytes:
    """A one-page PDF carrying exactly the given lines in its text layer.

    Used where the CONTENT is the point of the test -- a credential canary, a placeholder -- and
    the surrounding report structure of `native_text_pdf` would only be noise.
    """
    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{' '.join(f'{4 + i * 2} 0 R' for i in range(pages))}] /Count {pages} >>".encode("ascii"),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier /Encoding /WinAnsiEncoding >>",
    ]
    for index in range(pages):
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 3 0 R >> >> "
            f"/Contents {4 + index * 2 + 1} 0 R >>".encode("ascii")
        )
        drawn = ["BT", "/F1 11 Tf", "13 TL", "54 720 Td"]
        drawn += [f"({_escape(line)}) Tj T*" for line in lines]
        drawn.append("ET")
        objects.append(_stream("", "\n".join(drawn).encode("latin-1")))
    return _pdf(objects)


def _png_bytes(width: int, height: int, rows: list[bytes]) -> bytes:
    raw = b"".join(b"\x00" + row for row in rows)
    def chunk(kind: bytes, payload: bytes) -> bytes:
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def text_image_png(lines: list[str], *, width: int = 1100, height: int = 700, point: int = 30) -> bytes:
    """Text rendered to a greyscale PNG with no text layer anywhere -- an image of words.

    Uses Pillow when it is present (sharper glyphs, better OCR), and a built-in 5x7 bitmap font
    otherwise, so the fixture exists on any machine. Either way the RESULT is pixels: anything
    that reads it back has genuinely performed OCR.
    """
    try:
        from PIL import Image, ImageDraw, ImageFont

        image = Image.new("L", (width, height), 255)
        draw = ImageDraw.Draw(image)
        font = None
        for candidate in ("/System/Library/Fonts/Supplemental/Arial.ttf", "/System/Library/Fonts/Helvetica.ttc", "/Library/Fonts/Arial.ttf"):
            if Path(candidate).exists():
                try:
                    font = ImageFont.truetype(candidate, point)
                    break
                except OSError:
                    continue
        if font is None:
            font = ImageFont.load_default()
        y = 40
        for line in lines:
            draw.text((45, y), line, fill=0, font=font)
            y += int(point * 1.55)
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()
    except ImportError:
        scale = max(2, point // 7)
        rows = [bytearray(b"\xff" * width) for _ in range(height)]
        y = 30
        for line in lines:
            x = 40
            for character in line.upper():
                glyph = _BITMAP_FONT.get(character, _BITMAP_FONT[" "])
                for row_index, bits in enumerate(glyph):
                    for column, bit in enumerate(bits):
                        if bit != "1":
                            continue
                        for dy in range(scale):
                            for dx in range(scale):
                                py, px = y + row_index * scale + dy, x + column * scale + dx
                                if 0 <= py < height and 0 <= px < width:
                                    rows[py][px] = 0
                x += 6 * scale
            y += 9 * scale
        return _png_bytes(width, height, [bytes(row) for row in rows])


_BITMAP_FONT: dict[str, list[str]] = {
    " ": ["00000"] * 7,
    "-": ["00000", "00000", "00000", "11111", "00000", "00000", "00000"],
    ":": ["00000", "00100", "00000", "00000", "00000", "00100", "00000"],
    ".": ["00000", "00000", "00000", "00000", "00000", "00100", "00000"],
    "0": ["01110", "10001", "10011", "10101", "11001", "10001", "01110"],
    "1": ["00100", "01100", "00100", "00100", "00100", "00100", "01110"],
    "2": ["01110", "10001", "00001", "00010", "00100", "01000", "11111"],
    "3": ["11111", "00010", "00100", "00010", "00001", "10001", "01110"],
    "4": ["00010", "00110", "01010", "10010", "11111", "00010", "00010"],
    "5": ["11111", "10000", "11110", "00001", "00001", "10001", "01110"],
    "6": ["00110", "01000", "10000", "11110", "10001", "10001", "01110"],
    "7": ["11111", "00001", "00010", "00100", "01000", "01000", "01000"],
    "8": ["01110", "10001", "10001", "01110", "10001", "10001", "01110"],
    "9": ["01110", "10001", "10001", "01111", "00001", "00010", "01100"],
    "A": ["01110", "10001", "10001", "11111", "10001", "10001", "10001"],
    "B": ["11110", "10001", "10001", "11110", "10001", "10001", "11110"],
    "C": ["01110", "10001", "10000", "10000", "10000", "10001", "01110"],
    "D": ["11110", "10001", "10001", "10001", "10001", "10001", "11110"],
    "E": ["11111", "10000", "10000", "11110", "10000", "10000", "11111"],
    "F": ["11111", "10000", "10000", "11110", "10000", "10000", "10000"],
    "G": ["01110", "10001", "10000", "10111", "10001", "10001", "01111"],
    "H": ["10001", "10001", "10001", "11111", "10001", "10001", "10001"],
    "I": ["01110", "00100", "00100", "00100", "00100", "00100", "01110"],
    "J": ["00111", "00010", "00010", "00010", "00010", "10010", "01100"],
    "K": ["10001", "10010", "10100", "11000", "10100", "10010", "10001"],
    "L": ["10000", "10000", "10000", "10000", "10000", "10000", "11111"],
    "M": ["10001", "11011", "10101", "10101", "10001", "10001", "10001"],
    "N": ["10001", "11001", "10101", "10011", "10001", "10001", "10001"],
    "O": ["01110", "10001", "10001", "10001", "10001", "10001", "01110"],
    "P": ["11110", "10001", "10001", "11110", "10000", "10000", "10000"],
    "Q": ["01110", "10001", "10001", "10001", "10101", "10010", "01101"],
    "R": ["11110", "10001", "10001", "11110", "10100", "10010", "10001"],
    "S": ["01111", "10000", "10000", "01110", "00001", "00001", "11110"],
    "T": ["11111", "00100", "00100", "00100", "00100", "00100", "00100"],
    "U": ["10001", "10001", "10001", "10001", "10001", "10001", "01110"],
    "V": ["10001", "10001", "10001", "10001", "10001", "01010", "00100"],
    "W": ["10001", "10001", "10001", "10101", "10101", "11011", "10001"],
    "X": ["10001", "01010", "00100", "00100", "00100", "01010", "10001"],
    "Y": ["10001", "01010", "00100", "00100", "00100", "00100", "00100"],
    "Z": ["11111", "00001", "00010", "00100", "01000", "10000", "11111"],
}


def scanned_pdf(lines: list[str], *, width: int = 1100, height: int = 700) -> bytes:
    """A PDF whose only content is a page-filling greyscale image. No text layer, anywhere.

    A reader that returns this file's words without running OCR is impossible: the words are not
    in the file as characters. That is exactly what makes it a usable OCR test.
    """
    png = text_image_png(lines, width=width, height=height)
    try:
        from PIL import Image

        image = Image.open(io.BytesIO(png)).convert("L")
        raw = image.tobytes()
        width, height = image.size
    except ImportError:
        raw = _decode_greyscale_png(png)
    payload = zlib.compress(raw)
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {width // 2} {height // 2}] "
        f"/Resources << /XObject << /Im0 4 0 R >> >> /Contents 5 0 R >>".encode("ascii"),
        f"<< /Type /XObject /Subtype /Image /Width {width} /Height {height} /ColorSpace /DeviceGray "
        f"/BitsPerComponent 8 /Filter /FlateDecode /Length {len(payload)} >>\nstream\n".encode("ascii")
        + payload
        + b"\nendstream",
        _stream("", f"q {width // 2} 0 0 {height // 2} 0 0 cm /Im0 Do Q".encode("ascii")),
    ]
    return _pdf(objects)


def _decode_greyscale_png(data: bytes) -> bytes:
    """Undo the PNG filtering of the built-in writer (filter type 0 on every row)."""
    offset = 8
    header = b""
    idat = bytearray()
    while offset + 8 <= len(data):
        length = struct.unpack(">I", data[offset : offset + 4])[0]
        kind = data[offset + 4 : offset + 8]
        payload = data[offset + 8 : offset + 8 + length]
        if kind == b"IHDR":
            header = payload
        elif kind == b"IDAT":
            idat += payload
        offset += 12 + length
    width, height = struct.unpack(">II", header[:8])
    raw = zlib.decompress(bytes(idat))
    return b"".join(raw[row * (width + 1) + 1 : (row + 1) * (width + 1)] for row in range(height))


# --- DOCX ------------------------------------------------------------------------------------------

_DOCX_CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument'
    '.wordprocessingml.document.main+xml"/></Types>'
)
_DOCX_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/'
    'officeDocument" Target="word/document.xml"/></Relationships>'
)
_WNS = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def _w_paragraph(text: str, *, heading: int = 0) -> str:
    style = f'<w:pPr><w:pStyle w:val="Heading{heading}"/></w:pPr>' if heading else ""
    return f"<w:p>{style}<w:r><w:t xml:space=\"preserve\">{_xml_escape(text)}</w:t></w:r></w:p>"


def _xml_escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _w_table(rows: list[list[str]]) -> str:
    cells = "".join(
        "<w:tr>" + "".join(f'<w:tc><w:p><w:r><w:t>{_xml_escape(cell)}</w:t></w:r></w:p></w:tc>' for cell in row) + "</w:tr>"
        for row in rows
    )
    return f"<w:tbl>{cells}</w:tbl>"


def docx_document(*, marker: str = "SLATE-3319", with_macro: bool = False) -> bytes:
    """A real DOCX: headings, a tabbed paragraph, the quarterly table, and a closing marker."""
    rows = [list(QUARTERLY_HEADERS)] + [[str(cell) for cell in row] for row in QUARTERLY_ROWS]
    body = (
        _w_paragraph("Regional Performance Review", heading=1)
        + _w_paragraph("Summary", heading=2)
        + _w_paragraph("Revenue held across all four regions through the third quarter.")
        + _w_table(rows)
        + _w_paragraph("Appendix", heading=2)
        + _w_paragraph(f"Document reference: {marker}")
    )
    document = f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document {_WNS}><w:body>{body}</w:body></w:document>'
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", _DOCX_CONTENT_TYPES)
        archive.writestr("_rels/.rels", _DOCX_RELS)
        archive.writestr("word/document.xml", document)
        if with_macro:
            archive.writestr("word/vbaProject.bin", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 64)
    return buffer.getvalue()


# --- archives ---------------------------------------------------------------------------------------

def zip_archive(members: dict[str, bytes], *, compression: int = zipfile.ZIP_DEFLATED) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression) as archive:
        for name, payload in members.items():
            archive.writestr(name, payload)
    return buffer.getvalue()


def zip_with_traversal_member() -> bytes:
    """A ZIP carrying `../../etc/passwd` and an absolute path. Both must be refused by name."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("notes.txt", b"ordinary member\n")
        archive.writestr("../../etc/passwd", b"root:x:0:0::/root:/bin/sh\n")
        archive.writestr("/tmp/absolute.txt", b"absolute member\n")
    return buffer.getvalue()


def zip_with_symlink_member(target: str = "/etc/passwd") -> bytes:
    """A ZIP whose member is a symlink to a file outside the archive."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        info = zipfile.ZipInfo("link.txt")
        info.create_system = 3
        info.external_attr = (0o120777 << 16)
        archive.writestr(info, target)
        archive.writestr("real.txt", b"a real member\n")
    return buffer.getvalue()


def zip_bomb(*, member_bytes: int = 80 * 1024 * 1024) -> bytes:
    """A highly compressible member: tiny archive, enormous expansion."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        archive.writestr("bomb.txt", b"\x00" * member_bytes)
    return buffer.getvalue()


def zip_many_members(count: int = 2_500) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for index in range(count):
            archive.writestr(f"file-{index:05d}.txt", f"member {index}\n".encode())
    return buffer.getvalue()


# --- video ------------------------------------------------------------------------------------------

#: The event a video test must find. It appears ONLY in the middle third of the clip, so a reader
#: that samples just the first or last frame cannot report it, and a reader that claims to have
#: watched the whole video while sampling the ends is caught by its absence.
VIDEO_EVENT_TEXT = "PLATE KX-4471"
VIDEO_OPENING_TEXT = "SEGMENT ONE"
VIDEO_CLOSING_TEXT = "SEGMENT THREE"


def video_frames(*, seconds: int = 12, fps: int = 5, width: int = 640, height: int = 360) -> list[bytes]:
    """PNG frames: opening third, the event in the middle third, closing third."""
    frames: list[bytes] = []
    total = seconds * fps
    for index in range(total):
        position = index / max(1, total - 1)
        if position < 0.34:
            lines = [VIDEO_OPENING_TEXT, f"T {index // fps}"]
        elif position < 0.67:
            lines = [VIDEO_EVENT_TEXT, f"T {index // fps}"]
        else:
            lines = [VIDEO_CLOSING_TEXT, f"T {index // fps}"]
        frames.append(text_image_png(lines, width=width, height=height, point=54))
    return frames


def encode_video(frames: list[bytes], *, fps: int = 5, with_audio: bool = False, ffmpeg: str = "") -> bytes:
    """Encode PNG frames into an H.264 MP4. Returns b'' when no encoder is available."""
    import shutil as _shutil
    import subprocess as _subprocess
    import tempfile as _tempfile

    encoder = ffmpeg or _shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"
    if not Path(encoder).exists():
        return b""
    with _tempfile.TemporaryDirectory(prefix="vool-video-fixture-") as directory:
        root = Path(directory)
        for index, payload in enumerate(frames):
            (root / f"f{index:05d}.png").write_bytes(payload)
        output = root / "out.mp4"
        command = [encoder, "-nostdin", "-loglevel", "error", "-y", "-framerate", str(fps), "-i", str(root / "f%05d.png")]
        if with_audio:
            command += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={len(frames) / fps:.2f}", "-c:a", "aac", "-shortest"]
        command += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "ultrafast", str(output)]
        result = _subprocess.run(command, capture_output=True, timeout=180, check=False)
        if result.returncode != 0 or not output.exists():
            return b""
        return output.read_bytes()


def sample_video(*, seconds: int = 12, fps: int = 5, with_audio: bool = False) -> bytes:
    return encode_video(video_frames(seconds=seconds, fps=fps), fps=fps, with_audio=with_audio)


# --- RAR ---------------------------------------------------------------------------------------
#
# Nothing on a stock macOS creates RAR archives -- `bsdtar` reads the format but cannot write it,
# and no RAR compressor is installed. Without a fixture the RAR reader could only ever be claimed
# to work, so the format is written here directly. RAR5 with the STORE method is a short, exactly
# specified container: a signature, a main header, one header-plus-payload per member, and an end
# header, each block prefixed by its own CRC32. Verified against libarchive 3.7.4, which both lists
# and extracts what this produces.


def _rar_vint(value: int) -> bytes:
    """RAR5 variable-length integer: 7 bits per byte, high bit set while more bytes follow."""
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        out.append(byte | (0x80 if value else 0))
        if not value:
            return bytes(out)


def _rar_block(header_type: int, header_flags: int, body: bytes, *, data_size: int | None = None) -> bytes:
    """One RAR5 block. The CRC32 covers the size field and everything after it, as the format says."""
    payload = _rar_vint(header_type) + _rar_vint(header_flags) + (_rar_vint(data_size) if data_size is not None else b"") + body
    size = _rar_vint(len(payload))
    return struct.pack("<I", zlib.crc32(size + payload) & 0xFFFFFFFF) + size + payload


def rar_archive(members: dict[str, bytes]) -> bytes:
    """A valid RAR5 archive holding each member uncompressed."""
    out = bytearray(b"Rar!\x1a\x07\x01\x00")
    out += _rar_block(1, 0, _rar_vint(0))  # main archive header, no volume flags
    for name, payload in members.items():
        encoded = name.encode("utf-8")
        body = (
            _rar_vint(0x0004)  # file flags: data CRC32 present
            + _rar_vint(len(payload))  # unpacked size
            + _rar_vint(0)  # attributes
            + struct.pack("<I", zlib.crc32(payload) & 0xFFFFFFFF)
            + _rar_vint(0)  # compression info: RAR5 version 0, method 0 (store)
            + _rar_vint(1)  # host OS: unix
            + _rar_vint(len(encoded))
            + encoded
        )
        out += _rar_block(2, 0x0002, body, data_size=len(payload))
        out += payload
    out += _rar_block(5, 0, _rar_vint(0))  # end of archive
    return bytes(out)


def rar_with_traversal_member() -> bytes:
    return rar_archive({"notes.txt": b"ordinary rar member\n", "../../etc/passwd": b"root:x:0:0::/root:/bin/sh\n"})


# --- legacy DOC --------------------------------------------------------------------------------


def legacy_doc(text: str = "", *, marker: str = "OBSIDIAN-5512") -> bytes:
    """A genuine OLE2 .doc, written by the same system converter that reads it back.

    Returns b'' where no converter exists, so a caller can report the format BLOCKED rather than
    testing against a hand-made file that is not really a legacy Word document.
    """
    import subprocess as _subprocess
    import tempfile as _tempfile

    converter = "/usr/bin/textutil"
    if not Path(converter).exists():
        return b""
    body = text or (
        "Regional Performance Review\n\n"
        "Revenue held across all four regions through the third quarter.\n\n"
        f"Document reference: {marker}\n"
    )
    with _tempfile.TemporaryDirectory(prefix="vool-doc-fixture-") as directory:
        root = Path(directory)
        source = root / "source.txt"
        source.write_text(body, encoding="utf-8")
        output = root / "out.doc"
        result = _subprocess.run(
            [converter, "-convert", "doc", "-output", str(output), str(source)],
            capture_output=True,
            timeout=120,
            check=False,
        )
        if result.returncode != 0 or not output.exists():
            return b""
        return output.read_bytes()


# --- XLSX -------------------------------------------------------------------------------------------
#
# The workbook carries the SAME quarterly table as the PDF and DOCX fixtures, so a question about
# the numbers has one answer the fixture source itself states. A second sheet holds the semantic
# traps a spreadsheet reader is most likely to get quietly wrong: a formula with a cache, a
# formula without one, a date-formatted serial and a cached error.

_SSML_NS = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
_OFFICE_REL_NS_ATTR = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'


def _xlsx_content_types(*, with_macro: bool = False) -> str:
    overrides = (
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument'
        '.spreadsheetml.sheet.main+xml"/>'
    )
    if with_macro:
        overrides += (
            '<Override PartName="/xl/vbaProject.bin" ContentType="application/vnd.ms-office.vbaProject"/>'
        )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        + overrides
        + "</Types>"
    )


def _xlsx_shared_strings(*, marker: str = "QUARTZ-8842") -> str:
    #: Index 0-3 are the column headers, 4-7 the region names, 8 "Total", 9 the reference line
    #: and 10 the admin note. The sheet XML below must agree with this order.
    strings = [
        QUARTERLY_HEADERS[0],
        QUARTERLY_HEADERS[1],
        QUARTERLY_HEADERS[2],
        QUARTERLY_HEADERS[3],
        *(row[0] for row in QUARTERLY_ROWS),
        "Total",
        f"Workbook reference: {marker}",
        "Admin note",
    ]
    items = "".join(f"<si><t>{_xml_escape(text)}</t></si>" for text in strings)
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<sst {_SSML_NS} count="{len(strings)}" uniqueCount="{len(strings)}">{items}</sst>'
    )


def _xlsx_styles() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<styleSheet {_SSML_NS}>'
        '<numFmts count="1"><numFmt numFmtId="164" formatCode="yyyy\\-mm\\-dd;@"/></numFmts>'
        '<fonts count="1"><font><sz val="11"/><name val="Calibri"/></font></fonts>'
        '<fills count="1"><fill><patternFill patternType="none"/></fill></fills>'
        '<borders count="1"><border/></borders>'
        '<cellStyleXfs count="1"><xf numFmtId="0"/></cellStyleXfs>'
        '<cellXfs count="4">'
        '<xf numFmtId="0" xfId="0"/>'            # style 0: general
        '<xf numFmtId="164" xfId="0" applyNumberFormat="1"/>'  # style 1: custom date
        '<xf numFmtId="14" xfId="0" applyNumberFormat="1"/>'   # style 2: builtin date (14 = mm-dd-yy)
        '<xf numFmtId="18" xfId="0" applyNumberFormat="1"/>'   # style 3: builtin time (18 = h:mm)
        "</cellXfs>"
        "</styleSheet>"
    )


def _quarterly_sheet_xml(*, with_cached_totals: bool = True) -> str:
    rows = ['<row r="1">' + "".join(f'<c r="{"ABCD"[i]}1" t="s"><v>{i}</v></c>' for i in range(4)) + "</row>"]
    for index, row in enumerate(QUARTERLY_ROWS, start=2):
        cells = f'<c r="A{index}" t="s"><v>{index + 2}</v></c>'
        cells += "".join(f'<c r="{"BCD"[i]}{index}"><v>{row[i + 1]}</v></c>' for i in range(3))
        rows.append(f'<row r="{index}">{cells}</row>')
    total = quarterly_total("Q1") + quarterly_total("Q2") + quarterly_total("Q3")
    if with_cached_totals:
        cells = (
            f'<c r="A6" t="s"><v>8</v></c>'
            f'<c r="B6"><f>SUM(B2:B5)</f><v>{quarterly_total("Q1")}</v></c>'
            f'<c r="C6"><f>SUM(C2:C5)</f><v>{quarterly_total("Q2")}</v></c>'
            f'<c r="D6"><f>SUM(D2:D5)</f><v>{quarterly_total("Q3")}</v></c>'
            f'<c r="E6"><f>SUM(B6:D6)</f><v>{total}</v></c>'
        )
    else:
        # Formulas the producer never cached: the honest rendering is the formula and the ABSENCE.
        cells = '<c r="A6" t="s"><v>8</v></c><c r="B6"><f>SUM(B2:B5)</f></c>'
    rows.append(f'<row r="6">{cells}</row>')
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<worksheet {_SSML_NS}><sheetData>{"".join(rows)}</sheetData></worksheet>'
    )


def _admin_sheet_xml() -> str:
    # 46268 = 2026-09-03 under the 1900 system; style 2 is the builtin date, 3 the builtin time.
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<worksheet {_SSML_NS}><sheetData>'
        '<row r="1"><c r="A1" t="s"><v>10</v></c></row>'
        '<row r="2"><c r="A2" s="2"><v>46268</v></c><c r="B2" s="3"><v>0.39583333333333331</v></c></row>'
        '<row r="3"><c r="A3" t="e"><v>#DIV/0!</v></c><c r="B3" t="inlineStr"><is><t>inline text</t></is></c></row>'
        "</sheetData></worksheet>"
    )


def xlsx_workbook(
    *,
    marker: str = "QUARTZ-8842",
    with_cached_totals: bool = True,
    with_macro: bool = False,
    with_admin_sheet: bool = True,
) -> bytes:
    """A real XLSX: the quarterly table plus a Total row held as FORMULAS with cached results."""
    sheet2 = _admin_sheet_xml() if with_admin_sheet else '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData/></worksheet>'
    shared = _xlsx_shared_strings(marker=marker)
    workbook = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<workbook {_SSML_NS} {_OFFICE_REL_NS_ATTR}>'
        '<workbookPr date1904="0"/>'
        '<sheets>'
        '<sheet name="Quarterly" sheetId="1" r:id="rId1"/>'
        '<sheet name="Admin" sheetId="2" r:id="rId2"/>'
        "</sheets></workbook>"
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet2.xml"/>'
        '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings" Target="sharedStrings.xml"/>'
        '<Relationship Id="rId4" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
        "</Relationships>"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", _xlsx_content_types(with_macro=with_macro))
        archive.writestr("_rels/.rels", _DOCX_RELS)
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/_rels/workbook.xml.rels", rels)
        archive.writestr("xl/sharedStrings.xml", shared)
        archive.writestr("xl/styles.xml", _xlsx_styles())
        archive.writestr("xl/worksheets/sheet1.xml", _quarterly_sheet_xml(with_cached_totals=with_cached_totals))
        archive.writestr("xl/worksheets/sheet2.xml", sheet2)
        if with_macro:
            archive.writestr("xl/vbaProject.bin", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 64)
    return buffer.getvalue()


def xlsx_named_like_a_workbook_but_is_a_zip() -> bytes:
    """A ZIP wearing the .xlsx name, with no workbook part: bytes decide, and these are not one."""
    return zip_archive({"readme.txt": b"not a workbook\n"})


def xlsx_with_linked_workbook_reference() -> bytes:
    """A workbook whose sheet points OUTSIDE the package: the reference is reported, never fetched."""
    base = xlsx_workbook()
    workbook = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<workbook {_SSML_NS} {_OFFICE_REL_NS_ATTR}>'
        '<sheets><sheet name="Quarterly" sheetId="1" r:id="rId1"/></sheets>'
        '<externalReferences><externalReference r:id="rId9"/></externalReferences>'
        "</workbook>"
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
        '<Relationship Id="rId9" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/externalReference" '
        'TargetMode="External" Target="file://\\\\fileserver\\finance\\budget.xlsx"/>'
        "</Relationships>"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for member in zipfile.ZipFile(io.BytesIO(base)).namelist():
            payload = zipfile.ZipFile(io.BytesIO(base)).read(member)
            if member == "xl/workbook.xml":
                payload = workbook.encode("utf-8")
            elif member == "xl/_rels/workbook.xml.rels":
                payload = rels.encode("utf-8")
            archive.writestr(member, payload)
    return buffer.getvalue()


# --- PPTX -------------------------------------------------------------------------------------------
#
# The deck's decisive span lives only in a SPEAKER NOTE of slide two, and the deck's slide order
# deliberately does not match the slide parts' file names: a reader that walks slideN.xml in name
# order instead of the deck's own sldIdLst order puts the event on the wrong slide and the test
# sees it.

_PML_NS = 'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"'
_DRAWML_NS_ATTR = 'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"'

PPTX_SLIDE_TWO_MARKER = "AZURE-6273"


def _pptx_slide(title: str, lines: list[str], *, table: list[list[str]] | None = None) -> str:
    body = "".join(
        f"<a:p><a:r><a:t>{_xml_escape(line)}</a:t></a:r></a:p>" for line in lines
    )
    shapes = (
        f'<p:sp><p:nvSpPr><p:cNvPr id="1" name="Title"/><p:nvPr><p:ph type="ctrTitle"/></p:nvPr></p:nvSpPr>'
        f'<p:txBody><a:p><a:r><a:t>{_xml_escape(title)}</a:t></a:r></a:p></p:txBody></p:sp>'
    )
    shapes += (
        f'<p:sp><p:nvSpPr><p:cNvPr id="2" name="Content"/><p:nvPr/></p:nvSpPr>'
        f'<p:txBody>{body}</p:txBody></p:sp>'
    )
    if table:
        rows = "".join(
            "<a:tr>"
            + "".join(
                f'<a:tc><a:txBody><a:p><a:r><a:t>{_xml_escape(cell)}</a:t></a:r></a:p></a:txBody></a:tc>' for cell in row
            )
            + "</a:tr>"
            for row in table
        )
        shapes += (
            '<p:graphicFrame><p:nvGraphicFramePr><p:cNvPr id="3" name="Table"/><p:nvPr/></p:nvGraphicFramePr>'
            f"<a:graphic><a:graphicData uri=\"http://schemas.openxmlformats.org/drawingml/2006/table\"><a:tbl>{rows}</a:tbl></a:graphicData></a:graphic></p:graphicFrame>"
        )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f"<p:sld {_PML_NS} {_DRAWML_NS_ATTR}><p:cSld><p:spTree>{shapes}</p:spTree></p:cSld></p:sld>"
    )


def pptx_deck(*, with_notes: bool = True, with_link: bool = False, slide_count: int = 3) -> bytes:
    """A real PPTX: title slide, a content slide with the quarterly table and the decisive note,
    then a closing slide. The physical parts are numbered BACKWARDS on purpose."""
    slide_ids = "".join(
        f'<p:sldId id="{255 + i}" r:id="rId{i}"/>' for i in range(1, slide_count + 1)
    )
    presentation = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f"<p:presentation {_PML_NS} {_OFFICE_REL_NS_ATTR}>"
        "<p:sldIdLst>" + slide_ids + "</p:sldIdLst></p:presentation>"
    )
    rel_entries = [
        f'<Relationship Id="rId{index}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" '
        f'Target="slides/slide{slide_count + 1 - index}.xml"/>'
        for index in range(1, slide_count + 1)
    ]
    if with_link:
        rel_entries.append(
            '<Relationship Id="rId90" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" '
            'TargetMode="External" Target="https://example.invalid/brochure"/>'
        )
    presentation_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        + "".join(rel_entries)
        + "</Relationships>"
    )
    slide_parts = {
        1: _pptx_slide("Quarterly Review", ["Prepared by the regional desk"]),
        2: _pptx_slide(
            "Numbers behind the review",
            ["The table holds the same quarterly figures as the workbook."],
            table=[list(QUARTERLY_HEADERS), *[list(map(str, row)) for row in QUARTERLY_ROWS]],
        ),
        3: _pptx_slide("Questions", ["Appendix carries the workbook reference QUARTZ-8842."]),
    }
    slide_rels = {}
    if with_notes:
        def _notes_xml(body: str) -> str:
            return (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                f"<p:notes {_PML_NS} {_DRAWML_NS_ATTR}><p:cSld><p:spTree>"
                '<p:sp><p:nvSpPr><p:nvPr><p:ph type="body"/></p:nvPr></p:nvSpPr>'
                f'<p:txBody><a:p><a:r><a:t>{_xml_escape(body)}</a:t></a:r></a:p></p:txBody></p:sp>'
                '<p:sp><p:nvSpPr><p:nvPr><p:ph type="sldNum"/></p:nvPr></p:nvSpPr>'
                "<p:txBody><a:p><a:fld><a:t>2</a:t></a:fld></a:p></p:txBody></p:sp>"
                "</p:spTree></p:cSld></p:notes>"
            )

        # The decisive span lives ONLY in the notes of slide two. Slide one's notes are filler;
        # a reader that dumps every notes part under one slide, or drops notes entirely, fails.
        notes_one = _notes_xml("Generic planning note with nothing decisive in it.")
        notes_two = _notes_xml(f"Flag {PPTX_SLIDE_TWO_MARKER} to finance before Friday.")
        for index in (1, 2):
            slide_rels[f"ppt/slides/_rels/slide{index}.xml.rels"] = (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/notesSlide" '
                f'Target="../notesSlides/notesSlide{index}.xml"/>'
                "</Relationships>"
            )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", _xlsx_content_types())
        archive.writestr("_rels/.rels", _DOCX_RELS)
        archive.writestr("ppt/presentation.xml", presentation)
        archive.writestr("ppt/_rels/presentation.xml.rels", presentation_rels)
        for index in range(1, slide_count + 1):
            archive.writestr(f"ppt/slides/slide{index}.xml", slide_parts[index])
            if f"ppt/slides/_rels/slide{index}.xml.rels" in slide_rels:
                archive.writestr(f"ppt/slides/_rels/slide{index}.xml.rels", slide_rels[f"ppt/slides/_rels/slide{index}.xml.rels"])
        if with_notes:
            archive.writestr("ppt/notesSlides/notesSlide1.xml", notes_one)
            archive.writestr("ppt/notesSlides/notesSlide2.xml", notes_two)
        if with_link:
            archive.writestr("ppt/media/image1.png", b"\x89PNG\r\n\x1a\nfake")
    return buffer.getvalue()


# --- RTF --------------------------------------------------------------------------------------------
#
# The scanner's honesty is tested on what it must NOT read: an object's binary payload, a picture,
# a field INSTRUCTION (the link target), header furniture -- each counted in warnings, none of it
# in the extraction. The field's cached RESULT is content and must survive.

RTF_MARKER = "TOPAZ-3341"


def rtf_document(*, with_object: bool = True, with_picture: bool = True) -> bytes:
    parts = [
        r"{\rtf1\ansicpg1252\deff0{\fonttbl{\f0 Calibri;}}",
        r"{\info{\title Hidden title}{\author Meta author}}",
    ]
    if with_object:
        parts.append(r"{\object{\objdata FEEDFACEFEEDFACEFEEDFACEFEEDFACE}{\result{nothing here}}}")
    parts.append(r"\pard\b Regional memo: " + RTF_MARKER + r"\b0\par")
    parts.append(r"The quarterly figures match the workbook, including the pipe | character.\par")
    parts.append(r"{\field{\*\fldinst HYPERLINK " + '"' + r"https://tracker.example.invalid/click" + '"' + r"}{\fldrslt field result keeps this text}}\par")
    parts.append(r"Unicode en dash: \u8211? done\par")
    if with_picture:
        parts.append(r"{\pict{\*\bleader 0102030405}}")
    parts.append("}")
    # CRLF between tokens, the way real producers write RTF: a control word at end of line is
    # followed by a newline, never glued to the next word's text.
    return "\r\n".join(parts).encode("ascii")


# --- ODT --------------------------------------------------------------------------------------------

ODT_MARKER = "OPAL-5583"


def _odt_content() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<office:document-content '
        'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
        'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
        'xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0">'
        "<office:body><office:text>"
        "<text:h text:outline-level=\"1\">" + ODT_MARKER + " Heading One</text:h>"
        "<text:p>A paragraph with a<text:tab/>tab and a<text:line-break/>line break.</text:p>"
        "<table:table><table:table-row>"
        "<table:table-cell><text:p>Item</text:p></table:table-cell><table:table-cell><text:p>42</text:p></table:table-cell>"
        "</table:table-row><table:table-row>"
        "<table:table-cell><text:p>Other</text:p></table:table-cell><table:table-cell><text:p>7</text:p></table:table-cell>"
        "</table:table-row></table:table>"
        "<text:p>Closing line.</text:p>"
        "</office:text></office:body></office:document-content>"
    )


def _odt_manifest(*, encrypted: bool = False) -> str:
    encryption = (
        '<manifest:file-entry manifest:full-path="content.xml">'
        '<manifest:encryption-data manifest:checksum-type="SHA1/1K" manifest:checksum="abcd">'
        '<manifest:encryption-data/>'
        "</manifest:encryption-data></manifest:file-entry>"
        if encrypted
        else ""
    )
    return (
        '<?xml version="1.0"?>'
        '<manifest:manifest xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0">'
        '<manifest:file-entry manifest:full-path="/" manifest:media-type="application/vnd.oasis.opendocument.text"/>'
        + encryption
        + "</manifest:manifest>"
    )


def odt_document(*, with_macros: bool = False, encrypted: bool = False) -> bytes:
    """A real ODT: heading, paragraph with tabs and breaks, a small table, a closing line."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("mimetype", "application/vnd.oasis.opendocument.text")
        archive.writestr("content.xml", _odt_content())
        archive.writestr("META-INF/manifest.xml", _odt_manifest(encrypted=encrypted))
        if with_macros:
            archive.writestr("Basic/Standard/Module1.xml", "<script:module/>")
    return buffer.getvalue()


# --- EPUB -------------------------------------------------------------------------------------------
#
# The spine order deliberately differs from the chapters' hrefs: a reader that reads the manifest
# alphabetically instead of the spine puts the foreword in the wrong section and the test sees it.

EPUB_TITLE_MARKER = "BERYL-2214"
EPUB_CHAPTER_MARKER = "COBALT-7719"


def epub_book(*, with_drm: bool = False) -> bytes:
    container = (
        '<?xml version="1.0"?>'
        '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
        '<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>'
    )
    opf = (
        '<?xml version="1.0"?>'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="id">'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
        f"<dc:title>Field Guide to {EPUB_TITLE_MARKER}</dc:title><dc:creator>R. Desk</dc:creator></metadata>"
        '<manifest>'
        '<item id="c1" href="text/ch1.xhtml" media-type="application/xhtml+xml"/>'
        '<item id="c2" href="text/foreword.xhtml" media-type="application/xhtml+xml"/>'
        '<item id="c3" href="cover.png" media-type="image/png"/>'
        "</manifest>"
        '<spine><itemref idref="c2"/><itemref idref="c1"/><itemref idref="c3"/></spine></package>'
    )
    foreword = (
        '<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml"><head><title>Foreword</title></head>'
        "<body><h1>Foreword</h1><p>Reading order puts the foreword first.</p>"
        "<ul><li>bullet one</li><li>bullet two</li></ul></body></html>"
    )
    chapter = (
        '<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml"><head><title>Chapter one</title></head>'
        f"<body><h1>Wear orbits the stone {EPUB_CHAPTER_MARKER}</h1><p>First paragraph of chapter one.</p>"
        "<table><tr><th>k</th><th>v</th></tr><tr><td>alpha</td><td>5</td></tr></table></body></html>"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr("META-INF/container.xml", container)
        if with_drm:
            archive.writestr(
                "META-INF/encryption.xml",
                '<?xml version="1.0"?><encryption xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                "<enc:EncryptedData xmlns:enc=\"http://www.w3.org/2001/04/xmlenc#\"><enc:CipherData>"
                '<enc:CipherReference URI="OEBPS/text/ch1.xhtml"/></enc:CipherData></enc:EncryptedData></encryption>',
            )
        archive.writestr("OEBPS/content.opf", opf)
        archive.writestr("OEBPS/text/foreword.xhtml", foreword)
        archive.writestr("OEBPS/text/ch1.xhtml", chapter)
        archive.writestr("OEBPS/cover.png", b"\x89PNG\r\n\x1a\nfake")
    return buffer.getvalue()


# --- legacy XLS -------------------------------------------------------------------------------------
#
# No tool on a stock macOS writes .xls, so the fixture IS a workbook: a minimal BIFF8 stream --
# labels, numbers, an RK integer, FORMULA records with cached results, a boolean, a cached error
# and a date-formatted cell -- inside a hand-built OLE2 container. xlrd reads back exactly what
# this source says the file contains, which is what makes the assertions honest.

XLS_MARKER = "GARNET-9965"


def _xls_record(record_id: int, payload: bytes) -> bytes:
    return struct.pack("<HH", record_id, len(payload)) + payload


def _xls_bof(kind: int) -> bytes:
    return _xls_record(0x0809, struct.pack("<HHIHH", 0x0600, kind, 0x0DBB, 0x07CC, 0))


def _xls_short_unicode(text: str) -> bytes:
    return struct.pack("<H", len(text)) + b"\x00" + text.encode("latin-1")


def _xls_number(row: int, col: int, xf: int, value: float) -> bytes:
    return _xls_record(0x0203, struct.pack("<HHHd", row, col, xf, value))


def _xls_label(row: int, col: int, xf: int, text: str) -> bytes:
    return _xls_record(0x0204, struct.pack("<HHH", row, col, xf) + _xls_short_unicode(text))


def _xls_rk_int(row: int, col: int, xf: int, value: int) -> bytes:
    rk = (value << 2) | 0x2   # bit 1 set: the low 30 bits are a signed integer
    return _xls_record(0x027E, struct.pack("<HHHI", row, col, xf, rk))


def _xls_bool(row: int, col: int, xf: int, value: bool) -> bytes:
    return _xls_record(0x0205, struct.pack("<HHHBB", row, col, xf, 1 if value else 0, 0))


def _xls_error(row: int, col: int, xf: int, code: int) -> bytes:
    return _xls_record(0x0205, struct.pack("<HHHBB", row, col, xf, code, 1))


def _xls_formula(row: int, col: int, xf: int, cached: float) -> bytes:
    rgce = struct.pack("<BH", 0x1E, 0)  # a ptgInt token: the expression is never decompiled
    return _xls_record(
        0x0006,
        struct.pack("<HHH", row, col, xf)
        + struct.pack("<d", cached)
        + struct.pack("<HIH", 0xFFFF, 0, len(rgce))
        + rgce,
    )


def _xls_ole_container(stream: bytes) -> bytes:
    """A minimal single-FAT OLE2 container with one regular-sector stream."""
    SECTOR = 512
    FREE, ENDOFCHAIN, FATSECT = 0xFFFFFFFF, 0xFFFFFFFE, 0xFFFFFFFD
    stream_first = 2
    stream_sectors = (len(stream) + SECTOR - 1) // SECTOR
    stream_last = stream_first + stream_sectors - 1
    total_sectors = 2 + stream_sectors

    fat = [FREE] * total_sectors
    fat[0] = FATSECT
    fat[1] = ENDOFCHAIN
    for sector in range(stream_first, stream_last):
        fat[sector] = sector + 1
    fat[stream_last] = ENDOFCHAIN
    fat_blob = struct.pack(f"<{len(fat)}I", *fat)
    fat_blob += bytes(SECTOR - len(fat_blob))
    fat_sectors = 1

    header = bytearray(SECTOR)
    header[0:8] = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
    struct.pack_into("<H", header, 24, 0x003E)
    struct.pack_into("<H", header, 26, 0x0003)
    struct.pack_into("<H", header, 28, 0xFFFE)
    struct.pack_into("<H", header, 30, 9)
    struct.pack_into("<H", header, 32, 6)
    struct.pack_into("<I", header, 44, fat_sectors)
    struct.pack_into("<I", header, 48, 1)               # first directory sector
    struct.pack_into("<I", header, 56, 4096)            # mini-stream cutoff
    struct.pack_into("<I", header, 60, ENDOFCHAIN)      # first mini FAT
    struct.pack_into("<I", header, 68, ENDOFCHAIN)      # first DIFAT
    for index in range(109):                            # DIFAT[0] = FAT sector; the rest free
        struct.pack_into("<I", header, 76 + 4 * index, 0 if index == 0 else FREE)

    def entry(name: str, kind: int, start: int, size: int, child: int = 0xFFFFFFFF) -> bytes:
        raw = bytearray(128)
        encoded = name.encode("utf-16-le") + b"\x00\x00"
        raw[0 : len(encoded)] = encoded
        raw[64] = len(encoded)
        raw[66] = kind
        raw[67] = 1 if kind else 0
        struct.pack_into("<I", raw, 68, 0xFFFFFFFF)
        struct.pack_into("<I", raw, 72, 0xFFFFFFFF)
        struct.pack_into("<I", raw, 76, child)
        struct.pack_into("<I", raw, 116, start)
        struct.pack_into("<Q", raw, 120, size)
        return bytes(raw)

    directory = entry("Root Entry", 5, ENDOFCHAIN, 0, child=1)
    directory += entry("Workbook", 2, stream_first, len(stream))
    directory += bytes(256)  # unused directory slots

    out = bytearray(header)
    out += fat_blob
    out += directory
    out += stream
    return bytes(out)


def legacy_xls_workbook(*, marker: str = XLS_MARKER, with_macros: bool = False, encrypted: bool = False) -> bytes:
    """A real .xls: the quarterly table, a FORMULA total row with cached results, and the
    semantic cells -- boolean, cached error, date-formatted serial -- on one sheet. With
    ``encrypted`` a FILEPASS record declares the workbook protected, which is what Excel writes."""
    out = io.BytesIO()
    out.write(_xls_bof(0x0005))                                       # workbook globals
    if encrypted:
        out.write(_xls_record(0x002F, struct.pack("<HHH", 0, 0, 1)))  # FILEPASS: RC4-protected
    out.write(_xls_record(0x0042, struct.pack("<H", 1252)))           # CODEPAGE
    out.write(_xls_record(0x0031, struct.pack("<HHHHHHB", 200, 0, 0x7FFF, 400, 0, 0, 1) + b"Arial\x00\x00"))
    out.write(_xls_record(0x041E, struct.pack("<H", 164) + _xls_short_unicode("yyyy\\-mm\\-dd")))
    for ifmt in (0, 164, 0):                                          # XF 0 style, 1 date, 2 general
        out.write(_xls_record(0x00E0, struct.pack("<HHHBBBBIiH", 0, ifmt, 0x0035 if ifmt else 0xFFF5, 0, 0, 0, 0xFD, 0, 0, 0)))
    patch_at = len(out.getvalue()) + 4                                # lbPlyPos of the BOUNDSHEET
    out.write(_xls_record(0x0085, struct.pack("<IH", 0, 0) + struct.pack("<B", len("Quarterly")) + b"\x00Quarterly"))
    out.write(_xls_record(0x000A, b""))                               # EOF globals

    sheet_offset = len(out.getvalue())
    out.write(_xls_bof(0x0010))                                       # worksheet
    out.write(_xls_record(0x0200, struct.pack("<IIHHH", 0, 11, 0, 5, 0x0064)))
    out.write(_xls_label(0, 0, 2, QUARTERLY_HEADERS[0]))
    out.write(_xls_label(0, 1, 2, QUARTERLY_HEADERS[1]))
    out.write(_xls_label(0, 2, 2, QUARTERLY_HEADERS[2]))
    out.write(_xls_label(0, 3, 2, QUARTERLY_HEADERS[3]))
    for index, row in enumerate(QUARTERLY_ROWS, start=1):
        out.write(_xls_label(index, 0, 2, row[0]))
        out.write(_xls_number(index, 1, 2, float(row[1])))
        out.write(_xls_rk_int(index, 2, 2, row[2]))
        out.write(_xls_number(index, 3, 2, float(row[3])))
    total_row = len(QUARTERLY_ROWS) + 1
    out.write(_xls_label(total_row, 0, 2, "Total"))
    out.write(_xls_formula(total_row, 1, 2, float(quarterly_total("Q1"))))
    out.write(_xls_formula(total_row, 2, 2, float(quarterly_total("Q2"))))
    out.write(_xls_formula(total_row, 3, 2, float(quarterly_total("Q3"))))
    out.write(_xls_label(total_row + 1, 0, 2, f"Workbook reference: {marker}"))
    out.write(_xls_bool(total_row + 2, 1, 2, True))
    out.write(_xls_error(total_row + 2, 2, 2, 0x07))                  # #DIV/0!
    out.write(_xls_number(total_row + 3, 0, 1, 46268.0))              # date-formatted (xf 1)
    out.write(_xls_record(0x000A, b""))                               # EOF sheet

    blob = bytearray(out.getvalue())
    struct.pack_into("<I", blob, patch_at, sheet_offset)
    blob += b"\x00" * max(0, 4608 - len(blob))                        # keep the stream out of the mini-FAT
    if with_macros:
        blob += b"_\x00V\x00B\x00A\x00_\x00P\x00R\x00O\x00J\x00E\x00C\x00T\x00" + b"\x00" * 64
    return _xls_ole_container(bytes(blob))
