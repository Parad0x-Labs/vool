"""Read X with the user's own keys: post search, token callers, accounts, timelines, lists.

Everything here reads with the keys the user connected in Settings (``core.x_setup`` slot
``x.api.default``). Nothing here posts, likes, follows or changes anything on X; actions that act
as the user live in ``core.x_actions`` and wait for the user's approval in the app.

* One pinned origin (``https://api.x.com``), one outbound door (``open_remote_url``): Local Only
  and the per-turn veto apply, and keys never follow a redirect.
* Public reads use the Bearer Token when one is saved; reads that need "you" (home timeline, the
  connected account) are signed with the account keys (OAuth 1.0a).
* X bills reads on the user's account, so every request is counted locally (``core.x_usage``) and
  refused BEFORE it is sent once the user's monthly limit is reached. The page size is clamped to
  what is left under the limit.
* Every failure is a structured result with a plain message, never a raised exception.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode

from core import x_usage

API_ORIGIN = "https://api.x.com"
REQUEST_TIMEOUT_S = 20.0
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
MAX_PAGE = 100

POST_FIELDS = "created_at,public_metrics,author_id,conversation_id,entities,lang,referenced_tweets,in_reply_to_user_id"
USER_FIELDS = "username,name,created_at,description,public_metrics,verified,protected,location,url"
LIST_FIELDS = "name,description,follower_count,member_count,private,owner_id,created_at"

_HANDLE_RE = re.compile(r"@?([A-Za-z0-9_]{1,15})")
_ID_RE = re.compile(r"[0-9]{1,25}")
_POST_URL_RE = re.compile(r"(?:https?://)?(?:www\.|mobile\.)?(?:x|twitter)\.com/[A-Za-z0-9_]{1,15}/status(?:es)?/([0-9]{1,25})")
EVM_ADDRESS_RE = re.compile(r"0x[a-fA-F0-9]{40}")
SOLANA_ADDRESS_RE = re.compile(r"[1-9A-HJ-NP-Za-km-z]{32,44}")
CASHTAG_RE = re.compile(r"\$([A-Za-z][A-Za-z0-9_]{0,14})")
_ADDRESS_IN_TEXT_RE = re.compile(r"(?<![A-Za-z0-9])(0x[a-fA-F0-9]{40}|[1-9A-HJ-NP-Za-km-z]{32,44})(?![A-Za-z0-9])")


@dataclass
class XResult:
    ok: bool
    status: str
    message: str
    data: dict[str, Any] = field(default_factory=dict)


class XReadError(Exception):
    def __init__(self, status: str, message: str, *, http_status: int | None = None):
        super().__init__(message)
        self.status, self.message, self.http_status = status, message, http_status


# --------------------------------------------------------------------------- credentials


def _saved() -> dict[str, Any]:
    from core.credential_store import get_credential

    raw = get_credential("x.api.default")
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except ValueError:
        data = None
    return data if isinstance(data, dict) else {"bearer_token": str(raw).strip()}


def connected() -> bool:
    """Whether the user connected X keys (disk index only: no key read, no prompt)."""
    try:
        from core.credential_store import credential_is_indexed

        return bool(credential_is_indexed("x.api.default"))
    except Exception:
        return False


def _auth(saved: dict[str, Any], method: str, url: str, *, user_context: bool) -> tuple[str, tuple[str, ...]]:
    from core.x_setup import FIELDS, OAuth1Keys, oauth1_authorization

    token = str(saved.get("bearer_token") or "").strip()
    has_keys = all(isinstance(saved.get(name), str) and saved.get(name) for name in FIELDS)
    if user_context or (not token and has_keys):
        if not has_keys:
            raise XReadError(
                "needs_account",
                "This needs your X account connected (the four account keys in Settings > API Keys > X). "
                "Nothing was sent to X.",
            )
        keys = OAuth1Keys(**{name: saved[name] for name in FIELDS})
        return oauth1_authorization(method, url, keys), tuple(saved[name] for name in FIELDS)
    if not token:
        raise XReadError(
            "needs_credentials",
            "X is not connected. Add your X keys in Settings > API Keys > X first. Nothing was sent to X.",
        )
    return f"Bearer {token}", (token,)


def _http_failure(status: int, body: dict[str, Any] | None) -> XReadError:
    detail = ""
    if isinstance(body, dict):
        errors = body.get("errors")
        if isinstance(errors, list) and errors and isinstance(errors[0], dict):
            detail = str(errors[0].get("message") or errors[0].get("detail") or "")[:300]
        detail = detail or str(body.get("detail") or "")[:300]
    tail = f" X said: {detail}" if detail else ""
    if status == 401:
        return XReadError("unauthorized", "X rejected the saved keys. Check them in Settings > API Keys > X." + tail, http_status=status)
    if status == 403:
        return XReadError("plan_refused", "X refused this read: your X app's plan or permissions do not include it." + tail, http_status=status)
    if status == 402:
        return XReadError("exhausted", "X says your X app has no API credits left." + tail, http_status=status)
    if status == 429:
        return XReadError("rate_limited", "X is limiting requests for your app right now. Try again later." + tail, http_status=status)
    if status == 404:
        return XReadError("not_found", "X has no such post, account or list." + tail, http_status=status)
    if status == 400:
        return XReadError("invalid_request", "X did not accept this request." + tail, http_status=status)
    if status >= 500 or status == 408:
        return XReadError("provider_unavailable", f"X is having trouble right now (HTTP {status}). Try again later.", http_status=status)
    return XReadError("unexpected_response", f"X gave an unexpected answer (HTTP {status}).{tail}", http_status=status)


def _request(path: str, params: dict[str, Any] | None = None, *, user_context: bool = False,
             saved: dict[str, Any] | None = None) -> dict[str, Any]:
    """One read-only GET to the pinned X origin. Counts usage; raises XReadError on any failure."""
    import urllib.error

    from core.remote_fetch_policy import CredentialRedirectRefusedError, RemoteFetchRefusedError, open_remote_url
    from core.secret_redaction import register_exact_secret

    saved = _saved() if saved is None else saved
    clean = {k: v for k, v in (params or {}).items() if v not in (None, "", [])}
    url = API_ORIGIN + path + ("?" + urlencode(clean) if clean else "")
    authorization, secrets_in_use = _auth(saved, "GET", url, user_context=user_context)
    for value in secrets_in_use:
        register_exact_secret(value)
    refusal = x_usage.refusal()
    if refusal:
        raise XReadError("usage_limit_reached", refusal)
    x_usage.record_request(path)
    try:
        response = open_remote_url(
            url, headers={"Authorization": authorization, "Accept": "application/json"},
            method="GET", timeout=REQUEST_TIMEOUT_S, no_proxy=True, provider_id="x.api",
            keyed_or_keyless="keyed", credential_headers=("Authorization",),
        )
        status = int(getattr(response, "status", None) or getattr(response, "code", None) or 200)
        raw = response.read(MAX_RESPONSE_BYTES + 1) or b""
    except RemoteFetchRefusedError:
        raise XReadError("refused", "VOOL is not allowed to go online right now (for example Local Only is on), so X was not asked.") from None
    except CredentialRedirectRefusedError:
        raise XReadError("redirected", "X answered with a redirect to another site; VOOL did not send your keys there.") from None
    except urllib.error.HTTPError as exc:
        body = None
        try:
            body = json.loads((exc.read(64 * 1024) or b"{}").decode("utf-8"))
        except Exception:
            body = None
        raise _http_failure(int(exc.code or 0), body) from None
    except XReadError:
        raise
    except Exception as exc:
        from core.credential_intelligence.verification import is_timeout_error

        if is_timeout_error(exc):
            raise XReadError("timeout", "X did not answer in time. Try again.") from None
        raise XReadError("unreachable", "Could not reach X from this machine.") from None
    if len(raw) > MAX_RESPONSE_BYTES:
        raise XReadError("malformed_response", "X answered with more data than VOOL reads in one go.")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise XReadError("malformed_response", "X answered with something that is not an API response.") from None
    if not isinstance(payload, dict):
        raise XReadError("unexpected_schema", "X answered, but not in the shape VOOL expects.")
    if not 200 <= status < 300:
        raise _http_failure(status, payload)
    if "data" not in payload and payload.get("errors") and not payload.get("meta"):
        err = payload["errors"][0] if isinstance(payload["errors"], list) and payload["errors"] else {}
        title = str((err or {}).get("title") or "")
        if "Not Found" in title or "not-found" in str((err or {}).get("type") or ""):
            raise XReadError("not_found", "X has no such post, account or list. " + str((err or {}).get("detail") or "")[:200])
        raise XReadError("unexpected_response", "X answered with an error: " + str((err or {}).get("detail") or title)[:300])
    x_usage.record_read(posts=_count_posts(payload), users=_count_users(payload))
    return payload


def _count_posts(payload: dict[str, Any]) -> int:
    data = payload.get("data")
    n = 0
    if isinstance(data, list) and data and isinstance(data[0], dict) and "text" in data[0]:
        n += len(data)
    elif isinstance(data, dict) and "text" in data:
        n += 1
    includes = payload.get("includes") if isinstance(payload.get("includes"), dict) else {}
    n += len(includes.get("tweets") or [])
    return n


def _count_users(payload: dict[str, Any]) -> int:
    data = payload.get("data")
    n = 0
    if isinstance(data, list) and data and isinstance(data[0], dict) and "username" in data[0]:
        n += len(data)
    elif isinstance(data, dict) and "username" in data:
        n += 1
    includes = payload.get("includes") if isinstance(payload.get("includes"), dict) else {}
    n += len(includes.get("users") or [])
    return n


def _guard(fn):
    """Run a read; turn every failure into a structured XResult."""
    try:
        return fn()
    except XReadError as exc:
        return XResult(False, exc.status, exc.message, {"http_status": exc.http_status} if exc.http_status else {})
    except Exception as exc:  # never raise into the tool loop
        return XResult(False, "failed", f"Reading X failed ({type(exc).__name__}).")


# --------------------------------------------------------------------------- shaping


def _int(value: Any, default: int, lo: int, hi: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(lo, min(hi, number))


def _page_size(requested: Any, default: int = 25, lo: int = 10, hi: int = MAX_PAGE) -> int:
    """The page size to ask X for: what was asked, clamped to X's bounds and to what is left under
    the user's monthly read limit. A page can carry the author accounts too, so it is at most half
    of what is left; when that is under X's smallest page, nothing is sent."""
    size = _int(requested, default, lo, hi)
    left = x_usage.reads_left()
    if left is not None:
        if left // 2 < lo:
            raise XReadError("usage_limit_reached", x_usage.refusal() or (
                f"Only {left} reads are left under your X limit this month, fewer than one page needs. "
                "Nothing was sent to X. You can raise the limit in Settings > API Keys > X."))
        size = min(size, left // 2)
    return size


def post_url(username: str, post_id: str) -> str:
    return f"https://x.com/{username or 'i'}/status/{post_id}"


def _users_by_id(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    includes = payload.get("includes") if isinstance(payload.get("includes"), dict) else {}
    return {str(u.get("id")): u for u in includes.get("users") or [] if isinstance(u, dict)}


def shape_user(user: dict[str, Any]) -> dict[str, Any]:
    metrics = user.get("public_metrics") if isinstance(user.get("public_metrics"), dict) else {}
    return {
        "id": str(user.get("id") or ""),
        "username": str(user.get("username") or ""),
        "name": str(user.get("name") or ""),
        "created_at": str(user.get("created_at") or ""),
        "description": str(user.get("description") or "")[:400],
        "verified": bool(user.get("verified")),
        "protected": bool(user.get("protected")),
        "location": str(user.get("location") or "")[:80],
        "followers": metrics.get("followers_count"),
        "following": metrics.get("following_count"),
        "posts": metrics.get("tweet_count"),
        "listed": metrics.get("listed_count"),
        "url": f"https://x.com/{user.get('username')}" if user.get("username") else "",
    }


def _expanded_urls(post: dict[str, Any]) -> list[str]:
    entities = post.get("entities") if isinstance(post.get("entities"), dict) else {}
    return [str(u.get("expanded_url") or u.get("url") or "") for u in entities.get("urls") or [] if isinstance(u, dict)]


def token_mentions(post: dict[str, Any]) -> dict[str, list[str]]:
    """Cashtags and contract addresses a post names (text, entities and expanded links)."""
    entities = post.get("entities") if isinstance(post.get("entities"), dict) else {}
    tags = {str(t.get("tag") or "").upper() for t in entities.get("cashtags") or [] if isinstance(t, dict) and t.get("tag")}
    text = str(post.get("text") or "")
    tags.update(m.upper() for m in CASHTAG_RE.findall(text))
    addresses: list[str] = []
    for blob in [text, *_expanded_urls(post)]:
        for match in _ADDRESS_IN_TEXT_RE.findall(blob):
            if match.startswith("0x"):
                match = match.lower()
            elif not _plausible_solana(match):
                continue
            if match not in addresses:
                addresses.append(match)
    return {"cashtags": sorted(tags), "addresses": addresses}


def _plausible_solana(value: str) -> bool:
    # Base58 runs of 32-44 chars also match plain words glued together; a real address mixes
    # upper case, lower case and digits.
    return bool(SOLANA_ADDRESS_RE.fullmatch(value)) and any(c.isdigit() for c in value) \
        and any(c.isupper() for c in value) and any(c.islower() for c in value)


def shape_post(post: dict[str, Any], users: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    metrics = post.get("public_metrics") if isinstance(post.get("public_metrics"), dict) else {}
    author = (users or {}).get(str(post.get("author_id") or ""), {})
    username = str(author.get("username") or "")
    refs = [str(r.get("type") or "") for r in post.get("referenced_tweets") or [] if isinstance(r, dict)]
    shaped = {
        "id": str(post.get("id") or ""),
        "text": str(post.get("text") or "")[:1000],
        "created_at": str(post.get("created_at") or ""),
        "author_id": str(post.get("author_id") or ""),
        "author": username,
        "url": post_url(username, str(post.get("id") or "")),
        "likes": metrics.get("like_count"),
        "reposts": metrics.get("retweet_count"),
        "replies": metrics.get("reply_count"),
        "quotes": metrics.get("quote_count"),
        "views": metrics.get("impression_count"),
        "kind": refs[0] if refs else "post",
        "lang": str(post.get("lang") or ""),
    }
    mentions = token_mentions(post)
    if mentions["cashtags"] or mentions["addresses"]:
        shaped["tokens"] = mentions
    return shaped


def _posts_result(payload: dict[str, Any], message_noun: str) -> dict[str, Any]:
    users = _users_by_id(payload)
    posts = [shape_post(p, users) for p in payload.get("data") or [] if isinstance(p, dict)]
    meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
    return {"posts": posts, "next_token": str(meta.get("next_token") or ""), "noun": message_noun}


# --------------------------------------------------------------------------- queries


def classify_token(value: str) -> tuple[str, str]:
    """('evm_address'|'solana_address'|'cashtag'|'name', normalized value)."""
    text = str(value or "").strip()
    if EVM_ADDRESS_RE.fullmatch(text):
        return "evm_address", text
    if SOLANA_ADDRESS_RE.fullmatch(text) and _plausible_solana(text):
        return "solana_address", text
    tag = CASHTAG_RE.fullmatch(text)
    if tag:
        return "cashtag", tag.group(1).upper()
    return "name", text


def token_query(token: str, *, include_reposts: bool = False, include_replies: bool = True,
                language: str = "") -> tuple[str, str]:
    """Build an X search query for a token. Returns (kind, query)."""
    kind, value = classify_token(token)
    if not value:
        raise XReadError("invalid_arguments", "Say which token: a contract address, a $CASHTAG or a name.")
    if '"' in value or len(value) > 120:
        raise XReadError("invalid_arguments", "That token name is too long or has quotes in it.")
    if kind in {"evm_address", "solana_address"}:
        # An address shows up as text, or inside links (charts, launchpads, explorers).
        core = f'({value} OR url:"{value}")'
    elif kind == "cashtag":
        core = f"${value}"
    else:
        core = f'"{value}"'
    parts = [core]
    if not include_reposts:
        parts.append("-is:retweet")
    if not include_replies:
        parts.append("-is:reply")
    if language and re.fullmatch(r"[a-z]{2}", language):
        parts.append(f"lang:{language}")
    return kind, " ".join(parts)


def _iso(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise XReadError("invalid_arguments", f"'{text}' is not a date and time VOOL can read (use 2026-10-13 or 2026-10-13T08:00:00Z).") from None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def search_posts(query: str = "", *, token: str = "", max_results: Any = 25, archive: bool = False,
                 start_time: str = "", end_time: str = "", sort: str = "", next_token: str = "",
                 include_reposts: bool = False) -> XResult:
    """Search posts: a raw X query, or a token (address, $CASHTAG or name)."""
    def run() -> XResult:
        kind = "query"
        q = str(query or "").strip()
        if token and not q:
            kind, q = token_query(token, include_reposts=include_reposts)
        if not q:
            raise XReadError("invalid_arguments", "Say what to search for.")
        if len(q) > 1024:
            raise XReadError("invalid_arguments", "That search is longer than X accepts.")
        params = {
            "query": q, "max_results": _page_size(max_results),
            "tweet.fields": POST_FIELDS, "expansions": "author_id", "user.fields": USER_FIELDS,
            "start_time": _iso(start_time), "end_time": _iso(end_time),
            "sort_order": sort if sort in {"recency", "relevancy"} else "",
            "next_token": str(next_token or "").strip(),
        }
        path = "/2/tweets/search/all" if archive else "/2/tweets/search/recent"
        try:
            payload = _request(path, params)
        except XReadError as exc:
            if kind == "cashtag" and exc.status in {"invalid_request", "plan_refused"}:
                # Some X plans refuse the $cashtag operator; search the ticker as a word instead.
                params["query"] = q.replace(f"${classify_token(token)[1]}", f'"{classify_token(token)[1]}"', 1)
                payload = _request(path, params)
                q = params["query"]
            else:
                raise
        shaped = _posts_result(payload, "post")
        users = [shape_user(u) for u in _users_by_id(payload).values()]
        shaped.update({"query": q, "token_kind": kind if token else "", "archive": archive,
                       "authors": users})
        n = len(shaped["posts"])
        scope = "the full archive" if archive else "the last 7 days"
        return XResult(True, "executed", f"Found {n} post(s) on X for {q!r} in {scope}." if n else
                       f"X has no posts for {q!r} in {scope}.", shaped)
    return _guard(run)


def count_posts(query: str = "", *, token: str = "", granularity: str = "day", archive: bool = False,
                start_time: str = "", end_time: str = "") -> XResult:
    """How many posts match over time (no posts are read, so it is cheap on usage)."""
    def run() -> XResult:
        q = str(query or "").strip()
        if token and not q:
            _, q = token_query(token)
        if not q:
            raise XReadError("invalid_arguments", "Say what to count.")
        params = {"query": q, "granularity": granularity if granularity in {"minute", "hour", "day"} else "day",
                  "start_time": _iso(start_time), "end_time": _iso(end_time)}
        payload = _request("/2/tweets/counts/all" if archive else "/2/tweets/counts/recent", params)
        buckets = [{"start": b.get("start"), "end": b.get("end"), "count": b.get("tweet_count")}
                   for b in payload.get("data") or [] if isinstance(b, dict)]
        total = (payload.get("meta") or {}).get("total_tweet_count") if isinstance(payload.get("meta"), dict) else None
        if total is None:
            total = sum(int(b.get("count") or 0) for b in buckets)
        return XResult(True, "executed", f"{total} post(s) match {q!r}.", {"query": q, "total": total, "buckets": buckets})
    return _guard(run)


# --------------------------------------------------------------------------- token callers


def _caller_rows(posts: list[dict[str, Any]], authors: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    by_author: dict[str, dict[str, Any]] = {}
    for post in posts:
        aid = post.get("author_id") or ""
        row = by_author.setdefault(aid, {"posts": []})
        row["posts"].append(post)
    rows: list[dict[str, Any]] = []
    for aid, row in by_author.items():
        user = shape_user(authors.get(aid, {"id": aid}))
        mine = sorted(row["posts"], key=lambda p: p.get("created_at") or "")
        total = {k: sum(int(p.get(k) or 0) for p in mine) for k in ("likes", "reposts", "replies", "quotes", "views")}
        rows.append({
            **{k: user[k] for k in ("username", "name", "id", "followers", "following", "created_at", "verified", "url")},
            "account_created": user["created_at"],
            "first_call_at": mine[0].get("created_at") or "",
            "last_call_at": mine[-1].get("created_at") or "",
            "call_posts": len(mine),
            "engagement": total,
            "first_call": {k: mine[0].get(k) for k in ("id", "url", "text")},
        })
        rows[-1].pop("created_at", None)
    rows.sort(key=lambda r: r["first_call_at"] or "~")
    for rank, row in enumerate(rows, 1):
        row["rank_by_first_call"] = rank
    return rows


def calls_in_posts(posts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every token (cashtag or address) a set of posts names, with first/last mention and count."""
    calls: dict[str, dict[str, Any]] = {}
    for post in sorted(posts, key=lambda p: p.get("created_at") or ""):
        mentions = post.get("tokens") or token_mentions(post)
        names = [f"${t}" for t in mentions.get("cashtags") or []] + list(mentions.get("addresses") or [])
        for name in names:
            row = calls.setdefault(name, {"token": name, "first_at": post.get("created_at") or "",
                                          "first_post": post.get("url") or "", "mentions": 0})
            row["mentions"] += 1
            row["last_at"] = post.get("created_at") or ""
    return sorted(calls.values(), key=lambda r: r["first_at"] or "~")


def token_callers(token: str, *, max_results: Any = 50, archive: bool = False, start_time: str = "",
                  end_time: str = "", history_for: Any = 0, history_posts: Any = 20,
                  include_reposts: bool = False) -> XResult:
    """Who posted about a token, earliest first, with each caller's reach and engagement.

    ``history_for`` > 0 also reads that many top callers' recent posts and lists the other tokens
    each one called (costs one more request and up to ``history_posts`` post reads per caller).
    """
    def run() -> XResult:
        found = search_posts(token=token, max_results=max_results, archive=archive, start_time=start_time,
                             end_time=end_time, sort="recency", include_reposts=include_reposts)
        if not found.ok:
            return found
        # search_posts only shapes the authors it saw; rebuild the id map from them.
        authors = {a["id"]: {"id": a["id"], "username": a["username"], "name": a["name"],
                             "created_at": a["created_at"], "verified": a["verified"],
                             "public_metrics": {"followers_count": a["followers"], "following_count": a["following"],
                                                "tweet_count": a["posts"]}}
                   for a in found.data.get("authors") or []}
        rows = _caller_rows(found.data.get("posts") or [], authors)
        wanted = _int(history_for, 0, 0, 10)
        for row in rows[:wanted]:
            history = user_posts(f"id:{row['id']}" if row["id"] else row["username"], max_results=history_posts,
                                 exclude="retweets")
            if history.ok:
                other = [c for c in history.data.get("calls") or [] if not _same_token(c["token"], token)]
                row["other_calls"] = other[:25]
                row["history_posts_read"] = len(history.data.get("posts") or [])
            else:
                row["history_error"] = history.message
                if history.status in {"usage_limit_reached", "rate_limited", "exhausted"}:
                    break
        data = {
            "token": token, "token_kind": found.data.get("token_kind"), "query": found.data.get("query"),
            "posts_found": len(found.data.get("posts") or []), "callers": rows,
            "earliest_post_at": rows[0]["first_call_at"] if rows else "",
            "next_token": found.data.get("next_token") or "",
            "window": "full archive" if archive else "last 7 days",
        }
        if not rows:
            return XResult(True, "no_results", f"Nobody on X posted about {token} in the {data['window']} (query {data['query']!r}).", data)
        first = rows[0]
        return XResult(True, "executed",
                       f"{len(rows)} account(s) posted about {token} in the {data['window']}; earliest was "
                       f"@{first['username']} at {first['first_call_at']}.", data)
    return _guard(run)


def _same_token(name: str, token: str) -> bool:
    kind, value = classify_token(token)
    if kind == "cashtag":
        return name.upper() == f"${value}"
    return name.lower() == value.lower()


# --------------------------------------------------------------------------- accounts


def _user_id(who: Any, saved: dict[str, Any]) -> tuple[str, dict[str, Any] | None]:
    text = str(who or "").strip()
    if not text or text.lower() in {"me", "@me", "my", "mine"}:
        payload = _request("/2/users/me", {"user.fields": USER_FIELDS}, user_context=True, saved=saved)
        user = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        return str(user.get("id") or ""), user
    if text.startswith("https://") or text.startswith("x.com/") or text.startswith("twitter.com/"):
        text = text.rstrip("/").rsplit("/", 1)[-1]
    if text.startswith("id:") and _ID_RE.fullmatch(text[3:]):
        return text[3:], None
    if _ID_RE.fullmatch(text) and len(text) > 15:
        return text, None
    handle = _HANDLE_RE.fullmatch(text)
    if not handle:
        raise XReadError("invalid_arguments", f"'{text[:40]}' is not an X username.")
    payload = _request(f"/2/users/by/username/{handle.group(1)}", {"user.fields": USER_FIELDS}, saved=saved)
    user = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    if not user.get("id"):
        raise XReadError("not_found", f"X has no account @{handle.group(1)}.")
    return str(user["id"]), user


def lookup_users(usernames: Any) -> XResult:
    """Profiles for one or more accounts (username, @handle, profile link, or 'me')."""
    def run() -> XResult:
        items = usernames if isinstance(usernames, list) else [s for s in re.split(r"[,\s]+", str(usernames or "")) if s]
        if not items:
            raise XReadError("invalid_arguments", "Say which X account(s) to look up.")
        items = items[:100]
        saved = _saved()
        handles, users = [], []
        for item in items:
            text = str(item).strip()
            if text.lower() in {"me", "@me"}:
                _, me = _user_id("me", saved)
                users.append(shape_user(me or {}))
                continue
            if "/" in text:
                text = text.rstrip("/").rsplit("/", 1)[-1]
            m = _HANDLE_RE.fullmatch(text)
            if not m:
                raise XReadError("invalid_arguments", f"'{text[:40]}' is not an X username.")
            handles.append(m.group(1))
        missing: list[str] = []
        if handles:
            payload = _request("/2/users/by", {"usernames": ",".join(handles), "user.fields": USER_FIELDS,
                                               "expansions": "pinned_tweet_id", "tweet.fields": POST_FIELDS}, saved=saved)
            users.extend(shape_user(u) for u in payload.get("data") or [] if isinstance(u, dict))
            found = {u["username"].lower() for u in users}
            missing = [h for h in handles if h.lower() not in found]
        msg = f"Looked up {len(users)} account(s) on X." + (f" Not found: {', '.join('@' + m for m in missing)}." if missing else "")
        return XResult(bool(users), "executed" if users else "not_found", msg, {"users": users, "not_found": missing})
    return _guard(run)


def user_posts(who: Any, *, kind: str = "posts", max_results: Any = 25, exclude: str = "",
               start_time: str = "", end_time: str = "", next_token: str = "") -> XResult:
    """An account's posts, mentions, liked posts, or your home timeline; with the tokens they named."""
    def run() -> XResult:
        saved = _saved()
        k = str(kind or "posts").strip().lower()
        if k not in {"posts", "mentions", "likes", "home"}:
            raise XReadError("invalid_arguments", "kind must be posts, mentions, likes or home.")
        user_context = k == "home"
        uid, user = _user_id("me" if k == "home" else who, saved)
        params: dict[str, Any] = {
            "max_results": _page_size(max_results, lo=5 if k != "likes" else 10),
            "tweet.fields": POST_FIELDS, "expansions": "author_id", "user.fields": USER_FIELDS,
            "pagination_token": str(next_token or "").strip(),
        }
        if k in {"posts", "mentions", "home"}:
            params.update({"start_time": _iso(start_time), "end_time": _iso(end_time)})
        if k in {"posts", "home"}:
            ex = {e.strip() for e in str(exclude or "").split(",") if e.strip() in {"retweets", "replies"}}
            params["exclude"] = ",".join(sorted(ex))
        path = {"posts": f"/2/users/{uid}/tweets", "mentions": f"/2/users/{uid}/mentions",
                "likes": f"/2/users/{uid}/liked_tweets",
                "home": f"/2/users/{uid}/timelines/reverse_chronological"}[k]
        payload = _request(path, params, user_context=user_context, saved=saved)
        shaped = _posts_result(payload, k)
        shaped["account"] = shape_user(user) if user else {"id": uid}
        shaped["calls"] = calls_in_posts(shaped["posts"])
        who_txt = "your home timeline" if k == "home" else f"@{shaped['account'].get('username') or uid}"
        return XResult(True, "executed", f"Read {len(shaped['posts'])} {k} for {who_txt}.", shaped)
    return _guard(run)


def _post_id(value: Any) -> str:
    text = str(value or "").strip()
    m = _POST_URL_RE.search(text)
    if m:
        return m.group(1)
    if _ID_RE.fullmatch(text):
        return text
    raise XReadError("invalid_arguments", f"'{text[:60]}' is not an X post link or id.")


def post_details(post: Any, *, kind: str = "post", max_results: Any = 25, next_token: str = "") -> XResult:
    """A post (by link or id), or who liked / reposted / quoted / replied to it."""
    def run() -> XResult:
        k = str(kind or "post").strip().lower()
        ids = post if isinstance(post, list) else [post]
        post_ids = [_post_id(p) for p in ids if str(p or "").strip()][:100]
        if not post_ids:
            raise XReadError("invalid_arguments", "Give an X post link or id.")
        pid = post_ids[0]
        if k == "post":
            payload = _request("/2/tweets", {"ids": ",".join(post_ids), "tweet.fields": POST_FIELDS,
                                             "expansions": "author_id,referenced_tweets.id", "user.fields": USER_FIELDS})
            shaped = _posts_result(payload, "post")
            return XResult(bool(shaped["posts"]), "executed" if shaped["posts"] else "not_found",
                           f"Read {len(shaped['posts'])} post(s).", shaped)
        if k in {"liking_users", "reposted_by"}:
            path = f"/2/tweets/{pid}/" + ("liking_users" if k == "liking_users" else "retweeted_by")
            payload = _request(path, {"max_results": _page_size(max_results, 50, 1), "user.fields": USER_FIELDS,
                                      "pagination_token": str(next_token or "").strip()})
            users = [shape_user(u) for u in payload.get("data") or [] if isinstance(u, dict)]
            meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
            return XResult(True, "executed", f"{len(users)} account(s) {'liked' if k == 'liking_users' else 'reposted'} that post.",
                           {"users": users, "next_token": str(meta.get("next_token") or "")})
        if k == "quotes":
            payload = _request(f"/2/tweets/{pid}/quote_tweets", {
                "max_results": _page_size(max_results), "tweet.fields": POST_FIELDS, "expansions": "author_id",
                "user.fields": USER_FIELDS, "pagination_token": str(next_token or "").strip()})
            shaped = _posts_result(payload, "quote")
            return XResult(True, "executed", f"{len(shaped['posts'])} quote post(s).", shaped)
        if k == "replies":
            return search_posts(f"conversation_id:{pid}", max_results=max_results, next_token=next_token, sort="recency")
        raise XReadError("invalid_arguments", "kind must be post, liking_users, reposted_by, quotes or replies.")
    return _guard(run)


def follows(who: Any, *, kind: str = "followers", max_results: Any = 100, next_token: str = "") -> XResult:
    def run() -> XResult:
        k = str(kind or "followers").strip().lower()
        if k not in {"followers", "following"}:
            raise XReadError("invalid_arguments", "kind must be followers or following.")
        saved = _saved()
        uid, user = _user_id(who, saved)
        payload = _request(f"/2/users/{uid}/{k}", {"max_results": _page_size(max_results, 100, 1, 1000),
                                                   "user.fields": USER_FIELDS,
                                                   "pagination_token": str(next_token or "").strip()}, saved=saved)
        users = [shape_user(u) for u in payload.get("data") or [] if isinstance(u, dict)]
        meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
        name = (user or {}).get("username") or uid
        return XResult(True, "executed", f"Read {len(users)} {k} of @{name}.",
                       {"users": users, "next_token": str(meta.get("next_token") or ""), "account": name})
    return _guard(run)


def lists(*, who: Any = "", list_id: Any = "", kind: str = "owned", max_results: Any = 25,
          next_token: str = "") -> XResult:
    """An account's lists (owned / memberships / followed), or a list's details, posts or members."""
    def run() -> XResult:
        k = str(kind or "owned").strip().lower()
        params_page = {"pagination_token": str(next_token or "").strip()}
        if k in {"owned", "memberships", "followed"}:
            saved = _saved()
            uid, user = _user_id(who, saved)
            path = {"owned": f"/2/users/{uid}/owned_lists", "memberships": f"/2/users/{uid}/list_memberships",
                    "followed": f"/2/users/{uid}/followed_lists"}[k]
            payload = _request(path, {"max_results": _int(max_results, 25, 1, 100), "list.fields": LIST_FIELDS,
                                      **params_page}, saved=saved)
            found = [_shape_list(item) for item in payload.get("data") or [] if isinstance(item, dict)]
            return XResult(True, "executed", f"{len(found)} list(s) ({k}) for @{(user or {}).get('username') or uid}.",
                           {"lists": found})
        lid = str(list_id or "").strip().rstrip("/").rsplit("/", 1)[-1]
        if not _ID_RE.fullmatch(lid):
            raise XReadError("invalid_arguments", "Give an X list id or link.")
        if k == "details":
            payload = _request(f"/2/lists/{lid}", {"list.fields": LIST_FIELDS})
            return XResult(True, "executed", "Read the list.", {"list": _shape_list(payload.get("data") or {})})
        if k == "posts":
            payload = _request(f"/2/lists/{lid}/tweets", {"max_results": _page_size(max_results, lo=1),
                                                          "tweet.fields": POST_FIELDS, "expansions": "author_id",
                                                          "user.fields": USER_FIELDS, **params_page})
            shaped = _posts_result(payload, "post")
            shaped["calls"] = calls_in_posts(shaped["posts"])
            return XResult(True, "executed", f"Read {len(shaped['posts'])} post(s) from the list.", shaped)
        if k in {"members", "followers"}:
            payload = _request(f"/2/lists/{lid}/{k}", {"max_results": _page_size(max_results, 100, 1),
                                                       "user.fields": USER_FIELDS, **params_page})
            users = [shape_user(u) for u in payload.get("data") or [] if isinstance(u, dict)]
            return XResult(True, "executed", f"{len(users)} list {k}.", {"users": users})
        raise XReadError("invalid_arguments", "kind must be owned, memberships, followed, details, posts, members or followers.")
    return _guard(run)


def _shape_list(item: dict[str, Any]) -> dict[str, Any]:
    return {k: item.get(k) for k in ("id", "name", "description", "member_count", "follower_count", "private", "owner_id", "created_at")}


__all__ = [
    "XReadError",
    "XResult",
    "calls_in_posts",
    "classify_token",
    "connected",
    "count_posts",
    "follows",
    "lists",
    "lookup_users",
    "post_details",
    "search_posts",
    "shape_post",
    "shape_user",
    "token_callers",
    "token_mentions",
    "token_query",
    "user_posts",
]
