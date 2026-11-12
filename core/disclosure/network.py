"""Read the existing provider-specific network consent at final dispatch."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from urllib.parse import urlsplit


def route_fingerprint(state, model: str) -> str:
    bound = state.bounds.get(model)
    if state.error or bound is None:
        raise ValueError("The approved route is unavailable.")
    data = {"policy": state.policy.to_dict(), "bound": bound.to_dict()}
    return hashlib.sha256(json.dumps(data, sort_keys=True, allow_nan=False).encode()).hexdigest()


@dataclass(frozen=True)
class UsePodConsent:
    """Freshness of an already-authorized route, never spending authority.

    The adapter captures this only after the canonical authorizer (including
    its active price-wait rules) returns. No repricing or new approval occurs
    after the reservation. Global Local Only and the turn veto stay separate.
    """

    origin: str
    model: str
    route_sha: str

    def allowed(self, url: str, payload: dict) -> bool:
        from core.usepod import discovery, routing

        origin, target = urlsplit(self.origin), urlsplit(url)
        if ((origin.scheme, origin.netloc) != (target.scheme, target.netloc)
                or payload.get("model") != self.model or discovery.configured_origin() != self.origin):
            return False
        state = routing.load_route_state()
        try:
            if route_fingerprint(state, self.model) != self.route_sha:
                return False
        except ValueError:
            return False
        # Price admission and its live-task rules remain in the canonical
        # authorizer. This check only verifies that the owner has not changed
        # the route's network consent while the disclosure view was open.
        return True
