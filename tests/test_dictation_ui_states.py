"""Dictation/voice UI laws: states, stop path, push-to-talk, read-aloud, permissions.

NEW regressions for product/desktop-usability-20260917, driving the REAL page JavaScript
under the shared node DOM stub with fakes installed at the browser seams
(``navigator.mediaDevices``, ``MediaRecorder``, ``speechSynthesis``) — the page's own
handlers, state machine and fetch calls are the production ones.

The distinct failure paths asserted here (none covered by the existing suites):

* the BASELINE stop-path defect: the button's title said "click to stop and transcribe" but
  the handler returned early while recording — a recording could only end by the 115s
  timeout. The second click must STOP and transcribe.
* Escape cancels a recording with no upload (tracks released, no POST).
* a refused microphone shows the typed reason and the keyboard composer stays usable.
* the server's typed refusal (missing recogniser) renders message + remediation.
* voice mode: hold-to-talk places a DRAFT (never auto-sends), a completed answer is read
  aloud locally with a working Stop, and Esc stops playback.
* the recognition locale is explicit, persisted and sent on the request.
"""

from __future__ import annotations

import re

from core.vool_chat_page import render_vool_chat_html
from tests.chat_page_js_harness import DOM, run_node

HTML = render_vool_chat_html()


def page_script() -> str:
    scripts = re.findall(r"<script[^>]*>(.*?)</script>", HTML, re.DOTALL)
    assert scripts, "no inline script found in the chat page"
    return max(scripts, key=len)


# Installed BEFORE the page script so its feature detections (PointerEvent, document
# listeners) behave like a browser; the dictation fetch seam is redirected to a capture fake.
PRELUDE = r"""
globalThis.__docH = {};
document.addEventListener = (t, f) => { (__docH[t] = __docH[t] || []).push(f); };
globalThis.PointerEvent = class PointerEventFake {};
const micCalls = { getUserMedia: 0, tracksStopped: 0 };
const fakeTrack = { stop: () => { micCalls.tracksStopped += 1; } };
const fakeStream = { getTracks: () => [fakeTrack] };
Object.defineProperty(navigator, 'mediaDevices', {
  value: { getUserMedia: async () => { micCalls.getUserMedia += 1; return fakeStream; } },
  configurable: true,
});
const recorderLog = { started: 0, stopped: 0 };
globalThis.MediaRecorder = window.MediaRecorder = class {
  constructor(stream, opts) { this.stream = stream; this.state = 'inactive'; this.mimeType = (opts && opts.mimeType) || 'audio/webm'; }
  start() { this.state = 'recording'; recorderLog.started += 1; }
  stop() {
    if (this.state !== 'recording') return;
    this.state = 'inactive'; recorderLog.stopped += 1;
    if (this.ondataavailable) this.ondataavailable({ data: { size: 2048 } });
    if (this.onstop) this.onstop();
  }
};
globalThis.recorderLog = recorderLog; globalThis.micCalls = micCalls;
const speechSynthesisLog = { spoken: [], cancelled: 0 };
const synth = {
  speaking: false,
  speak(u) { this.speaking = true; speechSynthesisLog.spoken.push({ text: String(u.text || ''), lang: u.lang || null }); },
  cancel() { this.speaking = false; speechSynthesisLog.cancelled += 1; },
};
globalThis.speechSynthesis = window.speechSynthesis = synth;
globalThis.SpeechSynthesisUtterance = window.SpeechSynthesisUtterance = class { constructor(t) { this.text = t; } };
globalThis.Blob = window.Blob = class { constructor(parts) { this.size = parts.reduce((n, p) => n + (p && p.size ? p.size : String(p).length), 0); this.type = 'audio/webm'; } };
const dictationCalls = [];
globalThis.__dictationResult = { ok: true, text: 'labas rytas', complete: true, disclosure: '' };
globalThis.__dictationFetch = async (url, opts) => {
  dictationCalls.push({ url: String(url), headers: Object.assign({}, opts && opts.headers) });
  const ok = globalThis.__dictationOk !== undefined ? globalThis.__dictationOk : true;
  const status = globalThis.__dictationStatus || 200;
  return { ok, status, json: async () => globalThis.__dictationResult };
};
globalThis.__dictationCalls = dictationCalls;
"""


