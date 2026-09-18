"""Project archaeology (C21): the read-only locate/search authority, tested FIRST.

These tests pin the ``core.project_archaeology`` reader contract against the REAL
corpus seeded by ``tests/project_archaeology/conftest.py`` — the nine journal
entries, the runtime receipts, the git objects and the workspace tree that answer
the C21 questions as data. They are written before the module exists (RED); the
module import is therefore done lazily inside each test so this file always
COLLECTS and never breaks the run for other files.

Provenance discipline under test: every result item carries a full provenance
block (store/object_id/hash/timestamp/authority/...), history content is always
wrapped in quarantine markers, secrets are counted and never echoed, scope is
grant-driven, and the module itself executes nothing (``execution: none``).
"""
from __future__ import annotations

import datetime
import json
import re

import pytest

from tests.project_archaeology.conftest import (
    NIGHT_WINDOW,
    SECRET_TOKEN,
    journal_count,
    receipt_key_for,
)

# The exact provenance block every result item must carry (keys always present).
RESULT_KEYS = frozenset(
    {
        "store", "object_id", "hash", "timestamp", "authority", "verified",
        "turn_id", "session_id", "effect_id", "attempt_id", "path", "kind",
        "outcome", "confidence", "availability", "excerpt", "truncated",
    }
)

CONFIDENCE_LEVELS = {"exact", "derived", "partial"}
AVAILABILITY_LEVELS = {"AVAILABLE", "WITHHELD", "ERASED", "UNKNOWN"}

_HEX40 = re.compile(r"^[0-9a-f]{40}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")

BEGIN_MARKER = "[untrusted-history:begin]"
END_MARKER = "[untrusted-history:end]"


def _import_pa():
    """Lazy import: the module does not exist yet (RED); failing here keeps collection green."""
    from core import project_archaeology as pa

    return pa


def _iso8601_or_empty(value: str) -> bool:
    if value == "":
        return True
    try:
        datetime.datetime.fromisoformat(value)
    except ValueError:
        return False
    return True


def _hash_like_or_empty(value: str) -> bool:
    """A hash is a scheme-prefixed digest or a bare hex git sha — never invented prose."""
    if value == "":
        return True
    return (
        value.startswith("sha256:")
        or bool(_HEX40.match(value))
        or bool(_HEX64.match(value))
    )


def _assert_result_item(item: dict) -> None:
    assert isinstance(item, dict)
    assert set(item) >= RESULT_KEYS, f"missing provenance keys: {RESULT_KEYS - set(item)}"
    assert isinstance(item["object_id"], str) and item["object_id"], item
    assert isinstance(item["authority"], str) and item["authority"], item
    assert isinstance(item["timestamp"], str) and _iso8601_or_empty(item["timestamp"]), item
    assert isinstance(item["hash"], str) and _hash_like_or_empty(item["hash"]), item
    assert item["confidence"] in CONFIDENCE_LEVELS, item
    assert item["availability"] in AVAILABILITY_LEVELS, item
    assert item["verified"] in (True, False, None), item
    assert isinstance(item["truncated"], bool), item
    for key in ("turn_id", "session_id", "effect_id", "attempt_id", "path", "kind", "outcome", "excerpt"):
        assert isinstance(item[key], str), (key, item)


def _assert_envelope(out: dict, pa, operation: str) -> None:
    assert out["schema"] == pa.SCHEMA == "vool.archaeology.v1"
    assert out["operation"] == operation
    assert out["untrusted_content"] is True
    assert out["execution"] == "none"
    assert isinstance(out["results"], list)
    assert out["returned"] == len(out["results"])
    assert isinstance(out["truncated"], bool)
    assert isinstance(out["limits"], dict)
    scope = out["scope"]
    assert {"workspace_root", "store_roots", "grants", "refused", "content_access", "default_scope"} <= set(scope)
    assert isinstance(scope["store_roots"], dict)
    assert isinstance(scope["grants"], list)
    assert isinstance(scope["refused"], list)
    for refused in scope["refused"]:
        assert isinstance(refused, dict) and "path" in refused and "reason" in refused, refused
    assert isinstance(out["stores_read"], list)
    assert isinstance(out["redaction"], dict) and set(out["redaction"]) >= {"secrets_detected", "excerpts_wrapped"}
    for item in out["results"]:
        _assert_result_item(item)


