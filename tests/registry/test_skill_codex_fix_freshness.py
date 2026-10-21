"""Every new paid verdict is gated by the current signer state and operator instructions."""
import pytest

from registry_service.github_authority import RepoAuthority, Superseded
from registry_service.model_review import ModelReview
from tests.registry.test_skill_codex_authority import creator, draft
from tests.registry.test_skill_codex_github_signer import checkout, signer, team_steps
from tests.registry.test_skill_codex_github_signer import world as world


def test_changed_operator_steps_stop_before_the_first_paid_call(world):
    _, token, csrf = creator(world, "101", "alice")
    draft(world, token, csrf, "alice/notes")
    path = checkout(world)
    team_steps(world, "removed alice/notes\n")
    authority = RepoAuthority(path, env=world.env, transport=world.transport, clock=lambda: world.clock.t)
    authority._review = ModelReview("openrouter", "test/model", ledger=authority.dir / "spend.json",
                                    monthly_cap=1, key="fixture", request=world.model, clock=lambda: world.clock.t)
    with pytest.raises(Superseded, match="another signer run changed the state"):
        authority.run_once()
    assert world.model.calls == []
    assert not any(method == "POST" for method, _ in world.sent)
    assert signer(world, checkout(world)).run_once()["admitted"] == []
    assert world.model.calls == []


def test_freshness_is_checked_again_before_the_second_paid_call(world, monkeypatch):
    _, token, csrf = creator(world, "101", "alice")
    draft(world, token, csrf, "alice/first")
    world.clock.t += 1
    draft(world, token, csrf, "alice/second")
    authority = signer(world, checkout(world))
    checkpoint = authority.review_checkpoint
    calls = []

    def overtaken_after_checkpoint():
        checkpoint()
        calls.append(1)
        if len(calls) == 1:
            # A real remote commit arrives after the first review has been durably counted.
            team_steps(world, "removed alice/second\n")

    monkeypatch.setattr(authority, "review_checkpoint", overtaken_after_checkpoint)
    with pytest.raises(Superseded, match="another signer run changed the state"):
        authority.run_once()
    assert len(world.model.calls) == 1
    assert not any(method == "POST" for method, _ in world.sent)
    retry = signer(world, checkout(world))
    assert retry.review.spent() == pytest.approx(0.01)
    assert retry.run_once()["admitted"] == ["alice/first 1.0.0"]
    assert len(world.model.calls) == 1  # the cached first verdict survives; the removed second skill is not reviewed
