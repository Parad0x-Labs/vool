"""A move never replaces what appeared at the destination while the consent prompt was open, and a
new file is never created through a folder that became a link (independent review 2026-10-28)."""
from __future__ import annotations

from pathlib import Path

import pytest

from core import machine_file_ops as ops


def _setup(tmp_path: Path) -> tuple[Path, Path, Path]:
    root = (tmp_path / "root").resolve()
    (root / "Documents").mkdir(parents=True)
    src = root / "report-draft.pdf"
    src.write_bytes(b"draft")
    return root, src, root / "Documents" / "report.pdf"


def test_a_file_saved_while_the_prompt_was_open_is_never_replaced(tmp_path):
    root, src, dst = _setup(tmp_path)

    def consent(_reason: str) -> bool:
        dst.write_bytes(b"the owner's own report")
        return True

    result = ops.move_path(str(src), str(dst), consent_fn=consent, protected=[], allowed_roots=[root])
    assert (result.ok, result.status) == (False, "dest_exists")
    assert dst.read_bytes() == b"the owner's own report" and src.read_bytes() == b"draft"


def test_a_folder_swapped_for_a_link_while_the_prompt_was_open_moves_nothing(tmp_path):
    root, src, dst = _setup(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    def consent(_reason: str) -> bool:
        (root / "Documents").rename(root / "Documents-real")
        (root / "Documents").symlink_to(elsewhere)
        return True

    result = ops.move_path(str(src), str(dst), consent_fn=consent, protected=[], allowed_roots=[root])
    assert not result.ok and result.status == "path_changed"
    assert src.exists() and list(elsewhere.iterdir()) == []


@pytest.mark.parametrize("native", [True, False])
def test_rename_no_replace_refuses_a_taken_name_for_files_and_folders(tmp_path, monkeypatch, native):
    if not native:
        monkeypatch.setattr(ops, "_native_rename_no_replace", lambda *a: False)
    base = tmp_path.resolve()
    (base / "a.txt").write_text("a")
    (base / "b.txt").write_text("b")
    with pytest.raises(FileExistsError):
        ops.rename_no_replace(base / "a.txt", base / "b.txt")
    assert (base / "b.txt").read_text() == "b"
    (base / "d1").mkdir()
    (base / "d2").mkdir()
    with pytest.raises(FileExistsError):
        ops.rename_no_replace(base / "d1", base / "d2")
    ops.rename_no_replace(base / "a.txt", base / "c.txt")
    ops.rename_no_replace(base / "d1", base / "d3")
    assert (base / "c.txt").read_text() == "a" and not (base / "a.txt").exists() and (base / "d3").is_dir()


def test_a_new_file_is_never_created_through_a_link(tmp_path):
    base = tmp_path.resolve()
    (base / "outside").mkdir()
    (base / "Notes").symlink_to(base / "outside")
    with pytest.raises(OSError):
        ops.create_file_exclusive(base / "Notes" / "x.txt", "x")
    assert list((base / "outside").iterdir()) == []
    ops.create_file_exclusive(base / "y.txt", "y")
    with pytest.raises(FileExistsError):
        ops.create_file_exclusive(base / "y.txt", "z")
    assert (base / "y.txt").read_text() == "y"
