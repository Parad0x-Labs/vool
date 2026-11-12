"""The aliased bug-report markers: consistent, readable, unreversible, scanner-clean.

One report redacts through one AliasScope, so the same value keeps one marker everywhere
and different values never share a number. Secrets collapse to the uniform
[SECRET_REMOVED]. The scope (the only value-to-marker map) is never stored or exported,
and the outbound scanner accepts the pipeline's own markers instead of flagging them.
All fixtures are synthetic.
"""
from __future__ import annotations

import json
import re

from core.bug_report import scanner
from core.bug_report.fingerprint import compute_fingerprint
from core.bug_report.redaction import AliasScope, redact_text
from core.bug_report.scanner import mask_local_identifiers, scan_text
from core.bug_report.schema import InvolvedComponents
from core.bug_report.service import BugReportService

# --- synthetic sensitive fixtures (no real person, path, secret or address) ---------------
_PATH_A = "/Users/nobody_fixture_a/Projects/app"
_PATH_B = "/Users/nobody_fixture_b/Projects/app"
_EMAIL_A = "alice@example.com"
_EMAIL_B = "bob@example.com"
_SECRET = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6"
_IP_A = "192.168.50.10"
_IP_B = "192.168.50.11"
_LABEL = "api_key: " + "Zx9Yw8Vu7Ts6Rq5Po4Nm3Lk2Ji1Hg0Fe9Dc8Bb7Aa6"
_PHONE_A = "+44 20 7946 0958"
_PHONE_A_AGAIN = "+44 (20) 7946-0958"  # the same number, spelled another way
_PHONE_B = "+44 20 7946 0959"
_ADDRESS_A = "0x" + "1a2b3c4d5e" * 4
_ADDRESS_B = "0x" + "9f8e7d6c5b" * 4
_CARD = "4111 1111 1111 1111"  # the standard test card number (passes the Luhn check)
_IBAN = "GB82 WEST 1234 5698 7654 32"  # the standard documentation IBAN


def test_same_value_keeps_one_marker_across_fields_of_one_report() -> None:
    scope = AliasScope()
    first, _ = redact_text(f"failed for {_EMAIL_A} at {_PATH_A}", aliases=scope)
    second, _ = redact_text(f"retry later; {_EMAIL_A} saw {_IP_A}", aliases=scope)
    assert f"{_EMAIL_A}" not in first + second
    assert f"{_PATH_A}" not in first + second
    email_marker = first.split(" ")[2]  # "failed for [EMAIL_1] at ..."
    assert email_marker == "[EMAIL_1]"
    assert email_marker in second, "the same email must keep its marker across fields"
    assert "[PERSON_1]" in first and "[LOCAL_PATH_1]" not in first
    assert "[IP_1]" in second


def test_different_values_get_different_numbers() -> None:
    scope = AliasScope()
    text, _ = redact_text(f"{_EMAIL_A} wrote to {_EMAIL_B} from {_IP_A} then {_IP_B}", aliases=scope)
    assert "[EMAIL_1]" in text and "[EMAIL_2]" in text
    assert "[IP_1]" in text and "[IP_2]" in text
    assert _EMAIL_A not in text and _EMAIL_B not in text


def test_home_paths_keep_their_identity_granularity() -> None:
    scope = AliasScope()
    text, _ = redact_text(f"read {_PATH_A} and {_PATH_B}, then ~ and $HOME", aliases=scope)
    # Only the username segment is masked; the project-relative remainder survives for
    # reproduction, and the two different users are PERSON_1 and PERSON_2.
    assert "[PERSON_1]" in text and "[PERSON_2]" in text
    assert "/Projects/app" in text
    assert "nobody_fixture" not in text
    # ~ and $HOME both denote the local home: one LOCAL_PATH marker, consistently.
    assert text.count("[LOCAL_PATH_1]") == 2


