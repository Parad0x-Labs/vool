from __future__ import annotations

import hashlib
import re
import time
import uuid
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from core import policy_engine
from core.remote_fetch_policy import note_remote_fetch_attempt, remote_fetch_forbidden
from core.source_credibility import evaluate_source_domain
from core.source_reputation import SourceProfile, allowed_domains_for_topic, profiles_for_topic, render_query
from storage.db import execute_query, get_connection
from tools.web.web_research import ResearchResult, WebHit, web_research

# Shared wall-clock ceiling for a multi-profile planned search. Each profile is a full
# web_research call; without this, a blocked-scraper query multiplies per profile.
_PLANNED_TOTAL_BUDGET_S = 26.0

#: Below this, the generic fallback search cannot finish anything useful, so it is not started.
#: It is a floor on the REMAINING share of `_PLANNED_TOTAL_BUDGET_S`, never an extra allowance.
_MIN_FALLBACK_BUDGET_S = 4.0


class WebAdapter:
    """
    Bounded web-research adapter.

    Search results remain candidate-only low-confidence hints. The adapter keeps
    the legacy `search_query()` note shape so existing curiosity code keeps
    working while the provider stack underneath moves to SearXNG/DDG/browser.
    """

    @staticmethod
    def research_query(
        query_text: str,
        *,
        limit: int = 3,
        total_budget_s: float | None = None,
    ) -> ResearchResult | None:
        note_remote_fetch_attempt()
        if remote_fetch_forbidden():
            return None
        if not policy_engine.allow_web_fallback():
            return None
        text = (query_text or "").strip()
        if not text:
            return None
        return web_research(
            text,
            max_hits=max(1, int(limit)),
            max_pages=min(max(1, int(limit)), 3),
            total_budget_s=total_budget_s,
        )

    @staticmethod
    def search_query(
        query_text: str,
        *,
        task_id: str | None = None,
        limit: int = 3,
        source_label: str = "web.search",
        allowed_domains: tuple[str, ...] = (),
        blocked_domains: tuple[str, ...] = (),
        total_budget_s: float | None = None,
    ) -> list[dict]:
        note_remote_fetch_attempt()
        if remote_fetch_forbidden():
            return []
        research = WebAdapter.research_query(
            query_text,
            limit=limit,
            total_budget_s=total_budget_s,
        )
        if research is None:
            return []
        return _notes_from_research(
            research,
            query_text=query_text,
            task_id=task_id,
            source_label=source_label,
            allowed_domains=allowed_domains,
            blocked_domains=blocked_domains,
            max_notes=limit,
        )

    @staticmethod
    def planned_search_query(
        query_text: str,
        *,
        task_id: str | None = None,
        limit: int = 3,
        task_class: str = "unknown",
        topic_kind: str | None = None,
        topic_hints: list[str] | tuple[str, ...] = (),
        max_profiles: int = 3,
        per_profile_limit: int = 2,
        source_label: str = "web.search",
    ) -> list[dict]:
        note_remote_fetch_attempt()
        if remote_fetch_forbidden():
            return []
        text = str(query_text or "").strip()
        if not text:
            return []

        effective_kind = (
            str(topic_kind or "").strip().lower()
            or _infer_topic_kind(text, task_class=task_class, topic_hints=list(topic_hints or []))
        )
        selected_profiles = profiles_for_topic(effective_kind, text)[: max(1, int(max_profiles))]
        if not selected_profiles:
            return WebAdapter.search_query(
                text,
                task_id=task_id,
                limit=limit,
                source_label=source_label,
            )

        ranked_notes: list[dict[str, Any]] = []
        per_profile = max(1, min(int(per_profile_limit), max(1, int(limit))))
        loop_deadline = time.monotonic() + _PLANNED_TOTAL_BUDGET_S
        for profile in selected_profiles:
            remaining = loop_deadline - time.monotonic()
            if remaining <= 3.0:
                break
            effective_allow_domains = allowed_domains_for_topic(profile, text)
            research = WebAdapter.research_query(
                render_query(profile, text),
                limit=max(per_profile, 2),
                total_budget_s=min(18.0, remaining),
            )
            if research is None:
                continue
            ranked_notes.extend(
                _notes_from_research(
                    research,
                    query_text=text,
                    task_id=task_id,
                    source_label=source_label,
                    allowed_domains=effective_allow_domains,
                    blocked_domains=profile.deny_domains,
                    max_notes=per_profile,
                    source_profile=profile,
                )
            )

        deduped = _dedupe_ranked_notes(ranked_notes, limit=limit)
        if deduped:
            return deduped
        # THE FALLBACK SHARES THIS CALL'S BUDGET; it does not start a new one. Without the
        # argument, `search_query` -> `research_query` -> `web_research` applies its own
        # `_DEFAULT_WEB_BUDGET_S` (18 s) on top of the 26 s already spent here, so a "bounded"
        # adapter took 48 s to report that it found nothing. Measured on the served daemon
        # 2026-09-09: every retrieval in a 20-case pack took 48-49 s and returned nothing, and one
        # turn paid it twice before the model ran (validation-logs/consolidation-continuation-
        # 20260909, F35).
        #
        # With no time left there is no fallback: the profile loop has already spent the turn's
        # retrieval budget, and the caller learns "nothing found" now instead of 18 s later. The
        # generic search still runs whenever the loop finished early, which is the case it exists
        # for, and that control is pinned.
        remaining = loop_deadline - time.monotonic()
        if remaining < _MIN_FALLBACK_BUDGET_S:
            return []
        return WebAdapter.search_query(
            text,
            task_id=task_id,
            limit=limit,
            source_label=source_label,
            total_budget_s=remaining,
        )

    @staticmethod
    def search_and_summarize(task: dict, classification: dict) -> list[dict]:
        task_summary = str(task.get("task_summary", "") or "")
        task_class = str(classification.get("task_class", "unknown"))
        if task_class in {"research", "system_design", "integration_orchestration"}:
            return WebAdapter.planned_search_query(
                task_summary,
                task_id=task.get("task_id"),
                limit=3,
                task_class=task_class,
            )
        return WebAdapter.search_query(
            task.get("task_summary", ""),
            task_id=task.get("task_id"),
            limit=3,
            source_label="web.search",
        )


