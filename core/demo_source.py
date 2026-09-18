"""Ingest a product's source (a GitHub repo README, or any markdown/docs text) into a DemoBrief.

Reads the README, then distills the product name, tagline, and feature list that core.demo_planner
turns into a timed demo-video/image prompt plan. Heuristic and deterministic (no model call needed),
so the extraction is fully unit-testable; the network is touched only via an injectable fetcher.
A model may later refine the feature list, but the heuristic gives a solid brief on its own.
"""

from __future__ import annotations

import html as _htmllib
import re

from core.demo_planner import DemoBrief, Feature
from core.remote_fetch_policy import RemoteFetchRefusedError

# H2 sections that are project scaffolding, not product features.
_META_HEADINGS = {
    "install", "installation", "usage", "getting started", "quick start", "quickstart",
    "license", "licence", "contributing", "contributors", "table of contents", "contents",
    "requirements", "setup", "documentation", "docs", "changelog", "roadmap", "credits",
    "acknowledgements", "acknowledgments", "faq", "support", "security", "development",
    "build", "tests", "testing", "api", "api reference", "examples", "example", "authors",
}
_FEATURE_HEADINGS = re.compile(
    r"\b(features|highlights|capabilities|why|benefits|what (?:it|you|makes)|what.s different)\b",
    re.IGNORECASE,
)
_LEAD_SYMBOLS = re.compile(r"^[^0-9A-Za-z]+")   # strip leading emoji/symbols from a heading


def parse_github_url(url: str) -> tuple[str, str] | None:
    """Return (owner, repo) from a GitHub URL/ssh/`owner/repo` form, else None. The host must be
    exactly github.com (anchored), so a lookalike like fakegithub.com is not treated as GitHub."""
    text = str(url or "").strip()
    match = re.search(r"(?:^|//|@)(?:www\.)?github\.com[/:]([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)", text)
    if not match:
        match = re.fullmatch(r"([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)", text)
    if not match:
        return None
    owner, repo = match.group(1), match.group(2)
    if repo.endswith(".git"):
        repo = repo[:-4]
    if not owner or not repo:
        return None
    return owner, repo


_MAX_FETCH_BYTES = 3_000_000   # cap the body so a hostile/huge page can't exhaust memory
_FETCH_DEADLINE = 30.0         # total wall-clock ceiling (the socket timeout is only per-read)


def _bounded_get(url: str, headers: dict) -> str:
    """GET with a hard byte cap AND a total-time deadline (streamed), so an oversized or trickling
    server can neither exhaust memory nor hang the call indefinitely. Opened through the ONE
    outbound door (`core.remote_fetch_policy.open_remote_url`), so a turn that forbade remote
    fetching cannot reach GitHub through the demo planner; a refusal propagates (fail closed)."""
    import time

    from core.remote_fetch_policy import open_remote_url

    start = time.monotonic()
    with open_remote_url(url, headers=dict(headers), timeout=15.0) as resp:
        collected = bytearray()
        while len(collected) < _MAX_FETCH_BYTES and (time.monotonic() - start) <= _FETCH_DEADLINE:
            chunk = resp.read(65536)
            if not chunk:
                break
            collected.extend(chunk)
        charset = "utf-8"
        try:
            charset = resp.headers.get_content_charset() or "utf-8"
        except Exception:
            pass
    return bytes(collected[:_MAX_FETCH_BYTES]).decode(charset, errors="replace")


def _default_fetcher(api_url: str) -> str:
    return _bounded_get(
        api_url, {"Accept": "application/vnd.github.raw+json", "User-Agent": "vool-demo-planner"})


def fetch_readme(owner: str, repo: str, *, fetcher=None) -> str:
    """Fetch the raw README markdown via the GitHub API (fetcher injectable for tests)."""
    f = fetcher or _default_fetcher
    return str(f(f"https://api.github.com/repos/{owner}/{repo}/readme") or "")


_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")   # [^\]]* so an empty-text link [](url) collapses to ''
_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_HTML = re.compile(r"<[^>]+>")


def _strip_md(text: str) -> str:
    out = _IMAGE.sub("", str(text or ""))
    out = _LINK.sub(r"\1", out)
    out = _HTML.sub("", out)
    out = out.replace("—", " - ").replace("–", "-")   # em/en dash -> ascii, uniform splitting
    out = out.replace("`", "").replace("**", "").replace("__", "").strip("#*_ \t")
    return " ".join(out.split())


