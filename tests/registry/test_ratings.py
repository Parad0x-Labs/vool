"""Ratings: only someone who got the add-on through the install route can rate it, once, and nothing else is kept."""
import inspect
import json
import re
import secrets
import time

import pytest

from registry_service.api import PREFIX
from tests.registry.test_skill_codex_authority import ORIGIN, SKILL, creator, draft
from tests.registry.test_skill_codex_github_signer import FOLDER, on_github, run, upload
from tests.registry.test_skill_codex_github_signer import world as world

DAY = 86400
NOTES = "alice/notes"


def listed(world, *names):
    """Alice publishes her skills; the signer admits them."""
    alice, token, csrf = creator(world, "101", "alice")
    for name in names or ("notes",):
        answer = draft(world, token, csrf, "alice/" + name, SKILL.replace("name: notes", "name: " + name))
        assert answer.status_code == 201 and answer.json()["state"] == "checking", answer.text
    run(world)
    return alice


def later(world, days):
    """Time passes; the signer keeps running and the service keeps its list fresh, as both do every few minutes."""
    while days > 0:
        world.clock.t += min(days, 3) * DAY
        days -= 3
        run(world)
        world.online.refresh(now=world.clock.t)


def install_pass(world, package_id=NOTES, version="1.0.0"):
    """What VOOL does on install: ask for a ticket, fetch the package, keep the pass that comes with it."""
    ticket = world.http.get(PREFIX + "/download", params={"id": package_id, "version": version})
    assert ticket.status_code == 200, ticket.text
    served = world.http.get(ticket.json()["url"])
    assert served.status_code == 200, served.text
    return served.headers["x-vool-review-pass"]


def person(world, subject, name=None, *, age=60 * DAY):
    who = world.store.identity("google", subject, now=world.clock.t - age)
    if name:
        world.store.profile(who, {"namespace": name, "bio": "", "handles": {}}, now=world.clock.t)
    return who


def act(world, who, method, path, **options):
    """A signed-in browser request, with the page's own origin and CSRF header."""
    token, csrf = world.store.session(who, now=world.clock.t)
    world.http.cookies.set("vool_registry", token)
    try:
        return world.http.request(method, PREFIX + path, headers={"Origin": ORIGIN, "X-Vool-CSRF": csrf}, **options)
    finally:
        world.http.cookies.clear()


def rate(world, who, **body):
    return act(world, who, "PUT", "/ratings", json={"id": NOTES, **body})


def page(world, package_id=NOTES, who=None):
    if who:
        return act(world, who, "GET", "/ratings", params={"id": package_id}).json()
    return world.http.get(PREFIX + "/ratings", params={"id": package_id}).json()


def raters(world, count, *, start=200, stars=5, package_id=NOTES, version="1.0.0"):
    """`count` people install today and rate tomorrow. Returns their accounts."""
    passes = [install_pass(world, package_id, version) for _ in range(count)]
    later(world, 1)
    people = [person(world, str(start + index), "rater" + str(start + index)) for index in range(count)]
    for who, handed in zip(people, passes, strict=True):
        answer = act(world, who, "PUT", "/ratings", json={"id": package_id, "stars": stars, "pass": handed})
        assert answer.status_code == 200, answer.text
    return people


def test_a_rating_needs_an_install_pass_and_a_browser_download_alone_is_refused(world):
    listed(world)
    bob = person(world, "2", "bob")
    # Bob downloads the package in his browser, signed in. That is not an install and leaves no trace of him.
    token, _ = world.store.session(bob, now=world.clock.t)
    world.http.cookies.set("vool_registry", token)
    install_pass(world)
    world.http.cookies.clear()
    refused = rate(world, bob, stars=5)
    assert refused.status_code == 400 and refused.json() == {"error": "install pass required"}
    with world.store.connection() as db:
        for (table,) in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
            for row in db.execute(f"SELECT * FROM {table}").fetchall():
                assert not (bob in tuple(row) and NOTES in tuple(row)), table
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='account_downloads'").fetchone()
    assert page(world, who=bob)["can_rate"] is True and page(world, who=bob)["pass_needed"] is True
    assert rate(world, bob, stars=5, state="published").json() == {"error": "invalid rating fields"}