def run_driver(driver: str, *, saved_preferences: dict | None = None, saved_profile: dict | None = None) -> dict:
    """Boot the real page script under node with the browser seams faked, then drive it."""
    import json

    program = (
        DOM
        + "\n;(async function(){\n"
        + PRELUDE
        + "\nObject.entries(" + json.dumps(saved_preferences or {}) + ").forEach(([k,v]) => localStorage.setItem(k,v));\n"
        + "\n;\n"
        + "\nconst originalFetch = globalThis.fetch; globalThis.fetch = (url, opts) => String(url) === '/api/settings/prefs' ? Promise.resolve({ok:true,json:async()=>(" + json.dumps(saved_profile or {}) + ")}) : originalFetch(url,opts);\n"
        + page_script().replace("fetch('/api/chat/dictation'", "__dictationFetch('/api/chat/dictation'")
        + "\n" + driver
        + "\n})();\n"
    )
    return run_node(program, timeout=120)


TICK = "const tick = () => new Promise((r) => process.nextTick(r));"


def test_second_click_stops_and_transcribes_into_a_draft() -> None:
    """THE baseline defect: the stop branch never ran; a recording could not be stopped."""
    out = run_driver(r"""
const res = { errors: [] };
try {
""" + TICK + r"""
  const btn = document.getElementById('dictateBtn');
  const input = document.getElementById('input');
  await btn.__on.click({});           // first click: start recording
  res.stateWhileRecording = { recording: btn.classList.contains('recording'), title: btn.title, pressed: btn.getAttribute('aria-pressed') };
  await btn.__on.click({});           // second click: STOP and transcribe
  for (let i = 0; i < 20; i++) await tick();
  res.recorderLog = { started: recorderLog.started, stopped: recorderLog.stopped };
  res.transcribingDuring = btn.classList.contains('transcribing');
  for (let i = 0; i < 20; i++) await tick();
  res.finalRecording = btn.classList.contains('recording');
  res.finalError = btn.classList.contains('error');
  res.draft = input.value;
  res.postCount = __dictationCalls.filter((c) => c.url.indexOf('/api/chat/dictation') === 0 && c.headers && c.headers['X-Vool-Session-Id']).length;
} catch (e) { res.errors.push(String(e && e.stack || e)); }
out(res);
""")
    assert not out["errors"], out["errors"]
    assert out["stateWhileRecording"]["recording"] is True
    assert "Recording" in out["stateWhileRecording"]["title"]
    assert out["stateWhileRecording"]["pressed"] == "true"
    assert out["recorderLog"]["stopped"] == 1, f"the second click must stop: {out['recorderLog']}"
    assert out["draft"] == "labas rytas", "the transcript lands as draft text in the composer"
    assert out["postCount"] == 1, "exactly one dictation POST for the take"
    assert out["finalRecording"] is False and out["finalError"] is False


def test_escape_cancels_with_no_upload() -> None:
    out = run_driver(r"""
const res = { errors: [] };
try {
""" + TICK + r"""
  const btn = document.getElementById('dictateBtn');
  await btn.__on.click({});
  res.started = recorderLog.started;
  (__docH.keydown || []).forEach((f) => f({ key: 'Escape', preventDefault() {} }));
  for (let i = 0; i < 10; i++) await tick();
  res.afterEsc = { recording: btn.classList.contains('recording'), recorderStopped: recorderLog.stopped, tracksStopped: micCalls.tracksStopped };
  res.uploadPosts = __dictationCalls.filter((c) => c.headers && c.headers['X-Vool-Session-Id']).length;
  res.draft = document.getElementById('input').value;
} catch (e) { res.errors.push(String(e && e.stack || e)); }
out(res);
""")
    assert not out["errors"], out["errors"]
    assert out["afterEsc"]["recording"] is False
    assert out["afterEsc"]["recorderStopped"] == 1
    assert out["afterEsc"]["tracksStopped"] >= 1, "the microphone track must be released"
    assert out["uploadPosts"] == 0, "a cancelled recording is never uploaded"
    assert out["draft"] == ""


