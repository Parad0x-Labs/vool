"""Wrong-answer reports, the browser hand-off and the v0.7.1 scrubbing families.

Driven through the REAL ``dispatch_post`` / ``dispatch_get`` with the transcript seeded through
the production writer (``append_conversation_event`` under a request scope), so the answer a
report quotes is read exactly the way the chat export reads it. GitHub is never contacted: the
token transport is replaced by a recorder that must stay untouched by every hand-off.
"""
from __future__ import annotations

import json
import urllib.parse

import pytest

from core import runtime_paths
from core.web.api.runtime import RuntimeServices
from core.web.api.service import dispatch_get, dispatch_post
from tests.test_chat_export_api import SESSION, _admit_finalize, _request_scope, _seed

REQUEST = "req-wrong-answer-1"
ANSWER = (
    "Your **limit** is 5000 EUR. Call +370 612 34567 or write to bank@example.com.\n\n"
    "- the card ends in 1111\n- @octocat can help"
)
QUESTION = "What is the limit on card 4111 1111 1111 1111? My PIN is 4433."


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    monkeypatch.delenv("VOOL_BUG_REPORT_GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("VOOL_BUG_REPORT_DESTINATION", raising=False)
    runtime_paths.configure_runtime_home(tmp_path)
    yield
    runtime_paths.configure_runtime_home(None)


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    from core.bug_report import github_adapter

    calls: list[str] = []

    def transport(method, url, *, data, headers, timeout):
        calls.append(url)
        raise AssertionError("a hand-off must never open a connection")

    monkeypatch.setattr(github_adapter, "DEFAULT_TRANSPORT", transport)
    return calls


def _rt() -> RuntimeServices:
    return RuntimeServices(display_name="VOOL")


def _post(path: str, body: dict):
    response = dispatch_post(
        path=path,
        body=body,
        headers={"content-type": "application/json"},
        runtime=_rt(),
        model_name="vool",
        workspace_root_provider=lambda: "/tmp",
        client_host="127.0.0.1",
    )
    return response.status, json.loads(response.body)


def _get(path: str, query: dict | None = None):
    response = dispatch_get(path=path, query=query or {}, runtime=_rt(), model_name="vool", client_host="127.0.0.1")
    return response.status, json.loads(response.body)


def _seed_answer(request_id: str = REQUEST, *, user: str = QUESTION, assistant: str = ANSWER) -> None:
    with _request_scope(request_id):
        _seed(user=user, assistant=assistant)


def _answer_draft(**overrides) -> tuple[int, dict]:
    body = {"session_id": SESSION, "request_id": REQUEST, "reason": "wrong"}
    body.update(overrides)
    return _post("/api/bug-report/answer-draft", body)


def _preview(report_id: str, **extra) -> dict:
    status, data = _post("/api/bug-report/preview", {"report_id": report_id, **extra})
    assert status == 200, data
    return data


def _approve(report_id: str, preview: dict) -> None:
    status, data = _post(
        "/api/bug-report/approve",
        {"report_id": report_id, "payload_sha256": preview["payload_sha256"], "confirm": True},
    )
    assert status == 200, data


# --- the answer report quotes one answer, scrubbed, and nothing else ------------------------

def test_whole_answer_report_is_scrubbed_and_leaves_the_question_out():
    _seed_answer()
    status, data = _answer_draft(what_was_wrong="The limit is 3000, not 5000.")
    assert status == 200, data
    body = _preview(data["report_id"])["issue"]["body"]
    title = _preview(data["report_id"])["issue"]["title"]
    assert title.startswith("[wrong-answer]")
    assert "## Wrong answer" in body and "Wrong or made up" in body and "the whole answer" in body
    assert "5000 EUR" in body and "The limit is 3000, not 5000." in body
    for leaked in ("612 34567", "bank@example.com", "4111 1111 1111 1111", "4433", "What is the limit"):
        assert leaked not in body
    assert "[redacted-phone]" in body and "[redacted-email]" in body
    # A public tracker must not notify a stranger named in the answer.
    assert "@octocat" not in body and "@​octocat" in body


def test_question_is_quoted_only_when_the_user_opts_in_and_is_scrubbed_too():
    _seed_answer()
    status, data = _answer_draft(include_question=True)
    assert status == 200, data
    body = _preview(data["report_id"])["issue"]["body"]
    assert "The user's question (included by the user)" in body
    assert "What is the limit on card [redacted-card]?" in body
    assert "PIN is [redacted-pin]" in body and "4433" not in body


def test_selected_text_must_come_from_that_answer():
    _seed_answer()
    status, data = _answer_draft(excerpt="limit is 5000 EUR")  # rendered text, no ** markup
    assert status == 200, data
    body = _preview(data["report_id"])["issue"]["body"]
    assert "> limit is 5000 EUR" in body and "the part of the answer the user selected" in body
    assert "item" not in body.split("## Wrong answer", 1)[1].split("##", 1)[0].replace("excerpt", "")

    status, refused = _answer_draft(excerpt="text from a different chat turn")
    assert status == 404
    assert "not part of this answer" in refused["error"]


def test_unknown_or_forgotten_answers_cannot_be_reported():
    from core.finalization import AVAILABILITY_WITHHELD, set_availability

    status, data = _answer_draft(request_id="req-never-served")
    assert status == 404 and "chat has no saved answers" in data["error"]

    _seed_answer()
    status, data = _answer_draft(request_id="req-never-served")
    assert status == 404 and "not saved yet" in data["error"]

    secret = "FORGOTTEN-ANSWER-5521"
    commit = _admit_finalize(secret, request_id="req-forgotten")
    assert set_availability(commit["finalization_id"], AVAILABILITY_WITHHELD, reason="test")
    _seed_answer("req-forgotten", user="q", assistant=secret)
    status, data = _answer_draft(request_id="req-forgotten")
    assert status == 404 and "forgotten or withheld" in data["error"]


def test_unknown_reason_and_bad_flags_are_refused():
    _seed_answer()
    assert _answer_draft(reason="spam")[0] == 400
    assert _answer_draft(include_question="yes")[0] == 400
    assert _answer_draft(conversation="everything")[0] == 400


def test_report_carries_machine_health_and_receipt_sections_with_unknown_not_guessed():
    _seed_answer()
    status, data = _answer_draft()
    assert status == 200, data
    draft = data["draft"]
    assert draft["kind"] == "wrong_answer"
    assert set(draft["machine"]) >= {"os_version", "chip", "ram_gb", "gpu", "ollama_version"}
    assert "disk_free" in draft["health"]
    assert draft["local_ref"] == {"session_id": SESSION, "request_id": REQUEST}
    body = _preview(data["report_id"])["issue"]["body"]
    assert "- RAM (GB): " in body and "## Health" in body
    # A seeded turn with no recorded execution says so; nothing is invented for it.
    if draft["answer"]["receipt"] is not None:
        assert "## Answer receipt" in body
        assert draft["answer"]["receipt"]["tokens"] in ("unknown",) or draft["answer"]["receipt"]["tokens"].isdigit()
    # Local pointers never leave.
    assert REQUEST not in body and SESSION not in body


def test_each_evidence_part_can_be_removed_and_removal_changes_the_bytes():
    _seed_answer()
    _status, data = _answer_draft(include_question=True)
    report_id = data["report_id"]
    full = _preview(report_id)
    stripped = _preview(report_id, remove_fields=["excerpt", "question", "machine", "health", "receipt"])
    assert stripped["payload_sha256"] != full["payload_sha256"]
    body = stripped["issue"]["body"]
    assert "5000 EUR" not in body and "question" not in body.lower().split("## wrong answer", 1)[-1][:200]
    assert "## Health" not in body and "RAM (GB)" not in body and "## Answer receipt" not in body


def test_two_different_wrong_answers_are_two_reports_the_same_one_twice_is_one():
    _seed_answer()
    _seed_answer("req-wrong-answer-2", assistant="The capital of Australia is Sydney.")
    first = _answer_draft()[1]["fingerprint"]
    again = _answer_draft()[1]["fingerprint"]
    other = _answer_draft(request_id="req-wrong-answer-2")[1]["fingerprint"]
    assert first == again
    assert first != other


# --- the hand-off: no token, no socket, exact approved bytes -----------------------------------

def _decoded_report(url: str) -> tuple[str, dict]:
    parsed = urllib.parse.urlsplit(url)
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}", urllib.parse.parse_qs(parsed.query)