def test_a_pass_is_spent_once_only_for_its_add_on_and_only_in_its_time(world):
    alice = listed(world, "notes", "tasks")
    with world.store.connection() as db:
        sha = db.execute("SELECT sha256 FROM public_versions WHERE package_id=?", (NOTES,)).fetchone()[0]
    ticket = world.http.get(PREFIX + "/download", params={"id": NOTES, "version": "1.0.0"}).json()
    first, again = world.http.get(ticket["url"]), world.http.get(ticket["url"])
    handed = first.headers["x-vool-review-pass"]
    assert again.headers["x-vool-review-pass"] == handed  # a retried ticket never yields a second pass
    other, spare, own = install_pass(world, "alice/tasks"), install_pass(world), install_pass(world)
    bob, carol = person(world, "2", "bob"), person(world, "3", "carol")
    day = time.strftime("%Y-%m-%d", time.gmtime(world.clock.t + DAY))
    young = rate(world, bob, stars=5, **{"pass": handed})
    assert young.status_code == 400 and young.json() == {"error": "install pass not valid yet; you can rate from " + day}
    later(world, 1)
    assert rate(world, bob, stars=6, **{"pass": handed}).json() == {"error": "stars must be 1 to 5"}
    assert rate(world, bob, stars=True, **{"pass": handed}).json() == {"error": "stars must be 1 to 5"}
    done = rate(world, bob, stars=5, body="It made the notes clearer.", **{"pass": handed})
    assert done.status_code == 200 and done.json()["state"] == "published" and done.json()["counted"] is True
    assert rate(world, carol, stars=1, **{"pass": handed}).json() == {"error": "install pass already used"}
    assert rate(world, carol, stars=1, **{"pass": other}).json() == {"error": "install pass is for another add-on"}
    forged = handed[:-2] + ("AA" if not handed.endswith("AA") else "BB")
    assert "can't be read" in rate(world, carol, stars=1, **{"pass": forged}).json()["error"]
    assert rate(world, alice, stars=5, **{"pass": own}).json() == {"error": "you cannot rate your own add-on"}
    old = world.store.review_pass(sha, world.store.ticket(sha, now=world.clock.t - 181 * DAY))
    assert rate(world, carol, stars=1, **{"pass": old}).json() == {"error": "install pass expired"}
    assert rate(world, carol, stars=1, **{"pass": spare}).status_code == 200
    with world.store.connection() as db:
        # The used-pass table says which add-on and which day, and never who.
        assert [row[1] for row in db.execute("PRAGMA table_info(review_passes_spent)")] == ["digest", "package_id", "day"]
        assert db.execute("SELECT count(*) FROM review_passes_spent").fetchone()[0] == 2
        accounts = {row[0] for row in db.execute("SELECT id FROM accounts")}
        assert not accounts & {value for row in db.execute("SELECT * FROM review_passes_spent") for value in tuple(row)}
        # Handing a pass out writes nothing: only the two used ones are known.
        assert handed not in str(db.execute("SELECT * FROM review_passes_spent").fetchall())