def test_module_pins_vool_archaeology_v1_ScopeGrant_defaults_and_typed_errors() -> None:
    pa = _import_pa()
    assert pa.SCHEMA == "vool.archaeology.v1"
    grant = pa.ScopeGrant(root="/some/root")
    assert grant.access == "metadata" and grant.reason == ""
    content_grant = pa.ScopeGrant(root="/some/root", access="content", reason="operator asked")
    assert content_grant.access == "content" and content_grant.reason == "operator asked"
    assert issubclass(pa.ArchaeologyInputError, ValueError)
    assert issubclass(pa.ReferenceNotFound, LookupError)


def test_locate_eff_push_77_finds_blackbox_items_with_chain_backed_terminal_provenance(corpus) -> None:
    pa = _import_pa()
    out = pa.locate("eff-push-77")
    _assert_envelope(out, pa, "locate")
    blackbox_items = [i for i in out["results"] if i["store"] == "blackbox"]
    assert blackbox_items, out["results"]
    assert {i["kind"] for i in blackbox_items} == {"effect_intended", "effect_terminal"}
    assert all(i["effect_id"] == "eff-push-77" for i in blackbox_items)
    terminal = next(i for i in blackbox_items if i["kind"] == "effect_terminal")
    assert terminal["outcome"] == "failed"
    assert "eff-push-77" in terminal["object_id"]
    journal_terminals = [
        e for e in corpus["blackbox"].journal.entries()
        if e.get("kind") == "effect_terminal" and e.get("effect_id") == "eff-push-77"
    ]
    assert len(journal_terminals) == 1
    real = journal_terminals[0]
    assert terminal["hash"] and terminal["hash"].endswith(real["entry_hash"])
    assert terminal["timestamp"] == real["ts"]
    assert "journal" in terminal["authority"].lower()
    assert terminal["verified"] is True  # the journal chain verifies over the corpus
    assert terminal["confidence"] == "exact"


def test_locate_resolves_full_commit_sha_AND_its_8_char_prefix_as_git_objects(corpus) -> None:
    pa = _import_pa()
    repo = corpus["git"]["repo"]
    sha_b = corpus["git"]["sha_b"]
    out = pa.locate(sha_b, workspace_root=str(repo))
    _assert_envelope(out, pa, "locate")
    git_items = [i for i in out["results"] if i["store"] == "git"]
    assert git_items, out["results"]
    assert git_items[0]["hash"] == sha_b
    assert "git_objects" in git_items[0]["authority"]
    prefixed = pa.locate(sha_b[:8], workspace_root=str(repo))
    _assert_envelope(prefixed, pa, "locate")
    derived = [i for i in prefixed["results"] if i["store"] == "git" and i["hash"] == sha_b]
    assert derived, prefixed["results"]
    assert any(i["confidence"] == "derived" for i in derived)


def test_locate_receipt_night_002_surfaces_the_push_failure_execution(corpus) -> None:
    pa = _import_pa()
    out = pa.locate(receipt_key_for(2))
    _assert_envelope(out, pa, "locate")
    receipt_items = [i for i in out["results"] if i["store"] == "tool_receipts"]
    assert receipt_items, out["results"]
    item = receipt_items[0]
    assert item["session_id"] == "sess-night"
    assert "pre-receive" in item["excerpt"]  # the execution error is what the receipt shows


def test_locate_REJECTS_blank_references_and_raises_ReferenceNotFound_for_unknown(corpus) -> None:
    pa = _import_pa()
    with pytest.raises(pa.ArchaeologyInputError):
        pa.locate("")
    with pytest.raises(pa.ArchaeologyInputError):
        pa.locate("   ")
    with pytest.raises(pa.ReferenceNotFound) as excinfo:
        pa.locate("no-such-object-xyz-000")
    assert getattr(excinfo.value, "reference", "") == "no-such-object-xyz-000"


