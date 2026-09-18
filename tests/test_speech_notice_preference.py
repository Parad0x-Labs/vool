"""The native notice dismissal survives a new preference read through the real doors."""
from core.web.api.service import dispatch_get, dispatch_post
from core.web.api.runtime import RuntimeServices

def test_dismissal_is_persisted_and_read_back_without_changing_other_preferences():
    from core.user_preferences import load_preferences, save_preferences
    p = load_preferences()
    p.humor_percent = 37
    save_preferences(p)
    response = dispatch_post(path="/api/settings/prefs", body={"speech_notice_dismissed": True}, headers={"Content-Type": "application/json"}, runtime=RuntimeServices(display_name="VOOL"), model_name="vool", workspace_root_provider=lambda: None)
    assert response.status == 200
    assert load_preferences().speech_notice_dismissed is True
    assert load_preferences().humor_percent == 37
    response = dispatch_get(path="/api/settings/prefs", query={}, runtime=RuntimeServices(display_name="VOOL"), model_name="vool")
    assert response.status == 200
    import json
    assert json.loads(response.body)["speech_notice_dismissed"] is True


def test_browser_dismissal_survives_a_fresh_private_context():
    """Real page + real preference doors; dictation availability and other APIs are fixtures."""
    import json
    from urllib.parse import urlsplit
    from playwright.sync_api import sync_playwright, expect
    from core.vool_chat_page import render_vool_chat_html
    from core.user_preferences import load_preferences

    html = render_vool_chat_html()
    runtime = RuntimeServices(display_name="VOOL")
    writes = []

    def route(r):
        path = urlsplit(r.request.url).path
        if path == "/":
            return r.fulfill(status=200, content_type="text/html", body=html)
        if path == "/api/settings/prefs":
            if r.request.method == "POST":
                body = json.loads(r.request.post_data)
                writes.append(body)
                response = dispatch_post(path=path, body=body, headers={"content-type":"application/json"}, runtime=runtime, model_name="vool", workspace_root_provider=lambda: "/tmp")
            else:
                response = dispatch_get(path=path, query={}, runtime=runtime, model_name="vool")
            return r.fulfill(status=response.status, content_type="application/json", body=response.body)
        payload = {"ok":True, "sessions":[], "items":[], "models":[], "projects":[]}
        if path == "/api/chat/dictation":
            payload = {"available":False,"message":"This machine has not granted Speech Recognition access."}
        return r.fulfill(status=200, content_type="application/json", body=json.dumps(payload))

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            context = browser.new_context()
            context.route("**/*", route)
            page = context.new_page()
            page.goto("http://vool-notice.test/")
            expect(page.locator("#dictationNotice")).to_be_visible()
            page.get_by_role("button", name="Dismiss speech notice permanently").click()
            expect(page.locator("#dictationNotice")).to_be_hidden()
            page.wait_for_function("document.getElementById('dictationNote').hidden")
            assert writes == [{"speech_notice_dismissed":True}]
            assert load_preferences().speech_notice_dismissed
            context.close()
            context = browser.new_context()  # no localStorage survives
            context.route("**/*", route)
            page = context.new_page()
            with page.expect_response("**/api/chat/dictation?*"):
                page.goto("http://vool-notice.test/")
            expect(page.locator("#dictationNotice")).to_be_hidden()
            page.locator("#input").fill("Typed chat still works")
            expect(page.locator("#input")).to_have_value("Typed chat still works")
            context.close()
        finally:
            browser.close()
