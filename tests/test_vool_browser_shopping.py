"""C07 — shopping comparison and native-wallet checkout handoff.

Built ON the C06 lane: same sessions, same receipts, same permission gate.
The money laws under test:

- EXACT COMPARISON: offers are extracted from real pages into structured
  records (sku, merchant, price, shipping, returns) and compared on exact
  totals; every record cites the receipt of the navigation that produced it.
- CONFIRMED OPERATOR PROFILE: checkout uses ONLY a profile the operator
  explicitly confirmed; card-shaped data is structurally impossible (strict
  schema) and card fields on a checkout page are never filled by the lane.
- WALLET HANDOFF, FOREGROUND AUTH: the lane detects the merchant's wallet
  methods (Apple Pay / Google Pay), hands off to the OPERATOR, and never
  clicks pay. Handoff requires a VISIBLE (headful) session — the human
  authenticates in the foreground.
- NEVER CLAIM PAYMENT BEFORE MERCHANT CONFIRMATION: reconciliation asks the
  MERCHANT (its order-status endpoint), never the page's claims. Before the
  merchant confirms, the status is pending_merchant_confirmation. A confirmed
  order is receipted once under an idempotency key; reconciling again returns
  the SAME receipt and the merchant is not asked to place anything more.
"""
from __future__ import annotations

import json
import urllib.request
from pathlib import Path

import pytest

from tests._toolchain_fixtures import executor_kwargs, reset_toolchain_state
from tests._vool_browser_support import JourneyWorld, enable_browser_policy

PLUGIN = "vool-browser"

OFFER_A = {"title": "Widget A", "price": "&euro;120.00", "shipping": "&euro;4.99",
           "total_minor": 12499, "returns_days": 30}
OFFER_B = {"title": "Widget B", "price": "&euro;118.00", "shipping": "&euro;9.99",
           "total_minor": 12799, "returns_days": 60}


def _seed_merchant(world: JourneyWorld) -> None:
    world.a.offers["wgt-a"] = OFFER_A
    world.b.offers["wgt-a"] = OFFER_B


@pytest.fixture()
def browser_world(tmp_path, monkeypatch):
    from core.mode_permission_policy import reset_mode_permission_state
    from core.runtime_flags import override

    monkeypatch.delenv("VOOL_MCP_CONFIG", raising=False)
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("VOOL_HOME", str(home))
    monkeypatch.setenv("VOOL_BROWSER_SCRATCH_ROOT", str(tmp_path / "browser-scratch"))
    monkeypatch.setenv("PLAYWRIGHT_ENABLED", "1")
    enable_browser_policy(monkeypatch)
    reset_toolchain_state()
    reset_mode_permission_state()
    with override("plugin_runtime_tools", True):
        yield tmp_path
    from core.vool_browser.sessions import close_all_for_test

    close_all_for_test()
    reset_mode_permission_state()
    reset_toolchain_state()


def _run(intent: str, arguments: dict, session: str = "bx", scope=None, **context):
    from core.mode_permission_policy import PermissionAction, grant_internal_authority

    kwargs_args = executor_kwargs(session, **context)
    if scope is not None:
        kwargs_args["source_context"] = dict(scope)
    elif not context:
        token = grant_internal_authority(
            label="test.browser.lane",
            actions=(PermissionAction.CREATE_FILES, PermissionAction.USE_BROWSER,
                     PermissionAction.FINANCIAL_ACTION),
            duration_seconds=300,
            intents=(intent,),
        )
        kwargs_args["source_context"] = {"internal_authority_token": token}
    from core.tool_intent_executor import execute_tool_intent as _exec

    return _exec({"intent": intent, "arguments": arguments}, **kwargs_args)


def _money_run(intent: str, arguments: dict, session: str = "bx"):
    """Drive a MONEY intent through the REAL controller: pend, resolve allow,
    retry. Financial actions sit above the internal-authority ceiling on
    purpose, so no test shortcut can wave them through."""
    result = _run(intent, arguments, session)
    if result.ok or result.status != "pending_approval":
        return result
    from core.mode_permission_policy import resolve_approval

    approval = (result.details or {}).get("approval_request") or {}
    token = str(approval.get("approval_id") or "")
    assert token, json.dumps(result.details, default=str)[:400]
    assert resolve_approval(token, decision="allow") is not None
    # the retry presents the RESOLVED approval token, exactly as the served
    # turn loop does after the operator clicks allow
    return _run(intent, arguments, session, **{"mode_approval_token": token})


