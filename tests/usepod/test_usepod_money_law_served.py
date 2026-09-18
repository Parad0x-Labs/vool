"""The served UsePod money-law integration: one real prepaid call, money that stays found.

A real daemon (``apps.vool_api_server``, unchanged, production monetary authority = the merged
money law) against the SYNTHETIC strict local service, with one labelled SYNTHETIC spend grant
minted through the real operator authority by ``_money_law_served_launcher``. The flow is the
user's flow: Settings credential + discovery refresh (which is also the liquidity observation) →
route approval → paid pin → chat → a durable settled liability readable through the owner-local
money routes, surviving a daemon restart. Then the refusal and recovery controls.

No paid inference, no live credential, no wallet: every fund is synthetic and labelled.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.usepod.strict_usepod_service import Listing, StrictUsePodService
from tests.usepod.test_usepod_served_flow import (
    INFERENCE_PATHS,
    MARKET,
    MARKET_ID,
    MODEL,
    UsePodServedDaemon,
    _free_port,
    _session,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
LAUNCHER = Path(__file__).with_name("_money_law_served_launcher.py")
CENTRAL = ("groq", 700_000, 2_100_000)
LANE_ID = f"usepod-byok:{MODEL}"


class MoneyLawDaemon(UsePodServedDaemon):
    """The production daemon (no monetary double): the money law itself, plus the launcher's
    synthetic grant. Restartable on the same home for the reconciliation controls."""

    def __init__(self, home: Path) -> None:
        super().__init__(home)
        self.operator_token_path = home.parent / "operator_token.txt"

    def env(self) -> dict[str, str]:
        env = super().env()
        env.pop("USEPOD_SERVED_DOUBLE_JOURNAL", None)
        env["USEPOD_MONEY_TEST_OPERATOR_TOKEN"] = str(self.operator_token_path)
        return env

    def start(self, timeout: float = 240.0) -> "MoneyLawDaemon":
        self.home.mkdir(parents=True, exist_ok=True)
        handle = self.log_path.open("ab")
        self.process = subprocess.Popen(
            [sys.executable, "-B", str(LAUNCHER), "--port", str(self.port), "--bind", "127.0.0.1"],
            cwd=str(REPO_ROOT),
            env=self.env(),
            stdout=handle,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
        from urllib.error import HTTPError, URLError
        from urllib.request import urlopen

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError(f"daemon exited {self.process.returncode}\n{self.log_tail()}")
            try:
                with urlopen(f"{self.base_url}/healthz", timeout=3) as response:
                    if response.status == 200:
                        return self
            except (URLError, HTTPError, OSError):
                time.sleep(1.0)
        raise TimeoutError(f"daemon never became healthy\n{self.log_tail()}")

    def kill(self) -> None:
        if self.process is None:
            return
        try:
            os.killpg(os.getpgid(self.process.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        self.process.wait(timeout=15)
        self.process = None

    def restart(self) -> None:
        self.port = _free_port()
        self.start()
        # Re-register the UsePod lane through the same owner door Settings uses: lane
        # registration happens at credential-save time in a process, and a restarted daemon has
        # not re-registered it from the stored credential on its own.
        from core.usepod.lane import LanePreference, load_lane_preference, save_lane_preference

        preference, _error = load_lane_preference()
        status, _lane = self.call("POST", "/api/cloud/usepod/lane", preference.to_dict())
        assert status == 200, f"lane re-registration after restart failed: {status}"

    def operator_token(self):
        """The operator token the launcher minted, reconstructed in THIS process against the
        daemon's own store (the money law's cross-process SQLite deployment shape)."""
        from core.effect_budget import OperatorBudgetToken

        self.adopt_store()
        text = self.operator_token_path.read_text("utf-8").strip()
        return OperatorBudgetToken(token_id=text, note="synthetic test funds", granted_at="")

    def adopt_store(self) -> None:
        """Point THIS process's money store at the daemon's home, so grant minting, revocation
        and projection reads see exactly what the daemon sees. BOTH seams must move: the root
        test conftest pins an explicit default db path that would otherwise outrank the home."""
        from core import effect_budget, runtime_paths
        from storage.db import configure_default_db_path

        runtime_paths.configure_runtime_home(self.home)
        configure_default_db_path(str(self.home / "data" / "vool_web0_v2.db"))
        effect_budget.reset_effect_budget_process_state()


def _chat(daemon, text: str, session_id: str, *, stream: bool = False):
    payload = {"messages": [{"role": "user", "content": text}], "stream": stream, "session_id": session_id or BOUND_SESSION, "model": LANE_ID, "mode": "auto"}
    return daemon.call("POST", "/api/chat", payload, timeout=300.0)


def _answer_text(answer) -> str:
    if isinstance(answer, dict):
        message = answer.get("message") if isinstance(answer.get("message"), dict) else {}
        return str(message.get("content") or "")
    parts = []
    for line in str(answer or "").splitlines():
        line = line.strip()
        if line.startswith("data:"):
            line = line[5:].strip()
        if not line.startswith("{"):
            continue
        try:
            frame = json.loads(line)
        except ValueError:
            continue
        message = frame.get("message") if isinstance(frame.get("message"), dict) else {}
        if isinstance(message.get("content"), str):
            parts.append(message["content"])
    return "".join(parts)


def _keep(name: str, payload: object) -> None:
    keep = os.environ.get("USEPOD_SERVED_ARTIFACT_DIR")
    if keep:
        Path(keep).mkdir(parents=True, exist_ok=True)
        (Path(keep) / name).write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")


@pytest.fixture(scope="module")
def served(tmp_path_factory):
    root = tmp_path_factory.mktemp("usepod-money-served")
    token = str(uuid.uuid4())
    service = StrictUsePodService(
        tokens={token: 80_000_000},
        models={MODEL: [Listing("marketplace", MARKET_ID, *MARKET), Listing("centralized", *CENTRAL)]},
    ).start()
    daemon = MoneyLawDaemon(root / "home")
    try:
        daemon.start()
        status, saved = daemon.call("POST", "/api/settings/credentials", {"provider": "usepod", "value": f"{service.origin}/proxy/{token}/v1", "base_url": service.origin})
        assert status == 200, saved
        # The discovery refresh is ALSO the liquidity observation: one owner action, two laws fed.
        status, refreshed = daemon.call("POST", "/api/cloud/usepod/refresh", {})
        assert status == 200 and refreshed["credential"]["state"] == "observed", refreshed
        status, _policy = daemon.call("POST", "/api/cloud/usepod/route-policy", {"mode": "marketplace-only"})
        assert status == 200
        status, pinned = daemon.call("POST", "/api/cloud/model", {"model": MODEL, "provider": "usepod", "confirm_paid": True})
        assert status == 200 and pinned.get("ok") is True, pinned
        status, approved = daemon.call("POST", "/api/cloud/usepod/approve-route", {"model_id": MODEL})
        assert status == 200, approved
        served = SimpleNamespace(service=service, daemon=daemon, token=token, fingerprint=saved["credential_fingerprint"])
        daemon.adopt_store()
        yield served
    finally:
        daemon.stop()
        service.stop()


#: The one session the synthetic grants bind: a task_envelope grant must name its task or
#: session, and every chat in this drive runs inside it. The chat door canonicalizes session ids
#: exactly like the composer (`openclaw:` + a sha256 prefix), so the grant binds the CANONICAL
#: form the turn ledger will actually carry.
BOUND_SESSION = "openclaw:" + __import__("hashlib").sha256(b"usepod-money-law-drive").hexdigest()[:20]


def _mint_grant(daemon, fingerprint: str, *, max_total: int = 5_000_000, per_operation: int = 5_000_000, kind: str = "single_payment", credit_liquidity: str = "required") -> str:
    """Mint one labelled SYNTHETIC grant through the real operator authority, bound to the
    credential's fingerprint (the prepaid provider account the grant's liquidity governs) and to
    this drive's session."""
    from core.effect_budget_money import AssetIdentity, MoneyGrantSpec, grant_money_authority
    from core.usepod.money_law import USEPOD_ACCOUNT_NETWORK

    grant = grant_money_authority(
        daemon.operator_token(),
        MoneyGrantSpec(
            kind=kind, operation_kinds=("inference_prepaid",), provider_id="usepod",
            asset=AssetIdentity(network=USEPOD_ACCOUNT_NETWORK, asset="USDC", decimals=6),
            max_total_atomic=max_total, per_operation_max_atomic=per_operation,
            # The grant's route allowlist names the approval's permitted set exactly:
            # marketplace-only permits marketplace AND key_relay, joined by the route identity.
            models=(MODEL,), routes=("key_relay+marketplace",),
            expires_epoch=time.time() + 6 * 3600.0, credit_liquidity=credit_liquidity,
            provider_account=fingerprint, approval_ref="synthetic:test-drive",
            note="synthetic test funds: usepod money-law served drive",
        ),
    )
    return grant.grant_id


