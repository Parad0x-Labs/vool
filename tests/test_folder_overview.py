"""The folder-overview fast path: read a real folder and ground the answer, never confabulate.

Regression for the "what is this project about? check folder" failure where a small local model
said "I can't access folders on your machine" and invented a project from leaked context.
"""
from __future__ import annotations

from core.agent_runtime.fast_paths_utility import (
    maybe_handle_folder_overview_request,
    maybe_handle_workspace_identity_request,
)
from core.folder_overview import build_folder_overview


class _StubAgent:
    def _fast_path_result(self, *, session_id, user_input, response, confidence, source_context, reason):
        return {"response": response, "reason": reason, "confidence": confidence, "session_id": session_id}


def _overview(agent, text, ctx):
    return maybe_handle_folder_overview_request(
        agent, text, session_id="openclaw:0123456789abcdef0123", source_surface="api", source_context=ctx,
    )


def test_build_reads_real_folder_title_and_listing(tmp_path) -> None:
    (tmp_path / "README.md").write_text("# Zebra Ledger\nA Rust CLI for penguin invoices.\n")
    (tmp_path / "Cargo.toml").write_text("[package]\nname='zebra'\n")
    (tmp_path / "src").mkdir()
    out = build_folder_overview(str(tmp_path))
    assert "Zebra Ledger" in out                    # the README's own title
    assert "penguin invoices" in out                # the README's own words, not a guess
    assert "Rust" in out                            # stack detected from Cargo.toml
    assert "README.md" in out and "Cargo.toml" in out and "src/" in out
    assert "can't access" not in out.lower() and "cannot access" not in out.lower()


def test_build_skips_noise_dirs_and_dotfiles(tmp_path) -> None:
    (tmp_path / "keep.txt").write_text("x")
    (tmp_path / ".git").mkdir()
    (tmp_path / "node_modules").mkdir()
    (tmp_path / ".hidden").write_text("x")
    out = build_folder_overview(str(tmp_path))
    assert "keep.txt" in out
    assert ".git" not in out and "node_modules" not in out and ".hidden" not in out


def test_build_caps_entries(tmp_path) -> None:
    for i in range(60):
        (tmp_path / f"f{i:02d}.txt").write_text("x")
    out = build_folder_overview(str(tmp_path))
    assert "…and" in out and "more" in out          # truncated, not a 60-line dump


def test_build_empty_folder_guides_not_hallucinate(tmp_path) -> None:
    bound = build_folder_overview(str(tmp_path), project_bound=True)
    assert "empty" in bound.lower() and str(tmp_path) in bound
    unbound = build_folder_overview(str(tmp_path), project_bound=False)
    assert "+ Project" in unbound or "path" in unbound.lower()


def test_build_missing_or_blank_workspace_guides(tmp_path) -> None:
    assert "isn't pointed at a project folder" in build_folder_overview("")
    assert "isn't pointed at a project folder" in build_folder_overview(str(tmp_path / "does_not_exist"))


def test_fast_path_fires_on_folder_ask_and_is_grounded(tmp_path) -> None:
    (tmp_path / "README.md").write_text("# Zebra Ledger\nA Rust CLI.\n")
    result = _overview(
        _StubAgent(),
        "what is this project about? check folder and explain me pls",
        {"workspace": str(tmp_path), "surface": "api"},
    )
    assert result is not None
    assert result["reason"] == "folder_overview_fast_path"
    assert "Zebra Ledger" in result["response"]


def test_fast_path_defers_on_named_file(tmp_path) -> None:
    # "explain README.md" names a file -> the direct file reader owns it, this returns None.
    assert _overview(_StubAgent(), "explain README.md to me", {"workspace": str(tmp_path), "surface": "api"}) is None
    assert _overview(_StubAgent(), "read config.json", {"workspace": str(tmp_path), "surface": "api"}) is None


def test_fast_path_ignores_non_folder_and_build_intents(tmp_path) -> None:
    ctx = {"workspace": str(tmp_path), "surface": "api"}
    for text in ("hey ghey", "what time is it", "generate an image of a cat", "build me a todo app in this folder"):
        assert _overview(_StubAgent(), text, ctx) is None


def test_fast_path_does_not_fire_on_project_attribute_questions(tmp_path) -> None:
    # "what is the project <attribute>" asks about a fact, not "read the folder" — must not fire.
    ctx = {"workspace": str(tmp_path), "surface": "api"}
    for text in ("what is the project deadline", "what is the project status", "what is the project budget"):
        assert _overview(_StubAgent(), text, ctx) is None
    # But a demonstrative or an explicit "about" still reads the folder.
    (tmp_path / "README.md").write_text("# Demo\nhi\n")
    for text in ("what is this project about", "what is the project about", "what's in the folder"):
        assert _overview(_StubAgent(), text, ctx) is not None


def test_fast_path_unbound_workspace_guides(tmp_path) -> None:
    result = _overview(_StubAgent(), "what is this project about?", {"workspace": "", "surface": "api"})
    assert result is not None
    assert "isn't pointed at a project folder" in result["response"]


def _identity(agent, text, ctx):
    return maybe_handle_workspace_identity_request(
        agent, text, session_id="openclaw:0123456789abcdef0123", source_surface="api", source_context=ctx,
    )


def test_dropped_apostrophe_still_reads_the_real_folder(tmp_path) -> None:
    """"whats in the folder" is typed as often as "what's" — both must reach the disk.

    The normalizer does not restore the apostrophe ("whats in the folder" normalizes to itself), so
    an apostrophe-only regex sent these to the model, which is exactly the confabulation this fast
    path exists to remove. Driven through the handler with a real folder: the assertion is the
    folder's OWN filenames coming back, not merely that something fired.
    """
    (tmp_path / "README.md").write_text("# Zebra Ledger\nA Rust CLI for penguin invoices.\n")
    (tmp_path / "quokka_notes.txt").write_text("x")
    ctx = {"workspace": str(tmp_path), "surface": "api"}
    phrasings = (
        "whats in the folder",
        "whats in the directory",
        "whats in the repo",
        "whats in the repository",
        "whats in the codebase",
        "whats in my project",
        "whats in our codebase",
        "whats in this project",
        "whats in this folder",
        "whats the project about",
        "whats this project about",
        "whats this repo about",
        "whats this codebase about",
        "Whats In The Folder?",
    )
    for text in phrasings:
        # Workspace identity runs FIRST in production (turn_frontdoor), so a phrasing it claims never
        # reaches the reader no matter what the overview regex says. Assert the real route.
        assert _identity(_StubAgent(), text, ctx) is None, text
        result = _overview(_StubAgent(), text, ctx)
        assert result is not None, text
        assert result["reason"] == "folder_overview_fast_path", text
        assert "quokka_notes.txt" in result["response"], text   # this folder's own file
        assert "Zebra Ledger" in result["response"], text       # this folder's own README title
        assert "can't access" not in result["response"].lower(), text
    # The apostrophe form keeps working, and the bare "s" does not widen the gate: an attribute
    # question and a word that merely starts with "whats" must still not read the folder.
    assert _overview(_StubAgent(), "what's in the folder", ctx) is not None
    for text in ("whats the project deadline", "whats the project status", "whatsapp in the folder"):
        assert _overview(_StubAgent(), text, ctx) is None, text