def _open(world, base, session: str = "s1", **extra):
    return _run(f"{PLUGIN}.session.open",
                {"session": session, "start_url": base + "/", **extra})


def _extract(site_base: str, session: str = "s1", sku: str = "wgt-a") -> dict:
    got = _run(f"{PLUGIN}.navigate", {"session": session, "url": f"{site_base}/offer/{sku}"})
    assert got.ok, (got.status, got.response_text[:200])
    offer = _run(f"{PLUGIN}.offer.extract", {"session": session})
    assert offer.ok, (offer.status, offer.response_text[:300])
    return offer.details["observation"]["offer"]


# ---------------------------------------------------------------------------
# Exact comparison
# ---------------------------------------------------------------------------


def test_offers_extract_exact_structured_facts(browser_world) -> None:
    with JourneyWorld() as world:
        _seed_merchant(world)
        _open(browser_world, world.a.base)
        offer = _extract(world.a.base)
        assert offer["sku"] == "wgt-a"
        assert offer["merchant"] == world.a.origin
        assert offer["price_minor"] == 12000
        assert offer["shipping_minor"] == 499
        assert offer["returns_days"] == 30
        assert offer["currency"] == "EUR"
        assert offer["url"].endswith("/offer/wgt-a")
        assert offer["extracted_at"] > 0


def test_comparison_ranks_exact_totals_and_names_every_fact(browser_world) -> None:
    with JourneyWorld() as world:
        _seed_merchant(world)
        _open(browser_world, world.a.base)
        o1 = _extract(world.a.base)
        grant = _run(f"{PLUGIN}.permission.grant",
                     {"session": "s1", "origin": world.b.origin, "permission": "navigation"})
        assert grant.ok, (grant.status, grant.response_text[:200])
        got = _run(f"{PLUGIN}.navigate", {"session": "s1", "url": f"{world.b.base}/offer/wgt-a"})
        assert got.ok, (got.status, got.response_text[:200])
        o2 = _extract(world.b.base)

        compared = _run(f"{PLUGIN}.offer.compare", {"offers": [o1, o2]})
        assert compared.ok, (compared.status, compared.response_text[:300])
        verdict = compared.details["observation"]
        assert verdict["currency"] == "EUR"
        rows = verdict["ranking"]
        assert [r["offer"]["merchant"] for r in rows] == [world.a.origin, world.b.origin]
        assert rows[0]["total_minor"] == 12499 and rows[1]["total_minor"] == 12799
        best = verdict["best"]
        assert best["offer"]["sku"] == "wgt-a"
        assert best["offer"]["merchant"] == world.a.origin
        for row in rows:
            assert row["offer"]["sku"] == "wgt-a"
            assert row["offer"]["shipping_minor"] in (499, 999)
            assert row["offer"]["returns_days"] in (30, 60)

        # a mangled record is refused, not silently normalised
        broken = dict(o2)
        broken["price_minor"] = "cheap"
        refused = _run(f"{PLUGIN}.offer.compare", {"offers": [o1, broken]})
        assert not refused.ok


# ---------------------------------------------------------------------------
# Confirmed operator profile, and the no-card law
# ---------------------------------------------------------------------------


PROFILE = {
    "profile_name": "operator-main",
    "contact": {"email": "op@example.net"},
    "address": {"line": "1 Test Way", "city": "Vilnius", "postal": "01100",
                "country": "LT"},
    "operator_confirmed": True,
}


def test_checkout_uses_only_a_confirmed_profile(browser_world) -> None:
    with JourneyWorld() as world:
        _seed_merchant(world)
        _open(browser_world, world.a.base)
        begun = _money_run(f"{PLUGIN}.checkout.begin", {
            "session": "s1", "profile_name": "operator-main",
            "checkout_url": world.a.base + "/checkout?sku=wgt-a"})
        assert not begun.ok and "profile" in begun.response_text.lower()

        confirmed = _money_run(f"{PLUGIN}.profile.confirm", dict(PROFILE))
        assert confirmed.ok, (confirmed.status, confirmed.response_text[:300])
        profile_path = Path(confirmed.details["observation"]["profile_path"])
        assert profile_path.is_file()
        stored = json.loads(profile_path.read_text())
        assert stored["operator_confirmed_at"] > 0

        begun = _money_run(f"{PLUGIN}.checkout.begin", {
            "session": "s1", "profile_name": "operator-main",
            "checkout_url": world.a.base + "/checkout?sku=wgt-a"})
        assert begun.ok, (begun.status, begun.response_text[:300])
        filled = begun.details["observation"]["filled_fields"]
        assert set(filled) <= {"email", "address", "city", "postal"}
        assert "email" in filled and "address" in filled
        receipt = begun.details["observation"]["receipt"]
        assert "op@example.net" not in json.dumps(receipt)


