"""Product follow-up, Phase A: what a payment preview says, from typed facts, with readable amounts.

IN-PROCESS against the wallet authorities (a loopback scripted chain answers the quote's reads). The numeric producer
is `amounts.format_minor` over integer minor units: the long decimal tails a Base fee shows are base-unit precision
(wei), not floating-point contamination -- proven here by round-tripping every quote figure through the integer it
came from. The shortened forms come from one decimal owner: estimates marked, maximums rounded up, minimums rounded
down, dust never zero, the requested amount exact. The purpose comes from the operation owner's records, never from
the memo's wording.
"""
from __future__ import annotations

import json
import uuid
from decimal import Decimal

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core.wallet import amounts, proposals, purpose, quotes, transfers
from core.wallet.store import connection
from tests.wallet._rig import DEVNET_GENESIS, ScriptedRpc
from tests.wallet._rig_evm_native import ScriptedEvmNativeChain

pytestmark = [pytest.mark.safety]

SOLANA_DEVNET = "solana:EtWTRABZaYq6iMfeYKouRu166VU2xqa1"
BASE_SEPOLIA = "eip155:84532"
PIN = "482913"


@pytest.fixture
def wallet_home(monkeypatch, tmp_path):
    """The quote corpus's home: crypto on, no inherited row endpoints, an isolated Blackbox store, cold chain identity."""
    monkeypatch.setenv("VOOL_WALLET_ENABLED", "1")
    for name in ("VOOL_WALLET_NETWORK_ENVIRONMENT", "VOOL_WALLET_RPC_URLS", "VOOL_WALLET_TESTNET_RPC_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("VOOL_WALLET_X402_ALLOW_LOOPBACK", "1")
    monkeypatch.setenv("VOOL_BLACKBOX_DIR", str(tmp_path / "blackbox"))
    from core.blackbox import store as store_module
    from core.wallet import chains

    store_module.reset_default_store()
    chains.invalidate_chain_identity()
    yield tmp_path
    chains.invalidate_chain_identity()
    store_module.reset_default_store()


def _sol_key() -> str:
    from core.vool_wallet import b58encode

    return b58encode(Ed25519PrivateKey.generate().public_key().public_bytes_raw())


# --- the shortening owner ------------------------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("minor", "decimals", "mode", "short"),
    [
        (92_594_227_130_007, 18, amounts.ROUND_UP, "0.0000926"),        # the screenshot's fee ceiling, rounded outward
        (92_594_227_130_007, 18, amounts.ROUND_DOWN, "0.0000925"),
        (61_021_210_000_007, 18, amounts.ROUND_NEAREST, "0.000061"),    # the charged Jovian fee, 3 significant digits
        (4_999_595_000, 9, amounts.ROUND_DOWN, "4.99"),                  # a guaranteed minimum never overstated
        (4_999_595_000, 9, amounts.ROUND_UP, "5"),
        (1, 18, amounts.ROUND_NEAREST, "0.000000000000000001"),          # dust: exact, never zero
        (7, 18, amounts.ROUND_UP, "0.000000000000000007"),
        (5_000, 9, amounts.ROUND_NEAREST, "0.000005"),                   # already short: unchanged
        (1_000_000_000_000_000, 18, amounts.ROUND_NEAREST, "0.001"),     # the requested amount stays exact
        (999_999_999_999_999_999, 18, amounts.ROUND_UP, "1"),
        (123_456_789_012_345_678, 18, amounts.ROUND_DOWN, "0.123"),
    ],
)
def test_shortened_forms_round_in_the_safe_direction_and_never_zero(minor: int, decimals: int, mode: str, short: str) -> None:
    got = amounts.shorten_minor(minor, decimals, mode=mode)
    assert got == short
    exact = amounts.decimal_of(minor, decimals)
    if mode == amounts.ROUND_UP:
        assert Decimal(got) >= exact
    elif mode == amounts.ROUND_DOWN:
        assert Decimal(got) <= exact
    assert Decimal(got) > 0


def test_display_lines_carry_their_marker_and_exact_stays_exact() -> None:
    assert amounts.display_amount(61_021_210_000_007, 18, "ETH") == "≈0.000061 ETH"
    assert amounts.display_amount(92_594_227_130_007, 18, "ETH", mode=amounts.ROUND_UP) == "at most 0.0000926 ETH"
    assert amounts.display_amount(4_999_595_000, 9, "SOL", mode=amounts.ROUND_DOWN) == "at least 4.99 SOL"
    assert amounts.display_amount(1_000_000_000_000_000, 18, "ETH", exact=True) == "0.001 ETH"
    assert amounts.display_amount(5_000, 9, "SOL") == "0.000005 SOL"  # nothing to shorten: no marker


