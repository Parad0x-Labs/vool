"""P0 — ONE GOVERNED RETRIEVAL PATH, WITH THE PROVIDER NAMED.

Three surfaces reach the web for the user: the settings "Test search provider"
button, the typed live-data lane, and the generic keyless fallback. At base they
did not share a path, and two of the three could not say which provider answered.

The failures this file pins, each reproduced live on 0.5.0 before the fix:

* **The settings probe never entered the effect gateway.** `probe_search_provider`
  called the search client with no effect ledger open, so the one outbound door
  denied it with `NO_ACTIVE_LEDGER_DENIAL` *before any socket*. Its bare
  `except Exception` turned that typed refusal into `state=failed,
  detail="RemoteFetchRefusedError"`, which the settings panel renders as "The
  search did not come back usable (RemoteFetchRefusedError)". A valid, paid key
  looked broken. Every pre-existing probe test monkeypatched
  `tools.web.search_api_client.open_remote` away, so not one of them ever
  crossed the seam that actually failed — which is why the defect shipped.

* **The frozen policy was built with `copy.deepcopy`.** Any runtime object
  riding in the turn context — a lock, a client, a connection — made the freeze
  raise `TypeError: cannot pickle '_thread.lock' object`. That was recorded as
  `policy_error`, and `decide_network_fetch` turns a non-empty `policy_error`
  into a denial, so one piece of unrelated cargo revoked the whole turn's
  network authority. Fail-closed is correct for a policy input that cannot be
  read; it is not correct for cargo the policy never consults.

* **Provider identity was discarded.** `_provider_source_label` named only three
  keyless scrapers and fell through to the caller's hardcoded `"duckduckgo.com"`
  for every key-backed provider, and the receipt writer read only
  `origin_domain` from each note. A Brave-served answer was stored, receipted
  and displayed as DuckDuckGo, so no surface could prove which provider ran —
  or whether a keyed provider had run at all.

The tests are grouped by requirement. Each names what was RED at base.
"""
from __future__ import annotations

import threading
import urllib.request

import pytest

from core.effect_gateway import (
    DECISION_ALLOWED,
    DECISION_DENIED,
    EFFECT_NETWORK_FETCH,
    LIFECYCLE_SUCCEEDED,
    TurnPolicy,
    current_turn_policy,
    decide_network_fetch,
    effect_outcomes,
)
from core.remote_fetch_policy import remote_fetch_policy_scope

TURN = {"surface": "openclaw", "platform": "openclaw", "session_id": "live-search-p0"}


# --------------------------------------------------------------------------- stubs


class _SocketOpenedError(AssertionError):
    """Raised by the stub when a socket opens that the test says must not."""


def _brave_payload() -> bytes:
    return (
        b'{"web": {"results": ['
        b'{"title": "Kernel 6.19", "url": "https://www.kernel.org/", "description": "stable"},'
        b'{"title": "Release history", "url": "https://en.wikipedia.org/wiki/Linux", "description": "list"},'
        b'{"title": "endoflife", "url": "https://endoflife.date/linux", "description": "dates"}'
        b"]}}"
    )


class _Response:
    """The minimum the door and the search client read off a live response."""

    status = 200
    headers: dict = {}

    def __init__(self, body: bytes) -> None:
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, *args):
        return self._body

    def geturl(self):
        return "https://api.search.brave.com/res/v1/web/search"


@pytest.fixture
def transport(monkeypatch):
    """Replace ONLY the socket. Every policy layer above it runs for real.

    This is the whole point of the fixture: the pre-existing probe tests stubbed
    `search_api_client.open_remote`, which is the exact function that refused,
    so the refusal was invisible to them. Here the door, the gateway consult and
    the ledger all execute; the last inch is the only thing faked.
    """

    calls: list = []

    def _open(request, timeout=None, **kwargs):
        calls.append(request)
        return _Response(_brave_payload())

    monkeypatch.setattr(urllib.request, "urlopen", _open)
    return calls


@pytest.fixture
def brave_key(monkeypatch):
    """A stored Brave key, with no real credential store involved."""

    import core.credential_store as credential_store

    monkeypatch.setattr(credential_store, "get_credential", lambda slot: "BSAItest-key-value")
    monkeypatch.setattr(credential_store, "has_credential", lambda slot: slot == "search.web.brave")
    return "search.web.brave"