def test_one_rating_per_account_per_add_on_and_edits_keep_the_reviewed_version(world):
    listed(world)
    handed, second = install_pass(world), install_pass(world)
    later(world, 1)
    bob = person(world, "2", "bob")
    first = rate(world, bob, stars=2, body="Too slow for long notes.", **{"pass": handed}).json()
    edited = rate(world, bob, stars=4, body="Faster since the update.", show_name=False).json()  # no pass needed to edit
    assert edited["id"] == first["id"] and edited["state"] == "published"
    listing = page(world)
    assert [(item["stars"], item["body"], item["version"], item["proof"], item["author"]) for item in listing["ratings"]] == [
        (4, "Faster since the update.", "1.0.0", "installed", "VOOL user")]
    # A second pass does not make a second rating.
    assert rate(world, bob, stars=5, **{"pass": second}).json()["id"] == first["id"]
    with world.store.connection() as db:
        assert db.execute("SELECT count(*) FROM ratings").fetchone()[0] == 1
    mine = page(world, who=bob)["mine"]
    assert mine["stars"] == 5 and mine["state"] == "published"
    refused = rate(world, bob, stars=5, body="free sol airdrop, dm me")
    assert refused.status_code == 400 and refused.json()["state"] == "refused" and "Rating text" in refused.json()["reason"]
    assert page(world)["ratings"][0]["body"] == ""  # the earlier edit stands; the refused text was never kept
    assert act(world, bob, "DELETE", "/ratings", params={"id": NOTES}).json() == {"status": "deleted"}
    assert act(world, bob, "DELETE", "/ratings", params={"id": NOTES}).json() == {"error": "rating not found"}
    assert page(world)["ratings"] == []
    assert rate(world, bob, stars=5).json() == {"error": "install pass required"}  # and the used passes stay used
    assert rate(world, bob, stars=5, **{"pass": handed}).json() == {"error": "install pass already used"}


def test_fewer_than_five_ratings_show_no_average_and_deleting_an_account_removes_its_rating(world):
    listed(world)
    people = raters(world, 4, stars=5)
    summary = page(world)["summary"]
    assert (summary["shown"], summary["average"], summary["count"], summary["distribution"]) == (False, None, 4, None)
    feed = world.http.get(PREFIX + "/ratings/summary").json()
    assert feed == {"ratings": [{"id": NOTES, "shown": False, "average": None, "count": 4, "removed": 0}]}
    fifth = install_pass(world)
    later(world, 1)
    erin = person(world, "9", "erin")
    assert rate(world, erin, stars=2, **{"pass": fifth}).status_code == 200
    summary = page(world)["summary"]
    assert (summary["shown"], summary["average"], summary["count"]) == (True, 4.4, 5)
    assert summary["distribution"] == [0, 1, 0, 0, 4] and summary["scope"] == "all"
    assert world.http.get(PREFIX + "/ratings/summary").json()["ratings"][0] == {
        "id": NOTES, "shown": True, "average": 4.4, "count": 5, "removed": 0}
    # /presentation keeps the exact shape shipped apps read.
    shown = world.http.get(PREFIX + "/presentation").json()
    assert set(shown) == {"marks", "metrics"} and set(shown["metrics"][0]) == {"id", "stars", "download_requests"}
    # One of them deletes their account: the rating goes with it and the average is taken back.
    assert act(world, people[0], "DELETE", "/session").json() == {"status": "deleted"}
    summary = page(world)["summary"]
    assert (summary["shown"], summary["average"], summary["count"]) == (False, None, 4)
    with world.store.connection() as db:
        assert db.execute("SELECT count(*) FROM ratings WHERE account=?", (people[0],)).fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM review_passes_spent").fetchone()[0] == 5  # no account to delete there


def write(world, who, package_id, stars, *, version="1.0.0", at=None):
    """A stored rating, for the tests whose subject is the arithmetic, not the pass."""
    with world.store.connection() as db:
        db.execute("INSERT INTO ratings (id,account,package_id,stars,body,version,artifact_sha256,proof,state,show_name,"
                   "created,updated) VALUES (?,?,?,?,'',?,?,'installed','published',1,?,?)",
                   (secrets.token_hex(16), who, package_id, stars, version, "0" * 64, at or world.clock.t, at or world.clock.t))


def test_three_fives_never_outrank_three_hundred_at_four_point_four(world):
    from registry_service import ratings

    listed(world, "notes", "tasks", "plans")
    crowd = [person(world, str(1000 + index)) for index in range(300)]
    for index, who in enumerate(crowd):
        write(world, who, NOTES, 5 if index < 180 else 4 if index < 240 else 3)  # 300 ratings, 4.4 on average
    for who in crowd[:3]:
        write(world, who, "alice/tasks", 5)
    for who in crowd[:6]:
        write(world, who, "alice/plans", 3)
    feed = world.http.get(PREFIX + "/ratings/summary").json()["ratings"]
    assert [(item["id"], item["shown"], item["average"], item["count"]) for item in feed] == [
        (NOTES, True, 4.4, 300),            # 300 people at 4.4 come first
        ("alice/plans", True, 3.0, 6),
        ("alice/tasks", False, None, 3)]    # three ratings show no average and sort last
    assert round(ratings.score([5] * 3), 2) == 3.85 and round(ratings.score([5] * 180 + [4] * 60 + [3] * 60), 2) == 4.37


