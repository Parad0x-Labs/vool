"""Publishing boundary (Checkpoint 6) — zero publishing authority.

Audit result (this lane, base 2661343b):

- X, LinkedIn, Bluesky, Mastodon, Threads: NO canonical connector exists
  (relay.channel_outbound.TOPIC_BY_PLATFORM covers only discord/telegram).
  The manager returns typed ``publishing_unavailable`` with copy-ready bytes.
- Telegram/Discord: a canonical outbound path exists, BUT the production
  caller's direct-fallback (core.channel_actions._try_direct_delivery, reached
  from the turn fast path in core.agent_runtime.turn_dispatch before any
  permission/effect/approval check) bypasses the canonical authority chain
  whenever the relay mirror is merely unreachable. Until that seam is repaired
  by its owning lane, this manager cannot prove "explicit operator intent +
  final-byte approval + network-publication permission + effect budget +
  receipt + no remote-channel escalation" end to end, so it returns typed
  ``handoff_refused`` with copy-ready bytes. This is a RELEASE-CRITICAL
  HANDOFF recorded in the evidence bundle, not a silent capability claim.
- No content scheduler exists: scheduled calendar times are local planning
  data; external scheduling is typed ``scheduling_unavailable``.
"""

from __future__ import annotations

from typing import Any

PUBLISHING_UNAVAILABLE = "publishing_unavailable"
SCHEDULING_UNAVAILABLE = "scheduling_unavailable"
HANDOFF_REFUSED = "handoff_refused"
NO_CONNECTOR_PLATFORMS = ("x", "linkedin", "bluesky", "mastodon", "threads")
HANDOFF_CANDIDATES = ("telegram", "discord")

# The direct-fallback audit note rides every telegram/discord refusal so the
# operator sees the real reason, not a vague "unsupported".
DIRECT_FALLBACK_AUDIT = (
    "the canonical outbound path's direct-fallback (channel_actions."
    "_try_direct_delivery) is not approval/effect gated at the production "
    "caller; handoff stays closed until that seam is repaired by its owning lane"
)


def handoff(
    store, *, draft_id: str, version: int | None = None,
    destination_platform: str, destination: str = "",
) -> dict[str, Any]:
    """Typed handoff outcome for one draft version. Never a success minted by
    the caller: outcomes are computed here from store truth."""
    platform = str(destination_platform or "").strip().lower()
    draft = store.get_draft(draft_id, version=version)
    if draft is None:
        return {"outcome": "unknown_draft", "ok": False,
                "summary": f"no draft {draft_id!r}"}
    identity = {
        "draft_id": draft["draft_id"], "version": draft["version"],
        "content_hash": draft["content_hash"], "platform": draft["platform"],
    }
    covered = store.approval_for(**identity)
    if platform in NO_CONNECTOR_PLATFORMS:
        return {
            "outcome": PUBLISHING_UNAVAILABLE,
            "ok": False,
            "summary": (
                f"no canonical publishing connector exists for {platform}; "
                "nothing was sent and no network call was made"
            ),
            "copy_ready_bytes": draft["body_bytes"],
            "content_hash": draft["content_hash"],
            "receipt_state": PUBLISHING_UNAVAILABLE,
            "approved": covered is not None,
        }
    if platform in HANDOFF_CANDIDATES:
        if covered is None:
            return {
                "outcome": HANDOFF_REFUSED,
                "ok": False,
                "summary": (
                    f"no exact-byte approval covers {draft['draft_id']} "
                    f"v{draft['version']} for handoff; approval is required before "
                    "any external dispatch"
                ),
                "receipt_state": "handoff_refused",
            }
        return {
            "outcome": HANDOFF_REFUSED,
            "ok": False,
            "summary": (
                f"approval is on file, but the {platform} handoff is closed: "
                f"{DIRECT_FALLBACK_AUDIT}"
            ),
            "copy_ready_bytes": draft["body_bytes"],
            "content_hash": draft["content_hash"],
            "receipt_state": "handoff_refused",
            "release_critical_handoff": True,
        }
    return {
        "outcome": PUBLISHING_UNAVAILABLE, "ok": False,
        "summary": f"unknown destination platform {platform!r}",
        "receipt_state": PUBLISHING_UNAVAILABLE,
    }


def schedule(store, *, draft_id: str, when: str) -> dict[str, Any]:
    """A calendar time is local planning data. External scheduling is typed
    unavailable: there is no canonical content scheduler to carry an approved
    content identity, and this lane will not create a second one."""
    return {
        "outcome": SCHEDULING_UNAVAILABLE,
        "ok": False,
        "summary": (
            "no canonical content scheduler exists; the calendar entry is local "
            f"planning data only (draft {draft_id}, requested {when}); no external "
            "action was scheduled"
        ),
        "receipt_state": SCHEDULING_UNAVAILABLE,
    }


def publishing_tools_available() -> list[str]:
    """The honest connector inventory: empty for this lane by design."""
    return []


# Outbound tool names the manager must NEVER contribute to an offer. Used by
# tests that plant fake connectors and prove they cannot be smuggled in.
_FORBIDDEN_OFFER_TOKENS = (
    "channel.post", "telegram.send", "discord.send", "social.publish",
    "outbound.post", "publish.post",
)


def offer_is_safe(tool_names: tuple[str, ...]) -> tuple[bool, str]:
    for name in tool_names:
        low = str(name).lower()
        for token in _FORBIDDEN_OFFER_TOKENS:
            if token in low:
                return False, f"tool {name!r} looks like a publishing connector smuggled into the offer"
    return True, ""
