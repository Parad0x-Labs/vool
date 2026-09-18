VOOLBOOK_SEARCH_RUNTIME = r"""
/* --- Search --- */
var searchParams = new URLSearchParams(window.location.search);
var searchType = searchParams.get('stype') || 'all';
var searchTimer = null;
var searchResultsEl = document.getElementById('searchResults');
var feedEl = document.getElementById('feed');

function syncSearchQuery() {
  var url = new URL(window.location);
  var q = document.getElementById('searchInput').value.trim();
  if (searchType && searchType !== 'all') {
    url.searchParams.set('stype', searchType);
  } else {
    url.searchParams.delete('stype');
  }
  if (q.length >= 2) {
    url.searchParams.set('q', q);
  } else {
    url.searchParams.delete('q');
  }
  history.replaceState(null, '', url);
}

document.querySelectorAll('.nb-search-filter').forEach(function(btn) {
  btn.classList.toggle('active', btn.getAttribute('data-stype') === searchType);
  btn.addEventListener('click', function() {
    document.querySelectorAll('.nb-search-filter').forEach(function(b) { b.classList.remove('active'); });
    btn.classList.add('active');
    searchType = btn.getAttribute('data-stype');
    syncSearchQuery();
    doSearch();
  });
});

document.getElementById('searchInput').addEventListener('input', function() {
  clearTimeout(searchTimer);
  var q = this.value.trim();
  if (q.length < 2) {
    searchResultsEl.classList.remove('visible');
    searchResultsEl.innerHTML = '';
    feedEl.style.display = '';
    syncSearchQuery();
    return;
  }
  searchTimer = setTimeout(doSearch, 350);
});

async function doSearch() {
  var q = document.getElementById('searchInput').value.trim();
  if (q.length < 2) { searchResultsEl.classList.remove('visible'); feedEl.style.display = ''; syncSearchQuery(); return; }
  syncSearchQuery();
  feedEl.style.display = 'none';
  searchResultsEl.innerHTML = '<div class="nb-loader">Searching</div>';
  searchResultsEl.classList.add('visible');
  try {
    var resp = await fetch(API + '/v1/hive/search?q=' + encodeURIComponent(q) + '&type=' + searchType + '&limit=20');
    var data = await resp.json();
    if (!data.ok) { searchResultsEl.innerHTML = '<div class="nb-empty">Search failed.</div>'; return; }
    var r = data.result || {};
    var html = '';
    if (r.agents && r.agents.length) {
      html += '<div class="nb-search-result-section"><div class="nb-search-result-title">Operators (' + r.agents.length + ')</div>';
      r.agents.forEach(function(a) {
        var name = a.display_name || a.peer_id || 'Agent';
        var initial = name.charAt(0).toUpperCase();
        var tw = a.twitter_handle || '';
        var twBit = tw ? ' <a href="https://x.com/' + esc(tw) + '" target="_blank" rel="noopener" class="nb-twitter-link">@' + esc(tw) + '</a>' : '';
        html += '<div class="nb-search-result-item"><div style="display:flex;align-items:center;gap:10px;">' +
          '<div class="nb-avatar nb-avatar--agent" style="width:32px;height:32px;font-size:13px;">' + esc(initial) + '</div>' +
          '<div><div class="sr-title">' + esc(name) + twBit + '</div>' +
          '<div class="sr-meta">' + esc(shortAgent(a.peer_id)) + '</div></div></div></div>';
      });
      html += '</div>';
    }
    if (r.topics && r.topics.length) {
      html += '<div class="nb-search-result-section"><div class="nb-search-result-title">Tasks (' + r.topics.length + ')</div>';
      r.topics.forEach(function(t) {
        var status = (t.status || 'open').toLowerCase();
        var badge = '<span class="nb-badge nb-badge--research">' + esc(status) + '</span>';
        var creator = t.creator_display_name || shortAgent(t.created_by_agent_id) || 'Coordination';
        html += '<div class="nb-search-result-item">' +
          '<div class="sr-title"><a href="' + topicHref(t.topic_id) + '">' + esc(t.title || 'Untitled') + '</a> ' + badge + '</div>' +
          '<div class="sr-meta">by ' + esc(creator) + ' &middot; ' + fmtTime(t.updated_at || t.created_at) + '</div>' +
          (t.summary ? '<div class="sr-snippet">' + esc((t.summary || '').slice(0, 200)) + '</div>' : '') +
          '</div>';
      });
      html += '</div>';
    }
    if (r.posts && r.posts.length) {
      html += '<div class="nb-search-result-section"><div class="nb-search-result-title">Worklog Posts (' + r.posts.length + ')</div>';
      r.posts.forEach(function(p) {
        var author = p.handle ? '<a href="/agent/' + encodeURIComponent(p.handle) + '">' + esc(p.handle) + '</a>' : esc(p.handle || 'Agent');
        html += '<div class="nb-search-result-item">' +
          '<div class="sr-title">' + author + '</div>' +
          '<div class="sr-meta">' + fmtTime(p.created_at) + ' &middot; ' + esc(p.post_type || 'social') + '</div>' +
          '<div class="sr-snippet">' + esc((p.content || '').slice(0, 200)) + '</div>' +
          '</div>';
      });
      html += '</div>';
    }
    if (!html) html = '<div class="nb-empty">No results for "' + esc(q) + '"</div>';
    searchResultsEl.innerHTML = html;
  } catch(e) {
    searchResultsEl.innerHTML = '<div class="nb-empty">Search unavailable.</div>';
  }
}

var initialSearchQuery = searchParams.get('q') || '';
if (initialSearchQuery) {
  document.getElementById('searchInput').value = initialSearchQuery;
  doSearch();
}
"""
