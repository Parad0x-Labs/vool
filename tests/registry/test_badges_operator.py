"""Issuing the two team badges by hand, start to finish: the owner appoints people with the offline main key,
two of them sign on their own, the GitHub signer only passes the file on, the app shows the badge, and a
withdrawal takes it away. Real commands, a real git repository, the real service; keys made in the test."""
import json
import re

import pytest
import yaml

from registry_service import badges as tool
from registry_service import github_authority
from registry_service.api import PREFIX
from registry_service.github_authority import FOLDER, SECRETS, quiet
from tests.registry.helpers import TARGET
from tests.registry.test_skill_codex_authority import creator, draft
from tests.registry.test_skill_codex_github_signer import PASSPHRASE, app_rows, git, run, world

PHRASES = {"ana.pem": "ana's own long phrase", "ben.pem": "ben's own long phrase", "cy.pem": "cy's own long phrase",
           "eve.pem": "eve's own long phrase", "root.pem": PASSPHRASE}


@pytest.fixture
def desk(world, monkeypatch, capsys):
    """Runs one command as a person at a terminal would, and returns (exit code, what it printed)."""
    asked = []

    def typed(prompt=""):
        asked.append(prompt)
        named = re.search(r"Passphrase for (\S+):", prompt)
        return PHRASES[named.group(1)] if named else typed.choosing

    monkeypatch.setattr(tool.getpass, "getpass", typed)
    transcript = []

    def command(*words, choosing=""):
        typed.choosing = choosing
        code = tool.main([str(word) for word in words], clock=lambda: world.clock.t)
        printed = capsys.readouterr().out
        transcript.append("$ python -m registry_service.badges " + " ".join(
            str(word).replace(str(world.tmp), ".") for word in words) + "\n" + printed.replace(str(world.tmp), "."))
        return code, printed

    command.asked, command.transcript = asked, transcript
    return command


def push(world, message):
    git(world.owner, "add", "-A")
    git(world.owner, "commit", "-q", "-m", message)
    git(world.owner, "push", "-q", "origin", "main")


