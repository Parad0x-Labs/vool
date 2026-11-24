"""The Mac signer takes the same steps about ratings and listings, and refuses the same things."""
import pytest

from core.registry.codec import RegistryError
from registry_service.api import PREFIX
from registry_service.ratings import REASONS
from tests.registry.test_ratings import write
from tests.registry.test_skill_codex_authority import creator, draft, rows
from tests.registry.test_skill_codex_authority import world as world

NOTES = "alice/notes"


def test_the_mac_signer_voids_delists_and_removes_with_a_reason_and_refuses_unknown_ones(world):
    _, token, csrf = creator(world, "101", "alice")
    assert draft(world, token, csrf, NOTES).status_code == 201
    world.mac.run_once()
    people = [world.store.identity("google", str(700 + index), now=world.clock.t - 60 * 86400) for index in range(6)]
    for who in people:
        write(world, who, NOTES, 5)
    with world.store.connection() as db:
        target = db.execute("SELECT id FROM ratings WHERE account=?", (people[0],)).fetchone()[0]

    def summary():
        return world.http.get(PREFIX + "/ratings", params={"id": NOTES}).json()["summary"]

    def record():
        with world.store.connection() as db:
            return [tuple(row) for row in db.execute("SELECT action, target, reason, affected FROM moderation_log ORDER BY id")]

    for line, message in ((f"rating-void {target} spite", "the reason must be one of"),
                          (f"rating-erase {target} fake", "this is not a rating step"),
                          (f"rating-void {target}", "write it as"), ("", "this is not a rating step")):
        with pytest.raises(RegistryError, match=message):
            world.mac.act("step", line)
    with pytest.raises(RegistryError, match="the reason must be one of"):
        world.mac.act("delist", NOTES)
    with pytest.raises(RegistryError, match="the reason must be one of"):
        world.mac.act("remove", NOTES, reason="whim")
    assert record() == [] and summary()["count"] == 6 and NOTES in rows(world)

    assert world.mac.act("step", f"rating-void {target} fake") == "Done."
    assert record() == [("rating-void", target, "fake", 1)]
    assert (summary()["count"], summary()["removed_by_team"]) == (5, 1)
    assert world.mac.act("step", f"rating-restore {target}") == "Done."
    assert summary()["removed_by_team"] == 0

    assert world.mac.act("delist", NOTES, reason="misleading") == "Done."
    assert NOTES not in rows(world) and record()[-1] == ("delisted", NOTES, "misleading", 0)
    assert world.http.get(PREFIX + "/snapshot").json()["signed"]["revoked"] == []
    with pytest.raises(RegistryError, match="already done"):
        world.mac.act("delist", NOTES, reason="misleading")
    assert world.mac.act("relist", NOTES) == "Done."
    assert NOTES in rows(world) and record()[-1] == ("relisted", NOTES, None, 0)

    assert world.mac.act("remove", NOTES, reason="harmful") == "Done."
    notice = world.http.get(PREFIX + "/moderation/notices", params={"id": NOTES}).json()["notices"][0]
    assert (notice["action"], notice["reason"], notice["sentence"]) == ("removed", "harmful", REASONS["harmful"])
    assert len(world.http.get(PREFIX + "/snapshot").json()["signed"]["revoked"]) == 1
