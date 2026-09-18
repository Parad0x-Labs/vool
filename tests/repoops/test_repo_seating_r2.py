"""Revision-2 seating seams, unit-proven in-process (fast, deterministic):

1. The repo-family demand signals: GitHub-management vocabulary seats `repo` exactly as the
   coding lane's repair vocabulary seats `code`, and ordinary prose does not.
2. `active_repo_session_intents`: an open RepoOps session journals the seats its current stage
   needs (and a planned forge action seats its own executor), TTL'd and ownership-checked --
   the same law as the code-task lane's active-task seats, without which a repository
   workflow loses its tools on the next conversational turn.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from core.tool_demand_signals import resolve_demand_signals


@pytest.mark.parametrize(
    "text",
    [
        "open a repo session for pull request 8 on o/r",
        "inspect issue 17 and PR 8, tell me which current checks failed",
        "prepare a draft PR description from the verified repair",
        "what failed in CI on the pull request",
        "review the merge request on gitlab",
        "post the approved comment on github issue 17",
        "bind the exact SHAs of the pull request",
        "open the draft pull request now",
    ],
)
def test_repository_vocabulary_seats_the_repo_family(text: str) -> None:
    signals = resolve_demand_signals(text)
    assert "repo" in signals.required_families, text
    assert "repo.session.open" in signals.explicit_intents, text


@pytest.mark.parametrize(
    "text",
    [
        "what is the capital of France",
        "write me a poem",
        "run the tests",
        "fix the failing test suite",
        "the press release mentions a PR agency",
        "read the PRD file",
        "draft a comment for my blog",
        "update the reviewed draft body",
        "book a table for dinner",
        "post the approved comment on issue 17",  # bare "issue" without forge vocabulary
    ],
)
def test_ordinary_prose_does_not_seat_the_repo_family(text: str) -> None:
    assert "repo" not in resolve_demand_signals(text).required_families, text


def test_open_session_seats_stage_tools_from_the_journal(tmp_path, monkeypatch) -> None:
    from core.repoops.plane import active_repo_session_intents, session_dir

    monkeypatch.setenv("VOOL_REPOOPS_DIR", str(tmp_path))
    payload = {
        "session_key": "rs-seat-1",
        "session_id": "chat-seat-1",
        "stage": "retrieve",
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "forge_plan": {"action": "create"},
    }
    (session_dir() / "rs-seat-1.json").write_text(json.dumps(payload), encoding="utf-8")

    seats = active_repo_session_intents({"session_id": "chat-seat-1"})
    assert "repo.session.open" in seats
    # The planned action's executor is seated FIRST among the stage seats (never evicted by
    # them), and the whole offer stays bounded at five seats like the code-task lane's.
    assert "repo.pr.create" in seats
    assert len(seats) <= 5
    assert seats.index("repo.pr.create") <= 1
    # Without a planned action the retrieve stage seats its own plan step.
    (session_dir() / "rs-seat-1.json").write_text(json.dumps({**payload, "forge_plan": {}}), encoding="utf-8")
    assert "repo.pr.request" in active_repo_session_intents({"session_id": "chat-seat-1"})
    # Ownership: another chat session gets nothing.
    assert active_repo_session_intents({"session_id": "chat-other"}) == ()
    # A stale journal (beyond the TTL) seats nothing.
    stale = dict(payload, session_key="rs-seat-2", updated_at="2020-01-01T00:00:00+00:00")
    (session_dir() / "rs-seat-2.json").write_text(json.dumps(stale), encoding="utf-8")
    assert active_repo_session_intents({"session_id": "chat-seat-1"})  # fresh one still seats
    (session_dir() / "rs-seat-1.json").unlink()
    assert active_repo_session_intents({"session_id": "chat-seat-1"}) == ()
