"""Versioned platform policy facts for the Social Content Manager (P1).

Only facts supported by CURRENT official sources are encoded; every fact
carries its source URL and retrieval date. Limits that are NOT officially
published produce typed ``validation_unknown`` — never invented constants.

X is deliberately ABSENT: the X Editorial authority (core.x_platform_policy)
owns all X facts; this module delegates and never forks them.

Research ledger (retrieved 2026-09-04) is mirrored in
validation-logs/social-content-manager-p1-20260904/PLATFORM_POLICY_LEDGER.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

PLATFORM_POLICY_VERSION = "social-platform-2026-09"

VALIDATION_UNKNOWN = "validation_unknown"


@dataclass(frozen=True)
class PlatformFact:
    key: str
    value: Any                    # None means "not officially published"
    status: str                   # documented | validation_unknown | delegated
    source_url: str
    retrieved: str
    note: str = ""


_LINKEDIN_POST = "https://www.linkedin.com/help/linkedin/answer/a528176"
_BSKY_LEXICON = (
    "https://github.com/bluesky-social/atproto/blob/main/lexicons/app/bsky/feed/post.json"
)
_MASTODON_POSTING = "https://docs.joinmastodon.org/user/posting/"
_MASTODON_INSTANCE = "https://docs.joinmastodon.org/entities/Instance/"
_THREADS_HELP = "https://help.instagram.com/1217144552251333"
_TG_BOTAPI = "https://core.telegram.org/bots/api#sendmessage"
_DISCORD_DISCUSSION = "https://github.com/discord/discord-api-docs/discussions/3345"

_RETRIEVED = "2026-09-04"

PLATFORM_FACTS: dict[str, tuple[PlatformFact, ...]] = {
    "linkedin": (
        PlatformFact("post_character_limit", 3000, "documented", _LINKEDIN_POST, _RETRIEVED,
                     "official help: 'The character limit for a post is 3,000 characters.'"),
        PlatformFact("feed_fold_position", None, VALIDATION_UNKNOWN, _LINKEDIN_POST, _RETRIEVED,
                     "the ~200-char 'see more' fold is third-party lore, not officially documented"),
        PlatformFact("article_character_limit", None, VALIDATION_UNKNOWN, _LINKEDIN_POST,
                     _RETRIEVED, "no official help page states an article limit"),
    ),
    "bluesky": (
        PlatformFact("post_max_graphemes", 300, "documented", _BSKY_LEXICON, _RETRIEVED,
                     "app.bsky.feed.post lexicon: maxGraphemes=300, maxLength=3000"),
        PlatformFact("post_max_length_chars", 3000, "documented", _BSKY_LEXICON, _RETRIEVED, ""),
    ),
    "mastodon": (
        PlatformFact("default_character_limit", 500, "documented", _MASTODON_POSTING, _RETRIEVED,
                     "official docs: 'The default character limit is 500 characters.'"),
        PlatformFact("url_accounted_chars", 23, "documented", _MASTODON_POSTING, _RETRIEVED,
                     "links count as 23 characters regardless of actual length"),
        PlatformFact("instance_character_limit", None, VALIDATION_UNKNOWN, _MASTODON_INSTANCE,
                     _RETRIEVED,
                     "per-instance (instance-configurable): clients must read "
                     "configuration.statuses.max_characters from the target instance"),
    ),
    "threads": (
        PlatformFact("post_character_limit", 500, "documented", _THREADS_HELP, _RETRIEVED,
                     "official help: over 500 characters, another thread is added automatically"),
        PlatformFact("link_handling", None, VALIDATION_UNKNOWN, _THREADS_HELP, _RETRIEVED,
                     "no official page documents how links count"),
    ),
    "telegram": (
        PlatformFact("bot_sendmessage_character_limit", 4096, "documented", _TG_BOTAPI,
                     _RETRIEVED,
                     "Bot API: '1-4096 characters after entities parsing' (current through "
                     "Bot API 10.3, 2026-08-24 changelog)"),
        PlatformFact("core_app_character_limit", None, VALIDATION_UNKNOWN,
                     "https://telegram.org/faq", _RETRIEVED,
                     "the official FAQ states no message character limit; 4096 for the "
                     "core app is community knowledge"),
    ),
    "discord": (
        PlatformFact("message_character_limit_default", 2000, "documented", _DISCORD_DISCUSSION,
                     _RETRIEVED,
                     "enforced by the API for default/bot senders; not stated as a constant "
                     "in the developer docs — staff context confirms 2000 default vs 4000 Nitro"),
        PlatformFact("message_character_limit_nitro", 4000, "documented",
                     "https://support.discord.com", _RETRIEVED,
                     "Nitro perk; support pages block automated fetch, verified via search"),
    ),
    # "x" is owned by core.x_platform_policy and is intentionally absent here.
}

PLATFORMS_WITH_POLICY = tuple(sorted(PLATFORM_FACTS))


def fact(platform: str, key: str) -> PlatformFact:
    facts = PLATFORM_FACTS.get(str(platform or "").strip().lower())
    if facts is None:
        raise KeyError(f"no policy facts for platform {platform!r} (X is owned by "
                       "core.x_platform_policy)")
    for f in facts:
        if f.key == key:
            return f
    raise KeyError(f"unknown fact key {key!r} for {platform!r}")


def length_limit_with_certainty(platform: str) -> tuple[int | None, str]:
    """(limit, certainty) for the primary post/message limit of a platform.

    Mirrors core.x_platform_policy.length_limit_with_certainty for non-X
    platforms: a None limit means the runtime must NOT invent one.
    """
    p = str(platform or "").strip().lower()
    if p == "x":
        from core.x_platform_policy import length_limit_with_certainty as x_limit

        return x_limit("post")
    if p == "mastodon":
        # The DEFAULT is documented, but the per-instance limit governs the
        # actual post; without reading the instance config the honest answer
        # is unknown-with-default-note.
        return None, VALIDATION_UNKNOWN
    key_by_platform = {
        "linkedin": "post_character_limit",
        "bluesky": "post_max_graphemes",
        "threads": "post_character_limit",
        "telegram": "bot_sendmessage_character_limit",
        "discord": "message_character_limit_default",
    }
    if p not in key_by_platform:
        return None, VALIDATION_UNKNOWN
    f = fact(p, key_by_platform[p])
    if f.value is None:
        return None, VALIDATION_UNKNOWN
    return int(f.value), "documented"


def policy_ledger() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for platform, facts in sorted(PLATFORM_FACTS.items()):
        for f in facts:
            rows.append({
                "platform": platform, "key": f.key, "value": f.value,
                "status": f.status, "source_url": f.source_url,
                "retrieved": f.retrieved, "note": f.note,
                "policy_version": PLATFORM_POLICY_VERSION,
            })
    return rows