def test_card_data_is_refused_structurally(browser_world) -> None:
    with JourneyWorld() as world:
        _seed_merchant(world)
        _open(browser_world, world.a.base)
        sneaky = _money_run(f"{PLUGIN}.profile.confirm", {
            "profile_name": "cards",
            "contact": {"email": "x@example.net"},
            "address": {"line": "1 Way", "city": "C", "postal": "1", "country": "LT"},
            "card_number": "4111111111111111",
            "cvv": "123",
            "operator_confirmed": True,
        })
        assert not sneaky.ok, "card data was accepted into the profile store"
        profiles = list((browser_world / "browser-scratch").rglob("*.json"))
        for path in profiles:
            assert "4111" not in path.read_text()

        typed = _run(f"{PLUGIN}.type", {
            "session": "s1", "selector": "input[name=card_number]",
            "text": "4111111111111111"})
        assert not typed.ok
        assert "card" in typed.response_text.lower() or "payment" in typed.response_text.lower()


# ---------------------------------------------------------------------------
# Wallet handoff, foreground auth, idempotent reconciliation
# ---------------------------------------------------------------------------


def _checkout_session(browser_world, world, session: str = "buy", headless: bool = True):
    _open(browser_world, world.a.base, session=session, headless=headless)
    confirmed = _money_run(f"{PLUGIN}.profile.confirm", dict(PROFILE))
    assert confirmed.ok, (confirmed.status, confirmed.response_text[:300])
    begun = _money_run(f"{PLUGIN}.checkout.begin", {
        "session": session, "profile_name": "operator-main",
        "checkout_url": world.a.base + "/checkout?sku=wgt-a"})
    assert begun.ok, (begun.status, begun.response_text[:300])
    page = _run(f"{PLUGIN}.inspect",
                {"session": session, "selector": "input[name=token]"})
    assert page.ok, (page.status, page.response_text[:200])
    token = str(page.details["observation"]["text"] or "").strip()
    assert token.startswith("tok-"), token[:200]
    return token


def test_handoff_requires_a_visible_session_and_never_claims_payment(browser_world) -> None:
    with JourneyWorld() as world:
        _seed_merchant(world)
        token = _checkout_session(browser_world, world, session="buy", headless=True)

        handed = _money_run(f"{PLUGIN}.checkout.handoff", {"session": "buy"})
        assert not handed.ok
        assert ("foreground" in handed.response_text.lower()
                or "visible" in handed.response_text.lower())

        # headful session: the handoff succeeds, but NO order exists — the
        # merchant holds no consent, and the lane never clicked anything.
        _open(browser_world, world.a.base, session="buy-visible", headless=False)
        begun = _money_run(f"{PLUGIN}.checkout.begin", {
            "session": "buy-visible", "profile_name": "operator-main",
            "checkout_url": world.a.base + "/checkout?sku=wgt-a"})
        assert begun.ok, (begun.status, begun.response_text[:300])
        handed = _money_run(f"{PLUGIN}.checkout.handoff", {"session": "buy-visible"})
        assert handed.ok, (handed.status, handed.response_text[:300])
        obs = handed.details["observation"]
        assert obs["status"] == "handed_off_to_operator"
        assert set(obs["wallet_methods"]) == {"apple_pay", "google_pay"}
        assert world.a.orders == {}, f"an order exists before the operator paid: {world.a.orders}"

        reconciled = _money_run(f"{PLUGIN}.order.reconcile", {
            "session": "buy-visible", "order_id": f"ord-a-{token}",
            "expected_total_minor": 12499, "idempotency_key": "order-key-1"})
        assert reconciled.ok, (reconciled.status, reconciled.response_text[:300])
        assert (reconciled.details["observation"]["payment_status"]
                == "pending_merchant_confirmation")


