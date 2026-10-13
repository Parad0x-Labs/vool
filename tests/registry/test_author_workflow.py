"""Exercise the documented complete example and exclusive SDK output behavior."""
import json
import subprocess
from pathlib import Path

from starlette.testclient import TestClient

from core.registry.artifact import inspect
from core.registry.codec import canonical
from core.registry.trust import TrustedCatalog
from registry_service.api import PREFIX, create_app
from registry_service.auth import Auth
from registry_service.store import Store
from tests.registry.helpers import NOW, TARGET, artifact_fixture, prepublication_fixture, signed, trust_fixture
from tools.community_skills import main


def test_pack_existing_output_emits_only_refusal_and_preserves_bytes(tmp_path, capsys):
    _, source, info = artifact_fixture(tmp_path)
    (source / "registry.json").write_bytes(canonical(info))
    output = tmp_path / "existing.json"
    original = b"Preserve this unrelated file.\n"
    output.write_bytes(original)
    assert main(["pack", str(source), "--scratch", str(tmp_path / "scratch"), "--output", str(output)]) == 1
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["status"] == "refused"
    assert output.read_bytes() == original


def test_scratch_or_output_inside_package_is_refused_without_changing_package(tmp_path, capsys):
    _, source, info = artifact_fixture(tmp_path)
    (source / "registry.json").write_bytes(canonical(info))
    before = sorted(path.relative_to(source).as_posix() for path in source.rglob("*"))
    outside = tmp_path / "scratch"
    attempts = [["lint", str(source), "--scratch", str(source / "scratch")],
                ["pack", str(source), "--scratch", str(source / "scratch"), "--output", str(tmp_path / "a.json")],
                ["pack", str(source), "--scratch", str(outside), "--output", str(source / "out.json")]]
    for argv in attempts:
        assert main(argv) == 1
        lines = capsys.readouterr().out.splitlines()
        assert len(lines) == 1
        assert json.loads(lines[0]) == {"reason": "scratch and output must be outside the package directory",
                                        "status": "refused"}
        assert sorted(path.relative_to(source).as_posix() for path in source.rglob("*")) == before
    assert not (tmp_path / "a.json").exists()
    assert main(["pack", str(source), "--scratch", str(outside), "--output", str(tmp_path / "ok.json")]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "preflight_passed"


def test_documented_example_init_edit_lint_pack_check_and_update(tmp_path, capsys):
    example = Path(__file__).resolve().parents[2] / "docs/registry/examples/meeting-brief"
    source_repo = tmp_path / "source-repo"
    content = source_repo / "meeting-brief"
    content.mkdir(parents=True)
    for name in ("SKILL.md", "LICENSE"):
        (content / name).write_bytes((example / name).read_bytes())
    subprocess.run(["git", "init", "-q", str(source_repo)], check=True)
    subprocess.run(["git", "-C", str(source_repo), "add", "meeting-brief"], check=True)
    identity = ["-c", "user.name=sls_0x", "-c", "user.email=240776969+Parad0x-Labs@users.noreply.github.com"]
    subprocess.run(["git", "-C", str(source_repo), *identity, "commit", "-q", "-m", "Example source fixture",
                    "-m", "VOOL-Task: T-0072"], check=True)
    commit = subprocess.check_output(["git", "-C", str(source_repo), "rev-parse", "HEAD"], text=True).strip()
    # This local repository string exercises syntax; no remote provenance is claimed.
    info = {"id": "example-author/meeting-brief", "kind": "skill", "version": "1.0.0",
            "summary": "Summarize supplied meeting notes with decisions and action owners.",
            "category": "productivity", "subcategory": "meetings", "license": "MIT",
            "source": {"repository": "example-author/meeting-notes", "commit": commit, "path": "meeting-brief"}}
    metadata = tmp_path / "metadata.json"
    metadata.write_bytes(canonical(info))
    draft = tmp_path / "package-draft"
    scratch = tmp_path / "scratch"
    assert main(["init", str(draft), "--metadata", str(metadata), "--license-file", str(content / "LICENSE")]) == 0
    capsys.readouterr()
    skill = draft / "skills/meeting-brief/SKILL.md"
    skill.write_bytes((content / "SKILL.md").read_bytes())
    assert main(["lint", str(draft), "--scratch", str(scratch)]) == 0
    lint = json.loads(capsys.readouterr().out)
    assert lint["status"] == "preflight_passed" and lint["admitted"] is False
    output = tmp_path / "meeting-brief.vool.json"
    assert main(["pack", str(draft), "--scratch", str(scratch), "--output", str(output)]) == 0
    report = json.loads(capsys.readouterr().out)
    raw = output.read_bytes()
    artifact = inspect(raw)
    assert report["artifact_sha256"] == artifact["sha256"]
    assert report["native_digest"] == artifact["native_digest"]
    assert report["artifact_length"] == len(raw)
    assert report["id"] == info["id"] and report["kind"] == "skill" and report["version"] == "1.0.0"
    assert report["package_path"] == "packages/" + artifact["sha256"] + ".json"
    assert Path(report["output_file"]) == output
    assert artifact["files"]["LICENSE"] == (content / "LICENSE").read_bytes()
    assert artifact["files"]["skills/meeting-brief/SKILL.md"] == skill.read_bytes()
    assert main(["check", str(output), "--scratch", str(scratch)]) == 0
    assert json.loads(capsys.readouterr().out)["admitted"] is False
    assert main(["submission-link", str(output), "--github-user", "example-author", "--branch", "submit-meeting-brief"]) == 0
    assert "vool-skills/compare/main...example-author:submit-meeting-brief" in capsys.readouterr().out
    info["version"] = "1.0.1"
    (draft / "registry.json").write_bytes(canonical(info))
    manifest_path = draft / ".codex-plugin/plugin.json"
    manifest = json.loads(manifest_path.read_bytes())
    manifest["version"] = info["version"]
    manifest_path.write_bytes(canonical(manifest))
    updated = tmp_path / "meeting-brief-1.0.1.vool.json"
    assert main(["pack", str(draft), "--scratch", str(scratch), "--output", str(updated)]) == 0
    assert json.loads(capsys.readouterr().out)["version"] == "1.0.1"
    assert inspect(updated.read_bytes())["sha256"] != artifact["sha256"]
    assert output.read_bytes() == raw
    assert main(["check", str(updated), "--scratch", str(scratch)]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "preflight_passed"


def test_complete_example_private_form_review_and_exact_consent(tmp_path):
    example = Path(__file__).resolve().parents[2] / "docs/registry/examples/meeting-brief"
    policy, keys, root = trust_fixture()
    store = Store(tmp_path / "private/accounts.sqlite")
    account = store.identity("google", "private-form-owner", now=NOW)
    store.profile(account, {"namespace": "example-author", "bio": "", "handles": {}})
    token, csrf = store.session(account, now=NOW)
    trust = TrustedCatalog(root, tmp_path / "trust/state", now=NOW)
    try:
        app = create_app(store, Auth(store, "https://registry.test", []), trust, TARGET, clock=lambda: NOW)
        headers = {"Origin": "https://registry.test", "X-Vool-CSRF": csrf}
        body = {"metadata": {"id": "example-author/meeting-brief", "kind": "skill", "version": "1.0.0",
                "summary": "Summarize supplied meeting notes with decisions and action owners.",
                "category": "productivity", "subcategory": "meetings", "license": "MIT"},
                "skill_markdown": (example / "SKILL.md").read_text(),
                "license_text": (example / "LICENSE").read_text()}
        with TestClient(app, base_url="https://registry.test") as client:
            client.cookies.set("vool_registry", token)
            result = client.post(PREFIX + "/drafts", json=body, headers=headers)
            assert result.status_code == 201, result.text
            submission = result.json()
            assert submission["state"] == "quarantined" and not submission["consent"]
            assert client.get(PREFIX + "/submissions/" + submission["id"]).json() == submission
            assert client.get(PREFIX + "/catalog").status_code == 400
            with store.connection() as db:
                raw = db.execute("SELECT artifact FROM submissions WHERE id=?", (submission["id"],)).fetchone()[0]
            assert inspect(raw)["files"]["LICENSE"] == (example / "LICENSE").read_bytes()
            store.review(submission["id"], signed(prepublication_fixture(raw, policy), keys["review"]),
                         policy, root["signed"], now=NOW)
            route = PREFIX + "/submissions/" + submission["id"] + "/consent"
            assert client.post(route, json={"sha256": "0" * 64, "public": True}, headers=headers).status_code == 400
            assert store.submission(account, submission["id"])["state"] == "preflighted"
            consent = client.post(route, json={"sha256": submission["sha256"], "public": True}, headers=headers)
            assert consent.status_code == 200 and consent.json()["state"] == "ready_for_github"
            assert store.export_submission(submission["id"], policy, root["signed"], now=NOW)[0] == raw
            body["metadata"]["version"] = "1.0.1"
            body["skill_markdown"] = "Missing required frontmatter.\n"
            failed = client.post(PREFIX + "/drafts", json=body, headers=headers)
            assert failed.status_code == 201 and failed.json()["state"] == "held"
            assert failed.json()["findings"] and not failed.json()["consent"]
            client.cookies.clear()
            assert client.get(PREFIX + "/submissions/" + submission["id"]).status_code == 400
    finally:
        trust.close()
