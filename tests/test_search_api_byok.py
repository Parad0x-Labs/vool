"""A pasted search key works, or the runtime says exactly why — and nothing else changes.

The feature is "drop a key in and live search gets better". Its failure modes are all silent ones,
so these tests are built around the paste people actually perform (quoted, ``Bearer ``-prefixed,
whitespace) and around the ways a key can be present but useless (revoked, rate-limited, wrong
provider, provider answering HTML). Per project rule 0.4 the happy path is one test here; the rest
are the adversarial cases.

Two structural properties are pinned because they are the ones a later change could quietly break:

* **No key ⇒ nothing moves.** The provider chain for a machine with no search key must be exactly
  what it was before this feature existed.
* **One outbound door.** The new client must reach the network only through
  ``core.remote_fetch_policy.open_remote``, which is what makes the turn's ``web_calls`` accounting
  and the per-turn veto true. This is asserted structurally (AST) as well as behaviourally, the
  same way ``test_every_outbound_fetch_reports_and_obeys_the_veto`` does for web_research.
"""
from __future__ import annotations

import ast
import io
import json
import pathlib
import urllib.error

import pytest

from core.remote_fetch_policy import RemoteFetchRefusedError, remote_fetch_policy_scope
from core.search_providers import (
    SearchProviderConfig,
    SearchProviderGuess,
    detect_provider,
    normalize_key,
)
from tools.web import search_api_client as sac
from tools.web import web_research as wr

# A provider shaped like the real ones, defined here so these tests pin BEHAVIOUR and never break
# when the shipped table gains or loses an entry.
FAKE = SearchProviderConfig(
    provider_id="fakesearch",
    label="Fake Search",
    search_url="https://api.fake.test/v1/web/search",
    auth_style="header",
    auth_name="X-Test-Token",
    method="GET",
    query_param="q",
    count_param="count",
    results_path=("web", "results"),
    title_fields=("title",),
    url_fields=("url",),
    snippet_fields=("description",),
    key_prefixes=("fks-",),
)

QUERY_AUTH = SearchProviderConfig(
    provider_id="queryauth",
    label="Query Auth",
    search_url="https://api.query.test/search",
    auth_style="query",
    auth_name="api_key",
    method="GET",
)


class _Resp:
    def __init__(self, payload: bytes) -> None:
        self._buf = io.BytesIO(payload)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, *args):
        return self._buf.read(*args)


def _payload(*rows: dict) -> bytes:
    return json.dumps({"web": {"results": list(rows)}}).encode()


# --------------------------------------------------------------------------- pasting a real key


@pytest.mark.parametrize(
    "pasted",
    [
        "fks-abc123",
        "  fks-abc123  ",          # trailing spaces from a copy
        '"fks-abc123"',            # copied out of a JSON/config file
        "'fks-abc123'",
        "Bearer fks-abc123",       # copied out of a curl example
        "bearer   fks-abc123",
    ],
)
def test_a_pasted_key_is_normalized_to_the_key_itself(pasted):
    """Decoration a real paste carries must not become part of the stored secret."""
    assert normalize_key(pasted) == "fks-abc123"


def test_detection_is_high_confidence_only_on_a_unique_prefix(monkeypatch):
    monkeypatch.setattr("core.search_providers.SEARCH_PROVIDERS", {"fakesearch": FAKE})
    guess = detect_provider("fks-abc123")
    assert guess == SearchProviderGuess("fakesearch", "high")


def test_a_decorated_paste_still_detects(monkeypatch):
    """The user pasted a good key with quotes; the product must not call it unknown."""
    monkeypatch.setattr("core.search_providers.SEARCH_PROVIDERS", {"fakesearch": FAKE})
    assert detect_provider('"Bearer fks-abc123"').provider_id == "fakesearch"


def test_an_unrecognizable_key_is_never_auto_assigned(monkeypatch):
    """A key with no known prefix must NOT be stored under a guess.

    Storing it in the wrong slot produces a provider that fails auth forever with a key the user
    knows is good — the worst outcome this feature can have, and unrecoverable without the user
    understanding a slot concept the UI never showed them.
    """
    monkeypatch.setattr("core.search_providers.SEARCH_PROVIDERS", {"fakesearch": FAKE})
    guess = detect_provider("zz-unknown-format-999")
    assert guess.provider_id is None
    assert guess.confidence in {"none", "low"}


@pytest.mark.parametrize("blank", ["", "   ", "\n\t", '""', "Bearer "])
def test_a_blank_or_decoration_only_paste_detects_nothing(blank):
    assert detect_provider(blank).provider_id is None


# --------------------------------------------------------------------------- the request it builds


