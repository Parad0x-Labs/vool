"""In a chat bound to a project, document asks read the project's own files (0.7.8 sweep block 3).
Fresh wording and fresh files. Contributor: sls_0x.
"""
from __future__ import annotations

import pytest

import core.agent_runtime.project_documents as lane


@pytest.fixture
def project(tmp_path, monkeypatch):
    (tmp_path / "Camping").mkdir()
    (tmp_path / "Camping" / "gear-checklist.md").write_text("- tent\n- headlamp\n- water filter\n", encoding="utf-8")
    (tmp_path / "car-notes.txt").write_text("Tyre change booked 14 March at Riga Auto.\nWiper fluid low.\n", encoding="utf-8")
    (tmp_path / "groceries.csv").write_text("item,qty\noats,2\nlentils,1\n", encoding="utf-8")
    (tmp_path / ".hidden").mkdir()
    (tmp_path / ".hidden" / "secret.txt").write_text("tyre secret", encoding="utf-8")
    monkeypatch.setattr("core.context_namespace.authoritative_chat_workspace", lambda _sid: (str(tmp_path), "project"))
    return tmp_path


def test_reading_a_named_list_returns_its_contents(project):
    answer = lane.project_document_answer("read me my gear checklist", session_id="s")
    assert "gear-checklist.md" in answer and "water filter" in answer


def test_which_files_mention_a_thing(project):
    answer = lane.project_document_answer("which of my files mentions the tyre change?", session_id="s")
    assert "car-notes.txt" in answer and "secret" not in answer


def test_where_did_i_write_shows_the_line(project):
    answer = lane.project_document_answer("where did i write down the tyre change", session_id="s")
    assert "car-notes.txt" in answer and "14 March" in answer


def test_whats_on_my_list(project):
    answer = lane.project_document_answer("what's on my groceries list?", session_id="s")
    assert "groceries.csv" in answer and "lentils" in answer


def test_nothing_found_says_where_it_looked(project):
    answer = lane.project_document_answer("which of my files mentions the dentist?", session_id="s")
    assert "none of them" in answer and "dentist" in answer


def test_an_unbound_chat_is_not_this_lanes(monkeypatch):
    monkeypatch.setattr("core.context_namespace.authoritative_chat_workspace", lambda _sid: ("", "unbound"))
    assert lane.project_document_answer("read me my gear checklist", session_id="s") is None


@pytest.mark.parametrize("said", ["what is a csv file?", "write me a packing list for Rome", "tell me a joke"])
def test_ordinary_asks_are_not_document_asks(project, said):
    assert lane.project_document_answer(said, session_id="s") is None