def test_the_whole_flow_from_appointing_people_to_a_badge_in_the_app_and_its_withdrawal(world, desk):
    _, token, csrf = creator(world, "101", "alice")
    submission = draft(world, token, csrf, "alice/notes").json()
    assert run(world)["admitted"] == ["alice/notes 1.0.0"]
    assert app_rows(world)["alice/notes"]["badges"] == []
    usb, people = world.tmp / "usb", world.tmp / "people"
    people.mkdir()

    # Each appointed person makes a key of their own. A short passphrase is refused.
    code, out = desk("keygen", "--out", people / "ana.pem", choosing="short")
    assert code == 1 and out == "Refused: the passphrase needs at least 12 characters\n" and not (people / "ana.pem").exists()
    lines = {}
    for name in ("ana", "ben", "cy", "eve"):
        code, out = desk("keygen", "--out", people / f"{name}.pem", choosing=PHRASES[name + ".pem"])
        assert code == 0
        lines[name] = (re.search(r"Public line \(send this to the owner\): ([0-9a-f]{64})", out).group(1),
                       re.search(r"Key id: ([0-9a-f]{64})", out).group(1))
        pem = (people / f"{name}.pem").read_bytes()
        assert b"ENCRYPTED PRIVATE KEY" in pem and (people / f"{name}.pem").stat().st_mode & 0o077 == 0

    # The owner signs the list of appointed people with the offline main key. One person is not a list.
    git(world.owner, "pull", "-q", "--ff-only", "origin", "main")
    folder = world.owner / "badges"
    folder.mkdir()
    code, out = desk("issuers", "--root-key", usb / "root.pem", "--trusted-root", usb / "trusted-root.json",
                     "--key", lines["ana"][0], "--out", folder / "badge-issuers.json")
    assert code == 1 and "at least two people" in out
    code, out = desk("issuers", "--root-key", usb / "root.pem", "--trusted-root", usb / "trusted-root.json",
                     "--key", lines["ana"][0], "--key", lines["ben"][0], "--key", lines["cy"][0],
                     "--out", folder / "badge-issuers.json")
    assert code == 0 and "Signed with the main key" in out and all(lines[name][1] in out for name in ("ana", "ben", "cy"))

    # Two people propose: the checksum comes from the signer's own list, never typed by hand.
    evidence = world.tmp / "private" / "notes-1.0.0-test-record.json"
    evidence.parent.mkdir()
    evidence.write_text(json.dumps({"ran": "install, three asks, uninstall", "build": TARGET["version"]}))
    (folder / "badges.txt").write_text(
        "# what the team vouches for\n"
        f"tested alice/notes 1.0.0 {TARGET['version']} {TARGET['platform']} {evidence}\n"
        'editors-choice alice/notes 1.0.0 "Short, clear, asks before it sends"\n')
    propose = ("propose", "--file", folder / "badges.txt", "--issuers", folder / "badge-issuers.json",
               "--state", world.owner / FOLDER / "state.json", "--out", folder / "badges.json")
    code, out = desk(*propose, "--by", lines["ana"][1][:12], "--by", lines["ana"][1][:12])
    assert code == 1 and "two different people" in out
    code, out = desk(*propose, "--by", lines["ana"][1][:12], "--by", lines["eve"][1][:12])
    assert code == 1 and "not one key id on the issuer list" in out
    code, out = desk(*propose, "--by", lines["ana"][1][:12], "--by", lines["ben"][1][:12])
    assert code == 0 and "new: Tested by VOOL  alice/notes 1.0.0" in out and "new: Editor's Choice  alice/notes 1.0.0" in out
    document = json.loads((folder / "badges.json").read_text())
    assert {item["sha256"] for item in document["signed"]["badges"]} == {submission["sha256"]}
    assert document["signatures"] == []

    # Writing the unsigned file into the signer repository issues nothing: the signer refuses to pass it on.
    check = ("check", folder / "badges.json", "--issuers", folder / "badge-issuers.json",
             "--trusted-root", usb / "trusted-root.json")
    assert desk(*check) == (1, "Refused: 0 of 2 appointed people have signed this document. "
                               "Each one runs: sign --key THEIR-KEY badges.json\n")
    push(world, "Badges, not signed")
    report = run(world)
    assert report["badge_problem"] == "signature threshold not met" and "badges" not in report
    assert quiet({**report, "steps": {"problems": [], "applied": True}})["badges_waiting"] is True
    assert world.http.get(PREFIX + "/badges").json() == {"issuers": None, "badges": None, "stamp": None}
    assert app_rows(world)["alice/notes"]["badges"] == []

    # Each person signs on their own computer. A key that is not appointed is turned away; one signature is not enough.
    git(world.owner, "pull", "-q", "--ff-only", "origin", "main")
    code, out = desk("sign", "--key", people / "eve.pem", folder / "badges.json")
    assert code == 1 and "not on the issuer list" in out
    code, out = desk("sign", "--key", people / "ana.pem", folder / "badges.json")
    assert code == 0 and "1 of 2 signatures" in out
    assert desk(*check)[0] == 1 and "1 of 2 appointed people" in desk(*check)[1]
    push(world, "Badges, one signature")
    assert "badge_problem" in run(world) and app_rows(world)["alice/notes"]["badges"] == []
    git(world.owner, "pull", "-q", "--ff-only", "origin", "main")
    code, out = desk("sign", "--key", people / "ben.pem", folder / "badges.json")
    assert code == 0 and "2 of 2 signatures. It is complete." in out
    code, out = desk(*check)
    assert code == 0 and "Tested by VOOL  alice/notes 1.0.0 on VOOL " + TARGET["version"] in out
    assert all("Passphrase for" in prompt or "passphrase" in prompt or prompt == "Type it again: " for prompt in desk.asked)

    # The signer passes the signed file on with a fresh stamp, and the app draws both badges for these exact bytes.
    push(world, "Badges: alice/notes 1.0.0")
    report = run(world)
    assert report["badges"] == {"sequence": 1, "badges": 2} and "badge_problem" not in report
    assert ("POST", PREFIX + "/authority/badges") in world.sent
    assert app_rows(world)["alice/notes"]["badges"] == ["tested", "editors_choice"]
    world.clock.t += 300
    assert run(world)["badges"] == {"sequence": 1, "badges": 2}  # the next run stamps the same document again
    served = world.http.get(PREFIX + "/badges").json()
    assert served["stamp"]["signed"]["issued_at"] == world.clock.t

    # A new VOOL release: nothing extends by itself, the tool lists what waits for a new test.
    code, out = desk("propose", "--retest", "0.9.0", "--previous", folder / "badges.json", "--issuers", folder / "badge-issuers.json")
    assert code == 0 and "Not yet tested on VOOL 0.9.0: 1" in out and "alice/notes 1.0.0" in out
    code, out = desk("propose", "--retest", TARGET["version"], "--previous", folder / "badges.json",
                     "--issuers", folder / "badge-issuers.json")
    assert code == 0 and f"Not yet tested on VOOL {TARGET['version']}: 0" in out

    # Withdrawal: the line goes, the checksum is named, the number goes up, two people sign again.
    git(world.owner, "pull", "-q", "--ff-only", "origin", "main")
    (folder / "badges.txt").write_text(f"withdrawn {submission['sha256']}\n")
    code, out = desk(*propose, "--previous", folder / "badges.json", "--by", lines["ben"][1][:12], "--by", lines["cy"][1][:12])
    assert code == 0 and "Badge document 2" in out and "dropped: Tested by VOOL  alice/notes 1.0.0" in out
    assert desk("sign", "--key", people / "ben.pem", folder / "badges.json")[0] == 0
    assert desk("sign", "--key", people / "cy.pem", folder / "badges.json")[0] == 0
    assert desk(*check)[0] == 0
    push(world, "Badges: withdraw alice/notes 1.0.0")
    world.clock.t += 300
    assert run(world)["badges"] == {"sequence": 2, "badges": 0}
    assert app_rows(world)["alice/notes"]["badges"] == []
    # It can never be badged again under that checksum.
    (folder / "badges.txt").write_text(f'editors-choice alice/notes 1.0.0 "again"\nwithdrawn {submission["sha256"]}\n')
    code, out = desk(*propose, "--previous", folder / "badges.json", "--by", lines["ben"][1][:12], "--by", lines["cy"][1][:12])
    assert code == 1 and "was withdrawn" in out
    (world.tmp / "transcript.txt").write_text("\n".join(desk.transcript))
    print("\n".join(desk.transcript))