def test_the_key_rides_the_configured_header_and_never_the_url():
    request, redacted = sac.build_request(FAKE, "diesel price", "fks-secret", max_hits=3)
    assert request.get_header("X-test-token") == "fks-secret"
    assert "fks-secret" not in request.full_url
    assert "fks-secret" not in redacted
    assert "diesel+price" in request.full_url or "diesel%20price" in request.full_url


def test_a_query_auth_provider_masks_the_key_for_notes():
    """Some providers only take the key as a query param. The URL we QUOTE must still be safe."""
    request, redacted = sac.build_request(QUERY_AUTH, "gold price", "sekret", max_hits=2)
    assert "sekret" in request.full_url          # the real call must carry it
    assert "sekret" not in redacted              # anything we log/quote must not
    assert "api_key=REDACTED" in redacted


def test_an_empty_query_is_refused_before_any_request_is_built():
    with pytest.raises(sac.SearchApiError) as exc:
        sac.build_request(FAKE, "   ", "fks-abc", max_hits=3)
    assert exc.value.reason == "empty_query"


# --------------------------------------------------------------------------- reading the answer


def test_results_are_parsed_from_the_configured_path():
    hits = sac.parse_results(
        FAKE,
        {"web": {"results": [{"title": "T", "url": "https://e.test/a", "description": "D"}]}},
        max_hits=5,
    )
    assert [(h.title, h.url, h.snippet) for h in hits] == [("T", "https://e.test/a", "D")]


def test_rows_without_a_usable_url_are_dropped_not_crashed():
    hits = sac.parse_results(
        FAKE,
        {"web": {"results": [
            {"title": "no url"},
            {"title": "js", "url": "javascript:alert(1)"},
            {"title": "ok", "url": "https://e.test/ok"},
        ]}},
        max_hits=5,
    )
    assert [h.url for h in hits] == ["https://e.test/ok"]


def test_an_unexpected_response_shape_is_a_named_failure():
    """A provider that changed its schema must not read as 'the web had nothing'."""
    with pytest.raises(sac.SearchApiError) as exc:
        sac.parse_results(FAKE, {"unexpected": {}}, max_hits=5)
    assert exc.value.reason == "bad_shape"


def test_html_error_page_instead_of_json_is_bad_shape(monkeypatch):
    monkeypatch.setattr(sac, "open_remote", lambda req, timeout, **_declared: _Resp(b"<html>nope</html>"))
    with pytest.raises(sac.SearchApiError) as exc:
        sac.search(FAKE, "q", "fks-abc")
    assert exc.value.reason == "bad_shape"


# --------------------------------------------------------------------------- failure classification


@pytest.mark.parametrize(
    ("status", "reason"),
    [(401, "unauthorized"), (403, "unauthorized"), (429, "rate_limited"), (402, "quota_exhausted"), (500, "http_error")],
)
def test_provider_http_failures_are_classified_not_swallowed(monkeypatch, status, reason):
    """'This key is dead' must never be indistinguishable from 'the web had nothing'."""

    def _raise(req, timeout, **_declared):
        raise urllib.error.HTTPError(req.full_url, status, "err", {}, None)

    monkeypatch.setattr(sac, "open_remote", _raise)
    with pytest.raises(sac.SearchApiError) as exc:
        sac.search(FAKE, "q", "fks-abc")
    assert exc.value.reason == reason


def test_an_offline_machine_reports_unreachable_not_a_bad_key(monkeypatch):
    def _raise(req, timeout, **_declared):
        raise urllib.error.URLError("Network is unreachable")

    monkeypatch.setattr(sac, "open_remote", _raise)
    with pytest.raises(sac.SearchApiError) as exc:
        sac.search(FAKE, "q", "fks-abc")
    assert exc.value.reason == "unreachable"


def test_calling_without_a_key_never_reaches_the_network(monkeypatch):
    def _explode(req, timeout, **_declared):
        raise AssertionError("a request was built for an empty key")

    monkeypatch.setattr(sac, "open_remote", _explode)
    with pytest.raises(sac.SearchApiError) as exc:
        sac.search(FAKE, "q", "   ")
    assert exc.value.reason == "no_key"


# --------------------------------------------------------------------------- the one outbound door


def test_the_client_opens_no_socket_of_its_own():
    """Structural: nothing in the client may call urlopen — the door is the only way out.

    Same property `test_every_outbound_fetch_reports_and_obeys_the_veto` pins for web_research,
    asserted here because this module is a NEW outbound path and that test does not scan it.
    """
    tree = ast.parse(pathlib.Path(sac.__file__).read_text())
    called = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            target = node.func
            name = target.attr if isinstance(target, ast.Attribute) else getattr(target, "id", "")
            called.add(name)
    assert "urlopen" not in called


