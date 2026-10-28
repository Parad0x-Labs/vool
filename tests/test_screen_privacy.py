"""Hide my name on screen (core/screen_privacy.py): a display-only mask for streaming.

The rules are pinned on the path and name shapes a person really shows on screen (macOS, Linux,
Windows, JSON-escaped, other accounts, names inside file names) and on what must NOT change:
ordinary words, shared folders, unknown paths, and everything stored or sent.
"""
import json

import pytest

from core import screen_privacy
from core.screen_privacy import MASK, apply_rules, build_rules
from core.web.api.runtime import RuntimeServices
from core.web.api.service import dispatch_get, dispatch_post

MAC = build_rules(
    "/Users/rivermaple",
    ("rivermaple", "River Maple"),
    ("Rivers-MacBook-Pro.local", "Rivers-MacBook-Pro", "River\u2019s MacBook Pro"),
    "River",
)
WINDOWS = build_rules("C:\\Users\\Jesse", ("Jesse",), ("DESKTOP-7Q2", "MacBook"), "")

CASES = [
    # (rules, shown, expected)
    (MAC, "Saved to /Users/rivermaple/Desktop/report.pdf", "Saved to ~/Desktop/report.pdf"),
    (MAC, "/Users/RiverMaple/x", "~/x"),
    (MAC, "/Users/rivermaple", "~"),
    (MAC, "(/Users/rivermaple)", "(~)"),
    (MAC, "file:///Users/rivermaple/Downloads/a.txt", "file://~/Downloads/a.txt"),
    (MAC, "error: cannot open '/Users/rivermaple/.ssh/id' (EPERM)", "error: cannot open '~/.ssh/id' (EPERM)"),
    # Same account name, but a Windows path from somewhere else: the shape stays, the name goes.
    (MAC, "C:\\Users\\rivermaple\\Documents\\cv.docx", "C:\\Users\\" + MASK + "\\Documents\\cv.docx"),
    (MAC, '{"path": "C:\\\\Users\\\\rivermaple\\\\Documents"}', '{"path": "C:\\\\Users\\\\' + MASK + '\\\\Documents"}'),
    # Other people's home folders lose the name too; shared folders belong to nobody.
    (MAC, "C:/Users/bob/Desktop and /home/alice/notes", "C:/Users/" + MASK + "/Desktop and /home/" + MASK + "/notes"),
    (MAC, "/Users/Shared/x and C:\\Users\\Public\\y", "/Users/Shared/x and C:\\Users\\Public\\y"),
    (MAC, "/Users/rivermaple2/x", "/Users/" + MASK + "/x"),
    # Names inside file names, glued or not, and the computer's own names.
    (MAC, "~/Desktop/rivermaple-cv.pdf and rivermapleCV.pdf", "~/Desktop/" + MASK + "-cv.pdf and " + MASK + "CV.pdf"),
    (MAC, "Maple family photo.jpg", MASK + " family photo.jpg"),
    (MAC, "Hi River, your Mac Rivers-MacBook-Pro.local is fine", "Hi " + MASK + ", your Mac " + MASK + " is fine"),
    (MAC, "River\u2019s MacBook Pro", MASK),
    # Untouched: a web address, a drive that is not a home folder, ordinary text.
    (MAC, "https://example.com/home/page", "https://example.com/home/page"),
    (MAC, "/Volumes/VOOL_WORK/VOOL-Work stays", "/Volumes/VOOL_WORK/VOOL-Work stays"),
    (MAC, "nothing personal here", "nothing personal here"),
    # A Windows machine: either slash style, any letter case, JSON-escaped.
    (WINDOWS, "C:\\Users\\Jesse\\x", "~\\x"),
    (WINDOWS, "c:/users/jesse/y", "~/y"),
    (WINDOWS, '"C:\\\\Users\\\\Jesse\\\\z"', '"~\\\\z"'),
    (WINDOWS, "Jesse said hi from DESKTOP-7Q2", MASK + " said hi from " + MASK),
    # "MacBook" is a common word, never treated as a name.
    (WINDOWS, "a MacBook", "a MacBook"),
]


@pytest.mark.parametrize("rules,shown,expected", CASES)
def test_rules_hide_names_and_keep_everything_else(rules, shown, expected):
    assert apply_rules(shown, rules) == expected