def _page_for_hit(research: ResearchResult, url: str):
    for page in research.pages:
        if page.url == url or page.final_url == url:
            return page
    return None


def _looks_like_binary_noise(text: str) -> bool:
    """True when 'page text' is really undecoded bytes rather than prose.

    A compressed body decoded as UTF-8 does not raise -- it yields mojibake, and mojibake was being
    handed to the model as a source summary ("the summary is garbled" was a real answer). Prose is
    overwhelmingly ASCII letters, spaces and punctuation; a decode artifact is not.
    """
    sample = str(text or "")[:400]
    if not sample:
        return False
    # A C0 control byte is decisive on its own: prose never carries NUL, backspace or the gzip
    # header's 0x1f, and a compressed stream is full of them. This catches a small body that
    # compressed down to a mostly-ASCII stream, which the ratio below is too coarse to see.
    if any(ch in sample for ch in "\x00\x01\x02\x03\x04\x05\x06\x07\x08\x0b\x0c\x0e\x0f\x1f"):
        return True
    if len(sample) < 40:
        return False
    printable = sum(1 for ch in sample if ch.isascii() and (ch.isalnum() or ch.isspace() or ch in ".,;:!?'\"()-/&%$#@+*="))
    return (printable / len(sample)) < 0.75


_PASSAGE_MAX_CHARS = 700
_PASSAGE_STOPWORDS = frozenset({
    "the", "and", "for", "with", "that", "this", "from", "into", "about", "what", "which", "their", "there",
    "compare", "comparison", "detail", "details", "please", "now", "then", "also", "versus", "between",
})

