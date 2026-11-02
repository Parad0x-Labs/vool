#!/usr/bin/env python3
"""Build the fake HOME the audit daemon runs with (frozen; SHA256SUMS covers it).

    python3 -I make_fakehome.py <dest>

Makes an ordinary person's folders with ordinary files: Downloads, Desktop, Documents, Pictures, Music, Movies and a
code project. File ages are set relative to NOW (the "age_days" below), so "pictures from the last 2 days" has a
right answer at any time. Writes <dest>/MANIFEST.json with every file, its size, sha256 and age, which is the answer
key for file asks. Refuses to write into a folder that exists and is not empty.
"""
from __future__ import annotations

import hashlib
import json
import os
import struct
import sys
import time
import zlib
from pathlib import Path

DAY = 86400


def png(width: int, height: int, rgb: tuple[int, int, int]) -> bytes:
    raw = b"".join(b"\x00" + bytes(rgb) * width for _ in range(height))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def jpeg_like(seed: str) -> bytes:
    # A minimal JFIF header plus filler: enough for type sniffing by extension and magic bytes.
    body = hashlib.sha256(seed.encode()).digest() * 64
    return b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00" + body + b"\xff\xd9"


def pdf(lines: list[str]) -> bytes:
    text = "BT /F1 12 Tf 72 720 Td 14 TL " + " ".join(
        "(" + line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)") + ") '" for line in lines) + " ET"
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(text)).encode() + b" >>\nstream\n" + text.encode("latin-1") + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, obj in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


FILES: list[tuple[str, int, object]] = [
    # (relative path, age in days, content)
    ("Downloads/IMG_4021.jpg", 0, ("jpg", "beach-1")),
    ("Downloads/IMG_4022.jpg", 1, ("jpg", "beach-2")),
    ("Downloads/screenshot 2026-10-26 at 09.14.png", 1, ("png", (40, 30, (20, 120, 200)))),
    ("Downloads/dog_park.HEIC", 2, ("bytes", b"\x00\x00\x00\x18ftypheic" + b"\x00" * 2000)),
    ("Downloads/old_meme.gif", 9, ("bytes", b"GIF89a" + b"\x00" * 900)),
    ("Downloads/wallpaper_mountains.png", 30, ("png", (64, 40, (90, 140, 60)))),
    ("Downloads/invoice_4471.pdf", 3, ("pdf", ["Invoice 4471", "Bluebird Plumbing", "Total due: 129.40 EUR",
                                                "Due date: 14 November 2026"])),
    ("Downloads/boarding-pass-VNO-LIS.pdf", 5, ("pdf", ["Boarding pass", "Vilnius to Lisbon", "Flight LX 2231",
                                                        "Seat 14C", "Gate closes 06:40"])),
    ("Downloads/setup_helper.dmg", 12, ("bytes", b"\x00" * 300_000)),
    ("Downloads/bank_export_october.csv", 2, ("text", "date,description,amount\n2026-10-03,Groceries Rimi,-42.18\n"
                                                      "2026-10-07,Salary,2400.00\n2026-10-11,Electricity,-61.90\n"
                                                      "2026-10-19,Cinema,-18.00\n")),
    ("Downloads/movie_night_trailer.mp4", 4, ("bytes", b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 900_000)),
    ("Desktop/todo.txt", 0, ("text", "call the dentist\nbuy birthday present for Mia\nrenew car insurance before Nov 3\n")),
    ("Desktop/Screenshot 2026-10-27 at 18.02.png", 0, ("png", (50, 30, (200, 200, 200)))),
    ("Desktop/Screenshot 2026-10-20 at 11.40.png", 7, ("png", (50, 30, (180, 180, 180)))),
    ("Desktop/flat viewing notes.md", 6, ("text", "# Flats\n\n- Zirmunu 12: 58 m2, 620 EUR, balcony, noisy street\n"
                                                    "- Antakalnio 40: 64 m2, 700 EUR, near park, 4th floor no lift\n")),
    ("Documents/CV_2026.pdf", 40, ("pdf", ["Curriculum vitae", "Experience: 6 years in logistics",
                                           "Languages: English, Lithuanian, German"])),
    ("Documents/Taxes/2025/tax_return_2025.pdf", 200, ("pdf", ["Tax return 2025", "Refund: 312.00 EUR"])),
    ("Documents/Taxes/2025/receipts.txt", 210, ("text", "pharmacy 23.10\nschool books 88.40\n")),
    ("Documents/Recipes/grandma pancakes.txt", 90, ("text", "2 eggs, 250 ml milk, 120 g flour, pinch of salt.\n"
                                                             "Rest the batter 20 minutes.\n")),
    ("Documents/Work/Q3 report draft.txt", 15, ("text", "Q3 shipments up 8 percent. Late deliveries: 31.\n")),
    ("Documents/Work/meeting notes 2026-10-21.txt", 6, ("text", "Decided: move the warehouse audit to 4 November.\n"
                                                                 "Owner: Tomas.\n")),
    ("Pictures/Holiday Lisbon 2026/lisbon_001.jpg", 60, ("jpg", "lisbon-1")),
    ("Pictures/Holiday Lisbon 2026/lisbon_002.jpg", 60, ("jpg", "lisbon-2")),
    ("Pictures/Holiday Lisbon 2026/lisbon_003.jpg", 59, ("jpg", "lisbon-3")),
    ("Pictures/Family/mia_birthday.jpg", 300, ("jpg", "mia")),
    ("Pictures/cat_on_sofa.png", 3, ("png", (30, 30, (230, 150, 40)))),
    ("Music/playlist_road_trip.m3u", 100, ("text", "#EXTM3U\nsong_one.mp3\nsong_two.mp3\n")),
    ("Movies/kids_school_play.mov", 20, ("bytes", b"\x00\x00\x00\x14ftypqt  " + b"\x00" * 2_500_000)),
    ("Projects/garden-planner/README.md", 25, ("text", "# Garden planner\n\nSmall script that prints what to plant "
                                                       "each month.\n")),
    ("Projects/garden-planner/plan.py", 25, ("text", "MONTHS = {'March': ['peas'], 'April': ['carrots', 'beets']}\n"
                                                     "for month, crops in MONTHS.items():\n"
                                                     "    print(month, ', '.join(crops))\n")),
    ("Documents/big_backup_archive.zip", 50, ("bytes", b"PK\x03\x04" + b"\x00" * 6_000_000)),
]


def build(dest: Path) -> None:
    if dest.exists() and any(dest.iterdir()):
        raise SystemExit(f"refusing: {dest} exists and is not empty")
    now = time.time()
    manifest = []
    for rel, age, (kind, value) in FILES:
        path = dest / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if kind == "jpg":
            data = jpeg_like(value)
        elif kind == "png":
            data = png(*value)
        elif kind == "pdf":
            data = pdf(value)
        elif kind == "text":
            data = value.encode("utf-8")
        else:
            data = value
        path.write_bytes(data)
        stamp = now - age * DAY - 600
        os.utime(path, (stamp, stamp))
        manifest.append({"path": rel, "age_days": age, "bytes": len(data),
                         "sha256": hashlib.sha256(data).hexdigest()})
    for folder in ("Library", "Public"):
        (dest / folder).mkdir(exist_ok=True)
    (dest / "MANIFEST.json").write_text(json.dumps({"built_at_epoch": int(now), "files": manifest}, indent=2) + "\n")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    build(Path(sys.argv[1]).resolve())
    print("ok")
