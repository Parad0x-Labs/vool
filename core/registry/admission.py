"""Complete evidence policy. Compatibility and advisory scans alone grant no admission."""
from __future__ import annotations

from core.registry.codec import COMMIT, HEX, digest, require

CHECKS = frozenset({"source", "archive", "secrets", "code", "dependencies", "license",
                    "instructions-primary", "instructions-secondary", "native-boundaries", "compatibility", "quality"})


def publication(value: dict, artifact_sha256: str, kind: str) -> None:
    repository = "Parad0x-Labs/vool-skills" if kind == "skill" else "Parad0x-Labs/vool-plugins"
    require(isinstance(value, dict) and set(value) == {"repository", "commit", "path"} and
            value["repository"] == repository and bool(COMMIT.fullmatch(str(value["commit"]))) and
            value["path"] == "packages/" + artifact_sha256 + ".json", "publication must bind the exact GitHub artifact commit")


def validate_policy(policy: dict) -> str:
    require(isinstance(policy, dict) and set(policy) == {"schema", "checks", "targets", "max_age"} and
            policy["schema"] == "vool.registry.policy.v1", "unknown admission policy")
    require(type(policy["max_age"]) is int and 0 < policy["max_age"] <= 86400, "invalid evidence freshness")
    checks = policy["checks"]
    require(isinstance(checks, dict) and set(checks) == CHECKS, "policy omits required coverage")
    for check in checks.values():
        require(isinstance(check, dict) and set(check) == {"tool", "version", "config_sha256"} and
                all(isinstance(check[key], str) and check[key] for key in check) and
                bool(HEX.fullmatch(check["config_sha256"])), "scanner must have a pinned version and configuration")
    require(checks["instructions-primary"]["tool"] != checks["instructions-secondary"]["tool"],
            "instruction reviews must use independent providers")
    targets = policy["targets"]
    require(isinstance(targets, list) and 0 < len(targets) <= 32 and
            len({digest(target) for target in targets}) == len(targets), "invalid target matrix")
    for target in targets:
        require(isinstance(target, dict) and set(target) == {"version", "commit", "platform"} and
                isinstance(target["version"], str) and target["version"] and
                bool(COMMIT.fullmatch(str(target["commit"]))) and
                target["platform"] in {"macos-arm64", "macos-x86_64", "linux-x86_64", "windows-x86_64"},
                "compatibility requires an exact released build and platform")
    # The shipped root pins the rules/tools, not its own future release commit.
    # Invocation targets are bound separately by the signed compatibility report
    # and admission. Hashing them into a shipped root would create a self-reference.
    return digest({key: value for key, value in policy.items() if key != "targets"})


