from __future__ import annotations

from typing import Any

from core.agent_runtime.fast_live_info_news_rendering import render_news_response
from core.agent_runtime.fast_live_info_quote_rendering import all_live_quotes, first_live_quote
from core.agent_runtime.fast_live_info_weather_rendering import render_weather_response


def _canonical_crypto_id(alias: str) -> str:
    """The CoinGecko id a crypto alias resolves to, e.g. `bnb` -> `binancecoin`.

    A live crypto quote's `asset_name`/`symbol` are both derived from this id (see
    `tools.web.web_research._crypto_price_fallback`), not from the ticker the operator typed --
    `binancecoin` contains no `bnb`, `ripple` contains no `xrp`, `cardano` contains no `ada`,
    `avalanche-2` contains no `avax`, and plain `bitcoin` contains no `btc`. The substring check in
    `_asset_is_served` alone would report every one of those as missing right next to its own
    price. Lazy import: this module already lives under `core.agent_runtime`, and importing the
    web-tools module up front would risk a cycle for no benefit the rest of the year.
    """

    try:
        from tools.web.web_research import _CRYPTO_ALIASES
    except Exception:
        return ""
    return str(_CRYPTO_ALIASES.get(str(alias or "").strip().lower(), "") or "").replace("-", " ")


def _asset_is_served(alias: str, served: set[str]) -> bool:
    """Whether a quote in hand covers the asset the operator named.

    An alias and a quote's identifier are two spellings of one thing (`btc` / `Bitcoin` / `BTC`),
    so equality on either side counts, and so does one containing the other — `brent` against
    `brent crude oil`. Nothing here decides WHAT to fetch; it only decides whether to claim
    something was missed, and the cost of being wrong is a false sentence next to a correct one.
    """

    name = str(alias or "").strip().lower()
    if not name:
        return True
    if any(name == value or name in value or value in name for value in served):
        return True
    canonical = _canonical_crypto_id(name)
    return bool(canonical) and any(canonical == value or canonical in value for value in served)


def quotes_as_table(quotes: list[Any]) -> str:
    """Several live quotes as a markdown table, one row per asset.

    Stacked sentences make the reader align the numbers themselves. A comparison -- which is what
    asking for several assets at once IS -- reads as rows. Every column the quotes actually carry is
    kept: dropping the source or the observation time to make a tidier table would trade the two
    things that make a live number checkable for cosmetics.

    Columns that no quote in the set fills are omitted rather than left as a wall of dashes, so a
    crypto-only set does not carry an empty unit column and a metals set does not carry an empty
    24h column.
    """

    rows: list[dict[str, str]] = []
    for quote in quotes:
        price = f"{quote._price_text()}"
        if getattr(quote, "unit_label", ""):
            price = f"{price} {quote.unit_label}"
        change = ""
        if getattr(quote, "change_percent", None) is not None:
            window = str(getattr(quote, "change_window", "") or "").strip()
            change = f"{quote.change_percent:+.2f}%" + (f" ({window})" if window else "")
        source = str(getattr(quote, "source_label", "") or "")
        url = str(getattr(quote, "source_url", "") or "")
        rows.append({
            "Asset": str(getattr(quote, "asset_name", "") or ""),
            "Price": price,
            "Change": change,
            "As of": str(getattr(quote, "as_of", "") or ""),
            "Source": f"[{source}]({url})" if source and url else source,
        })
    if not rows:
        return ""
    columns = [name for name in ("Asset", "Price", "Change", "As of", "Source")
               if any(row.get(name) for row in rows)]
    header = "| " + " | ".join(columns) + " |"
    divider = "| " + " | ".join("---" for _ in columns) + " |"
    body = ["| " + " | ".join(row.get(name, "") or "-" for name in columns) + " |" for row in rows]
    return "\n".join([header, divider, *body])


def render_live_info_response(*, query: str, notes: list[dict[str, Any]], mode: str) -> str:
    if mode == "weather":
        return render_weather_response(query=query, notes=notes)
    if mode == "news":
        return render_news_response(query=query, notes=notes)
    live_quote = first_live_quote(notes)
    if mode == "fresh_lookup" and live_quote is not None:
        # EVERY quote, not the first one. "btc price now? and sol price please" returned Bitcoin
        # alone and said nothing about Solana, while `sol price now?` on its own answered
        # correctly - so the lookup could do both and the render threw one away.
        quotes = all_live_quotes(notes)
        # Several quotes are a comparison, and a comparison reads as a table. Three stacked
        # sentences ("Gold is $4,210.00 ... Silver is $61.48 ... Binancecoin is $596.10 ...") make
        # the reader do the alignment themselves; the same three as rows are scannable at a glance.
        # One quote stays a sentence -- a one-row table is worse than the sentence it replaces.
        answer = (
            quotes_as_table(quotes)
            if len(quotes) > 1
            else ("\n\n".join(quote.answer_text() for quote in quotes) if quotes else live_quote.answer_text())
        )
        # ...and an asset the operator named that no quote covers is SAID, not dropped. Answering
        # for one of two silently is indistinguishable from the operator having asked for one.
        # Deferred: `fast_live_info_price` already imports this module's renderer, so a top-level
        # import here is a cycle. Same guarded-lookup shape the rest of the runtime uses.
        from core.agent_runtime.fast_live_info_price import price_assets_named

        # Same authorization level the fetch lane used: this renderer is
        # rendering LIVE-QUOTE results, so the domain evidence is this lane
        # itself. Deriving `asked` without the flag blinded the missing-asset
        # note to exactly the assets whose anchor sat outside the per-mention
        # window -- the drop the note exists to surface went unreported too.
        asked = price_assets_named(query, domain_already_authorized=True)
        if len(asked) > 1:
            # Compare against the quotes' OWN identifiers, not against their rendered prose. The
            # first version of this tested `"btc" in "bitcoin is $63,791..."` — and `btc` is not a
            # substring of `bitcoin`, so an asset that WAS answered got reported missing in the
            # same breath as its own price. A note that contradicts the line above it is worse
            # than no note.
            served = set()
            for quote in (quotes or [live_quote]):
                for field in ("asset_name", "symbol", "asset", "name"):
                    value = str(getattr(quote, field, "") or "").strip().lower()
                    if value:
                        served.add(value)
            missing = [name for name in asked if not _asset_is_served(name, served)]
            if missing:
                answer += (
                    "\n\nNo live quote came back for "
                    + ", ".join(f"`{name}`" for name in missing)
                    + " on this lookup - ask again for it on its own and I will retry."
                )
        return answer
    label = {
        "news": "Live news results",
        "fresh_lookup": "Live web results",
    }.get(mode, "Live web results")
    lines = [f"{label} for `{query}`:"]
    browser_used = False
    for note in list(notes or [])[:3]:
        title = str(note.get("result_title") or note.get("origin_domain") or "Source").strip()
        domain = str(note.get("origin_domain") or "").strip()
        snippet = " ".join(str(note.get("summary") or "").split()).strip()
        url = str(note.get("result_url") or "").strip()
        line = f"- {title}"
        if domain and domain.lower() not in title.lower():
            line += f" ({domain})"
        if snippet:
            line += f": {snippet[:220]}"
        if url:
            line += f" [{url}]"
        lines.append(line)
        browser_used = browser_used or bool(note.get("used_browser"))
    if browser_used:
        lines.append("Browser rendering was used for at least one source when plain fetch was too thin.")
    return "\n".join(lines)
