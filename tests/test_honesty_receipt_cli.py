"""CLI surface for honesty receipts: `verify`, `verify-last`, and the self-contained `demo`."""
from __future__ import annotations

import json

from core.honesty_receipt import (
    VERDICT_BLOCKED,
    VERDICT_CLEAN,
    _latest_ledger_path,
    issue_honesty_receipt,
    main,
    record_honesty_receipt,
)


def _record_two(session_id: str, prev: str = "") -> None:
    r0 = issue_honesty_receipt(
        session_id=session_id, turn_index=0, prev_hash=prev,
        claimed_actions=["file_write"],
        executed_tools=[{"tool": "workspace.write_file", "status": "executed", "receipt_key": "rk-1"}],
        verdict=VERDICT_CLEAN,
    )
    record_honesty_receipt(r0)
    r1 = issue_honesty_receipt(
        session_id=session_id, turn_index=1, prev_hash=r0.content_hash,
        claimed_actions=["funds_sent"], executed_tools=[], verdict=VERDICT_BLOCKED,
    )
    record_honesty_receipt(r1)


def test_verify_last_finds_and_verifies_latest_ledger(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    _record_two("sess-real")
    rc = main(["verify-last"])
    out = capsys.readouterr().out
    assert rc == 0
    # Narrowed at dbc0da59: the verifier proves every receipt PRESENT is valid and
    # correctly linked. It cannot prove none were removed from the end, so the
    # boundary is printed with the verdict and asserted with it.
    assert "EVERY RECEIPT PRESENT VERIFIED" in out
    assert "NOT PROVEN:" in out
    assert "ALL RECEIPTS VERIFIED" not in out
    assert "BLOCKED" in out  # the caught-lie receipt is surfaced, not hidden


def test_verify_last_no_receipts_is_a_clean_miss(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    assert _latest_ledger_path() is None
    rc = main(["verify-last"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "No honesty receipts found" in out


def test_verify_last_session_flag_targets_a_specific_ledger(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    _record_two("sess-a")
    _record_two("sess-b")
    rc = main(["verify-last", "--session", "sess-a"])
    out = capsys.readouterr().out
    assert rc == 0
    # Narrowed at dbc0da59: the verifier proves every receipt PRESENT is valid and
    # correctly linked. It cannot prove none were removed from the end, so the
    # boundary is printed with the verdict and asserted with it.
    assert "EVERY RECEIPT PRESENT VERIFIED" in out
    assert "NOT PROVEN:" in out
    assert "ALL RECEIPTS VERIFIED" not in out


def test_verify_file_path(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    r = issue_honesty_receipt(
        session_id="one", turn_index=0, claimed_actions=["file_write"],
        executed_tools=[{"tool": "t", "status": "executed", "receipt_key": "rk"}], verdict=VERDICT_CLEAN,
    )
    path = tmp_path / "receipt.json"
    path.write_text(json.dumps(r.to_dict()), encoding="utf-8")
    rc = main(["verify", str(path)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "EVERY RECEIPT PRESENT VERIFIED" in out
    assert "NOT PROVEN:" in out


def test_demo_signs_verifies_and_forgery_fails(capsys) -> None:
    rc = main(["demo"])
    out = capsys.readouterr().out
    assert rc == 0
    # The happy path proves; the two forgery attempts both fail, each a different way.
    # Narrowed at dbc0da59: the verifier proves every receipt PRESENT is valid and
    # correctly linked. It cannot prove none were removed from the end, so the
    # boundary is printed with the verdict and asserted with it.
    assert "EVERY RECEIPT PRESENT VERIFIED" in out
    assert "NOT PROVEN:" in out
    assert "ALL RECEIPTS VERIFIED" not in out
    assert "content_hash_mismatch" in out
    assert "bad_signature" in out


def test_demo_output_is_ascii_clean(capsys) -> None:
    # The demo is the shareable artifact: it must render on every terminal, so no non-ASCII.
    main(["demo"])
    out = capsys.readouterr().out
    assert out.isascii(), "demo output must be ASCII-only for cross-terminal rendering"
