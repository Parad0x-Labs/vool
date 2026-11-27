"""The one "Open Mac settings" button, for every permission step whose next move is in the Mac's System Settings.

A server answer that names such a step carries the pane beside its words as ``mac_settings_pane`` (one key of
core.mac_settings.PANES, set only on a Mac). Every surface that shows the words -- the Contacts import card,
a chat answer's action receipt, the dictation notice, the plugin folder notice -- asks
``VoolMacSettings.control(pane)`` for the button; none of them reads the words to decide. A press asks
POST /api/system/open-mac-settings for that pane and says, in plain words, what to do there or that it did
not work.
"""
from __future__ import annotations

_CSS = r"""
.vool-mac-settings { display:flex; flex-wrap:wrap; align-items:center; gap:8px; margin-top:6px; }
.vool-mac-settings-btn { font:inherit; font-size:12.5px; padding:4px 10px; border-radius:8px; border:1px solid var(--border, #8884);
  background:transparent; color:inherit; cursor:pointer; }
.vool-mac-settings-btn:hover { border-color:var(--accent, #6af); }
.vool-mac-settings-btn:disabled { opacity:.6; cursor:default; }
.vool-mac-settings-status { font-size:12.5px; color:var(--muted, inherit); }
.vool-mac-settings-status.bad { color:var(--bad, #d55); }
"""

_JS = r"""
(function(){
  'use strict';
  if (window.VoolMacSettings) return;
  function T(key, fallback){
    try { if (typeof VOOLT === 'function') { var t = VOOLT(key); if (t && t !== key) return t; } } catch (e) {}
    return fallback;
  }
  // What to do once the pane is open, per pane; a pane without its own sentence reads the general one.
  var OPENED = {
    contacts: ['mac_settings.opened.contacts', 'System Settings is open on Contacts. Turn VOOL on there, then try again.'],
    automation: ['mac_settings.opened.automation', 'System Settings is open on Automation. Turn on Notes under VOOL there, then try again.'],
    speech: ['mac_settings.opened.speech', 'System Settings is open on Speech Recognition. Turn VOOL on there, then try again.'],
    files: ['mac_settings.opened.files', 'System Settings is open on Files and Folders. Turn VOOL on there, then choose Rescan.']
  };
  function openedText(pane){
    var said = OPENED[pane];
    return said ? T(said[0], said[1]) : T('mac_settings.opened.other', 'System Settings is open. Turn VOOL on there, then try again.');
  }
  // One button and its status line for `pane`, or null when there is no pane. opts.say(text, bad) sends the
  // outcome to the surface's own status line instead; opts.label / opts.opened / opts.testid / opts.buttonClass
  // let a surface keep its own words and look.
  function control(pane, opts){
    pane = String(pane || '').trim();
    if (!pane) return null;
    opts = opts || {};
    var wrap = document.createElement('div'); wrap.className = 'vool-mac-settings';
    var btn = document.createElement('button'); btn.type = 'button';
    btn.className = opts.buttonClass || 'vool-mac-settings-btn';
    btn.textContent = opts.label || T('mac_settings.button', 'Open Mac settings');
    btn.setAttribute('data-testid', opts.testid || ('mac-settings-' + pane));
    btn.setAttribute('data-mac-settings-pane', pane);
    var status = document.createElement('span'); status.className = 'vool-mac-settings-status'; status.setAttribute('role', 'status');
    function say(text, bad){
      if (typeof opts.say === 'function') { opts.say(text, bad); return; }
      status.textContent = text || ''; status.className = 'vool-mac-settings-status' + (bad ? ' bad' : '');
    }
    btn.addEventListener('click', function(){
      btn.disabled = true;
      fetch('/api/system/open-mac-settings', {method: 'POST', headers: {'Content-Type': 'application/json', 'Accept': 'application/json'},
                                              body: JSON.stringify({pane: pane})})
        .then(function(r){ return r.json().catch(function(){ return {}; }); })
        .then(function(j){
          btn.disabled = false;
          if (j && j.ok) say(opts.opened || openedText(pane));
          else say(T('mac_settings.failed', 'That did not work.'), true);
        })
        .catch(function(){ btn.disabled = false; say(T('mac_settings.no_answer', 'VOOL did not answer; nothing was changed.'), true); });
    });
    wrap.appendChild(btn);
    if (typeof opts.say !== 'function') wrap.appendChild(status);
    return wrap;
  }
  window.VoolMacSettings = Object.freeze({control: control});
})();
"""


def render_mac_settings_fragment() -> str:
    """The shared "Open Mac settings" control, appended to the chat page and the Settings page."""
    return "<style>" + _CSS + "</style><script>" + _JS + "</script>"


__all__ = ["render_mac_settings_fragment"]