def test_refused_microphone_shows_typed_reason_and_keyboard_stays_usable() -> None:
    out = run_driver(r"""
const res = { errors: [] };
try {
""" + TICK + r"""
  Object.defineProperty(navigator, 'mediaDevices', {
    value: { getUserMedia: async () => { const e = new Error('denied'); e.name = 'NotAllowedError'; throw e; } },
    configurable: true,
  });
  const btn = document.getElementById('dictateBtn');
  const note = document.getElementById('dictationNote');
  await btn.__on.click({});
  for (let i = 0; i < 10; i++) await tick();
  res.note = note.hidden ? '' : note.textContent;
  res.errorClass = btn.classList.contains('error');
  res.inputUsable = !!document.getElementById('input');
} catch (e) { res.errors.push(String(e && e.stack || e)); }
out(res);
""")
    assert not out["errors"], out["errors"]
    assert "permission denied" in out["note"], out["note"]
    assert "Type your message instead" in out["note"]
    assert out["errorClass"] is True
    assert out["inputUsable"] is True


def test_server_typed_refusal_renders_reason_and_remedy() -> None:
    out = run_driver(r"""
const res = { errors: [] };
try {
""" + TICK + r"""
  globalThis.__dictationOk = false; globalThis.__dictationStatus = 503;
  globalThis.__dictationResult = { ok: false, error: 'speech_recognizer_unauthorized',
    message: 'This machine has not granted Speech Recognition access.',
    remediation: 'Enable it in System Settings.' };
  const btn = document.getElementById('dictateBtn');
  const note = document.getElementById('dictationNote');
  await btn.__on.click({});
  for (let i = 0; i < 10; i++) await tick();
  await btn.__on.click({});
  for (let i = 0; i < 20; i++) await tick();
  res.note = note.hidden ? '' : note.textContent;
  res.errorClass = btn.classList.contains('error');
} catch (e) { res.errors.push(String(e && e.stack || e)); }
out(res);
""")
    assert not out["errors"], out["errors"]
    assert "not granted Speech Recognition access" in out["note"], out["note"]
    assert "System Settings" in out["note"], "the remediation must be shown"
    assert out["errorClass"] is True


def test_voice_mode_hold_to_talk_and_read_aloud_with_stop() -> None:
    out = run_driver(r"""
const res = { errors: [] };
try {
""" + TICK + r"""
  const mic = document.getElementById('dictateBtn');
  const toggle = document.getElementById('voiceModeBtn');
  const stopBtn = document.getElementById('voiceStop');
  toggle.__on.click({});
  res.toggled = { pressed: toggle.getAttribute('aria-pressed'), stored: localStorage.getItem('vool_voice_mode_v1') };
  res.titlePtt = mic.title;                       // hold-to-talk contract on the mic itself
  mic.__on.pointerdown({ preventDefault() {} });
  for (let i = 0; i < 10; i++) await tick();   // the start awaits getUserMedia
  res.pttRecording = mic.classList.contains('recording');
  mic.__on.pointerup({});
  for (let i = 0; i < 20; i++) await tick();
  res.draftAfterPtt = document.getElementById('input').value;
  // read-aloud of a completed answer, Stop, Esc and toggle-off all cancel
  const VV = window.VoolVoice;
  VV.readAloud('Atsakymas: viskas gerai.', 'lt-LT');
  res.readAloud = { spoken: speechSynthesisLog.spoken.slice(), stopVisible: !stopBtn.hidden };
  stopBtn.__on.click({});
  res.afterStop = { cancelled: speechSynthesisLog.cancelled, stopHidden: stopBtn.hidden };
  VV.readAloud('dar kartą', null);
  toggle.__on.click({});
  res.afterToggleOff = { cancelled: speechSynthesisLog.cancelled, pressed: toggle.getAttribute('aria-pressed') };
  // the synthetic click trailing a released hold must not start a second take (voice mode
  // re-enabled first: the suppression is a voice-mode interaction contract)
  toggle.__on.click({});
  res.reenabled = toggle.getAttribute('aria-pressed');
  const before = recorderLog.started;
  mic.__on.pointerdown({ preventDefault() {} });
  for (let i = 0; i < 10; i++) await tick();   // a real hold has duration
  mic.__on.pointerup({});
  mic.__on.click({});
  for (let i = 0; i < 20; i++) await tick();
  res.startsAroundClick = recorderLog.started - before;
  res.uploads = __dictationCalls.filter((c) => c.headers && c.headers['X-Vool-Session-Id']).length;
} catch (e) { res.errors.push(String(e && e.stack || e)); }
out(res);
""")
    assert not out["errors"], out["errors"]
    assert out["toggled"] == {"pressed": "true", "stored": "on"}
    assert "Hold to talk" in out["titlePtt"], out["titlePtt"]
    assert out["pttRecording"] is True, "holding the mic starts recording immediately"
    assert out["draftAfterPtt"] == "labas rytas", "release places the transcript as a draft"
    assert out["readAloud"]["spoken"] == [{"text": "Atsakymas: viskas gerai.", "lang": "lt-LT"}]
    assert out["readAloud"]["stopVisible"] is True
    assert out["afterStop"]["cancelled"] >= 1 and out["afterStop"]["stopHidden"] is True
    assert out["afterToggleOff"]["cancelled"] >= 2 and out["afterToggleOff"]["pressed"] == "false"
    assert out["startsAroundClick"] == 1, (
        f"pointer hold + trailing click must start exactly ONE take: {out['startsAroundClick']}"
    )
    assert out["uploads"] == 2, "two takes (PTT + suppression probe), each uploaded once"