# --- the typed purpose ---------------------------------------------------------------------------------------------

def _proposal(origin: str, memo: str, **over):
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    base = dict(proposal_id=f"pay-{uuid.uuid4().hex[:20]}", wallet_id="wallet-test", network=BASE_SEPOLIA, asset="ETH", amount_minor=1_000_000_000_000_000,
                destination="0x8a4af57c4a4c4d978b58db77a8fcf724e3faeb1a", memo=memo, origin=origin, idempotency_key="", state="pending_approval", created_at=now, updated_at=now)
    base.update(over)
    return proposals.TransactionProposal(**base)


def test_direct_transfer_purpose_never_reads_the_memo() -> None:
    view = purpose.purpose_for(_proposal(proposals.ORIGIN_USER, "x402 https://evil.example/pay-me"))
    assert view["kind"] == purpose.KIND_DIRECT and view["mechanism_label"] == "Direct transfer"
    assert view["headline"] == "Send 0.001 ETH to 0x8a4af57c4a4c4d978b58db77a8fcf724e3faeb1a"
    assert view["provider"] == "" and view["resource"] == "" and "evil" not in json.dumps(view)


@pytest.mark.parametrize("saved", [True, False])
def test_compact_review_names_only_a_bound_saved_recipient(saved) -> None:
    from core.wallet import payment_review

    proposal = _proposal(proposals.ORIGIN_USER, "Pay an unrelated name")
    fields = {"recipient_saved": saved, "recipient_label": "Zoë Ng · work",
              "to_address": proposal.destination, "asset": "ETH", "amount_human": "0.001"}
    review = payment_review.compose(fields, proposal)
    assert ("Zoë Ng · work" in review["recipient_line"]) is saved
    assert review["recipient_kind"] == ("saved_contact" if saved else "unidentified_wallet")
    assert payment_review.short_address(proposal.destination) in review["recipient_line"]
    assert "unrelated name" not in review["recipient_line"]


def test_unknown_origin_stays_visibly_unknown() -> None:
    view = purpose.purpose_for(_proposal("mystery", "pay for the thing"))
    assert view["kind"] == purpose.KIND_UNKNOWN and view["mechanism_label"] == "Purpose unknown"
    assert "purpose unknown" in view["headline"] and "mystery" in view["note"]


def test_x402_purpose_comes_from_the_binding_record_with_attributed_provider_text(wallet_home) -> None:
    from core.wallet import x402
    from core.wallet.store import utcnow

    pid = f"pay-{uuid.uuid4().hex[:20]}"
    now = utcnow()
    with connection() as conn:
        conn.execute(
            "INSERT INTO wallet_x402_bindings (binding_id, request_digest, url, method, pay_to, amount_minor, asset, network, proposal_id, tx_signature, state,"
            " resource_status, resource_digest, resource_bytes, created_at, updated_at, version, offer_json, resource_origin, resource_method)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '', 'proposed', 0, '', 0, ?, ?, 2, ?, ?, ?)",
            (f"x402b-{uuid.uuid4().hex[:16]}", uuid.uuid4().hex, "https://api.example.test/v1/summaries/42", "GET", "0x8a4af57c4a4c4d978b58db77a8fcf724e3faeb1a",
             10_000, "USDC", BASE_SEPOLIA, pid, now, now, json.dumps({"description": "Premium summary of document 42"}), "https://api.example.test", "GET"),
        )
    assert x402.binding_for_proposal(pid) is not None
    view = purpose.purpose_for(_proposal(proposals.ORIGIN_X402, "x402v2 https://api.example.test/v1/summaries/42", proposal_id=pid, asset="USDC", amount_minor=10_000))
    assert view["kind"] == purpose.KIND_SERVICE and view["mechanism_label"] == "Service payment · x402 v2"
    assert view["headline"] == "Pay api.example.test for GET https://api.example.test/v1/summaries/42"
    assert view["provider"] == "api.example.test" and "not a verified merchant identity" in view["provider_source"]
    assert view["description"] == "Premium summary of document 42" and "untrusted" in view["description_source"]
    assert view["charge_scope"] == purpose.CHARGE_ONE_RESPONSE and view["beneficiary"] == "0x8a4af57c4a4c4d978b58db77a8fcf724e3faeb1a"


def test_x402_purpose_without_a_binding_says_the_request_is_not_on_record() -> None:
    view = purpose.purpose_for(_proposal(proposals.ORIGIN_X402, "x402 https://api.example.test/paid"))
    assert view["kind"] == purpose.KIND_SERVICE and "not on record" in view["headline"] and view["resource"] == ""
    assert "api.example.test" not in json.dumps(view)  # the memo's URL is not consulted


