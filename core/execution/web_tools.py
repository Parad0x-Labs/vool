from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import asdict, is_dataclass
from types import SimpleNamespace
from typing import Any

from core.execution.models import ToolIntentExecution
from core.research_evidence import truthful_evidence_strength
from core.retrieval_observability import begin_web_retrieval, finish_web_retrieval

#: The lookup tools the Web lookups switch governs (browser.render has its own policy).
WEB_LOOKUP_INTENTS = frozenset({"web.search", "web.research", "web.fetch"})
WEB_OFF_REFUSAL_KEY = "web.off.tool_refusal"
WEB_OFF_REFUSAL_EN = (
    "Web lookups are off. You can turn them on in Settings, Privacy & Permissions. You can also paste the "
    "article text, links or notes you want me to use, and I will work from them."
)


_SHOPPING_RE = re.compile(
    r"\b(?:best|top\s+\d+|prices?|priced|costs?|cheap(?:est)?|budget|buy(?:ing)?|deals?|recommend\w*|reviews?|"
    r"reviewed|tested)\b",
    re.IGNORECASE,
)
_PAGE_PRICE_RE = re.compile(
    r"(?:[$€£]\s?\d[\d,]*(?:\.\d{1,2})?|\b\d[\d,]*(?:\.\d{1,2})?\s?(?:USD|EUR|GBP|dollars|euros)\b)"
)
_QUERY_FILLER = frozenset(
    [
        "a", "an", "the", "and", "or", "for", "of", "to", "in", "on", "with", "my", "me", "i", "you", "what", "which",
        "how", "is", "are", "it", "its", "best", "top", "good", "great", "cheap", "cheapest", "budget", "buy", "buying",
        "price", "prices", "priced", "cost", "costs", "today", "now", "current", "latest", "new", "review", "reviews",
        "reviewed", "tested", "deal", "deals", "recommend", "recommended", "recommendation", "recommendations", "vs",
        "suggest", "suggestion"
    ]
)
_BIG_AMOUNT_RE = re.compile(r"\b(?:million|billion|trillion|bn|mn)\b", re.IGNORECASE)


def _topic_words(query: str) -> list[str]:
    """The words a search is about ("best travel tripod 2026 price" -> travel, tripod), singular, years dropped."""
    words: list[str] = []
    for word in re.findall(r"[a-z][a-z'-]+", str(query or "").lower()):
        if word in _QUERY_FILLER or len(word) < 3:
            continue
        stem = word[:-1] if word.endswith("s") and not word.endswith("ss") else word
        if stem not in words:
            words.append(stem)
    return words[:4]


def _hit_is_on_topic(row: dict[str, Any], topic: list[str]) -> bool:
    """A hit is read only when its title and address name every word the search is about. Measured on the
    Mac (AQ row 12 on f7a80b44): "travel tripod" read Wikipedia's "Tripod (web hosting)" and "The Tripods" novels,
    and the model was handed "US$4 million venture capital" as a priced line."""
    # Title and address only: a snippet mentions everything near the subject. Mac AQ row 12 on 66ffc11f kept a
    # Wikipedia article in the sources for "travel tripod" because its snippet said travel tripods fold small.
    seen = " ".join(str(row.get(key) or "") for key in ("title", "url")).lower()
    return bool(topic) and all(word in seen for word in topic)


