"""The guided setup page (/setup), its Settings entry, and the prefs door contract behind them.

Pins the promises that make the redesign trustworthy rather than merely present: the page owns no
state and writes only through existing doors; the copy stays radically plain; the example is
labelled, bounded, pausable and static under reduced motion; the chat carries at most one line.
"""
from __future__ import annotations

import json
import re

from core.setup_progress import STEP_IDS, STEPS
from core.vool_chat_page import render_vool_chat_html
from core.vool_settings_page import render_vool_settings_html, settings_groups
from core.vool_setup_page import CAPTIONS, COPY, render_vool_setup_html

JARGON = ("llm", "inference", "api", "token", "endpoint", "credential", "provider", "runtime", "keychain", "json", "model")


def test_the_route_serves_the_setup_surface_like_settings() -> None:
    from core.web.api.runtime import RuntimeServices
    from core.web.api.service import dispatch_get

    runtime = RuntimeServices(display_name="VOOL")
    res = dispatch_get(path="/setup", query={}, runtime=runtime, model_name="vool", client_host="127.0.0.1")
    assert res.status == 200
    assert res.headers.get("X-Vool-Workstation-Surface") == "setup"
    body = res.body if isinstance(res.body, bytes) else res.body.encode()
    assert b"<title>VOOL Setup</title>" in body
    ref = dispatch_get(path="/settings", query={}, runtime=runtime, model_name="vool", client_host="127.0.0.1")
    assert set(res.headers) == set(ref.headers), "the two surfaces are served with the same header set"


def test_the_state_feed_is_owner_local_and_derived(tmp_path, monkeypatch) -> None:
    from core.runtime_paths import configure_runtime_home
    from core.web.api.runtime import RuntimeServices
    from core.web.api.service import dispatch_get

    home = tmp_path / "home"
    (home / "data").mkdir(parents=True)
    monkeypatch.setenv("VOOL_HOME", str(home))
    configure_runtime_home(home)
    try:
        runtime = RuntimeServices(display_name="VOOL")
        remote = dispatch_get(path="/api/setup/state", query={}, runtime=runtime, model_name="vool", client_host="10.0.0.9")
        assert remote.status == 403
        local = dispatch_get(path="/api/setup/state", query={}, runtime=runtime, model_name="vool", client_host="127.0.0.1")
        assert local.status == 200
        payload = json.loads(local.body)
        assert [s["id"] for s in payload["steps"]] == list(STEP_IDS)
        assert payload["show_entry"] is True and payload["done_count"] == 0
    finally:
        configure_runtime_home(None)


def test_the_page_is_self_contained_and_carries_the_step_model() -> None:
    html = render_vool_setup_html(build_commit="deadbeef")
    assert "deadbeef" in html
    for marker in ("src=\"http", "href=\"http", "@import", "cdn."):
        assert marker not in html, f"the page reaches outside itself: {marker}"
    for placeholder in ("__SETUP_STEPS__", "__SETUP_COPY__", "__SETUP_CAPTIONS__"):
        assert placeholder not in html
    for spec in STEPS:
        assert json.dumps(spec.title, ensure_ascii=False)[1:-1] in html
        assert json.dumps(spec.sentence, ensure_ascii=False)[1:-1] in html
        assert CAPTIONS[spec.id], f"{spec.id} has no caption (the text alternative for the example)"


def test_the_copy_is_radically_plain() -> None:
    for spec in STEPS:
        assert len(spec.title.split()) <= 5, spec.title
        assert len(spec.sentence.split()) <= 18, spec.sentence
        words = set(re.findall(r"[a-z]+", (spec.title + " " + spec.sentence).lower()))
        hits = words & set(JARGON)
        assert not hits, (spec.id, hits)
    assert len(COPY["lead"].split()) <= 12


def test_every_step_is_skippable_and_the_page_can_always_be_left() -> None:
    html = render_vool_setup_html()
    # One Skip control, one "Do this later" control, rendered for EVERY step (no per-step exemption).
    assert 'id="skipBtn"' in html and 'id="laterBtn"' in html and 'id="backBtn"' in html and 'id="nextBtn"' in html
    assert "$('#skipBtn').hidden = !!step.done;" in html, "skip disappears only when there is nothing left to skip"
    assert "if (e.key === 'Escape' && !typing) closeSetup();" in html
    # Leaving never navigates the chat away: framed -> ask the host; own window -> close, else the chat.
    assert "window.parent.postMessage({ type: 'vool-setup-close' }, window.location.origin)" in html
    assert "if (!window.closed) window.location.href = '/chat';" in html
    # No modal trap in the page itself.
    assert 'aria-modal' not in html


