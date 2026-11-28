"""An approval card names a place in the person's home as ~/..., never the full disk path with the account name.

Benchmark on d9a520dca (Manual mode, "make a new folder called ..."): the card said "Affected: /Users/<name>/Desktop/
<folder>", while every machine.* card shows ~/... . The place stays exact; only the home prefix is written as ~.
"""
from __future__ import annotations

from pathlib import Path

from core.mode_permission_policy import _affected_resources_display


def test_a_folder_under_home_is_shown_from_home(tmp_path, monkeypatch):
    home = tmp_path / "home" / "rasa"
    (home / "Desktop").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    context = {"workspace": str(home / "Desktop")}
    assert _affected_resources_display({"path": "Vasaros nuotraukos"}, context) == ["~/Desktop/Vasaros nuotraukos"]
    assert _affected_resources_display({"path": str(home / "Dokumente" / "Steuer")}, context) == ["~/Dokumente/Steuer"]
    assert _affected_resources_display({"cwd": str(home)}, context) == ["~"]


def test_a_place_outside_home_and_non_path_fields_stay_as_they_are(tmp_path, monkeypatch):
    home = tmp_path / "home" / "rasa"
    home.mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    other = tmp_path / "home" / "rasa-shared" / "x.txt"
    assert _affected_resources_display({"path": str(other)}, {}) == [str(other)]
    shown = _affected_resources_display({"recipient": "someone@example.com", "url": "https://example.com/x"}, {})
    assert sorted(shown) == ["https://example.com/x", "someone@example.com"]
