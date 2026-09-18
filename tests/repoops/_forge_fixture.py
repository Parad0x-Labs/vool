"""A hermetic remote: real adapters, real runtime, no socket.

The fixture replaces ONLY the transport -- the one callable VOOL injects into an adapter. The
adapter under test is the shipped one, it builds its own URLs, parses the provider's real wire
shapes, and is reached through the real registry. So a test here proves the adapter, the contract
and the runtime above them, and proves nothing about a network stack it never touches (which it
would be dishonest to claim either way).

A route can also be armed to FAIL in the two ways that matter and are otherwise unreachable
offline: `deny` (VOOL refused before the socket) and `unknown` (the request left the machine and
the reply never came back).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from core.kas.contract import KasRequest, KasResponse, TransportDeniedError, TransportUnknownError


@dataclass
class RecordedForge:
    """Routes keyed by ``"<METHOD> <url-suffix>"``, matched by suffix containment."""

    routes: dict[str, Any] = field(default_factory=dict)
    calls: list[dict[str, Any]] = field(default_factory=list)
    deny: set[str] = field(default_factory=set)
    unknown: set[str] = field(default_factory=set)

    def route(self, key: str, payload: Any, *, status: int = 200, headers: dict[str, str] | None = None) -> RecordedForge:
        self.routes[key] = (status, payload, dict(headers or {}))
        return self

    def arm_denial(self, fragment: str) -> RecordedForge:
        """Arm a VOOL-side pre-socket refusal. A bare fragment matches any method; a fragment
        with a method (``"POST /pulls"``) matches only that method's requests -- the shape a
        test needs when a mutating endpoint shares its path prefix with reads."""
        self.deny.add(fragment)
        return self

    def arm_unknown(self, fragment: str) -> RecordedForge:
        """Arm a lost-reply outcome (the request left, the answer never came back)."""
        self.unknown.add(fragment)
        return self

    def disarm(self, fragment: str) -> RecordedForge:
        self.deny.discard(fragment)
        self.unknown.discard(fragment)
        return self

    @staticmethod
    def _armed(fragment: str, method: str, url: str) -> bool:
        if " " in fragment:
            armed_method, _, armed_url = fragment.partition(" ")
            return armed_method.strip().upper() == method.upper() and armed_url.strip() in url
        return fragment in url

    def factory(self, *, config: Any = None, source_context: Any = None):
        def _send(request: KasRequest) -> KasResponse:
            self.calls.append(
                {
                    "method": request.method,
                    "url": request.url,
                    "purpose": request.purpose,
                    "auth": request.auth,
                    "mutating": request.mutating,
                    "headers": dict(request.headers),
                    "body": request.body,
                }
            )
            for fragment in self.deny:
                if self._armed(fragment, request.method, request.url):
                    raise TransportDeniedError("egress_denied", detail="fixture denial")
            for fragment in self.unknown:
                if self._armed(fragment, request.method, request.url):
                    raise TransportUnknownError("outcome_unproven", detail="fixture timeout")
            # Longest matching suffix wins, so a route for `/issues/41/comments` is not
            # shadowed by an earlier `/issues/41` and a page-2 listing is not answered by
            # the page-1 route its URL also contains.
            candidates = [
                (key, value)
                for key, value in self.routes.items()
                if request.method.upper() == key.partition(" ")[0].upper() and key.partition(" ")[2] in request.url
            ]
            if candidates:
                key, (status, payload, route_headers) = max(candidates, key=lambda item: len(item[0].partition(" ")[2]))
                body = payload if isinstance(payload, (bytes, bytearray)) else json.dumps(payload).encode("utf-8")
                if isinstance(payload, str):
                    body = payload.encode("utf-8")
                return KasResponse(status=int(status), body=bytes(body), headers=dict(route_headers))
            return KasResponse(status=404, body=b'{"message":"not found"}')

        return _send


def github_pull_request(number: str, *, base_sha: str, head_sha: str, head_ref: str = "feature") -> dict[str, Any]:
    return {
        "number": int(number),
        "title": "Repair the failing job",
        "state": "open",
        "draft": False,
        "mergeable_state": "clean",
        "base": {"ref": "main", "sha": base_sha},
        "head": {"ref": head_ref, "sha": head_sha},
    }


def gitlab_merge_request(number: str, *, base_sha: str, head_sha: str, head_ref: str = "feature") -> dict[str, Any]:
    return {
        "iid": int(number),
        "title": "Repair the failing job",
        "state": "opened",
        "work_in_progress": False,
        "merge_status": "can_be_merged",
        "target_branch": "main",
        "source_branch": head_ref,
        "sha": head_sha,
        "diff_refs": {"base_sha": base_sha, "head_sha": head_sha},
    }


UNIFIED_DIFF = (
    "diff --git a/calc.py b/calc.py\n"
    "--- a/calc.py\n"
    "+++ b/calc.py\n"
    "@@ -1,2 +1,2 @@\n"
    "-def add(a, b):\n"
    "-    return a - b\n"
    "+def add(a, b):\n"
    "+    return a + b\n"
)


def gitlab_changes() -> dict[str, Any]:
    return {
        "changes": [
            {
                "old_path": "calc.py",
                "new_path": "calc.py",
                "diff": "@@ -1,2 +1,2 @@\n-def add(a, b):\n-    return a - b\n+def add(a, b):\n+    return a + b\n",
            }
        ]
    }


__all__ = [
    "UNIFIED_DIFF",
    "RecordedForge",
    "github_pull_request",
    "gitlab_changes",
    "gitlab_merge_request",
]