def test_the_example_is_labelled_bounded_pausable_and_static_under_reduced_motion() -> None:
    html = render_vool_setup_html()
    assert 'class="mock" id="mock" aria-hidden="true"' in html, "the mock is decorative; the caption is the text"
    assert "'<span class=\"mock-badge\">' + COPY.example + '</span>'" in html and COPY["example"] == "Example"
    assert "animation-duration:6s" in html, "one loop, including the hold, is six seconds"
    assert "animation-iteration-count:infinite" in html
    assert ".mock.paused * , .mock.paused .cursor::after { animation-play-state:paused !important; }" in html
    reduced = html.split("@media (prefers-reduced-motion: reduce) {", 1)[1].split("\n}\n", 1)[0]
    assert ".mock:not(.motion-on) *, .mock:not(.motion-on) .cursor::after { animation:none !important; }" in reduced
    assert ".mock:not(.motion-on) .cursor { display:none; }" in reduced
    # Playing under reduced motion is an explicit opt-in, never automatic.
    assert "if (view.motion === 'on') mock.classList.add('motion-on');" in html
    for step in STEP_IDS:
        assert f'.mock[data-step="{step}"] .cursor {{ animation-name:' in html, f"{step} has no cursor timeline"


def test_the_page_writes_only_through_existing_doors() -> None:
    html = render_vool_setup_html()
    posts = set(re.findall(r"postJSON\('([^']+)'", html))
    assert posts == {"/api/onboarding/choice", "/api/projects", "/api/settings/prefs", "/api/profile/remember"}, posts
    gets = set(re.findall(r"getJSON\('([^']+)'", html))
    assert gets == {"/api/setup/state"}
    assert "/api/setup/skip" not in html and "/api/setup/dismiss" not in html, "skip and dismiss are preferences, not new doors"


def test_a_light_and_a_dark_palette_are_both_declared_and_the_host_chooses() -> None:
    html = render_vool_setup_html()
    root = html.split(":root {", 1)[1].split("}", 1)[0]
    light = html.split(':root[data-theme="light"] {', 1)[1].split("}", 1)[0]
    for token in ("--bg", "--panel", "--ink", "--muted", "--accent", "--border", "--mk-bg"):
        assert token + ":" in root and token + ":" in light, token
    # Dark by default like the chat and Settings; the OS scheme alone never flips a framed page light.
    assert "prefers-color-scheme" not in html
    assert "document.documentElement.dataset.theme = 'light'" in html


def test_the_chat_bottom_checklist_is_gone_and_one_line_remains() -> None:
    html = render_vool_chat_html()
    for gone in ('id="pactCard"', "PACT_COPY", "Step {n} of 8", "pact-card", "/api/onboarding/pact"):
        assert gone not in html, gone
    assert html.count('id="setupLine"') == 1
    line = html[html.index('id="setupLine"'):]
    line = line[: line.index("</div>")]
    assert "hidden" in line.split(">", 1)[0], "the line starts hidden; the derived state shows it"
    assert 'id="setupLineOpen"' in line and 'id="setupLineHide"' in line
    assert "line.hidden = !show;" in html and "const show = !!(d && d.show_chat_line);" in html
    # Dismissing is the one persisted choice, through the one prefs door.
    assert "JSON.stringify({ setup_dismissed: true })" in html
    # Opening mirrors Settings: native bridge, named window, the same frame overlay.
    assert "function openSetup(step)" in html
    assert "openSurfaceInFrame('/setup' + frag, 'VOOL Setup')" in html
    assert "d.type === 'vool-setup-close'" in html and "d.type === 'vool-setup-pick-folder'" in html


def test_settings_carries_the_entry_first_and_removes_it_from_the_model_when_hidden() -> None:
    groups = settings_groups()
    assert groups[0]["id"] == "setup"
    row = groups[0]["rows"][0]
    assert row["kind"] == "custom" and row["widget"] == "setup_progress"
    assert row["read"] == {"url": "/api/setup/state"}
    assert not row.get("write"), "the entry writes nothing of its own"
    html = render_vool_settings_html()
    assert "function widgetSetupProgress" in html
    assert "if (!setup || !setup.show_entry) removeSetupGroup();" in html, "hidden means removed before the first paint"
    assert "MODEL.splice(i, 1)" in html
    assert 'id="setupCount"' not in html and "head.id = 'setupCount';" in html
    assert "JSON.stringify({ setup_dismissed: true })" in html
    assert "openSetupPage(src.first_undone || '')" in html, "Continue reopens at the first undone step"