#: Glue vocabulary: words that appear on unrelated technical pages everywhere. A hit that matches
#: the query ONLY through these words is not about the question -- it is the engine's domain
#: restriction (site:github.com, site:wikipedia.org) satisfying itself. Measured on the live
#: daemon 2026-09-17: an OpenRouter-documentation turn bound an Apify store-actor PR and the
#: BGPsec and JWT Wikipedia pages, none of which carries a single distinctive term of the query,
#: because admission checked only domain allowlists and credibility.
_GENERIC_WEB_TERMS = frozenset({
    "api", "apis", "app", "apps", "available", "best", "build", "building", "builder", "clean",
    "clear", "client", "clients", "code", "create", "current", "currently", "data", "docs",
    "documentation", "example", "examples", "fetch", "fetches", "fetching", "file", "files",
    "good", "handle", "handles", "handling", "host", "hosts", "html", "http", "https", "info",
    "information", "json", "key", "keys", "latest", "list", "lists", "make", "making", "need",
    "needs", "new", "news", "print", "prints", "release", "request", "requests", "response",
    "responses", "script", "scripts", "server", "servers", "service", "services", "show",
    "shows", "simple", "small", "source", "support", "supports", "system", "systems", "test",
    "tests", "testing", "tool", "tools", "use", "used", "useful", "user", "users", "uses",
    "using", "utility", "web", "work", "works", "working", "provider", "providers", "router",
    "routers", "gateway", "gateways", "checker", "checkers", "verify", "verifies", "verified",
    "verifying", "retrieve", "retrieves", "retrieved", "extract", "extracts", "extracted",
    "sort", "sorts", "sorted", "printing", "research", "researches",
    "exists", "exist",
})


def _distinctive_query_terms(query_text: str) -> set[str]:
    """The query's subject-bearing tokens: long enough, not stop words, not page glue.

    The same tokenization ``_relevant_passage`` scores windows with, with hyphenated and
    versioned compounds split into their parts (``provider-health`` is judged as ``provider``
    and ``health``; a 5-character prefix of the compound would otherwise match the bare word on
    any unrelated page) and minus the glue vocabulary, so a term here is a word a page carries
    only by being ABOUT the question.
    """
    tokens = re.findall(r"[a-z0-9][a-z0-9.\-]*", str(query_text or "").lower())
    parts: set[str] = set()
    for token in tokens:
        parts.add(token)
        parts.update(part for part in re.split(r"[.\-]+", token) if len(part) >= 3)
    return {
        part
        for part in parts
        if len(part) >= 3 and part not in _PASSAGE_STOPWORDS and part not in _GENERIC_WEB_TERMS
    }


def _carries_distinctive_term(text: str, query_text: str) -> bool:
    """Whether the hit's own words name the question's subject. ``True`` when the query has
    nothing distinctive to demand.

    Full-term matching with simple plural forms, deliberately NOT the passage scorer's
    5-character prefix rule: a prefix lets ``verify`` match ``verified`` and admit a page that
    shares only a word-stem with the question -- exactly the leak that admitted the incident's
    unrelated pull request.
    """
    terms = _distinctive_query_terms(query_text)
    if not terms:
        return True
    lowered = str(text or "").lower()
    return any((term in lowered) or (term + "s" in lowered) or (term + "es" in lowered) for term in terms)


def _relevant_passage(page_text: str, query_text: str, *, already: str) -> str:
    """The window of the fetched page most about `query_text`, bounded, or "" when nothing qualifies.

    Sentence windows are scored by the distinct query terms they carry; the best window wins only
    when it carries at least two terms and adds something the snippet did not already say.
    Navigation noise and undecoded bytes never qualify, whatever they score.
    """
    text = " ".join(str(page_text or "").split()).strip()
    if not text or _looks_like_binary_noise(text):
        return ""
    terms = {
        token for token in re.findall(r"[a-z0-9][a-z0-9.\-]*", str(query_text or "").lower())
        if len(token) >= 3 and token not in _PASSAGE_STOPWORDS
    }
    if not terms:
        return ""
    sentences = [part.strip() for part in re.split(r"(?<=[.!?])\s+", text) if part.strip()]
    if not sentences:
        return ""
    best, best_score = "", 0
    for start in range(len(sentences)):
        window, length = [], 0
        for sentence in sentences[start:]:
            if length + len(sentence) > _PASSAGE_MAX_CHARS and window:
                break
            window.append(sentence)
            length += len(sentence) + 1
        candidate = " ".join(window)
        if _looks_like_navigation_noise(candidate):
            continue  # a real page opens with its navigation bar; that window never qualifies
        lowered = candidate.lower()
        # Prefix matching ("engines" ~ "engine", "prices" ~ "price"); a page about ONE of two
        # compared subjects legitimately carries one subject term, so one hit qualifies.
        score = sum(1 for term in terms if (term[:5] if len(term) > 5 else term) in lowered)
        if score > best_score:
            best, best_score = candidate, score
    if best_score < 1:
        return ""
    if best[:120] and best[:120] in already:
        # The snippet already opens with this passage; add only what it cut off.
        best = best[len(already.rstrip()) :].strip() if best.startswith(already.rstrip()) else best
    return best[:_PASSAGE_MAX_CHARS].strip()


