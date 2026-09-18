"""The census cannot certify a zero it is not able to see.

``_discover_http`` parsed one file and harvested string literals out of
``ast.Compare`` comparators. A route selected by
``normalized_path.startswith("/api/wallet/")`` is an ``ast.Call``, so it yielded no
row — and neither did anything behind eleven other prefixes, nor
``dispatch_dictation``, whose name carries no HTTP method.

Measured before this: **55 concrete routes catalogued nowhere, 37 of them genuinely
unowned**, including every wallet route that moves value, the mobile
device-authority surface, the operator-profile writes and the OAuth code intake —
while ``commands --check`` reported ``LEGACY_UNMIGRATED: 0``. The number was true
about the scanner and false about the product.

These tests pin both halves of the repair: the scanner sees the families, and the
families have owners. The sabotage removes the discovery and a named test goes RED,
so a future change that blinds the scanner again cannot pass quietly.
"""
from __future__ import annotations

import pytest

from core.command_registry.census import run_census

#: Surfaces that were invisible and are release-critical. Each must have a row AND
#: an owner. Named individually rather than counted, so a regression says WHICH.
MUST_BE_OWNED = [
    "http:POST:/api/wallet/approve",            # executes a transfer
    "http:POST:/api/wallet/external/submit",    # broadcasts a signed transaction
    "http:POST:/api/wallet/limits",             # widens the ceiling that bounds both
    "http:POST:/api/wallet/x402/fetch",         # pays for a resource
    "http:POST:/api/wallet/x402/retry",
    "http:POST:/api/wallet/pocket/create",      # creates a key-holding wallet
    "http:POST:/api/wallet/pocket/restore",
    "http:POST:/api/wallet/propose",
    "http:POST:/api/mobile/pairing/claim",      # mints a device grant
    "http:POST:/api/mobile/devices/revoke",     # revokes one
    "http:POST:/api/mobile/companion",          # the companion RPC door
    "http:POST:/api/auth/*",                    # OAuth authorization-code intake
    "http:POST:/api/profile/*",                 # durable operator memory writes
    "http:POST:/media-editor/*",                # reads and writes operator-named files
]


@pytest.fixture(scope="module")
def census():
    return run_census()


def _by_id(report) -> dict:
    return {item.census_id: item for item in report.items}


def test_the_previously_invisible_families_now_have_rows(census) -> None:
    rows = _by_id(census)
    missing = [cid for cid in MUST_BE_OWNED if cid not in rows]
    assert not missing, f"the scanner still cannot see these surfaces: {missing}"


def test_every_release_critical_surface_has_an_owner(census) -> None:
    rows = _by_id(census)
    unowned = {
        cid: rows[cid].classification
        for cid in MUST_BE_OWNED
        if cid in rows and rows[cid].classification == "LEGACY_UNMIGRATED"
    }
    assert not unowned, f"release-critical surfaces with no owning command: {unowned}"


def test_the_zero_is_about_the_product_not_the_scanner(census) -> None:
    """LEGACY_UNMIGRATED == 0 only counts if the scanner can see the hard cases."""
    totals = census.totals()
    assert totals["LEGACY_UNMIGRATED"] == 0, [
        i.census_id for i in census.items if i.classification == "LEGACY_UNMIGRATED"
    ]
    http_rows = [i for i in census.items if i.surface == "http"]
    # 186 measured after the repair, against 146 before it. The floor is set below
    # the measured value so adding a route does not red this, and far above the
    # pre-repair count so REMOVING the prefix walk does.
    assert len(http_rows) >= 175, (
        f"only {len(http_rows)} http rows discovered; the prefix families are missing again"
    )


def test_the_dictation_dispatcher_is_no_longer_invisible(census) -> None:
    """Its name carries no get/post/upload, which is why it was never walked."""
    rows = _by_id(census)
    dictation = [cid for cid in rows if "/api/chat/dictation" in cid]
    assert dictation, "dispatch_dictation's routes are still uncatalogued"