def test_every_envelope_is_untrusted_content_with_execution_none(corpus) -> None:
    pa = _import_pa()
    located = pa.locate("eff-push-77")
    searched = pa.search(text="pre-receive")
    for out, operation in ((located, "locate"), (searched, "search")):
        _assert_envelope(out, pa, operation)
    assert located["operation"] == "locate" and searched["operation"] == "search"


def test_search_pre_receive_wraps_excerpts_in_quarantine_markers_and_never_leaks_the_secret(corpus) -> None:
    pa = _import_pa()
    out = pa.search(text="pre-receive")
    _assert_envelope(out, pa, "search")
    assert out["returned"] >= 1
    assert any(i["store"] == "blackbox" for i in out["results"])
    for item in out["results"]:
        assert BEGIN_MARKER in item["excerpt"] and END_MARKER in item["excerpt"], item
    assert SECRET_TOKEN not in json.dumps(out["results"], ensure_ascii=False)
    assert out["redaction"]["secrets_detected"] >= 1


def test_search_NIGHT_WINDOW_returns_only_night_entries(corpus) -> None:
    pa = _import_pa()
    since, until = NIGHT_WINDOW
    out = pa.search(since=since, until=until)
    _assert_envelope(out, pa, "search")
    assert out["returned"] >= 1
    assert all(i["turn_id"] != "turn-day-1" for i in out["results"])
    assert all(i["session_id"] != "sess-day" for i in out["results"])
    assert any(i["session_id"] == "sess-night" for i in out["results"])


def test_search_REJECTS_inverted_and_malformed_time_windows(corpus) -> None:
    pa = _import_pa()
    since, until = NIGHT_WINDOW
    with pytest.raises(pa.ArchaeologyInputError):
        pa.search(since=until, until=since)
    with pytest.raises(pa.ArchaeologyInputError):
        pa.search(since="not-a-timestamp")


def test_search_store_blackbox_kind_effect_terminal_returns_ONLY_terminal_entries(blackbox) -> None:
    pa = _import_pa()
    out = pa.search(store="blackbox", kind="effect_terminal")
    _assert_envelope(out, pa, "search")
    assert out["returned"] == 4  # exactly the four seeded effect_terminal entries
    assert all(i["store"] == "blackbox" and i["kind"] == "effect_terminal" for i in out["results"])


def test_search_session_sess_day_scopes_to_turn_day_1(blackbox) -> None:
    pa = _import_pa()
    out = pa.search(session_id="sess-day")
    _assert_envelope(out, pa, "search")
    assert out["returned"] == 1
    assert out["results"][0]["session_id"] == "sess-day"
    assert out["results"][0]["turn_id"] == "turn-day-1"


def test_search_path_prefix_src_scoped_finds_eff_config_08(corpus) -> None:
    pa = _import_pa()
    out = pa.search(path_prefix="src/")
    _assert_envelope(out, pa, "search")
    assert out["returned"] >= 1
    assert all(i["path"].startswith("src/") for i in out["results"])
    assert any(i["effect_id"] == "eff-config-08" for i in out["results"])


def test_search_limit_2_truncates_a_three_store_match_and_echoes_limits(corpus) -> None:
    pa = _import_pa()
    unbounded = pa.search(text="pre-receive")
    assert unbounded["returned"] >= 3  # blackbox terminal + runtime event + tool receipt
    out = pa.search(text="pre-receive", limit=2)
    _assert_envelope(out, pa, "search")
    assert out["returned"] == 2
    assert out["truncated"] is True
    assert out["limits"].get("limit") == 2


def test_locate_limit_1_truncates_eff_report_01_across_stores(corpus) -> None:
    pa = _import_pa()
    out = pa.locate("eff-report-01", limit=1)
    _assert_envelope(out, pa, "locate")
    assert out["returned"] == 1
    assert out["truncated"] is True  # blackbox + the tool receipt both match this effect
    assert out["limits"].get("limit") == 1