def test_usepod_purpose_is_prepaid_credit_from_the_operation_record(wallet_home) -> None:
    from core.wallet import usepod
    from core.wallet.store import utcnow

    pid = f"pay-{uuid.uuid4().hex[:20]}"
    with connection() as conn:
        conn.execute(
            "INSERT INTO wallet_usepod_operations (operation_key, provider, correlation_id, authority, network, asset, pay_to, amount_minor, expires_at, resource,"
            " requirement_digest, proposal_id, wallet_id, state, mint_token, mint_lease_until, detail, created_at, updated_at, digest_version)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 2)",
            ("usepod.example|corr-1", "usepod.example", "corr-1", "core.wallet.usepod", BASE_SEPOLIA, "ETH", "0x8a4af57c4a4c4d978b58db77a8fcf724e3faeb1a", 1_000_000_000_000_000,
             4_102_444_800.0, "https://usepod.example/models/summarize", "d" * 64, pid, "wallet-test", "proposed", "", 0, "{}", utcnow(), utcnow()),
        )
    assert usepod.operation_for_proposal(pid) is not None
    view = purpose.purpose_for(_proposal(proposals.ORIGIN_USEPOD, "UsePod top-up https://usepod.example/models/summarize", proposal_id=pid))
    assert view["kind"] == purpose.KIND_CREDIT and view["mechanism_label"] == purpose.MECHANISM_CREDIT
    assert view["headline"] == "Prepay usepod.example credit for https://usepod.example/models/summarize"
    assert view["charge_scope"] == purpose.CHARGE_PREPAID_CREDIT and "not service delivered" in view["note"]


# --- the quote carries purpose and shortened forms; every figure round-trips its integer ---------------------------

def _ready_wallet(network: str, label: str) -> dict:
    from core.wallet import pilot_custody

    view = pilot_custody.create_pilot_wallet(network=network, method="pin", credential=PIN, credential_confirmation=PIN, creation_key=f"review-{uuid.uuid4().hex}", label=label)
    revealed = pilot_custody.reveal_pilot_backup(view["wallet_id"], credential=PIN)
    pilot_custody.acknowledge_pilot_backup(view["wallet_id"], ack_token=revealed["ack_token"])
    return pilot_custody.setup_view(view["wallet_id"])


def test_base_quote_figures_are_exact_integers_shown_short_with_exact_details(wallet_home, monkeypatch) -> None:
    """The original Base Sepolia review: operator and L1 fee parts produce long decimal tails; each tail is the exact
    integer's decimal (no float anywhere), the top-level forms are short and safely rounded, the purpose is direct."""
    with ScriptedEvmNativeChain(chain_id=84532, fee_model="op_stack", l1_fee=40_000_000_000_000, fork="jovian", operator_fee_scalar=100, operator_fee=7) as node:
        monkeypatch.setenv("VOOL_WALLET_RPC_URLS", json.dumps({BASE_SEPOLIA: node.url}))
        monkeypatch.setenv("VOOL_WALLET_X402_ALLOW_LOOPBACK", "1")
        from core.wallet import chains, environment

        environment.set_active_environment("testnet")
        chains.invalidate_chain_identity()
        wallet = _ready_wallet(BASE_SEPOLIA, "base review")
        node.fund(wallet["address"], 10**18)
        proposal = proposals.propose_transaction(wallet_id=wallet["wallet_id"], destination="0x8a4af57c4a4c4d978b58db77a8fcf724e3faeb1a", amount_minor=1_000_000_000_000_000,
                                                 asset="ETH", origin=proposals.ORIGIN_USER, memo="x402 https://not-a-service.example", network=BASE_SEPOLIA)
        from core.wallet import lifecycle

        lifecycle.default_lifecycle().prepare(proposal.proposal_id)
        quote = quotes.mint_quote(proposal.proposal_id)
        f = quote["fields"]
        for name in ("balance", "fee_estimate", "fee_max", "max_total", "estimated_after", "minimum_after"):
            assert amounts.format_minor(int(f[f"{name}_minor"]), 18) == f[f"{name}_human"]  # exact decimal of the integer
            assert Decimal(f[f"{name}_human"]) == amounts.decimal_of(int(f[f"{name}_minor"]), 18)
        assert f["amount_display"] == "0.001 ETH" and f["amount_human"] == "0.001"
        assert f["fee_max_display"].startswith("at most ") and Decimal(f["fee_max_display"].split()[2]) >= Decimal(f["fee_max_human"])
        assert f["minimum_after_display"].startswith("at least ") and Decimal(f["minimum_after_display"].split()[2]) <= Decimal(f["minimum_after_human"])
        assert f["fee_estimate_display"].startswith(amounts.APPROX_MARK) or f["fee_estimate_display"] == f["fee_estimate_human"] + " ETH"
        assert f["fee_asset_differs"] is False
        assert f["purpose"]["kind"] == purpose.KIND_DIRECT and f["purpose"]["mechanism_label"] == "Direct transfer"
        assert "not-a-service" not in json.dumps(f["purpose"])
        # the digest binds the fields the sheet shows, purpose and short forms included
        assert quotes.digest_of(f) == quote["digest"]
        record = quotes.require_open_quote(quote["quote_id"], proposal=proposal, quote_digest=quote["digest"], account_address=wallet["address"])
        assert record["fields"]["purpose"]["headline"] == f["purpose"]["headline"]