def test_phone_numbers_and_wallet_addresses_keep_one_alias_per_value() -> None:
    """A phone number is one alias however it is spelled (keyed by its digits); two
    wallet addresses in one payment story stay told apart."""
    scope = AliasScope()
    text, _ = redact_text(
        f"call {_PHONE_A} or {_PHONE_A_AGAIN}, not {_PHONE_B}; "
        f"sent from {_ADDRESS_A} to {_ADDRESS_B}, refund to {_ADDRESS_A}",
        aliases=scope,
    )
    assert text.count("[PHONE_1]") == 2 and text.count("[PHONE_2]") == 1
    assert text.count("[WALLET_ADDRESS_1]") == 2 and text.count("[WALLET_ADDRESS_2]") == 1
    for raw in ("7946", _ADDRESS_A, _ADDRESS_B):
        assert raw not in text


def test_every_secret_family_collapses_to_one_uniform_marker() -> None:
    scope = AliasScope()
    text, _ = redact_text(f"{_SECRET}; {_LABEL}; Authorization: Bearer abcdef123456", aliases=scope)
    assert _SECRET not in text and "Zx9Yw8" not in text
    assert "[SECRET_REMOVED]" in text
    # never numbered: distinct secrets are not given correlatable identities
    assert "[SECRET_REMOVED_1]" not in text


def test_card_bank_and_pin_values_are_secrets_never_numbered() -> None:
    scope = AliasScope()
    text, _ = redact_text(f"card {_CARD}, iban {_IBAN}, and my PIN is 4433", aliases=scope)
    assert text.count("[SECRET_REMOVED]") == 3, text
    for raw in ("4111", "1111 1111", "WEST", "5698", "4433"):
        assert raw not in text
    assert "[redacted" not in text, "an aliased report speaks one marker vocabulary"
    assert not re.search(r"\[SECRET_REMOVED_\d", text)


def test_both_modes_fire_the_same_rules_so_the_summary_stays_true() -> None:
    """The preview's redaction summary counts rules, not markers: aliasing a report must
    not change what it says was removed."""
    raw = (
        f"{_EMAIL_A} at {_PATH_A} via {_IP_A}; {_SECRET}; card {_CARD}; PIN is 4433; "
        f"call {_PHONE_A}; pay {_ADDRESS_A}; ~ again"
    )
    classic_text, classic = redact_text(raw)
    aliased_text, aliased = redact_text(raw, aliases=AliasScope())
    assert aliased.rule_counts == classic.rule_counts
    assert aliased.total_replacements == classic.total_replacements > 0
    assert "[redacted" in classic_text and "[redacted" not in aliased_text


def test_the_scanner_accepts_the_alias_markers_and_still_catches_real_leaks() -> None:
    scope = AliasScope()
    clean, _ = redact_text(
        f"turn failed for {_EMAIL_A} under {_PATH_A} with {_SECRET} at {_IP_A}; "
        f"Cookie: sid=abc123def456; session: {_EMAIL_B}; call {_PHONE_A}; pay {_ADDRESS_A}; PIN is 4433",
        aliases=scope,
    )
    assert scan_text(clean) == (), f"the pipeline's own aliased output must scan clean: {clean}"

    leaked = clean + f" and later {_EMAIL_B}"
    assert any(f.rule == "email" for f in scan_text(leaked)), "the scanner must still catch a raw email"


def test_redaction_is_idempotent_in_alias_mode() -> None:
    scope = AliasScope()
    once, _ = redact_text(
        f"contact {_EMAIL_A} at {_PATH_A}; password: hunter2hunter2; Cookie: a=b; card {_CARD}",
        aliases=scope,
    )
    twice, again = redact_text(once, aliases=scope)
    assert once == twice, "re-redacting redacted text must change nothing"
    assert again.total_replacements == 0, f"markers were counted as fresh finds: {again.rule_counts}"


