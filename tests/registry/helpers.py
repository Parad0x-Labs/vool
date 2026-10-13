"""Ephemeral test signers, not deployment credentials or real scanner outcomes."""
import base64
import copy
import json

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from core.registry.admission import CHECKS, admit, validate_policy
from core.registry.artifact import inspect, native_identity, pack
from core.registry.codec import canonical, digest

NOW = 1802336400
TARGET = {"version": "0.7.1", "commit": "a" * 40, "platform": "macos-arm64"}


def signed(value, keys):
    return {"signed": value, "signatures": [{"keyid": keyid, "sig": base64.b64encode(
        private.sign(canonical(value))).decode()} for keyid, private in keys.items()]}


def trust_fixture():
    policy = {"schema": "vool.registry.policy.v1", "checks": {
        name: {"tool": "test-fixture-" + name, "version": "1", "config_sha256": digest(name)}
        for name in CHECKS}, "targets": [TARGET], "max_age": 3600}
    keys, public, roles = {}, {}, {}
    for role in ("root", "admission", "review", "catalog"):
        group = {}
        for _ in range(1 if role == "catalog" else 2):
            private = Ed25519PrivateKey.generate()
            pub = private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()
            keyid = digest({"ed25519": pub})
            group[keyid], public[keyid] = private, pub
        keys[role] = group
        roles[role] = {"keyids": list(group), "threshold": len(group)}
    root = {"schema": "vool.registry.root.v1", "version": 1, "expires": NOW + 86400,
            "keys": public, "roles": roles, "policies": [validate_policy(policy)]}
    return policy, keys, signed(root, keys["root"])


def artifact_fixture(tmp_path, *, executable=False):
    root = tmp_path / "author"
    (root / ".codex-plugin").mkdir(parents=True)
    (root / ".codex-plugin" / "plugin.json").write_text(json.dumps({
        "name": native_identity("alice/echo"), "version": "1.0.0", "skills": "./skills/", "requires": []}))
    skill = root / "skills" / "echo"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: echo\ndescription: Summarize a provided message\n---\nSummarize only the provided message.\n")
    (root / "LICENSE").write_text("Test fixture licence.\n")
    (root / "empty").mkdir()
    if executable:
        (root / "tool.py").write_text("print('must never run on intake')\n")
        (root / "tool.py").chmod(0o755)
    info = {"id": "alice/echo", "kind": "plugin" if executable else "skill", "version": "1.0.0",
            "summary": "Summarize a message", "category": "productivity", "subcategory": "notes-summaries", "license": "MIT",
            "source": {"repository": "alice/skills", "commit": "b" * 40, "path": "echo"}}
    raw = pack(root, info, tmp_path / "scratch")
    return raw, root, info


def evidence_fixture(raw, policy):
    artifact = inspect(raw)
    value = {"schema": "vool.registry.evidence.v1", "artifact_sha256": artifact["sha256"],
            "native_digest": artifact["native_digest"], "policy_sha256": validate_policy(policy),
            "source": artifact["metadata"]["source"], "dependencies": {},
            "publication": {"repository": "Parad0x-Labs/vool-plugins" if artifact["metadata"]["kind"] == "plugin" else "Parad0x-Labs/vool-skills",
                            "commit": "c" * 40, "path": "packages/" + artifact["sha256"] + ".json"}, "checks": [
                {"name": name, "scanner": copy.deepcopy(policy["checks"][name]), "completed_at": NOW,
                 "status": "complete", "findings": [], "coverage": "all", "report_sha256": digest(name)}
                for name in sorted(CHECKS)]}
    from core.registry.rules import analyze

    value["reports"] = {}
    for check in value["checks"]:
        result = {}
        if check["name"] == "native-boundaries":
            result = {"obeys_native_rules": True, "conflicts": [],
                      "overlaps": [item["control"] for item in analyze(artifact["files"])["native_control_overlaps"]]}
        elif check["name"] == "compatibility":
            result = {"targets": policy["targets"], "passed": True}
        elif check["name"] == "dependencies":
            result = {"pins": {}}
        elif check["name"] == "source":
            result = {"publication": value["publication"], "artifact_sha256": artifact["sha256"]}
        report = {"schema": "vool.registry.scan_report.v1", **{key: copy.deepcopy(value[key]) for key in
            ("artifact_sha256", "native_digest", "policy_sha256", "publication")},
            **{key: copy.deepcopy(check[key]) for key in ("name", "scanner", "completed_at", "status", "findings", "coverage")}, "result": result}
        check["report_sha256"] = digest(report)
        value["reports"][check["name"]] = report
    return value


def prepublication_fixture(raw, policy):
    full = evidence_fixture(raw, policy)
    checks = [check for check in full["checks"] if check["name"] in {"archive", "secrets"}]
    reports = {check["name"]: full["reports"][check["name"]] for check in checks}
    for check in checks:
        reports[check["name"]]["publication"] = None
        check["report_sha256"] = digest(reports[check["name"]])
    return {"schema": "vool.registry.prepublication.v1", "artifact_sha256": full["artifact_sha256"],
            "native_digest": full["native_digest"], "policy_sha256": full["policy_sha256"],
            "checks": checks, "reports": reports}


def catalog_fixture(raw, policy, keys, *, sequence=1, revoked=(), root_version=1):
    entry = admit(inspect(raw), policy, evidence_fixture(raw, policy), now=NOW)
    entry.update(schema="vool.registry.admission.v1", issued_at=NOW, expires=NOW + 7200)
    entries = [signed(entry, keys["admission"])]
    authorization = {"schema": "vool.registry.catalog_authorization.v1", "root_version": root_version,
        "epoch": sequence, "issued_at": NOW, "expires": NOW + 7200,
        "entries_sha256": digest(entries), "revoked_sha256": digest(list(revoked))}
    value = {"schema": "vool.registry.catalog.v1", "root_version": root_version, "sequence": sequence,
             "issued_at": NOW, "expires": NOW + 3600,
             "entries": entries, "revoked": list(revoked), "authorization": signed(authorization, keys["admission"])}
    return signed(value, keys["catalog"])
