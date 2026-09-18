"""UsePod: a dynamic inference marketplace reached through VOOL's own provider contracts.

The package is deliberately split by the question each module answers, so any one of them can be
replaced without touching the others:

* :mod:`core.usepod.descriptor` -- what UsePod is (origin, surfaces, headers, documented facts with
  their sources) and the one way a token-bearing URL is built and fingerprinted;
* :mod:`core.usepod.pricing` -- what it currently costs, from the live marketplace feed, validated
  and normalized in exact integer microunits, with freshness;
* :mod:`core.usepod.routing` -- which route a request may take, the price bound the owner approved,
  the request headers that enforce it downstream, and the verdict on the route that answered;
* :mod:`core.usepod.trust` -- what is and is not known about who can see a request on each route;
* :mod:`core.usepod.monetary` -- the boundary a paid dispatch must pass (owned by the monetary
  authority; unavailable until that authority is integrated);
* :mod:`core.usepod.transport` -- the immutable request envelope, the HTTP boundary for the prepaid
  and accountless x402 surfaces, and the x402 operation journal.

Nothing in this package signs, pays or holds a key. The protocol codec for Anthropic Messages is
generic and lives in :mod:`core.anthropic_messages_protocol`.
"""
