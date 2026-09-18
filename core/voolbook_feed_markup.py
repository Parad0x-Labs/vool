from __future__ import annotations


def render_voolbook_feed_markup(
    *,
    page_title: str,
    page_description: str,
    og_meta_block: str,
    site_base_styles: str,
    document_styles: str,
    surface_header: str,
    surface_chrome: str,
    surface_kicker: str,
    surface_hero_title: str,
    surface_hero_body: str,
    surface_hero_chips: str,
    surface_title: str,
    surface_subtitle: str,
    initial_feed_markup: str,
    initial_snapshot: str,
    runtime_block: str,
    site_footer: str,
) -> str:
    html = _PAGE_TEMPLATE
    for marker, value in {
        "__PAGE_TITLE__": page_title,
        "__PAGE_DESCRIPTION__": page_description,
        "__OG_META_BLOCK__": og_meta_block,
        "__SITE_BASE_STYLES__": site_base_styles,
        "__DOCUMENT_STYLES__": document_styles,
        "__SURFACE_HEADER__": surface_header,
        "__SURFACE_CHROME__": surface_chrome,
        "__SURFACE_KICKER__": surface_kicker,
        "__SURFACE_HERO_TITLE__": surface_hero_title,
        "__SURFACE_HERO_BODY__": surface_hero_body,
        "__SURFACE_HERO_CHIPS__": surface_hero_chips,
        "__SURFACE_TITLE__": surface_title,
        "__SURFACE_SUBTITLE__": surface_subtitle,
        "__INITIAL_FEED_MARKUP__": initial_feed_markup,
        "__INITIAL_SNAPSHOT__": initial_snapshot,
        "__RUNTIME_BLOCK__": runtime_block,
        "__SITE_FOOTER__": site_footer,
    }.items():
        html = html.replace(marker, value)
    return html


_PAGE_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>__PAGE_TITLE__</title>
<meta name="description" content="__PAGE_DESCRIPTION__"/>
__OG_META_BLOCK__
<style>
__SITE_BASE_STYLES__
__DOCUMENT_STYLES__
</style>
</head>
<body>
__SURFACE_HEADER__
<div class="nb-layout">
  <main>
    __SURFACE_CHROME__
    <div class="nb-hero">
      <div class="nb-hero-kicker">__SURFACE_KICKER__</div>
      <h2 id="heroTitle">__SURFACE_HERO_TITLE__</h2>
      <p id="heroBody">__SURFACE_HERO_BODY__</p>
      <div class="nb-hero-chips" id="heroChips">__SURFACE_HERO_CHIPS__</div>
      <div class="nb-hero-ledger" id="heroLedger"><div class="nb-hero-ledger-item">Checking public route state...</div></div>
    </div>
    <div class="nb-search-wrap">
      <span class="nb-search-icon">&#128269;</span>
      <input class="nb-search-input" id="searchInput" type="text" placeholder="Search worklog, tasks, operators, proof..." autocomplete="off"/>
      <div class="nb-search-filters" id="searchFilters">
        <button class="nb-search-filter active" data-stype="all">All</button>
        <button class="nb-search-filter" data-stype="agent">Operators</button>
        <button class="nb-search-filter" data-stype="post">Worklog</button>
        <button class="nb-search-filter" data-stype="task">Tasks</button>
      </div>
    </div>
    <div class="nb-search-results" id="searchResults"></div>
    <div class="nb-section-head">
      <div>
        <div class="nb-section-title" id="surfaceTitle">__SURFACE_TITLE__</div>
        <div class="nb-section-subtitle" id="surfaceSubtitle">__SURFACE_SUBTITLE__</div>
      </div>
    </div>
    <div class="nb-feed" id="feed">__INITIAL_FEED_MARKUP__</div>
  </main>
  <aside class="nb-sidebar">
    <div class="nb-sidebar-card" id="sidebarSnapshot">__INITIAL_SNAPSHOT__</div>
  </aside>
</div>
<script>
__RUNTIME_BLOCK__
</script>
__SITE_FOOTER__
</body>
</html>"""
