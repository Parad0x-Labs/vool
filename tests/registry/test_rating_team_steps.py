"""The team's signed steps about ratings and listings: what each changes for the public, and the record each leaves."""
import json
import time
from types import SimpleNamespace

import pytest

from core.registry.codec import RegistryError, decode
from registry_service import cli
from registry_service.api import PREFIX
from registry_service.github_authority import parse_steps
from registry_service.ratings import REASONS
from tests.registry.test_ratings import NOTES, act, later, listed, page, person, rate, write
from tests.registry.test_skill_codex_authority import rows
from tests.registry.test_skill_codex_github_signer import FOLDER, app_rows, on_github, run, team_steps
from tests.registry.test_skill_codex_github_signer import world as world

RATING = "0" * 31 + "1"


def log(world):
    with world.store.connection() as db:
        return [dict(row) for row in db.execute("SELECT * FROM moderation_log ORDER BY id")]


def step(world, text):
    """The team commits its steps file and the signer runs. Returns the rows this added to the record."""
    before = len(log(world))
    team_steps(world, text)
    run(world)
    return log(world)[before:]


def notices(world, package_id=NOTES):
    answer = world.http.get(PREFIX + "/moderation/notices", params={"id": package_id})
    assert answer.status_code == 200, answer.text
    return answer.json()["notices"]


def rated_world(world):
    """Alice's add-on with seven counted ratings: bob and carol by name, five more at five stars."""
    alice = listed(world)
    bob, carol = person(world, "2", "bob"), person(world, "3", "carol")
    crowd = [person(world, str(800 + index)) for index in range(5)]
    write(world, bob, NOTES, 1, at=world.clock.t - 3600)
    write(world, carol, NOTES, 2, at=world.clock.t - 7200)
    for who in crowd:
        write(world, who, NOTES, 5, at=world.clock.t - 7200)
    with world.store.connection() as db:
        db.execute("UPDATE ratings SET body='Does not work at all.' WHERE account=?", (carol,))
        ids = {name: db.execute("SELECT id FROM ratings WHERE account=?", (who,)).fetchone()[0]
               for name, who in (("bob", bob), ("carol", carol))}
    return SimpleNamespace(alice=alice, bob=bob, carol=carol, crowd=crowd, ids=ids)


