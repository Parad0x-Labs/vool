"""VOOL Settings surface: the page, its declarative model, and the promises it makes.

These pin the properties that make the redesign trustworthy rather than merely present:

* every control writes to an authority that ALREADY exists (no second settings store);
* opening Settings reads nothing that could reach a provider, a model or the Keychain;
* no group is empty and no row is unsearchable;
* the copy that describes a gate matches what the gate actually does.
"""
from __future__ import annotations

import json

import pytest

from core.vool_settings_page import render_vool_settings_html, settings_groups

# The endpoints Settings is allowed to write through. Each one already forwards into the command
# registry (settings.prefs.set / settings.credentials.set) or an equivalent existing authority.
# A row that writes anywhere else is a second settings store and must fail this suite.
AUTHORISED_WRITE_URLS = {
    "/api/settings/prefs",
    "/api/settings/credentials",
}

# Reads that are plain local file/state reads. Anything NOT in here has to justify itself, because
# Settings must not probe a provider, launch a model, or touch the Keychain just by being opened.
LOCAL_READ_URLS = {
    "/api/settings/prefs",
    "/api/runtime/version",
    # The Operator Profile store: a local SQLite read. No provider, no model, no Keychain.
    "/api/profile",
    # The derived first-run setup state (core/setup_progress.py): preference and project files,
    # the provider-choice file, credential NAMES from the index files, the profile store. It reads
    # no secret and never calls credential_store.has_credential/list_credentials (which may
    # reconcile with the Keychain) -- pinned by tests/test_setup_progress.py.
    "/api/setup/state",
}
# Reads that DO reach a sensitive subsystem and are therefore allowed only lazily, never at boot.
LAZY_ONLY_READ_URLS = {
    # credential_store.list_credentials() calls _reconcile_index_with_keychain(), which asks the
    # macOS Keychain for passwords. Opening Settings must not do that.
    "/api/settings/credentials",
}


def _rows():
    for group in settings_groups():
        for row in group["rows"]:
            yield group, row


def test_every_group_has_rows_and_an_identity() -> None:
    """No empty categories: a group with nothing in it advertises a capability VOOL lacks."""
    groups = settings_groups()
    assert groups, "the settings model is empty"
    seen: set[str] = set()
    for group in groups:
        assert group["rows"], f"group {group['id']} is empty"
        assert group["title"] and group["blurb"], f"group {group['id']} has no title or blurb"
        assert group["id"] not in seen, f"duplicate group id {group['id']}"
        seen.add(group["id"])


def test_every_row_is_addressable_and_searchable() -> None:
    ids: set[str] = set()
    for _group, row in _rows():
        assert row["id"] not in ids, f"duplicate row id {row['id']}"
        ids.add(row["id"])
        assert row.get("label"), f"{row['id']} has no label"
        assert row.get("help"), f"{row['id']} has no explanatory text"
        # Search matches labels AND explanatory text; keywords carry the words a user would type
        # that the label does not contain ("api key" for a row labelled "Provider keys").
        assert row.get("keywords"), f"{row['id']} has no search keywords"


def test_no_row_writes_to_an_unauthorised_endpoint() -> None:
    """The load-bearing one: Settings owns no state. Every write goes to an endpoint that already
    carries that value's gate and receipt."""
    for _group, row in _rows():
        write = row.get("write")
        if not write:
            continue
        assert write["url"] in AUTHORISED_WRITE_URLS, (
            f"{row['id']} writes to {write['url']}, which is not an existing settings authority"
        )
        assert write.get("field"), f"{row['id']} has a write with no field"
        assert write.get("type") in {"str", "int", "bool"}, f"{row['id']} has an untyped write"


def test_a_writable_row_can_be_read_back() -> None:
    """A control that can be changed must be readable from the same authority, or the page cannot
    tell a saved value from a refused one."""
    for _group, row in _rows():
        if not row.get("write"):
            continue
        read = row.get("read")
        assert read and read.get("url"), f"{row['id']} is writable but has no read source"
        assert read["url"] == row["write"]["url"], (
            f"{row['id']} reads {read['url']} but writes {row['write']['url']}; a readback across "
            "two authorities cannot confirm the write"
        )