@pytest.fixture(autouse=True)
def _clear_probe_cache():
    """The probe rate-limits per provider; a cached verdict would hide the run."""
    from core import search_connection_state as scs

    scs._last_probe_ts.clear()
    scs._last_verdict.clear()
    yield
    scs._last_probe_ts.clear()
    scs._last_verdict.clear()


# ------------------------------------------------------- req 1: the settings probe is governed


def test_the_settings_probe_reaches_the_network_through_the_one_door(transport, brave_key):
    """REQ 1. RED at base: `state="failed"`, `detail="RemoteFetchRefusedError"`,
    and `transport` empty — the door denied before any socket because no effect
    ledger was open. The probe must open its own governed scope and get through."""
    from core.search_connection_state import STATE_OK, probe_search_provider

    verdict = probe_search_provider("brave")

    assert verdict["state"] == STATE_OK, verdict
    assert len(transport) == 1, f"the probe did not reach the transport: {transport}"


def test_the_settings_probe_names_the_provider_it_tested(transport, brave_key):
    """REQ 1 + REQ 5. A green Test must say WHICH provider answered and whether
    it ran on a key, so the panel can print "Brave Search · 3 sources" rather
    than a bare tick that proves nothing about which engine ran."""
    from core.search_connection_state import probe_search_provider

    verdict = probe_search_provider("brave")

    assert verdict["provider"] == "brave", verdict
    assert verdict["label"] == "Brave Search", verdict
    assert verdict["keyed_or_keyless"] == "keyed", verdict
    assert int(verdict["source_count"]) >= 1, verdict


def test_the_settings_probe_leaves_a_governed_receipt(transport, brave_key):
    """REQ 4. The probe is a real outbound effect, so it must leave the same
    durable, provider-attributed truth a chat retrieval leaves — not a verdict
    dict that vanishes with the HTTP response."""
    from core.search_connection_state import probe_search_provider

    verdict = probe_search_provider("brave")
    receipt = verdict.get("receipt")

    assert isinstance(receipt, dict), verdict
    assert receipt["provider_id"] == "brave", receipt
    assert receipt["keyed_or_keyless"] == "keyed", receipt
    assert receipt["lifecycle"] == LIFECYCLE_SUCCEEDED, receipt
    assert receipt["query_hash"], receipt
    assert int(receipt["source_count"]) >= 1, receipt


def test_the_settings_probe_never_carries_key_material(transport, brave_key):
    """REQ 4. Receipts and verdicts are provenance, never a second copy of the
    secret. The probe now emits more fields than it did; none may leak the key."""
    import json

    from core.search_connection_state import probe_search_provider

    blob = json.dumps(probe_search_provider("brave"))

    assert "BSAItest-key-value" not in blob, blob
    assert "X-Subscription-Token" not in blob, blob


def test_a_probe_denied_by_policy_is_not_reported_as_a_bad_key(transport, brave_key, monkeypatch):
    """REQ 7. A turn-level refusal is a POLICY fact, not a verdict on the key.
    Reporting it as `failed` sends the user to regenerate a key that is fine —
    which is exactly what the un-scoped probe did for months."""
    from core import search_connection_state as scs
    from core.remote_fetch_policy import RemoteFetchRefusedError

    def _refuse(*args, **kwargs):
        raise RemoteFetchRefusedError("network fetch denied by the permission gateway: test")

    monkeypatch.setattr("tools.web.search_api_client.open_remote", _refuse)

    verdict = scs.probe_search_provider("brave")

    assert verdict["state"] == scs.STATE_REFUSED, verdict
    assert verdict["state"] != scs.STATE_UNAUTHORIZED, verdict
    assert not transport, "a refused probe must not reach the socket"


# ------------------------------------- req 2: cargo in the context cannot revoke the network


def test_a_runtime_object_in_the_turn_context_does_not_revoke_the_network():
    """REQ 2. RED at base: `decide_network_fetch` returned
    `('denied', "policy construction failed closed: decision_context freeze:
    TypeError: cannot pickle '_thread.lock' object")`. A lock is cargo — no
    policy layer reads it — so it must not decide whether the turn may fetch."""
    context = dict(TURN)
    context["_runtime_handle"] = threading.Lock()

    with remote_fetch_policy_scope(context):
        policy = current_turn_policy()
        decision, reason = decide_network_fetch(context)

    assert policy.policy_error == "", policy.policy_error
    assert decision == DECISION_ALLOWED, (decision, reason)
    assert "pickle" not in reason.lower(), reason