def test_a_family_row_is_not_emitted_where_the_leaves_are_already_visible(census) -> None:
    """A `/prefix/*` row represents what cannot be seen individually, nothing else.

    bug-report's leaves are each selected by `==`, so they have their own rows and
    a family row would double-count them while hiding nothing.
    """
    rows = _by_id(census)
    assert "http:POST:/api/bug-report/*" not in rows, (
        "a family row was emitted over leaves that are already individually visible"
    )
    assert any("/api/bug-report/" in cid for cid in rows), "the leaves themselves vanished"


def test_v1_is_not_invented_as_a_route_family(census) -> None:
    """`startswith("/v1/")` on the POST side selects a RESPONSE FORMAT, not a route.

    Emitting a family for it would invent a surface rather than reveal one.
    """
    rows = _by_id(census)
    assert "http:POST:/v1/*" not in rows, "a response-format discriminator was catalogued as a route"


# ── the discovery is load-bearing ────────────────────────────────────────────


def test_sabotage_blinding_the_prefix_walk_restores_the_false_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Remove the prefix-family walk and the hidden surfaces vanish again.

    This is the exact failure the repair exists to prevent: the census keeps
    reporting LEGACY_UNMIGRATED == 0 while dozens of real action routes are simply
    not looked at. The assertion below is on the ROWS, not on the total, because the
    total is precisely the number that stays reassuring while the truth changes.
    """
    from core.command_registry import census as census_mod

    monkeypatch.setattr(census_mod, "_discover_delegated_families", lambda *a, **k: [])
    blinded = {item.census_id for item in census_mod.run_census().items}

    still_seen = [cid for cid in MUST_BE_OWNED if cid in blinded]
    assert not still_seen, (
        "sabotage no-op: the prefix-family walk was removed and these surfaces are "
        f"still discovered, so that walk is not what reveals them: {still_seen}"
    )


# ── ownership is more than a name ─────────────────────────────────────────────


def test_every_owned_row_carries_the_authority_it_is_owned_under() -> None:
    """A `registry_command_id` alone says who owns it, not under what authority.

    The closure asks for transport, method/path, production handler, owning command
    or typed exception, permission/effect class AND proof on every row. A row naming
    an owner with no effect class is the shape that lets a `spend` route read as
    owned while nothing about the row says money moves.
    """
    from core.command_registry.census import ownership_table

    rows = ownership_table()
    owned = [r for r in rows if r["owning_command"]]
    assert owned, "no row claims an owning command at all"
    naked = [r["census_id"] for r in owned if not r["effects"] or not r["permission"]]
    assert not naked, f"rows naming an owner but no authority: {naked}"


def test_the_money_routes_declare_that_they_move_money() -> None:
    """Named individually. A count would stay green while one of these went quiet."""
    from core.command_registry.census import ownership_table

    rows = {r["census_id"]: r for r in ownership_table()}
    for census_id in (
        "http:POST:/api/wallet/approve",
        "http:POST:/api/wallet/external/submit",
        "http:POST:/api/wallet/x402/fetch",
    ):
        row = rows.get(census_id)
        assert row is not None, f"{census_id} is not in the table"
        assert row["effects"] in {"spend", "external_send", "destructive", "mutating"}, (
            f"{census_id} declares effects={row['effects']!r}, which does not say money moves"
        )
        assert row["permission"], f"{census_id} declares no permission class"
        assert row["handler"], f"{census_id} names no production handler"


def test_sabotage_blanking_the_authority_lookup_reds_a_named_test(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the effect class came from anywhere but the registry, this is a no-op."""
    from core.command_registry import census as census_mod

    monkeypatch.setattr(census_mod, "_authority_of", lambda _cid: ("", ""))
    rows = {r["census_id"]: r for r in census_mod.ownership_table()}
    assert rows["http:POST:/api/wallet/approve"]["effects"] == "", (
        "sabotage no-op: the effect class does not come from _authority_of, so the "
        "table is carrying a second copy of it"
    )
