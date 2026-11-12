"""A failed load in the appended fragments never renders as 'nothing here'.

Each fixed loader is proven both ways under the node harness: a failed read (the fetch throws,
answers non-OK, or the body is not the list) shows the calm retry row and never the empty state;
the empty state appears only on a real OK answer whose list is really empty. Each fragment's
inline script is unwrapped the way tests/test_notification_inbox_laws.py unwraps it, so the
driver runs inside the fragment's own scope and drives its real loaders and painters.
"""
from __future__ import annotations

from core.addon_store_fragment import render_addon_store_fragment
from core.contacts_fragment import render_contacts_fragment
from core.notification_fragment import render_notification_fragment
from tests.chat_page_js_harness import DOM, run_node

UNREACHABLE = "Can\u2019t reach VOOL right now"

_BY_ID = """
const byId = (root, id) => {
  for (const c of (root.children || [])) { if (c.id === id) return c; const f = byId(c, id); if (f) return f; }
  return null;
};
const tick = () => new Promise((r) => process.nextTick(r));
"""


def _fragment_body(html: str) -> str:
    """The fragment's first inline script with its IIFE opened, so a driver can run inside it."""
    body = html.split("<script>", 1)[1].split("</script>", 1)[0]
    return body.replace("(function(){", "(async function(){", 1).rsplit("})();", 1)[0]


def _run(html: str, prelude: str, driver: str) -> dict:
    # Two closers: one for the fragment's own (still-open) function, one for the driver wrapper.
    return run_node(DOM + "\n" + prelude + "\n;(async function(){\n" + _fragment_body(html) + "\n" + driver + "\n})();\n})();\n")


# --- the notification bell -------------------------------------------------------------------------------------------

def _notification(prelude_mode: str, driver: str) -> dict:
    prelude = (
        """
globalThis.__calls = 0;
globalThis.fetch = async (url) => {
  if (String(url).indexOf('/api/notifications?') === 0) {
    globalThis.__calls += 1;
"""
        + prelude_mode
        + """
  }
  return { ok: true, status: 200, json: async () => ({ ok: true }) };
};
"""
        + _BY_ID
    )
    return _run(render_notification_fragment(), prelude, driver)


def test_notification_read_failure_shows_the_retry_row_not_the_empty_state() -> None:
    result = _notification(
        "    throw new TypeError('Failed to fetch');",
        r"""
for (let i = 0; i < 10; i++) await tick();   // the boot read settles into its failure
clearTimeout(centreRetryTimer); centreRetryTimer = null; centreRetryDelayMs = 10;   // fast retries
paintList();
const afterFail = pop.innerHTML;
await pollCentre();
await new Promise((resolve) => _st(resolve, 120));   // the harness unrefs page timers; _st is the real setTimeout
out({ calls: globalThis.__calls, afterFail,
      unreachable: afterFail.includes('Can\u2019t reach VOOL right now'),
      empty: afterFail.includes('Nothing here yet'),
      loaded: centreLoaded });
""",
    )
    assert result["errors"] == []
    assert result["unreachable"] is True
    assert result["empty"] is False, "a failed read must never paint 'Nothing here yet.'"
    assert result["loaded"] is False
    assert result["calls"] >= 3, "the failed read was never retried"


def test_notification_answer_that_is_not_the_inbox_is_not_the_empty_state() -> None:
    for mode in (
        "    return { ok: false, status: 503, json: async () => ({ ok: false, state: 'starting' }) };",
        "    return { ok: true, status: 200, json: async () => ({ ok: true, unread: 0, cursor: 0 }) };",
    ):
        result = _notification(
            mode,
            r"""
for (let i = 0; i < 10; i++) await tick();
paintList();
const html = pop.innerHTML;
out({ unreachable: html.includes('Can\u2019t reach VOOL right now'), empty: html.includes('Nothing here yet'), loaded: centreLoaded });
""",
        )
        assert result["errors"] == []
        assert result["unreachable"] is True, mode
        assert result["empty"] is False, mode
        assert result["loaded"] is False, mode


def test_notification_real_empty_answer_is_the_only_path_to_the_empty_state() -> None:
    result = _notification(
        "    return { ok: true, status: 200, json: async () => ({ ok: true, items: [], unread: 0, cursor: 0 }) };",
        r"""
for (let i = 0; i < 10; i++) await tick();
paintList();
const html = pop.innerHTML;
out({ empty: html.includes('Nothing here yet'), unreachable: html.includes('Can\u2019t reach VOOL right now'), loaded: centreLoaded });
""",
    )
    assert result["errors"] == []
    assert result["empty"] is True
    assert result["unreachable"] is False
    assert result["loaded"] is True


# --- the contacts overlay --------------------------------------------------------------------------------------------

def _contacts(contacts_answer: str, driver: str) -> dict:
    prelude = (
        """
globalThis.__listCalls = 0;
globalThis.fetch = async (url) => {
  const path = new URL(String(url), 'http://vool.test').pathname;
  if (path === '/api/contacts') {
    globalThis.__listCalls += 1;
"""
        + contacts_answer
        + """
  }
  if (path.indexOf('/api/contacts') === 0) throw new TypeError('Failed to fetch');
  return { ok: true, status: 200, json: async () => ({ ok: true }) };
};
"""
        + _BY_ID
    )
    return _run(render_contacts_fragment(), prelude, driver)


