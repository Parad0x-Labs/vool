#!/usr/bin/env node
/* Headless rasteriser for the VOOL companion art.
   Runs the real shared renderer against a recording 2D context and prints each pet as a
   palette-index grid, so frames can be inspected and hashed without a browser.
   Used by portable companion regression tests. No dependencies. */
const fs = require('fs');

function makeCtx(w, h) {
  const px = [];            // [x,y,hex]
  let fill = '#000';
  return {
    _px: px, w, h,
    /* A real canvas IGNORES an unparseable fillStyle and keeps the previous colour. The
       prototype relies on this (the golem asks for a palette slot it does not have), so the
       stub must behave the same way or the reference renders differently from the browser. */
    set fillStyle(c) { if (typeof c === 'string' && /^(#|rgba?\()/i.test(c)) fill = c; },
    get fillStyle() { return fill; },
    fillRect(x, y, ww, hh) {
      const c = fill;
      for (let j = 0; j < hh; j++) for (let i = 0; i < ww; i++) px.push([x + i, y + j, c]);
    },
    clearRect() { px.length = 0; },
  };
}

const sandbox = { window: {}, console };
sandbox.globalThis = sandbox;
sandbox.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} });
sandbox.document = {
  getElementById: () => ({ getContext: () => makeCtx(48, 48), addEventListener() {}, classList: { toggle() {} }, style: {} }),
  querySelector: () => null, addEventListener() {},
};

const js = fs.readFileSync(process.argv[2], 'utf8');
const fn = new Function('window', 'document', 'matchMedia', 'requestAnimationFrame', js);
fn(sandbox.window, sandbox.document, sandbox.matchMedia, function () {});

const W = sandbox.window.VoolCompanionWorld;
if (!W) { console.error('VoolCompanionWorld missing'); process.exit(2); }

/* Usage: render_pets.js <world.js> <states-csv> <out.json> [elapsedMs] [chars-csv] [activity]
   elapsedMs omitted => use the shared documented per-state sample times. */
const ids = process.argv[6] ? process.argv[6].split(',') : W.petIds();
const states = process.argv[3] ? process.argv[3].split(',') :
  ['idle', 'starting', 'thinking', 'tool', 'waiting', 'approval', 'retry', 'success', 'failure', 'cancelled', 'unknown'];
/* Documented deterministic sample times, shared with render_reference.js so the two are
   compared at the same instant. */
const TIMES = { idle: 0, starting: 600, thinking: 1400, tool: 600, waiting: 0,
  approval: 1000, retry: 800, success: 400, failure: 200, cancelled: 0, unknown: 0 };
const forced = process.argv[5] !== undefined && process.argv[5] !== '' ? Number(process.argv[5]) : null;
const activity = process.argv[7] || null;

const out = {};
for (const id of ids) {
  for (const st of states) {
    const ctx = makeCtx(48, 48);
    W.drawPet(ctx, id, st, activity, forced === null ? TIMES[st] : forced, { ground: false });
    /* Rasterise to a 48x48 colour grid. Later writes win, matching real canvas order. */
    const grid = Array.from({ length: 48 }, () => Array(48).fill(null));
    for (const [x, y, c] of ctx._px) {
      if (x >= 0 && x < 48 && y >= 0 && y < 48) grid[y][x] = c;
    }
    out[id + '/' + st] = grid;
  }
}
fs.writeFileSync(process.argv[4] || '/tmp/pets.json', JSON.stringify(out));
console.log('pets: ' + ids.length + ' [' + ids.join(', ') + ']');
console.log('states: ' + states.length);