def _pin_and_approve(served) -> None:
    status, pinned = served.daemon.call("POST", "/api/cloud/model", {"model": MODEL, "provider": "usepod", "confirm_paid": True})
    assert status == 200 and pinned.get("ok") is True, pinned
    status, approved = served.daemon.call("POST", "/api/cloud/usepod/approve-route", {"model_id": MODEL})
    assert status == 200, approved


def _usepod_receipts(events):
    out = []
    for event in events:
        details = event.get("details") if isinstance(event.get("details"), dict) else event
        receipt = details.get("provider_receipt") if isinstance(details, dict) else None
        if isinstance(receipt, dict) and receipt.get("provider") == "usepod":
            out.append(receipt)
    return out


# --- THE flow: Settings → approval → one real prepaid call → durable settlement -----------------------


def test_one_served_prepaid_call_reserves_claims_dispatches_and_settles_durally(served) -> None:
    daemon, service = served.daemon, served.service
    _mint_grant(daemon, served.fingerprint)
    session_id = _session(f"money-happy-{uuid.uuid4()}")
    status, answer = _chat(daemon, "Write a short thank-you note to a neighbor who watered my plants.", session_id)
    events = daemon.events(session_id)
    receipts = _usepod_receipts(events)
    _keep("money_served_happy.json", {"status": status, "answer": answer, "events": events, "receipts": receipts})
    assert status == 200, answer
    assert "synthetic reply" in _answer_text(answer)
    assert service.requests_to(INFERENCE_PATHS["openai"]) and len(service.requests_to(INFERENCE_PATHS["openai"])) == 1
    # The receipt names the money law and the durable liability id.
    assert receipts, "no UsePod receipt on the money-law turn"
    receipt = receipts[-1]
    assert receipt["monetary_authority"] == "effect_budget_money:v1"
    assert receipt["reservation_id"].startswith("mli:")
    assert receipt["settlement"] == {"outcome": "completed", "recording": "settled_with_evidence"}
    # The owner-local money routes show the settled liability as TRUTHFULLY BOUNDED: the
    # provider reported usage but no exact charge, so the lines settle bounded at their maximum
    # (settled_bounded), never exact (settled_exact stays zero -- a ceiling is not a bill).
    status, projection = daemon.call("GET", "/api/money/projection?provider_id=usepod")
    assert status == 200, projection
    inference = projection["projection"]["inference_expense"]
    assert sum(int(bucket.get("settled_bounded") or 0) for bucket in inference.values()) > 0, inference
    assert sum(int(bucket.get("settled_exact") or 0) for bucket in inference.values()) == 0, inference
    assert projection["projection"]["unknown_liabilities"] == []
    _keep("money_served_projection.json", projection)