def test_new_accounts_rate_but_do_not_count_for_seven_days_and_their_text_waits(world):
    listed(world)
    handed, texted = install_pass(world), install_pass(world)
    later(world, 1)
    fresh, wordy = person(world, "2", "fresh", age=0), person(world, "3", "wordy", age=0)
    answer = rate(world, fresh, stars=5, **{"pass": handed}).json()
    assert answer["state"] == "published" and answer["counted"] is False
    held = rate(world, wordy, stars=5, body="Best skill I have used.", **{"pass": texted}).json()
    assert held["state"] == "held" and held["reason"] == "waiting for review"
    assert page(world)["summary"]["count"] == 0 and len(page(world)["ratings"]) == 1  # shown, not counted; held text hidden
    assert page(world, who=wordy)["mine"]["state"] == "held"
    later(world, 7)
    assert page(world)["summary"]["count"] == 1


def test_a_burst_of_ratings_is_held_and_reaches_the_teams_queue(world):
    from registry_service import ratings

    listed(world)
    passes = [install_pass(world) for _ in range(12)]
    later(world, 1)
    states = [rate(world, person(world, str(300 + index)), stars=1, **{"pass": handed}).json() for index, handed in enumerate(passes)]
    assert [item["state"] for item in states] == ["published"] * 10 + ["held"] * 2
    assert page(world)["summary"]["count"] == 10
    waiting = ratings.pending(world.store)
    assert waiting["counts"] == {"ratings": 2, "appeals": 0} and sorted(waiting["ratings"]) == sorted(item["id"] for item in states[10:])
    run(world)
    state = json.loads(on_github(world, FOLDER + "/state.json"))
    assert state["moderation_ratings"] == waiting  # ids and counts for the team, in the signer's public state
    assert state["moderation"].keys() == {"counts", "comments", "reports", "pictures"}  # the older answer keeps its shape


def test_repeated_text_is_held(world):
    listed(world)
    passes = [install_pass(world) for _ in range(3)]
    later(world, 1)
    people = [person(world, str(400 + index)) for index in range(3)]
    first = rate(world, people[0], stars=5, body="A really great skill, saved me hours!", **{"pass": passes[0]}).json()
    same = rate(world, people[1], stars=5, body="a REALLY great skill - saved me hours", **{"pass": passes[1]}).json()
    other = rate(world, people[2], stars=5, body="Clear and short.", **{"pass": passes[2]}).json()
    assert (first["state"], same["state"], other["state"]) == ("published", "held", "published")


def test_three_new_accounts_cannot_hide_a_rating_and_the_creator_cannot_report_one(world):
    alice = listed(world)
    handed = install_pass(world)
    later(world, 1)
    bob = person(world, "2", "bob")
    target = rate(world, bob, stars=1, body="It dropped half my notes.", **{"pass": handed}).json()["id"]
    path = "/ratings/" + target + "/report"
    for index in range(3):
        refused = act(world, person(world, str(500 + index), age=DAY), "POST", path)
        assert refused.status_code == 400 and refused.json() == {"error": "new accounts can report a rating after 14 days"}
    assert act(world, alice, "POST", path).json() == {"error": "the creator of an add-on cannot report its ratings"}
    assert act(world, bob, "POST", path).json() == {"error": "you cannot report your own rating"}
    assert [item["id"] for item in page(world)["ratings"]] == [target]
    for index in range(3):
        assert act(world, person(world, str(600 + index)), "POST", path).json() == {"status": "reported"}
    assert page(world)["ratings"] == []  # three people with older accounts: out of sight until the team looks
    with world.store.connection() as db:
        assert db.execute("SELECT state FROM ratings WHERE id=?", (target,)).fetchone()[0] == "held"


