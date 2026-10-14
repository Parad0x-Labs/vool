"""The model's-own-tools chat replies follow the app language the page chose.

The page persists its locale picker as the ``vool_ui_locale`` cookie. A chat turn sent with
that cookie must answer the switch's replies in that language, through the real request path
(a served daemon's own HTTP server, cookie header and all), never by handing a locale to some
inner call. With no cookie, and with a cookie naming a language VOOL does not ship, the replies
stay English. The command words themselves remain English-only (core/model_own_tools.py).

Same rig discipline as tests/test_model_own_tools_served.py: real daemon and routing, synthetic
OpenAI-compatible provider, so nothing here reaches a real provider.
"""

from __future__ import annotations

import json
import uuid
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from core.i18n.catalog import catalog_for
from core.model_own_tools import REPLY_ALREADY_ON
from tests.test_conversation_intent_served import (
    OR_LANE_ID,
    OR_MODEL,
    OpenAICompatibleService,
    OpenRouterCompatibleDaemon,
)
from tests.usepod.test_usepod_served_flow import _answer_text, _session


def _post_chat(
    daemon: OpenRouterCompatibleDaemon, payload: dict, *, cookie_locale: str | None = None, timeout: float = 300.0
) -> tuple[int, object]:
    """POST /api/chat the way the page does, including its ``vool_ui_locale`` cookie."""
    data = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if cookie_locale is not None:
        headers["Cookie"] = f"vool_ui_locale={cookie_locale}"
    request = Request(f"{daemon.base_url}/api/chat", data=data, headers=headers, method="POST")
    try:
        with urlopen(request, timeout=timeout) as response:
            status, raw, kind = response.status, response.read(), response.headers.get("Content-Type", "")
    except HTTPError as exc:
        status, raw, kind = exc.code, exc.read(), exc.headers.get("Content-Type", "")
    if "json" in kind and "ndjson" not in kind:
        return status, json.loads(raw.decode("utf-8"))
    return status, raw.decode("utf-8", "replace")


def _turn(
    daemon: OpenRouterCompatibleDaemon,
    text: str,
    *,
    session_id: str,
    cookie_locale: str | None = None,
    stream: bool = False,
    turn_author: str | None = None,
) -> str:
    payload: dict = {
        "messages": [{"role": "user", "content": text}],
        "stream": stream,
        "session_id": session_id,
        "model": OR_LANE_ID,
        "mode": "auto",
    }
    if turn_author is not None:
        payload["turn_author"] = turn_author
    status, answer = _post_chat(daemon, payload, cookie_locale=cookie_locale)
    assert status == 200, answer
    return _answer_text(answer)


@pytest.fixture(scope="module")
def served(tmp_path_factory):
    service = OpenAICompatibleService().start()
    daemon = OpenRouterCompatibleDaemon(tmp_path_factory.mktemp("own-tools-i18n") / "home", service)
    try:
        daemon.start()
        status, catalog = daemon.call("GET", "/api/cloud/models?provider=openrouter")
        assert status == 200, catalog
        status, pinned = daemon.call(
            "POST", "/api/cloud/model", {"model": OR_MODEL, "provider": "openrouter", "confirm_paid": True}
        )
        assert status == 200 and pinned.get("ok") is True, pinned
        status, certified = daemon.call(
            "POST",
            "/api/model-tool-certification/run",
            {"provider_name": "openrouter-byok", "model_name": OR_MODEL},
            timeout=420.0,
        )
        assert status == 200, certified
        yield SimpleNamespace(service=service, daemon=daemon)
    finally:
        daemon.stop()
        service.stop()


def test_the_switch_replies_follow_the_cookie_language(served) -> None:
    daemon = served.daemon
    german_on = catalog_for("de").text("own_tools.reply.on")
    german_already = catalog_for("de").text("own_tools.reply.already_on")
    german_off = catalog_for("de").text("own_tools.reply.off")
    japanese_already = catalog_for("ja").text("own_tools.reply.already_on")
    japanese_not_owner = catalog_for("ja").text("own_tools.reply.not_owner")

    # German: the switch turns on and says so in German; asking again says "already on" in German.
    session_de = _session(f"own-tools-i18n-de-{uuid.uuid4()}")
    first = _turn(daemon, "use the model's own tools", session_id=session_de, cookie_locale="de")
    assert first == german_on, first
    again = _turn(daemon, "use the model's own tools", session_id=session_de, cookie_locale="de")
    assert again == german_already, again

    # The served UI streams: the same cookie drives the same German through the streaming lane.
    streamed = _turn(daemon, "use the model's own tools", session_id=session_de, cookie_locale="de", stream=True)
    assert streamed == german_already, streamed

    # Japanese: an agent-written turn cannot switch anything; its refusal comes back in Japanese.
    refused = _turn(
        daemon,
        "use the model's own tools",
        session_id=_session(f"own-tools-i18n-ja-{uuid.uuid4()}"),
        cookie_locale="ja",
        turn_author="agent",
    )
    assert refused == japanese_not_owner, refused
    # Japanese: the switch is on, so the owner asking again hears "already on" in Japanese.
    ja_again = _turn(
        daemon,
        "use the model's own tools",
        session_id=_session(f"own-tools-i18n-ja2-{uuid.uuid4()}"),
        cookie_locale="ja",
    )
    assert ja_again == japanese_already, ja_again

    # No cookie: English, byte-exact the source constant.
    plain = _turn(daemon, "use the model's own tools", session_id=_session(f"own-tools-i18n-plain-{uuid.uuid4()}"))
    assert plain == REPLY_ALREADY_ON, plain

    # A cookie naming a language VOOL does not ship: English, not a guess.
    unknown = _turn(
        daemon,
        "use the model's own tools",
        session_id=_session(f"own-tools-i18n-xx-{uuid.uuid4()}"),
        cookie_locale="zz",
    )
    assert unknown == REPLY_ALREADY_ON, unknown

    # Switching back also follows the cookie, and leaves the daemon in its default state.
    off = _turn(daemon, "use VOOL's tools", session_id=session_de, cookie_locale="de")
    assert off == german_off, off
