from __future__ import annotations

import sqlite3

from core import scoreboard_engine


class _UnclosableConn:
    def __init__(self, real: sqlite3.Connection) -> None:
        self._real = real

    def close(self) -> None:
        pass

    def __getattr__(self, name: str):
        return getattr(self._real, name)


def test_get_peer_scoreboard_defaults_cleanly_without_scoreboard_table(monkeypatch):
    raw = sqlite3.connect(":memory:")
    raw.row_factory = sqlite3.Row
    conn = _UnclosableConn(raw)

    monkeypatch.setattr(scoreboard_engine, "get_connection", lambda db_path=None: conn)

    board = scoreboard_engine.get_peer_scoreboard("peer-test")

    assert board.provider == 0.0
    assert board.validator == 0.0
    assert board.trust == 0.0
    assert board.tier == "Newcomer"
    assert board.glory_score == 0.0
    assert board.finality_ratio == 0.0


def test_get_glory_leaderboard_handles_missing_scoreboard_table(monkeypatch):
    raw = sqlite3.connect(":memory:")
    raw.row_factory = sqlite3.Row
    conn = _UnclosableConn(raw)

    monkeypatch.setattr(scoreboard_engine, "get_connection", lambda db_path=None: conn)

    assert scoreboard_engine.get_glory_leaderboard() == []
    assert scoreboard_engine.get_season_leaderboard() == []
