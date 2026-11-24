"""Who can pause a skill by reporting it, for how long, and what stays in sight of the team.

Three different people with accounts two weeks old pause an ordinary skill for 48 hours; a team skill or a
tested version is never paused by reports alone; a person who deletes their account neither hides a report
nor gets a second count. Real service handlers and signer; only the review provider's answers are scripted."""
import pytest

from core.registry.codec import DOWNLOADS_PAUSED, RegistryError
from registry_service import community, reports
from registry_service.api import PREFIX
from registry_service.operations import pending
from tests.registry.helpers import NOW, rated
from tests.registry.test_skill_codex import account, service
from tests.registry.test_skill_codex_authority import DAY, ORIGIN, creator, draft
from tests.registry.test_skill_codex_authority import world as world
from tests.registry.test_skill_codex_github_signer import upload

HOUR = 3600
MEMBER = "240776969"


def publish(world, subject, package_id):
    who, token, csrf = creator(world, subject, package_id.split("/")[0])
    assert draft(world, token, csrf, package_id).json()["state"] == "checking"
    assert world.mac.run_once()["admitted"] == [package_id + " 1.0.0"]
    return who, token, csrf


def reader(world, subject, *, age=30 * DAY):
    """A signed-in person whose Skill Codex account is `age` old."""
    return world.store.identity("github", subject, now=world.clock.t - age)


def file(world, who, package_id="alice/notes", reason="scam", **extra):
    return reports.file_report(world.store, who, {"id": package_id, "reason": reason, **extra}, now=world.clock.t)


def later(world, seconds):
    world.clock.t += seconds
    world.mac.run_once()  # the signed list stays fresh, as the signer's runs keep it
    world.online.refresh(now=world.clock.t)


def paused(world, package_id="alice/notes", version="1.0.0"):
    response = world.http.get(PREFIX + "/download", params={"id": package_id, "version": version})
    if response.status_code == 200:
        return False
    assert response.json() == {"error": DOWNLOADS_PAUSED}, response.text
    return True


def leave(world, who):
    """The website's own "delete my account"."""
    token, csrf = world.store.session(who, now=world.clock.t)
    world.http.cookies.set("vool_registry", token)
    response = world.http.request("DELETE", PREFIX + "/session", headers={"Origin": ORIGIN, "X-Vool-CSRF": csrf})
    world.http.cookies.clear()
    assert response.json() == {"status": "deleted"}, response.text


def view(world):
    return {item["package_id"]: item for item in reports.open_reports(world.store, now=world.clock.t)}


def test_a_pause_from_reports_alone_ends_after_48_hours_unless_a_team_step_confirms_it(world):
    publish(world, "101", "alice/notes")
    publish(world, "102", "bob/plain")
    people = [reader(world, str(200 + index)) for index in range(7)]
    for who in people[:3]:
        file(world, who)
        file(world, who, "bob/plain")
    assert paused(world) and paused(world, "bob/plain")
    reports.pause(world.store, "bob/plain", now=world.clock.t)  # the team looked and keeps this one paused

    later(world, 48 * HOUR - 60)
    assert paused(world) and paused(world, "bob/plain")
    later(world, 61)
    assert not paused(world)  # nobody confirmed it, so downloads continue
    assert paused(world, "bob/plain")  # the team's own pause has no end date
    assert "downloads_paused" not in {row["id"]: row for row in world.http.get(PREFIX + "/catalog").json()["entries"]}[
        "alice/notes"]

    # The team still sees all of it: the reports stay open and the ended pause is named as ended.
    seen = view(world)
    assert seen["alice/notes"]["paused"] is None and seen["alice/notes"]["pause_lapsed"] is True
    assert seen["alice/notes"]["pause_lapses"] == seen["alice/notes"]["paused_since"] + 48 * HOUR
    assert len(seen["alice/notes"]["reports"]) == 3 and seen["alice/notes"]["counting"] == 3
    assert seen["bob/plain"]["paused"] == "operator" and "pause_lapsed" not in seen["bob/plain"]
    assert reports.paused(world.store, now=world.clock.t) == frozenset({"bob/plain"})
    assert pending(world.store, now=world.clock.t)["counts"]["paused"] == 1

    # More reports on the same unanswered pile do not pause it again and again.
    file(world, people[3])
    assert not paused(world)
    # Once the team has looked and closed them, three new people can pause it afresh.
    reports.close(world.store, "alice/notes")
    for who in people[4:7]:
        file(world, who)
    assert paused(world)
    later(world, 48 * HOUR + 1)
    assert not paused(world)