def test_contacts_read_failure_shows_the_retry_row_not_the_empty_state() -> None:
    result = _contacts(
        "    throw new TypeError('Failed to fetch');",
        r"""
await open();
for (let i = 0; i < 10; i++) await tick();
clearTimeout(retryTimer); retryTimer = null; retryDelayMs = 10;   // fast retries
const afterFail = byId(document.body, 'vcList').textContent;
await refresh();
await new Promise((resolve) => _st(resolve, 120));
out({ listCalls: globalThis.__listCalls, afterFail,
      unreachable: afterFail.includes('Can\u2019t reach VOOL right now'),
      empty: afterFail.includes('Nothing saved yet'),
      loaded: contactsLoaded });
""",
    )
    assert result["errors"] == []
    assert result["unreachable"] is True
    assert result["empty"] is False, "a failed read must never paint 'Nothing saved yet.'"
    assert result["loaded"] is False
    assert result["listCalls"] >= 3, "the failed read was never retried"


def test_contacts_answer_without_the_list_is_not_the_empty_state() -> None:
    result = _contacts(
        "    return { ok: true, status: 200, json: async () => ({ ok: true }) };",
        r"""
await open();
for (let i = 0; i < 10; i++) await tick();
const text = byId(document.body, 'vcList').textContent;
out({ unreachable: text.includes('Can\u2019t reach VOOL right now'), empty: text.includes('Nothing saved yet'), loaded: contactsLoaded });
""",
    )
    assert result["errors"] == []
    assert result["unreachable"] is True
    assert result["empty"] is False
    assert result["loaded"] is False


def test_contacts_real_empty_answer_shows_the_empty_state() -> None:
    result = _contacts(
        "    return { ok: true, status: 200, json: async () => ({ ok: true, contacts: [] }) };",
        r"""
await open();
for (let i = 0; i < 10; i++) await tick();
const list = byId(document.body, 'vcList').textContent;
const detail = byId(document.body, 'vcDetail').textContent;
out({ empty: list.includes('Nothing saved yet'), unreachable: list.includes('Can\u2019t reach VOOL right now'),
      addContact: detail.includes('Add contact'), loaded: contactsLoaded });
""",
    )
    assert result["errors"] == []
    assert result["empty"] is True
    assert result["unreachable"] is False
    assert result["addContact"] is True
    assert result["loaded"] is True


# --- the add-on store -------------------------------------------------------------------------------------------------

def _addons(mode: str, driver: str) -> dict:
    prelude = (
        """
globalThis._pluginCatalog = {};   // the host page owns this binding; the fragment only assigns it
globalThis.__mode = '""" + mode + """';
globalThis.__calls = 0;
const __ok = (j) => ({ ok: true, status: 200, json: async () => j });
globalThis.fetch = async (url) => {
  const path = new URL(String(url), 'http://vool.test').pathname;
  if (path === '/api/addons') {
    globalThis.__calls += 1;
    if (globalThis.__mode === 'throw') throw new TypeError('Failed to fetch');
    if (globalThis.__mode === 'non-ok') return { ok: false, status: 503, json: async () => ({ ok: false, state: 'starting' }) };
    if (globalThis.__mode === 'no-entries') return __ok({ ok: true });
    return __ok({ ok: true, entries: [], reviews: [], eyebrow: { configured: false } });
  }
  if (path === '/api/plugins') return __ok({ ok: true, plugins: [] });
  return __ok({ ok: true });
};
// The fragment renders through append/replaceChildren/before; the shared DOM stub has appendChild only.
const __patch = (n) => { if (!n.replaceChildren) n.replaceChildren = function () { this.children.length =  0; };
  if (!n.append) n.append = function (...kids) { kids.forEach((k) => this.appendChild(k)); };
  if (!n.prepend) n.prepend = function (...kids) { kids.forEach((k) => this.insertBefore(k)); };
  if (!n.before) n.before = function () {}; return n; };
const __create = document.createElement;
document.createElement = (t) => __patch(__create(t));
const __gebi = document.getElementById.bind(document);
document.getElementById = (id) => __patch(__gebi(id));
"""
        + _BY_ID
    )
    return _run(render_addon_store_fragment(), prelude, driver)


_ADDON_DRIVER_FAIL = r"""
retryDelayMs = 10;   // fast retries
await load();
render('');
const afterFail = document.getElementById('pluginsBody').textContent;
await new Promise((resolve) => _st(resolve, 150));
out({ calls: globalThis.__calls, afterFail,
      error: afterFail.includes('Could not read the add-on catalogue'),
      retry: afterFail.includes('Try again'),
      empty: afterFail.includes('No matching add-ons') || afterFail.includes('No skills match that search'),
      loaded: !!data });
"""


def test_addon_store_read_failure_is_the_error_row_not_the_empty_state() -> None:
    for mode in ("throw", "non-ok"):
        result = _addons(mode, _ADDON_DRIVER_FAIL)
        assert result["errors"] == []
        assert result["error"] is True, mode
        assert result["retry"] is True, mode
        assert result["empty"] is False, mode
        assert result["loaded"] is False, mode
        assert result["calls"] >= 2, f"the failed read was never retried ({mode})"


def test_addon_store_answer_that_is_not_the_catalogue_is_not_the_empty_state() -> None:
    result = _addons("no-entries", _ADDON_DRIVER_FAIL)
    assert result["errors"] == []
    assert result["error"] is True
    assert result["empty"] is False, "a body without the catalogue must never paint the empty grid"
    assert result["loaded"] is False


def test_addon_store_real_empty_catalogue_shows_the_empty_state() -> None:
    result = _addons(
        "empty",
        r"""
await load();
render('');
const text = document.getElementById('pluginsBody').textContent;
out({ empty: text.includes('No skills match that search'),
      error: text.includes('Could not read the add-on catalogue'),
      loaded: !!data });
""",
    )
    assert result["errors"] == []
    assert result["empty"] is True
    assert result["error"] is False
    assert result["loaded"] is True