def test_the_settlement_survives_a_daemon_restart(served) -> None:
    daemon = served.daemon
    status, before = daemon.call("GET", "/api/money/projection?provider_id=usepod")
    assert status == 200
    daemon.kill()
    daemon.restart()
    status, after = daemon.call("GET", "/api/money/projection?provider_id=usepod")
    assert status == 200, after
    assert after["projection"]["liability_states"] == before["projection"]["liability_states"]
    assert sum(int(b.get("settled_bounded") or 0) for b in after["projection"]["inference_expense"].values()) > 0
    assert sum(int(b.get("settled_exact") or 0) for b in after["projection"]["inference_expense"].values()) == 0
    # And the lane still works after the restart: a second call settles a second liability.
    _mint_grant(daemon, served.fingerprint)
    session_id = _session(f"money-after-restart-{uuid.uuid4()}")
    status, answer = _chat(daemon, "Write a short note congratulating a colleague on finishing a marathon.", session_id)
    assert status == 200 and "synthetic reply" in _answer_text(answer)
    receipts = _usepod_receipts(daemon.events(session_id))
    assert receipts[-1]["settlement"]["recording"] == "settled_with_evidence"
    _keep("money_served_after_restart.json", {"receipt": receipts[-1]})


def test_a_stream_lost_after_dispatch_retains_the_liability_as_unknown(served) -> None:
    """The ACTUAL mode of this served path buffers the provider call and replays the client
    stream from it, so the "lost after dispatch" case here is the response body dying mid-read
    (an unparsable 200). The adapter-level partial-PROVIDER-stream case is covered at the
    adapter boundary in test_usepod_adapter_lane.py."""
    daemon, service = served.daemon, served.service
    _mint_grant(daemon, served.fingerprint)
    served.service.faults["malformed_body"] = True
    try:
        session_id = _session(f"money-stream-{uuid.uuid4()}")
        status, answer = _chat(daemon, "Write a short reminder asking my roommate to take out the recycling.", session_id, stream=True)
        events = daemon.events(session_id)
        _keep("money_served_stream_lost.json", {"status": status, "answer": str(answer)[:400], "events": events})
        status, projection = daemon.call("GET", "/api/money/projection?provider_id=usepod")
        unknown = projection["projection"]["unknown_liabilities"]
        assert unknown, "a stream lost after the request left must hold its liability, not release it"
        # And the unknown holds its MAXIMUM until evidence closes it -- never read as free.
        status, liabilities = daemon.call("GET", "/api/money/liabilities?state=unknown")
        assert status == 200
        rows = liabilities["liabilities"]
        assert any(str(row.get("state")) == "unknown" for row in rows), rows
    finally:
        served.service.faults.pop("malformed_body", None)


