"""The signer trusts the package's own name, never the queue's, and a team upload waits for a hand step naming its bytes.

Real service handlers and real signer code; only the review provider's answers are scripted. A service that was
taken over is played by planting a queue row and by renaming it on the wire."""
import hashlib
import json

import pytest

from core.registry.artifact import native_identity, pack
from core.registry.codec import RegistryError, canonical
from registry_service.authority import AGREEMENT, mit_license
from registry_service.github_authority import parse_steps
from tests.registry.helpers import NOW
from tests.registry.test_skill_codex_authority import SKILL, creator
from tests.registry.test_skill_codex_authority import world as world
from tests.registry.test_skill_codex_github_signer import run, team_steps
from tests.registry.test_skill_codex_github_signer import world as github_world

MEMBER = "240776969"
RENAMED = "the service named an upload differently than its package does; the uploads wait for the next run"


def package(tmp, package_id, *, summary="Turn meeting notes into decisions", version="1.0.0"):
    publisher, name = package_id.split("/")
    root = tmp / ("src-" + publisher + "-" + name + "-" + version)
    (root / ".codex-plugin").mkdir(parents=True)
    (root / ".codex-plugin" / "plugin.json").write_text(json.dumps({
        "name": native_identity(package_id), "version": version, "skills": "./skills/", "requires": []}))
    (root / "skills" / name).mkdir(parents=True)
    (root / "skills" / name / "SKILL.md").write_text(SKILL.replace("name: notes", "name: " + name))
    (root / "LICENSE").write_text(mit_license(publisher, now=NOW))
    info = {"id": package_id, "kind": "skill", "version": version, "summary": summary,
            "category": "productivity", "subcategory": "meetings", "license": "MIT", "languages": ["en"],
            "source": {"upload_id": hashlib.sha256((package_id + version).encode()).hexdigest()[:32], "namespace": publisher}}
    return pack(root, info, tmp / "scratch")


def planted(store, account, raw, package_id):
    """A waiting upload written straight into the service's table, as someone who took the service over could."""
    identifier, sha256 = "ab" * 16, hashlib.sha256(raw).hexdigest()
    with store.connection() as db:
        db.execute("INSERT INTO submissions (id,account,created,package_id,version,sha256,artifact,state,findings,"
                   "consent,agreement,publisher_bound,via) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (identifier, account, NOW, package_id, "1.0.0", sha256, raw, "checking", canonical([]), 1, AGREEMENT, 1,
                    "upload"))
    return identifier, sha256


def renaming(served, **change):
    """The service's answers, with every queue row renamed on the way to the signer."""
    def transport(method, url, body, headers):
        status, data = served(method, url, body, headers)
        if method == "GET" and url.endswith("/authority/queue") and status == 200:
            queue = json.loads(data)
            data = canonical({"submissions": [{**row, **change} for row in queue["submissions"]]})
        return status, data
    return transport


def state_of(store, identifier):
    with store.connection() as db:
        return db.execute("SELECT state FROM submissions WHERE id=?", (identifier,)).fetchone()[0]


def test_a_queue_row_cannot_pass_a_strangers_bytes_off_as_the_teams(world):
    eve, _, _ = creator(world, "666", "eve")
    world.mac.act("team", "vool-team", members=[MEMBER])
    raw = package(world.tmp, "eve/helper", summary="Official VOOL helper for meeting notes")
    # The service's own door refuses these words from anyone outside the team.
    assert world.store.upload(eve, raw, now=NOW, agreement=AGREEMENT)["state"] == "refused"
    identifier, _ = planted(world.store, eve, raw, "eve/helper")
    served = world.mac.transport

    for change in ({"package_id": "vool-team/helper"}, {"version": "9.9.9"}):
        world.mac.transport = renaming(served, **change)
        report = world.mac.run_once()
        assert report["admitted"] == [] and report["refused"] == [] and world.mac.state["entries"] == []
        assert report.get("stopped") == RENAMED
        assert world.model.calls == []  # nothing was paid for
        assert state_of(world.store, identifier) == "checking"  # held, neither signed nor thrown away

    # Under its own name the same package gets the rule every stranger gets.
    world.mac.transport = served
    report = world.mac.run_once()
    assert report["refused"] == ["eve/helper 1.0.0"] and report["admitted"] == [] and world.model.calls == []
    world.http.cookies.set("vool_registry", world.store.session(eve, now=world.clock.t)[0])
    from registry_service.api import PREFIX

    assert "Only the Skill Codex can mark" in world.http.get(
        PREFIX + "/submissions/" + identifier).json()["findings"][0]["reason"]


def team_upload(store, account, raw):
    item = store.upload(account, raw, now=NOW, agreement=AGREEMENT)
    assert item["state"] == "checking", item
    return store.consent(account, item["id"], item["sha256"], public=True)


