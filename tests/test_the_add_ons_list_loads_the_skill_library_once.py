"""The add-ons page lists the built-in skills from one skill library load, not one per row.

Measured on CI #681 (the v0.8 CI branch, b9530c3c with 6cc33224): after two chat turns the
served plugin journey opened My add-ons and waited on "Loading…" past its 10 s step. The page
reads `/api/plugins`, whose built-in entry asked `shown_to_people` row by row, and every row
loaded the whole skill library again: 27 loads and 30 plugin-folder probes for one read. A
settled daemon answered in about 1 s; right after a chat turn it took 6 to 10 s.

The developer skills stay hidden from a person and listed with the developer setting on.
"""
from __future__ import annotations

import pytest

from tests.test_native_skill_library import _iso_home, make_native_skill


@pytest.fixture()
def library(tmp_path, monkeypatch):
    from core import native_skill_library as nsl

    _iso_home(tmp_path, monkeypatch)
    monkeypatch.setenv("VOOL_PLUGIN_LIFECYCLE_PATH", str(tmp_path / "home" / "plugin_lifecycle.json"))
    monkeypatch.delenv("VOOL_DEVELOPER_SKILLS", raising=False)
    root = tmp_path / "native-skills"
    root.mkdir()
    monkeypatch.setenv("VOOL_NATIVE_SKILLS_DIR", str(root))
    for index in range(8):
        make_native_skill(root, f"everyday-{index}")
    make_native_skill(root, "release-gate-check", overrides={"audience": "developer"})
    nsl.reset_contract_caches()
    yield root
    nsl.reset_contract_caches()


def _listed() -> list[str]:
    from core.plugin_catalog import _native_library_entry

    entry = _native_library_entry() or {"skills": []}
    return sorted(skill["name"] for skill in entry["skills"])


def test_the_built_in_list_loads_the_skill_library_once(library, monkeypatch) -> None:
    from core import native_skill_library as nsl

    loads: list[int] = []
    real = nsl.load_skill_contracts

    def counted():
        loads.append(1)
        return real()

    monkeypatch.setattr(nsl, "load_skill_contracts", counted)
    assert _listed() == [f"everyday-{index}" for index in range(8)]
    nine_rows = len(loads)
    for index in range(8, 24):
        make_native_skill(library, f"everyday-{index}")
    nsl.reset_contract_caches()
    loads.clear()
    assert len(_listed()) == 24
    # The inventory and the developer marks: the same few loads for 9 rows or 25, never one a row.
    assert len(loads) == nine_rows <= 3, (nine_rows, len(loads))


def test_a_developer_skill_is_listed_only_with_the_developer_setting(library, monkeypatch) -> None:
    from core.native_skill_library import developer_skill_ids, shown_to_people

    assert "release-gate-check" not in _listed()
    assert developer_skill_ids() == frozenset({"release-gate-check"})
    assert not shown_to_people("Release-Gate-Check")
    assert not shown_to_people("release-gate-check", developer=developer_skill_ids())
    assert shown_to_people("everyday-1", developer=developer_skill_ids())
    monkeypatch.setenv("VOOL_DEVELOPER_SKILLS", "1")
    assert "release-gate-check" in _listed()


def test_a_skill_marked_developer_after_the_last_list_is_hidden_by_the_next(library) -> None:
    from core import native_skill_library as nsl

    assert "everyday-3" in _listed()
    make_native_skill(library, "everyday-3", overrides={"audience": "developer"})
    nsl.reset_contract_caches()
    assert "everyday-3" not in _listed()
