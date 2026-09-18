from __future__ import annotations

import base64
import html
import json
import os
import secrets
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from core.vool_wallet import VoolWallet
from core.remote_fetch_policy import RemoteFetchRefusedError, open_remote
from core.web0_gated_html import (
    DEFAULT_GATE_URL,
    WalletKeyStore,
    _split_whitelist,
    encrypt_content_block,
    render_gated_block_css,
    render_gated_block_html,
)

NULL_PORTAL_URL = os.environ.get("NULL_PORTAL_URL", "http://localhost:3000").rstrip("/")
# No default endpoint: a Parad0x server must never sit in the custody/signing/control path by
# default. The blind-sign pay path (dna_pay_and_unlock) is retired below; the read-only
# quote/builder helpers require DNA_X402_URL to be set explicitly or they fail closed.
DNA_X402_URL = os.environ.get("DNA_X402_URL", "").rstrip("/")
VOOL_GATE_URL = os.environ.get("VOOL_GATE_URL", DEFAULT_GATE_URL).rstrip("/")
_TEMPLATE_FALLBACKS = (
    {"id": "landing_page", "name": "Landing Page", "description": "Hero, sections, links"},
    {"id": "token_launch", "name": "Token Launch", "description": "Token info, links, socials"},
    {"id": "personal_site", "name": "Personal Site", "description": "Bio, work, contact"},
    {"id": "storefront", "name": "Storefront", "description": "Products or services"},
)
_ALLOWED_BLOCK_KINDS = {"text", "heading", "image", "video", "divider", "quote", "callout", "_gated_html"}
# Block kinds that carry recoverable visible text and so can be encrypted behind a gate.
_GATEABLE_TEXT_KINDS = ("heading", "text", "quote", "callout")
_GLOBAL_GATE_STORE = WalletKeyStore()