@pytest.mark.parametrize(
    "cargo",
    [
        threading.Lock(),
        threading.RLock(),
        threading.Event(),
        (x for x in ()),
    ],
    ids=["lock", "rlock", "event", "generator"],
)
def test_no_unpicklable_runtime_object_revokes_the_network(cargo):
    """REQ 2, widened. The lock was the one that was hit in production; every
    other unpicklable runtime handle would have produced the identical denial,
    so the fix is pinned against the class rather than the one instance."""
    context = dict(TURN)
    context["_handle"] = cargo

    with remote_fetch_policy_scope(context):
        decision, reason = decide_network_fetch(context)

    assert decision == DECISION_ALLOWED, (decision, reason)


def test_the_projection_records_that_cargo_was_dropped_rather_than_inventing_a_value():
    """REQ 2. The projection must not silently pretend the key was absent: an
    audit reading the frozen context has to be able to tell "no such key" from
    "a value that could not be carried into a policy decision"."""
    context = dict(TURN)
    context["_runtime_handle"] = threading.Lock()

    with remote_fetch_policy_scope(context):
        frozen = current_turn_policy().decision_context

    carried = frozen["_runtime_handle"]
    assert isinstance(carried, str), carried
    assert carried.startswith("<unserializable:"), carried
    assert "lock" in carried.lower(), carried


def test_the_frozen_projection_holds_no_live_reference_to_the_caller_object():
    """REQ 2. Never deepcopy AND never retain: keeping the live handle would
    make the "frozen" policy a window onto mutable runtime state."""
    context = dict(TURN)
    handle = threading.Lock()
    context["_runtime_handle"] = handle

    with remote_fetch_policy_scope(context):
        frozen = current_turn_policy().decision_context

    assert frozen["_runtime_handle"] is not handle


# ------------------------------------------------ req 2b: fail-closed is preserved where it belongs


def test_a_policy_bearing_key_that_cannot_be_projected_still_fails_closed():
    """REQ 2, the negative control. Cargo must not deny — but a key the policy
    layer actually READS must, because a decision made on a value the gateway
    could not see is not a decision. Without this test the fix would be a
    fail-open wearing a projection's name."""
    context = dict(TURN)
    context["workspace_root"] = threading.Lock()  # a real policy input, unreadable

    with remote_fetch_policy_scope(context):
        policy = current_turn_policy()
        decision, reason = decide_network_fetch(context)

    assert policy.policy_error, "an unreadable policy input must be recorded"
    assert decision == DECISION_DENIED, (decision, reason)
    assert "policy construction failed closed" in reason, reason


def test_every_policy_input_key_is_covered_by_the_fail_closed_set():
    """REQ 2. The fail-closed set is only honest if it actually names the keys
    the policy layer reads. A key added to `mode_permission_policy` later and
    not added here would silently become cargo — deniable input treated as
    ignorable noise."""
    from core.policy_projection import POLICY_INPUT_KEYS

    for key in ("workspace_root", "workspace", "operating_mode", "session_id", "surface", "platform"):
        assert key in POLICY_INPUT_KEYS, key


# ------------------------------------------------- req 2c: existing freeze semantics preserved


def test_decision_context_still_isolates_later_caller_mutation():
    """REQ 2, regression. The deepcopy this replaces existed to stop a caller
    rewriting the turn's policy truth mid-flight. The projection must keep that
    property, nested values included."""
    context = {"surface": "openclaw", "session_id": "p0-freeze", "nested": {"hint": "plan"}}

    with remote_fetch_policy_scope(context):
        policy = current_turn_policy()
        context["nested"]["hint"] = "TAMPERED"
        context["surface"] = "TAMPERED"

        assert policy.decision_context["nested"]["hint"] == "plan", policy.decision_context
        assert policy.decision_context["surface"] == "openclaw", policy.decision_context


def test_the_frozen_projection_cannot_be_written_through():
    """REQ 2. "Immutable" as an enforced property, not a convention: a gate that
    could write into the frozen context would rewrite what the next gate reads."""
    policy = TurnPolicy(principal="owner_local", decision_context={"nested": {"k": "v"}})

    with pytest.raises(TypeError):
        policy.decision_context["nested"]["k"] = "TAMPERED"

    assert policy.decision_context["nested"]["k"] == "v"


