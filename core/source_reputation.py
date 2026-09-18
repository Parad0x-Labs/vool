from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class SourceProfile:
    profile_id: str
    label: str
    topic_kinds: tuple[str, ...]
    trust_weight: float
    ttl_seconds: int
    query_template: str
    notes: str
    allow_domains: tuple[str, ...] = ()
    deny_domains: tuple[str, ...] = ()
    credibility_class: str = "curated"

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


_PROFILES: dict[str, SourceProfile] = {
    "official_docs": SourceProfile(
        profile_id="official_docs",
        label="Official docs",
        topic_kinds=("technical", "design", "integration"),
        trust_weight=0.82,
        ttl_seconds=60 * 60 * 24 * 21,
        query_template="{topic} official docs site:docs.python.org OR site:developer.mozilla.org OR site:web.dev OR site:developer.android.com OR site:developer.apple.com",
        notes="Best for stable technical guidance and standards.",
        allow_domains=("docs.python.org", "developer.mozilla.org", "web.dev", "developer.android.com", "developer.apple.com"),
        credibility_class="primary_technical",
    ),
    "messaging_platform_docs": SourceProfile(
        profile_id="messaging_platform_docs",
        label="Messaging platform docs",
        topic_kinds=("integration", "technical"),
        trust_weight=0.84,
        ttl_seconds=60 * 60 * 24 * 21,
        query_template="{topic} site:core.telegram.org OR site:discord.com/developers",
        notes="Focused on Telegram and Discord bot/platform documentation.",
        allow_domains=("core.telegram.org", "discord.com", "discord.com/developers"),
        credibility_class="primary_platform",
    ),
    "reputable_repos": SourceProfile(
        profile_id="reputable_repos",
        label="Reputable repositories",
        topic_kinds=("technical", "integration", "design"),
        trust_weight=0.68,
        ttl_seconds=60 * 60 * 24 * 14,
        query_template="{topic} site:github.com",
        notes="Useful for current implementation patterns and examples.",
        allow_domains=("github.com",),
        credibility_class="repo_reference",
    ),
    "wikipedia_orientation": SourceProfile(
        profile_id="wikipedia_orientation",
        label="Wikipedia orientation",
        topic_kinds=("technical", "design", "news", "general"),
        trust_weight=0.54,
        ttl_seconds=60 * 60 * 24 * 7,
        query_template="{topic} site:wikipedia.org",
        notes="Good for fast orientation, not canonical truth by itself.",
        allow_domains=("wikipedia.org",),
        credibility_class="orientation",
    ),
    "product_design": SourceProfile(
        profile_id="product_design",
        label="Design guidance",
        topic_kinds=("design",),
        trust_weight=0.72,
        ttl_seconds=60 * 60 * 24 * 14,
        query_template="{topic} site:material.io OR site:developer.apple.com/design OR site:web.dev",
        notes="Design-system and interaction guidance.",
        allow_domains=("material.io", "developer.apple.com", "web.dev"),
        credibility_class="design_guidance",
    ),
    # The profile that was missing, and whose absence made every ordinary question a developer-docs
    # question. `official_docs` led the `general` branch, so "current population of Iceland" was
    # searched as "... official docs site:docs.python.org OR site:developer.mozilla.org OR ...".
    # Measured on the live daemon 2026-07-30: Iceland's population, the Nintendo Switch 2 release
    # date, the latest Python version and "how do I hide columns in Google Sheets" all came back as
    # MDN and PageSpeed Insights pages, and the turn then correctly reported that the search results
    # were about something else. The refusals were honest; the SEARCH was wrong.
    #
    # No site: filter and no allow_domains — an empty allow list means "do not restrict" in
    # `_notes_from_research`, so the open web is read and `evaluate_source_domain` still screens it.
    "open_web": SourceProfile(
        profile_id="open_web",
        label="Open web",
        topic_kinds=("general", "news", "technical", "design", "integration"),
        trust_weight=0.58,
        ttl_seconds=60 * 60 * 24,
        query_template="{topic}",
        notes="The user's question as asked. First choice whenever the topic is not domain-bound.",
        credibility_class="open_web",
    ),
    "reputable_news": SourceProfile(
        profile_id="reputable_news",
        label="Reputable news",
        topic_kinds=("news",),
        trust_weight=0.52,
        ttl_seconds=60 * 60 * 12,
        query_template="{topic} site:reuters.com OR site:apnews.com OR site:bbc.com OR site:cnn.com",
        notes="Short-lived pulse on current events. Must decay quickly.",
        allow_domains=("reuters.com", "apnews.com", "bbc.com", "cnn.com"),
        deny_domains=("rt.com", "sputniknews.com", "infowars.com", "oann.com", "thegatewaypundit.com", "breitbart.com", "newsmax.com"),
        credibility_class="reputable_news",
    ),
}


def get_source_profile(profile_id: str) -> SourceProfile | None:
    return _PROFILES.get(profile_id)


