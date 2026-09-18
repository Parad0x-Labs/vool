"""ONE money authority, and the census that proves it.

Every signing, broadcast, key-creation or private-key export for user funds traverses the
canonical lifecycle in this package. Everything that used to do those things elsewhere is a
typed refusal now: :func:`refuse_legacy` / :func:`refuse_export` file the fault (with its
security observation) and hand back the exception to raise. Two censuses keep it true:

* :func:`static_census` walks every production package for money tokens (broadcasts, signing
  calls, key construction, exports) and refuses any hit outside ``core/wallet/`` that is not on
  the reasoned allowlist of authority-free helpers;
* :func:`executable_census` CALLS every retired surface and reports any that did not refuse,
  created a key file, produced a signature or reached a network.
"""
from __future__ import annotations

import ast
import contextlib
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.wallet.errors import WalletFault
from core.wallet.security import wallet_fault

AUTHORITY = "core.wallet.authority"
LEGACY_RETIRED = "wallet_legacy_surface_retired"
EXPORT_REFUSED = "wallet_export_refused"
REPO_ROOT = Path(__file__).resolve().parents[2]
PRODUCTION_ROOTS: tuple[str, ...] = ("core", "apps", "installer", "network", "relay", "tools", "channels", "storage", "adapters")
CANONICAL_PREFIX = "core/wallet/"

#: Files outside core/wallet/ that legitimately carry a money token, each with the reason it is authority-free.
AUTHORITY_FREE_ALLOWLIST: dict[str, str] = {
    "network/signer.py": "node identity key: signs mesh/peer identity, never user funds; the wallet derives sealing secrets from it",
    "installer/gen_publisher_keypair.py": "release-publisher signing key for update manifests; never user funds",
    "installer/update_release.py": "release manifest signing with the publisher key; never user funds",
    "core/web0_work_receipt.py": "work receipts signed by an INJECTED signer; the only production supply (nullpass issue) is retired",
    "core/nullpass.py": "verifies receipt signatures; decodes public keys only",
    "core/contribution_proof.py": "hive contribution proofs signed with the node identity key; never user funds",
    "core/x402/receipt_verifier.py": "verifies payment receipts by reading the chain; signs nothing",
}

#: Every retired surface, by id. The executable census probes each; test_6 demands a fault receipt for each.
RETIRED_SURFACES: tuple[str, ...] = (
    "vool_wallet.get_or_create_wallet",
    "vool_wallet.generate_and_save",
    "vool_wallet.load",
    "vool_wallet.sign",
    "vool_wallet.export_secret_key_base58",
    "vool_wallet.reveal_wallet_secret_key_base58",
    "wallet_spend_tools.execute_spend",
    "wallet_spend_tools.register_wallet_provider",
    "tool.wallet.spend",
    "tool.pay.x402",
    "x402.client.pay",
    "x402.client.pay_requirements",
    "x402.client._load_payer_keypair",
    "x402.client.wallet_signer",
    "x402.client.build_solana_x402_payment",
    "null_dial._dial_pay_x402",
    "solana_anchor.submit_memo_anchor",
    "solana_anchor.anchor_vault_proof",
    "solana_anchor.dispatch_anchor_in_background",
    "null_register_execute.sign_and_broadcast_registration",
    "credits.proof_of_work.anchor_proof",
    "credits.zk_verifier.send_gate_tx",
    "dna_wallet_manager.configure_wallets",
    "dna_wallet_manager.deposit_hot",
    "dna_wallet_manager.top_up_hot_from_cold",
    "dna_wallet_manager.move_hot_to_cold",
    "dna_wallet_manager.consume_hot_for_credit_purchase",
    "dna_payment_bridge.purchase_credits",
    "dna_payment_bridge.purchase_credits_from_dex",
    "installer.initialize_agent_wallet",
)


def refuse_legacy(surface: str, *, source_context: dict[str, Any] | None = None, reason: str = "") -> WalletFault:
    """File the typed refusal for a retired money surface and return it to raise."""
    return wallet_fault(LEGACY_RETIRED, authority=AUTHORITY, context={"surface": str(surface), "reason": str(reason or "canonical_lifecycle_only")}, source_context=source_context)


def refuse_export(surface: str, *, source_context: dict[str, Any] | None = None) -> WalletFault:
    return wallet_fault(EXPORT_REFUSED, authority=AUTHORITY, context={"surface": str(surface), "reason": "no_export_door"}, source_context=source_context)


# --- static census ----------------------------------------------------------------------------------