def test_stores_read_reports_blackbox_verified_true_entries_9_and_omits_git(blackbox, arch_home) -> None:
    pa = _import_pa()
    assert journal_count(arch_home["blackbox_root"]) == 9  # the journal really holds nine
    out = pa.search(store="blackbox")
    _assert_envelope(out, pa, "search")
    blackbox_rows = [r for r in out["stores_read"] if r["store"] == "blackbox"]
    assert len(blackbox_rows) == 1
    row = blackbox_rows[0]
    assert row["root"] == str(arch_home["blackbox_root"])
    assert row["verified"] is True
    assert row["entries"] == 9
    assert all(r["store"] != "git" for r in out["stores_read"])


def test_default_scope_grants_workspace_content_and_NEVER_opens_the_env_file(corpus) -> None:
    pa = _import_pa()
    ws = corpus["workspace"]
    out = pa.search(text="night report", workspace_root=str(ws))
    _assert_envelope(out, pa, "search")
    assert out["scope"]["default_scope"] is True
    assert out["scope"]["content_access"] is True
    found = [i for i in out["results"] if i["store"] == "workspace"]
    assert found and all(not i["path"].endswith(".env") for i in found)
    # Even a search for the secret itself must not open or surface the .env file.
    secret_hunt = pa.search(text=SECRET_TOKEN, workspace_root=str(ws))
    _assert_envelope(secret_hunt, pa, "search")
    every_path = [str(i.get("path", "")) for i in secret_hunt["results"]]
    every_path += [str(r.get("path", "")) for r in secret_hunt["scope"]["refused"]]
    assert not any(p.endswith(".env") for p in every_path)
    assert SECRET_TOKEN not in json.dumps(secret_hunt["results"], ensure_ascii=False)


def test_content_grant_extends_search_to_the_requested_root_and_metadata_access_is_refused(corpus, tmp_path) -> None:
    pa = _import_pa()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir(parents=True, exist_ok=True)
    marker_file = elsewhere / "marker-note.txt"
    MARKER = "VERBATIM-ELSEWHERE-MARKER-3f9d"
    marker_file.write_text(f"a note from outside the workspace\n{MARKER}\n", encoding="utf-8")
    ws = corpus["workspace"]

    granted = pa.search(
        text=MARKER, workspace_root=str(ws),
        grants=[{"root": str(elsewhere), "access": "content"}],
    )
    _assert_envelope(granted, pa, "search")
    hits = [i for i in granted["results"] if i["store"] == "workspace" and MARKER in i["excerpt"]]
    assert hits, granted["results"]
    assert hits[0]["path"] == str(marker_file)

    unrequested = pa.search(text=MARKER, workspace_root=str(ws))
    assert all(MARKER not in i["excerpt"] for i in unrequested["results"])

    requested_without_content = pa.search(text=MARKER, workspace_root=str(ws), grants=[{"root": str(elsewhere)}])
    assert all(MARKER not in i["excerpt"] for i in requested_without_content["results"])
    refused_paths = [str(r.get("path", "")) for r in requested_without_content["scope"]["refused"]]
    assert any(str(elsewhere) in p for p in refused_paths), requested_without_content["scope"]["refused"]


def test_provenance_fields_are_REAL_hashes_and_timestamps_never_prose(corpus) -> None:
    pa = _import_pa()
    ws = corpus["workspace"]
    envelopes = [
        pa.locate("eff-push-77"),
        pa.locate("eff-report-01"),
        pa.search(text="pre-receive", workspace_root=str(ws)),
    ]
    items = [item for envelope in envelopes for item in envelope["results"]]
    assert items
    for item in items:
        _assert_result_item(item)
        assert item["object_id"] != "" and item["authority"] != ""
        assert item["hash"] == "" or _hash_like_or_empty(item["hash"])
