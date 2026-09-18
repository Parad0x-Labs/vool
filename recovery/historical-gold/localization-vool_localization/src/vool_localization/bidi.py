"""RTL support primitives.

The registry stores logical-order Unicode; direction handling happens at the
rendering edge. These helpers are what every adapter/platform should call:

* ``direction(locale)``           -> 'rtl' | 'ltr'
* ``isolate(text, direction)``    -> wraps a fragment in FSI/PDI (or LRI/PDI)
  so mixed English brand names ("VOOL", "null://…"), numbers, and currency
  codes inside Arabic/Hebrew sentences cannot reorder across boundaries.
* ``bidi_wrap_paragraph(text)``   -> adds U+202B/RLE … U+202C/PDF for legacy
  renderers that ignore isolates (older Windows GDI text boxes).
"""
from __future__ import annotations

RTL_LANGS = {"ar", "he", "fa", "ur", "ps", "sd", "ug", "yi"}

FSI, PDI = "\u2068", "\u2069"     # first-strong isolate / pop
LRI = "\u2066"
RLE, PDF = "\u202B", "\u202C"     # legacy embedding controls
LRM, RLM = "\u200E", "\u200F"


def direction(locale: str) -> str:
    lang = locale.strip().lower().replace("_", "-").split("-", 1)[0]
    return "rtl" if lang in RTL_LANGS else "ltr"


def isolate(text: str, direction_: str | None = None) -> str:
    d = direction_ or direction(text[:40])
    start = FSI
    if d == "rtl":
        # neutral content inside a known-RTL paragraph gets an RLM anchor so a
        # leading Latin token ("VOOL receipt") does not flip the line open.
        body = text.lstrip()
        if body[:1].isascii():
            body = RLM + body
        return FSI + body + PDI
    return LRI + text + PDI


def bidi_wrap_paragraph(text: str, direction_: str) -> str:
    if direction_ != "rtl":
        return LRM + text + LRM if text[:1].isascii() else text
    return RLE + text + PDF


def strip_controls(text: str) -> str:
    return "".join(ch for ch in text if ch not in {FSI, PDI, LRI, RLE, PDF, LRM, RLM})


# ---------------------------------------------------------------------------
# RTL torture fixture — deliberately hostile fragments used in tests/examples.
# Each mixes at least two of: Arabic script, Latin brand, digits, currency,
# punctuation, and a null:// URI.
RTL_TORTURE_FRAGMENTS = [
    "VOOL receipt #4821 — مدفوع بالكامل",
    "الرصيد الحالي 1.234,56 ر.م مقابل $99 USD",
    "فتح المهمة عبر null://service/translate?src=en&dst=ar ثم انتظر النتيجة",
    "الحالة: blocked_false_claim — تم إيقاف ادعاء غير مدعوم!",
    "آخر تحديث: 26 أغسطس 2026 في 14:05 (UTC+03:00)",
    "‏تنبيه: انتهت المهلة بعد 90 ثانية؛ أُعيد المحاولة مرتين",
]
