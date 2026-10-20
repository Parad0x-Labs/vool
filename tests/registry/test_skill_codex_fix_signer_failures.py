"""Failures stop the signer before publication and retry without losing review spending."""
import json

import pytest

from core.registry.codec import RegistryError
from registry_service.github_authority import FOLDER, main
from registry_service.model_review import ModelReview, NoAnswer
from tests.registry.helpers import TARGET
from tests.registry.test_skill_codex_authority import SKILL, creator, draft
from tests.registry.test_skill_codex_github_signer import checkout, git, on_github, run, signer, team_steps
from tests.registry.test_skill_codex_github_signer import world as world


def state_bytes(path):
    return {name: (path / FOLDER / name).read_bytes() for name in ("state.json", "private.bin")}


def scripted_review(world, monkeypatch):
    """The command builds its own review; it answers from the script, so a regression never reaches the paid provider."""
    monkeypatch.setattr(ModelReview, "_post", lambda self, url, payload, headers: world.model(url, payload, headers))


@pytest.mark.parametrize("failure", ["release", "steps"])
def test_command_stops_before_any_write_and_retries_unchanged_state(world, monkeypatch, capsys, failure):
    _, token, csrf = creator(world, "101", "alice")
    draft(world, token, csrf, "alice/notes")
    if failure == "steps":
        team_steps(world, "not-a-valid-step PRIVATE_OPERATOR_TEXT\n")
    path = checkout(world)
    before = state_bytes(path)
    head = git(world.origin, "rev-parse", "main")
    for name, value in world.env.items():
        monkeypatch.setenv(name, value)
    scripted_review(world, monkeypatch)
    calls = []

    def releases():
        calls.append(1)
        if failure == "release":
            raise RegistryError("PRIVATE_RELEASE_TEXT")
        return [TARGET]

    for _ in range(2):
        assert main(["run", "--repo", str(path)], transport=world.transport,
                    clock=lambda: world.clock.t, releases=releases) == 1
        output = capsys.readouterr()
        assert "Stopped: " in output.out
        assert ("release discovery failed" if failure == "release" else "team-steps.txt line 1") in output.out
        assert "PRIVATE_" not in output.out + output.err
        assert state_bytes(path) == before and git(world.origin, "rev-parse", "main") == head
        assert world.sent == [] and world.model.calls == []
    assert len(calls) == (2 if failure == "release" else 0)
    if failure == "steps":
        team_steps(world, "")
    assert run(world, releases=lambda: [TARGET])["admitted"] == ["alice/notes 1.0.0"]


@pytest.mark.parametrize("failure", ["down", "cap"])
def test_review_failure_holds_the_entire_batch_and_keeps_paid_cost_for_retry(world, failure):
    _, token, csrf = creator(world, "101", "alice")
    first = draft(world, token, csrf, "alice/first").json()
    world.clock.t += 1
    second = draft(world, token, csrf, "alice/second", SKILL + "\nSECONDCASE\n").json()
    path = checkout(world)
    authority = signer(world, path)
    if failure == "down":
        world.model.answers["SECONDCASE"] = NoAnswer("PRIVATE_TRANSPORT_TEXT")
    else:
        authority._review.cap = 0.005
    report = authority.run_once()
    assert report["admitted"] == [] and report["refused"] == [] and "catalog" not in report
    assert report["waiting"] == ["alice/first 1.0.0", "alice/second 1.0.0"]
    assert ("review service unavailable" if failure == "down" else "monthly review cap reached") in report["stopped"]
    assert not any(method == "POST" for method, _ in world.sent)
    public = on_github(world, FOLDER + "/state.json")
    assert json.loads(public)["entries"] == [] and b"alice/" not in public and b"PRIVATE_" not in public
    retry = signer(world, checkout(world))
    assert retry.review.spent() == pytest.approx(0.01)
    world.model.answers.clear()
    count = len(world.model.calls)
    assert retry.run_once()["admitted"] == ["alice/first 1.0.0", "alice/second 1.0.0"]
    assert len(world.model.calls) == count + 1  # first verdict is retained; it is not paid for twice
    assert retry.review.spent() == pytest.approx(0.02)
    assert {item["signed"]["artifact_sha256"] for item in retry.state["entries"]} == {first["sha256"], second["sha256"]}


@pytest.mark.parametrize("failure", ["down", "moderation"])
def test_unreadable_website_stops_before_state_changes_or_paid_calls(world, failure):
    _, token, csrf = creator(world, "101", "alice")
    draft(world, token, csrf, "alice/notes")
    path = checkout(world)
    before = state_bytes(path)
    head = git(world.origin, "rev-parse", "main")

    def transport(method, url, body, headers):
        if failure == "down":
            raise OSError("PRIVATE_TRANSPORT_TEXT")
        if failure == "moderation" and url.endswith("/authority/moderation"):
            return 200, b'{}'
        return world.transport(method, url, body, headers)

    authority = signer(world, path, transport=transport)
    with pytest.raises((RegistryError, OSError)):
        authority.run_once()
    assert world.model.calls == [] and not any(method == "POST" for method, _ in world.sent)
    assert state_bytes(path) == before and git(world.origin, "rev-parse", "main") == head
    assert run(world)["admitted"] == ["alice/notes 1.0.0"]


@pytest.mark.parametrize("failure", ["queue", "bytes", "refused"])
def test_an_unreadable_upload_batch_waits_whole_and_nothing_in_it_is_paid_for_or_sent(world, monkeypatch, capsys,
                                                                                    failure):
    _, token, csrf = creator(world, "101", "alice")
    draft(world, token, csrf, "alice/notes")
    path = checkout(world)
    for name, value in world.env.items():
        monkeypatch.setenv(name, value)
    scripted_review(world, monkeypatch)

    def transport(method, url, body, headers):
        if failure == "queue" and url.endswith("/authority/queue"):
            return 200, b'{}'
        if failure == "bytes" and "/authority/submissions/" in url:
            return 200, b'PRIVATE_BROKEN_BYTES'
        if failure == "refused" and url.endswith("/authority/queue"):
            return 503, b'{"error":"PRIVATE_SERVICE_TEXT"}'
        return world.transport(method, url, body, headers)

    assert main(["run", "--repo", str(path)], transport=transport, clock=lambda: world.clock.t,
                releases=lambda: [TARGET]) == 1
    output = capsys.readouterr()
    assert output.out.startswith("Stopped: " + {"queue": "the service returned an invalid upload queue",
                                                "bytes": "the server sent different bytes",
                                                "refused": "the service refused the upload batch"}[failure])
    assert "PRIVATE_" not in output.out + output.err
    assert world.model.calls == [] and json.loads(on_github(world, FOLDER + "/state.json"))["entries"] == []
    assert not any(method == "POST" and "/authority/submissions/" in sent for method, sent in world.sent)
    assert b"PRIVATE_" not in on_github(world, FOLDER + "/state.json")
    assert run(world)["admitted"] == ["alice/notes 1.0.0"]