def test_the_machine_local_pass_shares_the_report_scope(monkeypatch) -> None:
    """This machine's account name is the same person its home path names; its home folder
    is the place ~ names. In an aliased report each keeps the one marker it already has."""
    monkeypatch.setattr(scanner, "_DYNAMIC_PATTERNS", [
        ("local_home", re.compile(re.escape("/srv/fixture-home"))),
        ("local_username", re.compile(r"(?<![A-Za-z0-9._-])nobody_fixture_a(?![A-Za-z0-9._-])")),
        ("local_hostname", re.compile(r"(?<![A-Za-z0-9._-])fixture-host-01(?![A-Za-z0-9._-])")),
    ])
    monkeypatch.setattr(scanner, "_personal_profile_values", lambda: ["Synthia Fixture"])

    scope = AliasScope()
    text, _ = redact_text(f"opened {_PATH_A} and ~/notes, then /srv/fixture-home/app", aliases=scope)
    text, hits = mask_local_identifiers(
        text + "; nobody_fixture_a on fixture-host-01 asked for Synthia Fixture", aliases=scope
    )
    assert hits == 4
    assert text.count("[PERSON_1]") == 2, text  # the path's user and the account name
    assert text.count("[LOCAL_PATH_1]") == 2, text  # ~ and the home folder itself
    assert "[HOST_1]" in text and "[PERSONAL_1]" in text
    assert "nobody_fixture" not in text and "fixture-host" not in text and "Synthia" not in text
    assert scan_text(text) == (), f"the scanner refused the aliased local pass: {text}"


def test_alias_numbering_never_splits_a_fingerprint() -> None:
    """The same defect is one fingerprint even when another field took the first number;
    a different kind of value is still a different signal."""
    def fingerprint(step: str) -> str:
        return compute_fingerprint(
            category="regression", error=None, components=InvolvedComponents(lanes=(), tools=(), models=()),
            repro_steps=[step],
        )

    assert fingerprint("sign in as [EMAIL_1]") == fingerprint("sign in as [EMAIL_2]")
    assert fingerprint("sign in as [EMAIL_1]") != fingerprint("sign in as [PHONE_1]")


def _service(tmp_path) -> BugReportService:
    return BugReportService(store_dir=tmp_path / "bug_reports", project_root=tmp_path)


def test_a_full_report_build_uses_one_scope_and_stores_no_map(tmp_path) -> None:
    """Through the real service: fields of one draft share markers; the persisted draft
    carries markers only -- the value-to-marker map exists nowhere on disk."""
    service = _service(tmp_path)
    draft = service.create_draft(
        expected=f"works for {_EMAIL_A}",
        actual=f"fails for {_EMAIL_A} reading {_PATH_A} (token {_SECRET})",
        repro_steps=[f"sign in as {_EMAIL_A}", "send one turn"],
        error_text=f"ValueError: cannot reach {_IP_A}",
        title="turn crash",
        log_sources=[{"name": "daemon.log", "lines": [f"2026-09-01T00:00:00 INFO conn from {_IP_A}"]}],
    )
    joined = draft.expected + draft.actual + draft.repro_steps[0] + draft.error.message + draft.logs[0].lines[0]
    for raw in (_EMAIL_A, _PATH_A, _SECRET, _IP_A, "nobody_fixture"):
        assert raw not in joined, f"{raw} survived into the stored draft"
    # one marker per value across every field of the report
    assert joined.count("[EMAIL_1]") == 3
    assert joined.count("[IP_1]") == 2, "the error and the log line name the same address"
    assert "[PERSON_1]" in joined and "[SECRET_REMOVED]" in joined
    # nothing on disk maps a marker back to a value
    for stored in tmp_path.rglob("*"):
        if stored.is_file():
            blob = stored.read_text(errors="replace")
            for raw in (_EMAIL_A, _PATH_A, _SECRET, _IP_A, "nobody_fixture"):
                assert raw not in blob, f"reversible alias material stored in {stored.name}"


def test_an_edited_field_continues_the_numbering(tmp_path) -> None:
    """update_draft mints aliases ABOVE the numbers already in the retained fields, so an
    edited field's [EMAIL_2] can never silently mean the address [EMAIL_1] names."""
    service = _service(tmp_path)
    draft = service.create_draft(expected=f"works for {_EMAIL_A}", actual="fails", repro_steps=["start"])
    assert "[EMAIL_1]" in draft.expected
    updated = service.update_draft(draft.report_id, actual=f"now mentions {_EMAIL_B}")
    assert "[EMAIL_2]" in updated.actual, "an edit must not reuse a live marker number"


