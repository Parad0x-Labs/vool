"""CRASH-ROW JOURNALING — operator-authorized harness slice, 2026-08-20.

The turn loop's old exception path printed and continued: no journal row, no
session_turns entry. A crashed turn's evidence self-deleted, and review-184040
graded a 181-prompt run as 180 without knowing a turn was missing. Law 4: every
accepted input produces an ordered raw row — crashes included, with the exact
input, the partial tape, the crash state, and the visible failure bytes.
"""
import contextlib
import json

from core.kernel import consensus, repl
from core.kernel.effects import EffectJournal


def _rows(path):
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def test_crashed_turn_journals_atomically_in_both_stores(tmp_path, monkeypatch):
    monkeypatch.setattr(repl, "_SESSION_LOG", tmp_path / "j.jsonl")
    monkeypatch.setattr(repl, "_RUN_ID", "run-crash-a")
    session_turns = [("earlier ok turn", "COMMIT\n\x01fine\x02", "10:00:00", None)]
    partial = EffectJournal()

    line = repl._journal_crash(
        "Answer items A through G exactly once.",
        ValueError("evidence type error: unit ungrounded"),
        partial, session_turns)

    assert line.startswith("turn failed, named: ValueError")
    # session_turns got the marker (prompt count == transcript count survives)
    assert len(session_turns) == 2
    assert session_turns[1][0] == "Answer items A through G exactly once."
    assert session_turns[1][1] == line
    # the raw journal got an ordered row carrying the crash state
    rows = _rows(tmp_path / "j.jsonl")
    assert len(rows) == 1
    row = rows[0]
    assert row["crashed"] is True
    assert row["crash_exception"] == "ValueError: evidence type error: unit ungrounded"
    assert row["question"] == "Answer items A through G exactly once."
    assert row["turn_index"] == 2                     # its true position, not an afterthought
    assert row["answer_present"] is False             # a crash ships nothing
    assert row["tape"] is not None                    # the partial tape rides the row


def test_normal_turns_are_not_marked_crashed(tmp_path, monkeypatch):
    monkeypatch.setattr(repl, "_SESSION_LOG", tmp_path / "j.jsonl")
    monkeypatch.setattr(repl, "_RUN_ID", "run-crash-b")
    repl._journal_turn("q", "COMMIT\n\x01ok\x02", None, mode="record", turn_index=1)
    row = _rows(tmp_path / "j.jsonl")[0]
    assert row["crashed"] is False and row["crash_exception"] is None


def test_review_counts_the_crash_and_digest_names_it(tmp_path, monkeypatch):
    """A run with a crashed turn opens a review whose turn count MATCHES the prompt
    count, and the digest STATE names the crash instead of '(no state line)'."""
    monkeypatch.setattr(repl, "_SESSION_LOG", tmp_path / "j.jsonl")
    monkeypatch.setattr(repl, "_RUN_ID", "run-crash-c")
    session_turns = [("q1", "COMMIT\n\x01a1\x02", "10:00:00", None)]
    repl._journal_turn("q1", "COMMIT\n\x01a1\x02", None, mode="record", turn_index=1)
    repl._journal_crash("q2 crashes", RuntimeError("boom"), None, session_turns)

    review = consensus.open_review(session_turns, "buildX", bus=tmp_path / "bus",
                                   run_id="run-crash-c", journal_path=str(tmp_path / "j.jsonl"))
    ctx = (review / "FULL_REVIEW_CONTEXT.md").read_text()
    assert "2 turns" in ctx                                   # both counted — nothing vanished
    assert "turn failed, named: RuntimeError: boom" in ctx    # the crash is NAMED
    assert "STATE: turn failed, named: RuntimeError: boom" in ctx