def test_a_new_major_version_gets_its_own_headline_after_five_ratings(world):
    alice, token, csrf = creator(world, "101", "alice")
    assert draft(world, token, csrf, NOTES).status_code == 201
    run(world)
    crowd = [person(world, str(700 + index)) for index in range(12)]
    for who in crowd[:6]:
        write(world, who, NOTES, 2)
    token, csrf = world.store.session(alice, now=world.clock.t)
    upload(world, token, csrf, NOTES, version="2.0.0")
    run(world)
    for who in crowd[6:10]:
        write(world, who, NOTES, 5, version="2.0.0")
    summary = page(world)["summary"]
    assert (summary["scope"], summary["count"], summary["average"]) == ("all", 10, 3.2)
    write(world, crowd[10], NOTES, 5, version="2.0.0")
    summary = page(world)["summary"]
    assert (summary["scope"], summary["count"], summary["average"]) == ("current_major", 5, 5.0)


def test_a_web_session_cannot_moderate_whoever_it_belongs_to(world):
    alice = listed(world)
    handed = install_pass(world)
    later(world, 1)
    bob = person(world, "2", "bob")
    target = rate(world, bob, stars=1, **{"pass": handed}).json()["id"]
    paths = [route.path for route in world.http.app.routes if "/authority/" in route.path]
    assert PREFIX + "/authority/marks" in paths and PREFIX + "/authority/moderation/ratings" in paths
    for who in (alice, bob):
        for path in paths:
            for method in ("GET", "POST"):
                answer = act(world, who, method, path.replace("{identifier}", target).replace("{sha256}", "0" * 64)[len(PREFIX):],
                             json={"operations": [{"action": "rating-void", "target": target, "reason": "fake"}]})
                assert answer.status_code in (400, 405), (path, method)
                assert answer.status_code == 405 or answer.json() == {"error": "authority signature required"}
    # No other route takes a moderation word either.
    known = {"/ratings", "/ratings/summary", "/ratings/{identifier}/report", "/moderation/notices", "/appeals"}
    assert known <= {route.path[len(PREFIX):] for route in world.http.app.routes}
    assert [item["id"] for item in page(world)["ratings"]] == [target]


def test_nothing_new_stores_an_address_a_user_agent_or_a_device_id(world):
    from registry_service import ratings

    tracking = re.compile(r"(^|_)(ip|addr|address|agent|device|fingerprint|browser|host|referer|referrer|cookie)(_|$)")
    new_tables = {"ratings", "review_passes_spent", "rating_reports", "rating_freezes", "banned_accounts", "moderation_log",
                  "moderation_notices", "appeals"}
    with world.store.connection() as db:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert new_tables <= tables
        for table in new_tables:
            for row in db.execute(f"PRAGMA table_info({table})").fetchall():
                assert not tracking.search(row[1]), (table, row[1])
    source = inspect.getsource(ratings).casefold()
    for word in ("request.client", "user-agent", "user_agent", "x-forwarded", "remote_addr", "request.headers", "device_id", ".client.host"):
        assert word not in source, word
    # The routes hand the rating code the account, the JSON body and the clock, and nothing about the connection.
    from registry_service import api

    handlers = inspect.getsource(api)
    start = handlers.index("# ---- ratings, the team's notices and appeals ----")
    block = handlers[start:handlers.index("async def github_import", start)].casefold()
    for word in ("request.client", "user-agent", "x-forwarded", "request.headers"):
        assert word not in block, word


@pytest.mark.parametrize("query", ["?id=alice/notes&after=zz", "?id=alice/notes&sort=stars", "?id=bob/none"])
def test_rating_reads_refuse_what_they_do_not_know(world, query):
    listed(world)
    answer = world.http.get(PREFIX + "/ratings" + query)
    assert answer.status_code == 400 and answer.json()["error"] in {"invalid cursor", "unknown ratings query", "this add-on is not listed"}
