"""Chat R8 on the Mac (d5d044b0, qwen3:8b, loop on): five conversations failed. The real hook, route,
tools, chat record and home folder; the brain is scripted like the Mac model behaved.

* C1, C2, C11, M5: the loop answered a listing from its staged read in one call. The chat's record
  got no folder, so "and the newest?" went to the model on its own instead of the folder lane.
* C5: "downloads folder contents" listed the folder, then the loop declined its own listing because
  no file is about "contents", and another lane answered with a folder match.
* C6, C7, M6: "no thanks, try documents instead" / "nah look on the desktop" name only a place. The
  loop was not told what to look for, and its result was judged against "thanks" and "instead".
* C7: "do i have a letter from the landlord anywhere" asks what the owner has kept.
* M6: a folder match printed the absolute home path.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core import conversation_state as cs
from core import runtime_continuity as rc
from core.clean_loop import brain, hook, route

OWNER = {"surface": "cli"}


class _Agent:
    memory_router = object()

    @staticmethod
    def _fast_path_result(**kwargs):
        return {"response": kwargs["response"], "reason": kwargs["reason"]}


@pytest.fixture
def setup(tmp_path, monkeypatch):
    home = (tmp_path / "home").resolve()
    for rel in ("Desktop/landlord_letter_2026.txt", "Downloads/beach-sunset.png", "Downloads/passport_scan.png",
                "Documents/boiler_service_2026.txt"):
        (home / rel).parent.mkdir(parents=True, exist_ok=True)
        (home / rel).write_text("Boiler service booked with HeatCo." if "boiler" in rel else "x" * 64, encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv(route.FLAG_ENV, "1")
    rc.configure_runtime_continuity_db_path(tmp_path / "state.db")
    shown: list[str] = []

    def scripted(_router, _manifest, _context):
        def reply(messages):
            last = messages[-1]["content"]
            shown.append(last)
            if "[tool result]" in last or "[Evidence gathered" in last:
                names = sorted({word for word in last.replace("/", " ").split() if "." in word and word[0].isalpha()})
                return json.dumps({"answer": "Here are the files: " + ", ".join(names)})
            low = last.lower()
            if "boiler" in low:
                return json.dumps({"tool": "find", "query": "boiler service"})
            for folder in ("desktop", "downloads", "documents"):
                if folder in low:
                    return json.dumps({"tool": "list_dir", "path": folder.capitalize()})
            return json.dumps({"answer": "Got it."})

        return reply

    monkeypatch.setattr(brain, "resolve_local_manifest", lambda _router, _context: object())
    monkeypatch.setattr(brain, "resolve_cloud_manifest", lambda _router, _context: None)
    monkeypatch.setattr(brain, "model_label", lambda _manifest: ("local", "dev-model"))
    monkeypatch.setattr(brain, "make_brain", scripted)
    yield home, shown
    rc.configure_runtime_continuity_db_path(None)


def _ask(text: str, session: str, context: dict | None = None):
    ctx = dict(context if context is not None else OWNER)
    hook._clear_evidence(session)
    return hook.maybe_answer(_Agent(), text=text, effective_input=text, session_id=session, source_context=ctx), ctx


def test_a_listing_answered_from_the_staged_read_records_its_folder(setup, caplog):
    _home, shown = setup
    caplog.set_level("INFO", logger=hook._LOG.name)
    out, ctx = _ask("whats on my desktop", "r8-c1")
    # The staged read is named in the turn's log, so a one-call answer never reads as one with no tool behind it.
    assert any("tools= staged=machine.list_directory" in r.getMessage() for r in caplog.records), [r.getMessage() for r in caplog.records]
    assert [r["tool_name"] for r in out["tool_receipts"]] == ["machine.list_directory"]
    assert out is not None and "landlord_letter_2026.txt" in out["response"]
    assert len(shown) == 1 and "[Evidence gathered" in shown[0], "answered in one call from the staged read"
    assert ctx["_conversation_kind_listing"]["path"] == "~/Desktop"


@pytest.mark.parametrize("text", ["downloads folder contents", "what is inside downloads"])
def test_a_listing_ask_named_by_its_contents_is_not_declined(setup, text):
    out, ctx = _ask(text, "r8-c5")
    assert out is not None, ctx.get("clean_local_loop_route")
    assert "passport_scan.png" in out["response"]


def test_a_redirect_looks_for_the_earlier_ask_in_the_new_folder(setup):
    _home, shown = setup
    redirect = {
        **OWNER,
        "_conversation_changed": True,
        "_conversation_slots": {"path": "~/Documents"},
        "_conversation_request": "any note about the boiler service in my stuff (change: documents instead)",
    }
    out, ctx = _ask("no thanks, try documents instead", "r8-c6", redirect)
    assert out is not None, ctx.get("clean_local_loop_route")
    assert "boiler_service_2026.txt" in out["response"]
    assert "[The earlier ask, now in ~/Documents: any note about the boiler service in my stuff]" in shown[0]


def test_a_note_lookup_in_a_statements_shape_is_recorded_so_a_redirect_moves_it(setup):
    cs.record_sealed_turn("r8-c6-record", "any note about the boiler service in my stuff",
                          {"response": "No notes in this workspace match 'boiler service'."})
    got = cs.resolve("r8-c6-record", "no thanks, try documents instead", cleared=False)
    assert got.kind == "retry_changed" and got.slots.get("path") == "~/Documents"
    assert got.request == "any note about the boiler service in my stuff"


@pytest.mark.parametrize(("text", "own"), [
    ("do i have a letter from the landlord anywhere", True),
    ("have i got the boiler receipt saved somewhere", True),
    ("do i have a meeting tomorrow", False),
    ("do i have time to cook before 7", False),
])
def test_do_i_have_it_anywhere_is_about_the_owners_things(text, own):
    assert route.asks_explicitly_about_own_things(text) is own


def test_a_folder_match_shows_the_home_folder_as_a_tilde(setup):
    from core.runtime_execution_tools import _machine_find_folder

    home, _shown = setup
    result = _machine_find_folder({"name": "documents"})
    assert result.ok and str(home) not in result.response_text
    assert "- ~/Documents" in result.response_text