def test_a_gate_gets_a_mutable_copy_it_cannot_leak_into_the_next_gate():
    """REQ 2. The consult needs a real mutable dict (`decide_tool_call` takes a
    `source_context`), and each consult must get its own."""
    policy = TurnPolicy(principal="owner_local", decision_context={"nested": {"k": "v"}})

    first = policy.decision_context_copy()
    first["nested"]["k"] = "TAMPERED"
    second = policy.decision_context_copy()

    assert isinstance(second, dict)
    assert second["nested"]["k"] == "v", second


def test_the_projection_survives_a_self_referencing_context():
    """REQ 2. A turn context that points at itself is ordinary (the effect scope
    writes receipts back onto it). `deepcopy` handled cycles; a naive recursive
    projection would not, and would take the turn down with a RecursionError."""
    context = dict(TURN)
    context["self"] = context

    with remote_fetch_policy_scope(context):
        decision, _reason = decide_network_fetch(context)

    assert decision == DECISION_ALLOWED


# -------------------------------------------- req 3/4: the retrieval path publishes named truth


def test_a_governed_search_records_the_provider_on_its_effect_receipt(transport, brave_key):
    """REQ 3 + 4. The effect ledger named only the HOST, and a host is not a
    provider: `api.search.brave.com` and a keyless scraper hitting `brave.com`
    are different facts. The receipt must name the provider and say whether a
    key was used."""
    from core.search_connection_state import probe_search_provider

    with remote_fetch_policy_scope(dict(TURN)):
        probe_search_provider("brave")
        outcomes = [dict(item) for item in effect_outcomes()]

    fetches = [item for item in outcomes if item["effect_class"] == EFFECT_NETWORK_FETCH]
    assert fetches, outcomes
    assert any(item.get("provider_id") == "brave" for item in fetches), fetches
    assert any(item.get("keyed_or_keyless") == "keyed" for item in fetches), fetches


def test_a_keyed_provider_is_never_labelled_as_the_keyless_default():
    """REQ 5. RED at base: `_provider_source_label("brave", hit, "duckduckgo.com")`
    returned `"duckduckgo.com"`, so a Brave-served result was stored, receipted
    and shown to the user as DuckDuckGo. Mislabelling is worse than no label —
    it is a false provenance claim the UI repeats verbatim."""
    from retrieval.web_adapter import _provider_source_label
    from tools.web.web_research import WebHit

    hit = WebHit(title="t", url="https://example.org/", snippet="s", engine="brave", score=None)
    label = _provider_source_label("brave", hit, "duckduckgo.com")

    assert "duckduckgo" not in label.lower(), label
    assert "brave" in label.lower(), label


def test_every_shipped_search_provider_can_name_itself():
    """REQ 5. The base implementation named three engines and let everything
    else fall through to the caller's label. Coverage is asserted over the
    shipped table, so a provider added later cannot silently inherit a name
    belonging to a different engine."""
    from core.search_providers import SEARCH_PROVIDERS
    from retrieval.web_adapter import _provider_source_label
    from tools.web.web_research import WebHit

    hit = WebHit(title="t", url="https://example.org/", snippet="s", engine="", score=None)
    sentinel = "CALLER-FALLBACK-NOT-A-PROVIDER"
    keyless = ["google_html", "browser_search", "searxng", "ddg_instant", "duckduckgo_html"]
    for provider_id in [*SEARCH_PROVIDERS, *keyless]:
        label = _provider_source_label(provider_id, hit, sentinel)
        assert label, provider_id
        assert label != sentinel, (provider_id, label)


def test_with_no_key_stored_the_chain_offers_no_keyed_provider(monkeypatch):
    """REQ D. Remove the key INSIDE the test and the keyed lane must disappear
    from the chain entirely — not fail at call time, and not linger as a name a
    later receipt could borrow. `keyed_providers` is the gate; this pins it."""
    import core.credential_store as credential_store

    monkeypatch.setattr(credential_store, "has_credential", lambda slot: False)
    monkeypatch.setattr(credential_store, "get_credential", lambda slot: "")

    from tools.web.web_research import keyed_search_api_providers

    with remote_fetch_policy_scope(dict(TURN)):
        assert keyed_search_api_providers() == (), keyed_search_api_providers()