def _on_topic_rows(query: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A shopping search's rows without the off-topic ones, when at least one row is on topic.

    Mac AQ row 12 on 98b42315: the receipt and the model still carried Wikipedia's "Tripod (web hosting)" after the
    page reads skipped it. A search that is not about buying, or whose rows are all off topic, keeps every row."""
    titles = " ".join(str(row.get("result_title") or row.get("title") or "") for row in rows)
    if not _SHOPPING_RE.search(f"{query} {titles}"):
        return rows
    topic = _topic_words(query)
    kept = [
        row for row in rows
        if _hit_is_on_topic({
            "title": row.get("result_title") or row.get("title"),
            "snippet": row.get("summary") or row.get("snippet"),
            "url": row.get("result_url") or row.get("url"),
        }, topic)
    ]
    return kept or rows


#: How many search hits a shopping search reads, and how many priced lines each page gives the model.
_PAGES_READ = 2
_PAGES_TRIED = 4
_LINES_PER_PAGE = 8


# A guide's own heading or a table header, not a thing it names ("12 Best Travel Tripods", "Tripod | Weight").
_LIST_HEADING_RE = re.compile(r"\b(?:best|top\s+\d+|reviews?|reviewed|tested|guide|buying)\b", re.IGNORECASE)


# A run of capitalised words or model codes: what a product name looks like ("Peak Design Travel Tripod (Carbon)",
# "Gitzo Traveler Series 1 Kit", "Zero Y"). Leading sentence words and dates are not part of a name.
_NAME_RUN_RE = re.compile(r"[A-Z][A-Za-z0-9'’&+.-]*(?:\s+\(?[A-Z0-9][A-Za-z0-9'’&+.-]*\)?)+")
_NOT_A_NAME_WORDS = frozenset(
    [
        "the", "a", "an", "our", "my", "your", "this", "that", "these", "those", "it", "its", "is", "are", "can",
        "how", "will", "do", "does", "what", "why", "which", "when", "where", "who", "price", "prices", "check",
        "buy", "see", "read", "written", "best", "top", "here", "there", "if", "for", "and", "in", "on", "at", "to",
        "with", "by", "from", "jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "sept", "oct", "nov",
        "dec", "january", "february", "march", "april", "june", "july", "august", "september", "october",
        "november", "december", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
    ]
)
# Words about the writer or the reader, not a product: a byline ("Written by ... I'm Laurence"), a note to the reader,
# an FAQ ("Can you take a tripod on a plane?").
_ABOUT_PEOPLE_RE = re.compile(
    r"\b(?:written\s+by|I|I'm|I’m|I've|I’ve|I'd|I’d|me|my|we|we're|we’re|we've|we’ve|our|us|you|your)\b",
    re.IGNORECASE,
)
# A price span, not a price: "from $20 to $600", "$30–$600", "between £100 and £200".
_PRICE_RANGE_RE = re.compile(
    r"(?:\b(?:from|between)\s+(?:[$€£]\s?)?\d[\d,.]*\s*(?:to|and|-|–|—)\s*(?:[$€£]\s?)?\d"
    r"|[$€£]\s?\d[\d,.]*\s*(?:-|–|—|to)\s*[$€£]?\s?\d)",
    re.IGNORECASE,
)


def _names_a_thing(line: str) -> bool:
    """Whether `line` holds a product-name-like phrase: two or more capitalised words or codes, not counting a
    sentence's first word, a month or a weekday ("Sep 11"), or a guide's own heading."""
    for match in _NAME_RUN_RE.finditer(str(line or "")):
        words = match.group(0).replace("(", " ").replace(")", " ").split()
        while words and words[0].lower().strip(".'’") in _NOT_A_NAME_WORDS:
            words = words[1:]
        if len(words) < 2 or all(word.lower().strip(".") in _NOT_A_NAME_WORDS or word.isdigit() for word in words):
            continue
        if _LIST_HEADING_RE.search(" ".join(words)) and not re.search(r"\d", " ".join(words[1:])):
            continue
        return True
    return False


def page_line_is_a_pick(line: str) -> bool:
    """A page line that names a product with its price or a short value ("Gitzo Traveler Series 1 Kit -- £540"),
    not a byline, a question to the reader or a price span."""
    text = str(line or "").strip()
    return bool(text) and not text.endswith("?") and not _PRICE_RANGE_RE.search(text) and _names_a_thing(
        text.split(" -- ")[0] if " -- " in text else text
    )


def _a_name_heading(chunk: str) -> bool:
    return (
        len(chunk) <= 100 and not chunk.endswith("?") and not _PAGE_PRICE_RE.search(chunk) and "|" not in chunk
        and not _ABOUT_PEOPLE_RE.search(chunk) and _names_a_thing(chunk)
    )


def priced_page_lines(text: str, *, limit: int = _LINES_PER_PAGE, topic: tuple[str, ...] | list[str] = ()) -> list[str]:
    """The priced product lines of a fetched page: a product-name-like phrase with a price on the same line, or the
    name heading just above a price that stands alone ("Peak Design Travel Tripod -- $379.95"); and, given the
    search's topic words, a name with a short value under it ("Peak Design Travel Tripod (Carbon) -- 2.8 lb").
    Priced picks come first, then named values, then priced lines that name nothing and price spans.

    Mac AQ row 12 on 66ffc11f: the guide's table named the Peak Design Travel Tripod with its weight, and no line of
    it reached the model. On b7a5266d the topic lines were the guide's byline ("Written by Laurence Norah ... a
    professional travel photographer") and its FAQ ("Can you take a tripod on a plane in hand luggage?"), and no
    tripod or price reached the model. Bylines, questions and lines about the writer are never kept. Only the page's
    own words, clipped."""
    chunks: list[str] = []
    for raw in str(text or "").splitlines():
        line = " ".join(raw.split())
        if not line:
            continue
        if len(line) > 300:
            chunks.extend(part.strip() for part in re.split(r"(?<=[.!?])\s+", line) if part.strip())
        else:
            chunks.append(line)
    ranked: list[tuple[int, int, str]] = []
    words = [str(word).lower() for word in topic if str(word).strip()]

    def _priced_alone(chunk: str) -> bool:
        return bool(_PAGE_PRICE_RE.search(chunk)) and len(_PAGE_PRICE_RE.sub("", chunk).strip(" :-|")) <= 12

    def _keep(rank: int, index: int, entry: str) -> None:
        if all(entry != kept for _, _, kept in ranked):
            ranked.append((rank, index, entry))

    for index, chunk in enumerate(chunks):
        if "?" in chunk:
            continue  # a question to the reader, and the FAQ answer run on after it
        if not _PAGE_PRICE_RE.search(chunk):
            # A name with a short value under it ("Peak Design Travel Tripod (Carbon)" / "2.8 lb").
            following = chunks[index + 1] if index + 1 < len(chunks) else ""
            if (
                words and len(chunk.split()) <= 12 and any(w in chunk.lower() for w in words)
                and not _LIST_HEADING_RE.search(chunk) and _a_name_heading(chunk)
                and following and len(following) <= 24 and re.search(r"\d", following)
                and not following.endswith("?") and not _priced_alone(following)
                and not any(w in following.lower() for w in words)
            ):
                _keep(1, index, f"{chunk} -- {following}")
            continue
        if _BIG_AMOUNT_RE.search(chunk):
            continue
        entry = chunk[:220]
        spans = bool(_PRICE_RANGE_RE.search(chunk))
        named = _names_a_thing(_PAGE_PRICE_RE.sub("", chunk))
        if not named and not spans:
            # The name heading a few lines above a price that names nothing ("1. Peak Design Travel Tripod
            # (Carbon)" / "Our top pick, around $599.").
            for back in range(index - 1, max(-1, index - 4), -1):
                above = chunks[back]
                if _PAGE_PRICE_RE.search(above):
                    break
                if not _LIST_HEADING_RE.search(above) and _a_name_heading(above):
                    entry = f"{above} -- {entry}"
                    named = True
                    break
        _keep(0 if named and not spans else 2, index, entry)
    ranked.sort(key=lambda item: (item[0], item[1]))
    return [entry for _, _, entry in ranked[:limit]]


def _read_shopping_hits(
    query: str, results: list[dict[str, Any]], *, source_context: dict[str, Any] | None, call_tool_fn: Callable[..., Any]
) -> list[dict[str, Any]]:
    """For a search that asks what to buy or what it costs, read the top hits and keep their priced lines.

    Measured on the Mac (AQ row 12 on 0a84ca8f, "suggest a good tripod for travel and tell me what it costs today"):
    the search gave the model only 180-character snippets of guide articles, so it listed the articles, named no
    tripod and gave no price. A hit is a page an engine returned, so it is read with the same public-only guard as
    research (`not_local`); a remote turn reads public addresses only. Only hits about what was searched are read
    (`_hit_is_on_topic`). A page that fails or states no price adds nothing."""
    titles = " ".join(str(row.get("title") or "") for row in results)
    if not _SHOPPING_RE.search(f"{query} {titles}"):
        return []
    from core.request_trust import turn_is_remote

    remote = turn_is_remote(source_context)
    excerpts: list[dict[str, Any]] = []
    topic = _topic_words(query)
    # A page that fails or names no priced pick does not use up a read: the next on-topic hit is read instead (Mac
    # AQ row 12 on b7a5266d read one guide of two and kept its byline).
    with_picks = 0
    for row in [hit for hit in results if _hit_is_on_topic(hit, topic)][:_PAGES_TRIED]:
        if with_picks >= _PAGES_READ:
            break
        url = str(row.get("url") or "").strip()
        if not url.lower().startswith(("http://", "https://")):
            continue
        try:
            fetched = (
                call_tool_fn("web.fetch", url=url, timeout_s=8.0, public_only=True) if remote
                else call_tool_fn("web.fetch", url=url, timeout_s=8.0, not_local=True)
            )
        except Exception:
            continue
        if str((fetched or {}).get("status") or "") != "ok":
            continue
        lines = priced_page_lines(str((fetched or {}).get("text") or ""), topic=topic)
        if lines:
            excerpts.append({"title": str(row.get("title") or url), "url": url, "priced_lines": lines})
            with_picks += any(page_line_is_a_pick(line) for line in lines)
    return excerpts


def normalize_item(item: Any) -> dict[str, Any]:
    if isinstance(item, dict):
        return dict(item)
    if is_dataclass(item):
        return asdict(item)
    if hasattr(item, "__dict__"):
        return {
            str(key): value
            for key, value in vars(item).items()
            if not str(key).startswith("_")
        }
    return {"value": str(item)}


def execute_web_tool(
    intent: str,
    arguments: dict[str, Any],
    *,
    task_id: str,
    source_context: dict[str, Any] | None,
    allow_web_fallback_fn: Callable[[], bool],
    load_builtin_tools_fn: Callable[[], Any],
    planned_search_query_fn: Callable[..., list[dict[str, Any]]],
    call_tool_fn: Callable[..., Any],
    adaptive_research_fn: Callable[..., Any],
    unsupported_execution_for_intent_fn: Callable[..., ToolIntentExecution],
    tool_observation_fn: Callable[..., dict[str, Any]],
    audit_log_fn: Callable[..., Any],
) -> ToolIntentExecution:
    from core.remote_fetch_policy import local_only_active, web_unavailable_reason

    if local_only_active(source_context):
        # An Offline chat (or a machine kept fully local): the fetch door would refuse every
        # request, so the tool does not pretend to search. Measured 2026-11-12 on the Mac: with
        # Web lookups on, an Offline chat ran web.research, every fetch was refused, and the user
        # saw only the tool's header line and "Search failed" -- never that the chat is Offline.
        from core.auto_local_only_mode import turn_selected_offline
        from core.remote_fetch_policy import OFFLINE_LOOKUP_TEXT

        reason = (
            OFFLINE_LOOKUP_TEXT if turn_selected_offline(source_context)
            else web_unavailable_reason(source_context)
        )
        return ToolIntentExecution(
            handled=True,
            ok=False,
            status="disabled",
            response_text=reason,
            user_safe_response_text=reason,
            mode="tool_failed",
            tool_name=intent,
            details={"offline": True},
        )
    if not allow_web_fallback_fn():
        execution = unsupported_execution_for_intent_fn(intent, status="disabled")
        if intent in WEB_LOOKUP_INTENTS:
            # The owner hears what still works (pasting the material), in the app language.
            from core.i18n.turn_locale import turn_text

            text = turn_text(WEB_OFF_REFUSAL_KEY, WEB_OFF_REFUSAL_EN)
            execution.response_text = text
            execution.user_safe_response_text = text
        return execution

    load_builtin_tools_fn()
    try:
        if intent == "web.search":
            query = str(arguments.get("query") or "").strip()
            limit = max(1, min(int(arguments.get("limit") or 3), 5))
            if not query:
                return ToolIntentExecution(
                    handled=True,
                    ok=False,
                    status="invalid_arguments",
                    response_text="Web search needs a non-empty query.",
                    mode="tool_failed",
                    tool_name=intent,
                )
            # ONE RECEIPT FOR THE WHOLE TYPED SEARCH, its internal rescue included.
            #
            # This handler had a rescue nothing recorded: when the planned search
            # came back empty it silently re-ran a raw `web.search` and reported
            # `status="executed"` from those rows, so a run whose primary search
            # found nothing was indistinguishable from one that worked. It also
            # published no `web_retrieval_receipt` at all, which is how a chat
            # turn that really did reach Brave left the Activity panel with a
            # `tool_executed` row naming no provider and no sources.
            #
            # The receipt spans both attempts because they are one retrieval of
            # one question, and it names whichever provider actually answered:
            # `finish_web_retrieval` reads `search_provider` off the rows, so a
            # rescue that ran keyless cannot inherit the keyed provider's name.
            retrieval_receipt = begin_web_retrieval(
                source_context,
                kind="web_search_tool",
                query=query,
                task_id=task_id,
                action="search",
            )
            try:
                rows = planned_search_query_fn(
                    query,
                    task_id=task_id,
                    limit=limit,
                    task_class="research",
                    source_label="web.search",
                )
                if not rows:
                    results = call_tool_fn("web.search", query=query, max_results=limit)
                    # The registry binds `web.search` to `SearXNGClient.search`
                    # (tools/registry.py), so the rescue's provider is known here
                    # and is stamped rather than left blank. A blank would let the
                    # receipt inherit the primary search's provider and report a
                    # keyless rescue under a keyed provider's name.
                    rows = [
                        dict(normalize_item(item), search_provider="searxng")
                        for item in list(results or [])[:limit]
                    ]
            except Exception as exc:
                finish_web_retrieval(source_context, retrieval_receipt, failure=exc)
                raise
            rows = _on_topic_rows(query, list(rows or []))
            finish_web_retrieval(source_context, retrieval_receipt, notes=list(rows or []))
            if not rows:
                return ToolIntentExecution(
                    handled=True,
                    ok=True,
                    status="no_results",
                    response_text=f'No live search results came back for "{query}".',
                    mode="tool_executed",
                    tool_name=intent,
                    details={
                        "query": query,
                        "result_count": 0,
                        "results": [],
                        "observation": tool_observation_fn(
                            intent=intent,
                            tool_surface="web",
                            ok=True,
                            status="no_results",
                            query=query,
                            result_count=0,
                            results=[],
                        ),
                    },
                )
            observation_results = [
                {
                    "title": str(row.get("result_title") or row.get("title") or row.get("url") or "Untitled").strip(),
                    "url": str(row.get("result_url") or row.get("url") or "").strip(),
                    "snippet": str(row.get("summary") or row.get("snippet") or "").strip()[:180],
                    "source_profile_label": str(row.get("source_profile_label") or "").strip(),
                    "origin_domain": str(row.get("origin_domain") or "").strip(),
                }
                for row in rows[:limit]
            ]
            lines = [f'Search results for "{query}":']
            for row in rows:
                title = str(row.get("result_title") or row.get("title") or row.get("url") or "Untitled").strip()
                url = str(row.get("result_url") or row.get("url") or "").strip()
                snippet = str(row.get("summary") or row.get("snippet") or "").strip()
                profile_label = str(row.get("source_profile_label") or "").strip()
                line = f"- {title}"
                if url:
                    line += f" - {url}"
                if profile_label:
                    line += f" [{profile_label}]"
                lines.append(line)
                if snippet:
                    lines.append(f"  {snippet[:180]}")
            page_excerpts = _read_shopping_hits(
                query, observation_results, source_context=source_context, call_tool_fn=call_tool_fn
            )
            for page in page_excerpts:
                lines.append(f"Read from {page['title']} - {page['url']}:")
                lines.extend(f"  {line}" for line in page["priced_lines"])
            extra = {"page_excerpts": page_excerpts} if page_excerpts else {}
            return ToolIntentExecution(
                handled=True,
                ok=True,
                status="executed",
                response_text="\n".join(lines),
                mode="tool_executed",
                tool_name=intent,
                details={
                    "query": query,
                    "result_count": len(rows),
                    "results": observation_results,
                    "observation": tool_observation_fn(
                        intent=intent,
                        tool_surface="web",
                        ok=True,
                        status="executed",
                        query=query,
                        result_count=len(rows),
                        results=observation_results,
                        **extra,
                    ),
                    **extra,
                },
            )

        if intent == "web.fetch":
            url = str(arguments.get("url") or "").strip()
            timeout_s = float(arguments.get("timeout_s") or 15.0)
            if not url:
                return ToolIntentExecution(
                    handled=True,
                    ok=False,
                    status="invalid_arguments",
                    response_text="Web fetch needs a URL.",
                    mode="tool_failed",
                    tool_name=intent,
                )
            from core.request_trust import turn_is_remote

            # A turn from another device reads public addresses only (core.remote_turn_fetch).
            if turn_is_remote(source_context):
                result = call_tool_fn("web.fetch", url=url, timeout_s=timeout_s, public_only=True)
            else:
                result = call_tool_fn("web.fetch", url=url, timeout_s=timeout_s)
            status = str(result.get("status") or "unknown").strip()
            text = str(result.get("text") or "").strip()
            preview = text[:500] if text else ""
            lines = [f"Fetched {url}", f"- Status: {status}"]
            if preview:
                lines.append(f"- Preview: {preview}")
            return ToolIntentExecution(
                handled=True,
                ok=status == "ok",
                status="executed" if status == "ok" else status,
                response_text="\n".join(lines),
                mode="tool_executed" if status == "ok" else "tool_failed",
                tool_name=intent,
                details={
                    "url": url,
                    "fetch_status": status,
                    "text_preview": preview,
                    "observation": tool_observation_fn(
                        intent=intent,
                        tool_surface="web",
                        ok=status == "ok",
                        status="executed" if status == "ok" else status,
                        url=url,
                        fetch_status=status,
                        text_preview=preview,
                    ),
                },
            )

        if intent == "web.research":
            query = str(arguments.get("query") or "").strip()
            if not query:
                return ToolIntentExecution(
                    handled=True,
                    ok=False,
                    status="invalid_arguments",
                    response_text="Web research needs a non-empty query.",
                    mode="tool_failed",
                    tool_name=intent,
                )
            research_result = adaptive_research_fn(
                task_id=task_id,
                user_input=query,
                classification={"task_class": "research"},
                interpretation=SimpleNamespace(topic_hints=[], understanding_confidence=0.82),
                source_context=dict(source_context or {"surface": "openclaw", "platform": "openclaw"}),
            )
            observation_hits = [
                {
                    "title": str(row.get("result_title") or row.get("title") or row.get("result_url") or "Untitled").strip(),
                    "url": str(row.get("result_url") or row.get("url") or "").strip(),
                    "snippet": str(row.get("summary") or row.get("snippet") or "").strip()[:180],
                    "domain": str(row.get("origin_domain") or "").strip(),
                }
                for row in list(research_result.notes or [])[:5]
            ]
            lines = [f'Adaptive web research for "{query}":']
            if research_result.actions_taken:
                lines.append("- Actions: " + ", ".join(research_result.actions_taken))
            if research_result.queries_run:
                lines.append("- Queries: " + " | ".join(research_result.queries_run[:3]))
            for row in observation_hits[:3]:
                line = f"- {row['title']}"
                if row["url"]:
                    line += f" - {row['url']}"
                if row["domain"]:
                    line += f" [{row['domain']}]"
                lines.append(line)
                if row["snippet"]:
                    lines.append(f"  {row['snippet']}")
            if research_result.admitted_uncertainty:
                lines.append(f"- Uncertainty: {research_result.uncertainty_reason}")
            elif research_result.stop_reason:
                lines.append(f"- Stop reason: {research_result.stop_reason}")
            # With nothing found, the person sees a plain sentence, never a bare header (0.7.8 sweep
            # block 2: "check wikipedia: what year was the eifel tower completed" showed only
            # 'Adaptive web research for "Eiffel Tower completion year":').
            user_lines = (
                list(lines)
                if observation_hits
                else [f"I searched the web for “{query}” and found no usable pages, so I can't answer from a source. "
                      "Try different words, or ask me to answer from what I already know."]
            )
            return ToolIntentExecution(
                handled=True,
                ok=bool(observation_hits),
                status="executed" if observation_hits else "no_results",
                response_text="\n".join(lines if observation_hits else user_lines + lines[1:]),
                user_safe_response_text="\n".join(user_lines),
                mode="tool_executed" if observation_hits else "tool_failed",
                tool_name=intent,
                details={
                    "query": query,
                    "strategy": research_result.strategy,
                    "actions_taken": list(research_result.actions_taken),
                    "queries_run": list(research_result.queries_run),
                    "evidence_strength": truthful_evidence_strength(research_result),
                    "uncertainty_reason": research_result.uncertainty_reason,
                    "hit_count": len(observation_hits),
                    "hits": observation_hits,
                    "observation": tool_observation_fn(
                        intent=intent,
                        tool_surface="web",
                        ok=bool(observation_hits),
                        status="executed" if observation_hits else "no_results",
                        query=query,
                        strategy=research_result.strategy,
                        actions_taken=list(research_result.actions_taken),
                        queries_run=list(research_result.queries_run),
                        evidence_strength=truthful_evidence_strength(research_result),
                        admitted_uncertainty=research_result.admitted_uncertainty,
                        uncertainty_reason=research_result.uncertainty_reason,
                        stop_reason=research_result.stop_reason,
                        hit_count=len(observation_hits),
                        hits=observation_hits,
                    ),
                },
            )

        if intent == "browser.render":
            url = str(arguments.get("url") or "").strip()
            if not url:
                return ToolIntentExecution(
                    handled=True,
                    ok=False,
                    status="invalid_arguments",
                    response_text="Browser render needs a URL.",
                    mode="tool_failed",
                    tool_name=intent,
                )
            result = call_tool_fn("browser.render", url=url)
            status = str(result.get("status") or "unknown").strip()
            final_url = str(result.get("final_url") or url).strip()
            title = str(result.get("title") or "").strip()
            text = str(result.get("text") or "").strip()
            lines = [f"Rendered {final_url}", f"- Status: {status}"]
            if title:
                lines.append(f"- Title: {title}")
            if text:
                lines.append(f"- Preview: {text[:240]}")
            return ToolIntentExecution(
                handled=True,
                ok=status == "ok",
                status="executed" if status == "ok" else status,
                response_text="\n".join(lines),
                mode="tool_executed" if status == "ok" else "tool_failed",
                tool_name=intent,
                details={
                    "url": url,
                    "final_url": final_url,
                    "render_status": status,
                    "title": title,
                    "text_preview": text[:240] if text else "",
                    "observation": tool_observation_fn(
                        intent=intent,
                        tool_surface="web",
                        ok=status == "ok",
                        status="executed" if status == "ok" else status,
                        url=url,
                        final_url=final_url,
                        render_status=status,
                        title=title,
                        text_preview=text[:240] if text else "",
                    ),
                },
            )
    except Exception as exc:
        audit_log_fn(
            "tool_intent_execution_error",
            target_id=task_id,
            target_type="task",
            details={"intent": intent, "arguments": arguments, "error": str(exc)},
        )
        return ToolIntentExecution(
            handled=True,
            ok=False,
            status="execution_failed",
            response_text=f"I tried `{intent}` but the tool failed: {exc}",
            mode="tool_failed",
            tool_name=intent,
            details={"error": str(exc)},
        )

    return unsupported_execution_for_intent_fn(intent, status="unsupported")
