"""Own-post observations and bounded unanswered replies, through the existing X read door."""
from __future__ import annotations

import json
import math
import os
import tempfile
import time
from datetime import datetime
from typing import Any

from core import x_api
from core.cross_process_lock import LockUnavailable, PublicationLock
from core.runtime_paths import data_path

METRICS = {'views': 'impression_count', 'likes': 'like_count', 'replies': 'reply_count',
           'reposts': 'retweet_count', 'quotes': 'quote_count', 'bookmarks': 'bookmark_count'}
MAX_POSTS = 10
MAX_SNAPSHOTS = 48
MAX_STORED_POSTS = 200


def _account() -> dict:
    saved = x_api._saved()
    account = saved.get('account') or {}
    if not isinstance(account, dict) or not str(account.get('id') or '').isdigit():
        raise x_api.XReadError('needs_account', 'Connect your own X account first. Nothing was read.')
    return account


def _load() -> dict:
    path = data_path('x_results.json')
    if not path.exists():
        return {'accounts': {}}
    state = json.loads(path.read_text())
    if not isinstance(state, dict) or not isinstance(state.get('accounts'), dict):
        raise ValueError('unreadable results')
    for aid, posts in state['accounts'].items():
        if not isinstance(aid, str) or not aid.isdigit() or not isinstance(posts, dict) or len(posts) > MAX_STORED_POSTS:
            raise ValueError('invalid results account')
        for pid, rows in posts.items():
            if not isinstance(pid, str) or not pid.isdigit() or not isinstance(rows, list) or not rows or len(rows) > MAX_SNAPSHOTS:
                raise ValueError('invalid snapshots')
            for row in rows:
                if not isinstance(row, dict) or not isinstance(row.get('metrics'), dict) or set(row['metrics']) != set(METRICS):
                    raise ValueError('invalid metrics')
                if set(row) != {'post_id', 'captured_at', 'created_at', 'age_seconds', 'label', 'metrics'} or row['post_id'] != pid or row['label'] != 'observed':
                    raise ValueError('invalid observation')
                if any(type(row.get(k)) not in (int, float) or not math.isfinite(row[k]) or row[k] < 0 for k in ('captured_at', 'created_at', 'age_seconds')):
                    raise ValueError('invalid observation time')
                if any(v is not None and (type(v) is not int or v < 0) for v in row['metrics'].values()):
                    raise ValueError('invalid metric count')
    return state


def _save(state: dict) -> None:
    path = data_path('x_results.json')
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix='.x-results-')
    try:
        with os.fdopen(fd, 'w') as out:
            json.dump(state, out)
            out.flush()
            os.fsync(out.fileno())
        os.replace(name, path)
    finally:
        from pathlib import Path

        Path(name).unlink(missing_ok=True)


def _write(account_id: str, post_id: str, snapshot: dict | None) -> list[dict]:
    path = data_path('x_results.json')
    with PublicationLock(path.with_suffix('.lock')):
        state = _load()
        posts = state['accounts'].setdefault(account_id, {})
        if snapshot is None:
            posts.pop(post_id, None)
            rows = []
        else:
            rows = posts.setdefault(post_id, [])
            rows.append(snapshot)
            posts[post_id] = rows = rows[-MAX_SNAPSHOTS:]
            # Bound disk retention without retaining any copied X text.
            for old in sorted(posts, key=lambda pid: posts[pid][-1]['captured_at'])[:-MAX_STORED_POSTS]:
                posts.pop(old, None)
        _save(state)
        return list(rows)


def _observation(post: dict, now: float) -> dict:
    created_at = str(post.get('created_at') or '')
    parsed = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('post time needs timezone')
    created = parsed.timestamp()
    if created > now or not post.get('author_id'):
        raise ValueError('invalid post age')
    metrics = post.get('public_metrics')
    if not isinstance(metrics, dict):
        metrics = {}
    # A missing metric is unavailable. Never coerce None, bool or a string to zero.
    numbers = {}
    for name, wire in METRICS.items():
        value = metrics.get(wire)
        numbers[name] = value if type(value) is int and value >= 0 else None
    return {'post_id': str(post['id']), 'captured_at': now, 'created_at': created,
            'age_seconds': round(now-created), 'label': 'observed', 'metrics': numbers}


