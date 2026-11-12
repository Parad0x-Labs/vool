"""Served /v1/chat/completions proof that the skill picker reads the ask on a REAL turn.

A real daemon (apps/vool_api_server.py) per developer setting, an isolated home, and the scripted
loopback provider that records every provider-bound request (tests/_served_skill_rig.py). Each turn
is a chat message; the evidence is what the runtime itself recorded and bound: the
``tool_offer_skills`` events of the session (every lane that put skill guidance into the turn's
context emits one) and the SKILL.md bodies in the provider-bound system instructions.

Before this change the served picker read only the router's task class, so the developer asks
below (classed ``unknown`` or ``chat_conversation``) reached no skill, and every advisory turn
carried the evidence lens.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests._served_skill_rig import (
    PROVIDER_MANIFEST_ID,
    ProviderState,
    ServedDaemon,
    make_provider_server,
)

REPO_SKILLS = Path(__file__).resolve().parents[1] / "skills"
LENS_IDS = {
    "lens-architecture", "lens-evidence", "lens-market", "lens-security", "lens-strategy", "lens-ux",
}
DEVELOPER_IDS = {
    "ci-repair", "code-review", "cumulative-testing", "database", "vool-database", "feature-build",
    "migration", "performance", "project-archaeology", "release-gate", "repo-onboarding",
    "root-cause-repair", "security-audit",
}


def _body(skill_dir: str) -> str:
    text = (REPO_SKILLS / skill_dir / "SKILL.md").read_text(encoding="utf-8")
    return text.split("---", 2)[2].strip()


def _rig(tmp_path_factory, name: str, extra_env: dict[str, str]):
    tmp = tmp_path_factory.mktemp(name)
    state = ProviderState()
    server, port = make_provider_server(state)
    home = tmp / "home"
    home.mkdir(parents=True)
    daemon = ServedDaemon(home, port, extra_env=extra_env).start()
    daemon.pin_provider_model()
    cert = daemon.certify_provider_model()
    result = cert.get("result") or cert
    assert result.get("state") == "verified", f"rig provider must certify: {json.dumps(result)[:400]}"
    return type("Rig", (), {"state": state, "daemon": daemon, "server": server})


@pytest.fixture(scope="module")
def developer_rig(tmp_path_factory):
    rig = _rig(tmp_path_factory, "picker-developer", {"VOOL_DEVELOPER_SKILLS": "1"})
    yield rig
    rig.daemon.stop()
    rig.server.shutdown()


@pytest.fixture(scope="module")
def person_rig(tmp_path_factory):
    rig = _rig(tmp_path_factory, "picker-person", {"VOOL_DEVELOPER_SKILLS": "0"})
    yield rig
    rig.daemon.stop()
    rig.server.shutdown()


def _turn(rig, text: str, session: str) -> tuple[set[str], str]:
    """(skills the runtime recorded as reaching the turn, provider-bound instructions)."""
    rig.state.requests.clear()
    rig.state.script = [{"final": "Noted."}]
    reply = rig.daemon.post(
        "/v1/chat/completions",
        {
            "model": PROVIDER_MANIFEST_ID,
            "messages": [{"role": "user", "content": text}],
            "stream": False,
            "mode": "manual",
            "session_id": session,
            "turn_id": f"{session}-1",
        },
    )
    # The turn must reach the model: an answer the runtime settles on its own binds no skill, and
    # an absence asserted on such a turn would prove nothing.
    assert rig.state.requests, f"{text!r} never reached the provider: {json.dumps(reply)[:300]}"
    served = str(reply.get("vool_session_id") or session)
    names: set[str] = set()
    for event in rig.daemon.session_events(served):
        if event.get("event_type") != "tool_offer_skills":
            continue
        for row in event.get("skills") or []:
            if row.get("complete", True) and not row.get("dropped"):
                names.add(str(row.get("name")))
    bound = "\n".join(p for p in rig.state.system_prompts() if "<retrieved_context>" not in p)
    return names, bound


@pytest.mark.parametrize(
    ("text", "wanted", "body"),
    [
        ("the pipeline on main went red after the last merge, get the build green again",
         "ci-repair", "vool-ci-repair"),
        ("look over my diff before I push and flag anything risky", "code-review", "vool-code-review"),
        ("the invoice total test started failing after my last change, find the root cause",
         "root-cause-repair", "vool-root-cause-repair"),
    ],
)
def test_served_developer_turn_binds_the_skill_its_words_ask_for(developer_rig, text, wanted, body) -> None:
    session = f"picker-dev-{wanted}"
    names, bound = _turn(developer_rig, text, session)
    assert wanted in names, f"{text!r} recorded {sorted(names)}"
    assert _body(body) in bound, f"{wanted}'s instructions never reached the provider"


def test_served_decision_turn_binds_the_strategy_lens_not_the_evidence_lens(developer_rig) -> None:
    names, bound = _turn(developer_rig, "help me decide between renting a flat in town or buying further out",
                         "picker-dev-decision")
    assert "lens-strategy" in names, sorted(names)
    assert "lens-evidence" not in names, sorted(names)
    assert _body("vool-lens-strategy") in bound
    assert _body("vool-lens-evidence") not in bound


def test_served_everyday_turn_binds_no_task_skill_and_no_lens(developer_rig) -> None:
    names, _bound = _turn(developer_rig, "write me a short poem about the sea at night", "picker-dev-poem")
    assert not names & (DEVELOPER_IDS | LENS_IDS | {"media-studio", "browser", "x-editorial-studio"}), sorted(names)


@pytest.mark.parametrize(
    "text",
    [
        "explain how vaccines work in simple terms",
        "write a polite note to my landlord, the boiler has been broken all week",
        # Was "open example.com and tell me what it says": on the v0.8 line a named address with
        # web lookups off is answered by the URL lane in plain words and never reaches the model,
        # so it could not prove an absence here. A developer word in an everyday ask can.
        "help me plan my kid's birthday party, I want a fun feature like a treasure hunt",
    ],
)
def test_served_everyday_turn_of_a_person_binds_no_developer_skill(person_rig, text) -> None:
    session = "picker-person-" + str(abs(hash(text)) % 10_000)
    names, bound = _turn(person_rig, text, session)
    assert not names & DEVELOPER_IDS, f"{text!r} recorded {sorted(names)}"
    for skill_dir in ("vool-database", "vool-project-archaeology", "vool-root-cause-repair"):
        assert _body(skill_dir) not in bound, f"{skill_dir} reached a person's everyday turn"


# --------------------------------------------------------------------------- media tools

MEDIA_ASK = "cut the boring first ten seconds out of my holiday video"


@pytest.fixture(scope="module")
def media_rig(tmp_path_factory):
    """A daemon on a machine WITH the media tools: a nebula-media worker and ffmpeg/ffprobe on
    PATH (tests/_media_tools_stub.py). The runtime's own boot registers the media family."""
    from tests._media_tools_stub import media_tools_env

    rig = _rig(tmp_path_factory, "picker-media", media_tools_env(tmp_path_factory.mktemp("media-tools")))
    yield rig
    rig.daemon.stop()
    rig.server.shutdown()


@pytest.fixture(scope="module")
def no_media_rig(tmp_path_factory):
    """The same daemon on a machine WITHOUT them: no worker where NEBULA_MEDIA_HOME points."""
    empty = tmp_path_factory.mktemp("no-media-worker")
    rig = _rig(tmp_path_factory, "picker-no-media", {"NEBULA_MEDIA_HOME": str(empty)})
    yield rig
    rig.daemon.stop()
    rig.server.shutdown()


def test_served_video_edit_binds_media_studio_where_the_media_tools_are(media_rig) -> None:
    names, bound = _turn(media_rig, MEDIA_ASK, "picker-media-edit")
    assert "media-studio" in names, sorted(names)
    assert _body("media-studio") in bound, "media-studio's instructions never reached the provider"


def test_served_video_edit_binds_no_media_skill_where_the_tools_are_missing(no_media_rig) -> None:
    names, bound = _turn(no_media_rig, MEDIA_ASK, "picker-no-media-edit")
    assert "media-studio" not in names, sorted(names)
    assert _body("media-studio") not in bound