def test_browser_handoff_needs_approval_then_carries_exactly_the_approved_bytes(_no_network):
    _seed_answer()
    report_id = _answer_draft()[1]["report_id"]
    status, refused = _post("/api/bug-report/handoff", {"report_id": report_id, "method": "browser"})
    assert status == 409 and "approve" in refused["error"]

    preview = _preview(report_id)
    _approve(report_id, preview)
    status, handed = _post("/api/bug-report/handoff", {"report_id": report_id, "method": "browser"})
    assert status == 200, handed
    base, query = _decoded_report(handed["url"])
    assert base == "https://github.com/Parad0x-Labs/vool-reports/issues/new"
    assert query["template"] == ["wrong_answer.yml"]
    assert query["title"] == [preview["issue"]["title"]]
    assert query["report"] == [preview["issue"]["body"]]
    assert handed["complete"] is True and handed["payload_sha256"] == preview["payload_sha256"]
    assert handed["search_url"].startswith("https://github.com/Parad0x-Labs/vool-reports/issues?q=")
    assert _no_network == []


def test_an_edit_after_approval_blocks_the_browser_handoff():
    _seed_answer()
    report_id = _answer_draft()[1]["report_id"]
    _approve(report_id, _preview(report_id))
    assert _post("/api/bug-report/update", {"report_id": report_id, "actual": "changed my mind"})[0] == 200
    status, refused = _post("/api/bug-report/handoff", {"report_id": report_id, "method": "browser"})
    assert status == 409


