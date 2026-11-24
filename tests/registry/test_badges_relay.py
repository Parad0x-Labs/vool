"""The Mac signer passes a signed badge document on and stamps it; it cannot make one."""
from core.registry import badges
from core.registry.codec import canonical
from core.registry.publisher import add_signature
from registry_service.api import PREFIX
from registry_service.mac_authority import unlock_root
from tests.registry.badge_helpers import by, entry, person, raw_document
from tests.registry.test_skill_codex_authority import creator, draft, world


def test_the_mac_signer_relays_what_two_people_signed_and_nothing_less(world):
    _, token, csrf = creator(world, "101", "alice")
    sha = draft(world, token, csrf, "alice/notes").json()["sha256"]
    assert world.mac.run_once()["admitted"] == ["alice/notes 1.0.0"]
    people = [person(), person()]
    folder = world.tmp / "mac" / "badges"
    folder.mkdir()
    now = world.clock.t
    issuers = badges.new_issuers([public for _, _, public in people], version=1, now=now, lifetime=86400)
    issuers = add_signature(issuers, unlock_root(world.tmp / "mac", "correct horse battery"))
    (folder / "badge-issuers.json").write_bytes(canonical(issuers))
    document = raw_document([entry("editors_choice", sha, people, package_id="alice/notes", issued_at=now)], issued_at=now)

    # Signed with the Mac's own admission key, which signs every upload: not a badge.
    (folder / "badges.json").write_bytes(canonical(add_signature({"signed": document, "signatures": []}, world.mac.admission_key)))
    report = world.mac.run_once()
    assert report["badge_problem"] == "signature threshold not met" and report["errors"] == []
    assert world.http.get(PREFIX + "/badges").json()["badges"] is None

    (folder / "badges.json").write_bytes(canonical(by(document, people)))
    world.clock.t += 300
    report = world.mac.run_once()
    assert report["badges"] == {"sequence": 1, "badges": 1}
    served = world.http.get(PREFIX + "/badges").json()
    assert served["badges"]["signed"] == document and served["stamp"]["signed"]["issued_at"] == world.clock.t
    assert served["stamp"]["signed"]["expires"] == world.clock.t + 7 * 86400
    # The issuer list alone, without a document, is nothing to relay; a missing list is said plainly.
    (folder / "badge-issuers.json").unlink()
    assert world.mac.run_once()["badge_problem"] == "badges/badge-issuers.json is missing beside badges/badges.json"