_BROADCAST_STRINGS = ("sendTransaction",)
_MONEY_STRINGS = ("sendTransaction", "/settle", "SOLANA_DEPLOYER_KEYPAIR", "solana_wallet.enc")
_MONEY_CALL_NAMES = frozenset({
    "generate_and_save", "get_or_create_wallet", "export_secret_key_base58", "reveal_wallet_secret_key_base58",
    "_load_payer_keypair", "register_wallet_provider", "submit_memo_anchor", "anchor_vault_proof", "dispatch_anchor_in_background",
    "execute_spend", "sign_transaction", "from_seed", "from_seed_and_derivation_path",
    "from_seed_phrase_and_passphrase", "from_private_bytes", "anchor_proof", "wallet_signer", "build_solana_x402_payment",
})
#: `.sign(` / `.sign_message(` count as money signing only on receivers that name a funds key. The node identity
#: signer (`signer.sign(...)` from network.signer) signs peer identity, receipts and manifests, never user funds.
_FUNDS_RECEIVERS = frozenset({"wallet", "kp", "keypair", "payer", "sk", "private_key", "self", "w", "signing_wallet"})
_SIGN_NAMES = frozenset({"sign", "sign_message"})
_KEY_CONSTRUCTORS = frozenset({("Ed25519PrivateKey", "generate"), ("Keypair", "from_bytes"), ("Keypair", "from_seed"), ("Keypair", "__call__")})


@dataclass
class CensusReport:
    scanned_files: int = 0
    violations: list[str] = field(default_factory=list)
    broadcast_sites: list[str] = field(default_factory=list)
    allowlist: dict[str, str] = field(default_factory=dict)
    hits: dict[str, list[str]] = field(default_factory=dict)


def _call_name(node: ast.Call) -> tuple[str, str]:
    func = node.func
    if isinstance(func, ast.Attribute):
        receiver = func.value.id if isinstance(func.value, ast.Name) else ""
        return receiver, func.attr
    if isinstance(func, ast.Name):
        return "", func.id
    return "", ""


def _scan_file(path: Path) -> list[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value in _MONEY_STRINGS:
            found.append(f"string {node.value!r} @{node.lineno}")
        elif isinstance(node, ast.Call):
            receiver, name = _call_name(node)
            if name in _MONEY_CALL_NAMES or (name in _SIGN_NAMES and receiver in _FUNDS_RECEIVERS):
                found.append(f"call {receiver + '.' if receiver else ''}{name}( @{node.lineno}")
            elif (receiver, name) in _KEY_CONSTRUCTORS or (receiver == "Keypair" and name == "") or (isinstance(node.func, ast.Name) and node.func.id == "Keypair"):
                found.append(f"key construction {receiver}.{name} @{node.lineno}")
    return found


def static_census(root: Path | None = None) -> CensusReport:
    base = Path(root) if root else REPO_ROOT
    report = CensusReport(allowlist=dict(AUTHORITY_FREE_ALLOWLIST))
    for top in PRODUCTION_ROOTS:
        for path in sorted((base / top).rglob("*.py")):
            rel = path.relative_to(base).as_posix()
            if "/tests/" in f"/{rel}" or rel.startswith("tests/"):
                continue
            report.scanned_files += 1
            hits = _scan_file(path)
            if not hits:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if rel != "core/wallet/authority.py" and any(f'"{token}"' in text or f"'{token}'" in text for token in _BROADCAST_STRINGS) and rel not in AUTHORITY_FREE_ALLOWLIST:
                report.broadcast_sites.append(rel)
            if rel.startswith(CANONICAL_PREFIX) or rel in AUTHORITY_FREE_ALLOWLIST:
                report.hits[rel] = hits
                continue
            report.violations.extend(f"{rel}: {hit}" for hit in hits)
    report.broadcast_sites = sorted(set(report.broadcast_sites))
    return report


# --- executable census ------------------------------------------------------------------------------

@contextlib.contextmanager
def _env(**values: str):
    saved = {key: os.environ.get(key) for key in values}
    try:
        for key, value in values.items():
            os.environ[key] = value
        yield
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _call_with_dummies(fn, **explicit: Any):
    """Bind every required parameter of `fn` to a harmless dummy so a refusal, not a TypeError, is what we measure."""
    import inspect

    kwargs = dict(explicit)
    try:
        signature = inspect.signature(fn)
    except (TypeError, ValueError):
        return fn(**kwargs)
    for name, param in signature.parameters.items():
        if name in kwargs or param.default is not inspect.Parameter.empty or param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD):
            continue
        kwargs[name] = 1.0 if "usdc" in name or "amount" in name or "balance" in name else "A" * 32
    return fn(**kwargs)


def _expect_refusal(violations: list[str], surface: str, fn, *, codes: tuple[str, ...] = (LEGACY_RETIRED, EXPORT_REFUSED, "wallet_signing_unavailable", "wallet_network_disabled")) -> None:
    try:
        result = fn()
    except WalletFault as exc:
        if exc.code not in codes:
            violations.append(f"{surface}: refused with an unexpected code {exc.code}")
        elif not exc.fault_id:
            violations.append(f"{surface}: refusal carried no fault receipt")
        return
    except Exception as exc:
        violations.append(f"{surface}: raised untyped {type(exc).__name__}: {str(exc)[:80]}")
        return
    violations.append(f"{surface}: did not refuse (returned {type(result).__name__})")