def test_an_edit_continues_above_numbers_held_only_in_the_error_or_logs(tmp_path) -> None:
    """The edit path reads the numbers from everything the draft keeps, not only the prose
    fields an edit can touch: the error message and log lines count too."""
    service = _service(tmp_path)
    draft = service.create_draft(
        expected="works",
        actual="fails",
        repro_steps=["start"],
        error_text=f"ValueError: cannot mail {_EMAIL_A}",
        log_sources=[{"name": "daemon.log", "lines": [f"2026-09-01T00:00:00 INFO conn from {_IP_A}"]}],
    )
    assert "[EMAIL_1]" in draft.error.message and "[IP_1]" in draft.logs[0].lines[0]
    updated = service.update_draft(draft.report_id, expected=f"mail {_EMAIL_B} from {_IP_B}")
    assert "[EMAIL_2]" in updated.expected and "[IP_2]" in updated.expected, updated.expected


def test_an_edit_gives_a_value_the_report_already_named_the_same_marker(tmp_path) -> None:
    """The editor re-sends every field it shows, raw. An email the report already calls
    [EMAIL_1] must come back as [EMAIL_1] in the edited field, not as a second person."""
    service = _service(tmp_path)
    draft = service.create_draft(
        expected=f"works for {_EMAIL_A}",
        actual=f"fails for {_EMAIL_A}",
        repro_steps=[f"sign in as {_EMAIL_A}"],
        error_text=f"ValueError: cannot reach {_IP_A}",
    )
    updated = service.update_draft(
        draft.report_id,
        expected=f"works for {_EMAIL_A}",  # unchanged, re-sent by the editor
        actual=f"fails for {_EMAIL_A} when {_IP_A} is down, and for {_EMAIL_B}",
        repro_steps=[f"sign in as {_EMAIL_A}"],
    )
    assert updated.expected == draft.expected, "an unchanged field must come back byte-identical"
    assert updated.repro_steps == draft.repro_steps
    assert updated.actual == "fails for [EMAIL_1] when [IP_1] is down, and for [EMAIL_2]", updated.actual


def test_without_the_open_scope_an_edit_never_reuses_a_number(tmp_path) -> None:
    """After a restart the scope is gone (it was never stored). An edit then numbers above
    every marker the draft holds: a known value may get a new number, but no number is
    ever given to a different value than the one it already means."""
    from core.bug_report import service as service_module

    service = _service(tmp_path)
    draft = service.create_draft(expected=f"works for {_EMAIL_A}", actual="fails", repro_steps=["start"])
    service_module.forget_open_scopes_for_tests()  # what a daemon restart does
    updated = service.update_draft(draft.report_id, actual=f"fails for {_EMAIL_B}")
    assert "[EMAIL_1]" not in updated.actual and "[EMAIL_2]" in updated.actual


def test_the_open_scope_is_memory_only_holds_no_secret_and_ends_with_the_draft(tmp_path, monkeypatch) -> None:
    from core.bug_report import service as service_module

    service = _service(tmp_path)
    draft = service.create_draft(
        expected=f"works for {_EMAIL_A}", actual=f"fails with {_SECRET} and PIN is 4433", repro_steps=["start"]
    )
    key = service._scope_key(draft.report_id)
    scope = service_module._open_scope(key)
    assert scope is not None
    held = json.dumps({family: sorted(values) for family, values in scope._families.items()})
    assert _EMAIL_A in held, "identity values are what the scope exists to remember"
    assert _SECRET not in held and "4433" not in held, "secrets never enter the scope"
    assert _EMAIL_A not in repr(scope), "printing the scope must not print its values"

    monkeypatch.setattr(service_module, "_OPEN_SCOPE_IDLE_SECONDS", -1.0)
    assert service_module._open_scope(key) is None, "an idle scope is dropped"
    monkeypatch.undo()

    draft = service.create_draft(expected=f"works for {_EMAIL_A}", actual="fails", repro_steps=["start"])
    key = service._scope_key(draft.report_id)
    assert service_module._open_scope(key) is not None
    service.delete_draft(draft.report_id)
    assert service_module._open_scope(key) is None, "deleting the draft drops its scope"


def test_fault_evidence_export_with_aliased_report_scans_clean() -> None:
    """The fault block plus aliased prose, as one outbound payload, passes the scanner."""
    scope = AliasScope()
    prose, _ = redact_text(f"failed at {_PATH_A} for {_EMAIL_A} with {_SECRET}", aliases=scope)
    payload = {"prose": prose, "faults": [{"code": "timeout", "title": "Timed out"}]}
    assert scan_text(json.dumps(payload)) == ()