def test_a_report_too_long_for_a_link_goes_to_the_clipboard_instead():
    long_answer = "This is a long wrong answer sentence number filler. " * 70
    _seed_answer(assistant=long_answer)
    report_id = _answer_draft(what_was_wrong="It repeats itself, over and over again. " * 90)[1]["report_id"]
    preview = _preview(report_id)
    _approve(report_id, preview)
    status, handed = _post("/api/bug-report/handoff", {"report_id": report_id, "method": "browser"})
    assert status == 200
    assert handed["complete"] is False
    assert len(handed["url"]) <= 7500
    assert handed["clipboard_text"] == preview["issue"]["body"]
    _base, query = _decoded_report(handed["url"])
    assert "clipboard" in query["report"][0]


def test_clipboard_handoff_needs_no_approval_and_is_the_preview_text():
    _seed_answer()
    report_id = _answer_draft()[1]["report_id"]
    preview = _preview(report_id)
    status, handed = _post("/api/bug-report/handoff", {"report_id": report_id, "method": "clipboard"})
    assert status == 200
    assert handed["clipboard_text"] == f"# {preview['issue']['title']}\n\n{preview['issue']['body']}"
    assert _post("/api/bug-report/handoff", {"report_id": report_id, "method": "email"})[0] == 400


def test_bug_reports_use_the_bug_form_and_other_destinations_a_plain_body():
    status, data = _post("/api/bug-report/draft", {"expected": "a", "actual": "b", "models": ["qwen3:8b"]})
    assert status == 200, data
    preview = _preview(data["report_id"])
    _approve(data["report_id"], preview)
    handed = _post("/api/bug-report/handoff", {"report_id": data["report_id"], "method": "browser"})[1]
    assert _decoded_report(handed["url"])[1]["template"] == ["bug_report.yml"]
    assert "- models: qwen3:8b" in preview["issue"]["body"]

    status, data = _post("/api/bug-report/draft", {"expected": "a", "actual": "b", "destination_repo": "my-org/my-tracker"})
    preview = _preview(data["report_id"])
    _approve(data["report_id"], preview)
    handed = _post("/api/bug-report/handoff", {"report_id": data["report_id"], "method": "browser"})[1]
    base, query = _decoded_report(handed["url"])
    assert base == "https://github.com/my-org/my-tracker/issues/new"
    assert "template" not in query and query["body"] == [preview["issue"]["body"]]


def test_my_reports_lists_how_each_report_left_and_delete_removes_the_local_draft():
    _seed_answer()
    report_id = _answer_draft()[1]["report_id"]
    _approve(report_id, _preview(report_id))
    _post("/api/bug-report/handoff", {"report_id": report_id, "method": "browser"})
    status, listed = _get("/api/bug-report/list")
    assert status == 200
    row = next(r for r in listed["reports"] if r["report_id"] == report_id)
    assert row["kind"] == "wrong_answer" and row["approved"] is True
    assert [h["how"] for h in row["handoffs"]] == ["browser"]
    assert _post("/api/bug-report/delete", {"report_id": report_id}) == (200, {"ok": True, "report_id": report_id})
    assert _post("/api/bug-report/delete", {"report_id": report_id})[0] == 404
    assert all(r["report_id"] != report_id for r in _get("/api/bug-report/list")[1]["reports"])


