"""The start page of VOOL's browser window.

A new tab in the lasting window opens VOOL's own page instead of the engine's: a search
box and the person's most visited sites, in VOOL's look and language. It is a small
extension VOOL writes and loads itself, the way the engine lets a new tab page be replaced.

Rules kept here:

- it is VOOL's own part of the browser, not a plugin, and it is never offered for review;
- nothing here goes online. The search box hands the words to the engine, which uses the
  search the person chose in the browser's settings; an address typed there is opened as
  typed. The most visited sites and their icons come from the engine's own history;
- it asks for no page content and holds no page script, and it cannot see other tabs;
- it welcomes the person the first time it is ever shown and greets them the first time
  it is shown on a day. Which day it last greeted is kept in the page's own storage in
  the profile and goes nowhere;
- behind the words is a picture of the hour: a print of land under the sun or the moon,
  with VOOL's flag on it and one line about VOOL beneath. It is drawn on the page from
  the clock and the date. Nothing is fetched for it, and it holds nothing of the person;
- a place is kept on it for the top skills and plugins from VOOL's libraries. It is
  empty and not shown: nothing fills it yet;
- a private window keeps the engine's own page, which says that the window is private.
  An ordinary window the person opens from a private one gets this page.
"""
from __future__ import annotations

import contextlib
import json
import shutil
from pathlib import Path
from typing import Any

from core.runtime_paths import active_data_dir

#: Raised whenever the files below change, so an older copy is never loaded.
FORMAT = 4
PAGE = "home.html"

#: The folder the page is loaded from. It is always the same one: the engine names what
#: it loads by the folder it came from, and it remembers by that name what the person
#: answered when it asked whether this page may stand in for its own new tab page.
FOLDER = "page"

_WORDS = ("home_name", "home_title", "home_search", "home_visited",
          "home_welcome", "home_welcome_note", "home_morning", "home_afternoon", "home_evening",
          "home_point_memory", "home_point_cost", "home_point_local", "home_point_account",
          "home_point_ask", "home_point_forget")

