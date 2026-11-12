"""Standalone fixed job program, copied into private scratch before confinement.

No runtime imports, tool dispatch, provider clients or input-selected commands.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import signal
import subprocess
import sys
import threading
import time
import warnings
import zipfile
from pathlib import Path
from xml.etree import ElementTree

# Address-space growth allowed after the fixed program has started and before guest bytes are read.
# Inputs are at most 10 MiB and photos at most 20 MP, so a legitimate job stays well inside it.
MEMORY_HEADROOM = 512 * 1024 * 1024


def watchdog(seconds):
    parent = os.getppid()
    deadline = time.monotonic() + seconds

    def watch():
        while time.monotonic() < deadline and os.getppid() == parent:
            time.sleep(0.1)
        Path('deadline').touch()
        try:
            # The trusted launcher gives this recorded worker its own session.
            # Stop its decoders as well, including when the supervisor died.
            os.killpg(os.getpgrp(), signal.SIGKILL)
        finally:
            os._exit(2)

    threading.Thread(target=watch, daemon=True).start()


def limit_memory(headroom=MEMORY_HEADROOM):
    """Install an RLIMIT_AS ceiling at the current mapped size plus headroom; decoders inherit it.

    macOS refuses a small absolute RLIMIT_AS (a fresh process already maps hundreds of GiB of
    shared regions), so the ceiling is relative to this process. It bounds growth, not resident
    memory. Not measurable means not started: the job fails closed.
    """
    import ctypes
    import resource

    info = (ctypes.c_uint64 * 12)()
    proc = ctypes.CDLL('/usr/lib/libproc.dylib', use_errno=True)
    # PROC_PIDTASKINFO: struct proc_taskinfo is 96 bytes; pti_virtual_size is its first field.
    if proc.proc_pidinfo(os.getpid(), 4, ctypes.c_uint64(0), info, ctypes.sizeof(info)) != ctypes.sizeof(info):
        raise OSError('worker memory size unavailable')
    ceiling = info[0] + headroom
    resource.setrlimit(resource.RLIMIT_AS, (ceiling, ceiling))
    if resource.getrlimit(resource.RLIMIT_AS) != (ceiling, ceiling):
        raise OSError('worker memory limit not applied')


def clean_pdf(data, limits):
    from pypdf import PdfReader
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

    if data.startswith(b'%PDF'):
        reader = PdfReader(io.BytesIO(data), strict=True)
        if reader.is_encrypted or len(reader.pages) > 100:
            raise ValueError('document limit')
        text = '\n\n'.join(page.extract_text() or '' for page in reader.pages)
    elif data.startswith(b'PK'):
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            item = archive.getinfo('word/document.xml')
            if item.file_size > 2*1024*1024:
                raise ValueError('document limit')
            xml = archive.read(item)
            if b'<!ENTITY' in xml or b'<!DOCTYPE' in xml:
                raise ValueError('entities')
            tree = ElementTree.fromstring(xml)
            ns = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
            text = '\n'.join(''.join(n.itertext()) for n in tree.iter(ns+'p'))
    else:
        text = data.decode('utf-8', errors='strict')
    if not text.strip() or len(text) > limits['max_length']:
        raise ValueError('text limit')
    font = Path('/System/Library/Fonts/Supplemental/Arial Unicode.ttf')
    if not font.exists():
        import reportlab

        font = Path(reportlab.__file__).parent/'fonts/Vera.ttf'
    face = TTFont('LendBody', str(font))
    if any(ord(ch) not in face.face.charToGlyph for ch in text if not ch.isspace()):
        raise ValueError('unsupported glyph')
    pdfmetrics.registerFont(face)
    style = ParagraphStyle('body', fontName='LendBody', fontSize=11, leading=16, splitLongWords=True)
    from xml.sax.saxutils import escape

    story = []
    for block in text.splitlines():
        if block:
            story.append(Paragraph(escape(block), style))
        else:
            story.append(Spacer(1, 8))
    out = io.BytesIO()
    SimpleDocTemplate(out, title='Document', author='', pageCompression=1).build(story)
    result = out.getvalue()
    checked = PdfReader(io.BytesIO(result), strict=True)
    if not checked.pages or len(checked.pages) > 200:
        raise ValueError('output limit')
    return result, 'document.pdf'


def contact_sheet(data, limits):
    from PIL import Image, ImageOps
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    warnings.simplefilter('error', Image.DecompressionBombWarning)
    Image.MAX_IMAGE_PIXELS = 20000000
    members = [data]
    if data.startswith(b'PK'):
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            items = archive.infolist()
            if not 1 <= len(items) <= min(100, limits['max_length']):
                raise ValueError('photo count')
            if any(i.is_dir() or i.file_size > limits['max_bytes'] for i in items):
                raise ValueError('member size')
            if sum(i.file_size for i in items) > limits['max_bytes']:
                raise ValueError('expanded size')
            # Nothing is extracted by filename. Links, path traversal, and
            # remote references remain meaningless bytes in the archive.
            members = [archive.read(i) for i in items]
    out = io.BytesIO()
    pdf = canvas.Canvas(out, pagesize=(595, 842), pageCompression=1)
    for index, raw in enumerate(members):
        if index and index % 6 == 0:
            pdf.showPage()
        with Image.open(io.BytesIO(raw)) as image:
            if image.format not in {'JPEG', 'PNG', 'WEBP'}:
                raise ValueError('photo format')
            image.load()
            image = ImageOps.exif_transpose(image).convert('RGB')
            image.thumbnail((1000, 1000))
            x = 30 + (index % 2)*280
            y = 590 - ((index % 6)//2)*270
            pdf.drawImage(ImageReader(image), x, y, width=255, height=230,
                          preserveAspectRatio=True, anchor='c')
            pdf.drawString(x, y-16, str(index+1))
    pdf.save()
    return out.getvalue(), 'contact-sheet.pdf'


def recording(limits):
    # An explicit local-only input/output invocation. No playlist can fetch
    # anything: sockets are denied, and only this scratch is writable.
    import shutil

    probe = shutil.which('ffprobe', path='/opt/homebrew/bin:/usr/local/bin')
    ffmpeg = shutil.which('ffmpeg', path='/opt/homebrew/bin:/usr/local/bin')
    duration = subprocess.run([probe, '-v', 'error', '-show_entries', 'format=duration',
                               '-of', 'default=nw=1:nk=1', 'input.bin'], capture_output=True,
                              check=True, timeout=limits['max_seconds'])
    seconds = float(duration.stdout)
    if not 0 < seconds <= min(3600, limits['max_length']) or limits['trim_start'] >= seconds:
        raise ValueError('recording length')
    subprocess.run([ffmpeg, '-v', 'error', '-nostdin', '-protocol_whitelist', 'file,pipe',
                    '-i', 'input.bin', '-ss', str(limits['trim_start']), '-t', str(limits['trim_length']),
                    '-map_metadata', '-1', '-vn', '-ac', '1', '-ar', '16000', '-c:a', 'pcm_s16le', '-f', 'wav', 'output.bin'],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                   timeout=limits['max_seconds'])
    return Path('output.bin').read_bytes(), 'recording.wav'


def main():
    started = time.monotonic()
    seconds = float(sys.argv[2])
    # The trusted parent records our exact process identity before allowing
    # any guest bytes into a parser. A cut during spawn cannot leave an
    # unrecorded parser running after the parent disappears.
    deadline = time.monotonic()+1
    while not Path('start.ok').exists():
        if time.monotonic() >= deadline or os.getppid() == 1:
            os._exit(2)
        time.sleep(.01)
    watchdog(max(.001, seconds-(time.monotonic()-started)))
    limit_memory()
    sys.path.insert(0, sys.argv[1])
    limits = json.loads(Path('terms.json').read_text())
    data = Path('input.bin').read_bytes()
    if len(data) > limits['max_bytes']:
        raise ValueError('input limit')
    capability = limits['capability']
    if capability == 'clean_pdf':
        result, name = clean_pdf(data, limits)
    elif capability == 'contact_sheet':
        result, name = contact_sheet(data, limits)
    elif capability == 'recording':
        result, name = recording(limits)
    else:
        raise ValueError('unavailable capability')
    if not result or len(result) > 10*1024*1024:
        raise ValueError('output limit')
    Path('output.bin').write_bytes(result)
    print(json.dumps({'name': name, 'sha256': hashlib.sha256(result).hexdigest()}))


if __name__ == '__main__':
    main()