def test_sabotage_old_print_and_continue_path_loses_the_turn(tmp_path, monkeypatch):
    """SABOTAGE (names the cause): the OLD path — print and continue without
    _journal_crash — leaves the journal one row short of the prompts, which is
    exactly the reconciliation hole review-184040 fell into: expected_turns from
    the driver no longer matches and the review refuses to open."""
    import pytest
    monkeypatch.setattr(repl, "_SESSION_LOG", tmp_path / "j.jsonl")
    monkeypatch.setattr(repl, "_RUN_ID", "run-crash-d")
    session_turns = [("q1", "COMMIT\n\x01a1\x02", "10:00:00", None)]
    repl._journal_turn("q1", "COMMIT\n\x01a1\x02", None, mode="record", turn_index=1)
    # crash happens; OLD behavior journals nothing, appends nothing
    with pytest.raises(consensus.FragmentedTranscriptError):
        consensus.open_review(session_turns, "buildX", bus=tmp_path / "bus",
                              expected_turns=2,      # the driver sent two prompts
                              run_id="run-crash-d", journal_path=str(tmp_path / "j.jsonl"))


# ---- ALL-OR-STOP durability (round-5 P0-HOLD: Codex's Law-4 counterexample) ----

def test_journal_write_failure_raises_persistence_error(tmp_path, monkeypatch):
    """A failed canonical append RAISES (the old best-effort swallow silently split
    the stores). The caller stops accepting prompts."""
    import pytest
    blocked = tmp_path / "not-a-dir" / "j.jsonl"
    monkeypatch.setattr(repl, "_SESSION_LOG", blocked)
    # make mkdir fail: parent path exists as a FILE
    (tmp_path / "not-a-dir").write_text("a file, not a dir")
    with pytest.raises(repl.JournalPersistenceError):
        repl._journal_turn("q", "COMMIT\n\x01a\x02", None, mode="record", turn_index=1)


def test_crash_journal_first_failure_leaves_both_stores_unchanged(tmp_path, monkeypatch):
    """Journal-first ordering: when the canonical append fails during a crash
    record, session_turns is UNTOUCHED — the stores cannot diverge."""
    import pytest
    blocked = tmp_path / "blocked" / "j.jsonl"
    (tmp_path / "blocked").write_text("file blocks mkdir")
    monkeypatch.setattr(repl, "_SESSION_LOG", blocked)
    session_turns = [("q1", "COMMIT\n\x01a1\x02", "10:00:00", None)]
    with pytest.raises(repl.JournalPersistenceError):
        repl._journal_crash("q2", RuntimeError("boom"), None, session_turns)
    assert len(session_turns) == 1                     # untouched — no divergence


def test_tape_serialization_failure_degrades_inside_the_row(tmp_path, monkeypatch):
    """A tape that will not serialize must NOT lose the turn: the row lands with
    its exact answer fields and a named tape_error, and nothing raises."""
    monkeypatch.setattr(repl, "_SESSION_LOG", tmp_path / "j.jsonl")
    monkeypatch.setattr(repl, "_RUN_ID", "run-ser")

    class BadTape:
        def to_json(self):
            raise ValueError("unserializable effect payload")
    repl._journal_turn("q", "COMMIT\n\x0142\x02", BadTape(), mode="record", turn_index=1)
    row = _rows(tmp_path / "j.jsonl")[0]
    assert row["answer"] == "42"                       # the row LANDED, exact
    assert row["tape"]["tape_error"].startswith("ValueError")


def test_sabotage_best_effort_swallow_recreates_the_divergence(tmp_path, monkeypatch):
    """SABOTAGE (names the cause): with the old swallow semantics — append to
    session_turns first, journal best-effort — a failed append leaves the
    transcript one turn AHEAD of the canonical journal: the exact count split
    Codex's P0-HOLD proved. The fixed path raises instead (test above)."""
    blocked = tmp_path / "blocked2" / "j.jsonl"
    (tmp_path / "blocked2").write_text("file blocks mkdir")
    monkeypatch.setattr(repl, "_SESSION_LOG", blocked)
    session_turns = [("q1", "t1", "10:00:00", None)]
    # OLD semantics, simulated: transcript append first, journal swallowed
    session_turns.append(("q2", "turn failed", "10:00:01", None))
    with contextlib.suppress(repl.JournalPersistenceError):   # old code swallowed this
        repl._journal_turn("q2", "turn failed", None, mode="record", turn_index=2)
    assert len(session_turns) == 2                     # transcript claims 2 turns...
    assert not blocked.exists()                        # ...canonical journal has none
