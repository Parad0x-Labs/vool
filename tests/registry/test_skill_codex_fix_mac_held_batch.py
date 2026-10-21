"""The Mac signer holds an upload batch it cannot read whole, and the list it already signed stays fresh for every VOOL.

Real service handlers, real signer code and the real app client; only the review provider's answers are scripted."""
from tests.registry.test_skill_codex_authority import DAY, SKILL, creator, draft
from tests.registry.test_skill_codex_authority import world as world
from tests.registry.test_skill_codex_github_signer import app_rows


def test_the_mac_signer_holds_an_upload_whose_bytes_come_back_wrong_and_the_list_stays_fresh(world):
    _, token, csrf = creator(world, "101", "alice")
    draft(world, token, csrf, "alice/one")
    assert world.mac.run_once()["admitted"] == ["alice/one 1.0.0"]
    world.clock.t += 1
    second = draft(world, token, csrf, "alice/two", SKILL + "\nSECOND\n").json()
    served = world.mac.transport
    sent = []

    def wrong_bytes(method, url, body, headers):
        sent.append((method, url))
        if method == "GET" and url.endswith("/authority/submissions/" + second["id"]):
            return 200, b"PRIVATE_BROKEN_BYTES"
        return served(method, url, body, headers)

    world.mac.transport = wrong_bytes
    calls = len(world.model.calls)
    for _ in range(9):
        world.clock.t += DAY
        report = world.mac.run_once()
        assert report["stopped"] == "the server sent different bytes; the uploads wait for the next run"
        assert report["admitted"] == [] and report["refused"] == []
        world.online.refresh(now=world.clock.t)  # the website can still stamp the list the signer renewed
        assert sorted(app_rows(world)) == ["alice/one"]
        assert world.mac.state["authorized_until"] > world.clock.t
    assert len(world.model.calls) == calls  # nothing in the held batch was paid for
    assert not [call for call in sent if call[0] == "POST" and "/authority/submissions/" in call[1]]
    # Once the service sends the right bytes again, the upload is checked and listed.
    world.mac.transport = served
    world.clock.t += DAY
    report = world.mac.run_once()
    assert "stopped" not in report and report["admitted"] == ["alice/two 1.0.0"]
    assert sorted(app_rows(world)) == ["alice/one", "alice/two"]