def admit(artifact: dict, policy: dict, evidence: dict, *, now: int) -> dict:
    from core.registry.validation import manifest_authority

    manifest_authority(artifact["manifest"])
    policy_digest = validate_policy(policy)
    from core.registry.rules import analyze

    require(not analyze(artifact["files"])["conflicts"], "native control override instructions block admission")
    require(isinstance(evidence, dict) and set(evidence) == {
        "schema", "artifact_sha256", "native_digest", "policy_sha256", "source",
        "dependencies", "checks", "reports", "publication"} and evidence["schema"] == "vool.registry.evidence.v1", "invalid evidence bundle")
    require(evidence["artifact_sha256"] == artifact["sha256"] and
            evidence["native_digest"] == artifact["native_digest"] and
            evidence["source"] == artifact["metadata"]["source"] and
            evidence["policy_sha256"] == policy_digest, "evidence describes different input or policy")
    publication(evidence["publication"], artifact["sha256"], artifact["metadata"]["kind"])
    dependencies = evidence["dependencies"]
    declared = artifact["manifest"].get("requires", [])
    require(isinstance(declared, list) and isinstance(dependencies, dict) and
            set(declared) == set(dependencies) and
            all(HEX.fullmatch(str(pin)) for pin in dependencies.values()), "dependency closure is not pinned")
    checks = evidence["checks"]
    require(isinstance(checks, list) and len(checks) == len(CHECKS) and
            {check.get("name") for check in checks if isinstance(check, dict)} == CHECKS,
            "scanner coverage is incomplete or duplicated")
    reports = evidence["reports"]
    require(isinstance(reports, dict) and set(reports) == CHECKS, "raw scanner reports are missing")
    for check in checks:
        require(set(check) == {"name", "scanner", "completed_at", "status", "findings", "coverage", "report_sha256"},
                "invalid scanner evidence")
        require(check["scanner"] == policy["checks"][check["name"]], "scanner pin differs from policy")
        require(type(check["completed_at"]) is int and
                now - policy["max_age"] <= check["completed_at"] <= now, "scanner evidence is stale or future dated")
        require(check["status"] == "complete" and check["findings"] == [] and check["coverage"] == "all" and
                bool(HEX.fullmatch(str(check["report_sha256"]))), check["name"] + ": findings or unsupported scanner coverage block admission")
        report = reports[check["name"]]
        require(isinstance(report, dict) and set(report) == {"schema", "name", "artifact_sha256", "native_digest",
                "policy_sha256", "publication", "scanner", "completed_at", "status", "findings", "coverage", "result"} and
                report["schema"] == "vool.registry.scan_report.v1" and digest(report) == check["report_sha256"],
                check["name"] + ": report checksum or format mismatch")
        require(all(report[key] == evidence[key] for key in ("artifact_sha256", "native_digest", "policy_sha256", "publication")) and
                all(report[key] == check[key] for key in ("name", "scanner", "completed_at", "status", "findings", "coverage")),
                check["name"] + ": report does not bind the reviewed input")
        require(isinstance(report["result"], dict), "scanner result must be structured data")
    from core.registry.rules import CONTROL_IDS

    native = reports["native-boundaries"]["result"]
    require(set(native) == {"obeys_native_rules", "overlaps", "conflicts"} and native["obeys_native_rules"] is True and
            native["conflicts"] == [] and isinstance(native["overlaps"], list) and
            all(isinstance(item, str) and item in CONTROL_IDS for item in native["overlaps"]) and
            len(set(native["overlaps"])) == len(native["overlaps"]), "native rule review is missing, conflicting or claims unknown authority")
    compatibility = reports["compatibility"]["result"]
    require(compatibility.get("targets") == policy["targets"] and compatibility.get("passed") is True,
            "compatibility report did not test the exact released target matrix")
    require(reports["dependencies"]["result"].get("pins") == dependencies,
            "dependency report does not bind the approved closure")
    require(reports["source"]["result"].get("publication") == evidence["publication"] and
            reports["source"]["result"].get("artifact_sha256") == artifact["sha256"],
            "source report does not verify the GitHub publication bytes")
    return {"metadata": artifact["metadata"], "artifact_sha256": artifact["sha256"],
            "artifact_length": artifact["raw_length"],
            "native_digest": artifact["native_digest"], "plugin_id": artifact["manifest"]["name"],
            "dependencies": dependencies, "policy_sha256": policy_digest,
            "evidence_sha256": digest(evidence), "targets": policy["targets"], "publication": evidence["publication"],
            "native_rule_review": native}


def prepublication(artifact: dict, policy: dict, evidence: dict, *, now: int) -> None:
    """Private archive and secret gate only. It grants no public admission."""
    policy_digest = validate_policy(policy)
    require(isinstance(evidence, dict) and set(evidence) == {"schema", "artifact_sha256", "native_digest", "policy_sha256", "checks", "reports"} and
            evidence["schema"] == "vool.registry.prepublication.v1" and
            evidence["artifact_sha256"] == artifact["sha256"] and evidence["native_digest"] == artifact["native_digest"] and
            evidence["policy_sha256"] == policy_digest, "prepublication evidence describes different input")
    checks = evidence["checks"]
    require(isinstance(checks, list) and len(checks) == 2 and
            {check.get("name") for check in checks if isinstance(check, dict)} == {"archive", "secrets"}, "private coverage incomplete")
    reports = evidence["reports"]
    require(isinstance(reports, dict) and set(reports) == {"archive", "secrets"}, "private raw reports are missing")
    for check in checks:
        require(set(check) == {"name", "scanner", "completed_at", "status", "findings", "coverage", "report_sha256"} and
                check["scanner"] == policy["checks"][check["name"]] and type(check["completed_at"]) is int and
                now - policy["max_age"] <= check["completed_at"] <= now and check["status"] == "complete" and
                check["coverage"] == "all" and check["findings"] == [] and bool(HEX.fullmatch(str(check["report_sha256"]))),
                check.get("name", "private scan") + ": private publication scan failed or stale")
        report = reports[check["name"]]
        require(isinstance(report, dict) and set(report) == {"schema", "name", "artifact_sha256", "native_digest",
                "policy_sha256", "publication", "scanner", "completed_at", "status", "findings", "coverage", "result"} and
                report["schema"] == "vool.registry.scan_report.v1" and report["publication"] is None and
                digest(report) == check["report_sha256"] and
                all(report[key] == evidence[key] for key in ("artifact_sha256", "native_digest", "policy_sha256")) and
                all(report[key] == check[key] for key in ("name", "scanner", "completed_at", "status", "findings", "coverage")),
                check["name"] + ": private report does not bind the exact unpublished input")