def _best_summary(hit: WebHit, page, query_text: str = "") -> str:
    """Pick the text most likely to ANSWER the query.

    The search engine chose its snippet FOR THIS QUERY, so it is where the answer usually is. The
    first 280 characters of the fetched page are just wherever the page happens to start, which on
    a real site is the navigation bar. Preferring page text meant a result whose snippet read
    "Iceland has a total population of 402,329" was summarised for the model as
    "Iceland Population 2026 World Population Review Data by Location chevron Data by Location
    Browse stats by country" -- and the model correctly reported that the results did not contain
    the answer, because by then they did not.
    """
    snippet = " ".join(str(hit.snippet or "").split()).strip()
    if snippet:
        lead = snippet[:280]
        # The snippet locates the answer; the fetched page's most relevant passage carries the
        # facts the snippet was cut before (measured 2026-09-06: "2.0 TSI 204 PS and 26" ended the
        # snippet, and a true "265 PS" claim was withheld while the page saying so sat fetched).
        if page is not None and query_text:
            passage = _relevant_passage(str(getattr(page, "text", "") or ""), query_text, already=lead)
            if passage and passage not in lead:
                return f"{lead} … {passage}"[: 280 + 3 + _PASSAGE_MAX_CHARS]
        return lead
    if page is not None:
        text = " ".join(str(getattr(page, "text", "") or "").split()).strip()
        if text and not _looks_like_navigation_noise(text) and not _looks_like_binary_noise(text):
            return text[:280]
    title = str(hit.title or "").strip()
    return title[:280]


def _looks_like_navigation_noise(text: str) -> bool:
    sample = " ".join(str(text or "").split())[:320].lower()
    if not sample:
        return False
    direct_markers = (
        "skip to content",
        "skip to main content",
        "accessibility help",
        "open menu",
        "search site",
        "sign in",
        "account notifications",
        "privacy settings",
        "download browser",
    )
    if any(marker in sample for marker in direct_markers):
        return True
    token_hits = sum(sample.count(token) for token in (" home ", " menu ", " search ", " settings ", " privacy ", " account ", " login "))
    if token_hits >= 4:
        return True
    return bool("home news sport weather" in sample or "home news sport business" in sample)


def _notes_from_research(
    research: ResearchResult,
    *,
    query_text: str,
    task_id: str | None,
    source_label: str,
    allowed_domains: tuple[str, ...],
    blocked_domains: tuple[str, ...],
    max_notes: int,
    source_profile: SourceProfile | None = None,
) -> list[dict]:
    notes: list[dict] = []
    for hit in list(research.hits or []):
        page = _page_for_hit(research, hit.url)
        resolved_url = str(getattr(page, "final_url", "") or hit.url or "").strip()
        origin_domain = _domain_from_url(resolved_url)
        if blocked_domains and _domain_matches(origin_domain, blocked_domains):
            continue
        if allowed_domains and not _domain_matches(origin_domain, allowed_domains):
            continue

        verdict = evaluate_source_domain(origin_domain)
        if verdict.blocked:
            continue

        summary = _best_summary(hit, page, query_text=query_text)
        if not summary:
            continue

        # WRONG-SOURCE ADMISSION FLOOR. The engine returned this hit for a domain-restricted
        # rewrite of the query, and an allowlist says the DOMAIN is acceptable -- neither says
        # the page is about the question. A hit whose own title and summary carry none of the
        # query's distinctive terms is unrelated material walking in on a credible domain, which
        # the answering model then correctly refuses to lean on while the binding row counts it
        # as retrieved support. Rejected here, before it is stored or ranked.
        if not _carries_distinctive_term(f"{hit.title or ''} {summary}", query_text):
            continue

        profile_id = str(source_profile.profile_id if source_profile else "").strip()
        profile_label = str(source_profile.label if source_profile else "").strip()
        github_root = _github_repo_root(resolved_url)
        rank_score = _rank_source_note(
            hit=hit,
            page=page,
            summary=summary,
            verdict_score=float(verdict.score),
            source_profile=source_profile,
            github_root=github_root,
        )
        confidence = _note_confidence(
            verdict_score=float(verdict.score),
            source_profile=source_profile,
            page=page,
            github_root=github_root,
            title=str(hit.title or ""),
            summary=summary,
        )

        note_id = _store_web_note(
            query_text=query_text,
            summary=summary,
            confidence=confidence,
            task_id=task_id,
            source_label=_provider_source_label(research.provider, hit, source_label),
            url=resolved_url,
        )
        notes.append(
            {
                "source_type": "web_derived",
                "summary": summary,
                "note_id": note_id,
                "query_text": str(query_text or "").strip(),
                "confidence": confidence,
                "source_label": _provider_source_label(research.provider, hit, source_label),
                "search_provider": research.provider,
                "result_url": resolved_url,
                "origin_domain": origin_domain,
                "result_title": hit.title,
                "fetch_status": getattr(page, "status", "no_page"),
                "used_browser": bool(getattr(page, "used_browser", False)),
                "source_profile_id": profile_id,
                "source_profile_label": profile_label,
                "source_credibility": verdict.to_dict(),
                "source_rank_score": rank_score,
                "github_repo_root": github_root,
            }
        )
        if len(notes) >= max(1, int(max_notes)):
            break
    return notes


