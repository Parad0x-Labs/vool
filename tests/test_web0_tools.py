from __future__ import annotations

import base64
import json
import urllib.parse
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import core.runtime_execution_tools as rt
from core.runtime_execution_tools import execute_runtime_tool
from core.vool_wallet import VoolWallet, b58encode, verify_wallet_signature
from core.wallet.errors import WalletFault
from core.web0_gated_html import make_gate_challenge
from core.web0_tools import (
    _GLOBAL_GATE_STORE,
    _active_projects,
    dna_pay_and_unlock,
    web0_add_block,
    web0_add_gated_section,
    web0_compile_preview,
    web0_create_project,
    web0_encrypt_whole_site,
    web0_gate_key_store,
    web0_open_builder_draft,
    web0_project,
    web0_publish,
)


def _offline(*_args, **_kwargs) -> dict:
    return {"error": True, "message": "offline"}


def _reset_projects() -> None:
    _active_projects.clear()
    _GLOBAL_GATE_STORE._entries.clear()


class _GateSigner:
    """An in-test Ed25519 key standing in for a visitor's browser wallet (Phantom-style).

    The runtime's own signing wallet is retired, so gate challenges are signed here, by a key the
    test owns, and verified through the runtime's public-key check exactly as a real visitor's
    signature would be. `pubkey` is a base58 Solana-format public key.
    """

    def __init__(self) -> None:
        self._key = Ed25519PrivateKey.generate()
        self.pubkey = b58encode(self._key.public_key().public_bytes_raw())

    def sign_message(self, message: bytes | str) -> bytes:
        payload = message.encode("utf-8") if isinstance(message, str) else bytes(message)
        return self._key.sign(payload)


def _wallet(tmp_path: Path) -> _GateSigner:
    signer = _GateSigner()
    assert verify_wallet_signature(wallet_pubkey=signer.pubkey, message="probe", signature=signer.sign_message("probe"))
    assert not list((tmp_path).rglob("solana_wallet.enc"))
    return signer


def test_web0_gated_section_registers_the_real_encryption_key_and_preview_hides_plaintext(tmp_path: Path) -> None:
    _reset_projects()
    wallet = _wallet(tmp_path)
    created = web0_create_project(
        "landing_page",
        "loop.null",
        "Loop",
        http=lambda *args, **kwargs: {"error": True, "message": "offline"},
    )

    added = web0_add_gated_section(
        created["project_id"],
        "home",
        "founder-only memo",
        [wallet.pubkey],
    )
    project = web0_project(created["project_id"])
    assert project is not None
    project_key = project.key_store.get_aes_key(added["block_id"], wallet.pubkey)
    global_key = web0_gate_key_store().get_aes_key(added["block_id"], wallet.pubkey)
    preview = web0_compile_preview(
        created["project_id"],
        http=lambda *args, **kwargs: {"error": True, "message": "offline"},
    )

    assert added["status"] == "encrypted"
    assert project_key is not None
    assert project_key == global_key
    assert "founder-only memo" not in preview["html"]
    assert "data-block-id" in preview["html"]


def test_web0_global_gate_key_unlocks_tool_created_section(tmp_path: Path) -> None:
    _reset_projects()
    wallet = _wallet(tmp_path)
    created = web0_create_project(
        "landing_page",
        "loop.null",
        "Loop",
        http=lambda *args, **kwargs: {"error": True, "message": "offline"},
    )
    added = web0_add_gated_section(created["project_id"], "home", "private", [wallet.pubkey])

    challenge = make_gate_challenge(added["block_id"], wallet.pubkey)
    body = {
        "block_id": added["block_id"],
        "wallet_pubkey": wallet.pubkey,
        "nonce": challenge,
        "signature": base64.b64encode(wallet.sign_message(challenge)).decode("ascii"),
    }
    from core.web0_gated_html import VoolGateHandler

    response = VoolGateHandler(web0_gate_key_store()).handle(body)

    assert "aes_key" in response


