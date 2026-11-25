"""The picture of the hour, drawn by the page that shows it.

One print for each part of the day: land under the sun or the moon, VOOL's flag on it. It
is drawn as SVG from the clock and the date alone, by script the page already holds, so
nothing is fetched for it and it holds nothing of the person. Two pages show it: the
start page of the browser window (``home``) and the empty view of the Browser panel inside
VOOL (``live_page``). Both are given this same text, so the two can never drift apart.

The script expects one name from the page around it: ``DIRECTION`` ("ltr" or "rtl"), which
decides which way the face on the flag is turned. It gives back ``paint(svg, now)``,
``dress(style, look)``, ``PLATES`` and ``POINTS``.
"""
from __future__ import annotations

PICTURE_JS = """// The picture behind the page: a print for each part of the day, drawn here from the
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
// The words on the picture take the print's own paper and ink, so they can be read on it
// at any hour. `style` is the style of whatever holds them.
function dress(style, look) {
  style.colorScheme = look.dark ? 'dark' : 'light';
  style.setProperty('--paper', look.paper); style.setProperty('--ink', look.ink);
  style.setProperty('--quiet', look.dark ? 'rgba(238,232,213,.72)' : 'rgba(26,26,26,.68)');
  style.setProperty('--field', look.dark ? '#171615' : '#f3eedd');
  style.setProperty('--line', look.dark ? 'rgba(238,232,213,.6)' : 'rgba(26,26,26,.6)');
}
"""