def test_each_void_freeze_ban_and_restore_step_changes_what_the_public_reads_and_writes_one_row(world):
    people = rated_world(world)
    start = page(world)["summary"]
    assert (start["count"], start["average"], start["removed_by_team"], start["frozen"]) == (7, 4.0, 0, False)

    added = step(world, f"rating-void {people.ids['bob']} fake\n")
    assert len(added) == 1 and (added[0]["action"], added[0]["target"], added[0]["reason"], added[0]["affected"]) == (
        "rating-void", people.ids["bob"], "fake", 1)
    assert added[0]["created"] == world.clock.t and added[0]["sequence"] > 0 and len(added[0]["step"]) == 64
    summary = page(world)["summary"]
    assert (summary["count"], summary["average"], summary["removed_by_team"]) == (6, 4.5, 1)
    assert people.ids["bob"] not in [item["id"] for item in page(world)["ratings"]]
    assert notices(world)[0] == {"case": str(added[0]["id"]), "action": "rating-void", "reason": "fake",
                                 "sentence": REASONS["fake"], "affected": 1,
                                 "date": time.strftime("%Y-%m-%d", time.gmtime(world.clock.t))}
    assert world.http.get(PREFIX + "/ratings/summary").json()["ratings"][0]["removed"] == 1
    # The person it was done to reads why, and cannot bring the rating back by writing or deleting it.
    mine = page(world, who=people.bob)
    assert mine["mine"]["state"] == "voided" and mine["mine"]["sentence"] == REASONS["fake"] and mine["can_rate_reason"] == "removed"
    assert "removed this rating" in rate(world, people.bob, stars=5).json()["error"]
    assert "removed this rating" in act(world, people.bob, "DELETE", "/ratings", params={"id": NOTES}).json()["error"]
    # The same file again applies nothing twice.
    assert step(world, f"rating-void {people.ids['bob']} fake\n# a note\n") == []

    added = step(world, f"rating-void {people.ids['bob']} fake\nreview-text-remove {people.ids['carol']} abusive\n")
    assert [(row["action"], row["reason"], row["affected"]) for row in added] == [("review-text-remove", "abusive", 1)]
    carol = next(item for item in page(world)["ratings"] if item["id"] == people.ids["carol"])
    assert (carol["stars"], carol["body"], carol["text_removed"]) == (2, "", True)  # the stars stay, the words go
    assert page(world)["summary"]["count"] == 6

    kept = f"rating-void {people.ids['bob']} fake\nreview-text-remove {people.ids['carol']} abusive\n"
    added = step(world, kept + "ratings-freeze alice/notes automated\n")
    assert [(row["action"], row["target"], row["reason"]) for row in added] == [("ratings-freeze", NOTES, "automated")]
    assert page(world)["summary"]["frozen"] is True
    assert rate(world, people.crowd[0], stars=1).json() == {"error": "ratings are closed for this add-on"}
    assert page(world, who=people.crowd[0])["can_rate_reason"] == "frozen"
    added = step(world, kept + "ratings-unfreeze alice/notes\n")
    assert [(row["action"], row["reason"]) for row in added] == [("ratings-unfreeze", None)]
    assert page(world)["summary"]["frozen"] is False and rate(world, people.crowd[0], stars=4).status_code == 200

    added = step(world, kept + "account-ban carol paid\n")
    assert [(row["action"], row["target"], row["reason"], row["affected"]) for row in added] == [("account-ban", "carol", "paid", 1)]
    assert rate(world, people.carol, stars=5).json() == {"error": "rating is turned off for this account"}
    summary = page(world)["summary"]
    assert (summary["count"], summary["removed_by_team"]) == (5, 2)  # a banned account's ratings read as removed
    added = step(world, kept + "account-unban carol\n")
    assert [row["action"] for row in added] == ["account-unban"] and page(world)["summary"]["count"] == 6

    added = step(world, f"review-text-remove {people.ids['carol']} abusive\nrating-restore {people.ids['bob']}\n")
    assert [(row["action"], row["target"]) for row in added] == [("rating-restore", people.ids["bob"])]
    summary = page(world)["summary"]
    assert (summary["count"], summary["removed_by_team"]) == (7, 0)
    assert [item["action"] for item in notices(world)] == ["rating-restore", "account-unban", "account-ban", "ratings-unfreeze",
                                                          "ratings-freeze", "review-text-remove", "rating-void"]
    assert all(set(item) == {"case", "action", "reason", "sentence", "date", "affected"} for item in notices(world))


def test_voiding_by_account_and_by_time_window_takes_back_a_bombing(world):
    people = rated_world(world)
    added = step(world, "ratings-void-account bob self_rating\n")
    assert [(row["action"], row["target"], row["reason"], row["affected"]) for row in added] == [
        ("ratings-void-account", "bob", "self_rating", 1)]
    assert page(world)["summary"]["count"] == 6
    # Ten one-star ratings land in one hour.
    bombers = [person(world, str(900 + index)) for index in range(10)]
    hour = world.clock.t - world.clock.t % 3600
    for who in bombers:
        write(world, who, NOTES, 1, at=hour + 60)
    assert page(world)["summary"]["average"] == 2.3
    stamp = lambda moment: time.strftime("%Y-%m-%dT%H", time.gmtime(moment))  # noqa: E731
    added = step(world, f"ratings-void-account bob self_rating\nratings-void-window alice/notes {stamp(hour)} {stamp(hour + 3600)} automated\n")
    assert [(row["action"], row["target"], row["affected"]) for row in added] == [("ratings-void-window", NOTES, 10)]
    summary = page(world)["summary"]
    assert (summary["count"], summary["average"], summary["removed_by_team"]) == (6, 4.5, 11)  # as before the bombing
    assert notices(world)[0]["affected"] == 10 and notices(world)[0]["sentence"] == REASONS["automated"]


