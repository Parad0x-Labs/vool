"""Image + video generation via a configured provider, off by default and metered by the usage quota.

Needs `image.api.<account>` / `video.api.<account>` = {"endpoint","api_key","provider"?,"params"?} in
the encrypted credential store. `provider` selects a shape adapter (generic/openai/runpod/replicate/
fal, or any registered via core.media_providers.register_media_provider); `params` are provider-
specific extra inputs (e.g. a Replicate model `version`, size, fps). Each generation is checked against
the per-tier quota (`image.generate` / `video.generate`) BEFORE the paid call and counted only on
success, so a free-tier user can't run up the operator's GPU bill. Nothing fires unless the operator
both configures a service and enables the tool. Fails closed (structured result, never a raw exception).
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import re
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from core import credential_store, usage_quota
from core.media_providers import extract_media_ref, get_media_provider
from core.provider_invocation_gateway import seal_direct_provider_invocation

_MAX_RESPONSE_BYTES = 2_000_000  # cap the response read so a misbehaving endpoint can't exhaust memory

Opener = Callable[..., Any]


@dataclass
class MediaResult:
    ok: bool
    status: str
    message: str
    ref: str = ""
    provider: str = ""


@dataclass
class ImageResult:
    ok: bool
    status: str
    message: str
    image_ref: str = ""


def _service(kind: str, account: str) -> dict | None:
    raw = credential_store.get_credential(f"{kind}.api.{str(account or 'default').strip()}")
    if not raw:
        return None
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _generate(
    *, kind: str, quota_key: str, prompt: str, account: str, tier: str | None,
    opener: Opener | None = None, timeout: float = 120.0,
) -> MediaResult:
    """Shared generate path for image/video. Never raises; returns a structured MediaResult."""
    # F-05 (independent-proof repair): when no explicit opener is injected,
    # the request goes through THE one outbound door
    # (core.remote_fetch_policy.open_remote) so the per-turn veto applies and
    # the fetch is reported into the owning turn's ledger. A model-authored
    # prompt must never reach a raw socket that bypasses policy.
    injected_opener = opener
    cfg = _service(kind, account)
    if cfg is None:
        return MediaResult(
            False, "needs_credentials",
            f"No {kind} service configured for account '{account}' "
            f"({kind}.api.<account> with endpoint + api_key [+ provider]).",
        )
    endpoint = str(cfg.get("endpoint") or "")
    api_key = str(cfg.get("api_key") or "")
    provider = get_media_provider(cfg.get("provider") or "generic")
    extra = cfg.get("params") if isinstance(cfg.get("params"), dict) else {}
    if not endpoint:
        return MediaResult(False, "needs_credentials", f"{kind.capitalize()} service endpoint is not configured.",
                           provider=provider.name)
    if not str(prompt or "").strip():
        return MediaResult(False, "invalid_prompt", f"No {kind} prompt was provided.", provider=provider.name)

    tier = tier or usage_quota.active_tier()
    check = usage_quota.check_quota(quota_key, tier=tier)
    if not check.allowed:
        return MediaResult(
            False, "quota_exceeded",
            f"{kind.capitalize()} generation limit reached for the '{tier}' tier "
            f"({check.used}/{check.limit} today).",
            provider=provider.name,
        )

    try:
        body = provider.body(str(prompt), extra)
        headers = provider.headers(api_key)
        permit = seal_direct_provider_invocation(
            provider_id=f"media:{provider.name}",
            model_id=str(extra.get("model") or extra.get("version") or kind),
            operation=f"{kind}_generation",
            payload=body,
            request_id=(
                f"media-{kind}-"
                + hashlib.sha256(str(prompt).encode()).hexdigest()[:24]
            ),
            header_names=tuple(headers),
        )
        req = urllib.request.Request(
            endpoint,
            data=json.dumps(permit.consume()).encode("utf-8"),
            method="POST",
            headers=headers,
        )
        if injected_opener is not None:
            resp_ctx = injected_opener(req, timeout=timeout)
        else:
            from core.remote_fetch_policy import open_remote

            resp_ctx = open_remote(req, timeout=timeout)
        with resp_ctx as resp:
            payload = json.loads(resp.read(_MAX_RESPONSE_BYTES).decode("utf-8"))
    except Exception as exc:  # network / auth / service — never surface a raw traceback
        return MediaResult(False, "generate_failed",
                           f"{kind.capitalize()} generation failed ({type(exc).__name__}): {exc}",
                           provider=provider.name)

    ref = extract_media_ref(payload)
    # The paid generation already succeeded; a local usage-file write failure (disk full / read-only)
    # must not crash the caller or lose the ref -- undercounting one unit beats raising after a paid call.
    with contextlib.suppress(Exception):
        usage_quota.consume_quota(quota_key, tier=tier)
    return MediaResult(True, "executed", f"{kind.capitalize()} generated.", ref=ref, provider=provider.name)


def generate_image(*, prompt: str, account: str = "default", tier: str | None = None,
                   opener: Opener | None = None) -> ImageResult:
    """Generate one image for `prompt` via the configured provider. Metered against image.generate."""
    r = _generate(kind="image", quota_key="image.generate", prompt=prompt, account=account, tier=tier,
                  opener=opener, timeout=60.0)
    return ImageResult(r.ok, r.status, r.message, image_ref=r.ref)


def generate_video(*, prompt: str, account: str = "default", tier: str | None = None,
                   opener: Opener | None = None) -> MediaResult:
    """Generate one video for `prompt` (or a per-shot scene prompt) via the configured provider.
    Metered against the paid-only video.generate quota. Async providers may return a poll URL as the
    ref for the caller to follow."""
    return _generate(kind="video", quota_key="video.generate", prompt=prompt, account=account, tier=tier,
                     opener=opener, timeout=300.0)


# --- Provider key acceptance (BYOK) ------------------------------------------------------------
# fal.ai's fast, cheap text-to-image default. `image key <key> <model>` overrides the model, and any
# fal model id maps to https://fal.run/<model>.
_FAL_DEFAULT_MODEL = "fal-ai/flux/schnell"
_FAL_ENDPOINT_BASE = "https://fal.run/"
# fal keys are "<key_id>:<key_secret>" — a hex/uuid-ish id, a colon, then a long secret. This shape is
# distinct from the LLM BYOK keys (sk-.../gsk_.../AIza...) so a pasted fal key is unambiguous.
_FAL_KEY_RE = re.compile(r"^[0-9a-fA-F][0-9a-fA-F-]{7,}:[0-9A-Za-z_-]{16,}$")


def _fal_endpoint(model: str = "") -> str:
    slug = (str(model or "").strip() or _FAL_DEFAULT_MODEL).strip().strip("/")
    return _FAL_ENDPOINT_BASE + slug


def detect_fal_key(key: str) -> bool:
    """True when the string looks like a fal.ai key ('<id>:<secret>')."""
    return bool(_FAL_KEY_RE.match(str(key or "").strip()))


def image_service_slot(account: str = "default") -> str:
    return f"image.api.{str(account or 'default').strip() or 'default'}"


def has_image_service(account: str = "default") -> bool:
    """Whether an image-generation service credential is configured for this account."""
    try:
        return bool(credential_store.has_credential(image_service_slot(account)))
    except Exception:
        return False


def configure_image_service(
    *, api_key: str, provider: str = "fal", model: str = "", endpoint: str = "", account: str = "default",
    label: str = "",
) -> tuple[bool, str]:
    """Seal an ``image.api.<account>`` media credential (endpoint + api_key + provider) in the encrypted
    store. For fal the endpoint defaults to the FLUX schnell model. Returns (ok, last4-of-key)."""
    key = str(api_key or "").strip()
    if not key:
        return False, ""
    prov = (str(provider or "fal").strip().lower()) or "fal"
    url = str(endpoint or "").strip() or (_fal_endpoint(model) if prov == "fal" else "")
    if not url:
        return False, ""
    blob = json.dumps({"endpoint": url, "api_key": key, "provider": prov})
    try:
        credential_store.store_credential(image_service_slot(account), blob, label=label or f"{prov} image")
    except Exception:
        return False, ""
    return True, key[-4:]


def forget_image_service(account: str = "default") -> bool:
    """Delete the image service credential for this account. Returns True if one was removed."""
    try:
        return bool(credential_store.delete_credential(image_service_slot(account)))
    except Exception:
        return False


__all__ = [
    "ImageResult",
    "MediaResult",
    "configure_image_service",
    "detect_fal_key",
    "forget_image_service",
    "generate_image",
    "generate_video",
    "has_image_service",
    "image_service_slot",
]