def test_reports_from_accounts_under_two_weeks_old_are_heard_but_do_not_pause(world):
    publish(world, "101", "alice/notes")
    for who in (reader(world, "200"), reader(world, "201"), reader(world, "202", age=14 * DAY - 60)):
        assert file(world, who) == {"status": "received"}
    assert not paused(world)
    assert [item["counted"] for item in view(world)["alice/notes"]["reports"]] == [True, True, False]
    assert view(world)["alice/notes"]["counting"] == 2
    file(world, reader(world, "203", age=14 * DAY))
    assert paused(world)


def test_leaving_and_signing_in_again_adds_no_second_count_and_reopens_nothing_the_team_closed(world):
    publish(world, "101", "alice/notes")
    publish(world, "102", "bob/plain")
    people = [reader(world, str(200 + index)) for index in range(3)]
    for who in people:
        file(world, who)
    for who in people[:2]:
        file(world, who, "bob/plain")
    assert paused(world) and not paused(world, "bob/plain")
    reports.close(world.store, "alice/notes")  # the team looked: nothing wrong with it
    assert not paused(world)

    for who in people:
        leave(world, who)
    again = [world.store.identity("github", str(200 + index), now=world.clock.t) for index in range(3)]
    assert not set(again) & set(people)
    later(world, 15 * DAY)  # the new accounts are old enough to count, were they new people
    for who in again:
        assert file(world, who) == {"status": "received"}  # heard, so a real new problem still reaches the team
    assert not paused(world)
    assert [item["counted"] for item in view(world)["alice/notes"]["reports"]] == [False] * 3

    # Their reports on the other skill are still open, so they cannot send those twice either.
    for who in again[:2]:
        assert reports.status(world.store, "bob/plain", who, now=world.clock.t)["can_report_reason"] == "already_reported"
        with pytest.raises(RegistryError, match="already reported"):
            file(world, who, "bob/plain")
    assert view(world)["bob/plain"]["counting"] == 2 and not paused(world, "bob/plain")
    file(world, again[2], "bob/plain")  # the third person never reported this one
    assert paused(world, "bob/plain")


def test_a_team_skill_and_a_tested_version_are_never_paused_by_reports_alone(world):
    _, alice_token, alice_csrf = publish(world, "101", "alice/notes")
    publish(world, "102", "bob/plain")
    _, token, csrf = creator(world, MEMBER, "sls")
    world.mac.act("team", "vool-team", members=[MEMBER])
    draft(world, token, csrf, "vool-team/helper")
    world.mac.act("team-admit", world.mac.run_once()["team_step_needed"][0])
    assert world.mac.run_once()["admitted"] == ["vool-team/helper 1.0.0"]
    world.mac.act("mark", "alice/notes")

    people = [reader(world, str(200 + index)) for index in range(4)]
    for who in people[:3]:
        for package_id in ("alice/notes", "bob/plain", "vool-team/helper"):
            file(world, who, package_id)
    assert paused(world, "bob/plain")
    assert not paused(world) and not paused(world, "vool-team/helper")
    # Kept and shown to the team, with the reason nothing was paused.
    seen = view(world)
    assert [(seen[name]["counting"], seen[name]["paused"], seen[name].get("protected")) for name in sorted(seen)] == [
        (3, None, "tested"), (3, "reports", None), (3, None, "team")]
    assert pending(world.store, now=world.clock.t)["counts"] == {"comments": 0, "reports": 9, "paused": 1, "pictures": 0}

    # The mark covers the tested bytes only: an update is an ordinary version again.
    world.clock.t += 1
    upload(world, alice_token, alice_csrf, "alice/notes", version="1.0.1")
    assert world.mac.run_once()["admitted"] == ["alice/notes 1.0.1"]
    file(world, people[3])
    assert paused(world, version="1.0.1") and "protected" not in view(world)["alice/notes"]
    # The team itself can always pause its own skill.
    reports.pause(world.store, "vool-team/helper", now=world.clock.t)
    assert paused(world, "vool-team/helper")


