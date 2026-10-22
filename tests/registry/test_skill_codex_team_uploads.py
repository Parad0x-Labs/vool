"""A team member publishes the team's own skills in batches: a higher daily limit and an operator upload command."""
import hashlib
import json
import time

import pytest

from core.registry.codec import RegistryError, canonical
from registry_service import cli
from registry_service.authority import AGREEMENT
from registry_service.store import DAILY_SUBMISSIONS, TEAM_DAILY_SUBMISSIONS
from tests.registry.helpers import NOW
from tests.registry.test_skill_codex_authority import SKILL, code, creator
from tests.registry.test_skill_codex_authority import world as world


def package(world, package_id, version="1.0.0"):
    from core.registry.artifact import native_identity, pack
    from registry_service.authority import mit_license

    publisher, name = package_id.split("/")
    root = world.tmp / ("src-" + publisher + "-" + name + "-" + version)
    (root / ".codex-plugin").mkdir(parents=True)
    (root / ".codex-plugin" / "plugin.json").write_text(json.dumps({
        "name": native_identity(package_id), "version": version, "skills": "./skills/", "requires": []}))
    (root / "skills" / name).mkdir(parents=True)
    (root / "skills" / name / "SKILL.md").write_text(SKILL.replace("name: notes", "name: " + name))
    (root / "LICENSE").write_text(mit_license(publisher, now=NOW))
    info = {"id": package_id, "kind": "skill", "version": version, "summary": "Turn meeting notes into decisions",
            "category": "productivity", "subcategory": "meetings", "license": "MIT", "languages": ["en"],
            "source": {"upload_id": hashlib.sha256((package_id + version).encode()).hexdigest()[:32], "namespace": publisher}}
    return pack(root, info, world.tmp / "scratch")


def team(world):
    world.mac.gate(code(world))
    world.mac.act("team", "vool-team", members=["240776969"])


def test_team_members_get_the_higher_daily_limit_and_everyone_else_keeps_ten(world):
    sls, _, _ = creator(world, "240776969", "sls")
    eve, _, _ = creator(world, "666", "eve")
    team(world)
    for index in range(DAILY_SUBMISSIONS + 2):
        item = world.store.upload(sls, package(world, "vool-team/skill-%d" % index), now=NOW, agreement=AGREEMENT)
        assert item["state"] == "checking", item
    for index in range(DAILY_SUBMISSIONS):
        world.store.upload(eve, package(world, "eve/skill-%d" % index), now=NOW, agreement=AGREEMENT)
    with pytest.raises(RegistryError, match="daily submission quota reached"):
        world.store.upload(eve, package(world, "eve/one-more"), now=NOW, agreement=AGREEMENT)
    assert TEAM_DAILY_SUBMISSIONS > DAILY_SUBMISSIONS


def run(world, tmp_path, monkeypatch, capsys, github_id, files):
    config = tmp_path / "config.json"
    config.write_bytes(canonical({"schema": "vool.registry.service.v1", "private_dir": str(tmp_path / "server")}))
    monkeypatch.setattr(time, "time", lambda: NOW)
    status = cli.main(["team-upload", "--config", str(config), "--github-id", github_id, "--packages", *map(str, files)])
    return status, [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.startswith("{")]


def test_team_upload_uploads_and_consents_the_team_packages(world, tmp_path, monkeypatch, capsys):
    sls, _, _ = creator(world, "240776969", "sls")
    team(world)
    files = []
    for name in ("agenda", "notes"):
        files.append(tmp_path / (name + ".json"))
        files[-1].write_bytes(package(world, "vool-team/" + name))
    status, lines = run(world, tmp_path, monkeypatch, capsys, "240776969", files)
    assert status == 0
    assert [(line["package_id"], line["state"], line["consent"]) for line in lines] == [
        ("vool-team/agenda", "checking", 1), ("vool-team/notes", "checking", 1)]
    assert world.mac.run_once()["admitted"] == ["vool-team/agenda 1.0.0", "vool-team/notes 1.0.0"]
    assert {item["package_id"] for item in world.store.submissions(sls)} == {"vool-team/agenda", "vool-team/notes"}


def test_team_upload_refuses_non_members_other_publishers_and_unknown_accounts(world, tmp_path, monkeypatch, capsys):
    creator(world, "240776969", "sls")
    creator(world, "666", "eve")
    team(world)
    official = tmp_path / "official.json"
    official.write_bytes(package(world, "vool-team/notes"))
    personal = tmp_path / "personal.json"
    personal.write_bytes(package(world, "sls/notes"))
    status, lines = run(world, tmp_path, monkeypatch, capsys, "666", [official])
    assert status == 1 and lines == [{"file": str(official),
                                      "refused": "the package's publisher is not a team this member belongs to"}]
    status, lines = run(world, tmp_path, monkeypatch, capsys, "240776969", [personal])
    assert status == 1 and lines[0]["refused"] == "the package's publisher is not a team this member belongs to"
    status, lines = run(world, tmp_path, monkeypatch, capsys, "999", [official])
    assert status == 1 and lines == []
    assert world.store.marks()["teams"] == {"vool-team": ["240776969"]}