def _comparable(posts: list[dict]) -> list[dict]:
    pairs = []
    for i, first in enumerate(posts):
        for second in posts[i+1:]:
            # An equal integer age, not an arbitrarily rounded bucket such as "today".
            for left in first['snapshots']:
                right = next((r for r in second['snapshots'] if r['age_seconds'] == left['age_seconds']), None)
                if right:
                    pairs.append({'post_ids': [first['post_id'], second['post_id']],
                                  'age_seconds': left['age_seconds'], 'label': 'observed at equal ages',
                                  'metrics': [left['metrics'], right['metrics']]})
                    break
    return pairs[:20]


def results(posts: Any = None) -> x_api.XResult:
    """Refresh up to ten own posts. Missing/deleted observations never become zero."""
    try:
        account = _account()
        account_id = str(account['id'])
        with PublicationLock(data_path('x_results.lock')):
            _load()  # unreadable history refuses before a paid read
        if posts is None or posts == []:
            # Refresh retained posts too, so a later confirmed deletion erases their snapshots.
            with PublicationLock(data_path('x_results.lock')):
                stored = _load()['accounts'].get(account_id, {})
            from core import x_actions

            with x_actions._lock:
                actions = x_actions._current()["actions"].values()
                sent = [str((a.get("result") or {}).get("post_id")) for a in actions
                        if a.get("kind") in {"post", "reply", "quote"} and a.get("state") == "sent"
                        and (a.get("account") or {}).get("id") == account_id
                        and (a.get("result") or {}).get("post_id")]
            ids = list(dict.fromkeys([*stored, *sent]))[-MAX_POSTS:]
            if not ids:
                timeline = x_api.user_posts('me', max_results=10)
                if not timeline.ok:
                    return timeline
                ids = [str(p['id']) for p in timeline.data.get('posts', []) if p.get('author_id') == account_id][:MAX_POSTS]
        else:
            values = posts if isinstance(posts, list) else [posts]
            if len(values) > MAX_POSTS:
                raise x_api.XReadError('invalid_arguments', 'Choose at most ten of your posts.')
            ids = list(dict.fromkeys(x_api._post_id(value) for value in values))
        refreshed, deleted, unavailable = [], [], []
        for pid in ids:
            try:
                # Same pinned origin, auth, privacy/network controls, usage reservation and no retry.
                payload = x_api._request('/2/tweets', {'ids': pid, 'tweet.fields': 'author_id,created_at,public_metrics'},
                                         user_context=True)
            except x_api.XReadError as exc:
                if exc.status == 'not_found':
                    _write(account_id, pid, None)
                    deleted.append(pid)
                    continue
                return x_api.XResult(False, exc.status, exc.message, {'posts': refreshed, 'deleted': deleted, 'reset_at': getattr(exc, 'reset_at', None)})
            raw = payload.get('data')
            post = next((p for p in raw if isinstance(p, dict) and str(p.get('id')) == pid), None) if isinstance(raw, list) else None
            if post is None:
                confirmed = any(str(e.get('resource_id') or e.get('value')) == pid and
                                ('not-found' in str(e.get('type')) or 'Not Found' in str(e.get('title')))
                                for e in payload.get('errors', []) if isinstance(e, dict))
                if confirmed:
                    _write(account_id, pid, None)
                    deleted.append(pid)
                else:
                    unavailable.append(pid)
                continue
            if str(post.get('author_id')) != account_id:
                return x_api.XResult(False, 'not_yours', 'Results are only available for your connected account.', {})
            observation = _observation(post, time.time())
            rows = _write(account_id, pid, observation)
            refreshed.append({'post_id': pid, 'snapshots': rows,
                              'availability': {key: 'observed' if value is not None else 'not available'
                                               for key, value in observation['metrics'].items()}})
        return x_api.XResult(True, 'executed', 'Observed results for your own posts; missing metrics are not available.',
                             {'posts': refreshed, 'comparisons': _comparable(refreshed), 'deleted': deleted,
                              'unavailable': unavailable})
    except x_api.XReadError as exc:
        return x_api.XResult(False, exc.status, exc.message, {'reset_at': getattr(exc, 'reset_at', None)})
    except (ValueError, TypeError, OSError, LockUnavailable):
        return x_api.XResult(False, 'store_or_metrics_unreadable', 'Could not safely read or save these post results.', {})


