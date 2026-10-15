"""Operator edits become signed, atomic service changes without a restart."""
import json

import pytest

from core.registry.codec import RegistryError, canonical, decode
from core.registry.publisher import add_signature
from registry_service import community, reports
from tests.registry.test_skill_codex_authority import creator, rows, upload_with_icon
from tests.registry.test_skill_codex_github_signer import FOLDER, checkout, on_github, run, signer, team_steps
from tests.registry.test_skill_codex_github_signer import world as world
from tests.registry.test_skill_pictures import ICON


def prepared(world):
    alice, token, csrf = creator(world, "101", "alice")
    upload_with_icon(world, token, csrf, "1.0.0", ICON)
    run(world)
    bob, _, _ = creator(world, "102", "bob", account_age=0)
    community.record_download(world.store, bob, "alice/notes", now=world.clock.t)
    comment = community.post_comment(world.store, bob, "alice/notes", "It made the notes clearer.", now=world.clock.t)
    assert comment["state"] == "held"
    return alice, bob, comment["id"]


def test_steps_publish_comments_pause_resume_mute_and_curate_without_restart(world):
    _, bob, comment = prepared(world)
    run(world)
    state = json.loads(on_github(world, FOLDER + "/state.json"))
    assert state["moderation"]["comments"] == [comment]
    assert b"It made the notes clearer" not in on_github(world, FOLDER + "/state.json")
    picture = state["moderation"]["pictures"][0]
    steps = (f"comment-publish {comment}\neditors-pick alice/notes\nfeatured alice/notes\n"
             f"picture alice/notes {picture['sha256']}\nreviewer bob community\n"
             "review alice/notes 1.0.0 bob 2026-10-15\n")
    team_steps(world, steps)
    run(world)
    row = rows(world)["alice/notes"]
    assert row["editors_pick"] and row["featured"] and row["icon"]["sha256"] == picture["sha256"]
    assert row["community_reviews"] == [{"reviewer": "bob", "role": "community", "version": "1.0.0", "date": "2026-10-15"}]
    assert community.comments(world.store, "alice/notes")["comments"][0]["id"] == comment
    team_steps(world, steps + "skill-pause alice/notes\nmute bob\n")
    run(world)
    assert rows(world)["alice/notes"]["downloads_paused"]
    assert not community.comments(world.store, "alice/notes")["comments"]
    team_steps(world, steps + "skill-resume alice/notes\nunmute bob\n")
    run(world)
    assert "downloads_paused" not in rows(world)["alice/notes"]
    assert community.comments(world.store, "alice/notes", bob)["can_comment"]
    team_steps(world, f"comment-remove {comment}\n")
    run(world)
    assert not rows(world)["alice/notes"]["editors_pick"] and "icon" not in rows(world)["alice/notes"]
    with world.store.connection() as db:
        assert db.execute("SELECT body,state FROM comments WHERE id=?", (comment,)).fetchone()[:] == ("", "removed")


def test_bad_syntax_applies_no_operator_actions_or_curation(world):
    _, _, comment = prepared(world)
    team_steps(world, f"comment-publish {comment}\neditors-pick alice/notes\npicture alice/notes bad\n")
    result = run(world)
    assert result["steps"]["applied"] is False
    assert not rows(world)["alice/notes"]["editors_pick"]
    assert community.held(world.store)[0]["id"] == comment


def test_semantic_failure_rolls_back_every_action_and_the_marks(world):
    _, _, comment = prepared(world)
    authority = signer(world, checkout(world))
    marks = {"schema": "vool.registry.marks.v1", "sequence": 100, "issued_at": world.clock.t,
             "tested": [], "highlighted": [], "suspended": [], "teams": {}, "operations": [
                 {"action": "comment-publish", "target": comment},
                 {"action": "comment-remove", "target": "0" * 32}]}
    before = canonical(world.store.marks())
    with pytest.raises(RegistryError, match="comment not found"):
        world.store.set_marks(add_signature({"signed": marks, "signatures": []}, authority.admission_key),
                              world.trust.root, now=world.clock.t)
    assert canonical(world.store.marks()) == before and community.held(world.store)[0]["id"] == comment


def test_invalid_operator_target_does_not_remove_a_skill_from_signer_state(world):
    prepared(world)
    team_steps(world, "removed alice/notes\ncomment-remove " + "0" * 32 + "\n")
    with pytest.raises(RegistryError, match="comment not found"):
        run(world)
    state = decode(on_github(world, FOLDER + "/state.json"))
    assert [item["signed"]["metadata"]["id"] for item in state["entries"]] == ["alice/notes"]


def test_report_closure_and_reporter_mute_from_steps_keep_private_text_out_of_state(world):
    _, bob, _ = prepared(world)
    reports.file_report(world.store, bob, {"id": "alice/notes", "reason": "broken", "note": "PRIVATE_REPORT_TEXT"}, now=world.clock.t)
    run(world)
    state = decode(on_github(world, FOLDER + "/state.json"))
    report, = state["moderation"]["reports"]
    assert "PRIVATE_REPORT_TEXT" not in json.dumps(state)
    team_steps(world, f"reporter-mute {report}\nreports-close alice/notes\n")
    run(world)
    assert reports.open_reports(world.store) == []
    with world.store.connection() as db:
        assert db.execute("SELECT note FROM skill_reports WHERE id=?", (report,)).fetchone()[0] == ""
        assert db.execute("SELECT 1 FROM muted_accounts WHERE account=?", (bob,)).fetchone()
