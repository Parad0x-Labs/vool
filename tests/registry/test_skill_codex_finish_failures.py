"""A stopped or corrupt signer never turns private transport data into public authority."""
import json

import pytest

from core.registry.codec import RegistryError
from registry_service.github_authority import FOLDER, quiet
from registry_service.model_review import NoAnswer
from tests.registry.test_skill_codex_authority import creator, draft
from tests.registry.test_skill_codex_github_signer import checkout, on_github, run, signer
from tests.registry.test_skill_codex_github_signer import world as world


@pytest.mark.parametrize("field", ["epoch", "targets", "marks", "private_text"])
def test_hand_edited_public_state_is_refused_before_any_request(world, field):
    from tests.registry.test_skill_codex_github_signer import git

    path = checkout(world)
    file = path / FOLDER / "state.json"
    value = json.loads(file.read_text())
    value[field] = {"epoch": 42, "targets": [], "marks": {}, "private_text": "PRIVATE_STATE_TEXT"}[field]
    file.write_text(json.dumps(value))
    git(path, "commit", "-am", "Edited public state")
    sent = len(world.sent)
    with pytest.raises(RegistryError, match="state"):
        signer(world, path).run_once()
    assert len(world.sent) == sent


def test_corrupt_sealed_state_is_refused_without_requests(world):
    path = checkout(world)
    file = path / FOLDER / "private.bin"
    raw = file.read_bytes()
    file.write_bytes(raw[:-1] + bytes([raw[-1] ^ 1]))
    sent = len(world.sent)
    with pytest.raises(RegistryError, match="does not open"):
        signer(world, path).run_once()
    assert len(world.sent) == sent


def test_existing_setup_state_migrates_to_a_sealed_public_state_binding(world):
    from registry_service.github_authority import SECRETS, seal, unseal
    from tests.registry.test_skill_codex_github_signer import git

    path = checkout(world)
    key = bytes.fromhex(world.secrets[SECRETS["state"]])
    file = path / FOLDER / "private.bin"
    private = unseal(key, file.read_bytes())
    private.pop("public_sha256")
    file.write_bytes(seal(key, private))
    git(path, "add", FOLDER)
    git(path, "commit", "-m", "Existing setup state")
    git(path, "push", "origin", "main")
    signer(world, path).run_once()
    current = json.loads(on_github(world, FOLDER + "/state.json"))
    private = unseal(key, on_github(world, FOLDER + "/private.bin"))
    from core.registry.codec import digest

    assert private["public_sha256"] == digest(current)


@pytest.mark.parametrize("unavailable", ["down", "cap"])
def test_review_outage_or_cap_keeps_bytes_private_and_never_admits(world, unavailable):
    _, token, csrf = creator(world, "101", "alice")
    draft(world, token, csrf, "alice/notes")
    authority = signer(world, checkout(world))
    if unavailable == "down":
        world.model.answers["Ask before"] = NoAnswer("PRIVATE_TRANSPORT_TEXT")
    else:
        authority._review.cap = 0
    result = authority.run_once()
    assert result["admitted"] == [] and result["waiting"] == ["alice/notes 1.0.0"]
    public = on_github(world, FOLDER + "/state.json")
    assert b"PRIVATE_TRANSPORT_TEXT" not in public and b"alice/notes" not in public
    assert json.loads(public)["entries"] == []


@pytest.mark.parametrize("garbage", [b"{}", b'{"submissions":null}', b'{"submissions":[{}]}'])
def test_garbage_queue_is_refused_before_signing_any_upload(world, garbage):
    def transport(method, url, body, headers):
        if url.endswith("/authority/queue"):
            return 200, garbage
        return world.transport(method, url, body, headers)

    authority = signer(world, checkout(world), transport=transport)
    with pytest.raises(RegistryError, match="queue"):
        authority.run_once()
    assert not any(path.endswith(("/authority/catalog", "/authority/marks")) and method == "POST"
                   for method, path in world.sent)


def test_public_run_summary_never_echoes_service_error_text():
    report = {"steps": {"applied": True, "problems": []}, "recheck_waiting": "PRIVATE_SERVER_TEXT",
              "releases_waiting": "PRIVATE_RELEASE_TEXT"}
    assert "PRIVATE" not in json.dumps(quiet(report))


def test_public_command_output_redacts_a_service_refusal(world, monkeypatch, capsys):
    from registry_service.github_authority import main
    from tests.registry.helpers import TARGET

    for name, value in world.env.items():
        monkeypatch.setenv(name, value)
    def refused(method, url, body, headers):
        return 400, b'{"error":"PRIVATE_SERVER_TEXT"}'
    assert main(["run", "--repo", str(checkout(world))], transport=refused,
                clock=lambda: world.clock.t, releases=lambda: [TARGET]) == 1
    output = capsys.readouterr()
    assert "PRIVATE_SERVER_TEXT" not in output.out + output.err
    assert "service refused" in output.out