def test_with_no_key_stored_the_probe_says_no_key_and_opens_no_socket(transport, monkeypatch):
    """REQ D. With the key gone the settings Test must report `no_key` and never
    reach the network. `transport` records every socket the door opens, so this
    is a claim about what happened, not about what the code intends."""
    import core.credential_store as credential_store

    monkeypatch.setattr(credential_store, "get_credential", lambda slot: "")
    monkeypatch.setattr(credential_store, "has_credential", lambda slot: False)

    from core.search_connection_state import STATE_NO_KEY, probe_search_provider

    verdict = probe_search_provider("brave")

    assert verdict["state"] == STATE_NO_KEY, verdict
    assert not transport, f"a keyless probe opened a socket: {transport}"


def test_a_keyless_note_never_carries_a_keyed_providers_name(monkeypatch):
    """REQ D, at the labelling seam. With no key stored, a result the keyless
    chain produced must not be labelled with a provider the user pays for —
    which is exactly what the old `return fallback` allowed, since the caller's
    label was a hardcoded engine name unrelated to what ran."""
    import core.credential_store as credential_store

    monkeypatch.setattr(credential_store, "has_credential", lambda slot: False)
    monkeypatch.setattr(credential_store, "get_credential", lambda slot: "")

    from core.retrieval_provenance import provider_attribution
    from retrieval.web_adapter import _provider_source_label
    from tools.web.web_research import WebHit

    hit = WebHit(title="t", url="https://example.org/", snippet="s", engine="google_html", score=None)
    label = _provider_source_label("google_html", hit, "duckduckgo.com")

    assert "brave" not in label.lower(), label
    assert provider_attribution("google_html")["keyed_or_keyless"] == "keyless"
    # And a keyed provider that has LOST its key is reported keyless, not keyed:
    # the table still knows its label, but nothing paid for this call.
    assert provider_attribution("brave")["keyed_or_keyless"] == "keyless"


def test_a_keyless_provider_labels_itself_keyless_and_never_brave():
    """REQ D. With no key stored, whatever answers is keyless and must say so.
    Borrowing the name of a keyed provider the user pays for is the worst
    version of this bug: it would prove a key works when none was used."""
    from core.retrieval_provenance import provider_attribution

    attribution = provider_attribution("google_html", has_key=False)

    assert attribution["keyed_or_keyless"] == "keyless", attribution
    assert "brave" not in attribution["provider_label"].lower(), attribution


def test_the_generic_fallback_receipt_names_the_provider_that_answered():
    """REQ 4 + 6. `finish_web_retrieval` read only `origin_domain` off each
    note, so the receipt could say three sources came back and never say who
    returned them. `search_provider` was on every note the whole time."""
    from core.retrieval_observability import begin_web_retrieval, finish_web_retrieval

    context: dict = {}
    started = begin_web_retrieval(context, kind="web_search", query="kernel version", task_id="t1")
    terminal = finish_web_retrieval(
        context,
        started,
        notes=[
            {"origin_domain": "kernel.org", "search_provider": "brave"},
            {"origin_domain": "endoflife.date", "search_provider": "brave"},
        ],
    )

    assert terminal["provider_id"] == "brave", terminal
    assert terminal["keyed_or_keyless"] in {"keyed", "keyless"}, terminal
    assert terminal["source_count"] == 2, terminal
    assert terminal["lifecycle"] == LIFECYCLE_SUCCEEDED, terminal


def test_a_failed_retrieval_receipt_is_not_reported_as_succeeded():
    """REQ 7. Attempt status must agree with tool status. A terminal that says
    SUCCEEDED for a retrieval that raised is the single most damaging lie this
    file exists to prevent, because every downstream gate trusts it."""
    from core.retrieval_observability import begin_web_retrieval, finish_web_retrieval

    context: dict = {}
    started = begin_web_retrieval(context, kind="web_search", query="kernel version", task_id="t1")
    terminal = finish_web_retrieval(context, started, failure=TimeoutError("read timed out"))

    assert terminal["status"] == "failed", terminal
    assert terminal["lifecycle"] != LIFECYCLE_SUCCEEDED, terminal
    assert terminal["source_count"] == 0, terminal


