"""Privacy redaction: nothing private reaches a demo artifact.

A demo video is an outward-facing artifact by construction — it exists to be
shown. The redaction engine is therefore a hard gate on every text the studio
emits (captions, the public event mirror, manifest free-text), with a leak
scan as the second, independent layer, and the test suite itself as the
third: canary secrets are planted into the inputs and grepped for in every
produced byte.
"""

from __future__ import annotations

import json
import os

from ops.demo_studio.redact import RedactionEngine, leak_scan, redact_text


def _engine(**kwargs) -> RedactionEngine:
    return RedactionEngine(
        workspace_root="/tmp/fake-workspace",
        runtime_home="/Users/someuser/.vool-isolated",
        scan_secrets=kwargs.pop("scan_secrets", []),
    )


def test_home_paths_are_redacted() -> None:
    out = redact_text("staged at /Users/someuser/.vool-isolated/data/x.db", engine=_engine())
    assert "/Users/someuser" not in out
    assert "<home>" in out


def test_arbitrary_user_home_paths_are_redacted() -> None:
    out = redact_text("see /Users/otherperson/Desktop/secret.txt and /home/jbond/notes", engine=_engine())
    assert "otherperson" not in out
    assert "jbond" not in out


def test_workspace_root_is_redacted() -> None:
    out = redact_text("wrote /tmp/fake-workspace/notes/demo.txt", engine=_engine())
    assert "/tmp/fake-workspace" not in out
    assert "<workspace>" in out


def test_api_keys_and_bearer_tokens_are_redacted() -> None:
    text = "key sk-ABCDEFGHIJKLMNOPQRSTUVWXYZ123456 and Bearer abcdef123456789012345"
    out = redact_text(text, engine=_engine())
    assert "sk-" not in out.replace("<redacted-key>", "")
    assert "abcdef123456789012345" not in out
    assert "<redacted-key>" in out
    assert "<redacted-token>" in out


def test_key_value_secrets_are_redacted() -> None:
    out = redact_text('config api_key = "hunter2hunter2" loaded', engine=_engine())
    assert "hunter2" not in out
    assert "api_key = <redacted>" in out or "api_key=<redacted>" in out or "<redacted>" in out


def test_email_addresses_are_redacted() -> None:
    out = redact_text("contact me at some.person@example.com ok", engine=_engine())
    assert "some.person@example.com" not in out
    assert "<redacted-email>" in out


def test_operator_username_is_redacted() -> None:
    name = os.environ.get("USER") or os.environ.get("LOGNAME") or ""
    if not name:
        import getpass

        name = getpass.getuser()
    out = redact_text(f"owned by {name} the operator", engine=_engine())
    assert name not in out


def test_commit_sha_binding_survives_redaction() -> None:
    # The evidence manifest MUST bind the exact commit; the sha is not a
    # secret. Redaction applies to free text only and must not destroy the
    # binding fields (structured manifest fields never pass through the
    # engine at all — this pins the contract that free-text redaction also
    # leaves 40-hex commit ids intact so the binding stays readable).
    sha = "a7b78e2ab523570e2a97c907d0728ea64bcd7bc3"
    out = redact_text(f"recorded at commit {sha}", engine=_engine())
    assert sha in out


def test_long_hex_secrets_are_redacted_but_commit_shas_survive() -> None:
    secret64 = "f" * 64
    commit40 = "a" * 40
    out = redact_text(f"digest {secret64} commit {commit40}", engine=_engine())
    assert secret64 not in out
    assert commit40 in out


def test_scan_secrets_canary_never_survives() -> None:
    canary = "CANARY-sk-0000000000000000000099"
    engine = _engine(scan_secrets=[canary])
    text = f"leaked {canary} into captions"
    out = engine.redact(text)
    assert canary not in out
    violations = engine.scan("captions", out)
    assert violations == []


def test_engine_scan_reports_unredacted_canary_as_violation() -> None:
    canary = "CANARY-token-zzzz1234"
    engine = _engine(scan_secrets=[canary])
    violations = engine.scan("manifest", f"still here {canary}")
    assert violations, "the leak scan must flag a canary that reaches an artifact"
    assert violations[0]["artifact"] == "manifest"
    assert violations[0]["rule"] == "scan_secret"


def test_leak_scan_finds_username_and_home_in_raw_text() -> None:
    violations = leak_scan(
        "captions.srt",
        "path /Users/someuser/.vool-isolated leaked",
        workspace_root="/tmp/fake-workspace",
        runtime_home="/Users/someuser/.vool-isolated",
    )
    assert violations, "raw home paths must be reported by the independent leak scan"


def test_leak_scan_over_every_text_artifact_is_clean_after_redaction() -> None:
    engine = _engine(
        scan_secrets=["CANARY-sk-aaaabbbbccccdddd0000", "CANARY-p4ssw0rd"]
    )
    dirty_events = [
        {
            "seq": 1,
            "event_type": "tool_executed",
            "message": "ran on /Users/someuser/.vool-isolated/data with CANARY-p4ssw0rd",
            "details": {"path": "/tmp/fake-workspace/x.txt"},
        },
        {
            "seq": 2,
            "event_type": "task_completed",
            "message": "done; key sk-realkeynotarealone00000000",
        },
    ]
    captions = [
        engine.redact("ran on /Users/someuser/.vool-isolated/data with CANARY-p4ssw0rd"),
        engine.redact("done; key sk-realkeynotarealone00000000"),
    ]
    # The studio's public mirror redacts EVERY emitted string field — message
    # and details alike — before anything reaches events.jsonl.
    mirror = json.dumps(
        [
            {**e, "message": engine.redact(e["message"]),
             "details": engine.redact(json.dumps(e.get("details") or {}))}
            for e in dirty_events
        ],
        sort_keys=True,
    )
    all_violations = []
    for name, text in [("captions", "\n".join(captions)), ("events-mirror", mirror)]:
        all_violations.extend(engine.scan(name, text))
    assert all_violations == [], f"redacted artifacts must scan clean: {all_violations}"
    assert "CANARY" not in "\n".join(captions) + mirror