def test_a_step_with_an_unknown_or_missing_reason_is_refused_by_the_signer_and_applies_nothing(world):
    people = rated_world(world)
    for line, message in (
            (f"rating-void {people.ids['bob']} spite", "the reason must be one of: fake, automated"),
            (f"rating-void {people.ids['bob']}", 'write it as "rating-void rating-id reason"'),
            ("ratings-freeze alice/notes", 'write it as "ratings-freeze publisher/name reason"'),
            ("delisted alice/notes because", "the reason must be one of"),
            ("delisted alice/notes", 'write it as "delisted publisher/name reason"'),
            ("removed alice/notes whim", "the reason must be one of"),
            ("rating-erase " + people.ids["bob"] + " fake", "this is not a step"),
            ("rating-void not-an-id fake", "invalid target for rating-void"),
            ("ratings-void-window alice/notes 2026-01-02T00 2026-01-01T00 fake", "the window must end after it starts"),
            ("ratings-void-window alice/notes yesterday today fake", "write the hour as"),
            (f"rating-void {people.ids['bob']} fake\nrating-restore {people.ids['bob']}", "conflicting or repeated")):
        with pytest.raises(RegistryError, match="team-steps.txt line [12]: .*" + message.replace("(", r"\(").replace(")", r"\)")):
            parse_steps(line + "\n")
    # In the real run, one bad line stops the whole file: the good line beside it is not applied either.
    team_steps(world, f"rating-void {people.ids['carol']} fake\nrating-void {people.ids['bob']} spite\n")
    with pytest.raises(RegistryError, match="line 2"):
        run(world)
    assert log(world) == [] and page(world)["summary"]["count"] == 7
    # A step that reads well but fits nothing stops the signed update inside the service, too.
    team_steps(world, f"rating-void {people.ids['carol']} fake\nrating-void {RATING} fake\n")
    with pytest.raises(RegistryError, match="rating not found"):
        run(world)
    assert log(world) == [] and page(world)["summary"]["count"] == 7


def test_delisted_leaves_the_list_but_revokes_nothing_and_removed_carries_its_reason(world):
    listed(world, "notes", "tasks")
    assert set(rows(world)) == {NOTES, "alice/tasks"} and set(app_rows(world)) == {NOTES, "alice/tasks"}
    added = step(world, "delisted alice/notes misleading\n")
    assert [(row["action"], row["target"], row["reason"]) for row in added] == [("delisted", NOTES, "misleading")]
    assert set(rows(world)) == {"alice/tasks"}
    assert set(app_rows(world)) == {"alice/tasks"}  # VOOL's own Browse hides it too, from the signed marks
    refused = world.http.get(PREFIX + "/download", params={"id": NOTES, "version": "1.0.0"})
    assert refused.status_code == 400 and refused.json() == {"error": "this add-on is no longer listed"}
    assert world.http.get(PREFIX + "/ratings", params={"id": NOTES}).json() == {"error": "this add-on is not listed"}
    assert notices(world)[0]["action"] == "delisted" and notices(world)[0]["sentence"] == REASONS["misleading"]
    # Nothing is revoked, so a copy someone installed keeps working: the signed list still carries the version.
    snapshot = world.http.get(PREFIX + "/snapshot").json()["signed"]
    assert snapshot["revoked"] == [] and NOTES in {item["signed"]["metadata"]["id"] for item in snapshot["entries"]}
    assert NOTES not in {row["id"] for row in decode(on_github(world, FOLDER + "/registry.json"))["entries"]}
    # The signed marks gained a key; the public feed keeps its two keys, which is all a shipped app checks.
    shown = world.http.get(PREFIX + "/presentation").json()
    assert set(shown) == {"marks", "metrics"} and shown["marks"]["signed"]["delisted"] == [{"id": NOTES, "reason": "misleading"}]

    added = step(world, "")
    assert [(row["action"], row["target"]) for row in added] == [("relisted", NOTES)]
    assert set(rows(world)) == {NOTES, "alice/tasks"} and set(app_rows(world)) == {NOTES, "alice/tasks"}

    added = step(world, "removed alice/tasks harmful\n")
    assert [(row["action"], row["target"], row["reason"]) for row in added] == [("removed", "alice/tasks", "harmful")]
    assert set(rows(world)) == {NOTES}
    assert len(world.http.get(PREFIX + "/snapshot").json()["signed"]["revoked"]) == 1  # installed copies stop
    assert notices(world, "alice/tasks")[0] == {"case": str(added[0]["id"]), "action": "removed", "reason": "harmful",
                                               "sentence": REASONS["harmful"], "affected": 0,
                                               "date": time.strftime("%Y-%m-%d", time.gmtime(world.clock.t))}


def test_the_older_steps_leave_a_row_in_the_record_too(world):
    listed(world)
    added = step(world, "skill-pause alice/notes\n")
    assert [(row["action"], row["target"], row["reason"]) for row in added] == [("skill-pause", NOTES, None)]
    assert notices(world)[0]["action"] == "skill-pause"