def _humanize(repo: str) -> str:
    return " ".join(w.capitalize() for w in re.split(r"[-_.]+", str(repo or "").strip()) if w) or "This project"


def _split_bullet(text: str) -> Feature:
    body = _strip_md(re.sub(r"^\s*(?:[-*+]|\d+\.)\s+", "", text))
    # Split on the EARLIEST separator (so "Auth: sign-in - out" splits at ':' not the later ' - ').
    best_sep, best_pos = "", len(body) + 1
    for sep in (" - ", " -- ", ": ", " -> "):
        pos = body.find(sep)
        if 0 <= pos < best_pos:
            best_sep, best_pos = sep, pos
    if best_sep:
        name, blurb = body.split(best_sep, 1)
        if 2 <= len(name.strip()) <= 60:
            return Feature(name.strip().rstrip(":"), blurb.strip())
    # No separator: keep the whole bullet as the name (no arbitrary mid-sentence slice).
    return Feature(body[:80].strip(), "")


def _is_heading(line: str) -> int:
    m = re.match(r"^(#{1,6})\s+\S", line)
    return len(m.group(1)) if m else 0


def extract_brief_from_readme(
    text: str, *, repo_name: str = "", source_url: str = "",
    presenter: str = "person", total_seconds: int = 30,
) -> DemoBrief:
    """Distill product name, tagline, and up to six features from README markdown."""
    lines = str(text or "").splitlines()
    # Product name: first H1, else the humanized repo name.
    product_name = _humanize(repo_name)
    h1_index = -1
    for i, line in enumerate(lines):
        if _is_heading(line) == 1:
            name = _strip_md(line)
            if name:
                product_name = name
                h1_index = i
                break

    # Tagline: first substantive line after the H1 (skip badges/blanks/headings/rules).
    tagline = ""
    for line in lines[h1_index + 1:]:
        s = line.strip()
        if not s or _is_heading(line) or s.startswith(("![", "---", "===", "<!--", "|")):
            continue
        if s.startswith(("[!", "[![")):   # a linked badge line - skip it, not a tagline
            continue
        cleaned = _strip_md(s.lstrip(">").strip())
        if len(cleaned) >= 8:
            tagline = cleaned[:160]
            break

    features = _extract_features(lines)
    brief = DemoBrief(
        product_name=product_name, tagline=tagline, features=tuple(features[:6]),
        presenter=presenter, total_seconds=total_seconds, source_url=source_url,
    )
    return brief


def _clean_heading_name(line: str) -> str:
    """Heading text with markdown + a leading emoji/symbol stripped."""
    return _LEAD_SYMBOLS.sub("", _strip_md(line)).strip()


def _is_meta_heading(name: str) -> bool:
    low = name.lower()
    return any(word in low for word in _META_HEADINGS)


def _heading_feature(name: str, blurb: str) -> Feature:
    """Turn a heading like 'Tool-use loop - not prompt theater' into name + blurb."""
    for sep in (" - ", " -- ", ": "):
        if sep in name:
            head, tail = name.split(sep, 1)
            if 2 <= len(head.strip()) <= 60:
                return Feature(head.strip().rstrip(":"), (tail.strip() or blurb)[:140])
    return Feature(name[:70], blurb[:140])


def _first_blurb_under(lines: list[str], start: int) -> str:
    for k in range(start, len(lines)):
        s = lines[k].strip()
        if _is_heading(lines[k]):
            break
        if s and not s.startswith(("![", "|", "```", "<", "[!")):
            return _strip_md(s)[:140]
    return ""