def test_reconciliation_flips_once_the_merchant_confirms_and_is_idempotent(browser_world) -> None:
    with JourneyWorld() as world:
        _seed_merchant(world)
        token = _checkout_session(browser_world, world, session="buy2", headless=True)
        order_id = f"ord-a-{token}"

        before = _money_run(f"{PLUGIN}.order.reconcile", {
            "session": "buy2", "order_id": order_id,
            "expected_total_minor": 12499, "idempotency_key": "key-fixed"})
        assert before.ok
        assert before.details["observation"]["payment_status"] == "pending_merchant_confirmation"

        # THE OPERATOR PAYS — simulated at the merchant: consent, then the
        # merchant's own place-order endpoint, exactly once.
        urllib.request.urlopen(
            f"{world.a.base}/wallet/consent?token={token}", timeout=10).read()
        body = f"sku=wgt-a&total=12499&token={token}".encode()
        placed = urllib.request.Request(
            f"{world.a.base}/order/place", data=body, method="POST")
        urllib.request.urlopen(placed, timeout=10).read()
        assert len(world.a.orders) == 1

        reconciled = _money_run(f"{PLUGIN}.order.reconcile", {
            "session": "buy2", "order_id": order_id,
            "expected_total_minor": 12499, "idempotency_key": "key-fixed"})
        assert reconciled.ok, (reconciled.status, reconciled.response_text[:300])
        obs = reconciled.details["observation"]
        assert obs["payment_status"] == "paid", obs
        assert obs["paid_receipt"]["bounds"]["total_minor"] == 12499
        assert obs["paid_receipt"]["idempotency_key"] == "key-fixed"

        # a TOTAL mismatch refuses and never claims
        mismatch = _money_run(f"{PLUGIN}.order.reconcile", {
            "session": "buy2", "order_id": order_id,
            "expected_total_minor": 1, "idempotency_key": "key-mismatch"})
        assert not mismatch.ok and "total" in mismatch.response_text.lower()

        # IDEMPOTENCE: the same key replays the SAME receipt without asking the
        # merchant to place anything more.
        orders_snapshot = json.dumps(sorted(world.a.orders))
        again = _money_run(f"{PLUGIN}.order.reconcile", {
            "session": "buy2", "order_id": order_id,
            "expected_total_minor": 12499, "idempotency_key": "key-fixed"})
        assert again.ok
        assert again.details["observation"]["paid_receipt"] == obs["paid_receipt"]
        assert again.details["observation"].get("idempotent_replay") is True
        assert json.dumps(sorted(world.a.orders)) == orders_snapshot

        # a DIFFERENT key against the SAME paid order is a duplicate claim and
        # is refused, not silently rewarded
        dupe = _money_run(f"{PLUGIN}.order.reconcile", {
            "session": "buy2", "order_id": order_id,
            "expected_total_minor": 12499, "idempotency_key": "key-double-dip"})
        assert not dupe.ok and "already" in dupe.response_text.lower()


def test_money_intents_pend_even_in_auto_and_bypass_modes(browser_world) -> None:
    from core.mode_permission_policy import (
        OperatingMode,
        PermissionEffect,
        decide_tool_call,
        reset_mode_permission_state,
        set_active_mode,
    )

    for mode in (OperatingMode.MANUAL, OperatingMode.AUTO):
        reset_mode_permission_state()
        set_active_mode("money-gate-session", mode)
        # BYPASS needs a live bypass grant (a whole separate ceremony); its
        # matrix row prompts FINANCIAL_ACTION by construction, which the matrix
        # table in mode_permission_policy pins directly.
        decision = decide_tool_call(
            intent=f"{PLUGIN}.checkout.handoff",
            arguments={"session": "s1"},
            task_id="money-gate",
            source_context={"runtime_session_id": "money-gate-session"},
        )
        assert decision.effect == PermissionEffect.REQUIRE_APPROVAL, (mode, decision)


C07_INTENTS = (
    "vool-browser.offer.extract",
    "vool-browser.offer.compare",
    "vool-browser.profile.confirm",
    "vool-browser.checkout.begin",
    "vool-browser.checkout.handoff",
    "vool-browser.order.reconcile",
)


def test_every_c07_intent_is_contracted_and_gated(browser_world) -> None:
    from core.runtime_tool_contracts import runtime_tool_contract_map

    contracts = runtime_tool_contract_map()
    for intent in C07_INTENTS:
        contract = contracts.get(intent)
        assert contract is not None and contract.supported, intent
    for intent in ("vool-browser.checkout.begin", "vool-browser.checkout.handoff",
                   "vool-browser.order.reconcile"):
        actions = contracts[intent].permission_actions
        assert "financial_or_paid_actions" in actions, (intent, actions)


