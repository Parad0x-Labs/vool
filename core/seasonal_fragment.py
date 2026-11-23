"""Seasonal touch: a small Christmas hat on the VOOL logo in the app header, shown only inside a
date window.

Presentation only. The window is read from this computer's own local date, so the hat appears on
1 December and is gone on 7 January without an update, a server call or a setting. A person who
turns it off keeps it off: the choice is stored on this computer (localStorage, the same place the
pet's preferences live), and every open VOOL window follows it at once through the storage event.

The owner's call (2026-11-23): the hat sits on the VOOL logo at the top left of the header, not
on the pets. The hat is one inline SVG placed over the logo's top-left corner; it takes no clicks
and is hidden from screen readers, so it can never cover or rename a control.
"""

from __future__ import annotations

SEASONAL_PREF_KEY = "vool_seasonal_v1"

# (month 1-12, day) inclusive bounds, local time. Dec 1 .. Jan 6 wraps the year end.
SEASON_START = (12, 1)
SEASON_END = (1, 6)

# A red hat leaning right, white fur band, white bobble on the tip. 32x28 units.
_HAT_SVG = (
    '<svg viewBox="0 0 32 28" width="30" height="26" aria-hidden="true" focusable="false">'
    '<path d="M3.5 21.5C5 12.5 11 4.2 19.6 3.1c4.6-.6 8 2.2 8.1 6.6-2.4-1.7-5.4-1.5-7.1.9'
    '-1.9 2.7-1.6 6.6.3 10.9z" fill="#c9302c"/>'
    '<path d="M19.6 3.1c4.6-.6 8 2.2 8.1 6.6-2.4-1.7-5.4-1.5-7.1.9-.9-3-1.2-5.4-1-7.5z" fill="#a52521"/>'
    '<rect x="1.2" y="19" width="23.4" height="7" rx="3.5" fill="#f4efe6"/>'
    '<rect x="1.2" y="23.4" width="23.4" height="2.6" rx="1.3" fill="#d9d1c3"/>'
    '<circle cx="27.4" cy="10.6" r="3.7" fill="#f4efe6"/>'
    "</svg>"
)

SEASONAL_CSS = r"""
header .brand{position:relative}
header .brand .vs-logo-hat{position:absolute;left:-10px;top:-14px;width:30px;height:26px;
  transform:rotate(-24deg);transform-origin:60% 80%;pointer-events:none;z-index:2;line-height:0;
  filter:drop-shadow(0 1px 1px rgba(0,0,0,.35))}
header .brand .vs-logo-hat[hidden]{display:none}
"""

SEASONAL_JS = r"""(function(){
'use strict';
if(window.VoolSeasonal)return;
const KEY='__KEY__',START=[__START__],END=[__END__],HAT='__HAT__';
function inSeason(date){const d=date instanceof Date&&!isNaN(date)?date:new Date(),m=d.getMonth()+1,day=d.getDate();
 const after=m>START[0]||(m===START[0]&&day>=START[1]),before=m<END[0]||(m===END[0]&&day<=END[1]);
 return START[0]>END[0]?(after||before):(after&&before);}
function enabled(){try{return localStorage.getItem(KEY)!=='off';}catch(_){return true;}}
function setEnabled(on){let saved=true;try{if(on)localStorage.removeItem(KEY);else localStorage.setItem(KEY,'off');}catch(_){saved=false;}
 window.dispatchEvent(new Event('vool-seasonal-changed'));return saved;}
function active(date){return enabled()&&inSeason(date);}
/* The hat on the header logo. A page with no header logo (Settings) only gets the switch API. */
function dressLogo(date){
 const brand=document.querySelector('header .brand');
 if(!brand)return false;
 let hat=brand.querySelector('.vs-logo-hat');
 const on=active(date);
 if(on&&!hat){hat=document.createElement('span');hat.className='vs-logo-hat';hat.setAttribute('aria-hidden','true');hat.innerHTML=HAT;brand.insertBefore(hat,brand.firstChild);}
 if(hat)hat.hidden=!on;
 return on;}
window.addEventListener('vool-seasonal-changed',function(){dressLogo();});
window.addEventListener('storage',function(e){if(e.key===KEY||e.key===null)dressLogo();});
/* A window left open across midnight on Nov 30 or Jan 6 follows the date when it is next looked
   at, and at least once an hour while it stays open. */
window.addEventListener('focus',function(){dressLogo();});
document.addEventListener('visibilitychange',function(){if(!document.hidden)dressLogo();});
setInterval(function(){dressLogo();},3600000);
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',function(){dressLogo();});else dressLogo();
window.VoolSeasonal={inSeason,enabled,setEnabled,active,dressLogo,window:{start:START.slice(),end:END.slice()}};
})();
""".replace("__KEY__", SEASONAL_PREF_KEY).replace(
    "__START__", "%d,%d" % SEASON_START
).replace("__END__", "%d,%d" % SEASON_END).replace("__HAT__", _HAT_SVG.replace("'", "\\'"))


def render_seasonal_fragment() -> str:
    """The header-logo hat as ``<style> + <script>``, appended to the chat page."""
    return "<style>" + SEASONAL_CSS + "</style><script>" + SEASONAL_JS + "</script>"