def operator(world, monkeypatch, capsys, *command):
    config = world.tmp / "service.json"
    config.write_text(json.dumps({"schema": "vool.registry.service.v1", "private_dir": str(world.store.path.parent)}))
    capsys.readouterr()
    code = cli.main([command[0], "--config", str(config), *command[1:]])
    return code, capsys.readouterr().out


def test_an_appeal_reaches_the_queue_and_its_answer_reaches_the_person(world, monkeypatch, capsys):
    people = rated_world(world)
    case = str(step(world, f"rating-void {people.ids['bob']} fake\n")[0]["id"])
    assert page(world, who=people.bob)["mine"]["case"] == case
    ask = lambda who, **body: act(world, who, "POST", "/appeals", json=body)  # noqa: E731
    assert ask(people.carol, case=case, text="Please look again.").json() == {
        "error": "this is not your case; only the creator or the person who wrote the rating can ask"}
    assert ask(people.bob, case="999", text="Please look again.").json() == {"error": "case not found"}
    assert ask(people.bob, case=case, text="").json() == {"error": "describe the problem in the note"}
    assert "at most 1000" in ask(people.bob, case=case, text="x" * 1001).json()["error"]
    sent = ask(people.bob, case=case, text="I installed it and it lost my notes. The rating is mine.")
    assert sent.status_code == 201 and sent.json()["state"] == "open"
    assert ask(people.bob, case=case, text="Again.").json() == {"error": "you already asked for another look; the team will answer"}
    assert ask(people.alice, case=case, text="That rating was fair to remove.").status_code == 201  # the creator's case too
    run(world)
    state = json.loads(on_github(world, FOLDER + "/state.json"))
    assert state["moderation_ratings"]["counts"]["appeals"] == 2 and sent.json()["id"] in state["moderation_ratings"]["appeals"]
    assert b"lost my notes" not in on_github(world, FOLDER + "/state.json")
    code, out = operator(world, monkeypatch, capsys, "appeals-open")
    waiting = {item["id"]: item for item in json.loads(out)}
    assert code == 0 and waiting[sent.json()["id"]]["text"].startswith("I installed it") and waiting[sent.json()["id"]]["from"] == "bob"
    assert waiting[sent.json()["id"]]["action"] == "rating-void" and waiting[sent.json()["id"]]["reason"] == "fake"
    assert operator(world, monkeypatch, capsys, "appeal-close", "--target", sent.json()["id"], "--outcome", "maybe")[0] == 1
    assert operator(world, monkeypatch, capsys, "appeal-close", "--target", sent.json()["id"], "--outcome", "upheld")[0] == 0
    assert operator(world, monkeypatch, capsys, "appeal-close", "--target", sent.json()["id"], "--outcome", "upheld")[0] == 1
    mine = act(world, people.bob, "GET", "/appeals").json()["appeals"]
    assert mine == [{"id": sent.json()["id"], "case": case, "state": "upheld", "answer": "The team looked again and is undoing the step."}]
    with world.store.connection() as db:
        assert db.execute("SELECT text FROM appeals WHERE id=?", (sent.json()["id"],)).fetchone()[0] == ""  # no text kept
    # Closing an appeal changes no rating: undoing is a signed step.
    assert page(world)["summary"]["removed_by_team"] == 1
    step(world, f"rating-restore {people.ids['bob']}\n")
    assert page(world)["summary"]["removed_by_team"] == 0
    code, out = operator(world, monkeypatch, capsys, "ratings-held")
    assert code == 0 and json.loads(out) == []


def test_removed_text_is_kept_thirty_days_for_a_second_look_then_erased(world):
    people = rated_world(world)
    step(world, f"review-text-remove {people.ids['carol']} off_topic\n")
    later(world, 29)
    world.store.prune(now=world.clock.t)
    with world.store.connection() as db:
        assert db.execute("SELECT removed_body FROM ratings WHERE id=?", (people.ids["carol"],)).fetchone()[0] == "Does not work at all."
    later(world, 2)
    world.store.prune(now=world.clock.t)
    with world.store.connection() as db:
        assert db.execute("SELECT removed_body, body FROM ratings WHERE id=?", (people.ids["carol"],)).fetchone()[:] == (None, "")
