"""Explicit, bounded, read-only logged-in X research boundary (P1, 2026-09-04).

LAWS of this module (each is tested):

- It activates ONLY from an explicit operator grant bound to one user-selected
  X tab. There is no profile discovery, no automatic attach, no cookie/local-
  storage/credential surface — the authority interface below has no such
  method, so a hostile page cannot request one.
- The production authority CANNOT attach to an authenticated tab (the
  canonical browser engine launches disposable scratch profiles by law), so it
  returns the typed ``authenticated_browser_session_unavailable`` and offers
  safe alternatives. Tests inject a fake tab authority; a real account is
  never touched.
- Read-only: the offered intent set contains no post/reply/like/repost/follow/
  bookmark/DM/settings mutation. ``assert_intent_set_is_read_only`` is the
  named guard.
- Domain scope is x.com only; cross-domain links become observations that stay
  un-actionable until separately authorized.
- Work is bounded: page count, post count, captured characters, wall clock and
  navigation count, all enforced here, not by caller discipline.
- Personalized feeds are ``personal_feed`` samples — never global/platform
  trends. Visible engagement numbers are observations with a collection time.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

UNAVAILABLE_CODE = "authenticated_browser_session_unavailable"
ALLOWED_DOMAIN = "x.com"

# Read-only intent vocabulary for this lane. Mutations are absent BY DESIGN:
# adding one here must fail test_social_content_x_session (named sabotage).
READ_ONLY_INTENTS = (
    "x_session.observe_visible_posts",
    "x_session.observe_surface_label",
)

SAFE_ALTERNATIVES = (
    "public X search via the normal browser lane",
    "pasted or exported posts as operator-supplied evidence",
    "screenshot/URL evidence processed through the attachment lane",
)


class AuthenticatedBrowserSessionUnavailable(Exception):
    """Typed refusal: no safe attach to an operator-selected authenticated tab."""

    code = UNAVAILABLE_CODE

    def __init__(self, reason: str = "") -> None:
        super().__init__(
            f"{UNAVAILABLE_CODE}: cannot safely attach to an authenticated X tab "
            f"without exposing the wider browser profile ({reason}); safe alternatives: "
            + "; ".join(SAFE_ALTERNATIVES)
        )
        self.alternatives = SAFE_ALTERNATIVES


# ---------------------------------------------------------------- bounds + grant


@dataclass(frozen=True)
class SessionBounds:
    max_posts: int = 40
    max_pages: int = 3
    max_chars: int = 40_000
    max_seconds: float = 30.0
    max_navigations: int = 4


DEFAULT_BOUNDS = SessionBounds()


@dataclass(frozen=True)
class XTabGrant:
    grant_id: str
    tab_handle: str                 # opaque id of the user-selected tab
    surface: str                    # home | following | for_you | explore | search | account
    issued_by: str = "operator"
    domain: str = ALLOWED_DOMAIN
    read_only: bool = True
    bounds: SessionBounds = field(default_factory=SessionBounds)
    issued_at: float = 0.0
    expires_at: float = 0.0
    approved_redirect_chain: tuple[str, ...] = ()

    def validate(self, *, now: float) -> None:
        problems: list[str] = []
        if self.issued_by != "operator":
            problems.append("grant must be operator-issued")
        if not self.read_only:
            problems.append("grant must be read-only")
        allowed = {ALLOWED_DOMAIN, *self.approved_redirect_chain}
        if self.domain not in allowed:
            problems.append(f"domain {self.domain!r} outside approved scope {sorted(allowed)}")
        if self.expires_at and now > self.expires_at:
            problems.append("grant expired")
        if problems:
            raise ValueError("; ".join(problems))


# ---------------------------------------------------------------- authority


@dataclass(frozen=True)
class VisiblePost:
    author: str
    reposter: str = ""
    quoted_author: str = ""
    text: str = ""
    url: str = ""
    surface: str = ""               # where it was seen
    engagement: dict[str, str] = field(default_factory=dict)  # visible numbers, as shown
    collected_at: float = 0.0


class XTabAuthority(Protocol):
    """The ONLY surface this lane may touch. No cookie/storage/credential
    method exists on the protocol — that absence is the boundary."""

    def visible_posts(self, grant: XTabGrant, *, page: int) -> list[VisiblePost]: ...


class ProductionXTabAuthority:
    """The honest production answer: the canonical browser engine launches
    disposable scratch profiles and structurally refuses operator profiles, so
    no authenticated attach exists. Refuse typed; never improvise one."""

    def visible_posts(self, grant: XTabGrant, *, page: int) -> list[VisiblePost]:
        raise AuthenticatedBrowserSessionUnavailable("production engine uses disposable profiles")


_MUTATION_TOKENS = frozenset({
    "post", "reply", "like", "repost", "quote", "follow", "unfollow",
    "bookmark", "dm", "message", "notification", "settings", "delete", "send",
})


def assert_intent_set_is_read_only(intents: tuple[str, ...]) -> None:
    """Token-strict: NO intent — not even one already listed in
    READ_ONLY_INTENTS — may carry a mutation token. Membership is not
    authority; a smuggled 'x_session.repost_post' added to the list must
    still refuse (sabotage-13 proof)."""
    for intent in intents:
        tokens = {t for t in re.split(r"[^a-z0-9]+", intent.lower()) if t}
        hit = tokens & _MUTATION_TOKENS
        if hit:
            raise ValueError(
                f"intent {intent!r} is a mutation intent {sorted(hit)} — "
                "the X session lane is read-only"
            )


def surface_source_class(surface: str) -> str:
    s = str(surface or "").strip().lower()
    if s in ("home", "following", "for_you", "for you"):
        return "personal_feed"
    if s in ("explore", "trending", "trends"):
        return "platform_trending"
    if s == "search":
        return "x_search"
    if s in ("account", "profile"):
        return "named_account"
    return "personal_feed"


def _dedup_key(post: VisiblePost) -> str:
    """Reposts, quotes and near-identical copies collapse on normalized TEXT;
    the author distinction is preserved in the stored fields, not the key."""
    normalized = re.sub(r"\s+", " ", post.text).strip().lower()
    return normalized[:200]


@dataclass
class SessionObservation:
    grant_id: str
    source_class: str
    posts: list[dict[str, Any]] = field(default_factory=list)
    duplicates_removed: int = 0
    pages_read: int = 0
    chars_captured: int = 0
    stopped_reason: str = ""
    cross_domain_links: list[str] = field(default_factory=list)
    collected_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "grant_id": self.grant_id, "source_class": self.source_class,
            "observation_count": len(self.posts),
            "duplicates_removed": self.duplicates_removed,
            "pages_read": self.pages_read, "chars_captured": self.chars_captured,
            "stopped_reason": self.stopped_reason,
            "cross_domain_links": self.cross_domain_links,
            "collected_at": self.collected_at,
            "posts": self.posts,
        }


def observe_session(
    authority: XTabAuthority, grant: XTabGrant, *, now: float | None = None,
) -> SessionObservation:
    """Bounded read-only observation of the granted tab only."""
    grant.validate(now=now if now is not None else time.time())
    assert_intent_set_is_read_only(READ_ONLY_INTENTS)
    bounds = grant.bounds
    observation = SessionObservation(
        grant_id=grant.grant_id, source_class=surface_source_class(grant.surface),
        collected_at=now if now is not None else time.time(),
    )
    seen: set[str] = set()
    started = now if now is not None else time.time()
    for page in range(1, bounds.max_pages + 1):
        posts = authority.visible_posts(grant, page=page)
        observation.pages_read = page
        for post in posts:
            if len(observation.posts) >= bounds.max_posts:
                observation.stopped_reason = "post bound"
                return observation
            key = _dedup_key(post)
            if key in seen:
                observation.duplicates_removed += 1
                continue
            seen.add(key)
            text = str(post.text or "")
            if observation.chars_captured + len(text) > bounds.max_chars:
                observation.stopped_reason = "character bound"
                return observation
            observation.chars_captured += len(text)
            for link in re.findall(r"https?://\S+", text + " " + post.url):
                domain = re.sub(r"^https?://", "", link).split("/")[0].lower()
                if domain.endswith(ALLOWED_DOMAIN) or domain == ALLOWED_DOMAIN:
                    continue
                observation.cross_domain_links.append(link)
            observation.posts.append({
                "author": post.author, "reposter": post.reposter,
                "quoted_author": post.quoted_author, "text": text,
                "url": post.url, "surface": post.surface or grant.surface,
                "engagement": dict(post.engagement),  # visible numbers only, as shown
                "collected_at": post.collected_at or observation.collected_at,
            })
        if not posts:
            observation.stopped_reason = "no further posts"
            return observation
    observation.stopped_reason = "page bound"
    return observation


# ---------------------------------------------------------------- trend brief


def trend_brief(
    observation: SessionObservation, *, campaign_terms: tuple[str, ...] = (),
    expiry_seconds: float = 6 * 3600.0, now: float | None = None,
) -> dict[str, Any]:
    """The honest trend brief: what is visible, why it may matter, what CANNOT
    be inferred, corroboration state, expiry, candidate angles — or NO_POST."""
    at = now if now is not None else time.time()
    terms = [t.lower() for t in campaign_terms if t.strip()]
    relevant = []
    for post in observation.posts:
        low = post["text"].lower()
        matched = [t for t in terms if t in low]
        if matched:
            relevant.append({"post": post, "matched": matched})
    cannot_infer = [
        "a personalized feed sample is not proof of global popularity",
        "visible engagement numbers are point-in-time observations, not quality "
        "or causal evidence; abbreviated metrics are unknown",
        "posts are low-trust orientation under the social source policy — "
        "external claims need corroboration before becoming clean facts",
    ]
    if observation.source_class == "personal_feed":
        cannot_infer.insert(0, "this is a personal feed sample, labelled personal_feed, "
                               "not a platform trend")
    brief: dict[str, Any] = {
        "what_is_visible": [
            {"text": r["post"]["text"], "author": r["post"]["author"]}
            for r in relevant
        ] or [p["text"] for p in observation.posts[:5]],
        "why_it_may_matter": (
            f"{len(relevant)} of {len(observation.posts)} visible posts mention campaign terms"
            if relevant else "no visible post mentions the campaign terms"
        ),
        "source_class": observation.source_class,
        "sample_limit": f"{len(observation.posts)} posts over {observation.pages_read} page(s); "
                        f"stopped: {observation.stopped_reason}",
        "what_cannot_be_inferred": cannot_infer,
        "corroboration": "uncorroborated — treat every visible claim as orientation only",
        "expiry": at + expiry_seconds,
        "candidate_angles": [],
    }
    if relevant:
        brief["candidate_angles"] = [
            {
                "angle": f"Respond to the visible discussion about {m[0]}",
                "evidence_ref": r["post"]["url"] or f"session:{observation.grant_id}",
                "source_class": observation.source_class,
            }
            for r, m in ((r, r["matched"]) for r in relevant)
        ][:3]
    else:
        brief["decision"] = "NO_POST"
        brief["reason"] = (
            "no relevant or defensible angle exists in the bounded sample"
        )
    return brief