def _expect_refusal_result(violations: list[str], surface: str, fn, *, status_field: str = "status") -> None:
    try:
        result = fn()
    except WalletFault as exc:
        if not exc.fault_id:
            violations.append(f"{surface}: refusal carried no fault receipt")
        return
    except Exception as exc:
        violations.append(f"{surface}: raised untyped {type(exc).__name__}: {str(exc)[:80]}")
        return
    status = result.get(status_field) if isinstance(result, dict) else getattr(result, status_field, None)
    ok = result.get("ok") if isinstance(result, dict) else getattr(result, "ok", False)
    error = result.get("error") if isinstance(result, dict) else ""
    if ok or (status not in {LEGACY_RETIRED, EXPORT_REFUSED} and error not in {LEGACY_RETIRED, EXPORT_REFUSED}):
        violations.append(f"{surface}: did not refuse (status={status!r}, ok={ok!r}, error={error!r})")


def executable_census(*, home: Path) -> list[str]:
    """Probe every retired surface; return the surfaces that did NOT refuse. Touches no network."""
    from core.wallet import config

    home = Path(home)
    violations: list[str] = []
    destination = "11111111111111111111111111111111"

    # 1. the legacy key authority
    from core import vool_wallet

    _expect_refusal(violations, "vool_wallet.get_or_create_wallet", lambda: vool_wallet.get_or_create_wallet(runtime_home=home))
    legacy = vool_wallet.VoolWallet(runtime_home=home)
    _expect_refusal(violations, "vool_wallet.generate_and_save", legacy.generate_and_save)
    _expect_refusal(violations, "vool_wallet.load", legacy.load)
    _expect_refusal(violations, "vool_wallet.sign", lambda: legacy.sign(b"probe"))
    _expect_refusal(violations, "vool_wallet.export_secret_key_base58", legacy.export_secret_key_base58, codes=(EXPORT_REFUSED,))
    _expect_refusal(violations, "vool_wallet.reveal_wallet_secret_key_base58", lambda: vool_wallet.reveal_wallet_secret_key_base58(runtime_home=home), codes=(EXPORT_REFUSED,))
    if list(home.rglob("solana_wallet.enc")):
        violations.append("vool_wallet.get_or_create_wallet: a legacy key file exists under the probe home")

    # 2. the spend tool and its provider seam
    from core import wallet_spend_tools

    with _env(VOOL_ENABLE_WALLET_SPEND="1"):
        _expect_refusal_result(violations, "wallet_spend_tools.execute_spend", lambda: wallet_spend_tools.execute_spend(to=destination, amount_lamports=1))
    _expect_refusal(violations, "wallet_spend_tools.register_wallet_provider", lambda: wallet_spend_tools.register_wallet_provider(lambda: object()))
    from core import runtime_execution_tools

    with _env(VOOL_ENABLE_WALLET_SPEND="1"):
        _expect_refusal_result(violations, "tool.wallet.spend", lambda: runtime_execution_tools._wallet_spend({"to": destination, "amount_lamports": 1, "asset": "SOL", "allow_spend": True, "approve": True}))
    from core.execution import payment_tools

    with _env(VOOL_ENABLE_X402_SPEND="1"):
        _expect_refusal_result(violations, "tool.pay.x402", lambda: payment_tools.execute_payment_tool("pay.x402", {"resource_url": "https://example.test/paid", "allow_spend": True, "max_spend_usdc": 0.5}, source_context={"vool_wallet": object(), "owner_local": True}))

    # 3. the x402 client's live paths and its key loader
    from core.x402 import client as x402_client

    for mode in (x402_client.X402Mode.DEVNET, x402_client.X402Mode.MAINNET):
        cfg = x402_client.X402Config(mode=mode, max_fee_usdc=1.0, keypair_path=str(home / "kp.json"))
        _expect_refusal(violations, "x402.client.pay", lambda cfg=cfg: x402_client.X402Client(cfg).pay(0.001, destination))
        _expect_refusal(violations, "x402.client.pay_requirements", lambda cfg=cfg: x402_client.X402Client(cfg).pay_requirements({"network": "solana-devnet", "maxAmountRequired": "1", "payTo": destination}))
        _expect_refusal(violations, "x402.client._load_payer_keypair", lambda cfg=cfg: x402_client.X402Client(cfg)._load_payer_keypair())
    _expect_refusal(violations, "x402.client.wallet_signer", lambda: x402_client.wallet_signer(object()))
    _expect_refusal(violations, "x402.client.build_solana_x402_payment", lambda: x402_client.build_solana_x402_payment(object(), {}, "https://api.devnet.solana.com", decimals=6))
    from core import null_dial

    _expect_refusal_result(violations, "null_dial._dial_pay_x402", lambda: null_dial._dial_pay_x402("https://example.test/paid", object(), allow_spend=True, owner_local=True, requirements={"network": "solana-devnet", "maxAmountRequired": "1", "payTo": destination}), status_field="error")

    # 4. the background anchor and the .null registration tail
    from core import solana_anchor

    with _env(VOOL_ANCHOR_RECEIPTS="1"):
        _expect_refusal(violations, "solana_anchor.submit_memo_anchor", lambda: solana_anchor.submit_memo_anchor("ab" * 32))
        _expect_refusal(violations, "solana_anchor.anchor_vault_proof", lambda: solana_anchor.anchor_vault_proof("task", "ab" * 32, 1.0))
        _expect_refusal(violations, "solana_anchor.dispatch_anchor_in_background", lambda: solana_anchor.dispatch_anchor_in_background("task", "ab" * 32, 1.0))
    from core import null_register_execute

    _expect_refusal(violations, "null_register_execute.sign_and_broadcast_registration", lambda: null_register_execute.sign_and_broadcast_registration(None, None))

    # 5. the credit-side broadcasters and the simulated custody surfaces
    from core.credits import proof_of_work, zk_verifier

    with _env(SOLANA_DEPLOYER_KEYPAIR="1" * 88):
        _expect_refusal(violations, "credits.proof_of_work.anchor_proof", lambda: proof_of_work.ProofOfWorkMinter().anchor_proof(None, "https://api.devnet.solana.com"))
        _expect_refusal(violations, "credits.zk_verifier.send_gate_tx", lambda: zk_verifier.send_gate_tx(b"", "https://api.devnet.solana.com"))
    from core import dna_payment_bridge, dna_wallet_manager

    _expect_refusal(violations, "dna_wallet_manager.configure_wallets", lambda: _call_with_dummies(dna_wallet_manager.DNAWalletManager().configure_wallets))
    _expect_refusal(violations, "dna_wallet_manager.deposit_hot", lambda: _call_with_dummies(dna_wallet_manager.DNAWalletManager().deposit_hot))
    _expect_refusal(violations, "dna_wallet_manager.top_up_hot_from_cold", lambda: _call_with_dummies(dna_wallet_manager.DNAWalletManager().top_up_hot_from_cold))
    _expect_refusal(violations, "dna_wallet_manager.move_hot_to_cold", lambda: _call_with_dummies(dna_wallet_manager.DNAWalletManager().move_hot_to_cold))
    _expect_refusal(violations, "dna_wallet_manager.consume_hot_for_credit_purchase", lambda: _call_with_dummies(dna_wallet_manager.DNAWalletManager().consume_hot_for_credit_purchase))
    _expect_refusal(violations, "dna_payment_bridge.purchase_credits", lambda: _call_with_dummies(dna_payment_bridge.DNAPaymentBridge().purchase_credits))
    _expect_refusal(violations, "dna_payment_bridge.purchase_credits_from_dex", lambda: _call_with_dummies(dna_payment_bridge.DNAPaymentBridge().purchase_credits_from_dex))
    from installer import initialize_agent_wallet

    _expect_refusal(violations, "installer.initialize_agent_wallet", lambda: initialize_agent_wallet.initialize_agent_wallet(str(home)))
    if list(home.rglob("solana_wallet.enc")):
        violations.append("installer.initialize_agent_wallet: a legacy key file exists under the probe home")

    # 6. no endpoint serves another row: an undeclared mainnet alias resolves to nothing, and the Solana Devnet
    #    broadcast client refuses both the excluded mainnet endpoint and the endpoint the Solana mainnet row declares
    from core.wallet import chains, lifecycle

    if config.network_allowed("solana-mainnet") or config.network_allowed("mainnet-beta"):
        violations.append("mainnet: the network policy resolves an undeclared mainnet alias")
    for mainnet_url in ("https://api.mainnet-beta.solana.com", chains.resolve_network(chains.SOLANA_MAINNET).rpc_origins[0]):
        try:
            lifecycle.RpcClient(mainnet_url, network=config.NETWORK_SOLANA_DEVNET)
            violations.append("mainnet: the Solana Devnet broadcast client accepted a mainnet endpoint")
        except WalletFault:
            pass
    return violations


__all__ = ["AUTHORITY_FREE_ALLOWLIST", "EXPORT_REFUSED", "LEGACY_RETIRED", "RETIRED_SURFACES", "CensusReport", "executable_census", "refuse_export", "refuse_legacy", "static_census"]