def test_the_prefs_door_accepts_and_reads_back_the_two_setup_fields(tmp_path, monkeypatch) -> None:
    from core.command_registry.groups.convergence import _handle_prefs_list
    from core.runtime_paths import configure_runtime_home
    from core.user_preferences import load_preferences
    from core.web.api.registry_authorities import set_prefs_authority
    from core.web.api.runtime import RuntimeServices

    home = tmp_path / "home"
    (home / "data").mkdir(parents=True)
    monkeypatch.setenv("VOOL_HOME", str(home))
    configure_runtime_home(home)
    try:
        rt = RuntimeServices(display_name="VOOL")
        hdr = {"Content-Type": "application/json"}
        assert set_prefs_authority({"setup_dismissed": "yes"}, hdr, rt).status == 400
        assert set_prefs_authority({"setup_skipped_steps": ["thinking"]}, hdr, rt).status == 400
        assert set_prefs_authority({"setup_skipped_steps": "thinking,\x00folder"}, hdr, rt).status == 400
        assert set_prefs_authority({"autonomy_chosen_at": "2026-01-01T00:00:00Z"}, hdr, rt).status == 400, "provenance is stamped by the door, never posted"
        assert set_prefs_authority({"setup_dismissed": True, "setup_skipped_steps": "thinking,folder"}, hdr, rt).status == 200
        listed = _handle_prefs_list(None, None).data
        assert listed["setup_dismissed"] is True and listed["setup_skipped_steps"] == "thinking,folder"
        assert "autonomy_chosen_at" not in listed
        assert load_preferences().autonomy_chosen_at == "", "an unrelated write does not stamp autonomy"
    finally:
        configure_runtime_home(None)


# --- the three honesty pins that left with the chat pact card, re-homed on the page that replaced it ---


def _page_script() -> str:
    html = render_vool_setup_html()
    m = re.search(r"<script>(.*)</script>", html, re.S)
    assert m, "the setup page carries one inline script"
    return m.group(1)


def test_done_state_is_never_minted_by_the_page() -> None:
    """A step reads as done only because GET /api/setup/state says so; the page never sets it."""
    js = _page_script()
    assert not re.search(r"\bdone\s*[:=]\s*true\b", js), "the page must not mark a step done on its own"
    assert not re.search(r"\.done\s*=", js), "done-ness is read from the state feed, never assigned"
    assert "view.snap = await getJSON('/api/setup/state')" in js, "the only source of done-ness is the derived feed"


def test_every_saved_line_follows_a_re_read_of_the_state_feed() -> None:
    """No unreturned verification claim: after each write the page re-reads the feed and only then speaks.

    Every ``postJSON`` write is followed by ``await refresh()`` before any ``live(`` line, and every
    'Saved' wording is guarded by the re-read ``current().done`` — otherwise the page says
    'Not confirmed yet.'
    """
    js = _page_script()
    writes = [m.start() for m in re.finditer(r"await postJSON\(", js)]
    assert len(writes) >= 5, "the five step writes are present"
    for start in writes:
        tail = js[start:]
        next_live = tail.find("live(")
        next_refresh = tail.find("await refresh()")
        assert next_refresh != -1 and (next_live == -1 or next_refresh < next_live), (
            "a write spoke before re-reading the state feed:\n" + tail[:400]
        )
    for m in re.finditer(r"live\((.{0,80}?'Saved)", js):
        assert "current().done ?" in m.group(1), "every 'Saved' is conditional on the re-read done-state: " + m.group(0)
    assert "'Not confirmed yet.'" in js


def test_the_page_carries_no_oauth_and_no_deep_links() -> None:
    """Setup never sends anyone to a browser sign-in or a custom URL scheme; keys are pasted in Settings."""
    html = render_vool_setup_html()
    stripped = html.replace('xmlns="http://www.w3.org/2000/svg"', "")
    assert "http://" not in stripped and "https://" not in stripped, "no outbound links on the setup page"
    assert "oauth" not in stripped.lower() and "sign in with" not in stripped.lower()
    assert not re.search(r"\b[a-z][a-z0-9+.-]*://", stripped.replace("http://", "").replace("https://", "")), "no custom URL schemes (deep links)"
    # The page's own navigation is same-origin only: Settings for keys, chat when setup closes.
    targets = re.findall(r"(?:window\.open\(|location\.href\s*=\s*)('[^']*')", stripped)
    assert targets, "the page navigates somewhere (to Settings and back to chat)"
    for target in targets:
        assert target.startswith("'/settings") or target == "'/chat'", "navigation stays on this origin: " + target