def test_short_and_common_names_do_not_wreck_ordinary_words():
    # A three-letter account name is hidden only as a whole word; "user" is never a name to hide.
    rules = build_rules("/home/sls", ("sls",), ("user",), "Ann")
    assert apply_rules("sls wrote islslide in /home/sls/x", rules) == MASK + " wrote islslide in ~/x"
    assert apply_rules("the user asked Ann, not annotate", rules) == "the user asked " + MASK + ", not annotate"
    # A name with no usable home (a one-part home) adds no path rule and still masks names.
    rules = build_rules("/root", ("root",), (), "")
    assert apply_rules("/root/x by root", rules) == "/root/x by root"


def _prefs_post(body):
    return dispatch_post(path="/api/settings/prefs", body=body, headers={"Content-Type": "application/json"},
                         runtime=RuntimeServices(display_name="VOOL"), model_name="vool",
                         workspace_root_provider=lambda: None, client_host="127.0.0.1")


def _get(path, client_host="127.0.0.1"):
    return dispatch_get(path=path, query={}, runtime=RuntimeServices(display_name="VOOL"), model_name="vool", client_host=client_host)


@pytest.fixture()
def identity(monkeypatch, tmp_path):
    home = tmp_path / "rivermaple"
    monkeypatch.setattr(screen_privacy, "_machine_identity",
                        lambda: (str(home), ("rivermaple", "River Maple"), ("Rivers-MacBook-Pro",)))
    return home


def test_switch_is_off_by_default_and_off_carries_no_names(identity):
    assert json.loads(_get("/api/settings/prefs").body)["hide_name_on_screen"] is False
    off = json.loads(_get("/api/screen-privacy").body)
    assert off == {"enabled": False, "rules": []}
    page = _get("/settings").body.decode()
    assert "rivermaple" not in page.lower().split("window.__vool_screen_privacy__=", 1)[1][:200]
    # A non-boolean never turns it on.
    assert _prefs_post({"hide_name_on_screen": "yes"}).status == 400
    assert json.loads(_get("/api/screen-privacy").body)["enabled"] is False


def test_switch_on_masks_shown_text_and_never_stored_text(identity):
    assert _prefs_post({"hide_name_on_screen": True}).status == 200
    cfg = json.loads(_get("/api/screen-privacy").body)
    assert cfg["enabled"] is True and cfg["rules"]
    assert screen_privacy.mask_text(f"{identity}/Desktop/River notes.txt") == "~/Desktop/" + MASK + " notes.txt"
    # The stored preference file and everything else keep their bytes.
    from core.user_preferences import load_preferences
    assert load_preferences().hide_name_on_screen is True
    # Only this computer may read the names.
    assert _get("/api/screen-privacy", client_host="192.168.1.20").status == 403
    assert _prefs_post({"hide_name_on_screen": False}).status == 200
    assert screen_privacy.mask_text(f"{identity}/x") == f"{identity}/x"


def test_every_page_carries_the_mask_before_its_own_markup(identity):
    _prefs_post({"hide_name_on_screen": True})
    for path in ("/chat", "/settings", "/setup", "/trace", "/web0"):
        html = _get(path).body.decode()
        head = html.lower().find("<head")
        mask_at = html.find("window.__VOOL_SCREEN_PRIVACY__=")
        assert 0 <= head < mask_at, path
        # Nothing of the page's own (no <body>, no other <script>) comes before it.
        assert html.lower().find("<body") > mask_at and html.lower().find("<script") >= head
    assert screen_privacy.inject("<p>x</p>").startswith("<script data-vool-screen-privacy>window.__VOOL_SCREEN_PRIVACY__=")


def test_notification_banner_text_is_masked_when_on(identity):
    from core.operator.native_notifications import _submit_op

    def op():
        return _submit_op(identifier="n1", kind="immediate", deliver_at="", source_kind="reminder",
                          title="River, file ready", body=f"Saved to {identity}/Desktop/a.pdf",
                          preferences={"lock_screen": "full"}, thread="t")
    assert op()["title"] == "River, file ready"
    _prefs_post({"hide_name_on_screen": True})
    shown = op()
    assert shown["title"] == MASK + ", file ready" and shown["body"] == "Saved to ~/Desktop/a.pdf"


JS_CASES = [(shown, expected) for rules, shown, expected in CASES if rules is MAC]


@pytest.fixture()
def mac_identity(monkeypatch):
    """The same machine the MAC rules above describe, so the browser and Python are compared on one rule set."""
    monkeypatch.setattr(screen_privacy, "_machine_identity", lambda: (
        "/Users/rivermaple", ("rivermaple", "River Maple"),
        ("Rivers-MacBook-Pro.local", "Rivers-MacBook-Pro", "River\u2019s MacBook Pro")))
    monkeypatch.setattr(screen_privacy, "_profile_name", lambda: "River")
    assert screen_privacy.rules() == MAC