def _http(
    method: str,
    url: str,
    *,
    body: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 15.0,
) -> dict[str, Any]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request_headers = {"Accept": "application/json"}
    if body is not None:
        request_headers["Content-Type"] = "application/json"
    if headers:
        request_headers.update(headers)
    request = urllib.request.Request(url, data=data, headers=request_headers, method=method.upper())
    try:
        # THE SHARED DOOR, not a private socket: the veto is enforced before any I/O and the
        # call is reported into the owning turn's ledger like every other outbound fetch.
        with open_remote(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
        return json.loads(raw) if raw.strip() else {}
    except RemoteFetchRefusedError as exc:
        return {"error": True, "refused": True, "message": str(exc)}
    except urllib.error.HTTPError as exc:
        body_text = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(body_text)
        except json.JSONDecodeError:
            payload = {"message": body_text}
        return {"error": True, "status": exc.code, **dict(payload)}
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        return {"error": True, "message": str(exc)}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _escape(value: Any) -> str:
    return html.escape(str(value or ""), quote=True)


def _local_project_id() -> str:
    return f"web0_{secrets.token_hex(8)}"


def _base64url_json(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _insert_block(blocks: list[dict[str, Any]], block: dict[str, Any], position: int) -> None:
    if position < 0 or position >= len(blocks):
        blocks.append(block)
        return
    blocks.insert(position, block)


def _safe_block(block: dict[str, Any]) -> dict[str, Any]:
    kind = _text(block.get("kind"))
    if kind not in _ALLOWED_BLOCK_KINDS:
        raise ValueError(f"Unsupported Web0 block kind: {kind!r}")
    return dict(block)


@dataclass
class Web0Project:
    project_id: str
    template_id: str
    domain: str
    project_name: str
    slots: dict[str, Any] = field(default_factory=dict)
    pages: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    background_fx: str = "none"
    accent: str = "#00C2A8"
    gated_blocks: list[dict[str, Any]] = field(default_factory=list)
    key_store: WalletKeyStore = field(default_factory=WalletKeyStore)


_active_projects: dict[str, Web0Project] = {}


def web0_gate_key_store() -> WalletKeyStore:
    return _GLOBAL_GATE_STORE


def web0_list_templates(http: Callable[..., dict[str, Any]] = _http) -> list[dict[str, Any]]:
    result = http("GET", f"{NULL_PORTAL_URL}/api/templates")
    if isinstance(result, dict) and not result.get("error") and isinstance(result.get("templates"), list):
        return list(result["templates"])
    return [dict(item) for item in _TEMPLATE_FALLBACKS]


def web0_create_project(
    template_id: str,
    domain: str,
    project_name: str = "",
    *,
    http: Callable[..., dict[str, Any]] = _http,
) -> dict[str, Any]:
    normalized_template = _text(template_id)
    normalized_domain = _text(domain)
    if not normalized_template:
        return {"error": "template_id_required"}
    if not normalized_domain:
        return {"error": "domain_required"}
    response = http(
        "POST",
        f"{NULL_PORTAL_URL}/api/projects",
        body={
            "templateId": normalized_template,
            "domain": normalized_domain,
            "name": _text(project_name) or f"{normalized_domain} site",
        },
    )
    project_id = _text(response.get("projectId") if isinstance(response, dict) else "") or _local_project_id()
    project = Web0Project(
        project_id=project_id,
        template_id=normalized_template,
        domain=normalized_domain,
        project_name=_text(project_name) or f"{normalized_domain} site",
    )
    _active_projects[project_id] = project
    return {
        "project_id": project_id,
        "template_id": normalized_template,
        "domain": normalized_domain,
        "status": "created",
        "source": "null_portal" if isinstance(response, dict) and not response.get("error") else "local_draft",
    }


def web0_fill_slots(
    project_id: str,
    slots: dict[str, Any],
    *,
    http: Callable[..., dict[str, Any]] = _http,
) -> dict[str, Any]:
    project = _active_projects.get(_text(project_id))
    if project is None:
        return {"error": "project_not_found", "project_id": project_id}
    if not isinstance(slots, dict):
        return {"error": "slots_must_be_object"}
    project.slots.update(dict(slots))
    http("PATCH", f"{NULL_PORTAL_URL}/api/projects/{urllib.parse.quote(project.project_id)}/slots", body={"slots": slots})
    return {"project_id": project.project_id, "updated_slots": sorted(str(key) for key in slots), "status": "ok"}


def web0_add_block(
    project_id: str,
    page_id: str,
    block: dict[str, Any],
    position: int = -1,
) -> dict[str, Any]:
    project = _active_projects.get(_text(project_id))
    if project is None:
        return {"error": "project_not_found", "project_id": project_id}
    try:
        safe = _safe_block(block)
    except ValueError as exc:
        return {"error": str(exc)}
    page_blocks = project.pages.setdefault(_text(page_id) or "home", [])
    _insert_block(page_blocks, safe, int(position))
    return {
        "project_id": project.project_id,
        "page_id": _text(page_id) or "home",
        "block_count": len(page_blocks),
        "status": "ok",
    }


def web0_add_gated_section(
    project_id: str,
    page_id: str,
    content: str,
    whitelist: list[str],
    mode: str = "whitelist",
    label: str = "Private content",
    position: int = -1,
) -> dict[str, Any]:
    project = _active_projects.get(_text(project_id))
    if project is None:
        return {"error": "project_not_found", "project_id": project_id}
    try:
        encrypted = encrypt_content_block(
            str(content),
            whitelist,
            mode=mode,
            gate_url=VOOL_GATE_URL,
            label=label,
        )
    except ValueError as exc:
        return {"error": str(exc)}
    project.key_store.register_encrypted_block(encrypted)
    _GLOBAL_GATE_STORE.register_encrypted_block(encrypted)
    html_block = render_gated_block_html(encrypted.block)
    page_blocks = project.pages.setdefault(_text(page_id) or "home", [])
    _insert_block(
        page_blocks,
        {
            "kind": "_gated_html",
            "block_id": encrypted.block.block_id,
            "html": html_block,
            "mode": encrypted.block.mode,
            "wallet_count": len(encrypted.block.allowed_wallets),
        },
        int(position),
    )
    project.gated_blocks.append(
        {
            "block_id": encrypted.block.block_id,
            "page_id": _text(page_id) or "home",
            "mode": encrypted.block.mode,
            "wallet_count": len(encrypted.block.allowed_wallets),
        }
    )
    return {
        "project_id": project.project_id,
        "block_id": encrypted.block.block_id,
        "wallets_registered": len(encrypted.block.allowed_wallets),
        "pending_null_resolve": list(encrypted.block.pending_null_names),
        "invalid_whitelist_entries": list(encrypted.block.invalid_whitelist_entries),
        "status": "encrypted",
    }


def web0_encrypt_whole_site(
    project_id: str,
    whitelist: list[str],
    mode: str = "whitelist",
    label: str = "Private content",
) -> dict[str, Any]:
    """Encrypt every text-bearing block on every page behind the same wallet gate.

    Each gateable block (heading/text/quote/callout) is replaced in place with a
    ``_gated_html`` block whose payload carries only AES-GCM ciphertext — the
    plaintext is no longer present in the project state or its compiled HTML and
    is recoverable only with the per-block key held in the project key store.
    Already-gated blocks and blocks with no recoverable text (image/video/
    divider) are skipped and reported.
    """
    project = _active_projects.get(_text(project_id))
    if project is None:
        return {"error": "project_not_found", "project_id": project_id}

    wallets, pending_names, invalid_entries = _split_whitelist(list(whitelist))
    if not wallets:
        return {
            "error": "whitelist_requires_valid_wallet",
            "pending_null_resolve": list(pending_names),
            "invalid_whitelist_entries": list(invalid_entries),
        }

    protected: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for page_id, blocks in project.pages.items():
        for idx, block in enumerate(blocks):
            kind = _text(block.get("kind"))
            if kind == "_gated_html":
                skipped.append({"page_id": page_id, "reason": "already_gated", "block_id": block.get("block_id")})
                continue
            if kind not in _GATEABLE_TEXT_KINDS:
                skipped.append({"page_id": page_id, "reason": f"no_gateable_text:{kind or 'unknown'}"})
                continue
            text_value = _text(block.get("text") or block.get("content"))
            if not text_value:
                skipped.append({"page_id": page_id, "reason": "empty_text"})
                continue
            try:
                encrypted = encrypt_content_block(
                    text_value, list(whitelist), mode=mode, gate_url=VOOL_GATE_URL, label=label
                )
            except ValueError as exc:
                return {"error": str(exc)}
            project.key_store.register_encrypted_block(encrypted)
            _GLOBAL_GATE_STORE.register_encrypted_block(encrypted)
            blocks[idx] = {
                "kind": "_gated_html",
                "block_id": encrypted.block.block_id,
                "html": render_gated_block_html(encrypted.block),
                "mode": encrypted.block.mode,
                "wallet_count": len(encrypted.block.allowed_wallets),
            }
            project.gated_blocks.append(
                {
                    "block_id": encrypted.block.block_id,
                    "page_id": page_id,
                    "mode": encrypted.block.mode,
                    "wallet_count": len(encrypted.block.allowed_wallets),
                }
            )
            protected.append({"page_id": page_id, "block_id": encrypted.block.block_id, "prev_kind": kind})

    project.slots["_whole_site_gate"] = {
        "wallets": len(wallets),
        "mode": _text(mode) or "whitelist",
        "blocks_protected": len(protected),
    }
    return {
        "project_id": project.project_id,
        "blocks_protected": len(protected),
        "protected": protected,
        "skipped": skipped,
        "wallets_registered": len(wallets),
        "pending_null_resolve": list(pending_names),
        "invalid_whitelist_entries": list(invalid_entries),
        "status": "encrypted" if protected else "nothing_to_encrypt",
    }


def web0_set_background_fx(project_id: str, effect: str, accent: str = "#00C2A8") -> dict[str, Any]:
    project = _active_projects.get(_text(project_id))
    if project is None:
        return {"error": "project_not_found", "project_id": project_id}
    normalized_effect = _text(effect) or "none"
    if normalized_effect not in {"aurora", "plasma", "starfield", "network", "none"}:
        return {"error": "unsupported_background_fx", "effect": normalized_effect}
    project.background_fx = normalized_effect
    project.accent = _text(accent) or "#00C2A8"
    return {"project_id": project.project_id, "effect": project.background_fx, "accent": project.accent, "status": "ok"}


def web0_open_builder_draft(
    title: str,
    code: str,
    *,
    domain: str = "",
    base_url: str = NULL_PORTAL_URL,
    updated_at: str | None = None,
) -> dict[str, Any]:
    clean_title = _text(title) or "VOOL-built Web0 draft"
    clean_domain = _text(domain)
    html_code = str(code or "").strip()
    if not html_code:
        html_code = (
            "<main><h1>VOOL-built Web0 draft</h1>"
            "<p>Local builder draft. Edit, preview, then publish only when ready.</p></main>"
        )
    project = {
        "v": 1,
        "template": "code",
        "domain": clean_domain or None,
        "updatedAt": _text(updated_at) or _utc_now_iso(),
        "content": {
            "title": clean_title,
            "code": html_code,
        },
    }
    encoded = _base64url_json(project)
    params = {"t": "code", "payload": encoded}
    if clean_domain:
        params["name"] = clean_domain
    builder_url = f"{_text(base_url) or NULL_PORTAL_URL}/templates/editor/?{urllib.parse.urlencode(params)}"
    return {
        "status": "builder_url_ready",
        "template": "code",
        "title": clean_title,
        "domain": clean_domain,
        "builder_url": builder_url,
        "payload": encoded,
        "project": project,
    }


def _fallback_preview_html(project: Web0Project) -> str:
    title = _escape(project.slots.get("title") or project.project_name or project.domain)
    tagline = _escape(project.slots.get("tagline") or project.slots.get("description") or "")
    sections = [f"<h1>{title}</h1>"]
    if tagline:
        sections.append(f"<p class=\"tagline\">{tagline}</p>")
    for key, value in sorted(project.slots.items()):
        if str(key).startswith("_") or key in {"title", "tagline", "description"}:
            continue
        sections.append(f"<section><h2>{_escape(key)}</h2><p>{_escape(value)}</p></section>")
    for page_id, blocks in sorted(project.pages.items()):
        sections.append(f"<main data-page=\"{_escape(page_id)}\">")
        for block in blocks:
            kind = _text(block.get("kind"))
            if kind == "_gated_html":
                sections.append(str(block.get("html") or ""))
            elif kind == "heading":
                sections.append(f"<h2>{_escape(block.get('text') or block.get('content'))}</h2>")
            elif kind in {"text", "quote", "callout"}:
                sections.append(f"<p>{_escape(block.get('text') or block.get('content'))}</p>")
            elif kind == "divider":
                sections.append("<hr>")
            elif kind == "image":
                src = _escape(block.get("src") or "")
                alt = _escape(block.get("alt") or "")
                if src.startswith(("https://", "http://", "data:image/")):
                    sections.append(f"<img src=\"{src}\" alt=\"{alt}\">")
        sections.append("</main>")
    css = render_gated_block_css()
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
:root {{ color-scheme: dark; --accent: {_escape(project.accent)}; }}
body {{ margin: 0; min-height: 100vh; font-family: ui-sans-serif, system-ui; background: radial-gradient(circle at top, rgba(0,194,168,0.22), #020812 42%, #02040a); color: #f3fffb; padding: 3rem; }}
main, section {{ max-width: 920px; }}
h1 {{ font-size: clamp(2.4rem, 7vw, 6rem); line-height: 0.9; margin: 0 0 1rem; }}
.tagline {{ color: rgba(243,255,251,0.74); font-size: 1.2rem; }}
img {{ max-width: 100%; border-radius: 14px; }}
{css}
</style>
</head>
<body>
{''.join(sections)}
</body>
</html>"""


def web0_compile_preview(
    project_id: str,
    *,
    http: Callable[..., dict[str, Any]] = _http,
) -> dict[str, Any]:
    project = _active_projects.get(_text(project_id))
    if project is None:
        return {"error": "project_not_found", "project_id": project_id}
    payload = {
        "projectId": project.project_id,
        "templateId": project.template_id,
        "domain": project.domain,
        "slots": project.slots,
        "pages": project.pages,
        "backgroundFx": project.background_fx,
        "accent": project.accent,
    }
    result = http("POST", f"{NULL_PORTAL_URL}/api/compile", body=payload, timeout=30.0)
    if isinstance(result, dict) and not result.get("error") and isinstance(result.get("html"), str):
        html_text = str(result.get("html") or "")
        return {
            "project_id": project.project_id,
            "html": html_text,
            "size_kb": round(len(html_text.encode("utf-8")) / 1024, 2),
            "ar_cost_usdc": float(result.get("arCost") or 0.0),
            "status": "preview_ready",
            "source": "null_portal",
        }
    html_text = _fallback_preview_html(project)
    return {
        "project_id": project.project_id,
        "html": html_text,
        "size_kb": round(len(html_text.encode("utf-8")) / 1024, 2),
        "ar_cost_usdc": 0.0,
        "status": "preview_ready",
        "source": "local_fallback",
    }


def web0_publish(
    project_id: str,
    wallet: VoolWallet,
    *,
    allow_network_publish: bool = False,
    http: Callable[..., dict[str, Any]] = _http,
) -> dict[str, Any]:
    project = _active_projects.get(_text(project_id))
    if project is None:
        return {"error": "project_not_found", "project_id": project_id}
    if not allow_network_publish:
        return {
            "error": "publish_requires_explicit_allow_network_publish",
            "reason": "Publishing uploads content and may require signed network transactions.",
        }
    preview = web0_compile_preview(project.project_id, http=http)
    if preview.get("error"):
        return preview
    upload = http(
        "POST",
        f"{NULL_PORTAL_URL}/api/publish/arweave",
        body={"html": preview.get("html", ""), "domain": project.domain, "walletPubkey": wallet.pubkey},
        timeout=60.0,
    )
    if upload.get("error"):
        return {"error": "arweave_upload_failed", "detail": upload}
    txid = _text(upload.get("arweaveTxId") or upload.get("txid"))
    return {
        "project_id": project.project_id,
        "domain": project.domain,
        "arweave_txid": txid,
        "permanent_url": f"https://arweave.net/{txid}" if txid else "",
        "wallet_pubkey": wallet.pubkey,
        "status": "published" if txid else "uploaded_without_txid",
    }


def dna_get_quote(
    resource_url: str,
    privacy_path: str = "normal",
    *,
    http: Callable[..., dict[str, Any]] = _http,
) -> dict[str, Any]:
    if not DNA_X402_URL:
        return {"error": "x402_endpoint_not_configured", "reason": "Set DNA_X402_URL to a quote endpoint."}
    params = {"resource": resource_url}
    if privacy_path == "dark-null":
        params["privacyPath"] = "dark-null"
    return http("GET", f"{DNA_X402_URL}/quote?{urllib.parse.urlencode(params)}")


def dna_pay_and_unlock(
    resource_url: str,
    wallet: VoolWallet,
    *,
    max_spend_usdc: float = 1.0,
    privacy_path: str = "normal",
    allow_spend: bool = False,
    http: Callable[..., dict[str, Any]] = _http,
) -> dict[str, Any]:
    if not allow_spend:
        return {
            "error": "spend_requires_explicit_allow_spend",
            "reason": "Payment tools must not spend USDC without an explicit caller opt-in.",
        }
    # Retired: this path had the server (a remote /commit endpoint that defaulted to a Parad0x
    # host) BUILD the transaction, which this wallet then signed blindly — the signed bytes were
    # never checked to transfer the expected mint/amount to the expected recipient. That is both
    # a §3 violation (a third party in the signing/control path) and blind-signing. USDC x402
    # payments go through the canonical engine instead: core.x402.client.X402Client builds a
    # TransferChecked and PARTIAL-signs it locally (see the .null dial lane), so this node never
    # signs a transaction it did not construct. This helper now refuses rather than blind-sign.
    return {
        "error": "blind_sign_path_retired",
        "reason": (
            "The server-built-transaction x402 pay path is disabled: it signed a transaction "
            "this node did not construct. Payments go through the canonical wallet only: fetch "
            "the resource via POST /api/wallet/x402/fetch or propose it with wallet.propose, "
            "then approve it in the Wallet section."
        ),
    }


def dna_create_builder_draft(
    prompt: str,
    *,
    http: Callable[..., dict[str, Any]] = _http,
) -> dict[str, Any]:
    if not DNA_X402_URL:
        return {"error": "x402_endpoint_not_configured", "reason": "Set DNA_X402_URL to a builder endpoint."}
    return http("POST", f"{DNA_X402_URL}/v1/agent-builder/draft", body={"prompt": str(prompt or "")})


def dna_get_receipt(
    receipt_id: str,
    *,
    http: Callable[..., dict[str, Any]] = _http,
) -> dict[str, Any]:
    return http("GET", f"{DNA_X402_URL}/receipt/{urllib.parse.quote(_text(receipt_id))}")


def web0_project(project_id: str) -> Web0Project | None:
    return _active_projects.get(_text(project_id))


__all__ = [
    "Web0Project",
    "dna_create_builder_draft",
    "dna_get_quote",
    "dna_get_receipt",
    "dna_pay_and_unlock",
    "web0_add_block",
    "web0_add_gated_section",
    "web0_compile_preview",
    "web0_create_project",
    "web0_encrypt_whole_site",
    "web0_fill_slots",
    "web0_gate_key_store",
    "web0_list_templates",
    "web0_open_builder_draft",
    "web0_project",
    "web0_publish",
    "web0_set_background_fx",
]
