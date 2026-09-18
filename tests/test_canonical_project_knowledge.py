from __future__ import annotations

from pathlib import Path

import core.canonical_project_knowledge as canonical


def _write_source(root: Path) -> None:
    (root / "docs").mkdir()
    (root / "README.md").write_text(
        "# Product\n"
        "VOOL is the local runtime; vool remains its internal codename.\n\n"
        "## Web0\n"
        "Web0 is the project runtime and x402 is its payment rail.\n",
        encoding="utf-8",
    )
    for relative_path in (
        "AGENT_HANDOVER.md",
        "docs/SYSTEM_SPINE.md",
        "docs/STATUS.md",
        "docs/PROOF_PATH.md",
    ):
        path = root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# Other\nUnrelated text.\n", encoding="utf-8")


def test_retrieval_requires_matching_canonical_entity(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _write_source(tmp_path)
    monkeypatch.setattr(canonical, "project_path", lambda *parts: tmp_path.joinpath(*parts))

    passages = canonical.retrieve_canonical_passages("Explain Web0")

    assert len(passages) == 1
    assert passages[0].source_path == "README.md"
    assert passages[0].heading == "Web0"
    assert len(passages[0].content_hash) == 64
    assert canonical.retrieve_canonical_passages("What is the capital of France?") == ()
    vool_passages = canonical.retrieve_canonical_passages("What is VOOL?")
    assert len(vool_passages) == 1
    assert "VOOL is the local runtime" in vool_passages[0].content


def test_live_vool_identity_source_names_the_builder() -> None:
    passages = canonical.retrieve_canonical_passages(
        "A teammate asks what VOOL is and who builds it.",
        limit=5,
    )

    assert passages
    assert any("Parad0x Labs" in passage.content for passage in passages)


def test_entities_inside_windows_or_unc_paths_do_not_trigger_grounding(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _write_source(tmp_path)
    monkeypatch.setattr(canonical, "project_path", lambda *parts: tmp_path.joinpath(*parts))

    for query in (
        r"Inspect C:\work\vool-audit\README.md",
        r"Inspect \\server\share\vool-audit\README.md",
    ):
        assert canonical.retrieve_canonical_passages(query) == ()


def test_canonical_entity_detection_ignores_path_only_mentions() -> None:
    assert canonical.has_canonical_project_entity("What is VOOL and who builds it?") is True
    assert canonical.has_canonical_project_entity(r"Inspect C:\work\vool-audit\README.md") is False
    assert canonical.has_canonical_project_entity("What is photosynthesis?") is False


def test_context_provenance_never_exposes_absolute_paths(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _write_source(tmp_path)
    monkeypatch.setattr(canonical, "project_path", lambda *parts: tmp_path.joinpath(*parts))

    items = canonical.canonical_context_items("How does x402 work?")

    assert items
    for item in items:
        source_path = item.metadata["source_path"]
        assert not Path(source_path).is_absolute()
        assert str(tmp_path) not in item.title
        assert str(tmp_path) not in item.content
        assert item.metadata["scope"] == "project"
        assert item.metadata["source_class"] == "canonical"


def test_canonical_source_path_is_root_confined(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _write_source(tmp_path)
    monkeypatch.setattr(canonical, "project_path", lambda *parts: tmp_path.joinpath(*parts))

    try:
        canonical._allowed_path("../outside.md")
    except ValueError as exc:
        assert "escaped project root" in str(exc)
    else:
        raise AssertionError("path traversal was not rejected")
