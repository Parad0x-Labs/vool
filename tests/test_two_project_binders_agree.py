"""The chat dispatch has two ways to bind a chat to a project from its words: the words resolver
(core.workspace_from_words, the workspace-from-words job) and the named-project binder
(core.named_project_binding, the full 0.7.7 train). A folder the words resolver refuses -- one
named for passwords, keys or a wallet -- must never become a project through the other door.
"""

from __future__ import annotations

from core.named_project_binding import resolve_named_project
from core.workspace_from_words import folder_refusal_kind


def _home(tmp_path):
    home = tmp_path / "person"
    for name in ("garden-planner", "wallet", "passwords"):
        (home / "Projects" / name).mkdir(parents=True)
    (home / "Downloads").mkdir()
    return home


def test_a_private_folder_is_refused_by_the_shared_guard(tmp_path):
    home = _home(tmp_path)
    assert folder_refusal_kind(home / "Projects" / "wallet", home) == "secret"
    assert folder_refusal_kind(home / "Projects" / "passwords", home) == "secret"
    assert folder_refusal_kind(home / "Downloads", home) == "standard"
    assert folder_refusal_kind(home / "Projects" / "garden-planner", home) == ""


def test_the_named_project_binder_never_finds_a_refused_folder(tmp_path, monkeypatch):
    from core import project_store

    monkeypatch.setattr(project_store, "list_projects", lambda: [])
    home = _home(tmp_path)
    for name in ("wallet", "passwords"):
        found, reason = resolve_named_project(name, home=str(home))
        assert found is None and reason == "not_found", (name, found, reason)
    found, reason = resolve_named_project("garden planner", home=str(home))
    assert found is not None and reason == "", reason
    assert found.root == str((home / "Projects" / "garden-planner").resolve())