def test_a_retrieval_that_returned_nothing_is_not_a_failure_either():
    """REQ 7, the other edge. "Reached the provider and it had nothing" is a
    real, distinct outcome; collapsing it into `failed` would send the user to
    fix a key that works."""
    from core.retrieval_observability import begin_web_retrieval, finish_web_retrieval

    context: dict = {}
    started = begin_web_retrieval(context, kind="web_search", query="kernel version", task_id="t1")
    terminal = finish_web_retrieval(context, started, notes=[])

    assert terminal["status"] == "unavailable", terminal
    assert terminal["lifecycle"] != LIFECYCLE_SUCCEEDED, terminal


# ------------------------------------------------- req 6: a rescue needs its own governed receipt


def test_a_rescue_without_its_own_receipt_cannot_ground_a_current_claim():
    """REQ 6. The hidden split: the typed tool reports failure while a generic
    fallback quietly returns snippets, and the answer speaks as if the typed
    tool had worked. A fallback may rescue only if it produced its own
    successful governed receipt."""
    from core.retrieval_provenance import retrieval_supports_current_claims

    unreceipted = {"web_retrieval_receipts": []}
    assert retrieval_supports_current_claims(unreceipted) is False


def test_a_rescue_with_its_own_successful_receipt_may_ground_the_claim():
    """REQ 6, the negative control — the gate must not refuse a fallback that
    really did run and really did leave a receipt, or the rescue path is dead
    rather than governed."""
    from core.retrieval_provenance import retrieval_supports_current_claims

    receipted = {
        "web_retrieval_receipts": [
            {
                "schema": "vool.web_retrieval_receipt.v1",
                "provider_id": "google_html",
                "keyed_or_keyless": "keyless",
                "status": "available",
                "lifecycle": LIFECYCLE_SUCCEEDED,
                "source_count": 2,
            }
        ]
    }
    assert retrieval_supports_current_claims(receipted) is True


def test_an_unreceipted_web_snippet_is_not_current_evidence():
    """REQ 6, at the gate that actually decides. `turn_has_current_evidence`
    accepted a `web_derived` note on its own, so snippets from an un-receipted
    rescue search were treated as an observation and the answer stated a current
    fact on them. THE HIDDEN SPLIT, in one predicate."""
    from core.unsourced_current_claim import turn_has_current_evidence

    note = {"source_type": "web_derived", "summary": "BTC is $79,067", "origin_domain": "example.com"}

    assert turn_has_current_evidence(notes=[note], source_context={"web_retrieval_receipts": []}) is False


def test_a_receipted_web_snippet_is_current_evidence():
    """REQ 6, the negative control. The rule must not make governed retrieval
    unusable — that would trade a false claim for a mute product."""
    from core.unsourced_current_claim import turn_has_current_evidence

    note = {"source_type": "web_derived", "summary": "BTC is $79,067", "origin_domain": "example.com"}
    context = {
        "web_retrieval_receipts": [
            {
                "schema": "vool.web_retrieval_receipt.v1",
                "provider_id": "brave",
                "keyed_or_keyless": "keyed",
                "status": "available",
                "lifecycle": LIFECYCLE_SUCCEEDED,
                "source_count": 1,
            }
        ]
    }

    assert turn_has_current_evidence(notes=[note], source_context=context) is True


def test_a_non_web_observation_is_untouched_by_the_rescue_rule():
    """REQ 6, boundary. A live quote and material the user supplied reach the
    turn by their own route and were never part of the split; narrowing them
    would break lanes this P0 has no business touching."""
    from core.unsourced_current_claim import turn_has_current_evidence

    quote = {"source_type": "live_quote", "live_quote": "BTC USD 79,067", "summary": "BTC USD 79,067"}

    assert turn_has_current_evidence(notes=[quote], source_context={"web_retrieval_receipts": []}) is True


def test_a_failed_receipt_does_not_authorize_a_current_claim():
    """REQ 6/7. A receipt exists — and says the retrieval failed. Presence of a
    receipt is not the test; its terminal state is."""
    from core.retrieval_provenance import retrieval_supports_current_claims

    failed = {
        "web_retrieval_receipts": [
            {
                "schema": "vool.web_retrieval_receipt.v1",
                "provider_id": "brave",
                "status": "failed",
                "lifecycle": "failed",
                "source_count": 0,
            }
        ]
    }
    assert retrieval_supports_current_claims(failed) is False