def test_opening_settings_touches_no_sensitive_subsystem() -> None:
    """Boot reads only the FIRST group's sources. The credentials read reaches the Keychain, so it
    must not belong to the group Settings opens on."""
    first = settings_groups()[0]
    boot_reads = {r["read"]["url"] for r in first["rows"] if r.get("read")}
    leaked = boot_reads & LAZY_ONLY_READ_URLS
    assert not leaked, f"opening Settings would read {sorted(leaked)} — that reaches the Keychain"
    assert boot_reads <= LOCAL_READ_URLS, f"unexpected boot read: {sorted(boot_reads - LOCAL_READ_URLS)}"


def test_the_page_reads_lazily_rather_than_all_at_once() -> None:
    """The mechanism behind the promise above: there is no read-everything call, and navigation
    fetches one group's sources."""
    html = render_vool_settings_html()
    assert "function ensureGroupSources" in html
    assert "await ensureGroupSources(groupId)" in html
    assert "readAll(" not in html, "a read-all would defeat the lazy-read guarantee"


def test_a_refused_write_is_never_reported_as_saved() -> None:
    """The page verifies against the authority instead of trusting a 200: it re-reads the source
    and compares. Both the refusal branch and the normalised-value branch must exist."""
    html = render_vool_settings_html()
    assert "const refused = !r.ok || (j && (j.error || j.ok === false));" in html
    assert "'failed', 'Not saved — ' + why" in html
    # The clamp/normalise branch: the authority may store something other than what was asked for.
    assert "'VOOL stored ' + JSON.stringify(stored) + ' instead.'" in html
    assert "if (!same(stored, value))" in html


def test_the_page_is_self_contained() -> None:
    """It has to work inside a packaged installer with no network: no CDN, no external font, no
    build step."""
    html = render_vool_settings_html(build_commit="deadbeef")
    assert "<title>VOOL Settings</title>" in html
    assert "deadbeef" in html
    assert "__SETTINGS_MODEL__" not in html, "the model placeholder was not substituted"
    for marker in ("src=\"http", "href=\"http", "@import", "cdn."):
        assert marker not in html, f"the page reaches outside itself: {marker}"


def test_the_declared_model_reaches_the_page() -> None:
    html = render_vool_settings_html()
    for group in settings_groups():
        assert json.dumps(group["title"])[1:-1] in html
        for row in group["rows"]:
            assert json.dumps(row["label"])[1:-1] in html


def test_autonomy_copy_matches_the_gate_it_describes() -> None:
    """The shipped chat modal labels hands_off as "ask before acting". core/execution_gate.py
    grants it the FEWEST approvals of the three, so that label is backwards. This pins the
    corrected ordering against the real gate rather than against prose."""
    from core.execution_gate import ExecutionGate

    fn = ExecutionGate._requires_explicit_approval

    def asks(mode: str, action: str, **kw) -> bool:
        from unittest import mock

        with mock.patch("core.execution_gate.effective_autonomy_mode", return_value=mode):
            return fn(action, **kw)

    # A routine local action nobody special-cases.
    assert asks("strict", "read_file") is True
    assert asks("balanced", "read_file") is False
    assert asks("hands_off", "read_file") is False
    # A destructive one separates balanced from hands_off.
    assert asks("balanced", "delete_path", destructive=True) is True
    assert asks("hands_off", "delete_path", destructive=True) is False
    # Therefore: hands_off asks about a SUBSET of what balanced asks about, which is a subset of
    # strict. The Settings copy must not claim hands_off is the one that asks.
    options = {}
    for _group, row in _rows():
        if row["id"] == "autonomy_mode":
            options = {o["value"]: o["label"] for o in row["options"]}
    assert options, "the autonomy row disappeared"
    assert "ask before acting" not in options["hands_off"].lower()
    assert "every action" in options["strict"].lower()


def test_outward_facing_actions_always_confirm_whatever_the_mode() -> None:
    """The Settings copy promises this. Pin it against the gate."""
    from unittest import mock

    from core.execution_gate import ExecutionGate

    for mode in ("hands_off", "balanced", "strict", "auto"):
        with mock.patch("core.execution_gate.effective_autonomy_mode", return_value=mode):
            assert ExecutionGate._requires_explicit_approval("discord_post", outward_facing=True) is True
            assert ExecutionGate._requires_explicit_approval("read_contacts", privacy_sensitive=True) is True


def test_guidance_and_enforced_limits_are_labelled_apart() -> None:
    """A number the runtime enforces and a number it merely knows about must not look alike."""
    badges = {row["id"]: row.get("badge") for _g, row in _rows()}
    assert badges["ram_reserve_pct"] == "Enforced"
    assert badges["daily_token_budget"] == "Guidance only"