def test_retired_runtime_wallet_cannot_sign_a_gate_challenge(tmp_path: Path) -> None:
    # The runtime's legacy signing wallet is retired: it refuses typed and produces no signature, so a
    # gated section can only ever be unlocked by a key the visitor holds, never by this process.
    _reset_projects()
    retired = VoolWallet(runtime_home=tmp_path / "runtime")
    created = web0_create_project("landing_page", "loop.null", "Loop", http=_offline)
    visitor = _wallet(tmp_path)
    added = web0_add_gated_section(created["project_id"], "home", "private", [visitor.pubkey])
    challenge = make_gate_challenge(added["block_id"], visitor.pubkey)
    with pytest.raises(WalletFault) as exc:
        retired.sign_message(challenge)
    assert exc.value.code == "wallet_legacy_surface_retired" and exc.value.fault_id.startswith("fault-")
    assert not list((tmp_path / "runtime").rglob("solana_wallet.enc"))
    # and the key store still only answers to the visitor's key
    assert web0_gate_key_store().get_aes_key(added["block_id"], visitor.pubkey) is not None


def test_publish_and_spend_fail_closed_without_explicit_permission(tmp_path: Path) -> None:
    _reset_projects()
    wallet = _wallet(tmp_path)
    created = web0_create_project(
        "landing_page",
        "loop.null",
        "Loop",
        http=lambda *args, **kwargs: {"error": True, "message": "offline"},
    )

    publish = web0_publish(created["project_id"], wallet)
    spend = dna_pay_and_unlock("https://example.test/resource", wallet)

    assert publish["error"] == "publish_requires_explicit_allow_network_publish"
    assert spend["error"] == "spend_requires_explicit_allow_spend"


def test_web0_open_builder_draft_returns_payload_url_for_local_portal() -> None:
    draft = web0_open_builder_draft(
        "Nully Web0",
        "<style>body{background:#020812;color:#56e7ff}</style><main>Nully owns this page.</main>",
        domain="nully",
        base_url="http://127.0.0.1:3000",
        updated_at="2026-06-17T12:00:00Z",
    )

    parsed = urllib.parse.urlparse(draft["builder_url"])
    query = urllib.parse.parse_qs(parsed.query)
    payload = query["payload"][0]
    padded = payload + "=" * (-len(payload) % 4)
    project = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))

    assert draft["status"] == "builder_url_ready"
    assert parsed.scheme == "http"
    assert parsed.netloc == "127.0.0.1:3000"
    assert parsed.path == "/templates/editor/"
    assert query["t"] == ["code"]
    assert query["name"] == ["nully"]
    assert project["v"] == 1
    assert project["template"] == "code"
    assert project["domain"] == "nully"
    assert project["content"]["title"] == "Nully Web0"
    assert "Nully owns this page." in project["content"]["code"]


# ---------------------------------------------------------------------------
# encrypt_whole_site: real per-block encryption, not a marker
# ---------------------------------------------------------------------------

def test_encrypt_whole_site_protects_every_text_block_and_hides_plaintext(tmp_path: Path) -> None:
    _reset_projects()
    wallet = _wallet(tmp_path)
    created = web0_create_project("landing_page", "loop.null", "Loop", http=_offline)
    pid = created["project_id"]
    web0_add_block(pid, "home", {"kind": "heading", "text": "TOP SECRET HEADLINE"})
    web0_add_block(pid, "home", {"kind": "text", "content": "the secret body text"})
    web0_add_block(pid, "home", {"kind": "divider"})  # no recoverable text -> skipped

    out = web0_encrypt_whole_site(pid, [wallet.pubkey])

    assert out["status"] == "encrypted"
    assert out["blocks_protected"] == 2  # heading + text, not the divider
    assert any(str(s.get("reason", "")).startswith("no_gateable_text") for s in out["skipped"])

    preview = web0_compile_preview(pid, http=_offline)
    assert "TOP SECRET HEADLINE" not in preview["html"]
    assert "the secret body text" not in preview["html"]

    project = web0_project(pid)
    assert project is not None
    other = _GateSigner()
    for blk in out["protected"]:
        # the whitelisted wallet holds a key; a non-whitelisted wallet gets nothing
        assert project.key_store.get_aes_key(blk["block_id"], wallet.pubkey) is not None
        assert project.key_store.get_aes_key(blk["block_id"], other.pubkey) is None


def test_encrypt_whole_site_rejects_a_whitelist_with_no_valid_wallet(tmp_path: Path) -> None:
    _reset_projects()
    created = web0_create_project("landing_page", "loop.null", "Loop", http=_offline)
    pid = created["project_id"]
    web0_add_block(pid, "home", {"kind": "text", "content": "secret"})

    out = web0_encrypt_whole_site(pid, ["not-a-real-wallet"])

    assert out["error"] == "whitelist_requires_valid_wallet"
    # nothing got encrypted: the text block is still plaintext in the preview
    preview = web0_compile_preview(pid, http=_offline)
    assert "secret" in preview["html"]