_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title></title><link rel="stylesheet" href="home.css"></head>
<body><svg id="sky" aria-hidden="true"></svg><main>
<h1>VOOL</h1>
<p id="hello" hidden><strong id="helloTitle"></strong><span id="helloNote"></span></p>
<form id="search" role="search" autocomplete="off"><input id="q" type="text" autofocus spellcheck="false"></form>
<section id="visited" hidden><h2 id="visitedTitle"></h2><div id="sites"></div></section>
<section id="picks" hidden></section>
</main><p id="point"></p><script src="home.js"></script></body></html>
"""

_CSS = """:root{color-scheme:light;--paper:#eee8d5;--ink:#1a1a1a;--quiet:rgba(26,26,26,.68);--line:rgba(26,26,26,.6);--field:#f3eedd}
*{box-sizing:border-box}
body{margin:0;min-height:100vh;display:flex;flex-direction:column;background:var(--paper);color:var(--ink);font:16px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;overflow-x:hidden}
#sky{position:fixed;inset:0;width:100%;height:100%;z-index:0}
[dir=rtl] #sky{transform:scaleX(-1)}
main{position:relative;z-index:1;width:min(38rem,100% - 3rem);margin:0;margin-inline-start:max(1.5rem,7vw);padding:12vh 0 2rem;text-align:start}
h1{font-size:2.6rem;letter-spacing:.42em;font-weight:600;margin:0 0 1.4rem}
#q{width:100%;font:inherit;font-size:1.05rem;padding:.9rem 1.15rem;border-radius:.2rem;border:1.5px solid var(--line);background:var(--field);color:var(--ink);box-shadow:5px 5px 0 var(--line)}
#q:focus-visible{outline:2px solid var(--ink);outline-offset:3px}
#q::placeholder{color:var(--quiet)}
#hello{margin:-.4rem 0 1.5rem;color:var(--quiet)}
#hello strong{display:block;color:var(--ink);font:italic 500 1.55rem/1.3 "Iowan Old Style","Palatino Linotype",Palatino,Georgia,serif;margin-bottom:.1rem}
h2{font-size:.85rem;font-weight:600;letter-spacing:.06em;color:var(--quiet);margin:2.2rem 0 .8rem}
#sites{display:flex;flex-wrap:wrap;gap:.55rem}
#sites a{display:grid;grid-template-columns:auto minmax(0,1fr);column-gap:.6rem;align-items:center;padding:.4rem .8rem;border:1.5px solid var(--line);border-radius:.2rem;background:var(--field);color:var(--ink);text-decoration:none;max-width:12rem}
#sites a:hover{box-shadow:3px 3px 0 var(--line)}
#sites a:focus-visible{outline:2px solid var(--ink);outline-offset:3px}
#sites img{grid-row:span 2;width:20px;height:20px;border-radius:3px}
#sites span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:.88rem;line-height:1.25}
#sites small{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--quiet);font-size:.72rem;line-height:1.25}
#point{position:relative;z-index:1;margin:auto 0 0;padding:1rem 1.5rem 2rem;padding-inline-start:max(1.5rem,7vw);max-width:38rem;color:#eee8d5;font:italic 500 1.9rem/1.2 "Iowan Old Style","Palatino Linotype",Palatino,Georgia,serif;text-shadow:0 1px 2px rgba(0,0,0,.6)}
#point::before{content:"";display:block;width:3.2rem;border-top:2px solid #eee8d5;margin-bottom:.7rem}
@media (max-width:56rem),(max-height:36rem){main{margin:1.5rem;margin-inline-start:max(1.5rem,7vw);padding:1.5rem;background:var(--paper);border:1.5px solid var(--line);box-shadow:5px 5px 0 var(--line)}h1{font-size:2rem;margin-bottom:1.1rem}}
@media (max-width:56rem){main{margin-inline:auto}#point{align-self:center;padding-inline:1.5rem;text-align:center}#point::before{margin-inline:auto}}
"""

_JS = """const WORDS = __WORDS__;
const DIRECTION = __DIRECTION__;
const $ = (id) => document.getElementById(id);
document.documentElement.lang = WORDS.lang; document.documentElement.dir = DIRECTION;
document.title = WORDS.home_title;
$('q').placeholder = WORDS.home_search; $('q').setAttribute('aria-label', WORDS.home_search);
$('visitedTitle').textContent = WORDS.home_visited;
// The box is ready to type in the moment the page shows. "autofocus" alone waits for the
// engine's next paint, which a busy or covered window can put off: CI on 94b9dad5d
// (2026-11-24) read the page with nothing focused.
$('q').focus();
// A welcome the first time this page is ever shown in this profile, and a greeting the
// first time it is shown on a day, with that day's date. The day last greeted is kept in
// this page's own storage in the profile; nothing about it is sent anywhere.
function greeting(hour) { return hour >= 5 && hour < 12 ? 'home_morning' : hour >= 12 && hour < 18 ? 'home_afternoon' : 'home_evening'; }
function hello(now) {
  const day = [now.getFullYear(), now.getMonth() + 1, now.getDate()].join('-');
  let last = null;
  try { last = JSON.parse(localStorage.getItem('hello') || 'null'); } catch (e) {}
  if (last && last.day === day) return;
  $('helloTitle').textContent = last ? WORDS[greeting(now.getHours())] : WORDS.home_welcome;
  $('helloNote').textContent = last ? now.toLocaleDateString(WORDS.lang, {weekday: 'long', day: 'numeric', month: 'long'}) : WORDS.home_welcome_note;
  $('hello').hidden = false;
  try { localStorage.setItem('hello', JSON.stringify({day})); } catch (e) {}
}
hello(new Date());
// An address is told from words to search for by its shape: it has no spaces, and it is
// a full web address, a name whose last part after a dot is made of letters, or a number.
function address(text) {
  if (/\\s/.test(text)) return '';
  try {
    const whole = new URL(text);
    if (whole.protocol === 'http:' || whole.protocol === 'https:') return whole.href;
  } catch (e) {}
  // This machine, or a machine named by its number: such places are usually plain ones.
  if (/^(localhost|\\d{1,3}(\\.\\d{1,3}){3})(:\\d+)?([/?#].*)?$/i.test(text)) {
    try { return new URL('http://' + text).href; } catch (e) {}
  }
  if (/^[^/?#@:]+\\.\\p{L}{2,}(:\\d+)?([/?#].*)?$/u.test(text)) {
    try { return new URL('https://' + text).href; } catch (e) {}
  }
  return '';
}
$('search').addEventListener('submit', (event) => {
  event.preventDefault();
  const text = $('q').value.trim();
  if (!text) return;
  const to = address(text);
  // Words go to the engine, which searches with the search the person chose in its settings.
  if (to) location.assign(to); else chrome.search.query({text, disposition: 'CURRENT_TAB'});
});
// The sites the person went to most in the last three months, read from the engine's
// history each time this page opens. The engine also keeps a ready-made list of top
// sites, but it brings that one up to date on a timer of its own: a site visited before
// the last quit could be missing from it after a start. One entry per site, the site
// with the most visits first. Its link is the page of that site the person opened most;
// a page address with a question part is never the one linked, so no search words show.
chrome.history.search({text: '', startTime: Date.now() - 90 * 864e5, maxResults: 2000}, (pages) => {
  const sites = new Map();
  for (const page of pages || []) {
    let at;
    try { at = new URL(page.url); } catch (e) { continue; }
    if (at.protocol !== 'http:' && at.protocol !== 'https:') continue;
    const site = sites.get(at.host) || {visits: 0, last: 0, url: at.origin + '/', title: '', most: -1};
    const visits = page.visitCount || 0;
    site.visits += visits; site.last = Math.max(site.last, page.lastVisitTime || 0);
    if (!at.search && visits > site.most) { site.most = visits; site.url = page.url; site.title = page.title || ''; }
    sites.set(at.host, site);
  }
  const shown = [...sites.values()].sort((a, b) => b.visits - a.visits || b.last - a.last).slice(0, 8);
  for (const site of shown) {
    const link = document.createElement('a'), icon = new Image(), name = document.createElement('span'), host = document.createElement('small');
    link.href = site.url;
    // The icon is the one the engine already holds for the page; nothing is fetched for it.
    icon.src = chrome.runtime.getURL('_favicon/') + '?pageUrl=' + encodeURIComponent(site.url) + '&size=32';
    icon.alt = '';
    name.textContent = site.title || new URL(site.url).hostname; host.textContent = new URL(site.url).hostname;
    link.append(icon, name, host);
    $('sites').append(link);
  }
  $('visited').hidden = !shown.length;
});
// The picture behind the page: a print for each part of the day, drawn here from the
// clock and the date alone. Nothing is fetched for it and nothing about it is kept. The
// land is new each day and the same all that day; the sun and the moon are where the
// hour puts them, and the moon is as full as it is that night.
const BONE = '#eee8d5', INK = '#1a1a1a';
const PLATES = [
  {name: 'night', from: 0, paper: '#121110', ink: BONE, accent: BONE, mass: '#1d1b19', dark: true, stars: 1},
  {name: 'first_light', from: 4.75, paper: '#26283a', ink: BONE, accent: '#e2a57c', mass: '#16171f', dark: true, stars: .5},
  {name: 'dawn', from: 6, paper: '#ecd6bd', ink: INK, accent: '#c8553d', mass: INK, dark: false, stars: 0},
  {name: 'day', from: 8.5, paper: BONE, ink: INK, accent: INK, mass: INK, dark: false, stars: 0},
  {name: 'evening', from: 17, paper: '#e2bd95', ink: INK, accent: '#b5452f', mass: INK, dark: false, stars: 0},
  {name: 'last_light', from: 19.75, paper: '#35262f', ink: BONE, accent: '#d9825a', mass: '#17121a', dark: true, stars: .5},
  {name: 'night', from: 21, paper: '#121110', ink: BONE, accent: BONE, mass: '#1d1b19', dark: true, stars: 1},
];
// One line about VOOL under the picture, another for each part of the day.
const POINTS = ['home_point_memory', 'home_point_cost', 'home_point_local', 'home_point_account', 'home_point_ask', 'home_point_forget'];
const SUN_UP = 6, SUN_DOWN = 19.75, W = 1600, H = 900, HORIZON = 690;
function dice(seed) { let a = seed >>> 0; return () => { a = (a + 0x6D2B79F5) >>> 0; let t = Math.imul(a ^ (a >>> 15), 1 | a); t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t; return ((t ^ (t >>> 14)) >>> 0) / 4294967296; }; }
function el(name, attrs, parent) { const node = document.createElementNS('http://www.w3.org/2000/svg', name); for (const key in attrs) node.setAttribute(key, attrs[key]); if (parent) parent.append(node); return node; }
// A line of hills. The far ones keep lower on the side where the words stand and rise toward the flag.
function ridge(roll, base, height, low) {
  const waves = [0, 1, 2, 3].map((k) => ({a: height / (1.6 + k * 1.3), f: (1.1 + k * 1.7 + roll()) * Math.PI * 2 / W, p: roll() * 7}));
  const rise = (x) => { const t = Math.min(1, Math.max(0, x / (W * .62))); return low + (1 - low) * t * t * (3 - 2 * t); };
  const at = (x) => base - rise(x) * waves.reduce((sum, w) => sum + w.a * (0.5 + 0.5 * Math.sin(x * w.f + w.p)), 0);
  let path = `M-40,${H + 60} L-40,${at(0).toFixed(1)}`;
  for (let x = 16; x <= W; x += 16) path += ` L${x},${at(x).toFixed(1)}`;
  return {path: path + ` L${W + 40},${at(W).toFixed(1)} L${W + 40},${H + 60} Z`, at};
}
// How far the moon is through its month on that day: 0 new, .5 full.
function moonAge(now) { const days = (now - Date.UTC(2000, 0, 6, 18, 14)) / 864e5; return ((days % 29.530588) + 29.530588) % 29.530588 / 29.530588; }
function paint(svg, now) {
  const hour = now.getHours() + now.getMinutes() / 60, at = PLATES.findLastIndex((one) => hour >= one.from), look = PLATES[at];
  const date = now.getFullYear() * 10000 + (now.getMonth() + 1) * 100 + now.getDate();
  // The land and the stars each have their own run of chance, so the land is the same all day.
  const roll = dice(date), spark = dice(date + 7919);
  svg.replaceChildren();
  svg.setAttribute('viewBox', `0 0 ${W} ${H}`); svg.setAttribute('preserveAspectRatio', 'xMaxYMax slice');
  const defs = el('defs', {}, svg);
  // Lines wobble a little, as if cut by hand, and the paper has a grain.
  const hand = el('filter', {id: 'hand', x: '-5%', y: '-5%', width: '110%', height: '110%'}, defs);
  el('feTurbulence', {type: 'fractalNoise', baseFrequency: '.018', numOctaves: '2', seed: '7', result: 'n'}, hand);
  el('feDisplacementMap', {in: 'SourceGraphic', in2: 'n', scale: '7'}, hand);
  const grain = el('filter', {id: 'grain', x: '0', y: '0', width: '100%', height: '100%'}, defs);
  el('feTurbulence', {type: 'fractalNoise', baseFrequency: '.85', numOctaves: '2', seed: '3'}, grain);
  el('feColorMatrix', {type: 'matrix', values: `0 0 0 0 ${look.dark ? .93 : .1}  0 0 0 0 ${look.dark ? .91 : .1}  0 0 0 0 ${look.dark ? .84 : .1}  0 0 0 ${look.dark ? .5 : .62} -.2`}, grain);
  for (const [id, gap, width] of [['far', 9, 1.1], ['mid', 5, 1.5]]) {
    const hatch = el('pattern', {id, width: gap, height: gap, patternUnits: 'userSpaceOnUse', patternTransform: 'rotate(38)'}, defs);
    el('rect', {width: gap, height: gap, fill: look.paper}, hatch);
    el('line', {x1: 0, y1: 0, x2: 0, y2: gap, stroke: look.ink, 'stroke-width': width}, hatch);
  }
  const edge = el('radialGradient', {id: 'edge', cx: '50%', cy: '42%', r: '75%'}, defs);
  el('stop', {offset: '.55', 'stop-color': '#000', 'stop-opacity': '0'}, edge);
  el('stop', {offset: '1', 'stop-color': '#000', 'stop-opacity': look.dark ? '.5' : '.22'}, edge);
  el('rect', {width: W, height: H, fill: look.paper}, svg);
  // First and last light: the sky warms along the land before the sun is seen and after it has gone.
  if (look.dark && look.accent !== look.ink) {
    const glow = el('linearGradient', {id: 'glow', x1: 0, y1: 0, x2: 0, y2: 1}, defs);
    el('stop', {offset: '0', 'stop-color': look.accent, 'stop-opacity': '0'}, glow);
    el('stop', {offset: '1', 'stop-color': look.accent, 'stop-opacity': '.6'}, glow);
    el('rect', {y: HORIZON - 330, width: W, height: 420, fill: 'url(#glow)'}, svg);
  }
  const drawn = el('g', {filter: 'url(#hand)'}, svg);
  // Stars: the same sky all night, another one the next day.
  if (look.stars) {
    const sky = el('g', {id: 'stars', fill: look.ink, stroke: look.ink, opacity: look.stars}, drawn);
    for (let i = 0; i < 110; i++) {
      // The words stand on the near side of the sky: stars keep clear of them, save a few low ones.
      const side = spark(), up = spark(), r = .8 + spark() * spark() * 2.6;
      const x = side < .82 ? W * (.53 + side / .82 * .47) : W * (side - .82) / .18 * .53, y = side < .82 ? up * HORIZON * .9 : HORIZON * (.74 + up * .16);
      if (r > 2.5) el('path', {d: `M${x - r * 3},${y} H${x + r * 3} M${x},${y - r * 3} V${y + r * 3}`, 'stroke-width': 1.2, fill: 'none'}, sky);
      else el('circle', {cx: x, cy: y, r, stroke: 'none'}, sky);
    }
  }
  // The sun by day, the moon by night, each on its way across. They keep to the side away
  // from the words: up from behind the land in the middle, down beside the flag.
  const day = hour >= SUN_UP && hour < SUN_DOWN;
  const way = day ? (hour - SUN_UP) / (SUN_DOWN - SUN_UP) : ((hour - SUN_DOWN + 24) % 24) / (24 - SUN_DOWN + SUN_UP);
  const cx = W * (.5 + way * .47), cy = HORIZON + 40 - Math.sin(Math.PI * way) * 560, r = 74;
  if (day) {
    const solid = look.accent !== look.ink;
    const sun = el('g', {id: 'sun', stroke: look.accent, 'stroke-width': 3, 'stroke-linecap': 'round', fill: 'none'}, drawn);
    el('circle', {cx, cy, r, fill: solid ? look.accent : look.paper}, sun);
    if (!solid) el('circle', {cx, cy, r: r - 13, 'stroke-width': 1.6}, sun);
    for (let i = 0; i < 36; i++) {
      const turn = i / 36 * Math.PI * 2, from = r + 16, to = r + (i % 3 ? 34 : 62);
      el('line', {x1: cx + Math.cos(turn) * from, y1: cy + Math.sin(turn) * from, x2: cx + Math.cos(turn) * to, y2: cy + Math.sin(turn) * to, 'stroke-width': i % 3 ? 1.8 : 3}, sun);
    }
  } else {
    const age = moonAge(now), lit = Math.cos(age * 2 * Math.PI), growing = age < .5, m = 58;
    // The lit part: half a disc on one side, closed by an arc that runs from thin to full.
    const side = growing ? 1 : 0, bulge = Math.abs(lit) * m, back = (lit > 0) === growing ? 0 : 1;
    const moon = el('g', {id: 'moon', 'data-age': age.toFixed(3)}, drawn);
    el('circle', {cx, cy, r: m, fill: look.mass, stroke: look.ink, 'stroke-width': 1.4, 'stroke-opacity': .5}, moon);
    el('path', {d: `M${cx},${cy - m} A${m},${m} 0 0 ${side} ${cx},${cy + m} A${bulge.toFixed(1)},${m} 0 0 ${back} ${cx},${cy - m} Z`, fill: BONE}, moon);
  }
  // Three ridges, a new line of them each day; the nearest carries the flag.
  const far = ridge(roll, HORIZON + 10, 170, .55), mid = ridge(roll, HORIZON + 80, 140, .55), near = ridge(roll, H - 150, 100, 1);
  el('path', {d: far.path, fill: 'url(#far)', stroke: look.ink, 'stroke-width': 2}, drawn);
  el('path', {d: mid.path, fill: 'url(#mid)', stroke: look.ink, 'stroke-width': 2.4}, drawn);
  const fx = 1050 + roll() * 100, fy = near.at(fx) + 6, cloth = look.dark ? '#0c0b0b' : INK;
  const flag = el('g', {id: 'flag', transform: `translate(${fx.toFixed(1)},${(fy - 300).toFixed(1)}) rotate(-4 0 300)`, 'stroke-linecap': 'round', 'stroke-linejoin': 'round'}, drawn);
  el('path', {d: 'M0,-14 V304', stroke: look.dark ? BONE : INK, 'stroke-width': 9, fill: 'none'}, flag);
  el('path', {d: 'M-7,26 H9 M-7,214 H9', stroke: look.dark ? BONE : INK, 'stroke-width': 5, fill: 'none'}, flag);
  el('path', {d: 'M5,4 C64,-22 122,22 184,-4 S236,-6 246,8 L231,26 L248,42 L226,58 L243,78 L222,92 L240,112 L224,130 L236,148 L214,156 C172,176 112,128 62,150 S16,160 5,146 Z', fill: cloth, stroke: BONE, 'stroke-width': look.dark ? 2.4 : 0}, flag);
  // Where the page reads from the right the whole picture is turned over, and the face is turned back.
  const face = el('g', {id: 'face', stroke: BONE, 'stroke-width': 7, fill: 'none', transform: DIRECTION === 'rtl' ? 'translate(214,0) scale(-1,1)' : ''}, flag);
  el('path', {d: 'M66,38 L104,62 L142,28'}, face);
  el('circle', {cx: 86, cy: 88, r: 18}, face); el('circle', {cx: 142, cy: 80, r: 11, 'stroke-width': 6}, face);
  el('circle', {cx: 90, cy: 90, r: 5, fill: BONE, stroke: 'none'}, face); el('circle', {cx: 144, cy: 81, r: 3.4, fill: BONE, stroke: 'none'}, face);
  el('path', {d: 'M88,124 L152,114 L154,100'}, face);
  el('path', {id: 'land', d: near.path, fill: look.mass, stroke: look.dark ? look.ink : 'none', 'stroke-width': 2.4}, drawn);
  el('rect', {width: W, height: H, fill: 'url(#edge)'}, svg);
  el('rect', {width: W, height: H, filter: 'url(#grain)', opacity: look.dark ? .16 : .3}, svg);
  return {look, at};
}
// The page takes the print's own paper and ink, so the words can be read on it at any hour.
function scene(now) {
  const {look, at} = paint($('sky'), now), root = document.documentElement;
  root.dataset.sky = look.name; root.style.colorScheme = look.dark ? 'dark' : 'light';
  root.style.setProperty('--paper', look.paper); root.style.setProperty('--ink', look.ink);
  root.style.setProperty('--quiet', look.dark ? 'rgba(238,232,213,.72)' : 'rgba(26,26,26,.68)');
  root.style.setProperty('--field', look.dark ? '#171615' : '#f3eedd');
  root.style.setProperty('--line', look.dark ? 'rgba(238,232,213,.6)' : 'rgba(26,26,26,.6)');
  $('point').textContent = WORDS[POINTS[(now.getDate() + at) % POINTS.length]];
}
scene(new Date());
// A page left open keeps up with the day.
setInterval(() => scene(new Date()), 5 * 60 * 1000);
"""

_RTL = frozenset({"ar", "he", "fa", "ur"})


def _root() -> Path:
    return active_data_dir() / "browser" / "desk" / "home"


def _files(locale: str) -> dict[str, str]:
    from core.i18n import catalog_for

    words_of = catalog_for(locale)
    words = {key: words_of.text("live." + key) for key in _WORDS}
    words["lang"] = locale
    direction = "rtl" if locale.split("-")[0].lower() in _RTL else "ltr"
    manifest = {
        "manifest_version": 3, "name": words["home_name"], "version": f"{FORMAT}.0",
        "chrome_url_overrides": {"newtab": PAGE},
        # The engine's own history, its search, and the icons it already holds. No access
        # to any site, no page script, no background part.
        "permissions": ["history", "search", "favicon"],
    }
    script = lambda value: json.dumps(value, ensure_ascii=False).replace("<", "\\u003c")  # noqa: E731
    return {
        "manifest.json": json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        PAGE: _HTML,
        "home.css": _CSS,
        "home.js": _JS.replace("__WORDS__", script(words)).replace("__DIRECTION__", script(direction)),
    }


def _as_written(folder: Path, files: dict[str, str]) -> bool:
    """True when *folder* holds exactly *files* and nothing else."""
    try:
        if sorted(path.name for path in folder.iterdir()) != sorted(files):
            return False
        return all((folder / name).is_file() and (folder / name).read_text(encoding="utf-8") == text for name, text in files.items())
    except (OSError, ValueError):
        return False


def build(locale: str = "") -> Path:
    """Write the start page in the language VOOL was last seen in and return its folder.

    It is checked against what VOOL writes at every start and written afresh when it
    differs in any way, so what is loaded is what VOOL wrote, even if the folder was
    changed while the browser was closed. A folder that is as written is left alone: an
    open window may be reading it.
    """
    from . import guard

    base = _root()
    files = _files(locale or guard.language())
    target = base / FOLDER
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not _as_written(target, files):
        staging = base / (FOLDER + ".new")
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(mode=0o700)
        for filename, text in files.items():
            path = staging / filename
            path.write_text(text, encoding="utf-8")
            path.chmod(0o600)
        shutil.rmtree(target, ignore_errors=True)
        staging.rename(target)
    for path in base.iterdir():
        if path.name != target.name:
            shutil.rmtree(path, ignore_errors=True)
    return target


async def apply(window: Any, folder: Path | None) -> bool:
    """Make VOOL's page, written to *folder*, the new tab page of *window*. Runs on the
    desk loop, once, at its start.

    True when the engine took it. A start page that will not load does not keep the browser
    from opening: new tabs then show the engine's own page.
    """
    if folder is None or window.context is None or window.context.browser is None:
        return False
    session = await window.context.browser.new_browser_cdp_session()
    try:
        answer = await session.send("Extensions.loadUnpacked", {"path": str(folder)})
        engine_id = str(answer.get("id") or "")
    except Exception:
        return False
    finally:
        with contextlib.suppress(Exception):
            await session.detach()
    if not engine_id:
        return False
    window.home_id = engine_id
    return True


def address(window: Any) -> str:
    """The address VOOL's start page has in *window*, or "" when it is not loaded there."""
    engine_id = str(getattr(window, "home_id", "") or "")
    return f"chrome-extension://{engine_id}/{PAGE}" if engine_id else ""


__all__ = ["PAGE", "address", "apply", "build"]
