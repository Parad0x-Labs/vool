"""C07 — shopping comparison and native-wallet checkout handoff.

Money laws, enforced structurally (this module is their only home):

- The lane NEVER sees card data. `profile.confirm`'s schema has no card fields
  and `profile.card_like_keys()` refuses anything shaped like one;
  `checkout.begin` fills ONLY the profile-mapped contact/address fields and
  `type` refuses card/CVV-named targets outright.
- The lane NEVER clicks pay. `checkout.handoff` detects the merchant's wallet
  methods, hands the payment to the OPERATOR (foreground auth — a headless
  session is refused), and flips the session to `awaiting_operator_payment`.
- The lane NEVER claims payment before the MERCHANT confirms.
  `order.reconcile` asks the merchant's own order-status endpoint; without a
  matching merchant confirmation the answer is `pending_merchant_confirmation`.
  A confirmed order is receipted once under its idempotency key; replays return
  the SAME receipt, and a second key against an already-claimed order is a
  refused duplicate claim.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from core.vool_browser.sessions import OpFailed, SessionHandle, get_handle, scratch_root

PRICE_RE = re.compile(r"([€$])\s*(\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?)")
SYMBOL_CURRENCY = {"€": "EUR", "$": "USD"}
RETURN_DAYS_RE = re.compile(r"(\d{1,4})\s*day", re.I)
SKUs_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
IDEMPOTENCY_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{4,127}$")
CARD_LIKE_KEY_RE = re.compile(r"card|cvv|cvc|pan\b|expiry|exp_date|cardnumber|security.?code", re.I)

WALLET_CLASS_MARKERS = {
    "apple_pay": ("apple pay", "applepay", "apple-pay"),
    "google_pay": ("google pay", "googlepay", "google-pay", "gpay"),
}

PROFILE_TEMPLATE = {
    "contact": ["email"],
    "address": ["line", "city", "postal", "country"],
}


def card_like_keys(arguments: dict[str, Any]) -> list[str]:
    """Any argument key (at any depth) that looks like card data."""

    hits: list[str] = []

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for key, inner in value.items():
                if CARD_LIKE_KEY_RE.search(str(key)):
                    hits.append(str(key))
                walk(inner)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(arguments)
    return hits


def _parse_price(text: str) -> tuple[int, str] | None:
    """'€120.00' / '$1.234,50' -> (minor_units, currency). None when absent."""

    match = PRICE_RE.search(str(text or ""))
    if not match:
        return None
    symbol, digits = match.group(1), match.group(2)
    currency = SYMBOL_CURRENCY.get(symbol, "")
    if not currency:
        return None
    normalized = digits.replace(".", "") if "," in digits else digits.replace(",", "")
    normalized = normalized.replace(",", ".") if "," in digits and "." not in digits else normalized
    try:
        value = float(normalized)
    except ValueError:
        return None
    return round(value * 100), currency


def parse_minor(text: str) -> int | None:
    parsed = _parse_price(text)
    return parsed[0] if parsed else None


def extract_offer(handle: SessionHandle, view: dict[str, Any], arguments: dict[str, Any]) -> dict[str, Any]:
    page: Any = view["page"]
    url = str(page.url or "")
    mapping = {
        "sku": ".sku",
        "price": ".price",
        "shipping": ".shipping",
        "returns": ".returns",
    }
    custom = arguments.get("mapping")
    if isinstance(custom, dict):
        for key, selector in custom.items():
            if key in mapping and isinstance(selector, str) and selector.strip():
                mapping[key] = selector

    def field(selector: str) -> str:
        try:
            located = page.locator(selector)
            if located.count() == 0:
                return ""
            return (located.first.inner_text() or "").strip()
        except Exception:
            return ""

    sku_text = field(mapping["sku"])
    if not sku_text or not SKUs_RE.match(sku_text):
        raise OpFailed("no_sku", f"no usable SKU on {url} (found {sku_text[:40]!r})")
    price = _parse_price(field(mapping["price"]))
    if price is None:
        raise OpFailed("no_price", f"no parseable price on {url}")
    price_minor, currency = price
    shipping_text = field(mapping["shipping"])
    if shipping_text:
        shipping = _parse_price(shipping_text)
        shipping_minor = shipping[0] if shipping else 0
    else:
        shipping_minor = 0
    returns_days_match = RETURN_DAYS_RE.search(field(mapping["returns"]))
    try:
        title = str(page.title() or "")
    except Exception:
        title = ""
    parts = url.split("/", 3)
    merchant_host = parts[2] if len(parts) >= 3 else ""
    offer = {
        "sku": sku_text,
        "merchant": merchant_host,
        "title": title[:200],
        "price_minor": price_minor,
        "shipping_minor": shipping_minor,
        "currency": currency,
        "returns_days": int(returns_days_match.group(1)) if returns_days_match else None,
        "url": url,
        "extracted_at": int(time.time()),
    }
    return {"receipt_outcome": "offer_extracted", "offer": offer,
            "final_url": url, "origin": merchant_host}


def compare_offers(handle: SessionHandle, view: dict[str, Any], arguments: dict[str, Any]) -> dict[str, Any]:
    offers = arguments.get("offers")
    if not isinstance(offers, list) or not (2 <= len(offers) <= 10):
        raise OpFailed("invalid_arguments", "compare needs 2..10 offer records")

    def valid(offer: Any) -> bool:
        if not isinstance(offer, dict):
            return False
        if not SKUs_RE.match(str(offer.get("sku") or "")):
            return False
        for key in ("price_minor", "shipping_minor"):
            value = offer.get(key)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                return False
        return bool(str(offer.get("merchant") or "").strip())

    clean = [dict(o) for o in offers if valid(o)]
    if len(clean) != len(offers):
        raise OpFailed(
            "invalid_arguments",
            "every offer needs a bare sku, integer price_minor/shipping_minor and a "
            "merchant; a mangled record is refused, never normalised")
    currencies = {str(o.get("currency") or "") for o in clean}
    if len(currencies) != 1 or "" in currencies:
        raise OpFailed("currency_mismatch", f"offers must share one currency, got {sorted(currencies)}")
    currency = currencies.pop()

    def returns_of(offer: dict[str, Any]) -> int:
        value = offer.get("returns_days")
        return value if isinstance(value, int) and not isinstance(value, bool) else -1

    ranked = sorted(
        clean,
        key=lambda o: (o["price_minor"] + o["shipping_minor"], -returns_of(o), o["merchant"]),
    )
    ranking = [{
        "offer": offer,
        "total_minor": offer["price_minor"] + offer["shipping_minor"],
        "returns_days": returns_of(offer) if returns_of(offer) >= 0 else None,
    } for offer in ranked]
    return {"receipt_outcome": "compared", "currency": currency,
            "ranking": ranking, "best": ranking[0]}


# ---------------------------------------------------------------------------
# Operator profile (confirmed, card-free by construction)
# ---------------------------------------------------------------------------


def profiles_dir() -> Path:
    return scratch_root() / "profiles"


def confirm_profile(arguments: dict[str, Any]) -> dict[str, Any]:
    if not arguments.get("operator_confirmed"):
        raise OpFailed(
            "operator_confirmation_required",
            "a checkout profile exists only with the operator's explicit "
            "confirmation (operator_confirmed: true)")
    name = str(arguments.get("profile_name") or "").strip()
    if not SKUs_RE.match(name):
        raise OpFailed("invalid_arguments", "profile_name must be a bare name")
    card_keys = card_like_keys(arguments)
    if card_keys:
        raise OpFailed(
            "card_data_refused",
            f"refused: card-like fields ({', '.join(sorted(set(card_keys))[:4])}) are never "
            "collected or stored by this lane; payment belongs to the wallet handoff")
    profile = {
        "profile_name": name,
        "contact": dict(arguments.get("contact") or {}),
        "address": dict(arguments.get("address") or {}),
        "operator_confirmed_at": int(time.time()),
    }
    if card_like_keys(profile):
        raise OpFailed("card_data_refused", "card-like field inside contact/address")
    target = profiles_dir() / f"{name}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(profile, indent=1, sort_keys=True), encoding="utf-8")
    return {"receipt_outcome": "profile_confirmed", "profile_path": str(target),
            "profile_name": name}


def load_profile(name: str) -> dict[str, Any]:
    path = profiles_dir() / f"{name}.json"
    if not path.is_file():
        raise OpFailed(
            "profile_missing",
            f"no confirmed checkout profile named {name!r}; the operator confirms one "
            "with vool-browser.profile.confirm")
    profile = json.loads(path.read_text(encoding="utf-8"))
    if not profile.get("operator_confirmed_at"):
        raise OpFailed("profile_missing", f"profile {name!r} was never confirmed")
    return profile


# ---------------------------------------------------------------------------
# Checkout begin + wallet handoff
# ---------------------------------------------------------------------------

_FIELD_MAP = {
    "email": ("contact", "email"),
    "address": ("address", "line"),
    "city": ("address", "city"),
    "postal": ("address", "postal"),
    "country": ("address", "country"),
}


def begin_checkout(handle: SessionHandle, view: dict[str, Any], arguments: dict[str, Any]) -> dict[str, Any]:
    profile_name = str(arguments.get("profile_name") or "").strip()
    profile = load_profile(profile_name)
    from core.vool_browser.ops import op_navigate

    checkout_url = str(arguments.get("checkout_url") or "").strip()
    if checkout_url:
        op_navigate(handle, view, {"url": checkout_url})
    page: Any = view["page"]
    filled: list[str] = []
    for field, (section, key) in _FIELD_MAP.items():
        value = str((profile.get(section) or {}).get(key) or "").strip()
        if not value:
            continue
        for selector in (f"input[name={field}]", f"input[placeholder={field}]"):
            try:
                located = page.locator(selector)
                if located.count() > 0:
                    located.first.fill(value, timeout=5_000)
                    filled.append(field)
                    break
            except Exception:
                continue
    # The lane fills contact/address ONLY. Any card field on the page is left
    # strictly alone — payment belongs to the wallet handoff.
    final_url = str(page.url or "")
    origin = final_url.split("/", 3)[2] if final_url.count("/") >= 3 else ""
    return {"receipt_outcome": "checkout_begun", "profile_name": profile_name,
            "filled_fields": filled, "skipped_payment_fields": True,
            "final_url": final_url, "origin": origin}


def checkout_handoff(handle: SessionHandle, view: dict[str, Any], arguments: dict[str, Any]) -> dict[str, Any]:
    if handle.headless:
        raise OpFailed(
            "foreground_required",
            "wallet handoff needs a VISIBLE session (open with headless: false): the "
            "operator authenticates Apple Pay / Google Pay in the foreground; the lane "
            "never clicks pay and never sees payment data")
    page: Any = view["page"]
    methods: list[str] = []
    try:
        located = page.locator(".payment-methods .pay")
        for element in located.all()[:10]:
            text = (element.inner_text() or "").strip().lower()
            for kind, markers in WALLET_CLASS_MARKERS.items():
                if any(marker in text for marker in markers):
                    methods.append(kind)
                    break
    except Exception:
        pass
    methods = sorted(set(methods))
    if not methods:
        raise OpFailed("no_wallet_method", "no known wallet payment method found on the page")
    final_url = str(page.url or "")
    origin = final_url.split("/", 3)[2] if final_url.count("/") >= 3 else ""
    handle.state = "awaiting_operator_payment"
    return {"receipt_outcome": "handed_off_to_operator", "status": "handed_off_to_operator",
            "wallet_methods": methods, "note": (
                "Payment is the operator's act: authenticate in the visible window. "
                "The lane never clicks pay and never sees payment data."),
            "final_url": final_url, "origin": origin}


# ---------------------------------------------------------------------------
# Idempotent order reconciliation
# ---------------------------------------------------------------------------


def orders_dir() -> Path:
    return scratch_root() / "orders"


def reconcile_order(handle: SessionHandle, view: dict[str, Any], arguments: dict[str, Any]) -> dict[str, Any]:
    order_id = str(arguments.get("order_id") or "").strip()
    key = str(arguments.get("idempotency_key") or "").strip()
    expected_total = arguments.get("expected_total_minor")
    if not order_id or not IDEMPOTENCY_KEY_RE.match(key):
        raise OpFailed("invalid_arguments", "reconcile needs order_id and an idempotency_key (>=5 chars)")
    if not isinstance(expected_total, int) or isinstance(expected_total, bool) or expected_total <= 0:
        raise OpFailed("invalid_arguments", "expected_total_minor must be a positive integer")

    store = orders_dir()
    store.mkdir(parents=True, exist_ok=True)
    receipt_path = store / f"{key.replace(':', '_')}.json"
    if receipt_path.is_file():
        previous = json.loads(receipt_path.read_text(encoding="utf-8"))
        return {"receipt_outcome": "reconciled", "payment_status": "paid",
                "paid_receipt": previous, "idempotent_replay": True,
                "note": "already reconciled under this key; the merchant was not asked again"}

    context: Any = view["context"]
    origin = handle.primary_origin
    status_url = str(arguments.get("status_url") or "").strip()
    if not status_url:
        if not origin:
            raise OpFailed("invalid_arguments", "no status URL and the session has no origin")
        status_url = f"http://{origin}/order/{order_id}"
    try:
        response = context.request.get(status_url, max_redirects=0,
                                       fail_on_status_code=False, timeout=15_000)
        status = int(response.status or 0)
        payload: dict[str, Any] = {}
        if status == 200:
            try:
                payload = response.json()
            except Exception:
                payload = {}
    except Exception as exc:
        raise OpFailed("merchant_unreachable", f"the merchant's order status failed: {str(exc)[:200]}") from exc

    merchant_status = str(payload.get("status") or "")
    merchant_total = payload.get("total_minor")
    confirmed = status == 200 and merchant_status == "paid"
    if confirmed and merchant_total != expected_total:
        raise OpFailed(
            "total_mismatch",
            f"the merchant reports total {merchant_total} for {order_id}, expected "
            f"{expected_total}: refusing to reconcile a mismatched amount")
    if confirmed:
        # ONE paid order, ONE terminal claim, ever: a different key against an
        # order this lane already reconciled is a duplicate claim, refused even
        # though the merchant would answer again.
        for existing in store.glob("*.json"):
            try:
                prior = json.loads(existing.read_text(encoding="utf-8"))
            except ValueError:
                continue
            if str(prior.get("order_id") or "") == order_id:
                raise OpFailed(
                    "already_reconciled",
                    f"order {order_id} was already reconciled under idempotency key "
                    f"{prior.get('idempotency_key')!r}; a second claim is refused")

    from core.vool_browser.receipts import build_receipt

    if not confirmed:
        return {
            "receipt_outcome": "reconciled",
            "payment_status": "pending_merchant_confirmation",
            "merchant_seen": {"http_status": status, "status": merchant_status or None},
            "order_id": order_id,
            "note": ("the merchant has NOT confirmed this order; the lane does not "
                     "claim payment from page claims, totals-on-screens, or silence"),
        }

    receipt = build_receipt(
        op="order.paid", session=handle.session, outcome="paid",
        origin=origin, final_url=status_url,
        bounds={"total_minor": expected_total},
        order_id=order_id, idempotency_key=key,
        merchant_order_status=merchant_status)
    receipt_path.write_text(json.dumps(receipt, indent=1, sort_keys=True), encoding="utf-8")
    return {"receipt_outcome": "reconciled", "payment_status": "paid", "paid_receipt": receipt,
            "idempotent_replay": False}


def find_session(session: str) -> SessionHandle:
    handle = get_handle(session)
    if handle is None or handle.state in {"closed", "closing"}:
        raise OpFailed("unknown_session", f"no live session named {session!r}")
    return handle