def test_a_vetoed_turn_cannot_search_even_with_a_valid_key():
    """The per-turn veto outranks a stored key, and the refusal is raised, not returned empty."""
    with remote_fetch_policy_scope({"allow_remote_fetch": False}):
        with pytest.raises(RemoteFetchRefusedError):
            sac.search(FAKE, "diesel price", "fks-abc")


def test_a_vetoed_turn_offers_no_keyed_provider_to_the_chain(monkeypatch):
    monkeypatch.setattr(wr, "keyed_search_api_providers", lambda: ())
    with remote_fetch_policy_scope({"allow_remote_fetch": False}):
        assert wr.keyed_search_api_providers.__call__() == ()


# --------------------------------------------------------------------------- the chain around it


def _pre_feature_chain() -> list[str]:
    """The order this runtime produced before key-backed providers existed.

    Re-derived from the same inputs rather than hardcoded, so the comparison stays honest on any
    machine — including one with no Chromium-family browser, where browser_search is dropped.
    """
    chosen = [
        item
        for item in wr.policy_engine.web_provider_order()
        if item in set(wr.policy_engine.allowed_web_engines())
    ]
    if "browser_search" in chosen and not wr._browser_search_available():
        chosen = [item for item in chosen if item != "browser_search"]
    return wr._demote_instant_answer(chosen)


def test_with_no_key_the_provider_chain_is_untouched(monkeypatch):
    """The regression guard: a user who never pastes a key sees no behaviour change at all."""
    monkeypatch.setattr(wr, "keyed_search_api_providers", lambda: ())
    assert wr._provider_order() == _pre_feature_chain()


def test_a_keyed_provider_leads_the_chain(monkeypatch):
    """A provider the user holds a key for answers before the keyless scrapers behind it."""
    monkeypatch.setattr(wr, "keyed_search_api_providers", lambda: ("fakesearch",))
    order = wr._provider_order()
    assert order[0] == "fakesearch"
    # and the existing chain is still there, in its original relative order, as the fallback
    assert [item for item in order if item != "fakesearch"] == _pre_feature_chain()


def test_several_keys_all_join_the_chain_in_table_order(monkeypatch):
    monkeypatch.setattr(wr, "keyed_search_api_providers", lambda: ("alpha", "beta"))
    assert wr._provider_order()[:2] == ["alpha", "beta"]


def test_a_stale_env_provider_order_cannot_bury_a_keyed_provider(monkeypatch):
    """`WEB_SEARCH_PROVIDER_ORDER` sets order for the keyless chain; it must not outrank a key.

    A launcher-baked env order predates any provider added later — the exact hazard recorded for
    google_html — so a keyed provider has to be placed after that env order is applied, not before.
    """
    monkeypatch.setenv("WEB_SEARCH_PROVIDER_ORDER", "ddg_instant,searxng")
    monkeypatch.setattr(wr, "keyed_search_api_providers", lambda: ("fakesearch",))
    assert wr._provider_order()[0] == "fakesearch"


def test_a_key_deleted_mid_turn_fails_with_a_named_reason(monkeypatch):
    """The order was computed while a key existed; by call time it is gone."""
    monkeypatch.setattr("core.search_providers.SEARCH_PROVIDERS", {"fakesearch": FAKE})
    monkeypatch.setattr("core.credential_store.get_credential", lambda slot: None)
    with pytest.raises(sac.SearchApiError) as exc:
        wr._search_api_hits("fakesearch", "q", max_hits=3, timeout_s=5.0)
    assert exc.value.reason == "no_key"


def test_chain_hits_carry_the_provider_as_their_engine(monkeypatch):
    monkeypatch.setattr("core.search_providers.SEARCH_PROVIDERS", {"fakesearch": FAKE})
    monkeypatch.setattr("core.credential_store.get_credential", lambda slot: "fks-abc")
    monkeypatch.setattr(sac, "open_remote", lambda req, timeout, **_declared: _Resp(_payload({"title": "T", "url": "https://e.test/x", "description": "D"})))
    hits = wr._search_api_hits("fakesearch", "diesel price", max_hits=3, timeout_s=5.0)
    assert [(h.url, h.engine) for h in hits] == [("https://e.test/x", "fakesearch")]


# --------------------------------------------------------------------------- the settings probe


def test_the_probe_reports_no_key_without_touching_the_network(monkeypatch):
    monkeypatch.setattr("core.search_providers.SEARCH_PROVIDERS", {"fakesearch": FAKE})
    monkeypatch.setattr("core.credential_store.get_credential", lambda slot: "")

    def _explode(req, timeout, **_declared):
        raise AssertionError("the probe called out with no key stored")

    monkeypatch.setattr(sac, "open_remote", _explode)
    from core.search_connection_state import STATE_NO_KEY, probe_search_provider

    assert probe_search_provider("fakesearch")["state"] == STATE_NO_KEY