def _extract_features(lines: list[str]) -> list[Feature]:
    # 1) A features-ish section -> its H3 subheadings and/or bullets (stops at the next same/higher
    #    heading). Handles "## What makes X different" with "### Feature" subheadings, and bullet lists.
    for i, line in enumerate(lines):
        lvl = _is_heading(line)
        if lvl and lvl <= 2 and _FEATURE_HEADINGS.search(_strip_md(line)):
            h3_feats: list[Feature] = []
            bullet_feats: list[Feature] = []
            for j in range(i + 1, len(lines)):
                nxt = lines[j]
                nlvl = _is_heading(nxt)
                if nlvl and nlvl <= lvl:                 # next top-level section ends this one
                    break
                if nlvl == 3:
                    name = _clean_heading_name(nxt)
                    if name and not _is_meta_heading(name):
                        h3_feats.append(_heading_feature(name, _first_blurb_under(lines, j + 1)))
                elif re.match(r"^(?:[-*+]|\d+\.)\s+\S", nxt):
                    bullet_feats.append(_split_bullet(nxt))
            # H3 subheadings are the features; their bullets are sub-details, so prefer H3s.
            feats = h3_feats or bullet_feats
            if feats:
                return feats

    # 2) Otherwise, H2/H3 section headings that are not scaffolding -> feature names.
    feats = []
    for i, line in enumerate(lines):
        if _is_heading(line) == 2:
            name = _clean_heading_name(line)
            if not name or _is_meta_heading(name):
                continue
            feats.append(_heading_feature(name, _first_blurb_under(lines, i + 1)))
    if feats:
        return feats

    # 3) Fall back to the first top-level bullet list anywhere.
    return [_split_bullet(line) for line in lines if re.match(r"^(?:[-*+]|\d+\.)\s+\S", line)]


def brief_from_github(
    url: str, *, fetcher=None, presenter: str = "person", total_seconds: int = 30,
) -> DemoBrief | None:
    """End-to-end: parse the GitHub URL, fetch the README, and distill a DemoBrief (or None)."""
    parsed = parse_github_url(url)
    if parsed is None:
        return None
    owner, repo = parsed
    try:
        readme = fetch_readme(owner, repo, fetcher=fetcher)
    except RemoteFetchRefusedError:
        raise   # a turn veto is not "no brief found"; fail closed, loudly
    except Exception:
        return None
    if not readme.strip():
        return None
    return extract_brief_from_readme(
        readme, repo_name=repo, source_url=url, presenter=presenter, total_seconds=total_seconds
    )


# script/style/noscript hold no body text; drop them even when the close tag is missing (\Z), so a
# truncated page can't leak raw JS/CSS. Structural wrappers keep the close-tag-required form so an
# unclosed one doesn't swallow the whole document.
_DROP_TO_END = re.compile(r"(?is)<(script|style|noscript)[^>]*>.*?(?:</\1>|\Z)")
_DROP_BLOCK = re.compile(r"(?is)<(nav|footer|header|head|title|aside)[^>]*>.*?</\1>")


def _html_to_text(html: str) -> str:
    """Convert an HTML docs page into heading/paragraph/bullet text the extractor can read."""
    s = _DROP_TO_END.sub(" ", str(html or ""))
    s = _DROP_BLOCK.sub(" ", s)
    s = re.sub(r"(?i)<h1[^>]*>", "\n# ", s)
    s = re.sub(r"(?i)<h2[^>]*>", "\n## ", s)
    s = re.sub(r"(?i)<h3[^>]*>", "\n### ", s)
    s = re.sub(r"(?i)<li[^>]*>", "\n- ", s)
    s = re.sub(r"(?i)<br\s*/?>", "\n", s)
    s = re.sub(r"(?i)</(p|div|section|article|ul|ol|h[1-6]|li|tr)>", "\n", s)
    s = _HTML.sub("", s)
    s = _htmllib.unescape(s)
    lines = [" ".join(line.split()) for line in s.splitlines()]
    return "\n".join(line for line in lines if line)


def _default_page_fetcher(url: str) -> str:
    return _bounded_get(url, {"User-Agent": "vool-demo-planner"})


def fetch_page(url: str, *, fetcher=None) -> str:
    """Fetch a web page's raw HTML (fetcher injectable for tests)."""
    return str((fetcher or _default_page_fetcher)(url) or "")


def brief_from_url(
    url: str, *, fetcher=None, presenter: str = "person", total_seconds: int = 30,
) -> DemoBrief | None:
    """Distill a DemoBrief from any source URL: a GitHub repo (reads the README) or a docs/website
    page (reads the HTML). ``fetcher`` matches the resolved path (README API vs page HTML)."""
    if parse_github_url(url) is not None:
        return brief_from_github(url, fetcher=fetcher, presenter=presenter, total_seconds=total_seconds)
    try:
        page_html = fetch_page(url, fetcher=fetcher)
    except RemoteFetchRefusedError:
        raise   # a turn veto is not "no brief found"; fail closed, loudly
    except Exception:
        return None
    text = _html_to_text(page_html)
    if not text.strip():
        return None
    return extract_brief_from_readme(
        text, source_url=url, presenter=presenter, total_seconds=total_seconds
    )
