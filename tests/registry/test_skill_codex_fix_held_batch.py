"""A held upload batch signs nothing of its own, while the list already signed stays fresh for every VOOL.

Real service handlers, real signer code and a local git remote; only the review provider's answers are scripted."""
import json

from registry_service.github_authority import FOLDER
from registry_service.model_review import ModelReview
from tests.registry.test_skill_codex_authority import SKILL, creator, draft
from tests.registry.test_skill_codex_github_signer import DAY, app_rows, checkout, on_github, signer
from tests.registry.test_skill_codex_github_signer import world as world

CAP_STOP = "monthly review cap reached; uploads wait for the next budget"


def held_signer(world, *, cap=1.0, transport=None):
    authority = signer(world, checkout(world), transport=transport)
    authority._review = ModelReview("openrouter", "test/model", ledger=authority.dir / "spend.json", monthly_cap=cap,
                                    key="not-a-real-key", request=world.model, clock=lambda: world.clock.t)
    return authority


def listed_then_queued(world):
    """alice/one is listed; alice/two then waits in the queue."""
    _, token, csrf = creator(world, "101", "alice")
    draft(world, token, csrf, "alice/one")
    first = held_signer(world).run_once()
    assert first["admitted"] == ["alice/one 1.0.0"] and "catalog" in first
    world.clock.t += 1
    return draft(world, token, csrf, "alice/two", SKILL + "\nSECOND\n").json()


def state(world):
    return json.loads(on_github(world, FOLDER + "/state.json"))


def held_for(world, second, *, stopped, runs=9, every=DAY, cap=1.0, transport=None):
    for _ in range(runs):
        world.clock.t += every
        report = held_signer(world, cap=cap, transport=transport).run_once()
        assert report["stopped"].startswith(stopped)
        assert "alice/two 1.0.0" not in report["admitted"]
        world.online.refresh(now=world.clock.t)  # the website can still stamp the list the signer renewed
        assert sorted(app_rows(world)) == ["alice/one"]  # the app still browses; the held upload is not listed
        assert state(world)["authorized_until"] > world.clock.t
    assert second["sha256"] not in {entry["signed"]["artifact_sha256"] for entry in state(world)["entries"]}


def test_a_spent_review_cap_holds_the_upload_but_the_signed_list_never_lapses(world):
    second = listed_then_queued(world)
    calls = len(world.model.calls)
    # 33 days: past the 7-day list and past the 30-day signature on alice/one, which renews from its saved verdict.
    held_for(world, second, stopped=CAP_STOP, runs=11, every=3 * DAY, cap=0.0)
    assert len(world.model.calls) == calls  # nothing was paid for while the cap is spent


def test_an_upload_whose_bytes_come_back_wrong_waits_and_the_list_stays_fresh(world):
    second = listed_then_queued(world)

    def wrong_bytes(method, url, body, headers):
        if method == "GET" and url.endswith("/authority/submissions/" + second["id"]):
            world.sent.append((method, url))
            return 200, b'{"not":"the uploaded bytes"}'
        return world.transport(method, url, body, headers)

    held_for(world, second, stopped="the server sent different bytes", transport=wrong_bytes)
    assert not [call for call in world.sent if call == ("POST", "/registry/api/authority/submissions/" + second["id"])]
    # Once the service sends the right bytes again, the upload is checked and listed.
    world.clock.t += DAY
    report = held_signer(world).run_once()
    assert "stopped" not in report and report["admitted"] == ["alice/two 1.0.0"]
    assert sorted(app_rows(world)) == ["alice/one", "alice/two"]


def test_an_admission_the_service_keeps_failing_waits_and_the_list_stays_fresh(world):
    second = listed_then_queued(world)

    def failing_admission(method, url, body, headers):
        if method == "POST" and url.endswith("/authority/submissions/" + second["id"]):
            world.sent.append((method, url))
            return 502, b'{"error":"bad gateway"}'
        return world.transport(method, url, body, headers)

    held_for(world, second, stopped="the service did not confirm", transport=failing_admission)
    world.clock.t += DAY
    report = held_signer(world).run_once()  # the saved decision is sent again, without another review
    assert "stopped" not in report and report["admitted"] == ["alice/two 1.0.0"]


def test_a_listed_skill_the_daily_recheck_cannot_read_waits_and_stays_listed(world):
    listed_then_queued(world)
    world.clock.t += DAY

    failed = []

    def unreadable(method, url, body, headers):
        if method == "GET" and "/authority/artifacts/" in url and not failed:
            failed.append(url)  # the recheck's read fails once; the service answers again afterwards
            return 502, b'{"error":"bad gateway"}'
        return world.transport(method, url, body, headers)

    report = held_signer(world, transport=unreadable).run_once()
    assert failed and report["recheck_waiting"] == "the server refused: bad gateway" and report["dropped"] == []
    world.online.refresh(now=world.clock.t)
    assert "alice/one" in app_rows(world)
    world.clock.t += DAY
    report = held_signer(world).run_once()
    world.online.refresh(now=world.clock.t)
    assert "recheck_waiting" not in report and "alice/one" in app_rows(world)


def test_a_signer_that_never_published_does_not_publish_an_empty_list_for_a_held_batch(world):
    _, token, csrf = creator(world, "101", "alice")
    draft(world, token, csrf, "alice/one")
    report = held_signer(world, cap=0.0).run_once()
    assert report["stopped"] == CAP_STOP and "catalog" not in report
    assert state(world)["authorized_until"] == 0
    assert world.http.get("/registry/api/snapshot").json() == {"error": "no approved snapshot"}


def test_a_listed_skill_whose_bytes_stay_unreadable_leaves_only_itself_and_the_rest_stays_fresh(world):
    _, token, csrf = creator(world, "101", "alice")
    first = draft(world, token, csrf, "alice/one").json()
    assert held_signer(world).run_once()["admitted"] == ["alice/one 1.0.0"]
    listed = world.clock.t
    world.clock.t += 1
    draft(world, token, csrf, "alice/two", SKILL + "\nSECOND\n")
    world.clock.t += DAY
    assert held_signer(world).run_once()["admitted"] == ["alice/two 1.0.0"]
    calls = len(world.model.calls)

    def different(method, url, body, headers):
        if method == "GET" and url.endswith("/authority/artifacts/" + first["sha256"]):
            world.sent.append((method, url))
            return 200, b"PRIVATE_DIFFERENT_BYTES"
        return world.transport(method, url, body, headers)

    for day in range(3, 37, 3):  # past alice/one's 30-day signature, which can no longer be renewed
        world.clock.t = listed + day * DAY
        report = held_signer(world, transport=different).run_once()
        assert "stopped" not in report
        assert report.get("recheck_waiting") == ("the server sent different bytes" if day <= 30 else None)
        world.online.refresh(now=world.clock.t)
        assert sorted(app_rows(world)) == (["alice/one", "alice/two"] if day < 30 else ["alice/two"])
        assert state(world)["authorized_until"] > world.clock.t
    assert len(world.model.calls) == calls  # alice/two renewed from its saved verdict
    assert b"PRIVATE_" not in on_github(world, FOLDER + "/state.json")