def test_the_token_budget_copy_does_not_claim_enforcement() -> None:
    for _group, row in _rows():
        if row["id"] == "daily_token_budget":
            help_text = row["help"].lower()
            assert "not a cut-off it enforces" in help_text
            return
    pytest.fail("the daily token budget row disappeared")


def test_the_route_serves_the_settings_surface() -> None:
    from core.web.api.runtime import RuntimeServices
    from core.web.api.service import dispatch_get

    res = dispatch_get(
        path="/settings", query={}, runtime=RuntimeServices(display_name="VOOL"),
        model_name="vool", client_host="127.0.0.1",
    )
    assert res.status == 200
    assert res.headers.get("X-Vool-Workstation-Surface") == "settings"
    assert b"<title>VOOL Settings</title>" in (res.body if isinstance(res.body, bytes) else res.body.encode())


def test_the_chat_window_points_at_the_new_surface() -> None:
    """The gear, and Cmd+comma, reach /settings — the native window when there is one, a tab when
    there is not. The legacy panel stays as the last resort so nothing becomes unreachable."""
    from core.vool_chat_page import render_vool_chat_html

    html = render_vool_chat_html()
    assert "window.pywebview.api.open_settings" in html
    assert "window.open('/settings' + frag, 'vool-settings')" in html
    assert "openLegacySettingsPanel()" in html
    assert "e.key === ','" in html
    # The guided setup opens the same three ways, in the same order, and reuses the same frame.
    assert "window.pywebview.api.open_setup" in html
    assert "window.open('/setup' + frag, 'vool-setup')" in html
    assert "openSurfaceInFrame('/setup' + frag, 'VOOL Setup')" in html


def test_the_native_window_reuses_one_settings_window() -> None:
    """Reopening focuses what exists; it never stacks a second window. Read from the source of the
    bundle window, which cannot be imported without pywebview."""
    from pathlib import Path

    src = Path(__file__).resolve().parents[1] / "installer" / "bundle" / "vool_window.py"
    text = src.read_text()
    assert "def open_settings" in text
    assert "existing = self._settings_window" in text and "_focus_window(existing)" in text
    assert "SETTINGS_URL" in text and 'f"{_API_ORIGIN}/settings"' in text
    # Closing Settings must not be able to veto Cmd+Q: the handler is on `closed`, not `closing`.
    assert "events.closed += self._on_settings_closed" in text
    assert "events.closing" not in text.split("def open_settings", 1)[1].split("def pick_folder", 1)[0]
    # A pywebview without the menu API must still give a window.
    assert "except TypeError:" in text and "webview.start(_post_start_all)" in text


def test_a_section_deep_link_selects_that_group() -> None:
    """/settings#memory opens on Memory. This is how a caller outside the Settings window points a
    user at one section, now that scrolling into another window's document is impossible."""
    html = render_vool_settings_html()
    assert "function sectionFromHash" in html
    from tests.chat_page_js_harness import DOM, run_node
    start = html.index('function sectionFromHash()')
    end = html.index('async function boot()', start)
    result = run_node(DOM + "const MODEL=[{id:'models',rows:[{id:'usepod'}]},{id:'memory',rows:[]}]; const state={};" + html[start:end] + """
location.hash='#models/usepod/spend';const nested=sectionFromHash(), row=state.onlyRow, part=state.focusPart;
location.hash='#memory';const simple=sectionFromHash();
location.hash='#unknown/usepod/spend';const unknown=sectionFromHash();
out({nested,row,part,simple,unknown,onlyRow:state.onlyRow});
""")
    assert result == {'nested':'models','row':'usepod','part':'spend','simple':'memory','unknown':'','onlyRow':'', 'errors': [], 'drove': []}
    assert "window.addEventListener('hashchange'" in html
    from pathlib import Path

    window = (Path(__file__).resolve().parents[1] / "installer" / "bundle" / "vool_window.py").read_text()
    assert 'f"{SETTINGS_URL}#{wanted}" if wanted else SETTINGS_URL' in window
    # The fragment reaching the native bridge is allowlisted, so a page cannot turn it into a
    # navigation of its own choosing.
    assert 'wanted.replace("_", "").replace("-", "").isalnum()' in window
    assert "existing.load_url(url)" in window


# Values the Operator Profile owns. core/user_preferences.py:migrate_legacy_profile_fields moves
# these OUT of user_preferences.json at every daemon boot and empties the JSON field, and their
# readers (user_address(), email_signature()) resolve from the profile. A Settings control that
# wrote them as preferences would look empty after a restart while the real value stayed in
# effect -- the exact requested-vs-effective gap this surface exists to remove.
PROFILE_OWNED = {"user_address", "email_signature"}