def test_a_team_upload_waits_for_the_hand_step_that_names_its_exact_bytes(world):
    sls, _, _ = creator(world, MEMBER, "sls")
    world.mac.act("team", "vool-team", members=[MEMBER])
    agenda = team_upload(world.store, sls, package(world.tmp, "vool-team/agenda"))
    report = world.mac.run_once()
    assert report["admitted"] == [] and report["refused"] == [] and "stopped" not in report
    assert report["waiting"] == ["vool-team/agenda 1.0.0"]
    assert report["team_step_needed"] == ["vool-team/agenda@" + agenda["sha256"]]
    assert world.model.calls == []  # no paid review before a team member has named it

    # A step that names other bytes, or another skill, releases nothing.
    world.mac.act("team-admit", "vool-team/agenda@" + "0" * 64)
    world.mac.act("team-admit", "vool-team/other@" + agenda["sha256"])
    assert world.mac.run_once()["admitted"] == [] and world.model.calls == []
    for target, problem in (("vool-team/agenda", "publisher/name@checksum"), ("vool-team/agenda@abc", "publisher/name@checksum"),
                            ("eve/notes@" + agenda["sha256"], "not a team"),
                            ("vool-team/agenda@" + "0" * 64, "already done")):
        with pytest.raises(RegistryError, match=problem):
            world.mac.act("team-admit", target)

    world.mac.act("team-admit", "vool-team/agenda@" + agenda["sha256"])
    report = world.mac.run_once()
    assert report["admitted"] == ["vool-team/agenda 1.0.0"] and "team_step_needed" not in report
    assert len(world.model.calls) == 1  # reviewed once, and only after the hand step

    # The next version is other bytes: the earlier step does not cover it.
    world.clock.t += 1
    team_upload(world.store, sls, package(world.tmp, "vool-team/agenda", version="1.0.1"))
    report = world.mac.run_once()
    assert report["admitted"] == [] and report["waiting"] == ["vool-team/agenda 1.0.1"]
    # Everyone else's uploads are signed as before while a team upload waits.
    alice, _, _ = creator(world, "101", "alice")
    team_upload(world.store, alice, package(world.tmp, "alice/notes"))
    assert world.mac.run_once()["admitted"] == ["alice/notes 1.0.0"]


def test_waiting_team_uploads_never_hold_up_everyone_elses(world):
    sls, _, _ = creator(world, MEMBER, "sls")
    alice, _, _ = creator(world, "101", "alice")
    world.mac.act("team", "vool-team", members=[MEMBER])
    for index in range(21):  # more than one run takes from the service
        team_upload(world.store, sls, package(world.tmp, f"vool-team/skill-{index}"))
    team_upload(world.store, alice, package(world.tmp, "alice/notes"))
    report = world.mac.run_once()
    assert report["admitted"] == ["alice/notes 1.0.0"] and len(report["waiting"]) == 19
    # The team's own are still taken oldest first once a step names them.
    oldest = report["team_step_needed"][0]
    assert oldest.startswith("vool-team/skill-0@")
    world.mac.act("team-admit", oldest)
    assert world.mac.run_once()["admitted"] == ["vool-team/skill-0 1.0.0"]


def test_the_team_upload_command_prints_the_hand_step_for_the_bytes_it_uploaded(world, tmp_path, monkeypatch, capsys):
    import time

    from registry_service import cli

    creator(world, MEMBER, "sls")
    world.mac.act("team", "vool-team", members=[MEMBER])
    file = tmp_path / "agenda.json"
    file.write_bytes(package(world.tmp, "vool-team/agenda"))
    sha = hashlib.sha256(file.read_bytes()).hexdigest()
    config = tmp_path / "config.json"
    config.write_bytes(canonical({"schema": "vool.registry.service.v1", "private_dir": str(tmp_path / "server")}))
    monkeypatch.setattr(time, "time", lambda: NOW)
    assert cli.main(["team-upload", "--config", str(config), "--github-id", MEMBER, "--packages", str(file)]) == 0
    printed = [line.strip() for line in capsys.readouterr().out.splitlines()]
    step = "team-admit vool-team/agenda " + sha
    command = "python -m registry_service.mac_authority team-admit vool-team/agenda@" + sha + " --dir DIR"
    assert step in printed and command in printed
    # Both printed forms are the ones the signers read.
    assert parse_steps("team vool-team " + MEMBER + "\n" + step + "\n")["team_admits"] == ["vool-team/agenda@" + sha]
    assert world.mac.run_once()["admitted"] == []
    world.mac.act(*command.split()[3:5])
    assert world.mac.run_once()["admitted"] == ["vool-team/agenda 1.0.0"]
    # A package the service refused waits for nothing, so no step is printed for it.
    assert cli.main(["team-upload", "--config", str(config), "--github-id", MEMBER, "--packages", str(file)]) == 1
    assert "team-admit" not in capsys.readouterr().out


def test_the_github_signer_holds_a_team_upload_until_a_team_admit_line_names_its_bytes(github_world):
    world = github_world
    sls, _, _ = creator(world, MEMBER, "sls")
    team_steps(world, "team vool-team " + MEMBER + "\n")
    run(world)
    agenda = team_upload(world.store, sls, package(world.tmp, "vool-team/agenda"))
    report = run(world)
    assert report["admitted"] == [] and report["waiting"] == ["vool-team/agenda 1.0.0"] and "stopped" not in report
    team_steps(world, "team vool-team " + MEMBER + "\nteam-admit vool-team/agenda " + "0" * 64 + "\n")
    assert run(world)["admitted"] == [] and world.model.calls == []  # a line naming other bytes releases nothing
    team_steps(world, "team vool-team " + MEMBER + "\nteam-admit vool-team/agenda " + agenda["sha256"] + "\n"
                      "team-admit eve/notes " + "1" * 64 + "\n")
    report = run(world)
    assert report["admitted"] == ["vool-team/agenda 1.0.0"]
    assert report["steps"]["problems"] == ["team-admit eve/notes: eve is not a team"]
    assert len(world.model.calls) == 1  # reviewed once, and only after the line named its bytes


def test_team_admit_lines_read_strictly():
    sha = "a" * 64
    assert parse_steps("team vool-team 1\nteam-admit vool-team/agenda " + sha + "\n")["team_admits"] == [
        "vool-team/agenda@" + sha]
    assert "team_admits" not in parse_steps("team vool-team 1\n")
    for line in ("team-admit vool-team/agenda", "team-admit vool-team/agenda@" + sha, "team-admit vool-team/agenda abc",
                 "team-admit vool-team " + sha, "team-admit vool-team/agenda " + sha + " extra"):
        with pytest.raises(RegistryError, match="line 1: write it as"):
            parse_steps(line)