# ---------------------------------------------------------------------------
# the builder intents route through the runtime dispatch
# ---------------------------------------------------------------------------

def test_web0_builder_intents_route_through_dispatch(tmp_path: Path, monkeypatch) -> None:
    _reset_projects()
    monkeypatch.setattr(rt, "web0_create_project", lambda tpl, dom, name="": web0_create_project(tpl, dom, name, http=_offline))
    monkeypatch.setattr(rt, "web0_compile_preview", lambda pid: web0_compile_preview(pid, http=_offline))

    created = execute_runtime_tool(
        "web0.create_project",
        {"template_id": "landing_page", "domain": "loop.null", "project_name": "Loop"},
    )
    assert created.ok and created.status == "executed"
    pid = str(created.details["project_id"])

    added = execute_runtime_tool(
        "web0.add_block",
        {"project_id": pid, "page_id": "home", "block": {"kind": "heading", "text": "Welcome"}},
    )
    assert added.ok and int(added.details["block_count"]) >= 1

    preview = execute_runtime_tool("web0.compile_preview", {"project_id": pid})
    assert preview.ok and "html" in preview.details
    assert preview.details["observation"]["intent"] == "web0.compile_preview"


def test_web0_add_gated_section_intent_round_trips(tmp_path: Path) -> None:
    _reset_projects()
    wallet = _wallet(tmp_path)
    created = web0_create_project("landing_page", "loop.null", "Loop", http=_offline)
    pid = created["project_id"]

    res = execute_runtime_tool(
        "web0.add_gated_section",
        {"project_id": pid, "page_id": "home", "content": "founder-only memo", "whitelist": [wallet.pubkey]},
    )

    assert res.ok and res.status == "executed"
    block_id = str(res.details["block_id"])
    project = web0_project(pid)
    assert project is not None
    assert project.key_store.get_aes_key(block_id, wallet.pubkey) is not None
    assert "founder-only memo" not in web0_compile_preview(pid, http=_offline)["html"]


def test_web0_encrypt_whole_site_intent_protects_blocks(tmp_path: Path) -> None:
    _reset_projects()
    wallet = _wallet(tmp_path)
    created = web0_create_project("landing_page", "loop.null", "Loop", http=_offline)
    pid = created["project_id"]
    web0_add_block(pid, "home", {"kind": "text", "content": "classified body"})

    res = execute_runtime_tool("web0.encrypt_whole_site", {"project_id": pid, "whitelist": [wallet.pubkey]})

    assert res.ok and res.status == "executed"
    assert int(res.details["blocks_protected"]) == 1
    assert "classified body" not in web0_compile_preview(pid, http=_offline)["html"]


def test_web0_publish_intent_is_gated_off_by_default(tmp_path: Path) -> None:
    _reset_projects()
    created = web0_create_project("landing_page", "loop.null", "Loop", http=_offline)
    pid = created["project_id"]

    # No opt-in and no wallet: the autonomous layer refuses to publish.
    res = execute_runtime_tool("web0.publish", {"project_id": pid})
    assert not res.ok and res.status == "requires_opt_in"
    assert res.details["status"] == "publish_gated_off"


def test_web0_publish_intent_refuses_optin_without_a_wired_wallet(tmp_path: Path) -> None:
    _reset_projects()
    created = web0_create_project("landing_page", "loop.null", "Loop", http=_offline)
    pid = created["project_id"]

    # Even with the opt-in present in source_context, no wallet means no publish.
    res = execute_runtime_tool(
        "web0.publish",
        {"project_id": pid},
        source_context={"allow_network_publish": True},
    )
    assert not res.ok and res.status == "requires_opt_in"
    assert res.details["wallet_present"] is False


def test_web0_publish_ignores_model_supplied_optin(tmp_path: Path) -> None:
    _reset_projects()
    wallet = _wallet(tmp_path)
    created = web0_create_project("landing_page", "loop.null", "Loop", http=_offline)
    pid = created["project_id"]

    # A wallet is wired, but the opt-in lives only in model arguments — which are
    # NOT trusted. The model cannot flip its own publish gate: still refused.
    res = execute_runtime_tool(
        "web0.publish",
        {"project_id": pid, "allow_network_publish": True},
        source_context={"vool_wallet": wallet},  # opt-in NOT in trusted context
    )
    assert not res.ok and res.status == "requires_opt_in"
    assert res.details["allow_network_publish"] is False  # the model-supplied flag was ignored