def test_no_row_writes_a_value_the_operator_profile_owns() -> None:
    for _group, row in _rows():
        write = row.get("write")
        if not write:
            continue
        assert write["field"] not in PROFILE_OWNED, (
            f"{row['id']} writes {write['field']} as a preference, but the Operator Profile owns it "
            "(migrate_legacy_profile_fields empties that JSON field at boot)"
        )
        read = row.get("read") or {}
        assert read.get("field") not in PROFILE_OWNED, (
            f"{row['id']} reads {read.get('field')} from the preferences file, which is emptied at boot"
        )


def test_the_prefs_endpoint_reads_back_exactly_what_it_accepts() -> None:
    """The authority's write-set and read-set must be the same set.

    A field the endpoint ACCEPTS but does not return cannot be shown, so a control over it can
    never tell a save from a refusal. A field it RETURNS but will not accept renders a control
    that always 400s. Both are read straight out of the code rather than restated here.
    """
    import inspect
    import re

    from core.command_registry.groups.convergence import _handle_prefs_list
    from core.web.api.registry_authorities import set_prefs_authority

    writable = set(re.findall(r'"([a-z_]+)"', inspect.getsource(set_prefs_authority).split("allowed =")[1].split("\n")[0]))
    src = inspect.getsource(set_prefs_authority)
    for group in ("_BOOL_PREFS", "_INT_PREFS", "_ENUM_PREFS", "_TEXT_PREFS"):
        block = src.split(group + " = {", 1)[1].split("}", 1)[0]
        writable |= set(re.findall(r'"([a-z_]+)"', block))
    writable.add("user_address")   # accepted, but routed to the profile rather than stored here

    readable = set(re.findall(r'"([a-z_]+)":', inspect.getsource(_handle_prefs_list).split("data={", 1)[1]))

    assert writable == readable, (
        f"write-only: {sorted(writable - readable)}; read-only: {sorted(readable - writable)}"
    )
    # And every field the page binds is in that agreed set.
    bound = {r["write"]["field"] for _g, r in _rows() if r.get("write")}
    assert bound <= writable, f"the page binds fields the authority rejects: {sorted(bound - writable)}"


def test_keys_are_their_own_group_because_the_store_holds_more_than_models() -> None:
    """One credential store holds llm.cloud.* AND search.web.*, so keys are not a model setting."""
    from core.credential_store import _RECONCILE_NAMES, _search_reconcile_names

    families = {n.split(".")[0] for n, _ in (*_RECONCILE_NAMES, *_search_reconcile_names())}
    assert {"llm", "search"} <= families, f"expected at least two key families, got {families}"

    groups = {g["id"]: g for g in settings_groups()}
    assert "keys" in groups, "credentials no longer have their own group"
    key_rows = {r["id"] for r in groups["keys"]["rows"]}
    assert "cloud_keys" in key_rows
    # and they are NOT also sitting under Models & Providers
    model_rows = {r["id"] for r in groups["models"]["rows"]}
    assert "cloud_keys" not in model_rows, "the key control is duplicated under Models & Providers"


def test_a_runtime_minted_key_is_listed_but_never_removable() -> None:
    """The credential store also holds keys VOOL minted for itself. blackbox.cas.keys is the
    AES-256-GCM keyring for the Blackbox CAS: deleting it makes every sealed blob unreadable and
    there is no plaintext fallback. Such a slot must be visible (hiding it would be its own lie)
    and must NOT carry a Remove button."""
    from core.blackbox.coverage.cas_keys import KEYRING_CREDENTIAL_NAME

    assert KEYRING_CREDENTIAL_NAME == "blackbox.cas.keys"
    # It belongs to none of the operator-supplied families, so the page classifies it as managed.
    assert not KEYRING_CREDENTIAL_NAME.startswith(("llm.cloud.", "search.web."))

    html = render_vool_settings_html()
    assert "function isOperatorKey" in html
    # The guard returns BEFORE the Remove button is built.
    guard = html.split("if (!isOperatorKey(c.name)) {", 1)[1].split("}", 1)[0]
    assert "return;" in guard, "a managed key would fall through to the Remove button"
    assert "'system'" in guard, "a managed key is not marked as one"
    assert html.index("function isOperatorKey") < html.index("btn danger")
