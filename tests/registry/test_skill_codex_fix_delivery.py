"""An unconfirmed admission or refusal stops the batch before later decisions or catalog writes."""
import json

import pytest

from core.registry.codec import RegistryError
from registry_service.github_authority import FOLDER
from tests.registry.test_skill_codex_authority import SKILL, creator, draft, reply
from tests.registry.test_skill_codex_github_signer import checkout, on_github, signer, team_steps
from tests.registry.test_skill_codex_github_signer import world as world


@pytest.mark.parametrize("response", [(502, b""), (200, b"{}")])
@pytest.mark.parametrize("decision", ["admitted", "refused"])
def test_unconfirmed_decision_stops_later_sends_and_retries_without_another_review(world, response, decision):
    _, token, csrf = creator(world, "101", "alice")
    draft(world, token, csrf, "alice/first", SKILL + "\nFIRSTCASE\n")
    world.clock.t += 1
    draft(world, token, csrf, "alice/second")
    if decision == "refused":
        world.model.answers["FIRSTCASE"] = reply({"verdict": "refuse", "findings": [{"reason": "The scope is unclear."}]})
    writes = []

    def interrupted(method, url, body, headers):
        if method == "POST":
            writes.append(url)
            if "/authority/submissions/" in url:
                return response
        return world.transport(method, url, body, headers)

    first = signer(world, checkout(world), transport=interrupted).run_once()
    assert len(writes) == 1 and "/authority/submissions/" in writes[0]
    assert first["admitted"] == [] and first["refused"] == [] and "catalog" not in first
    assert "did not confirm" in first["stopped"]
    assert json.loads(on_github(world, FOLDER + "/state.json"))["entries"] == []
    assert len(world.model.calls) == 2
    retry = signer(world, checkout(world))
    assert retry.review.spent() == pytest.approx(0.02)
    result = retry.run_once()
    assert result["admitted"] == (["alice/first 1.0.0", "alice/second 1.0.0"] if decision == "admitted" else ["alice/second 1.0.0"])
    assert result["refused"] == (["alice/first 1.0.0"] if decision == "refused" else [])
    assert "catalog" in result and "stopped" not in result and len(world.model.calls) == 2


@pytest.mark.parametrize("endpoint", ["marks", "catalog"])
def test_malformed_publication_acknowledgement_stops_before_any_later_request(world, endpoint):
    _, token, csrf = creator(world, "101", "alice")
    draft(world, token, csrf, "alice/notes")
    if endpoint == "marks":
        team_steps(world, "suspended bob\n")
    calls = []

    def malformed(method, url, body, headers):
        calls.append((method, url))
        result = world.transport(method, url, body, headers)
        if method == "POST" and url.endswith("/authority/" + endpoint):
            return 200, b'{}'  # the real service accepted it, but the response was lost or corrupted
        return result

    with pytest.raises(RegistryError, match="invalid " + endpoint + " acknowledgement"):
        signer(world, checkout(world), transport=malformed).run_once()
    assert calls[-1][0] == "POST" and calls[-1][1].endswith("/authority/" + endpoint)
    assert signer(world, checkout(world)).run_once()["catalog"]
    assert len(world.model.calls) == 1
