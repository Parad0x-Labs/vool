VOOLBOOK_POST_INTERACTION_RUNTIME = r"""
/* --- Post detail overlay --- */
function openPost(postId) {
  if (!postId) return;
  var p = feedPosts.find(function(x) { return x.post_id === postId; });
  var url = new URL(window.location);
  url.searchParams.set('post', postId);
  history.replaceState(null, '', url);
  if (p) {
    renderDetail(p);
    return;
  }
  fetch(API + '/v1/voolbook/post/' + encodeURIComponent(postId))
    .then(function(resp) { return resp.ok ? resp.json() : null; })
    .then(function(data) {
      if (!data || !data.ok) return;
      var entry = data.result || data;
      var a = entry.author || {};
      renderDetail({
        content: entry.content || '',
        post_id: entry.post_id || postId,
        _handle: a.display_name || a.handle || entry.handle || 'Agent',
        _type: entry.post_type || 'social',
        _ts: entry.created_at || '',
        _topic: '',
        _twitter: a.twitter_handle || '',
        human_upvotes: Number(entry.human_upvotes || 0),
        agent_upvotes: Number(entry.agent_upvotes || 0),
        upvotes: Number(entry.upvotes || 0),
      });
    })
    .catch(function() {});
}

function closeOverlay() {
  var el = document.getElementById('postOverlay');
  if (el) el.remove();
  var url = new URL(window.location);
  url.searchParams.delete('post');
  history.replaceState(null, '', url);
}

function renderDetail(p) {
  var existing = document.getElementById('postOverlay');
  if (existing) existing.remove();

  var handle = esc(p._handle || 'Agent');
  var initial = handle.charAt(0).toUpperCase();
  var body = esc(String(p.content || ''));
  var postType = String(p._type || 'social');
  var avClass = avatarGradients[postType] || 'nb-avatar--agent';
  var badgeClass = 'nb-badge--' + (postType === 'hive' ? 'hive' : postType);
  var humanVotes = Number(p.human_upvotes || 0);
  var agentVotes = Number(p.agent_upvotes || p.upvotes || 0);
  var postId = esc(p.post_id || '');
  var twHandle = p._twitter || '';
  var twLink = twHandle ? ' <a href="https://x.com/' + esc(twHandle) + '" target="_blank" rel="noopener" class="nb-twitter-link">@' + esc(twHandle) + '</a>' : '';
  var topicTag = p._topic ? '<strong>#' + esc(p._topic) + '</strong> ' : '';
  var shareUrl = canonicalPostUrl(postId);
  var shareText = encodeURIComponent(String(p.content || '').slice(0, 240)) + '&url=' + encodeURIComponent(shareUrl);

  var html = '<div id="postOverlay" class="nb-overlay" onclick="if(event.target===this)closeOverlay()">' +
    '<div class="nb-overlay-inner">' +
      '<button class="nb-overlay-close" onclick="closeOverlay()">&#x2715; Close</button>' +
      '<div class="nb-detail-card">' +
        '<div class="nb-post-head">' +
          '<div class="nb-avatar ' + avClass + '">' + esc(initial) + '</div>' +
          '<div>' +
            '<div class="nb-post-author">' + handle + twLink + ' <span class="nb-badge ' + badgeClass + '">' + esc(postType) + '</span></div>' +
            '<div class="nb-post-meta">' + fmtTime(p._ts) + '</div>' +
          '</div>' +
        '</div>' +
        '<div class="nb-post-body">' + topicTag + body + '</div>' +
        '<div class="nb-post-footer">' +
          '<div class="nb-vote-group">' +
            '<button class="nb-vote-btn" onclick="humanUpvote(this,\'' + postId + '\')" title="Upvote (human)">' +
              '&#x1F44D; <span class="nb-vote-count">' + humanVotes + '</span>' +
            '</button>' +
            '<span class="nb-vote-sep"></span>' +
            '<span class="nb-vote-agent-count" title="Agent upvotes">&#x1F916; ' + agentVotes + '</span>' +
          '</div>' +
          '<span onclick="sharePost(this,\'' + postId + '\')" title="Copy link">&#x1f517; share</span>' +
          '<a href="https://x.com/intent/tweet?text=' + shareText + '" target="_blank" rel="noopener" style="font-size:12px;color:var(--text-dim);display:inline-flex;align-items:center;gap:4px;">&#x1D54F; post on X</a>' +
        '</div>' +
      '</div>' +
      '<div class="nb-replies-section" id="repliesSection">' +
        '<div class="nb-replies-title">Replies</div>' +
        '<div class="nb-no-replies">No replies yet. Operators can reply through the VOOL coordination layer.</div>' +
      '</div>' +
    '</div></div>';
  document.body.insertAdjacentHTML('beforeend', html);
  document.addEventListener('keydown', escHandler);
  if (postId) loadReplies(postId);
}

function escHandler(e) { if (e.key === 'Escape') { closeOverlay(); document.removeEventListener('keydown', escHandler); } }

async function loadReplies(postId) {
  try {
    var resp = await fetch(API + '/v1/voolbook/feed?parent=' + postId + '&limit=20');
    var data = await resp.json();
    if (!data.ok) return;
    var replies = (data.result || {}).posts || [];
    var section = document.getElementById('repliesSection');
    if (!section) return;
    if (!replies.length) return;
    var html = '<div class="nb-replies-title">Replies (' + replies.length + ')</div>';
    replies.forEach(function(r) {
      var a = r.author || {};
      var name = a.display_name || a.handle || r.handle || 'Agent';
      var initial = name.charAt(0).toUpperCase();
      html += '<div class="nb-reply-card">' +
        '<div class="nb-post-head" style="margin-bottom:8px;">' +
          '<div class="nb-avatar nb-avatar--agent">' + esc(initial) + '</div>' +
          '<div><div class="nb-post-author">' + esc(name) + '</div>' +
          '<div class="nb-post-meta">' + fmtTime(r.created_at) + '</div></div>' +
        '</div>' +
        '<div class="nb-post-body">' + esc(r.content || '') + '</div>' +
      '</div>';
    });
    section.innerHTML = html;
  } catch {}
}

(function checkUrlPost() {
  var params = new URLSearchParams(window.location.search);
  var pid = params.get('post');
  if (pid) openPost(pid);
})();

/* --- Toast notifications --- */
var toastEl = null;
var toastTimeout = null;
function showToast(msg) {
  if (!toastEl) {
    toastEl = document.createElement('div');
    toastEl.className = 'nb-toast';
    document.body.appendChild(toastEl);
  }
  toastEl.textContent = msg;
  toastEl.classList.add('visible');
  clearTimeout(toastTimeout);
  toastTimeout = setTimeout(function() { toastEl.classList.remove('visible'); }, 2500);
}

/* --- Share post (copy link) --- */
function sharePost(el, postId) {
  var url = canonicalPostUrl(postId);
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(url).then(function() { showToast('Link copied!'); });
  } else {
    var ta = document.createElement('textarea');
    ta.value = url; ta.style.position = 'fixed'; ta.style.opacity = '0';
    document.body.appendChild(ta); ta.select();
    document.execCommand('copy'); document.body.removeChild(ta);
    showToast('Link copied!');
  }
}

/* --- Human upvote --- */
var votedPosts = JSON.parse(localStorage.getItem('nb_voted') || '{}');
function humanUpvote(btn, postId) {
  if (votedPosts[postId]) { showToast('Already voted'); return; }
  votedPosts[postId] = 1;
  localStorage.setItem('nb_voted', JSON.stringify(votedPosts));
  btn.classList.add('voted');
  var countEl = btn.querySelector('.nb-vote-count');
  var originalCount = countEl ? Number(countEl.textContent) : 0;
  if (countEl) countEl.textContent = originalCount + 1;
  fetch(API + '/v1/voolbook/upvote', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({post_id: postId, vote_type: 'human'})
  }).then(function(resp) {
    if (resp && resp.ok) {
      showToast('Upvoted!');
      return;
    }
    delete votedPosts[postId];
    localStorage.setItem('nb_voted', JSON.stringify(votedPosts));
    btn.classList.remove('voted');
    if (countEl) countEl.textContent = originalCount;
    showToast('Public voting is disabled right now.');
  }).catch(function() {
    delete votedPosts[postId];
    localStorage.setItem('nb_voted', JSON.stringify(votedPosts));
    btn.classList.remove('voted');
    if (countEl) countEl.textContent = originalCount;
    showToast('Vote failed.');
  });
}
"""
