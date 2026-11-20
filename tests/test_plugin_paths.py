"""Names joined under plugin stores stay directly inside them."""
from __future__ import annotations

import pytest

from core.plugin_paths import child_of, inside


@pytest.mark.parametrize("name", ["..", "../x", "a/b", "/etc/passwd", "", "."])
def test_child_of_refuses_names_that_leave_or_nest(tmp_path, name):
    with pytest.raises(ValueError):
        child_of(tmp_path, name)


def test_child_of_keeps_a_link_visible_to_its_caller(tmp_path):
    (tmp_path / "real").mkdir()
    (tmp_path / "link").symlink_to(tmp_path / "real")
    assert child_of(tmp_path, "link") == tmp_path / "link"


@pytest.mark.parametrize("relative", ["../x", ".", "out/../../x"])
def test_inside_refuses_paths_outside_or_equal_to_the_root(tmp_path, relative):
    with pytest.raises(ValueError):
        inside(tmp_path, relative)


def test_inside_follows_links_before_checking(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    root = tmp_path / "root"
    root.mkdir()
    (root / "bin").symlink_to(outside)
    with pytest.raises(ValueError):
        inside(root, "bin/tool")
    (root / "tool").write_text("")
    assert inside(root, "tool") == (root / "tool").resolve()