def test_revoking_the_grant_stops_the_next_call_before_any_dispatch(served) -> None:
    daemon, service = served.daemon, served.service
    grant_id = _mint_grant(daemon, served.fingerprint)
    status, grants = daemon.call("GET", "/api/money/grants?active=1")
    assert status == 200 and grants["grants"], grants
    before = len(service.requests_to(INFERENCE_PATHS["openai"]))
    status, revoked = daemon.call("POST", "/api/money/grants/revoke", {"grant_id": grant_id, "reason": "test revocation"})
    assert status == 200, revoked
    session_id = _session(f"money-revoked-{uuid.uuid4()}")
    status, answer = _chat(daemon, "Write a short welcome message for a new member of a book club.", session_id)
    events = daemon.events(session_id)
    blob = json.dumps(events)
    _keep("money_served_revoked.json", {"status": status, "answer": answer, "events": events})
    # The refusal names the money law -- the code is whatever actually blocked this dispatch
    # (a fresh drive: MONEY_AUTHORITY_REVOKED; after sibling grants spent their envelopes:
    # the law's own exhaustion code with the revoked grant named in the refusal detail).
    assert "MONEY_AUTHORITY_" in blob, blob[:400]
    assert "revoked" in blob.lower(), "the refusal must name the revoked grant"
    assert len(service.requests_to(INFERENCE_PATHS["openai"])) == before, "nothing was dispatched after revocation"
    # Held liabilities keep their state (unit-proven in test_usepod_money_law.py:
    # a revoked grant stops new reservations but keeps held ones); nothing more to show here
    # when this test runs alone and no liability ever existed.


