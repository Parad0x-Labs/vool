"""Plan step 1 — scope authority.

"this / current / local folder" and "the folder we are in" mean the BOUND project root, not a
folder NAME to substring-match. Kills the split-brain bug where "analyse local folder we are in"
resolved "local" -> vool-local-product instead of reading the bound project.
"""
from __future__ import annotations

from core.agent_runtime import fast_paths_utility
from core.execution.constants import (
    machine_folder_audit_intent,
    machine_folder_search_intent,
    refers_to_current_scope,
)

DEMONSTRATIVES = (
    "analyse the local folder we are in",
    "audit this folder",
    "review the current project",
    "check the folder we are in",
    "go through our repo",
    "inspect this codebase",
    "look through the project we're in",
)


def test_demonstratives_are_recognised_as_current_scope():
    for phrase in DEMONSTRATIVES:
        assert refers_to_current_scope(phrase) is True, phrase


def test_demonstratives_do_not_become_a_named_folder():
    # The machine named-folder lanes must stand down for demonstratives, so the project-aware
    # folder-overview path resolves the bound root instead.
    for phrase in DEMONSTRATIVES:
        assert machine_folder_audit_intent(phrase) is None, phrase
        assert machine_folder_search_intent(phrase) is None, phrase


def test_explicit_named_folder_still_resolves():
    # An explicitly NAMED folder is still extracted (not a demonstrative) — no regression.
    name = machine_folder_audit_intent("audit my token hunter folder")
    assert name and "token hunter" in name
    assert refers_to_current_scope("audit my token hunter folder") is False
    assert machine_folder_search_intent("find the reports folder") == ("machine.find_folder", "reports")


def test_demonstrative_reads_the_bound_root_never_vool(tmp_path):
    # Codex gate #1: a bound "web0" project + "analyse the folder we are in" reads the BOUND root and
    # never resolves to vool-local-product.
    web0 = tmp_path / "web0"
    web0.mkdir()
    (web0 / "README.md").write_text("# web0\nThe distinctive web0 project.\n")
    (web0 / "web0_marker.txt").write_text("marker")

    class _StubAgent:
        def _fast_path_result(self, **kw):
            return {"response": kw["response"], "reason": kw["reason"]}

    res = fast_paths_utility.maybe_handle_folder_overview_request(
        _StubAgent(),
        "analyse the local folder we are in",
        session_id="s1",
        source_surface="api",
        source_context={"workspace_root": str(web0), "project_id": "proj_web0"},
    )
    assert res is not None, "demonstrative must route to the deterministic folder overview"
    body = str(res["response"]).lower()
    assert "vool-local-product" not in body
    assert "web0" in body  # it read the bound root


def test_unbound_demonstrative_fails_honestly(tmp_path):
    # No bound root -> honest guidance, never a silent fall-through to a ~-root name search.
    class _StubAgent:
        def _fast_path_result(self, **kw):
            return {"response": kw["response"], "reason": kw["reason"]}

    res = fast_paths_utility.maybe_handle_folder_overview_request(
        _StubAgent(),
        "audit this folder",
        session_id="s1",
        source_surface="api",
        source_context={"workspace_root": "", "project_id": ""},
    )
    assert res is not None
    assert "vool-local-product" not in str(res["response"]).lower()


# --- Codex #3: hard tool-boundary invariant — workspace ops confine to the bound root ---

def test_workspace_confinement_allows_local_named_folder_inside_root(tmp_path):
    import pytest

    from core.execution.workspace_tools import resolve_workspace_path

    root = (tmp_path / "web0").resolve()
    root.mkdir()
    (root / "Local").mkdir()  # a real folder literally named "Local" INSIDE the bound root
    assert resolve_workspace_path("Local", workspace_root=root) == (root / "Local").resolve()
    assert resolve_workspace_path("Local/f.txt", workspace_root=root) == (root / "Local" / "f.txt").resolve()


def test_workspace_confinement_blocks_traversal_and_absolute_escape(tmp_path):
    import pytest

    from core.execution.workspace_tools import resolve_workspace_path

    root = (tmp_path / "web0").resolve()
    root.mkdir()
    with pytest.raises(ValueError):
        resolve_workspace_path("../../etc/hosts", workspace_root=root)
    with pytest.raises(ValueError):
        resolve_workspace_path("/etc/hosts", workspace_root=root)


def test_workspace_confinement_blocks_symlink_escape(tmp_path):
    import pytest

    from core.execution.workspace_tools import resolve_workspace_path

    root = (tmp_path / "web0").resolve()
    root.mkdir()
    outside = tmp_path / "secret"
    outside.mkdir()
    (outside / "s.txt").write_text("x")
    (root / "link").symlink_to(outside)  # a symlink escaping the project
    with pytest.raises(ValueError):
        resolve_workspace_path("link/s.txt", workspace_root=root)


def test_escape_failure_is_surfaced_clearly_not_cryptically(tmp_path):
    # An escape must still be REFUSED (containment is a hard boundary) but the message now NAMES the
    # bound project and points at the fix, instead of a bare "Path escapes the active workspace." --
    # so a legitimate caller can retry with an in-project path rather than hit a dead-end. The
    # load-bearing substring the containment gauntlet matches is preserved.
    import pytest

    from core.execution.workspace_tools import resolve_workspace_path

    root = (tmp_path / "web0").resolve()
    root.mkdir()
    with pytest.raises(ValueError) as exc:
        resolve_workspace_path("/etc/passwd", workspace_root=root)
    message = str(exc.value)
    assert "escapes the active workspace" in message.lower()   # containment substring, load-bearing
    assert "web0" in message                                   # names the bound project
    assert "relative to it" in message                         # actionable next step


def test_workspace_confinement_blocks_cross_project(tmp_path):
    import pytest

    from core.execution.workspace_tools import resolve_workspace_path

    a = (tmp_path / "projA").resolve()
    a.mkdir()
    b = (tmp_path / "projB").resolve()
    b.mkdir()
    (b / "b.txt").write_text("x")
    with pytest.raises(ValueError):  # bound to A, an absolute path into B is rejected
        resolve_workspace_path(str(b / "b.txt"), workspace_root=a)
