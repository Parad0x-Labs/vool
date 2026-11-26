"""The clock's own answer is runtime code's, whatever the turn's judges picked.

iMac gate22 on 1e85219dc (qwen3:8b, Web lookups off): "what time is it in Lima right now?" was judged a web ask, the
date and time lane answered from the clock, and publication replaced that answer with "Web lookups are off, so I can't
look this up online": the judges' own model calls were read as a model having written the answer, so the answer was
held to sources a web lookup should have found. Served daemon, a stand-in model, no network.
"""
from __future__ import annotations

import json
from typing import Any

import pytest


def test_a_judged_web_pick_does_not_replace_the_clock(tmp_path) -> None:
    from tests._blackbox_served_rig import REPO_ROOT, SEED_MANIFEST, ScriptedProvider, ServedDaemon, run_in_home

    model, session = "qwen3-stub:2b", "clock-web-pick"
    ask = "what's the current time in Nairobi?"

    def reply_fn(body: dict[str, Any]) -> Any:
        messages = [m for m in body.get("messages") or [] if isinstance(m, dict)]
        prompt = " ".join(str(m.get("content") or "") for m in messages)
        if "which of these tools does the message ask" in prompt:
            return json.dumps({"answer": [14]})   # the web, as qwen3:8b picked it on the iMac
        if "need the INTERNET" in prompt:
            return json.dumps({"answer": "yes"})
        return json.dumps({"answer": "no"}) if '{"answer"' in prompt else "It is noon."

    home = tmp_path / "home"
    provider = ScriptedProvider({model: "It is noon."}, reply_fn=reply_fn)
    daemon = ServedDaemon(home, env_extra={
        "OLLAMA_HOST": provider.base_url, "VOOL_OLLAMA_URL": provider.base_url,
        "VOOL_OLLAMA_CHAT_URL": f"{provider.base_url}/api/chat", "HOME": str(tmp_path / "owner-home"),
    })
    provider.__enter__()
    try:
        try:
            daemon.start(timeout=240)
        except Exception as exc:  # pragma: no cover - environment, not the guard
            pytest.skip(f"served daemon could not boot here: {exc}")
        seed = SEED_MANIFEST + '''
from tests._authorship_certification import certify_for_authorship
certify_for_authorship(manifest({model!r}))
'''
        run_in_home(home, seed.format(root=REPO_ROOT, base_url=provider.base_url, registered=[model], model=model))
        answered = daemon.chat(ask, session_id=session, model=model, mode="auto")
    finally:
        daemon.stop()
        provider.__exit__(None, None, None)

    said = str((answered.get("message") or {}).get("content") or "")
    assert "Web lookups are off" not in said and "look this up online" not in said, said
    assert "Nairobi" in said, said


def test_a_clock_asked_in_another_language_reaches_the_model_with_the_time_there(tmp_path) -> None:
    """iMac gate24 on a1cf06d6d: "Que horas são agora em Lisboa?" was judged a web ask and refused before any model
    saw it. The clock question takes it back from the web, and the answer call carries the time in that place."""
    from tests._blackbox_served_rig import REPO_ROOT, SEED_MANIFEST, ScriptedProvider, ServedDaemon, run_in_home

    model, session = "qwen3-stub:2b", "clock-other-language"
    ask = "Lisboa: que horas são lá neste momento?"
    seen = {"place_line": False}

    def reply_fn(body: dict[str, Any]) -> Any:
        messages = [m for m in body.get("messages") or [] if isinstance(m, dict)]
        prompt = " ".join(str(m.get("content") or "") for m in messages)
        if "which of these tools does the message ask" in prompt:
            return json.dumps({"answer": [14]})
        if "need the INTERNET" in prompt:
            return json.dumps({"answer": "yes"})
        if "The computer's clock answers that" in prompt:
            return json.dumps({"answer": "yes", "zone": "Europe/Lisbon"})
        if '{"answer"' in prompt:
            return json.dumps({"answer": "no"})
        if "If the person asks the time or date in a place this message names" in prompt and "Lisbon:" in prompt:
            seen["place_line"] = True
        return "Em Lisboa são agora as horas indicadas."

    home = tmp_path / "home"
    provider = ScriptedProvider({model: "ok"}, reply_fn=reply_fn)
    daemon = ServedDaemon(home, env_extra={
        "OLLAMA_HOST": provider.base_url, "VOOL_OLLAMA_URL": provider.base_url,
        "VOOL_OLLAMA_CHAT_URL": f"{provider.base_url}/api/chat", "HOME": str(tmp_path / "owner-home"),
    })
    provider.__enter__()
    try:
        try:
            daemon.start(timeout=240)
        except Exception as exc:  # pragma: no cover - environment, not the guard
            pytest.skip(f"served daemon could not boot here: {exc}")
        seed = SEED_MANIFEST + '''
from tests._authorship_certification import certify_for_authorship
certify_for_authorship(manifest({model!r}))
'''
        run_in_home(home, seed.format(root=REPO_ROOT, base_url=provider.base_url, registered=[model], model=model))
        answered = daemon.chat(ask, session_id=session, model=model, mode="auto")
    finally:
        daemon.stop()
        provider.__exit__(None, None, None)

    said = str((answered.get("message") or {}).get("content") or "")
    assert "Web lookups are off" not in said, said
    assert seen["place_line"], said