def test_a_crashed_claimant_reconciles_to_unknown_after_restart(served) -> None:
    daemon, service = served.daemon, served.service
    # A fresh grant for this control (the previous test's grant was revoked).
    _mint_grant(daemon, served.fingerprint)
    service.faults["delay_seconds"] = 20
    session_id = _session(f"money-crash-{uuid.uuid4()}")
    result: dict = {}

    def run() -> None:
        try:
            result["r"] = _chat(daemon, "Write a short apology for missing yesterday's standup meeting.", session_id)
        except Exception as exc:  # the connection dies with the process
            result["error"] = str(exc)

    worker = threading.Thread(target=run)
    worker.start()
    time.sleep(6.0)   # past reserve + claim, mid provider wait
    daemon.kill()
    worker.join(timeout=30.0)
    service.faults.pop("delay_seconds", None)
    daemon.restart()
    status, reconciled = daemon.call("POST", "/api/money/reconcile", {})
    assert status == 200, reconciled
    _keep("money_served_crash_reconcile.json", {"reconcile": reconciled, "chat": str(result)[:300]})
    status, projection = daemon.call("GET", "/api/money/projection?provider_id=usepod")
    # A claimed liability whose claimant died becomes UNKNOWN (it may have been sent and billed),
    # never released by the crash; an unclaimed one is released.
    changes = [change for change in reconciled.get("money_changes", []) if str(change.get("to") or "") == "unknown"]
    assert changes, reconciled
    assert any("process_gone" in json.dumps(change) or "instance" in json.dumps(change) for change in changes), changes


def test_competing_reservations_share_one_grant_and_only_one_dispatches(served) -> None:
    daemon, service = served.daemon, served.service
    # A grant with room for exactly ONE call.
    _mint_grant(daemon, served.fingerprint, credit_liquidity="not_required")
    before = len(service.requests_to(INFERENCE_PATHS["openai"]))
    sessions: list[str] = []
    results: list = []
    lock = threading.Lock()

    def run(index: int) -> None:
        session_id = _session(f"money-race-{index}-{uuid.uuid4()}")
        with lock:
            sessions.append(session_id)
        try:
            results.append(_chat(daemon, f"Write a two-sentence note number {index} for a colleague.", session_id))
        except Exception as exc:
            with lock:
                results.append(("error", str(exc)))

    threads = [threading.Thread(target=run, args=(i,)) for i in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=300.0)
    after = len(service.requests_to(INFERENCE_PATHS["openai"]))
    failures = [
        {"session": session_id, "rejection": e.get("rejection_reason")}
        for session_id in sessions
        for e in daemon.events(session_id)
        if e.get("event_type") == "model_routing_failed"
    ]
    _keep("money_served_race.json", {"dispatched": after - before, "results": [str(r)[:200] for r in results], "routing_failures": failures})
    assert after - before == 1, f"exactly one call may dispatch; got {after - before}"
    # The refused competitor names the exhausted grant; the winner settled exactly one liability.
    blobs = [json.dumps(daemon.events(session_id)) for session_id in sessions]
    assert any("usepod_dispatch_refused:MONEY_AUTHORITY_" in blob for blob in blobs if blob), blobs[0][:400]
    assert any(isinstance(result, tuple) and "synthetic reply" in _answer_text(result[1]) for result in results)


# --- admission refusals settle at their proven zero (the starvation repair) ---------------------------