def test_browser_masks_live_restores_and_leaves_typing_alone(mac_identity):
    """The real Settings page and its real preference door in a browser. The page's own
    mask must produce exactly the Python result, follow the switch live in an open window,
    leave what the person types alone, and bring the real text back when switched off."""
    from urllib.parse import urlsplit

    from playwright.sync_api import sync_playwright

    runtime = RuntimeServices(display_name="VOOL")

    def route(r):
        path = urlsplit(r.request.url).path
        if r.request.method == "POST":
            resp = dispatch_post(path=path, body=json.loads(r.request.post_data or "{}"),
                                 headers={"content-type": "application/json"}, runtime=runtime, model_name="vool",
                                 workspace_root_provider=lambda: None, client_host="127.0.0.1")
        else:
            resp = dispatch_get(path=path, query={}, runtime=runtime, model_name="vool", client_host="127.0.0.1")
        return r.fulfill(status=resp.status, content_type=resp.content_type, body=resp.body)

    real = "Saved to /Users/rivermaple/Desktop/rivermaple-cv.pdf on Rivers-MacBook-Pro"
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            page = browser.new_context().new_page()
            page.route("**/*", route)
            page.goto("http://127.0.0.1:11435/settings")
            page.evaluate("""(real) => {
                const p = document.createElement('p'); p.id = 'probe'; p.textContent = real; p.title = real;
                const i = document.createElement('input'); i.id = 'typed'; i.value = real;
                const s = document.createElement('select'); s.id = 'pick';
                const a = document.createElement('option'); a.value = 'a'; a.textContent = real;
                const b = document.createElement('option'); b.textContent = real;
                s.append(a, b);
                document.body.append(p, i, s);
            }""", real)
            assert page.text_content("#probe") == real          # off: untouched
            _prefs_post({"hide_name_on_screen": True})
            page.evaluate("window.VoolScreenPrivacy.refresh()")
            page.wait_for_function("document.getElementById('probe').textContent.startsWith('Saved to ~')")
            shown = f"Saved to ~/Desktop/{MASK}-cv.pdf on {MASK}"
            assert page.text_content("#probe") == shown
            assert page.get_attribute("#probe", "title") == shown
            # A list choice with its own value shows the mask; one whose value IS its text keeps it.
            assert page.evaluate("[...document.getElementById('pick').options].map(o => [o.text, o.value])") == [
                [shown, "a"], [real, real]]
            # A text box only sitting on screen shows the mask, while what the page reads and saves
            # stays real; the moment the person clicks in to edit, it shows the real text.
            drawn = """() => { const f = document.createElement('iframe'); document.body.append(f);
                const get = Object.getOwnPropertyDescriptor(f.contentWindow.HTMLInputElement.prototype, 'value').get;
                const v = get.call(document.getElementById('typed')); f.remove(); return v; }"""
            assert page.evaluate(drawn) == shown
            assert page.evaluate("document.getElementById('typed').value") == real
            page.focus("#typed")
            assert page.evaluate(drawn) == real
            page.keyboard.press("End")
            page.keyboard.type(" River")
            assert page.evaluate(drawn) == real + " River"     # typing is never rewritten
            page.evaluate("document.activeElement.blur()")
            assert page.evaluate("document.getElementById('typed').value") == real + " River"
            page.wait_for_timeout(50)
            assert page.evaluate(drawn) == shown + " " + MASK
            # Text drawn after the switch is masked as it arrives, and matches Python exactly.
            got = page.evaluate("""(cases) => Promise.all(cases.map(([s]) => {
                const d = document.createElement('div'); d.textContent = s; document.body.append(d);
                return new Promise(res => setTimeout(() => res(d.textContent), 0));
            }))""", JS_CASES)
            assert got == [expected for _, expected in JS_CASES]
            page.evaluate("(real) => { document.getElementById('probe').textContent = 'new ' + real; }", real)
            page.wait_for_function("document.getElementById('probe').textContent.startsWith('new Saved to ~')")
            _prefs_post({"hide_name_on_screen": False})
            page.evaluate("window.VoolScreenPrivacy.refresh()")
            page.wait_for_function("!window.VoolScreenPrivacy.enabled()")
            assert page.text_content("#probe") == "new " + real   # the real text is back
            assert page.get_attribute("#probe", "title") == real
        finally:
            browser.close()
