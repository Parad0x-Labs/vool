"""Chat-side handlers for the X tools: the model calls these intents, ``core.x_api`` does the reads.

Each handler returns the read as structured data in the tool observation (posts, accounts,
callers, token calls) plus one plain sentence, so the model analyses real data and the reply can
cite post links. Actions that act as the user on X (post, reply, like, repost, follow) go through
``core.x_actions``: the model may only PROPOSE one; the user approves it in the app.
"""
from __future__ import annotations

from typing import Any

X_READ_INTENTS = frozenset({
    "x.search", "x.token_callers", "x.user", "x.user_posts", "x.post", "x.follows", "x.lists", "x.usage",
})
X_ACTION_INTENTS = frozenset({"x.action.propose", "x.action.status"})
X_INTENTS = X_READ_INTENTS | X_ACTION_INTENTS


def _bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _result(intent: str, outcome: Any, *, keys: tuple[str, ...]):
    from core.runtime_execution_tools import RuntimeExecutionResult, _tool_observation

    data = dict(outcome.data or {})
    payload = {key: data.get(key) for key in keys if key in data}
    if not outcome.ok and outcome.status != "no_results":
        payload["error"] = outcome.message
    return RuntimeExecutionResult(
        handled=True,
        ok=bool(outcome.ok),
        status=str(outcome.status),
        response_text=outcome.message,
        details={
            **payload,
            "observation": _tool_observation(intent=intent, tool_surface="x", ok=bool(outcome.ok),
                                             status=str(outcome.status), message=outcome.message, **payload),
        },
    )


def run_x_tool(intent: str, arguments: dict[str, Any] | None, source_context: dict[str, Any] | None = None):
    from core import x_api

    args = dict(arguments or {})
    if intent == "x.search":
        if str(args.get("mode") or "").strip().lower() == "counts":
            outcome = x_api.count_posts(str(args.get("query") or ""), token=str(args.get("token") or ""),
                                        granularity=str(args.get("granularity") or "day"),
                                        archive=_bool(args.get("archive")),
                                        start_time=str(args.get("start_time") or ""),
                                        end_time=str(args.get("end_time") or ""))
            return _result(intent, outcome, keys=("query", "total", "buckets"))
        outcome = x_api.search_posts(str(args.get("query") or ""), token=str(args.get("token") or ""),
                                     max_results=args.get("max_results", 25), archive=_bool(args.get("archive")),
                                     start_time=str(args.get("start_time") or ""),
                                     end_time=str(args.get("end_time") or ""), sort=str(args.get("sort") or ""),
                                     next_token=str(args.get("next_token") or ""),
                                     include_reposts=_bool(args.get("include_reposts")))
        return _result(intent, outcome, keys=("query", "token_kind", "posts", "authors", "next_token"))
    if intent == "x.token_callers":
        outcome = x_api.token_callers(str(args.get("token") or ""), max_results=args.get("max_results", 50),
                                      archive=_bool(args.get("archive")),
                                      start_time=str(args.get("start_time") or ""),
                                      end_time=str(args.get("end_time") or ""),
                                      history_for=args.get("history_for", 0),
                                      history_posts=args.get("history_posts", 20),
                                      include_reposts=_bool(args.get("include_reposts")),
                                      min_followers=args.get("min_followers", 0),
                                      evaluate_calls=_bool(args.get("evaluate_calls")),
                                      source_context=source_context)
        return _result(intent, outcome, keys=("token", "token_kind", "query", "window", "posts_found",
                                              "earliest_post_at", "callers", "next_token", "min_followers",
                                              "callers_below_min_followers"))
    if intent == "x.user":
        outcome = x_api.lookup_users(args.get("usernames") or args.get("username") or "")
        return _result(intent, outcome, keys=("users", "not_found"))
    if intent == "x.user_posts":
        outcome = x_api.user_posts(args.get("username") or args.get("user") or "", kind=str(args.get("kind") or "posts"),
                                   max_results=args.get("max_results", 25), exclude=str(args.get("exclude") or ""),
                                   start_time=str(args.get("start_time") or ""), end_time=str(args.get("end_time") or ""),
                                   next_token=str(args.get("next_token") or ""))
        return _result(intent, outcome, keys=("account", "posts", "calls", "next_token"))
    if intent == "x.post":
        outcome = x_api.post_details(args.get("post") or args.get("posts") or "", kind=str(args.get("kind") or "post"),
                                     max_results=args.get("max_results", 25), next_token=str(args.get("next_token") or ""))
        return _result(intent, outcome, keys=("posts", "users", "next_token"))
    if intent == "x.follows":
        outcome = x_api.follows(args.get("username") or "", kind=str(args.get("kind") or "followers"),
                                max_results=args.get("max_results", 100), next_token=str(args.get("next_token") or ""))
        return _result(intent, outcome, keys=("account", "users", "next_token"))
    if intent == "x.lists":
        outcome = x_api.lists(who=args.get("username") or "", list_id=args.get("list_id") or "",
                              kind=str(args.get("kind") or "owned"), max_results=args.get("max_results", 25),
                              next_token=str(args.get("next_token") or ""))
        return _result(intent, outcome, keys=("lists", "list", "posts", "users", "calls", "next_token"))
    if intent == "x.usage":
        from core import x_usage

        snap = x_usage.snapshot()
        outcome = x_api.XResult(bool(snap.get("ok")), "executed" if snap.get("ok") else "unreadable", _usage_text(snap),
                                {"usage": {k: v for k, v in snap.items() if k not in {"ok", "history"}}})
        return _result(intent, outcome, keys=("usage",))
    if intent in X_ACTION_INTENTS:
        from core import x_actions

        if intent == "x.action.propose":
            outcome = x_actions.propose(args, source_context=source_context)
        else:
            outcome = x_actions.status_for_model(str(args.get("action_id") or ""), source_context=source_context)
        return _result(intent, outcome, keys=("action",))
    from core.runtime_execution_tools import RuntimeExecutionResult

    return RuntimeExecutionResult(handled=False, ok=False, status="unsupported", response_text=f"Unknown X intent {intent}.")


def _usage_text(snap: dict[str, Any]) -> str:
    if not snap.get("ok"):
        return str(snap.get("message") or "The X usage meter could not be read.")
    from datetime import datetime

    month = datetime.strptime(snap["month"], "%Y-%m").strftime("%B")
    reads, actions = snap["reads"], snap["actions"]
    limit, alimit = snap.get("monthly_reads"), snap.get("monthly_actions")
    reads_txt = f"{reads:,} posts and accounts" + (f" (of your {limit:,} limit)" if limit is not None else " (no limit set)")
    sent_txt = f"{actions:,} posts, replies, likes and follows" + (f" (of {alimit:,})" if alimit is not None else " (no limit set)")
    return (f"In {month} VOOL has read {reads_txt} on X and sent {sent_txt}. "
            "X charges these to your X developer account.")


__all__ = ["X_ACTION_INTENTS", "X_INTENTS", "X_READ_INTENTS", "run_x_tool"]