def test_recognition_locale_is_persisted_and_sent() -> None:
    out = run_driver(r"""
const res = { errors: [] };
try {
""" + TICK + r"""
  const sel = document.getElementById('dictationLocale');
  sel.value = 'lt-LT';
  sel.__on.change({});
  res.stored = localStorage.getItem('vool_dictation_locale_v1');
  const mic = document.getElementById('dictateBtn');
  await mic.__on.click({});
  for (let i = 0; i < 10; i++) await tick();
  await mic.__on.click({});
  for (let i = 0; i < 20; i++) await tick();
  res.sentLocale = (__dictationCalls.find((c) => c.headers && c.headers['X-Vool-Session-Id']) || {}).headers
    ? __dictationCalls.filter((c) => c.headers && c.headers['X-Vool-Session-Id'])[0].headers['X-Vool-Dictation-Locale']
    : null;
} catch (e) { res.errors.push(String(e && e.stack || e)); }
out(res);
""")
    assert not out["errors"], out["errors"]
    assert out["stored"] == "lt-LT"
    assert out["sentLocale"] == "lt-LT", "the recognition locale rides the dictation request"


def test_composer_row_has_distinct_mic_and_voice_controls() -> None:
    """One microphone control for dictation, a distinct speaker control for voice mode."""
    assert 'id="dictateBtn"' in HTML and "Dictate into this message" in HTML
    mic_html = HTML.split('id="dictateBtn"')[1].split("</button>")[0]
    assert "Dictate</button>" not in HTML, "the old text button must be gone"
    assert "&#127897;" in mic_html, "the dictate control carries the standing microphone glyph"
    voice_html = HTML.split('id="voiceModeBtn"')[1].split("</button>")[0]
    assert "&#128266;" in voice_html, "the voice control carries a DISTINCT speaker glyph"
    assert "aria-pressed" in voice_html
    assert "Voice conversation" in voice_html or "voiceModeBtn" in HTML


def test_dismissed_speech_notice_stays_closed_after_late_probe_and_restart() -> None:
    out = run_driver(r"""
showDictationNote('Speech Recognition access has not been granted.');
const before = document.getElementById('dictationNote').hidden;
document.getElementById('dictationDismiss').click();
showDictationNote('A late availability probe also reports permission missing.');
out({before, hidden: document.getElementById('dictationNote').hidden,
     dismissed: localStorage.getItem('vool_dictation_notice_dismissed_v1')});
""")
    assert out['before'] is False
    assert out['hidden'] is True
    assert out['dismissed'] == '1'
    restarted = run_driver(r"""
showDictationNote('Speech Recognition access is still not granted.');
setDictationError('Permission denied. Type your message instead.');
out({hidden: document.getElementById('dictationNote').hidden,
     error: document.getElementById('dictateBtn').classList.contains('error'),
     title: document.getElementById('dictateBtn').title});
""", saved_preferences={'vool_dictation_notice_dismissed_v1': out['dismissed']})
    assert restarted['hidden'] is True
    assert restarted['error'] is True
    assert 'Permission denied' in restarted['title']


def test_native_restart_with_empty_browser_storage_reads_saved_profile() -> None:
    out = run_driver(TICK + r"""
for (let i=0; i<10; i++) await tick();
showDictationNote('Speech access not granted.');
out({hidden:document.getElementById('dictationNote').hidden});
""", saved_profile={'speech_notice_dismissed': True})
    assert out['hidden'] is True
