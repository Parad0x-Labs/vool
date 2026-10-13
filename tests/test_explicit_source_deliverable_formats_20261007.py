import io
import zipfile

import pytest

from core.deliverables import DeliverableRefused, build_file


@pytest.mark.parametrize("fmt", ["css", "py", "js", "sh"])
def test_explicit_source_format_matches_its_supported_extension(fmt):
    built = build_file("sample content", filename="sample." + fmt, fmt=fmt)
    assert built.fmt == "code" and built.filename.endswith("." + fmt)
    assert built.data == b"sample content\n"

def test_html_css_archive_accepts_explicit_member_formats():
    built = build_file("", filename="page.zip", fmt="zip", files=[
        {"filename":"index.html","format":"html","content":"<p>Picnic</p>"},
        {"filename":"style.css","format":"css","content":"p { color: blue; }"},
    ])
    with zipfile.ZipFile(io.BytesIO(built.data)) as archive:
        assert set(archive.namelist()) == {"index.html", "style.css"}

@pytest.mark.parametrize("name,fmt", [("unsafe.rar", "py"), ("unsafe.7z", "css"), ("unsafe.exe", "exe")])
def test_unsupported_formats_are_not_disguised_as_source(name, fmt):
    with pytest.raises(DeliverableRefused):
        build_file("sample content", filename=name, fmt=fmt)

@pytest.mark.parametrize("name", ["style.css", "index.html", "main.py"])
def test_archive_members_cannot_be_silently_changed_to_text_files(name):
    with pytest.raises(DeliverableRefused) as error:
        build_file("", filename="source.zip", fmt="zip", files=[{"filename": name, "format": "txt", "content": "content"}])
    assert error.value.code == "member_format_mismatch"

def test_archive_member_names_alone_determine_html_and_css_bytes():
    built = build_file("", filename="page.zip", fmt="zip", files=[
        {"filename": "index.html", "content": "<link rel=stylesheet href=style.css><p>Picnic</p>"},
        {"filename": "style.css", "content": "p { color: blue; }"},
    ])
    with zipfile.ZipFile(io.BytesIO(built.data)) as archive:
        assert set(archive.namelist()) == {"index.html", "style.css"}
        assert b"style.css" in archive.read("index.html")
        assert b"color: blue" in archive.read("style.css")