def test_delete_never_reaches_a_file_outside_the_drafts_folder(tmp_path):
    from core.bug_report.store import DraftStore

    store = DraftStore(tmp_path / "store")
    (tmp_path / "store" / "drafts").mkdir(parents=True)
    outside = tmp_path / "store" / "br_000000000000.json"
    outside.write_text("{}", encoding="utf-8")
    for bad in ("../br_000000000000", "br_000000000000/../../x", "", "BR_000000000000"):
        assert store.delete(bad) is False
    assert store.delete("br_000000000000") is False  # valid shape, but not in the drafts folder
    assert outside.exists()
    assert _post("/api/bug-report/delete", {"report_id": "../../etc/passwd"})[0] == 400


def test_destination_says_whether_a_token_exists_without_naming_it(monkeypatch):
    assert _get("/api/bug-report/destination")[1]["token_available"] is False
    monkeypatch.setenv("VOOL_BUG_REPORT_GITHUB_TOKEN", "ghp_" + "x" * 36)
    data = _get("/api/bug-report/destination")[1]
    assert data["token_available"] is True
    assert "ghp_" not in json.dumps(data)


# --- scrubbing families and model ids ----------------------------------------------------------

def test_personal_numbers_are_masked_and_ordinary_numbers_survive():
    from core.bug_report.redaction import redact_text

    poison = (
        "my PIN is 4433, cvv: 123, OTP 482913, card 4111 1111 1111 1111, phone +370 612 34567, "
        "call me on 0612-345-67, IBAN GB82WEST12345698765432, "
        "wallet 0x52908400098527886E0F7030069857D2E4169EE7 and 7EcDhSYGxXyscszYEp35KHN8vvw3svAuLKTzXwCFLtV"
    )
    clean, summary = redact_text(poison)
    for leaked in ("4433", "123,", "482913", "4111", "612 34567", "345-67", "GB82", "0x5290", "7EcDh"):
        assert leaked not in clean, leaked
    assert {"pin_code", "card_number", "phone", "iban", "wallet_address"} <= set(summary.rule_counts)

    ordinary = (
        "VOOL 0.7.1 on 2026-10-12T18:53:10+00:00 at 18:49:20 used total 12345 tokens, "
        "sha 9464c58f7d28, latency 1234 ms, qwen3:8b, the pin is set, order 4111111111111112"
    )
    assert redact_text(ordinary)[0] == ordinary


def test_scanner_refuses_a_raw_card_pin_or_phone_that_skipped_redaction():
    from core.bug_report.scanner import scan_text

    assert {f.rule for f in scan_text("card 4111 1111 1111 1111")} == {"card_number"}
    assert {f.rule for f in scan_text("my PIN is 4433")} == {"pin_code"}
    assert {f.rule for f in scan_text("ring +370 612 34567")} == {"phone"}
    assert scan_text("record 2026-10-12 version 0.7.1 tokens 12345") == ()


def test_local_model_names_are_kept_and_paths_still_refused():
    from core.bug_report.allowlist import require_identifier

    for good in ("qwen3:8b", "llama3.1:8b-instruct-q4_K_M", "openai/gpt-5.1"):
        assert require_identifier(good, field="model") == good
    for bad in ("../etc", "/Users/x/model", "a b", "x//y"):
        with pytest.raises(ValueError):
            require_identifier(bad, field="model")
    with pytest.raises(ValueError):
        require_identifier("qwen3:8b", field="lane")


def test_a_draft_written_before_this_version_still_loads():
    from core.bug_report.schema import BugReportDraft

    status, data = _post("/api/bug-report/draft", {"expected": "a", "actual": "b"})
    legacy = dict(data["draft"])
    for key in ("kind", "machine", "health", "answer", "local_ref"):
        legacy.pop(key)
    legacy["schema_version"] = 1
    loaded = BugReportDraft.from_dict(legacy)
    assert loaded.kind == "bug" and loaded.answer is None and loaded.machine == {}
    legacy["conversation"] = "injected"
    with pytest.raises(ValueError):
        BugReportDraft.from_dict(legacy)
