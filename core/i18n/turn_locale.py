"""The app language of the chat turn in flight.

A chat command answered by VOOL itself (not by the model) speaks the app language the user
picked in the page, not English. The page persists that picker as the ``vool_ui_locale``
cookie; the chat request handler binds it here for exactly the length of one request
(``core.web.api.service.dispatch_post``), and any chat command deep inside the turn reads its
words through :func:`turn_text` without growing a locale argument through every layer.

The binding is a ContextVar, the same carrier the ingress context uses to keep request state
alive across the streaming worker thread, so the buffered and the streamed turn both see it.
Nothing bound (another channel, a direct caller): English, as before. The app language is
deliberately separate from the model answer language (its own Settings widget).
"""
from __future__ import annotations

import contextvars
from typing import Any

from core.i18n.catalog import SOURCE_LOCALE, catalog_for, format_message, resolve_render_locale

_TURN_LOCALE: contextvars.ContextVar[str] = contextvars.ContextVar(
    "vool_turn_ui_locale", default=SOURCE_LOCALE
)


def bind_turn_locale(locale: str | None) -> contextvars.Token:
    """Bind this request's app language; returns the token for :func:`reset_turn_locale`.

    Only a supported locale is bound; anything else (missing, unknown, malformed) binds English.
    """
    try:
        resolved = resolve_render_locale(None, locale, SOURCE_LOCALE)
    except Exception:
        resolved = SOURCE_LOCALE
    return _TURN_LOCALE.set(resolved)


def reset_turn_locale(token: contextvars.Token) -> None:
    """Release a :func:`bind_turn_locale` binding."""
    _TURN_LOCALE.reset(token)


def turn_locale() -> str:
    """The bound app language of the current turn (``"en"`` when none was bound)."""
    return _TURN_LOCALE.get()


def turn_text(key: str, english: str, locale: str = "", **fields: Any) -> str:
    """One VOOL-authored chat reply in the turn's app language.

    ``english`` is the caller's own English source text, served whenever the catalog cannot
    give a real message (the key missing from the shipped catalogs), so a user never sees a
    bare catalog key. An explicit ``locale`` wins over the bound one. With ``fields`` the
    message is formatted with the catalog's placeholder law (values are HTML-escaped by that
    law, so pass only plain words). Unknown, missing or English locales serve English.
    """
    catalog = catalog_for(str(locale or "").strip() or turn_locale())
    text = catalog.text(key)
    if text == key:
        return format_message(english, fields) if fields else english
    return catalog.format(key, **fields) if fields else text


__all__ = ["bind_turn_locale", "reset_turn_locale", "turn_locale", "turn_text"]