def test_a_clock_the_web_question_reads_no_still_reaches_the_model_with_the_time_there(tmp_path) -> None:
    """iMac gate25 on cbcb090a4: "Która godzina jest teraz w Nowym Jorku?" was read "no" for the web, so the clock
    question never ran and the answer call held no time in New York; the model wrote the computer's own time. The web
    question's "no" names the place's zone, and the answer call carries the time there."""
    from tests._blackbox_served_rig import REPO_ROOT, SEED_MANIFEST, ScriptedProvider, ServedDaemon, run_in_home

    model, session = "qwen3-stub:2b", "clock-web-no"
    ask = "Kolik je teď hodin v Novém Yorku, prosím?"
    seen = {"place_line": False}

    def reply_fn(body: dict[str, Any]) -> Any:
        messages = [m for m in body.get("messages") or [] if isinstance(m, dict)]
        prompt = " ".join(str(m.get("content") or "") for m in messages)
        if "which of these tools does the message ask" in prompt:
            return json.dumps({"answer": [14]})
        if "need the INTERNET" in prompt:
            return json.dumps({"answer": "no", "zone": "America/New_York"})
        if '{"answer"' in prompt:
            return json.dumps({"answer": "no"})
        if "If the person asks the time or date in a place this message names" in prompt and "New York:" in prompt:
            seen["place_line"] = True
        return "V New Yorku je teď uvedený čas."

    home = tmp_path / "home"
    provider = ScriptedProvider({model: "ok"}, reply_fn=reply_fn)
    daemon = ServedDaemon(home, env_extra={
        "OLLAMA_HOST": provider.base_url, "VOOL_OLLAMA_URL": provider.base_url,
        "VOOL_OLLAMA_CHAT_URL": f"{provider.base_url}/api/chat", "HOME": str(tmp_path / "owner-home"),
    })
    provider.__enter__()
    try:
        try:
            daemon.start(timeout=240)
        except Exception as exc:  # pragma: no cover - environment, not the guard
            pytest.skip(f"served daemon could not boot here: {exc}")
        seed = SEED_MANIFEST + '''
from tests._authorship_certification import certify_for_authorship
certify_for_authorship(manifest({model!r}))
'''
        run_in_home(home, seed.format(root=REPO_ROOT, base_url=provider.base_url, registered=[model], model=model))
        answered = daemon.chat(ask, session_id=session, model=model, mode="auto")
    finally:
        daemon.stop()
        provider.__exit__(None, None, None)

    said = str((answered.get("message") or {}).get("content") or "")
    assert "Web lookups are off" not in said, said
    assert seen["place_line"], said


def test_a_clock_picked_as_another_tool_reaches_the_model_with_the_time_there(tmp_path) -> None:
    """iMac gate26 on 11e7a9468: "Cât e ceasul acum la Viena?" was picked as a Reminders ask, the pick was dropped, and
    the web question ran only after the answer, so no time in Vienna reached the model. A turn the judge picked a tool
    for is put the web question before the answer."""
    from tests._blackbox_served_rig import REPO_ROOT, SEED_MANIFEST, ScriptedProvider, ServedDaemon, run_in_home

    model, session = "qwen3-stub:2b", "clock-picked-reminders"
    ask = "Spune-mi ora exactă din Viena acum"
    seen = {"place_line": False}

    def reply_fn(body: dict[str, Any]) -> Any:
        messages = [m for m in body.get("messages") or [] if isinstance(m, dict)]
        prompt = " ".join(str(m.get("content") or "") for m in messages)
        if "which of these tools does the message ask" in prompt:
            return json.dumps({"answer": [1]})
        if "Which one is the message?" in prompt:
            return json.dumps({"answer": "other"})   # the Reminders pick is dropped, as on the iMac
        if "need the INTERNET" in prompt:
            return json.dumps({"answer": "no", "zone": "Europe/Vienna"})
        if '{"answer"' in prompt:
            return json.dumps({"answer": "no"})
        if "If the person asks the time or date in a place this message names" in prompt and "Vienna:" in prompt:
            seen["place_line"] = True
        return "La Viena este acum ora indicată."

    home = tmp_path / "home"
    provider = ScriptedProvider({model: "ok"}, reply_fn=reply_fn)
    daemon = ServedDaemon(home, env_extra={
        "OLLAMA_HOST": provider.base_url, "VOOL_OLLAMA_URL": provider.base_url,
        "VOOL_OLLAMA_CHAT_URL": f"{provider.base_url}/api/chat", "HOME": str(tmp_path / "owner-home"),
    })
    provider.__enter__()
    try:
        try:
            daemon.start(timeout=240)
        except Exception as exc:  # pragma: no cover - environment, not the guard
            pytest.skip(f"served daemon could not boot here: {exc}")
        seed = SEED_MANIFEST + '''
from tests._authorship_certification import certify_for_authorship
certify_for_authorship(manifest({model!r}))
'''
        run_in_home(home, seed.format(root=REPO_ROOT, base_url=provider.base_url, registered=[model], model=model))
        answered = daemon.chat(ask, session_id=session, model=model, mode="auto")
    finally:
        daemon.stop()
        provider.__exit__(None, None, None)

    said = str((answered.get("message") or {}).get("content") or "")
    assert "Web lookups are off" not in said, said
    assert seen["place_line"], said
