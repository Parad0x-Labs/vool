"""Locale-aware dates, numbers, and currencies — stdlib only, no setlocale.

Why not Python's ``locale`` module: it is process-global (a desktop app and a
web handler in one process would fight over it) and depends on host OS
locale data being installed. A small explicit table is deterministic on
macOS, Windows, Linux, iOS and Android.

Currency rendering uses an explicit pattern per locale:
    '{sym} {amt}'   symbol first  (en-US, pt-BR)
    '{amt} {sym}'   symbol last   (pt-PT)
"""
from __future__ import annotations

import datetime as _dt

# locale -> formatting profile
PROFILES: dict[str, dict] = {
    "en": {
        "group": ",", "decimal": ".",
        "date_short": "%b %-d, %Y", "time_short": "%-I:%M %p",
        "currencies": {"USD": ("$", "{sym}{amt}", 2), "BRL": ("R$", "{sym} {amt}", 2), "EUR": ("€", "{sym}{amt}", 2)},
    },
    "pt-br": {
        "group": ".", "decimal": ",",
        "date_short": "%d/%m/%Y", "time_short": "%H:%M",
        "currencies": {"BRL": ("R$", "{sym} {amt}", 2), "USD": ("US$", "{sym} {amt}", 2), "EUR": ("€", "{sym} {amt}", 2)},
    },
    "pt-pt": {
        "group": " ", "decimal": ",",
        "date_short": "%d/%m/%Y", "time_short": "%H:%M",
        # pt-PT: euro sign AFTER the amount with a non-breaking space; BRL keeps prefix.
        "currencies": {"EUR": ("€", "{amt} {sym}", 2), "BRL": ("R$", "{sym} {amt}", 2), "USD": ("US$", "{sym} {amt}", 2)},
    },
    "ar": {
        "group": ",", "decimal": ".",
        "date_short": "%d/%m/%Y", "time_short": "%H:%M",
        "currencies": {"EGP": ("ج.م", "{amt} {sym}", 2), "USD": ("US$", "{sym} {amt}", 2)},
    },
}


def resolve_profile(locale_tag: str) -> tuple[dict, str]:
    tag = locale_tag.strip().lower().replace("_", "-")
    if tag in PROFILES:
        return PROFILES[tag], tag.replace("-", "_")
    lang = tag.split("-", 1)[0]
    if lang in PROFILES:
        return PROFILES[lang], lang.replace("-", "_")
    return PROFILES["en"], "en"


def _attr_name(s: str) -> str:
    # "%b %-d" style flags are glibc-only; emulate the two we use portably.
    return s


def format_number(value: float | int, locale_tag: str, decimals: int | None = None) -> str:
    profile, _ = resolve_profile(locale_tag)
    neg = value < 0
    value = abs(float(value))
    if decimals is None:
        text = ("%f" % value).rstrip("0").rstrip(".")
        if not text or text == ".":
            text = "0"
    else:
        text = "%.*f" % (decimals, value)
    whole, _, frac = text.partition(".")
    groups = []
    while len(whole) > 3:
        whole, head = whole[:-3], whole[-3:]
        groups.insert(0, head)
    groups.insert(0, whole)
    out = profile["group"].join(groups)
    if frac:
        out += profile["decimal"] + frac
    return ("-" if neg else "") + out


def format_currency(amount: float, currency: str, locale_tag: str) -> str:
    profile, key = resolve_profile(locale_tag)
    currencies = profile["currencies"]
    entry = currencies.get(currency.upper())
    if entry is None:
        sym = currency.upper() + " "
        pattern, dec = "{sym}{amt}", 2
    else:
        sym, pattern, dec = entry
    amt = format_currency_amount(abs(amount), profile, dec)
    rendered = pattern.format(sym=sym, amt=amt)
    return ("-" if amount < 0 else "") + rendered


def format_currency_amount(value: float, profile: dict, decimals: int) -> str:
    text = "%.*f" % (decimals, value)
    whole, _, frac = text.partition(".")
    groups = []
    while len(whole) > 3:
        whole, head = whole[:-3], whole[-3:]
        groups.insert(0, head)
    groups.insert(0, whole)
    out = profile["group"].join(groups)
    return out + profile["decimal"] + frac if frac else out


def format_date(dt: _dt.datetime | _dt.date, locale_tag: str, kind: str = "date") -> str:
    profile, key = resolve_profile(locale_tag)
    fmt = profile["date_short"] if kind == "date" else profile["time_short"]
    # portable %-d / %-I emulation for the platforms that reject them
    if "%-d" in fmt:
        fmt = fmt.replace("%-d", str(dt.day))
    if "%-I" in fmt:
        fmt = fmt.replace("%-I", str((dt.hour % 12) or 12))
    month_names = MONTHS.get(key.split("_")[0], MONTHS["en"])
    fmt = fmt.replace("%b", month_names[dt.month - 1])
    return dt.strftime(fmt)


MONTHS: dict[str, list[str]] = {
    "en": ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
    "pt": ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"],
    "ar": ["يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو", "يوليو", "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر"],
}
