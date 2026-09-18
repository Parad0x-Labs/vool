"""The draft-store recovery hold is an operator surface, not a pair of Python functions.

At revision 6, `email_drafts.store_recovery_status` and `acknowledge_store_recovery` existed only as
module functions: no route served them, the Settings page had no email section, and an acknowledgement
was bound to a quarantine file name alone. This suite specifies the operator surface:

* `GET /api/email/recovery` reports the hold in plain terms with what was preserved, the Message-IDs the
  damaged bytes named (with any account they named), and the exact effect an acknowledgement would have;
* `POST /api/email/recovery/check` reconciles one named Message-ID against the account's own sent view
  where an account is known, and records the outcome without deciding for the operator;
* `POST /api/email/recovery/acknowledge` releases NEW approved sends only when it names the exact current
  recovery generation, is confirmed, and persists; a stale generation, a missing confirmation, a failed
  write or a second click are each a distinct, coherent answer;
* the model has no tool that reaches any of it, and the registry refuses the model principal;
* the Settings model carries an email section for the accounts and the recovery hold.

What executes: the production draft store and its recovery (isolated home), the web API dispatch, the
command registry, and the Gmail adapter over loopback against tests/provider_api_fixture.py for the
reconciliation check. No live mailbox or model.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from core import credential_store, email_drafts, runtime_paths
from core.web.api.runtime import RuntimeServices
from core.web.api.service import dispatch_get, dispatch_post
from tests.provider_api_fixture import ProviderApiServer

SESSION = "openclaw:recovery-surface"
WORK = "studio@clayworks.example.test"
LOST = "<lost-7@quarry.example.test>"
#: Torn bytes that recorded an unresolved send on the `work` account (the shape revision 5's recovery
#: tests use, plus the account the reservation named).
TORN = (b'{"version": 1, "drafts": {"ed-lost": {"draft_id": "ed-lost", "status": "delivery_unknown", '
        b'"account_resolved": "work", "sent_message_id": "<lost-7@quarry.example.test>", '
        b'"to": ["buyer@quarry.example.test"], "subject": "Quarry order"')


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    monkeypatch.setenv("VOOL_CREDENTIAL_STORE", "vault")
    runtime_paths.configure_runtime_home(tmp_path)
    email_drafts.reset_drafts()
    yield
    runtime_paths.configure_runtime_home(None)


@pytest.fixture()
def api():
    server = ProviderApiServer()
    server.start()
    try:
        yield server
    finally:
        server.stop()


def _rt() -> RuntimeServices:
    return RuntimeServices(display_name="VOOL")


def _get(path: str):
    response = dispatch_get(path=path, query={}, runtime=_rt(), model_name="vool", client_host="127.0.0.1")
    return response.status, json.loads(response.body.decode("utf-8") or "{}")


def _post(path: str, body: dict, *, client_host: str = "127.0.0.1", headers: dict | None = None):
    response = dispatch_post(
        path=path, body=body,
        headers=headers if headers is not None else {"content-type": "application/json"},
        runtime=_rt(), model_name="vool", workspace_root_provider=lambda: "/tmp", client_host=client_host,
    )
    return response.status, json.loads(response.body.decode("utf-8") or "{}")


def _damage_the_store() -> Path:
    path = email_drafts._drafts_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(TORN)
    return path


def _quarantines(path: Path) -> list[Path]:
    return sorted(path.parent.glob("drafts.corrupt-*.json"))


def _approved(subject: str, *, account: str = "default") -> str:
    saved = email_drafts.save_draft(to="buyer@quarry.example.test", subject=subject, body="Recovery probe.",
                                    account=account, session_id=SESSION)
    assert saved.ok, saved.message
    assert email_drafts.approve_draft(saved.draft["draft_id"], session_id=SESSION).ok
    return saved.draft["draft_id"]


def _store_work_account(api: ProviderApiServer, *, sent: list[dict] | None = None) -> None:
    grant = api.add_oauth_client("gmail", "work", WORK)
    credential_store.store_credential("email.oauth.gmail.work", json.dumps(
        {"client_id": "c", "client_secret": "s", "refresh_token": grant, "token_url": api.token_url,
         "account_email": WORK}), label="work oauth")
    blob = {"provider": "gmail", "from_addr": WORK, "token_url": api.token_url, "api_base": api.gmail_base}
    for kind in ("imap", "smtp"):
        credential_store.store_credential(f"email.{kind}.work", json.dumps(blob), label=f"work {kind}")
    # The fixture's search covers every label (`in:anywhere`), so a message seeded in the mailbox with our
    # Message-ID is what a sent copy looks like to the adapter's reconciliation lookup.
    api.gmail_inbox(WORK, [dict(entry, **{"from": WORK, "body": "Quarry order copy", "date": "Fri, 11 Sep 2026 08:00:00 +0000"})
                           for entry in (sent or [])])


# ---------------------------------------------------------------------------------------------
# The status the operator sees
# ---------------------------------------------------------------------------------------------


def test_the_hold_is_reported_in_plain_terms_with_what_was_preserved_and_what_release_would_do() -> None:
    path = _damage_the_store()
    _approved("Held while the store recovers")
    status, payload = _get("/api/email/recovery")
    assert status == 200, payload
    hold = payload["recovery"]
    assert payload["send_hold"] is True and hold["status"] == "unresolved_effects"
    assert hold["quarantine"] == _quarantines(path)[0].name
    assert hold["sha256"] == hashlib.sha256(TORN).hexdigest() and hold["bytes"] == len(TORN)
    assert LOST in hold["message_ids_in_damaged_store"]
    assert hold["accounts_in_damaged_store"] == ["work"]
    assert payload["generation"], payload
    # Plain language, and the effect of acknowledging stated before anyone confirms it.
    summary = payload["summary"].lower()
    assert "on hold" in summary and LOST in payload["summary"]
    effect = payload["acknowledge_effect"].lower()
    assert "new" in effect and "not" in effect and "resend" in effect
    # Never the damaged bytes, never a credential.
    dumped = json.dumps(payload)
    assert TORN.decode("utf-8") not in dumped and "refresh_token" not in dumped and "password" not in dumped
    # The evidence file is still there, byte for byte.
    assert _quarantines(path)[0].read_bytes() == TORN


def test_a_healthy_store_reports_no_hold() -> None:
    _approved("Nothing held")
    status, payload = _get("/api/email/recovery")
    assert status == 200 and payload["send_hold"] is False and payload["recovery"] is None, payload


def test_the_status_route_is_owner_local() -> None:
    _damage_the_store()
    response = dispatch_get(path="/api/email/recovery", query={}, runtime=_rt(), model_name="vool",
                            client_host="203.0.113.9")
    assert response.status == 403, response.body


# ---------------------------------------------------------------------------------------------
# Acknowledgement binds the exact generation and persists, or it did not happen
# ---------------------------------------------------------------------------------------------


def test_acknowledgement_requires_the_current_generation_and_a_confirmation() -> None:
    path = _damage_the_store()
    did = _approved("Held until acknowledged")
    _status, before = _get("/api/email/recovery")
    generation = before["generation"]
    quarantine = before["recovery"]["quarantine"]

    status, payload = _post("/api/email/recovery/acknowledge", {"quarantine": quarantine, "generation": generation})
    assert status == 409 and payload["status"] == "confirmation_required", payload
    status, payload = _post("/api/email/recovery/acknowledge",
                            {"quarantine": quarantine, "generation": "stale-tab", "confirm": True})
    assert status == 409 and payload["status"] == "generation_mismatch", payload
    status, payload = _post("/api/email/recovery/acknowledge",
                            {"quarantine": "drafts.corrupt-0.json", "generation": generation, "confirm": True})
    assert status == 409 and payload["status"] == "quarantine_mismatch", payload
    assert email_drafts.send_draft(did, session_id=SESSION).status == "store_recovery_hold"

    status, payload = _post("/api/email/recovery/acknowledge",
                            {"quarantine": quarantine, "generation": generation, "confirm": True})
    assert status == 200 and payload["status"] == "acknowledged" and payload["send_hold"] is False, payload
    assert payload["recovery"]["acknowledged_via"] == "operator_surface"
    # The evidence and the unresolved identifiers survive the acknowledgement.
    assert _quarantines(path)[0].read_bytes() == TORN
    _status, after = _get("/api/email/recovery")
    assert after["send_hold"] is False and LOST in after["recovery"]["message_ids_in_damaged_store"]
    # Released: the NEW approved send reaches the dispatch seam (and stops there without credentials).
    assert email_drafts.send_draft(did, session_id=SESSION).status == "needs_credentials"


def test_a_second_click_and_a_restart_read_the_same_answer() -> None:
    _damage_the_store()
    _status, before = _get("/api/email/recovery")
    body = {"quarantine": before["recovery"]["quarantine"], "generation": before["generation"], "confirm": True}
    assert _post("/api/email/recovery/acknowledge", body)[1]["status"] == "acknowledged"
    status, payload = _post("/api/email/recovery/acknowledge", body)
    assert status == 200 and payload["status"] == "no_hold" and payload["send_hold"] is False, payload
    # A fresh load (what a restart does) reads the persisted acknowledgement.
    state = email_drafts._load()
    assert state["recovery"]["send_hold"] is False and state["recovery"]["status"] == "acknowledged"


def test_a_new_damage_after_the_page_loaded_makes_the_old_generation_stale() -> None:
    path = _damage_the_store()
    _status, first = _get("/api/email/recovery")
    # The store is damaged again (a second, different quarantine generation).
    path.write_bytes(TORN.replace(b"lost-7", b"lost-8"))
    _status, second = _get("/api/email/recovery")
    assert second["generation"] != first["generation"] and len(_quarantines(path)) == 2
    status, payload = _post("/api/email/recovery/acknowledge",
                            {"quarantine": first["recovery"]["quarantine"], "generation": first["generation"], "confirm": True})
    assert status == 409 and payload["status"] in {"generation_mismatch", "quarantine_mismatch"}, payload
    assert email_drafts._load()["recovery"]["send_hold"] is True


def test_a_failed_persist_is_not_reported_as_released(monkeypatch) -> None:
    _damage_the_store()
    did = _approved("Still held after a failed write")
    _status, before = _get("/api/email/recovery")
    original = email_drafts._save

    def refuse(state):
        if state.get("recovery", {}).get("send_hold") is False:
            raise OSError("synthetic disk failure while persisting the acknowledgement")
        return original(state)

    monkeypatch.setattr(email_drafts, "_save", refuse)
    status, payload = _post("/api/email/recovery/acknowledge",
                            {"quarantine": before["recovery"]["quarantine"], "generation": before["generation"], "confirm": True})
    assert status == 500 and payload["status"] == "acknowledgement_not_persisted" and payload["send_hold"] is True, payload
    monkeypatch.setattr(email_drafts, "_save", original)
    assert email_drafts._load()["recovery"]["send_hold"] is True
    assert email_drafts.send_draft(did, session_id=SESSION).status == "store_recovery_hold"


def test_acknowledgement_is_owner_local_json_and_same_origin() -> None:
    _damage_the_store()
    _status, before = _get("/api/email/recovery")
    body = {"quarantine": before["recovery"]["quarantine"], "generation": before["generation"], "confirm": True}
    assert _post("/api/email/recovery/acknowledge", body, client_host="203.0.113.9")[0] == 403
    assert _post("/api/email/recovery/acknowledge", body, headers={"content-type": "text/plain"})[0] == 415
    assert _post("/api/email/recovery/acknowledge", body,
                 headers={"content-type": "application/json", "origin": "https://evil.example"})[0] == 403
    assert email_drafts._load()["recovery"]["send_hold"] is True


# ---------------------------------------------------------------------------------------------
# Reconciliation of a named Message-ID, where an account is known
# ---------------------------------------------------------------------------------------------


def test_a_named_message_id_can_be_checked_against_the_accounts_sent_view(api) -> None:
    _store_work_account(api, sent=[{"message_id": LOST, "subject": "Quarry order", "to": "buyer@quarry.example.test"}])
    _damage_the_store()
    status, payload = _post("/api/email/recovery/check", {"message_id": LOST})
    assert status == 200, payload
    check = payload["check"]
    assert check["message_id"] == LOST and check["account"] == "work" and check["status"] == "found", check
    _status, after = _get("/api/email/recovery")
    assert after["recovery"]["checks"][LOST]["status"] == "found"
    assert after["send_hold"] is True, "a found message resolves that id; it does not release the hold by itself"


def test_an_absent_or_uninspectable_sent_view_keeps_the_hold_and_says_so(api) -> None:
    _store_work_account(api)
    _damage_the_store()
    status, payload = _post("/api/email/recovery/check", {"message_id": LOST})
    assert status == 200 and payload["check"]["status"] == "absent", payload
    assert "not proof" in payload["check"]["message"].lower()
    credential_store.delete_credential("email.oauth.gmail.work")
    status, payload = _post("/api/email/recovery/check", {"message_id": LOST})
    assert status == 200 and payload["check"]["status"] == "unverified", payload
    assert email_drafts._load()["recovery"]["send_hold"] is True


def test_a_message_id_the_hold_never_named_cannot_be_checked(api) -> None:
    _store_work_account(api)
    _damage_the_store()
    status, payload = _post("/api/email/recovery/check", {"message_id": "<never-named@example.test>"})
    assert status == 409 and payload["status"] == "unknown_message_id", payload


# ---------------------------------------------------------------------------------------------
# The model cannot acknowledge; the operator surface exists
# ---------------------------------------------------------------------------------------------


def test_no_model_tool_reaches_the_acknowledgement() -> None:
    from core.command_registry.execute import ExecutionContext, execute_command
    from core.runtime_tool_contracts import runtime_tool_contract_map

    assert not [intent for intent in runtime_tool_contract_map() if "recover" in intent or "acknowledge" in intent]
    _damage_the_store()
    state = email_drafts._load()
    envelope = execute_command("email.recovery.acknowledge",
                               {"payload": {"quarantine": state["recovery"]["quarantine"], "confirm": True}},
                               context=ExecutionContext(projection="chat", principal="model"))
    assert not envelope.ok, envelope
    assert email_drafts._load()["recovery"]["send_hold"] is True


def test_the_settings_model_carries_the_email_section() -> None:
    from core.vool_settings_page import render_vool_settings_html, settings_groups

    groups = {group["id"]: group for group in settings_groups()}
    assert "email" in groups, sorted(groups)
    rows = {row["id"]: row for row in groups["email"]["rows"]}
    assert {"email_accounts", "email_recovery"} <= set(rows), sorted(rows)
    assert rows["email_accounts"]["read"]["url"] == "/api/email/accounts"
    assert rows["email_recovery"]["read"]["url"] == "/api/email/recovery"
    html = render_vool_settings_html()
    assert "function widgetEmailAccounts" in html and "function widgetEmailRecovery" in html
