"""The chat page draws pictures only from VOOL itself (security review 2026-11-13).

An answer is model text, and the model may have read a web page, a pasted article or a file someone
else wrote. The page used to draw any ``![alt](https://...)`` in an answer as an image, which the
browser fetched with no click, so an injected line could have the model put the person's file text
in the address and send it off the computer. Now:

- ``/chat`` carries ``img-src 'self' data: blob:``, so the browser refuses any other image;
- the renderer turns an image on another site into a link (tests/test_the_chat_renderer_renders_markdown.py);
- a picture VOOL makes in the cloud is saved here first and shown from the file, like a local render.

The served proof with Chromium across web, paste and file turns is
tests/test_a_reply_loads_no_remote_image_served.py.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from core import credential_store, generated_files, media_tools, runtime_paths, usage_quota
from core.effect_gateway import named_background_effect_scope

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    monkeypatch.setenv("VOOL_FILES_DIRS", "")
    monkeypatch.delenv("VOOL_TIER", raising=False)
    runtime_paths.configure_runtime_home(tmp_path)
    usage_quota.reset_usage()
    with named_background_effect_scope("tests.chat_page_images"):
        yield
    runtime_paths.configure_runtime_home(None)


def _service() -> None:
    credential_store.store_credential(
        "image.api.default", json.dumps({"endpoint": "https://img.example.com/generate", "api_key": "k"}), label="t"
    )


class _Resp:
    def __init__(self, body: bytes) -> None:
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False

    def read(self, *_a):
        return self.body


def _opener(picture: bytes, seen: list[str]):
    def opener(req, timeout=None):
        seen.append(req.full_url)
        if req.full_url.endswith("/generate"):
            return _Resp(json.dumps({"url": "https://cdn.example.com/out/1.png"}).encode())
        return _Resp(picture)

    return opener


def test_the_chat_page_allows_images_only_from_vool_itself() -> None:
    from core.web.api.service import CHAT_PAGE_HEADERS

    policy = CHAT_PAGE_HEADERS["Content-Security-Policy"]
    assert "img-src 'self' data: blob:" in policy
    assert "frame-ancestors 'self'" in policy
    assert CHAT_PAGE_HEADERS["X-Frame-Options"] == "SAMEORIGIN"


def test_a_cloud_picture_is_saved_here_and_shown_from_the_file() -> None:
    _service()
    seen: list[str] = []
    result = media_tools.generate_image(prompt="a lighthouse", opener=_opener(PNG, seen))
    assert result.ok, result
    assert seen == ["https://img.example.com/generate", "https://cdn.example.com/out/1.png"]
    assert result.remote_ref == "https://cdn.example.com/out/1.png"
    saved = Path(result.image_ref)
    assert saved.is_file() and saved.read_bytes() == PNG and saved.suffix == ".png"
    assert generated_files.path_is_allowed(str(saved))
    assert generated_files.read_file_bytes(str(saved)) == PNG


def test_what_is_not_a_picture_is_never_saved_and_the_link_stays() -> None:
    _service()
    result = media_tools.generate_image(prompt="a lighthouse", opener=_opener(b"<html>not a picture</html>", []))
    assert result.ok
    assert result.image_ref == "https://cdn.example.com/out/1.png"
    assert result.remote_ref == ""
    roots = [Path(root) for root in generated_files.files_roots()]
    assert not any(root.exists() and any(root.iterdir()) for root in roots)


def test_a_data_picture_is_decoded_here_without_a_fetch() -> None:
    seen: list[str] = []
    saved = media_tools._keep_image_here(
        "data:image/png;base64," + base64.b64encode(PNG).decode(), prompt="dot", opener=_opener(PNG, seen), timeout=5
    )
    assert seen == []
    assert Path(saved).read_bytes() == PNG


def test_only_https_is_fetched() -> None:
    seen: list[str] = []
    for ref in ("http://cdn.example.com/1.png", "file:///etc/passwd", "/etc/passwd", "ftp://x/y.png"):
        assert media_tools._keep_image_here(ref, prompt="x", opener=_opener(PNG, seen), timeout=5) == ""
    assert seen == []


def test_an_oversized_picture_is_not_kept() -> None:
    assert generated_files.save_image_bytes(PNG + b"\0" * generated_files.MAX_SAVED_IMAGE_BYTES) == ""


def test_the_image_tool_hands_the_model_the_saved_file() -> None:
    from core.runtime_execution_tools import _image_generate

    _service()
    original = media_tools.generate_image

    def with_opener(**kwargs):
        return original(**kwargs, opener=_opener(PNG, []))

    media_tools.generate_image = with_opener
    try:
        result = _image_generate({"prompt": "a lighthouse"})
    finally:
        media_tools.generate_image = original
    assert result.ok
    assert result.details["image_ref"].startswith("/")
    assert result.details["remote_ref"] == "https://cdn.example.com/out/1.png"
    assert "cdn.example.com" not in json.dumps(result.details["observation"])