def test_a_line_that_cannot_be_read_names_itself_and_writes_nothing(world, desk):
    _, token, csrf = creator(world, "101", "alice")
    draft(world, token, csrf, "alice/notes")
    assert run(world)["admitted"] == ["alice/notes 1.0.0"]
    git(world.owner, "pull", "-q", "--ff-only", "origin", "main")
    folder, people, usb = world.owner / "badges", world.tmp / "people", world.tmp / "usb"
    folder.mkdir()
    people.mkdir()
    ids = []
    for name in ("ana", "ben"):
        _, out = desk("keygen", "--out", people / f"{name}.pem", choosing=PHRASES[name + ".pem"])
        ids.append((re.search(r"owner\): ([0-9a-f]{64})", out).group(1), re.search(r"Key id: ([0-9a-f]{64})", out).group(1)))
    assert desk("issuers", "--root-key", people / "ana.pem", "--trusted-root", usb / "trusted-root.json",
                "--key", ids[0][0], "--key", ids[1][0], "--out", folder / "badge-issuers.json") == (
        1, "Refused: that is not the main (root) key of this trust root\n")
    assert desk("issuers", "--root-key", usb / "root.pem", "--trusted-root", usb / "trusted-root.json",
                "--key", ids[0][0], "--key", ids[1][0], "--out", folder / "badge-issuers.json")[0] == 0
    cases = {
        "tested alice/notes 2.0.0 0.7.1 macos-arm64 record.json": "line 1: alice/notes 2.0.0 is not in the Skill Codex list",
        "tested alice/notes 1.0.0 0.7.1 macos-arm64 missing.json": "line 1: the test record missing.json cannot be read",
        "tested alice/notes 1.0.0 0.7.1 amiga record.json": "invalid badge targets",
        "editors-choice alice/notes 1.0.0": "line 1: write it as: editors-choice",
        "best alice/notes 1.0.0": "line 1: a line starts with tested, editors-choice or withdrawn",
        'editors-choice alice/notes 1.0.0 "open': "line 1: a quote is not closed",
        "withdrawn abc": "line 1: write it as: withdrawn",
        "tested plugin:text-digest 1.0.0 0.7.1 macos-arm64 record.json": "line 1: after the version, give the checksum",
    }
    (folder / "record.json").write_text("{}")
    for line, reason in cases.items():
        (folder / "badges.txt").write_text(line + "\n")
        code, out = desk("propose", "--file", folder / "badges.txt", "--issuers", folder / "badge-issuers.json",
                         "--state", world.owner / FOLDER / "state.json", "--out", folder / "badges.json",
                         "--by", ids[0][1][:8], "--by", ids[1][1][:8])
        assert code == 1 and out.startswith("Refused: ") and reason in out, (line, out)
        assert not (folder / "badges.json").exists()
    # A plugin or a bundled skill is named by the checksum of its own files.
    (folder / "badges.txt").write_text(
        f"tested plugin:text-digest 1.0.0 sha256:{'a' * 64} 0.7.1 macos-arm64,macos-x86_64 record.json\n"
        f'editors-choice bundled:meeting-notes 1.0.0 sha256:{"b" * 64} "A good first skill"\n')
    code, out = desk("propose", "--file", folder / "badges.txt", "--issuers", folder / "badge-issuers.json",
                     "--out", folder / "badges.json", "--by", ids[0][1][:8], "--by", ids[1][1][:8])
    assert code == 0, out
    entries = json.loads((folder / "badges.json").read_text())["signed"]["badges"]
    assert [(item["kind"], item["digest_type"], len(item["targets"])) for item in entries] == [
        ("plugin", "package", 2), ("skill", "skill-file", 0)]
    assert desk("sign", "--key", people / "ana.pem", folder / "badges.json")[0] == 0
    assert desk("sign", "--key", people / "ana.pem", folder / "badges.json")[1].startswith(
        "Signed badge document 1: 1 of 2 signatures")  # the same person twice is still one signature


def test_no_badge_key_can_be_in_the_workflow_or_the_environment():
    text = (github_authority.TEMPLATES / "skill-codex.yml").read_text()
    flow = yaml.safe_load(text)
    assert set(re.findall(r"secrets\.([A-Za-z0-9_]+)", text)) == {*SECRETS.values(), "SKILL_CODEX_SERVER", "OPENROUTER_API_KEY"}
    for job in flow["jobs"].values():
        for step in job["steps"]:
            for name, value in {**step.get("env", {}), **step.get("with", {})}.items():
                assert "badge" not in (name + str(value)).lower()
            assert "registry_service.badges" not in step.get("run", "")  # the job never proposes or signs a badge
    # The tool takes keys from files and passphrases from the keyboard: it reads no environment at all.
    source = (github_authority.TEMPLATES.parent / "badges.py").read_text()
    assert "os.environ" not in source and "getenv" not in source
    relay = (github_authority.TEMPLATES.parent / "mac_authority.py").read_text()
    body = relay[relay.index("def relay_badges"):relay.index("@_exclusive\n    def run_once")]
    assert "admission_key" in body and "load_pem_private_key" not in body and "os.environ" not in body