def _envelope_grant(daemon, fingerprint: str, *, max_total: int = 90_000, per_operation: int = 15_000) -> str:
    """An account-wide provider budget -- the operator's daily-envelope shape, no operation cap."""
    from core.effect_budget_money import AssetIdentity, MoneyGrantSpec, grant_money_authority
    from core.usepod.money_law import USEPOD_ACCOUNT_NETWORK

    grant = grant_money_authority(
        daemon.operator_token(),
        MoneyGrantSpec(
            kind="provider_budget",
            operation_kinds=("inference_prepaid",),
            provider_id="usepod",
            asset=AssetIdentity(network=USEPOD_ACCOUNT_NETWORK, asset="USDC", decimals=6),
            max_total_atomic=max_total,
            per_operation_max_atomic=per_operation,
            expires_epoch=time.time() + 6 * 3600.0,
            credit_liquidity="required",
            provider_account=fingerprint,
            approval_ref="synthetic:admission-refusal-drive",
            note="synthetic test funds: admission-refusal served drive",
        ),
    )
    return grant.grant_id


def test_a_served_402_storm_settles_at_zero_and_keeps_the_envelope_usable(served) -> None:
    """The demonstrated starvation, served: a token the proxy refuses (402, no balance) used to
    hold each turn's maximum forever until the day's envelope was gone. Now every refused turn
    settles at its proven zero through the real daemon, and the same grant still serves a real
    call afterwards."""
    daemon, service = served.daemon, served.service
    grant_id = _envelope_grant(daemon, served.fingerprint, max_total=90_000, per_operation=15_000)

    # Drain the token: the strict proxy answers 402 insufficient_balance BEFORE any upstream
    # call -- the documented admission refusal this repair settles at zero.
    with service.lock:
        service.tokens[served.token] = 0
    session_id = _session(f"money-402-storm-{uuid.uuid4()}")
    storm = 8  # 8 x 15,000 = 120,000 held would exceed the 90,000 envelope
    for index in range(storm):
        _chat(daemon, f"Write note {index} for the refusal storm.", session_id)
    blob = json.dumps(daemon.events(session_id))
    _keep("money_served_402_storm.json", {"events": daemon.events(session_id)})
    assert "payment_or_balance_required" in blob, blob[:400]

    # Every refused turn's liability settled at its proven zero: THIS storm's grant holds no
    # unknown (sibling tests keep their own deliberately-held unknowns on their own grants).
    status, liabilities = daemon.call("GET", "/api/money/liabilities?state=unknown")
    assert status == 200, liabilities
    held = [row for row in liabilities["liabilities"] if row.get("grant_id") == grant_id]
    assert not held, held

    # The storm itself consumed nothing: every refusal-settled liability on this grant is closed
    # with both expense lines exact at zero. (An account-wide envelope may carry OTHER in-flight
    # work in this shared daemon -- background lanes also reserve -- so the bound here is the
    # storm's own footprint, read through the store the adopted process shares with the daemon.)
    from core.effect_budget_money import grant_headroom, liabilities as _liabilities

    rows = _liabilities(grant_id=grant_id)
    _keep("money_served_402_storm_grant_rows.json", {"rows": rows})
    refused_rows = [row for row in rows if any(item.get("evidence_kind") == "provider_refusal_record" for item in row.get("evidence") or [])]
    assert len(refused_rows) >= storm, [row.get("liability_id") for row in refused_rows]
    for row in refused_rows:
        assert str(row.get("state")) == "settled", row
        for line in row.get("lines") or []:
            assert str(line.get("line_state")) == "exact" and int(line.get("actual_atomic") or 0) == 0, line
    head = grant_headroom(grant_id)
    assert head is not None, head
    storm_would_have_held = storm * 15_000
    assert int(head["principal_left_atomic"]) > 90_000 - 2 * 15_000, head
    assert storm_would_have_held >= 90_000, "the storm must be large enough to exhaust the envelope unrepaired"

    # And the SAME grant still serves a real, settled call once the balance is back.
    with service.lock:
        service.tokens[served.token] = 80_000_000
    ok_session = _session(f"money-402-after-{uuid.uuid4()}")
    status, answer = _chat(daemon, "Write a short thank-you note after the storm.", ok_session)
    assert status == 200 and "synthetic reply" in _answer_text(answer), answer
    receipts = _usepod_receipts(daemon.events(ok_session))
    assert receipts and receipts[-1]["settlement"]["recording"] == "settled_with_evidence", receipts[-1:]