def unanswered(post: str, *, next_token: str = '', max_results: int = 25) -> x_api.XResult:
    """One bounded recent-search page; no sends, no automatic proposals, no bulk authority."""
    try:
        me = str(_account()['id'])
        pid = x_api._post_id(post)
        root = x_api.post_details(pid)
        if not root.ok:
            return root
        if not root.data.get('posts') or root.data['posts'][0].get('author_id') != me:
            return x_api.XResult(False, 'not_yours', 'Choose a post from your connected X account.', {})
        size = x_api._page_size(max_results)
        payload = x_api._request('/2/tweets/search/recent', {
            'query': f'conversation_id:{pid}', 'max_results': size, 'next_token': next_token,
            'tweet.fields': x_api.POST_FIELDS, 'expansions': 'author_id', 'user.fields': x_api.USER_FIELDS})
        raw = [p for p in payload.get('data', []) if isinstance(p, dict)]
        answered = set()
        own_cursor = ""
        for _page_index in range(3):
            own = x_api._request('/2/tweets/search/recent', {
                'query': f'conversation_id:{pid} from:{me}', 'max_results': size,
                'next_token': own_cursor, 'tweet.fields': x_api.POST_FIELDS})
            for item in own.get('data', []):
                if isinstance(item, dict) and str(item.get('author_id')) == me:
                    answered.update(str(ref.get('id')) for ref in item.get('referenced_tweets', [])
                                    if isinstance(ref, dict) and ref.get('type') == 'replied_to')
            own_cursor = str((own.get('meta') or {}).get('next_token') or '')
            if not own_cursor:
                break
        if own_cursor:
            return x_api.XResult(False, 'incomplete_coverage',
                                 'The recent answer search is incomplete. No replies were labelled unanswered.',
                                 {'posts': [], 'coverage': 'incomplete'})
        from core import x_actions

        # A waiting, in-flight, uncertain or just-sent local reply must not be proposed again.
        with x_actions._lock:
            actions = x_actions._current()['actions'].values()
            answered.update(str((a.get('payload') or {}).get('post_id')) for a in actions
                            if a.get('kind') == 'reply' and (a.get('account') or {}).get('id') == me
                            and a.get('state') in {'pending', 'sending', 'unknown', 'sent'})
        candidates = [p for p in raw if str(p.get('author_id')) != me and str(p.get('id')) != pid
                      and str(p.get('conversation_id')) == pid and str(p.get('id')) not in answered
                      and any(ref.get('type') == 'replied_to' for ref in p.get('referenced_tweets', []) if isinstance(ref, dict))]
        rows = [x_api.shape_post(p, x_api._users_by_id(payload)) for p in candidates]
        return x_api.XResult(True, 'executed', 'Replies with no answer observed in this page. Review each and propose one reply at a time; each needs its own approval.',
                             {'posts': rows, 'next_token': str((payload.get('meta') or {}).get('next_token') or ''),
                              'coverage': 'candidate page only; complete own-reply search within recent seven-day window',
                              'answer_status': 'no answer observed in complete recent own-reply search'})
    except x_api.XReadError as exc:
        return x_api.XResult(False, exc.status, exc.message, {'reset_at': getattr(exc, 'reset_at', None)})
    except (OSError, ValueError, TypeError, RuntimeError):
        return x_api.XResult(False, 'store_unreadable', 'Could not safely check your replies.', {})