def test_solana_quote_carries_purpose_and_short_forms(wallet_home, monkeypatch) -> None:
    with ScriptedRpc(genesis_hash=DEVNET_GENESIS) as node:
        monkeypatch.setenv("VOOL_WALLET_RPC_URLS", json.dumps({SOLANA_DEVNET: node.url}))
        monkeypatch.setenv("VOOL_WALLET_X402_ALLOW_LOOPBACK", "1")
        from core.wallet import chains, environment

        environment.set_active_environment("testnet")
        chains.invalidate_chain_identity()
        wallet = _ready_wallet(SOLANA_DEVNET, "solana review")
        to = _sol_key()
        proposal = proposals.propose_transaction(wallet_id=wallet["wallet_id"], destination=to, amount_minor=400_000, asset="SOL", origin=proposals.ORIGIN_MODEL, memo="for the review", network=SOLANA_DEVNET)
        from core.wallet import lifecycle

        lifecycle.default_lifecycle().prepare(proposal.proposal_id)
        f = quotes.mint_quote(proposal.proposal_id)["fields"]
        assert f["purpose"]["headline"] == f"Send 0.0004 SOL to {to}" and f["purpose"]["kind"] == purpose.KIND_DIRECT
        assert f["amount_display"] == "0.0004 SOL" and f["fee_estimate_display"] == "0.000005 SOL" and f["fee_max_display"] == "0.000005 SOL"
        assert f["minimum_after_display"] == "at least 4.99 SOL" and f["balance_display"] == "5 SOL"


def test_transfer_view_keeps_purpose_and_short_fee_forms(wallet_home) -> None:
    from core.wallet.store import utcnow

    pid = f"pay-{uuid.uuid4().hex[:20]}"
    now = utcnow()
    with connection() as conn:
        conn.execute(
            "INSERT INTO wallet_proposals (proposal_id, wallet_id, network, asset, amount_minor, destination, memo, origin, idempotency_key, content_digest, state, created_at, updated_at)"
            " VALUES (?, 'wallet-test', ?, 'ETH', 1000000000000000, '0x8a4af57c4a4c4d978b58db77a8fcf724e3faeb1a', '', 'user', '', 'x', 'approved', ?, ?)", (pid, BASE_SEPOLIA, now, now))
        conn.execute(
            "INSERT INTO wallet_transfers (proposal_id, wallet_id, network, environment, family, from_address, to_address, asset, amount_minor, fee_max_minor,"
            " quote_id, quote_digest, challenge_digest, owner_token, lease_until, dispatch_deadline, epoch_freeze, epoch_enabled, epoch_environment, enabled_generation,"
            " state, created_at, updated_at, charged_fee_minor, fee_state)"
            " VALUES (?, 'wallet-test', ?, 'testnet', 'evm', '0xF0D1Ebc864dFCA5B06fC0973A984ED357AaBd8C2', '0x8a4af57c4a4c4d978b58db77a8fcf724e3faeb1a', 'ETH', 1000000000000000, 92594227130007,"
            " ?, 'q', 'c', 't', 0, 0, 0, 0, 0, 0, 'confirmed', ?, ?, 61021210000007, 'exact')",
            (pid, BASE_SEPOLIA, f"quote-{uuid.uuid4().hex[:20]}", now, now))
    view = transfers.latest_receipt(pid)
    assert view["purpose"]["kind"] == purpose.KIND_DIRECT and view["purpose"]["headline"].startswith("Send 0.001 ETH to 0x8a4a")
    assert view["fee_max_display"] == "at most 0.0000926 ETH" and view["charged_fee_display"] == "≈0.000061 ETH"
    assert view["charged_fee_human"] == "0.000061021210000007" and view["fee_max_human"] == "0.000092594227130007"
