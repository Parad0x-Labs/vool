"""A computer without VOOL's media tools hears plain words, never "unknown capability id".

On a machine without the nebula-media worker or ffmpeg/ffprobe -- which today includes every
packaged app, since the bundles leave vendor/ out (see MEDIA-PACKAGING.md in the v0.8 line folder)
-- the media family is never registered, so the media-studio skill is refused with the typed reason
"unavailable capabilities: ('media.video.split (unknown capability id)', ...)". That reason is
right for developers and stays in the records; these tests prove that every place a person reads
it says the plain sentence instead: the chat skill list, skill.inspect, the Skills catalog, the
media tools' own refusal and the Media Studio editor.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.media_capabilities import MEDIA_TOOLS_FAILED_TEXT, MEDIA_TOOLS_MISSING_TEXT

DEVELOPER_WORDS = ("unknown capability", "capability id", "NEBULA_MEDIA_HOME", "nebula-media", "worker exited")


def _plain(text: str) -> None:
    for word in DEVELOPER_WORDS:
        assert word not in text, f"{word!r} reached a person: {text}"


@pytest.fixture()
def no_media(tmp_path, monkeypatch):
    """The packaged app's machine: no NEBULA_MEDIA_HOME, no vendored worker beside the code, no
    importable nebula package, and a fresh capability graph with no media family."""
    from core import capability_graph, media_worker_client
    from core.media_worker_client import reset_media_worker

    monkeypatch.delenv("NEBULA_MEDIA_HOME", raising=False)
    monkeypatch.setattr(media_worker_client, "VENDORED_NEBULA_HOME", tmp_path / "app" / "vendor" / "nebula-media")
    capability_graph.reset()
    capability_graph.ensure_registry_bootstrap()
    reset_media_worker()
    from core.media_capabilities import media_tools_present, register_media_capabilities_if_present

    assert media_tools_present()[0] is False
    assert register_media_capabilities_if_present()["registered"] is False
    yield tmp_path
    reset_media_worker()
    capability_graph.reset()
    capability_graph.ensure_registry_bootstrap()


def test_the_typed_reason_stays_and_the_plain_reason_is_what_people_read(no_media):
    from core.native_skill_library import inspect_skill, skill_inventory

    row = next(r for r in skill_inventory() if r["id"] == "media-studio")
    assert row["available"] is False
    assert "unknown capability id" in row["reason"], row["reason"]  # kept for developers
    assert row["plain_reason"] == MEDIA_TOOLS_MISSING_TEXT

    inspected = inspect_skill("media-studio")
    assert "unknown capability id" in inspected["reason"]
    assert inspected["plain_reason"] == MEDIA_TOOLS_MISSING_TEXT


def test_skill_inspect_says_it_plainly(no_media):
    from core.runtime_execution_tools import _skill_inspect

    reply = _skill_inspect({"id": "media-studio"}).response_text
    assert MEDIA_TOOLS_MISSING_TEXT in reply, reply
    _plain(reply)


def test_the_chat_skill_list_says_why_media_studio_is_not_available(no_media):
    from core.runtime_execution_tools import _skill_list

    reply = _skill_list({}).response_text
    line = next(line for line in reply.splitlines() if line.startswith("- Media studio"))
    assert "not available here" in line and MEDIA_TOOLS_MISSING_TEXT in line, line
    _plain(reply)


def test_the_skills_catalog_shows_the_plain_reason(no_media):
    from core.plugin_catalog import _native_library_entry

    entry = _native_library_entry()
    skill = next(s for s in entry["skills"] if s["name"] == "media-studio")
    assert skill["reason"] == MEDIA_TOOLS_MISSING_TEXT
    assert "unknown capability id" in skill["reason_code"]


def test_a_media_tool_call_without_the_worker_is_refused_in_plain_words(no_media):
    from core.media_studio_chat_tools import media_open

    clip = no_media / "clip.mp4"
    clip.write_bytes(b"\x00" * 64)
    result = media_open({"source_path": str(clip)})
    assert result.ok is False and result.status == "worker_unavailable", result
    assert result.response_text == MEDIA_TOOLS_MISSING_TEXT
    assert result.details["detail"], "the worker's own text stays in the details"


def test_the_media_editor_says_it_plainly(no_media):
    from core.web.api.media_editor_api import handle_media_editor_post

    clip = no_media / "clip.mp4"
    clip.write_bytes(b"\x00" * 64)
    response = handle_media_editor_post("/media-editor/open", {"source_path": str(clip)})
    body = json.loads(response.body)
    assert body["error"] == "worker_unavailable"
    assert body["message"] == MEDIA_TOOLS_MISSING_TEXT
    assert body["detail"]
    _plain(body["message"])


def test_ffmpeg_missing_from_path_is_said_plainly(tmp_path, monkeypatch):
    """The worker is there but ffmpeg is not on PATH: the worker's "ffmpeg not found on PATH"."""
    from core.media_studio_chat_tools import _err
    from core.media_studio_service import MediaStudioServiceError

    bare = tmp_path / "bare-path"
    bare.mkdir()
    monkeypatch.setenv("PATH", str(bare))
    result = _err(MediaStudioServiceError("op_failed", "ffmpeg not found on PATH"), "media.edit")
    assert result.status == "worker_unavailable"
    assert result.response_text == MEDIA_TOOLS_MISSING_TEXT


def test_tools_present_but_the_worker_failed_says_nothing_was_changed(tmp_path, monkeypatch):
    from core.media_studio_chat_tools import _err
    from core.media_studio_service import MediaStudioServiceError
    from tests._media_tools_stub import media_tools_env

    for key, value in media_tools_env(tmp_path).items():
        monkeypatch.setenv(key, value)
    result = _err(MediaStudioServiceError("worker_unavailable", "worker pipe broke: EPIPE"), "media.edit")
    assert result.response_text == MEDIA_TOOLS_FAILED_TEXT
    _plain(result.response_text)


def test_a_skill_missing_something_else_still_gets_a_plain_reason():
    from core.native_skill_library import plain_unavailable_reason

    assert plain_unavailable_reason((), ("web.browser",)) == "It needs a part of VOOL that isn't on this computer yet."
    assert plain_unavailable_reason((), ()) == ""
    assert plain_unavailable_reason(["binary:ffmpeg"], ()) in (MEDIA_TOOLS_MISSING_TEXT, MEDIA_TOOLS_FAILED_TEXT)


def test_the_media_studio_skill_file_is_where_the_test_expects_it():
    assert (Path(__file__).resolve().parents[1] / "skills" / "media-studio" / "SKILL.md").is_file()