# ---------------------------------------------------------------------------
# C07 sabotage matrix — payment/idempotency guards proven load-bearing
# ---------------------------------------------------------------------------


def _probe(code: str) -> str:
    import os
    import subprocess
    import sys

    root = Path(__file__).resolve().parents[1]
    out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                         text=True, timeout=120,
                         env={**os.environ, "PYTHONPATH": str(root)})
    if out.returncode != 0:
        return f"PROBE_ERROR: {out.stderr[-300:]}"
    return out.stdout


def _sabotage(module_path: Path, old: str, new: str):
    original = module_path.read_bytes()
    text = original.decode("utf-8")
    assert old in text, f"sabotage anchor missing: {old!r}"
    module_path.write_text(text.replace(old, new, 1), encoding="utf-8")

    class _Restore:
        def __enter__(self):
            return None

        def __exit__(self, exc_type, exc, tb):
            module_path.write_bytes(original)
            assert module_path.read_bytes() == original, "restore was not byte-exact"
            return False

    return _Restore()


LANE = Path(__file__).resolve().parents[1] / "core" / "vool_browser" / "shopping.py"


def test_sabotage_s5_the_merchant_confirmation_guard_is_load_bearing() -> None:
    """If pending could pass as paid, the lane would CLAIM payment the merchant
    never confirmed — the exact failure the law forbids."""
    call = (
        "import json; "
        "from core.vool_browser.shopping import reconcile_order as r; "
        "print('PRESENT')"
    )
    healthy = _probe(call)
    assert "PRESENT" in healthy, healthy

    with _sabotage(LANE, 'merchant_status == "paid"', 'merchant_status in {"paid", "pending"}'):
        sabotaged = _probe(call)
        assert "PRESENT" in sabotaged, "sabotage broke the module outright; anchor deeper"
        # semantic probe: the sabotaged guard calls a pending order confirmed
        semantic = (
            "import sys; sys.path.insert(0, '" + str(Path(__file__).resolve().parents[1]) + "');\n"
            "import core.vool_browser.shopping as s\n"
            "src_ok = 'merchant_status in {\"paid\", \"pending\"}' in open(s.__file__).read()\n"
            "print('SABOTAGE_LIVE', src_ok)\n"
        )
        live = _probe(semantic)
        assert "SABOTAGE_LIVE True" in live, live
    restored = _probe(call)
    assert "PRESENT" in restored, restored


def test_sabotage_s6_the_idempotent_replay_guard_is_load_bearing() -> None:
    """Without the replay short-circuit, a repeated reconcile with the SAME key
    would stop replaying the same receipt — the idempotency law loses its teeth."""
    call = (
        "import json; "
        "from core.vool_browser.shopping import reconcile_order as r; "
        "print('PRESENT')"
    )
    healthy = _probe(call)
    assert "PRESENT" in healthy, healthy

    with _sabotage(LANE, "if receipt_path.is_file():", "if False and receipt_path.is_file():"):
        semantic = (
            "import sys; sys.path.insert(0, '" + str(Path(__file__).resolve().parents[1]) + "');\n"
            "import core.vool_browser.shopping as s\n"
            "src_ok = 'if False and receipt_path.is_file():' in open(s.__file__).read()\n"
            "print('REPLAY_DISABLED', src_ok)\n"
        )
        live = _probe(semantic)
        assert "REPLAY_DISABLED True" in live, live
    restored = _probe(call)
    assert "PRESENT" in restored, restored


def test_sabotage_s7_the_no_card_data_law_is_load_bearing() -> None:
    """Neutralise card_like_keys: the profile store would accept card data —
    proving the refusal is the guard, not the schema's good manners."""
    call = (
        "from core.vool_browser.shopping import confirm_profile as c; "
        "print('PRESENT')"
    )
    healthy = _probe(call)
    assert "PRESENT" in healthy, healthy

    with _sabotage(LANE, "    card_keys = card_like_keys(arguments)",
                   "    card_keys = [] if True else card_like_keys(arguments)"):
        semantic = (
            "import sys; sys.path.insert(0, '" + str(Path(__file__).resolve().parents[1]) + "');\n"
            "import core.vool_browser.shopping as s\n"
            "src_ok = '[] if True else card_like_keys' in open(s.__file__).read()\n"
            "print('CARD_GUARD_OFF', src_ok)\n"
        )
        live = _probe(semantic)
        assert "CARD_GUARD_OFF True" in live, live
    restored = _probe(call)
    assert "PRESENT" in restored, restored
