"""The website follows signed releases without changing its legacy target setting."""
import pytest

from tests.registry.helpers import TARGET
from tests.registry.test_skill_codex_authority import DAY, creator, draft, rows
from tests.registry.test_skill_codex_github_signer import run
from tests.registry.test_skill_codex_github_signer import world as world


def test_new_release_is_served_downloadable_and_survives_service_restart_with_old_config(world, tmp_path):
    from core.registry.codec import canonical
    from registry_service.cli import load_service

    _, token, csrf = creator(world, "101", "alice")
    submission = draft(world, token, csrf, "alice/notes").json()
    run(world, releases=lambda: [TARGET])
    world.clock.t += DAY
    newer = {"version": "0.8.0", "commit": "8" * 40, "platform": "macos-x86_64"}
    report = run(world, releases=lambda: [newer])
    assert report["releases_changed"] and report["catalog"]
    assert rows(world)["alice/notes"]["targets"] == [newer]
    ticket = world.http.get("/registry/api/download", params={"id": "alice/notes", "version": "1.0.0"})
    assert ticket.status_code == 200, ticket.text
    assert world.http.get(ticket.json()["url"]).status_code == 200
    world.trust.close()
    root = tmp_path / "root.json"
    root.write_bytes(canonical(world.root))
    config = {"schema": "vool.registry.service.v1", "origin": "https://registry.test",
              "private_dir": str(tmp_path / "server"), "trusted_root": str(root),
              "catalog_file": str(world.catalog_file), "artifact_dir": str(tmp_path / "cache"),
              "target": TARGET, "providers": [], "authority": True}
    store, trust, _, _ = load_service(config, now=world.clock.t)
    try:
        assert store.public_artifact(submission["sha256"])[0]
        assert trust.resolve("alice/notes", "1.0.0", target=newer, now=world.clock.t)
        with pytest.raises(ValueError, match="compatibility"):
            trust.resolve("alice/notes", "1.0.0", target=TARGET, now=world.clock.t)
    finally:
        trust.close()