# Words that make a question plausibly about developer documentation, for the `general` branch
# below. Deliberately names languages, platforms and build tooling rather than soft words like
# "api" or "bot", which belong to every subject and were what pinned unrelated questions to
# developer-docs domains in the first place.
_DEVELOPER_FLAVOUR_TOKENS = (
    "python", "javascript", "typescript", "java ", "kotlin", "swift", "rust", "golang", "node.js",
    "nodejs", "npm", "pip ", "css", "html", "sql", "react", "vue", "angular", "django", "flask",
    "sdk", "compiler", "runtime", "stdlib", "regex", "docker", "kubernetes", "git ", "github",
    "stack trace", "traceback", "syntax", "function", "library", "framework", "dependency",
)


def _looks_developer_flavoured(lowered: str) -> bool:
    return any(token in lowered for token in _DEVELOPER_FLAVOUR_TOKENS)


def profiles_for_topic(topic_kind: str, topic: str) -> list[SourceProfile]:
    topic_kind = (topic_kind or "general").strip().lower() or "general"
    lowered = (topic or "").lower()
    selected: list[SourceProfile] = []
    # The platform must be NAMED. "bot", "api" and "webhook" are words that belong to every
    # platform, so they selected the Telegram/Discord profile for questions about neither:
    # "how do I use the fetch api in javascript" was searched as
    # "... site:core.telegram.org OR site:discord.com". Same shape as the `general` branch below —
    # a topic word standing in for a topic.
    platform_topic = any(token in lowered for token in ("telegram", "discord"))

    if topic_kind in {"technical", "integration"}:
        if platform_topic:
            selected.extend(
                [
                    _PROFILES["messaging_platform_docs"],
                    _PROFILES["reputable_repos"],
                    _PROFILES["official_docs"],
                    _PROFILES["wikipedia_orientation"],
                ]
            )
        else:
            # Docs first — this branch really is a technical question. Open web second, because the
            # five domains in `official_docs` are not the world: "what webhook format does stripe
            # use" is a technical question whose answer is on stripe.com and in none of them.
            selected.extend(
                [
                    _PROFILES["official_docs"],
                    _PROFILES["open_web"],
                    _PROFILES["reputable_repos"],
                    _PROFILES["wikipedia_orientation"],
                ]
            )
    elif topic_kind == "design":
        selected.extend(
            [
                _PROFILES["product_design"],
                _PROFILES["official_docs"],
                _PROFILES["reputable_repos"],
            ]
        )
    elif topic_kind == "news":
        # The four wire services first, then the open web — four `site:` filters and nothing else
        # is a very small net for "what happened today", and when it comes back empty the turn has
        # no evidence at all rather than a lesser source.
        selected.extend(
            [
                _PROFILES["reputable_news"],
                _PROFILES["open_web"],
                _PROFILES["wikipedia_orientation"],
            ]
        )
    else:
        # `general` is the DEFAULT kind — `_infer_topic_kind` returns it for everything that does
        # not name news, an API, or design — so this branch decides how most questions are searched.
        # It used to LEAD with `official_docs`, which pins the search to five developer-docs
        # domains. The user's own words go first now.
        #
        # `official_docs` was then kept third as a safety net for a technical question that was
        # never classified technical. But the selected profiles are not tried in order until one
        # works -- `planned_search_query` runs them ALL and MERGES the results -- so a third-place
        # doc profile is not a fallback, it is a guaranteed share of every general question's
        # evidence. Measured live: "search the web for when the Sydney Harbour Bridge opened"
        # answered "the other two sources are MDN Web Docs (web development documentation) which
        # are unrelated", because two of its three searches were sent to docs.python.org and MDN.
        # So the net is only cast there when the question actually names something developer-ish.
        selected.append(_PROFILES["open_web"])
        selected.append(_PROFILES["wikipedia_orientation"])
        if _looks_developer_flavoured(lowered):
            selected.append(_PROFILES["official_docs"])

    deduped: list[SourceProfile] = []
    seen: set[str] = set()
    for profile in selected:
        if profile.profile_id in seen:
            continue
        seen.add(profile.profile_id)
        deduped.append(profile)
    return deduped


def render_query(profile: SourceProfile, topic: str) -> str:
    lowered = topic.strip().lower()
    if profile.profile_id == "messaging_platform_docs":
        if "telegram" in lowered or "bot api" in lowered:
            return f"{topic.strip()} site:core.telegram.org"
        if "discord" in lowered:
            return f"{topic.strip()} site:discord.com/developers OR site:discord.com"
    return profile.query_template.format(topic=topic.strip())


def allowed_domains_for_topic(profile: SourceProfile, topic: str) -> tuple[str, ...]:
    lowered = topic.strip().lower()
    if profile.profile_id == "messaging_platform_docs":
        if "telegram" in lowered or "bot api" in lowered:
            return ("core.telegram.org",)
        if "discord" in lowered:
            return ("discord.com", "discord.com/developers")
    return profile.allow_domains