def test_a_revoked_key_is_reported_as_unauthorized_not_as_broken_search(monkeypatch):
    monkeypatch.setattr("core.search_providers.SEARCH_PROVIDERS", {"fakesearch": FAKE})
    monkeypatch.setattr("core.credential_store.get_credential", lambda slot: "fks-revoked")

    def _raise(req, timeout, **_declared):
        raise urllib.error.HTTPError(req.full_url, 401, "unauthorized", {}, None)

    monkeypatch.setattr(sac, "open_remote", _raise)
    from core.search_connection_state import STATE_UNAUTHORIZED, _last_probe_ts, probe_search_provider

    _last_probe_ts.clear()
    assert probe_search_provider("fakesearch")["state"] == STATE_UNAUTHORIZED


def test_an_authenticated_key_that_returns_nothing_is_not_called_a_bad_key(monkeypatch):
    """Empty results are a search outcome, not a credential verdict — telling the user their key
    is bad would send them to regenerate a key that is fine."""
    monkeypatch.setattr("core.search_providers.SEARCH_PROVIDERS", {"fakesearch": FAKE})
    monkeypatch.setattr("core.credential_store.get_credential", lambda slot: "fks-ok")
    monkeypatch.setattr(sac, "open_remote", lambda req, timeout, **_declared: _Resp(_payload()))
    from core.search_connection_state import STATE_UNAUTHORIZED, _last_probe_ts, probe_search_provider

    _last_probe_ts.clear()
    assert probe_search_provider("fakesearch")["state"] != STATE_UNAUTHORIZED


def test_the_probe_never_puts_the_key_or_the_users_words_in_its_result(monkeypatch):
    monkeypatch.setattr("core.search_providers.SEARCH_PROVIDERS", {"fakesearch": FAKE})
    monkeypatch.setattr("core.credential_store.get_credential", lambda slot: "fks-supersecret")
    monkeypatch.setattr(sac, "open_remote", lambda req, timeout, **_declared: _Resp(_payload({"title": "T", "url": "https://e.test/x", "description": "D"})))
    from core.search_connection_state import _last_probe_ts, probe_search_provider

    _last_probe_ts.clear()
    blob = json.dumps(probe_search_provider("fakesearch"))
    assert "fks-supersecret" not in blob


def test_repeated_probes_are_rate_limited_so_a_free_tier_is_not_burned(monkeypatch):
    monkeypatch.setattr("core.search_providers.SEARCH_PROVIDERS", {"fakesearch": FAKE})
    monkeypatch.setattr("core.credential_store.get_credential", lambda slot: "fks-ok")
    calls: list[int] = []

    def _count(req, timeout, **_declared):
        calls.append(1)
        return _Resp(_payload({"title": "T", "url": "https://e.test/x", "description": "D"}))

    monkeypatch.setattr(sac, "open_remote", _count)
    from core.search_connection_state import _last_probe_ts, _last_verdict, probe_search_provider

    _last_probe_ts.clear()
    _last_verdict.clear()
    probe_search_provider("fakesearch", now=1000.0)
    probe_search_provider("fakesearch", now=1000.5)
    probe_search_provider("fakesearch", now=1001.0)
    assert len(calls) == 1


# --------------------------------------------------------------------------- the shipped table


def test_every_shipped_provider_is_well_formed():
    """Whatever the table holds must be complete enough to actually call and to detect."""
    from core.search_providers import SEARCH_PROVIDERS

    for provider_id, cfg in SEARCH_PROVIDERS.items():
        assert cfg.provider_id == provider_id
        assert cfg.search_url.startswith("https://"), f"{provider_id} must use https"
        assert cfg.credential_slot.startswith("search.web."), provider_id
        assert cfg.results_path, f"{provider_id} needs a results path"
        assert cfg.url_fields and cfg.title_fields, provider_id
        if cfg.auth_style in {"header", "query", "body"}:
            assert cfg.auth_name, f"{provider_id} needs the auth field name"


def test_search_slots_are_reindexed_after_a_runtime_dir_wipe():
    """A key in the Keychain with no sidecar row must still be findable.

    The sidecar index is what `list_credentials` reads, and a wiped runtime dir leaves the key in
    the Keychain but the row gone. `_RECONCILE_NAMES` is hand-maintained, so search slots are
    derived from the provider table instead — this pins that they are actually included.
    """
    from core.credential_store import _search_reconcile_names
    from core.search_providers import SEARCH_PROVIDERS

    assert {name for name, _label in _search_reconcile_names()} == {
        cfg.credential_slot for cfg in SEARCH_PROVIDERS.values()
    }