def test_deleted_reporters_leave_a_pause_the_team_can_still_trace_and_mute(world):
    publish(world, "101", "alice/notes")
    publish(world, "102", "bob/plain")
    people = [reader(world, str(200 + index)) for index in range(3)]
    for index, who in enumerate(people):
        file(world, who, note="It asks for my wallet words." if index == 0 else "")
    assert paused(world)
    for who in people:
        leave(world, who)

    listed = view(world)["alice/notes"]
    assert listed["paused"] == "reports" and listed["counting"] == 3
    assert [(item["reason"], item["note"], item["counted"], item["account_deleted"]) for item in listed["reports"]] == [
        ("scam", "", True, True)] * 3  # what the person typed went with the account
    assert len({item["reporter"] for item in listed["reports"]}) == 3
    with world.store.connection() as db:
        assert db.execute("SELECT count(*) FROM skill_reports WHERE account IS NOT NULL").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM identities WHERE subject IN ('200','201','202')").fetchone()[0] == 0

    # Muting the sender of a report still works after they left, and is waiting if they come back.
    reports.mute_reporter(world.store, listed["reports"][0]["id"], now=world.clock.t)
    assert [item["counted"] for item in view(world)["alice/notes"]["reports"]] == [False, True, True]
    back = world.store.identity("github", "200", now=world.clock.t)
    with world.store.connection() as db:
        assert db.execute("SELECT 1 FROM muted_accounts WHERE account=?", (back,)).fetchone()
    # Someone who already came back is muted on the spot.
    returned = world.store.identity("github", "201", now=world.clock.t)
    reports.mute_reporter(world.store, listed["reports"][1]["id"], now=world.clock.t)
    with world.store.connection() as db:
        assert db.execute("SELECT 1 FROM muted_accounts WHERE account=?", (returned,)).fetchone()
    later(world, 15 * DAY)
    file(world, returned, "bob/plain")
    assert view(world)["bob/plain"]["reports"][0]["counted"] is False  # a muted person's report never counts
    with pytest.raises(RegistryError, match="report not found"):
        reports.mute_reporter(world.store, "f" * 32, now=world.clock.t)


def test_a_comment_is_held_by_three_people_with_accounts_two_weeks_old(tmp_path):
    store, catalog, *_ = service(tmp_path, [("alice/notes", {}, NOW - 100)])
    bob = account(store, "2", "bob", created=NOW - 30 * DAY)[0]
    rated(store, bob, "alice/notes", now=NOW)
    comment = community.post_comment(store, bob, "alice/notes", "Clear and short", now=NOW)
    assert comment["state"] == "published"

    def state():
        with store.connection() as db:
            return db.execute("SELECT state FROM comments WHERE id=?", (comment["id"],)).fetchone()[0]

    old = [account(store, str(10 + index), None, created=NOW - 14 * DAY)[0] for index in range(3)]
    young = [account(store, str(20 + index), None, created=NOW - 14 * DAY + 60)[0] for index in range(3)]
    muted = account(store, "30", "loud", created=NOW - 30 * DAY)[0]
    community.mute(store, "loud", True, now=NOW)
    for who in (*young, muted, *old[:2]):
        assert community.report_comment(store, who, comment["id"], now=NOW) == {"status": "reported"}
    assert state() == "published"  # six reports, two of them from people who count
    community.report_comment(store, old[0], comment["id"], now=NOW)  # the same person again changes nothing
    assert state() == "published"
    community.report_comment(store, old[2], comment["id"], now=NOW)
    assert state() == "held"
    catalog.close()


def test_the_operators_command_lists_an_ended_pause_as_ended(tmp_path, capsys, monkeypatch):
    import json
    from types import SimpleNamespace

    from registry_service import cli

    store, catalog, *_ = service(tmp_path, [("alice/notes", {}, NOW - 100)])
    for index in range(3):
        who = account(store, str(10 + index), None, created=NOW - 30 * DAY)[0]
        reports.file_report(store, who, {"id": "alice/notes", "reason": "scam"}, now=NOW)
    config = tmp_path / "service.json"
    config.write_text(json.dumps({"schema": "vool.registry.service.v1", "private_dir": str(tmp_path / "private")}))
    for clock, paused_now, ended in ((NOW + 47 * HOUR, "reports", False), (NOW + 49 * HOUR, None, True)):
        monkeypatch.setattr(cli, "time", SimpleNamespace(time=lambda clock=clock: clock))
        assert cli.main(["reports-open", "--config", str(config)]) == 0
        [listed] = json.loads(capsys.readouterr().out)
        assert listed["paused"] == paused_now and listed.get("pause_lapsed", False) is ended
        assert listed["pause_lapses"] == NOW + 48 * HOUR and listed["counting"] == 3
    catalog.close()


def test_reports_whose_sign_ins_overlap_are_one_person():
    a, b, c, d = ("a" * 64, "b" * 64, "c" * 64, "d" * 64)
    assert community.people([]) == 0
    assert community.people([[a], [b], [c]]) == 3
    assert community.people([[a, b], [b], [c]]) == 2  # one account with two sign-ins, later back with one of them
    assert community.people([[a], [b], [a, b]]) == 1  # the third report ties the first two together
    assert community.people([[a, b], [c, d], [b, c]]) == 1
    assert community.people([[a], [], [a], []]) == 1  # a report tied to no sign-in is nobody we can count