def _store_web_note(
    *,
    query_text: str,
    summary: str,
    confidence: float,
    task_id: str | None,
    source_label: str,
    url: str,
) -> str:
    note_id = str(uuid.uuid4())
    created_at = datetime.now(timezone.utc).isoformat()
    linked_task_id = _existing_local_task_id(task_id)
    execute_query(
        """
        INSERT INTO web_notes (note_id, query_hash, source_label, source_url_hash, summary, confidence, freshness_ts, used_in_task_id, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            note_id,
            hashlib.sha256(str(query_text or "").encode("utf-8")).hexdigest(),
            source_label,
            hashlib.sha256(url.encode("utf-8")).hexdigest() if url else None,
            summary,
            float(confidence),
            created_at,
            linked_task_id,
            created_at,
        ),
    )
    return note_id


def _existing_local_task_id(task_id: str | None) -> str | None:
    clean_task_id = str(task_id or "").strip()
    if not clean_task_id:
        return None
    try:
        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT task_id FROM local_tasks WHERE task_id = ? LIMIT 1",
                (clean_task_id,),
            ).fetchone()
            return clean_task_id if row else None
        finally:
            conn.close()
    except Exception:
        return None


def _dedupe_ranked_notes(notes: list[dict[str, Any]], *, limit: int) -> list[dict]:
    ranked = sorted(
        list(notes or []),
        key=lambda item: (
            float(item.get("source_rank_score") or 0.0),
            float(item.get("confidence") or 0.0),
        ),
        reverse=True,
    )
    selected: list[dict] = []
    seen: set[str] = set()
    for note in ranked:
        key = _canonical_note_key(note)
        if key in seen:
            continue
        seen.add(key)
        selected.append(note)
        if len(selected) >= max(1, int(limit)):
            break
    return selected


def _canonical_note_key(note: dict[str, Any]) -> str:
    github_root = str(note.get("github_repo_root") or "").strip().lower()
    if github_root:
        return github_root
    result_url = str(note.get("result_url") or "").strip().rstrip("/").lower()
    if result_url:
        return result_url
    title = str(note.get("result_title") or "").strip().lower()
    summary = str(note.get("summary") or "").strip().lower()
    return f"{title}|{summary[:120]}"


def _infer_topic_kind(query_text: str, *, task_class: str, topic_hints: list[str]) -> str:
    lowered = f"{query_text} {' '.join(str(item) for item in topic_hints)}".lower()
    if any(token in lowered for token in ("news", "headline", "current events", "breaking")):
        return "news"
    if any(token in lowered for token in ("telegram", "discord", "bot", "api", "webhook", "integration")):
        return "integration"
    if any(token in lowered for token in ("design", "ux", "ui", "layout", "theme")):
        return "design"
    if task_class in {"research", "system_design", "dependency_resolution", "config", "integration_orchestration"}:
        return "technical"
    return "general"


def _rank_source_note(
    *,
    hit: WebHit,
    page: Any,
    summary: str,
    verdict_score: float,
    source_profile: SourceProfile | None,
    github_root: str,
) -> float:
    profile_weight = float(source_profile.trust_weight if source_profile else 0.42)
    github_signal = _github_result_signal(hit.url, title=str(hit.title or ""), summary=summary, github_root=github_root)
    page_signal = 0.08 if page is not None and str(getattr(page, "text", "") or "").strip() else 0.0
    profile_bonus = _profile_priority_bonus(source_profile)
    return max(
        0.0,
        min(
            1.0,
            0.45 * verdict_score
            + 0.30 * profile_weight
            + 0.10 * github_signal
            + page_signal
            + profile_bonus,
        ),
    )


def _note_confidence(
    *,
    verdict_score: float,
    source_profile: SourceProfile | None,
    page: Any,
    github_root: str,
    title: str,
    summary: str,
) -> float:
    base = 0.25
    if source_profile is not None:
        base = 0.32 + (0.16 * float(source_profile.trust_weight))
    if page is not None and str(getattr(page, "text", "") or "").strip():
        base += 0.04
    base += 0.18 * verdict_score
    base += 0.10 * _github_result_signal("", title=title, summary=summary, github_root=github_root)
    return max(0.25, min(0.78, base))


def _profile_priority_bonus(source_profile: SourceProfile | None) -> float:
    if source_profile is None:
        return 0.0
    if source_profile.profile_id in {"official_docs", "messaging_platform_docs"}:
        return 0.10
    if source_profile.profile_id == "reputable_repos":
        return 0.03
    return 0.0


def _github_repo_root(url: str) -> str:
    domain = _domain_from_url(url)
    if domain != "github.com":
        return ""
    parsed = urlparse(url)
    parts = [part for part in str(parsed.path or "").split("/") if part]
    if len(parts) < 2:
        return ""
    owner, repo = parts[0].strip(), parts[1].strip()
    if not owner or not repo:
        return ""
    return f"https://github.com/{owner}/{repo}"


def _github_result_signal(url: str, *, title: str, summary: str, github_root: str) -> float:
    if not github_root:
        return 0.0
    parsed = urlparse(url)
    parts = [part for part in str(parsed.path or "").split("/") if part]
    path_signal = 1.0
    if len(parts) >= 3:
        kind = parts[2].lower().strip()
        if kind in {"issues", "pull", "pulls", "actions", "commit", "commits", "compare"}:
            path_signal = 0.35
        elif kind in {"blob", "tree", "wiki", "releases"}:
            path_signal = 0.72
        else:
            path_signal = 0.58
    text = f"{title} {summary}".lower()
    if any(token in text for token in ("deprecated", "archived", "unmaintained")):
        path_signal = min(path_signal, 0.18)
    if any(token in text for token in ("example", "examples", "starter", "template")):
        path_signal = min(1.0, path_signal + 0.08)
    return max(0.0, min(1.0, path_signal))


def _provider_source_label(provider: str, hit: WebHit, fallback: str) -> str:
    """Name the provider that actually answered this hit.

    RED at base: only `searxng`, `ddg_instant` and `duckduckgo_html` were named
    and everything else fell through to `return fallback` — the CALLER's label,
    which on the live-info lane and the reasoning fallback is the hardcoded
    string `"duckduckgo.com"`. So a Brave-served result was stored in
    `web_notes`, carried into the receipt and shown to the user as DuckDuckGo.
    A missing label is a gap; a borrowed one is a false provenance claim, and it
    made the paid provider unprovable in both directions: the user could not see
    that their key had run, and could not see when it had not.

    `fallback` survives only for a provider nothing can name — and
    `provider_attribution` names every entry in the shipped table plus every
    keyless engine, so in practice that is the empty-provider case.
    """
    from core.retrieval_provenance import PROVIDER_NONE, provider_attribution

    clean = str(provider or "").strip().lower()
    if not clean or clean in {PROVIDER_NONE, "disabled"}:
        return fallback
    if clean == "searxng":
        # SearXNG is a meta-search: the engine that really answered is on the
        # hit, and reporting the aggregator would hide it.
        engine = str(hit.engine or "").strip()
        if engine and engine != "searxng":
            return engine
    return str(provider_attribution(clean)["provider_label"])


def _domain_from_url(url: str) -> str:
    from urllib.parse import urlparse

    if not url:
        return ""
    parsed = urlparse(url)
    netloc = (parsed.netloc or "").lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    return netloc


def _domain_matches(domain: str, patterns: tuple[str, ...]) -> bool:
    if not domain:
        return False
    for pattern in patterns:
        normalized = pattern.lower().strip()
        if normalized.startswith("www."):
            normalized = normalized[4:]
        if domain == normalized or domain.endswith(f".{normalized}"):
            return True
    return False
