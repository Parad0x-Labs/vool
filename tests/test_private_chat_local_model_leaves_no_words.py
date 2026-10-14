"""Served proof: a private turn answered by the real local Ollama provider leaves none of its words on disk.

Only the HTTP answer is faked; the daemon, the ollama-local adapter and everything it records are real. The fake
model repeats the user's words back, as real models do, so any place that keeps the raw prompt or the raw model
answer shows up in a scan of every file under the daemon's home. The speed record (``local_inference_benchmarks``)
is checked by name: it keeps counts and durations for ordinary turns, and nothing for a private one.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from tests._blackbox_served_rig import ServedDaemon, run_in_home
from tests._served_sufficiency_provider import SEED
from tests.test_private_chat_served_workflow import MODEL, _certify, _files_holding, _history, _Provider, _stream

REPO = Path(__file__).resolve().parents[1]
PRIVATE_WORD = "Tamsinvale"  # authored marker for the private turn; appears nowhere else in the repo
ORDINARY_WORD = "Corrowick"  # authored marker for an ordinary turn


class _EchoingModel(_Provider):
    """Answers a chat turn by repeating the marker it was told, the way a real model echoes a message.

    While it is answering the private turn, the turn is still running: every file under the home is read then, so a
    row written at intake and blanked or deleted at the end of the turn (measured on the 0.7.1 Mac smoke:
    obligation_sets.snapshot_json held the request text for the 20-30 s a private turn ran) is caught in the act.
    """

    home: Path | None = None
    mid_turn_holders: list[str] | None = None

    def reply(self, model: str, body: dict[str, Any]) -> Any:
        sent = json.dumps(body)
        if PRIVATE_WORD in sent and self.home is not None and self.mid_turn_holders is None:
            self.mid_turn_holders = _files_holding(self.home, PRIVATE_WORD)
        for word in (PRIVATE_WORD, ORDINARY_WORD):
            if word in sent and not body.get("tools"):
                return f"Noted: {word} Hall on Friday."
        return super().reply(model, body)


def _benchmark_rows(home: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in home.rglob("*.db"):
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        try:
            if conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'local_inference_benchmarks'").fetchone():
                rows += [dict(row) for row in conn.execute("SELECT * FROM local_inference_benchmarks")]
        finally:
            conn.close()
    return rows


def test_a_private_turn_through_the_local_model_writes_none_of_its_words(tmp_path):
    home = tmp_path / "home"
    provider = _EchoingModel({MODEL: "Sounds lovely."})
    provider.home = home
    with provider:
        run_in_home(home, SEED.format(root=REPO, base_url=provider.base_url, models=[(MODEL, {})]))
        with ServedDaemon(home, env_extra={"VOOL_MODEL_LOAD_FLOOR_GB": "0"}) as daemon:
            assert _certify(daemon).get("state") == "verified"
            chat = "openclaw:" + "7d" * 10

            # An ordinary turn: saved as usual, and its speed is measured from the model's counts only.
            before = len(provider.calls)
            answer, _ = _stream(daemon, f"The book club meets at {ORDINARY_WORD} Hall.", chat)
            assert ORDINARY_WORD in answer, answer
            assert any(ORDINARY_WORD in call.get("full_prompt", "") for call in provider.calls[before:])
            measured = [row for row in _benchmark_rows(home) if row["provider_id"].startswith("ollama-local")]
            assert measured, "the ordinary turn recorded no speed fact"
            for row in measured:
                kept = json.loads(row["raw_result_json"])
                assert kept and set(kept) <= {"eval_count", "prompt_eval_count"}, kept
                assert row["prompt_hash"] == "", row
                assert ORDINARY_WORD not in json.dumps(row)
            rows_before_private = len(_benchmark_rows(home))

            switched, _ = _stream(daemon, "don't record this", chat)
            assert "Private chat is on" in switched, switched

            before = len(provider.calls)
            answer, commit = _stream(daemon, f"The surprise party is at {PRIVATE_WORD} Hall on Friday.", chat)
            assert commit.get("display_metadata", {}).get("private_chat") is True
            # The words really went through the local model and came back in its answer.
            assert any(PRIVATE_WORD in call.get("full_prompt", "") for call in provider.calls[before:])
            assert PRIVATE_WORD in answer, answer
            # Mid-turn, the database and its WAL already held nothing of the private words.
            assert provider.mid_turn_holders == [], provider.mid_turn_holders

            assert len(_benchmark_rows(home)) == rows_before_private, "the private turn recorded a speed fact"
            assert PRIVATE_WORD not in json.dumps(_history(daemon, chat))

            back, _ = _stream(daemon, "turn recording back on", chat)
            assert "Recording is back on" in back, back

        # After the daemon is stopped, every file it left behind, every database table included.
        assert _files_holding(home, PRIVATE_WORD) == []
        assert not any(PRIVATE_WORD in json.dumps(row) for row in _benchmark_rows(home))
