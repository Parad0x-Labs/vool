"""MEDIA GOBLIN M1 — edit graph tests (G1–G8) + S2 sabotage.

Pure state tests: no worker, no DB required.
"""

from __future__ import annotations

import pytest

from core.media_edit_project import (
    ActiveMediaSelection,
    MediaEditError,
    MediaEditProject,
    SourceAsset,
    validate_operation,
)


def make_project() -> MediaEditProject:
    return MediaEditProject.create("mep-test", SourceAsset(
        asset_id="asset-abc", path="/tmp/clip.mp4", sha256="deadbeef",
        metadata={"duration": 10.0, "width": 1920, "height": 1080, "has_audio": True},
    ))


# ---------------------------------------------------------------- G1
def test_g1_gui_and_chat_trim_produce_same_graph():
    gui = make_project()
    chat = make_project()
    # human drags trim-start to 3.2s (end stays at media end)
    gui.apply("trim", {"start": 3.2, "end": 10.0}, actor="gui")
    # user says: "remove the first 3.2 seconds"
    chat.apply("trim", {"start": 3.2, "end": 10.0}, actor="chat")
    g = [op.to_dict() for op in gui.effective_ops()]
    c = [op.to_dict() for op in chat.effective_ops()]
    assert g[0]["kind"] == c[0]["kind"] == "trim"
    assert set(g[0]) == set(c[0]) == {"kind", "params", "actor", "revision"}
    assert list(g[0]["params"].keys()) == ["start", "end"]
    # the ONLY difference is the recorded actor label — semantics identical
    def strip(d, *skip):
        return {k: v for k, v in d.items() if k not in skip}
    assert strip(g[0], "actor") == strip(c[0], "actor")


def test_g1b_selection_driven_chat_op_matches_gui_drag():
    proj = make_project()
    proj.active_selection = ActiveMediaSelection(
        project_id=proj.project_id, asset_id=proj.source_asset.asset_id,
        range_start=1.5, range_end=4.0, playhead=2.0)
    expected = dict(proj.active_selection.to_dict())
    op = proj.apply("trim", {}, actor="chat", use_selection_range=True)
    assert op.params == {"start": expected["range_start"], "end": expected["range_end"]}
    # a GUI drag to the same handles yields byte-identical params
    other = make_project()
    other.apply("trim", {"start": 1.5, "end": 4.0}, actor="gui")
    assert other.effective_ops()[0].params == op.params


# ---------------------------------------------------------------- G2 / G3
def test_g2_undo_restores_previous_graph_and_g3_redo_restores_next():
    proj = make_project()
    proj.apply("trim", {"start": 1.0, "end": 9.0})
    rev_after_trim = proj.edit_revision
    before_undo = [op.to_dict() for op in proj.effective_ops()]
    proj.apply("crop", {"x": 0, "y": 0, "width": 100, "height": 100})
    assert proj.undo() is True
    assert [op.to_dict() for op in proj.effective_ops()] == before_undo
    assert proj.edit_revision == rev_after_trim
    assert proj.redo() is True
    assert len(proj.effective_ops()) == 2
    assert proj.redo() is False


def test_new_edit_invalidates_redo():
    proj = make_project()
    proj.apply("trim", {"start": 0.0, "end": 5.0})
    proj.apply("rotate", {"degrees": 90})
    proj.undo()
    proj.apply("resize", {"width": 640, "height": 360})
    assert proj.redo() is False


# ---------------------------------------------------------------- G4
def test_g4_reopen_project_restores_edit_graph():
    proj = make_project()
    proj.apply("trim", {"start": 2.0, "end": 7.0})
    proj.apply("set_aspect_ratio", {"ratio": "9:16"})
    proj.undo()
    doc = proj.to_dict()
    restored = MediaEditProject.from_dict(doc)
    assert restored.project_id == proj.project_id
    assert restored.source_asset.sha256 == proj.source_asset.sha256
    assert [o.to_dict() for o in restored.effective_ops()] == \
           [o.to_dict() for o in proj.effective_ops()]
    assert restored.edit_revision == proj.edit_revision
    assert restored.active_selection.to_dict() == proj.active_selection.to_dict()


# ---------------------------------------------------------------- G5
def test_g5_source_asset_identity_stable():
    proj = make_project()
    identity = (proj.source_asset.asset_id, proj.source_asset.sha256,
                proj.source_asset.path)
    for _ in range(5):
        proj.apply("trim", {"start": 0.0, "end": 1.0})
        proj.undo()
    assert (proj.source_asset.asset_id, proj.source_asset.sha256,
            proj.source_asset.path) == identity
    roundtrip = MediaEditProject.from_dict(proj.to_dict())
    assert roundtrip.source_asset.to_dict() == proj.source_asset.to_dict()


# ---------------------------------------------------------------- G6
def test_g6_crop_does_not_mutate_source_representation():
    proj = make_project()
    src_before = proj.source_asset.to_dict()
    meta_before = dict(src_before["metadata"])
    proj.apply("crop", {"x": 10, "y": 10, "width": 500, "height": 500})
    proj.apply("resize", {"width": 320, "height": 240})
    assert proj.source_asset.to_dict() == src_before
    assert proj.source_asset.metadata == meta_before
    # the graph records intent only; rendering happens elsewhere
    assert all(op.kind in {"crop", "resize"} for op in proj.effective_ops())


# ---------------------------------------------------------------- G7
def test_g7_selection_is_typed():
    sel = ActiveMediaSelection(project_id="p", asset_id="a",
                               range_start=1.0, range_end=2.0,
                               playhead=1.5, crop_selection=None, track="")
    d = sel.to_dict()
    assert set(d) == {"project_id", "asset_id", "range_start", "range_end",
                      "playhead", "crop_selection", "track"}
    assert ActiveMediaSelection.from_dict(d).to_dict() == d


# ---------------------------------------------------------------- G8
def test_g8_selected_range_targets_exact_selection():
    proj = make_project()
    proj.active_selection = ActiveMediaSelection(
        project_id=proj.project_id, asset_id="a", range_start=3.3, range_end=4.7)
    op = proj.apply("delete_range", {}, actor="chat", use_selection_range=True)
    assert op.params == {"start": 3.3, "end": 4.7}   # exact selection, not guessed


# ---------------------------------------------------------------- validation
def test_unknown_operation_kind_is_typed_rejected():
    with pytest.raises(MediaEditError):
        validate_operation("apply_blockbuster_filter", {})
    with pytest.raises(MediaEditError):
        make_project().apply("teleport", {})


def test_aspect_ratio_whitelist():
    for ok in ("16:9", "9:16", "1:1", "4:5"):
        validate_operation("set_aspect_ratio", {"ratio": ok})
    with pytest.raises(MediaEditError):
        validate_operation("set_aspect_ratio", {"ratio": "21:9"})
