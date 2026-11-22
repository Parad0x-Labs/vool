"""Motion for the painted pets: each painting on its own bones, posed and moved without a jump.

The owner asked (2026-11-22) for real motion with continuity: a pet never stands in one frame and
lies in the next; it sits down, lies down and gets up again. ``RIG_ENGINE_JS`` is the shared rig
(posture chain, cross-faded moves, one running breath, eased lights); ``RIG_PETS_JS`` gives each of
the ten its own postures and a move for every state and every kind of work. The parts are in
``core/companion_pet_rig_art.py``. Drawn by ``core/companion_painted_fragment.py``.
"""

from __future__ import annotations

RIG_ENGINE_JS = r"""
/* Painted pet rig: a painting cut into parts on bones, posed and moved without a single jump.

   Continuity rule: what is drawn next always starts from what was drawn last. A posture change
   (stand, sit, lie) walks the posture chain one step at a time (lie -> sit -> stand), each step a
   timed blend from the pose actually on screen. A work move (tool, thinking, success...) is an
   overlay whose weight fades in and out, so switching moves cross-fades instead of snapping.
   Breathing (and wing beats, flame flicker...) runs on one accumulated phase whose rhythm eases
   between postures, and every light eases toward its level. Everything is a function of the
   clock the host passes (`now`, ms); no randomness. */
(function (root) {
  "use strict";
  const CHAIN = ["stand", "sit", "lie"];
  const KEYS = ["rot", "dx", "dy", "sx", "sy", "a"];
  const TAU = Math.PI * 2;
  const ease = (t) => (t <= 0 ? 0 : t >= 1 ? 1 : t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);
  // A heavy body settles: a small overshoot at the end of a posture change.
  const settle = (t) => { const e = ease(t); return e + Math.sin(Math.PI * Math.min(1, t)) * 0.06 * (t > 0.6 ? 1 : 0) * (1 - t) * 4; };
  // Helpers the creatures share.
  const H = {
    TAU, ease,
    wave: (t, ms, ph) => Math.sin(TAU * (t / ms + (ph || 0))),
    beat: (t, ms) => { const p = (t % ms) / ms; return Math.sin(Math.PI * p); },        // 0..1..0, repeating
    once: (t, ms) => (t >= ms ? 0 : Math.sin(Math.PI * t / ms)),                         // 0..1..0, once
    ramp: (t, ms) => ease(Math.min(1, Math.max(0, t / ms))),                              // 0..1, then holds
    // 0..1..0 for `on` ms at the start of every `every` ms: a nod, a peck, a blink.
    pulse: (t, every, on) => { const p = t % every; return p < on ? Math.sin(Math.PI * p / on) : 0; },
  };
  function zeroPose(rig) {
    const p = { root: { x: 0, y: 0, rot: 0 }, parts: {} };
    for (const n in rig.parts) p.parts[n] = { rot: 0, dx: 0, dy: 0, sx: 1, sy: 1, a: 1 };
    return p;
  }
  function copyPose(a) { return JSON.parse(JSON.stringify(a)); }
  function fullPose(rig, partial) {
    const p = zeroPose(rig);
    if (!partial) return p;
    if (partial.root) Object.assign(p.root, partial.root);
    for (const n in partial.parts || {}) if (p.parts[n]) Object.assign(p.parts[n], partial.parts[n]);
    return p;
  }
  function mix(a, b, t) {
    const o = { root: {}, parts: {} };
    for (const k of ["x", "y", "rot"]) o.root[k] = a.root[k] + (b.root[k] - a.root[k]) * t;
    for (const n in a.parts) {
      o.parts[n] = {}; for (const k of KEYS) o.parts[n][k] = a.parts[n][k] + (b.parts[n][k] - a.parts[n][k]) * t;
      // Drawing order is not blended: a part comes in front (or goes behind) halfway through.
      const z = t < 0.5 ? a.parts[n].z : b.parts[n].z; if (z != null) o.parts[n].z = z;
    }
    return o;
  }
  // Add an overlay (deltas: rot/dx/dy added, sx/sy/a multiplied) with weight w.
  function addOverlay(base, d, w) {
    if (!d || !w) return base;
    if (d.root) for (const k of ["x", "y", "rot"]) if (d.root[k]) base.root[k] += d.root[k] * w;
    for (const n in d.parts || {}) {
      const q = base.parts[n], e = d.parts[n]; if (!q) continue;
      if (e.rot) q.rot += e.rot * w; if (e.dx) q.dx += e.dx * w; if (e.dy) q.dy += e.dy * w;
      if (e.sx) q.sx *= 1 + (e.sx - 1) * w; if (e.sy) q.sy *= 1 + (e.sy - 1) * w;
      if (e.a != null) q.a *= 1 + (e.a - 1) * w;
      if (e.z != null && w > 0.5) q.z = e.z;
    }
    return base;
  }
  // Which posture a state wants. Resting sits after a while and lies down after longer; any work
  // stands up first.
  function wantedPosture(rig, state, elapsed) {
    const r = rig.rest || {};
    if (state === "idle" || state === "unknown") {
      if (elapsed >= (r.lieAfter || 90000)) return "lie";
      if (elapsed >= (r.sitAfter || 25000)) return "sit";
      return "stand";
    }
    if (state === "waiting") return elapsed >= (r.sitAfter || 25000) ? "sit" : "stand";
    return "stand";
  }
  // ---- tracker: one per drawn pet (canvas) -------------------------------------------------
  // The first frame starts already in the posture and move the state wants (a picker preview or a
  // pet that appears mid-task is drawn as it should look, not mid-blend from nothing).
  function newTracker(rig, now, state, activity, elapsed) {
    const posture = state ? wantedPosture(rig, state, elapsed || 0) : "stand";
    const overlays = state ? [{ key: state + "|" + (activity || ""), state, activity, t0: now - Math.max(0, elapsed || 0), out: null, full: true }] : [];
    // `bare`: the posture pose last drawn, before breath and moves. Posture steps start from it,
    // while breath and moves keep being added on top, so neither is counted twice.
    return { posture, target: posture, bare: copyPose(fullPose(rig, rig.postures[posture])), step: null, overlays,
      last: now, breathPhase: 0, levels: {} };
  }
  function advance(rig, tr, state, activity, elapsed, now, reduced) {
    const want = wantedPosture(rig, state, elapsed);
    tr.target = want;
    if (reduced) { tr.posture = want; tr.step = null; }
    // Posture: one chain step at a time, each from the pose on screen.
    if (tr.step && now - tr.step.t0 >= tr.step.dur) { tr.posture = tr.step.to; tr.step = null; }
    if (!tr.step && tr.posture !== tr.target) {
      const i = CHAIN.indexOf(tr.posture), j = CHAIN.indexOf(tr.target);
      const next = CHAIN[i + (j > i ? 1 : -1)];
      const dur = (rig.durations && rig.durations[tr.posture + ">" + next]) || 900;
      tr.step = { from: copyPose(tr.bare), to: next, t0: now, dur };
    }
    // Overlays: the current move fades in once the body stands (or rests); others fade out.
    const moveKey = tr.posture === tr.target && !tr.step ? (state + "|" + (activity || "")) : "";
    let cur = tr.overlays.find((o) => o.key === moveKey && o.out == null);
    if (moveKey && !cur) { cur = { key: moveKey, state, activity, t0: now, out: null }; tr.overlays.push(cur); }
    for (const o of tr.overlays) if (o !== cur && o.out == null) o.out = now;
    tr.overlays = tr.overlays.filter((o) => o.out == null || now - o.out < 400);
  }
  function overlayWeight(o, now) {
    // In: quick, with a small overshoot that settles (a move arrives with some snap). Out: eased.
    const f = o.full ? 1 : Math.min(1, (now - o.t0) / 260), c1 = 1.4, c3 = c1 + 1;
    const fadeIn = 1 + c3 * Math.pow(f - 1, 3) + c1 * Math.pow(f - 1, 2);
    const fadeOut = o.out == null ? 1 : Math.max(0, 1 - (now - o.out) / 300);
    return fadeIn * ease(fadeOut);
  }
  function lerpParams(a, b, k) {
    const o = {}; for (const key in a) o[key] = typeof a[key] === "number" && typeof b[key] === "number" ? a[key] + (b[key] - a[key]) * k : a[key];
    return o;
  }
  // Follow-through: loose parts (heads, arms, wings, tails, flames) lag behind the body and swing
  // back on a damped spring, so a hop or a dart carries through them instead of moving the pet as
  // one stiff piece. rig.loose = { part: { x, y } }: how far it turns per 1000 px/s the body moves
  // sideways (x) or down (y). A long gap between frames resets it rather than kicking.
  function followThrough(rig, tr, base, now) {
    const L = rig.loose; if (!L) return;
    const gap = now - tr.last, dt = Math.min(0.05, Math.max(0, gap) / 1000);
    const rx = base.root.x, ry = base.root.y + (base.parts.torso ? base.parts.torso.dy : 0);
    const S = tr.spring || (tr.spring = { x: rx, y: ry, vx: 0, vy: 0, parts: {} });
    if (gap > 250 || gap <= 0) { S.vx = 0; S.vy = 0; }
    else {
      const a = 1 - Math.exp(-dt / 0.06);
      S.vx += ((rx - S.x) / (gap / 1000) - S.vx) * a; S.vy += ((ry - S.y) / (gap / 1000) - S.vy) * a;
    }
    S.x = rx; S.y = ry;
    for (const n in L) {
      const q = base.parts[n]; if (!q) continue;
      const c = L[n], p = S.parts[n] || (S.parts[n] = { a: 0, v: 0 });
      const target = Math.max(-0.35, Math.min(0.35, (-(c.x || 0) * S.vx + (c.y || 0) * S.vy) / 1000));
      for (let left = dt; left > 1e-6; left -= 0.008) {
        const h = Math.min(0.008, left);
        p.v += (150 * (target - p.a) - 13 * p.v) * h; p.a += p.v * h;
      }
      if (gap > 250) { p.v = 0; }
      q.rot += p.a;
    }
  }
  function poseAt(rig, tr, now, reduced) {
    let base;
    if (tr.step) {
      const t = Math.min(1, (now - tr.step.t0) / tr.step.dur);
      base = mix(tr.step.from, fullPose(rig, rig.postures[tr.step.to]), settle(t));
    } else {
      base = fullPose(rig, rig.postures[tr.posture]);
    }
    tr.bare = copyPose(base);
    if (reduced) return base;
    // Life on top: breathing always (its rhythm eases between postures), then the moves.
    const posture = tr.step ? tr.step.to : tr.posture;
    const k = tr.step ? settle(Math.min(1, (now - tr.step.t0) / tr.step.dur)) : 1;
    const p = lerpParams(rig.breathFor(tr.posture), rig.breathFor(posture), k);
    tr.breathPhase = (tr.breathPhase + TAU * Math.max(0, Math.min(200, now - tr.last)) / p.ms) % TAU;
    addOverlay(base, rig.breath(Math.sin(tr.breathPhase), p, now), 1);
    for (const o of tr.overlays) {
      const fn = rig.moves[o.state] || rig.moves.idle;
      const w = overlayWeight(o, now), age = now - o.t0;
      addOverlay(base, fn.call(rig.moves, age, o.activity, posture), w);
      const body = base.parts.torso;
      // A new move starts with a beat of anticipation: the body dips, then springs up into it.
      if (!o.full && age < 340 && o.out == null && body && o.state !== "idle" && o.state !== "unknown") {
        const a = -Math.sin(TAU * age / 340) * (1 - age / 340);
        body.sy *= 1 + 0.035 * a; body.sx *= 1 - 0.02 * a;
      }
      // At rest on its feet it does not stand like a statue: now and then a small hop or a shake.
      if ((o.state === "idle" || o.state === "unknown") && posture === "stand" && !tr.step && age > 4000) {
        const n = Math.floor(age / 8000), ph = age % 8000;
        if (ph < 520) {
          const h = Math.sin(Math.PI * ph / 520) * w;
          if (n % 2) base.root.y -= 6 * h; else base.root.rot += 0.05 * Math.sin(TAU * ph / 260) * h;
        }
      }
    }
    followThrough(rig, tr, base, now);
    return base;
  }

  // ---- drawing -----------------------------------------------------------------------------
  function partMatrix(rig, pose, name, cache) {
    if (cache[name]) return cache[name];
    const def = rig.parts[name], q = pose.parts[name];
    let m;
    if (def.parent === "static") {
      m = new DOMMatrix();               // the ground it stands on never moves
    } else if (def.parent === "hip") {
      const r = pose.root, gp = rig.groundPivot;
      m = new DOMMatrix().translate(gp[0] + r.x, gp[1] + r.y).rotate(r.rot * 180 / Math.PI).translate(-gp[0], -gp[1]);
    } else {
      m = DOMMatrix.fromMatrix(partMatrix(rig, pose, def.parent, cache));
    }
    m = m.translate(def.pivot[0] + q.dx, def.pivot[1] + q.dy).rotate(q.rot * 180 / Math.PI).scale(q.sx, q.sy).translate(-def.pivot[0], -def.pivot[1]);
    cache[name] = m;
    return m;
  }
  // Lights shared by every creature: glows that follow their part and ease with the mood, lids
  // that shut while it sleeps (and blink now and then), and Zs drifting up while it sleeps.
  const GLOW_OF = { failure: 0.35, cancelled: 0.6, success: 1.4, approval: 1.2, thinking: 1.1, tool: 1.15, waiting: 0.8 };
  function lights(g, rig, mat, base, fx) {
    const L = rig.lights; if (!L) return;
    const sleeping = fx.posture === "lie";
    const glowOf = Object.assign({}, GLOW_OF, L.glowOf || {});
    const glow = fx.smooth("glow", sleeping ? (L.sleepGlow != null ? L.sleepGlow : 0.25) : (glowOf[fx.state] || 1), sleeping ? 900 : 350);
    const pulse = fx.reduced ? 1 : 0.85 + 0.15 * Math.sin(TAU * fx.now / 2200);
    g.globalCompositeOperation = "lighter";
    (L.glows || []).forEach((s, i) => {      // [part, x, y, r, "r,g,b", alpha]
      const m = mat(s[0]); g.setTransform(m);
      const fl = fx.reduced ? 1 : 0.9 + 0.1 * Math.sin(TAU * fx.now / (700 + 97 * i));
      const a = Math.min(1, (s[5] || 0.5) * glow * pulse * fl);
      const gr = g.createRadialGradient(s[1], s[2], 0, s[1], s[2], s[3]);
      gr.addColorStop(0, `rgba(${s[4]},${a})`); gr.addColorStop(1, `rgba(${s[4]},0)`);
      g.fillStyle = gr; g.fillRect(s[1] - s[3], s[2] - s[3], 2 * s[3], 2 * s[3]);
    });
    g.globalCompositeOperation = "source-over";
    if (L.lids) {
      const shut = fx.smooth("lids", sleeping ? 1 : 0, 700);
      const bp = (fx.now % 4300) / 4300;
      const blink = fx.reduced || sleeping || L.blink === false ? 0 : (bp < 0.04 ? Math.sin(Math.PI * bp / 0.04) : 0);
      const lid = Math.max(shut, blink);
      // [part, x, y, r, colour a, colour b, kind]. "shutter": a stone lid slides down over the eye
      // (a shutter painted in the eye's own surround). Otherwise the eye closes softly: the glow
      // under it darkens with a feathered edge and a closed-eye curve shows, so it never reads as a
      // flat disc painted on.
      if (lid > 0.02) for (const s of L.lids) {
        g.setTransform(mat(s[0]));
        const r = s[3], x = s[1], y = s[2];
        g.save();
        if (s[6] === "shutter") {
          const y0 = y - r;
          g.beginPath(); g.arc(x, y, r, 0, TAU); g.clip();
          const gr = g.createLinearGradient(0, y0, 0, y0 + 2 * r * lid);
          gr.addColorStop(0, s[4]); gr.addColorStop(1, s[5]);
          g.fillStyle = gr; g.fillRect(x - r, y0, 2 * r, 2 * r * lid);
          g.fillStyle = "rgba(20,20,24,0.8)"; g.fillRect(x - r, y0 + 2 * r * lid - 1.2, 2 * r, 1.6);
        } else {
          const gr = g.createRadialGradient(x, y, 0, x, y, r * 1.15);
          gr.addColorStop(0, s[5]); gr.addColorStop(0.7, s[5]); gr.addColorStop(1, "rgba(0,0,0,0)");
          g.globalAlpha = 0.88 * lid; g.fillStyle = gr; g.fillRect(x - r * 1.2, y - r * 1.2, r * 2.4, r * 2.4);
          g.globalAlpha = lid; g.strokeStyle = s[4]; g.lineWidth = Math.max(1.2, r * 0.16); g.lineCap = "round";
          g.beginPath(); g.arc(x, y - r * 0.55, r * 0.8, Math.PI * 0.22, Math.PI * 0.78); g.stroke();
        }
        g.restore();
      }
    }
    const zs = fx.smooth("zs", sleeping && !fx.step ? 1 : 0, 450);
    if (L.zs && zs > 0.01 && !fx.reduced) {
      const q = base.inverse().transformPoint(mat(L.zs[0]).transformPoint(new DOMPoint(L.zs[1], L.zs[2])));
      g.setTransform(base); g.textAlign = "center";
      for (let i = 0; i < 3; i++) {
        const ph = ((fx.now / 2400) + i / 3) % 1;
        g.fillStyle = `rgba(220,232,255,${0.9 * zs * Math.sin(Math.PI * ph)})`;
        g.font = `bold ${11 + 7 * ph}px -apple-system, system-ui, sans-serif`;
        g.fillText("z", q.x + 18 * ph + 8, q.y - 38 * ph);
      }
    }
  }
  function drawPose(g, rig, imgs, pose, box, fx) {
    // box: {x, y, s} maps rig px to canvas px.
    const cache = {};
    const zOf = (n) => (pose.parts[n] && pose.parts[n].z != null ? pose.parts[n].z : rig.parts[n].z);
    const order = Object.keys(rig.parts).sort((a, b) => zOf(a) - zOf(b));
    const base = new DOMMatrix([box.s, 0, 0, box.s, box.x, box.y]);
    const mat = (n) => base.multiply(partMatrix(rig, pose, n, cache));
    for (const n of order) {
      const def = rig.parts[n], a = pose.parts[n].a;
      if (a <= 0.01) continue;
      g.setTransform(mat(n)); g.globalAlpha = Math.min(1, a);
      g.drawImage(imgs[n], def.x, def.y, def.w, def.h);
    }
    g.globalAlpha = 1;
    fx = fx || {};
    if (!fx.smooth) fx.smooth = (k, v) => v;
    lights(g, rig, mat, base, fx);
    if (rig.extras) { g.setTransform(base); rig.extras(g, pose, mat, base, fx); }
    g.globalAlpha = 1; g.globalCompositeOperation = "source-over";
    g.setTransform(1, 0, 0, 1, 0, 0);
  }
  function frame(g, rig, imgs, tr, state, activity, elapsed, now, box, reduced) {
    advance(rig, tr, state, activity, elapsed, now, reduced);
    const dt = Math.max(0, Math.min(200, now - tr.last));
    const pose = poseAt(rig, tr, now, reduced);
    tr.last = now;
    // Eases a level toward `target` with time constant `ms`; the first call starts at the target.
    const smooth = (key, target, ms) => {
      const cur = tr.levels[key];
      const v = cur == null || reduced ? target : cur + (target - cur) * (1 - Math.exp(-dt / (ms || 250)));
      tr.levels[key] = v; return v;
    };
    const posture = tr.step ? tr.step.to : tr.posture;
    const fx = { now, state, activity, elapsed, posture, leaving: tr.posture, step: tr.step, reduced, smooth,
      moves: tr.overlays.map((o) => ({ state: o.state, activity: o.activity, t: now - o.t0, w: overlayWeight(o, now) })) };
    drawPose(g, rig, imgs, pose, box, fx);
    return { pose, fx };
  }
  root.VoolPetRig = { newTracker, frame, drawPose, fullPose, wantedPosture, H, rigs: root.VoolPetRig ? root.VoolPetRig.rigs : {} };
})(typeof VP_RIG_ROOT !== "undefined" ? VP_RIG_ROOT : typeof window !== "undefined" ? window : globalThis);
"""

RIG_PETS_JS: dict[str, str] = {
    "beetle": r"""
/* MORROW BEETLE: a rune-shell beetle on many legs. Settles its shell down to sit, tucks its legs
   to sleep with the runes dimmed and eyes shut. Scuttles to search, digs with its front claws. */
VoolPetRig.rigs.beetle = function (meta) {
  const { wave, beat, pulse, ramp, once } = VoolPetRig.H;
  return {
    parts: meta.parts, groundPivot: [128, 243],
    loose: { legsL: { y: 1 }, legsR: { y: -1 }, fangs: { x: -1.6 } },
    rest: { sitAfter: 25000, lieAfter: 90000 },
    durations: { "stand>sit": 800, "sit>stand": 700, "sit>lie": 1200, "lie>sit": 900 },
    postures: {
      stand: {},
      // The shell settles while the feet stay put: legs shorten by what the shell drops.
      sit: { parts: { torso: { dy: 20, sy: 0.97 }, legsL: { rot: -0.14, dy: -8, sx: 1.06, sy: 0.9 }, legsR: { rot: 0.14, dy: -8, sx: 1.06, sy: 0.9 }, fangs: { dy: -6, sy: 0.82 } } },
      lie: { parts: { torso: { dy: 32, sy: 0.94 }, legsL: { rot: -0.2, dy: -10, sx: 0.82, sy: 0.8 }, legsR: { rot: 0.2, dy: -10, sx: 0.82, sy: 0.8 }, fangs: { dy: -8, sy: 0.62 } } },
    },
    breathFor(posture) { return posture === "lie" ? { ms: 4400, amp: 1.5 } : { ms: 3000, amp: 1 }; },
    breath(b, p) { return { parts: { torso: { sy: 1 + 0.01 * p.amp * b }, legsL: { rot: 0.01 * b }, legsR: { rot: -0.01 * b } } }; },
    moves: {
      idle(t) { // shifts its legs now and then
        const s = pulse(t, 5200, 600), s2 = pulse(t + 2600, 5200, 600);
        return { parts: { legsL: { rot: 0.05 * s }, legsR: { rot: -0.05 * s2 } } };
      },
      starting(t) { const k = ramp(t, 400); return { parts: { torso: { dy: -3 * k }, legsL: { rot: -0.04 * k }, legsR: { rot: 0.04 * k } } }; },
      thinking(t) { return { parts: { torso: { rot: 0.04 * wave(t, 2800) }, fangs: { sx: 1 + 0.04 * wave(t, 600) } } }; },
      tool(t, activity) {
        switch (activity) {
          case "read": return { parts: { torso: { dy: 3, rot: 0.02 * wave(t, 1800) }, fangs: { sy: 0.95 } } };
          case "search": { // scuttles side to side
            const w = wave(t, 2000), step = wave(t, 250);
            return { root: { x: 12 * w }, parts: { legsL: { rot: 0.08 * step }, legsR: { rot: 0.08 * step }, torso: { dy: 1.5 * Math.abs(step) } } };
          }
          case "dig": { const d = Math.max(0, wave(t, 600)); return { root: { rot: 0.05 * d }, parts: { fangs: { dy: 6 * d, sx: 1 - 0.08 * d }, torso: { dy: 3 * d } } }; }
          case "code": { const a = wave(t, 220); return { parts: { legsL: { rot: 0.05 * a }, legsR: { rot: 0.05 * a }, fangs: { sx: 1 + 0.03 * a } } }; }
          case "test": { const s = Math.sign(wave(t, 1600)) * Math.min(1, Math.abs(wave(t, 1600)) * 3); return { root: { rot: 0.06 * s }, parts: { legsL: { rot: 0.06 * Math.max(0, s) }, legsR: { rot: 0.06 * Math.max(0, -s) } } }; }
          case "watch": return { parts: { torso: { rot: 0.015 * wave(t, 3600) } } };
          default: { const k = beat(t, 700); return { root: { y: -6 * k }, parts: { legsL: { rot: -0.08 * k }, legsR: { rot: 0.08 * k } } }; } // run: stomps
        }
      },
      waiting(t) { return { parts: { legsR: { rot: -0.05 * Math.max(0, wave(t, 900)) } } }; },
      approval(t) { const h = beat(t, 800); return { root: { y: -8 * h }, parts: { legsL: { rot: 0.14 * h }, legsR: { rot: -0.14 * h } } }; },
      retry(t) { const k = Math.max(0, 1 - t / 1400); return { root: { rot: 0.06 * wave(t, 240) * k } }; },
      success(t) { const hop = t < 1100 ? beat(t, 550) : 0; return { root: { y: -14 * hop }, parts: { legsL: { rot: 0.18 }, legsR: { rot: -0.18 } } }; },
      failure() { return { parts: { torso: { dy: 6 }, legsL: { rot: -0.08, dy: -4 }, legsR: { rot: 0.08, dy: -4 }, fangs: { dy: -4 } } }; },
      cancelled(t) { const k = once(t, 900); return { parts: { legsL: { rot: 0.1 * k }, legsR: { rot: -0.1 * k } } }; },
      unknown(t) { return this.idle(t); },
    },
    lights: {
      glows: [["torso", 124, 58, 16, "255,210,122", 0.5], ["torso", 81, 105, 16, "255,210,122", 0.5], ["torso", 124, 102, 16, "255,210,122", 0.5],
              ["torso", 169, 105, 16, "255,210,122", 0.5], ["torso", 104, 157, 13, "255,179,71", 0.6], ["torso", 149, 157, 13, "255,179,71", 0.6]],
      lids: [["torso", 104, 157, 11, "rgba(255,190,110,0.8)", "rgba(24,22,26,0.96)"], ["torso", 149, 157, 11, "rgba(255,190,110,0.8)", "rgba(24,22,26,0.96)"]],
      sleepGlow: 0.15, zs: ["torso", 175, 40],
    },
  };
};
""",
    "raven": r"""
/* CLOCKWORK RAVEN: perches with wings half open, folds them to sit, tucks its head in to sleep.
   Pecks to read and write, turns its head sharply to search, flaps to run. */
VoolPetRig.rigs.raven = function (meta) {
  const { wave, beat, pulse, ramp, once } = VoolPetRig.H;
  // A bird's head moves in quick turns that then hold: a smoothed step between looks.
  const snap = (t, every, looks) => {
    const i = Math.floor(t / every), f = (t % every) / every, e = VoolPetRig.H.ease(Math.min(1, f / 0.12));
    const a = looks[i % looks.length], b = looks[(i + 1) % looks.length];
    return a + (b - a) * e;
  };
  return {
    parts: meta.parts, groundPivot: [118, 229],
    loose: { head: { x: 1.5 }, wingL: { y: 1.2 }, wingR: { y: -1.2 }, tail: { y: -1.2 } },
    rest: { sitAfter: 25000, lieAfter: 90000 },
    durations: { "stand>sit": 900, "sit>stand": 700, "sit>lie": 1200, "lie>sit": 900 },
    postures: {
      stand: {},
      // Wings fold down along the body, feathers fluffed.
      sit: { parts: { torso: { dy: 6, sx: 1.03 }, wingL: { rot: -0.2, sx: 0.72, sy: 0.88, dy: 6 }, wingR: { rot: 0.2, sx: 0.72, sy: 0.88, dy: 6 }, head: { dy: 4 }, feet: { sy: 0.85 }, tail: { rot: 0.1 } } },
      lie: { parts: { torso: { dy: 14, sx: 1.06, sy: 0.95 }, wingL: { rot: -0.3, sx: 0.6, sy: 0.78, dy: 12 }, wingR: { rot: 0.3, sx: 0.6, sy: 0.78, dy: 12 },
        head: { dy: 16, rot: 0.25 }, feet: { sy: 0.55 }, tail: { rot: 0.2 } } },
    },
    breathFor(posture) { return posture === "lie" ? { ms: 4200, amp: 1.5 } : { ms: 2800, amp: 1 }; },
    breath(b, p) { return { parts: { torso: { sy: 1 + 0.014 * p.amp * b }, wingL: { rot: 0.02 * b }, wingR: { rot: -0.02 * b }, head: { dy: -0.6 * b } } }; },
    moves: {
      idle(t) { return { parts: { head: { rot: snap(t, 2300, [0, 0.12, 0, -0.08, 0.05]) } } }; },
      starting(t) { const k = ramp(t, 400); return { parts: { head: { dy: -3 * k }, wingL: { rot: 0.1 * k }, wingR: { rot: -0.1 * k } } }; },
      thinking(t) { return { parts: { head: { rot: 0.22 + 0.03 * wave(t, 2400) }, tail: { rot: 0.04 * wave(t, 1200) } } }; },
      tool(t, activity) {
        switch (activity) {
          case "read": { const p = pulse(t, 1400, 300); return { parts: { head: { rot: 0.12 + 0.06 * wave(t, 2800), dy: 3 + 4 * p } } }; }
          case "search": return { parts: { head: { rot: snap(t, 600, [0.18, -0.15, 0.1, -0.2]) }, wingL: { rot: 0.12 }, wingR: { rot: -0.12 } } };
          case "dig": { const p = pulse(t, 500, 260); return { parts: { head: { dy: 10 * p, rot: 0.3 * p }, torso: { rot: 0.04 * p } } }; }
          case "code": { const p = pulse(t, 300, 150); return { parts: { head: { dy: 5 * p, rot: 0.15 * p } } }; }
          case "test": return { parts: { head: { rot: snap(t, 1200, [0.25, -0.2]) }, tail: { rot: 0.06 * wave(t, 600) } } };
          case "watch": return { parts: { head: { rot: 0.06 * wave(t, 3800) } } };
          default: { const f = wave(t, 420); return { root: { y: -3 * Math.max(0, f) }, parts: { wingL: { rot: 0.3 * f }, wingR: { rot: -0.3 * f } } }; } // run: flaps
        }
      },
      waiting(t) { return { parts: { feet: { sy: 1 - 0.04 * Math.max(0, wave(t, 1400)) }, head: { rot: snap(t, 3500, [0, 0.1]) } } }; },
      approval(t) { const f = wave(t, 600); return { parts: { head: { rot: -0.12, dy: -2 }, wingL: { rot: 0.25 + 0.2 * f }, wingR: { rot: -0.25 - 0.2 * f } } }; },
      retry(t) { const k = Math.max(0, 1 - t / 1400); return { parts: { torso: { sx: 1 + 0.04 * Math.abs(wave(t, 200)) * k }, head: { rot: 0.12 * wave(t, 260) * k } } }; },
      success(t) { const hop = t < 1100 ? beat(t, 550) : 0, f = t < 1100 ? wave(t, 275) : 0; return { root: { y: -14 * hop }, parts: { wingL: { rot: 0.2 + 0.25 * f }, wingR: { rot: -0.2 - 0.25 * f }, head: { rot: -0.1 } } }; },
      failure() { return { parts: { head: { dy: 8, rot: 0.15 }, wingL: { rot: -0.3, dy: 4 }, wingR: { rot: 0.3, dy: 4 }, tail: { rot: 0.12 } } }; },
      cancelled(t) { const k = once(t, 900); return { parts: { wingL: { rot: 0.2 * k }, wingR: { rot: -0.2 * k }, head: { rot: 0.1 * k } } }; },
      unknown(t) { return this.idle(t); },
    },
    lights: {
      glows: [["head", 105, 77, 12, "255,210,122", 0.6], ["torso", 145, 117, 12, "255,210,122", 0.3]],
      lids: [["head", 105, 77, 10, "rgba(255,214,140,0.85)", "rgba(28,26,30,0.96)"]],
      zs: ["head", 120, 50],
    },
  };
};
""",
    "golem": r"""
/* STONE GOLEM: a heavy body that sits and lies down like stone settling; eyes and chest gem glow. */
VoolPetRig.rigs.golem = function (meta) {
  const { wave, beat, pulse, ramp } = VoolPetRig.H;
  return {
    parts: meta.parts, groundPivot: [140, 262],
    loose: { head: { x: 1.2 }, armL: { x: -1.4, y: 1.2 }, armR: { x: -1.4, y: -1.2 } },
    rest: { sitAfter: 25000, lieAfter: 90000 },
    durations: { "stand>sit": 900, "sit>stand": 800, "sit>lie": 1400, "lie>sit": 1100 },
    postures: {
      stand: {},
      sit: { parts: {
        torso: { dy: 24 },
        legL: { dy: 1, rot: 0.95, sy: 0.85, z: 4 }, legR: { dy: 1, rot: -0.95, sy: 0.85, z: 4 },   // feet stay on the ground
        armL: { rot: -0.18 }, armR: { rot: 0.18 }, head: { dy: 1 } } },
      // Lying down to sleep, facing you: the body sinks to the ground, legs out flat, arms
      // resting beside it, chin down.
      lie: { parts: {
        torso: { dy: 27, sy: 0.93 },
        legL: { dy: 7, rot: 1.0, sy: 0.55, z: 0.5 }, legR: { dy: 7, rot: -1.0, sy: 0.55, z: 0.5 },
        armL: { rot: 0.22, dy: 3, z: 1.5 }, armR: { rot: -0.22, dy: 3, z: 1.5 }, head: { dy: 12, rot: 0.1 } } },
    },
    breathFor(posture) { return posture === "lie" ? { ms: 4600, amp: 1.6 } : { ms: 3400, amp: 1 }; },
    breath(b, p) {
      const a = p.amp;
      return { parts: { torso: { sy: 1 + 0.012 * a * b }, head: { dy: -0.9 * a * b }, armL: { rot: 0.012 * b }, armR: { rot: -0.012 * b } } };
    },
    moves: {
      idle(t) { // a slow look around
        return { parts: { head: { rot: 0.05 * Math.sin(2 * Math.PI * t / 7000) * (Math.sin(2 * Math.PI * t / 13000) > 0 ? 1 : 0.3) } } };
      },
      starting(t) { // gets ready: shoulders up, a small stretch
        const k = ramp(t, 400);
        return { parts: { armL: { rot: 0.3 * k }, armR: { rot: -0.3 * k }, head: { dy: -2 * k } } };
      },
      thinking(t) { // hand to chin, head tilted, slow nod
        return { parts: { armL: { rot: -1.3, z: 1.5 }, head: { rot: 0.1 + 0.03 * wave(t, 2400), dy: 1 } } };
      },
      tool(t, activity) {
        switch (activity) {
          case "read": return { parts: { armL: { rot: -1.2 }, armR: { rot: 1.2 }, head: { rot: 0.02 * wave(t, 1500), dy: 3 } } };
          case "search": return { parts: { armR: { rot: -2.5 }, head: { rot: 0.14 * wave(t, 1800) } } };
          case "dig": { const a = wave(t, 700); return { parts: { armL: { rot: -0.6 * Math.max(0, a) }, armR: { rot: 0.6 * Math.max(0, -a) }, torso: { dy: 1.5 * Math.abs(a) } } }; }
          case "code": { const a = wave(t, 260); return { parts: { armL: { rot: -0.9 + 0.12 * a }, armR: { rot: 0.9 + 0.12 * a }, head: { dy: 2 } } }; }
          case "test": { // holds a hand to the chest gem, checks it, nods when it holds
            const n = pulse(t, 1600, 420);
            return { parts: { armL: { rot: -0.95, z: 1.5 }, head: { rot: -0.05, dy: 3 + 3 * n }, armR: { rot: 0.1 * wave(t, 1600) } } };
          }
          case "watch": return { parts: { head: { rot: 0.04 * wave(t, 3000) }, armL: { rot: 0.1 }, armR: { rot: -0.1 } } };
          default: { // run: a slam -- raise, strike, rebound
            const p = (t % 1100) / 1100;
            const up = p < 0.55 ? Math.sin((p / 0.55) * Math.PI / 2) : p < 0.68 ? 1 - (p - 0.55) / 0.13 * 1.08 : -0.08 * (1 - (p - 0.68) / 0.32);
            const hit = p >= 0.66 && p < 0.8 ? 1 - (p - 0.66) / 0.14 : 0;
            return { root: { y: 1.5 * hit }, parts: { armR: { rot: -2.2 * up }, torso: { sy: 1 - 0.02 * hit }, head: { dy: 2 * hit } } };
          }
        }
      },
      waiting(t) { // taps a foot
        return { parts: { legR: { rot: -0.12 * Math.max(0, wave(t, 900)) }, head: { rot: 0.04 * wave(t, 4000) } } };
      },
      approval(t) { // looks at you and waves
        return { parts: { head: { rot: -0.12 }, armR: { rot: -2.3 + 0.3 * wave(t, 520) } } };
      },
      retry(t) { // shakes it off
        const k = Math.max(0, 1 - t / 1400);
        return { parts: { head: { rot: 0.14 * wave(t, 260) * k }, armL: { rot: 0.1 * wave(t, 260) * k } } };
      },
      success(t) { // two happy hops, arms up
        const hop = t < 1100 ? beat(t, 550) : 0;
        return { root: { y: -16 * hop }, parts: { armL: { rot: 2.3 }, armR: { rot: -2.3 }, torso: { sy: 1 + 0.03 * hop }, head: { dy: -2 * hop } } };
      },
      failure() { // slumps
        return { parts: { torso: { dy: 5, sy: 0.98 }, head: { dy: 7, rot: 0.06 }, armL: { rot: -0.12 }, armR: { rot: 0.12 } } };
      },
      cancelled(t) { // a shrug, then lets it go
        const k = t < 900 ? Math.sin(Math.PI * t / 900) : 0;
        return { parts: { armL: { rot: 0.7 * k }, armR: { rot: -0.7 * k }, head: { rot: 0.12 * k } } };
      },
      unknown(t) { return this.idle(t); },
    },
    lights: {
      glows: [["head", 118, 78, 15, "90,200,255", 0.55], ["head", 162, 78, 15, "90,200,255", 0.55], ["torso", 140, 164, 34, "70,170,255", 0.35]],
      lids: [["head", 118, 78, 14, "rgba(170,225,255,0.75)", "rgba(86,90,84,0.97)"], ["head", 162, 78, 14, "rgba(170,225,255,0.75)", "rgba(86,90,84,0.97)"]],
      zs: ["head", 110, 40],
    },
    // Dust where a slam lands.
    extras(g, pose, mat, base, fx) {
      const slam = fx.moves && fx.moves.find((m) => m.state === "tool" && !["read", "search", "dig", "code", "test", "watch"].includes(m.activity));
      if (!slam || fx.reduced) return;
      const p = (slam.t % 1100) / 1100;
      if (p <= 0.66) return;
      const k = (p - 0.66) / 0.34;
      for (let i = 0; i < 7; i++) {
        const a = (i / 6) * Math.PI, r = 8 + 30 * k;
        g.fillStyle = `rgba(170,160,140,${0.5 * (1 - k) * slam.w})`;
        g.beginPath(); g.arc(232 + Math.cos(a) * r, 258 - Math.sin(a) * r * 0.5, 3 + 3 * (1 - k), 0, 2 * Math.PI); g.fill();
      }
    },
  };
};
""",
    "tide": r"""
/* TIDE WISP: a diving-helmet octopus in its own splash. Sways its tentacles, sinks down to sit,
   curls up low to sleep with its portholes shuttered. Reaches out with tentacles to search. */
VoolPetRig.rigs.tide = function (meta) {
  const { wave, beat, pulse, ramp, once } = VoolPetRig.H;
  return {
    parts: meta.parts, groundPivot: [128, 240],
    loose: { head: { x: 1.2 }, tentL: { y: 1 }, tentR: { y: -1 } },
    rest: { sitAfter: 25000, lieAfter: 90000 },
    durations: { "stand>sit": 1000, "sit>stand": 800, "sit>lie": 1300, "lie>sit": 1000 },
    postures: {
      stand: { root: { y: -6 } },
      sit: { parts: { torso: { dy: 8, sy: 0.95 }, tentL: { rot: -0.08 }, tentR: { rot: 0.08 } } },
      lie: { parts: { torso: { dy: 18, sy: 0.88 }, head: { dy: 6, rot: 0.06 }, tentL: { rot: -0.18, sx: 0.88 }, tentR: { rot: 0.18, sx: 0.88 } } },
    },
    breathFor(posture) { return posture === "stand" ? { ms: 2600, sway: 1, bob: 3 } : posture === "sit" ? { ms: 3400, sway: 0.6, bob: 1 } : { ms: 4800, sway: 0.25, bob: 0 }; },
    breath(b, p, now) {
      return { root: { y: p.bob * Math.sin(2 * Math.PI * now / 3200) },
        parts: { tentL: { rot: 0.05 * p.sway * b }, tentR: { rot: 0.05 * p.sway * Math.sin(2 * Math.PI * now / 2900) }, torso: { sy: 1 + 0.012 * b }, head: { dy: -0.8 * b } } };
    },
    moves: {
      idle(t) { return { parts: { head: { rot: 0.04 * wave(t, 6400) } } }; },
      starting(t) { const k = ramp(t, 400); return { parts: { head: { dy: -3 * k }, tentL: { rot: 0.08 * k }, tentR: { rot: -0.08 * k } } }; },
      thinking(t) { return { parts: { head: { rot: 0.09 + 0.03 * wave(t, 2400) }, tentR: { rot: -0.3 + 0.04 * wave(t, 1200) } } }; },
      tool(t, activity) {
        switch (activity) {
          case "read": return { parts: { head: { dy: 3, rot: 0.03 * wave(t, 1800) }, tentL: { rot: -0.15 }, tentR: { rot: 0.15 } } };
          case "search": { const w = wave(t, 1600); return { parts: { tentL: { rot: 0.3 * Math.max(0, w) }, tentR: { rot: -0.3 * Math.max(0, -w) }, head: { rot: 0.1 * w } } }; }
          case "dig": { const d = beat(t, 700); return { parts: { torso: { dy: 6 * d, sy: 1 - 0.04 * d }, tentL: { rot: -0.12 * d }, tentR: { rot: 0.12 * d } } }; }
          case "code": return { parts: { tentL: { rot: 0.07 * wave(t, 240) }, tentR: { rot: 0.07 * wave(t, 240, 0.5) }, head: { dy: 2 } } };
          case "test": { const n = pulse(t, 1500, 450); return { parts: { tentR: { rot: -0.35 - 0.12 * n }, head: { rot: -0.04 - 0.04 * n } } }; }   // taps its helmet
          case "watch": return { parts: { head: { rot: 0.04 * wave(t, 3600) } } };
          default: { const k = beat(t, 650); return { root: { y: -5 * k }, parts: { torso: { sy: 1 + 0.04 * k }, tentL: { rot: 0.1 * k }, tentR: { rot: -0.1 * k } } }; } // run: pumps
        }
      },
      waiting(t) { return { parts: { head: { rot: 0.05 * wave(t, 4400) } } }; },
      approval(t) { const f = wave(t, 600); return { parts: { tentL: { rot: 0.3 + 0.12 * f }, tentR: { rot: -0.3 + 0.12 * f }, head: { rot: -0.06 } } }; },
      retry(t) { const k = Math.max(0, 1 - t / 1400); return { parts: { head: { rot: 0.12 * wave(t, 260) * k } } }; },
      success(t) { const h = t < 1100 ? beat(t, 550) : 0; return { root: { y: -14 * h }, parts: { tentL: { rot: 0.35 }, tentR: { rot: -0.35 } } }; },
      failure() { return { parts: { torso: { dy: 8, sy: 0.95 }, head: { dy: 4, rot: 0.08 }, tentL: { rot: -0.15 }, tentR: { rot: 0.15 } } }; },
      cancelled(t) { const k = once(t, 900); return { parts: { tentL: { rot: 0.2 * k }, tentR: { rot: -0.2 * k } } }; },
      unknown(t) { return this.idle(t); },
    },
    lights: {
      glows: [["head", 102, 91, 20, "255,179,71", 0.55], ["head", 151, 92, 20, "255,179,71", 0.55]],
      lids: [["head", 102, 91, 18, "rgba(255,190,110,0.8)", "rgba(52,38,20,0.96)"], ["head", 151, 92, 18, "rgba(255,190,110,0.8)", "rgba(52,38,20,0.96)"]], blink: false,
      zs: ["head", 175, 30],
    },
  };
};
""",
    "spark": r"""
/* SPARK, the lantern bee: hovers on buzzing wings, lands to sit, sleeps on the ground with its
   wings folded. Works in the air: darts to search, dives to dig, loops when done. */
VoolPetRig.rigs.spark = function (meta) {
  const { wave, beat, pulse, ramp, once } = VoolPetRig.H;
  return {
    parts: meta.parts, groundPivot: [128, 248],
    loose: { head: { x: 1.5 }, legs: { x: -2.5 } },
    rest: { sitAfter: 25000, lieAfter: 90000 },
    durations: { "stand>sit": 1000, "sit>stand": 700, "sit>lie": 1300, "lie>sit": 1000 },
    postures: {
      stand: { root: { y: -14 } },                                  // in the air
      sit: { parts: { legs: { sy: 0.9 }, wingL: { rot: -0.12 }, wingR: { rot: 0.12 } } },   // landed
      lie: { root: { y: 4 }, parts: { torso: { sy: 0.94 }, legs: { sy: 0.7, dy: -6 }, head: { dy: 8, rot: 0.08 },
        wingL: { rot: -0.45, sx: 0.85 }, wingR: { rot: 0.45, sx: 0.85 } } },                 // asleep, wings down
    },
    // Wing beat: a fast buzz in the air, a slow fan when landed, still when asleep.
    breathFor(posture) {
      return posture === "stand" ? { ms: 150, wing: 0.22, bob: 3, body: 0.6 } : posture === "sit" ? { ms: 1500, wing: 0.1, bob: 0, body: 1 } : { ms: 4400, wing: 0.02, bob: 0, body: 1.6 };
    },
    breath(b, p, now) {
      return { root: { y: p.bob * Math.sin(2 * Math.PI * now / 2600) },
        parts: { wingL: { rot: p.wing * b, sx: 1 - 0.25 * p.wing * Math.abs(b) }, wingR: { rot: -p.wing * b, sx: 1 - 0.25 * p.wing * Math.abs(b) },
          torso: { sy: 1 + 0.01 * p.body * Math.sin(2 * Math.PI * now / 3000) } } };
    },
    moves: {
      idle(t) { return { parts: { head: { rot: 0.07 * wave(t, 5200) } }, root: { x: 3 * wave(t, 7000) } }; },
      starting(t) { const k = ramp(t, 400); return { root: { y: -6 * k }, parts: { head: { dy: -2 * k } } }; },
      thinking(t) { // hovers still, head tilted, rubs its front legs
        return { parts: { head: { rot: 0.12 + 0.03 * wave(t, 2400) }, legs: { sx: 1 + 0.05 * wave(t, 300) } } };
      },
      tool(t, activity) {
        switch (activity) {
          case "read": return { root: { rot: 0.08 }, parts: { head: { dy: 4, rot: 0.06 * wave(t, 1600) } } };
          case "search": // darts from side to side, looking
            return { root: { x: 14 * wave(t, 2200), rot: 0.08 * wave(t, 2200, 0.25) }, parts: { head: { rot: -0.1 * wave(t, 2200) } } };
          case "dig": { const d = beat(t, 900); return { root: { y: 12 * d, rot: 0.12 * d }, parts: { legs: { sx: 1 + 0.08 * wave(t, 180) } } }; }
          case "code": return { parts: { legs: { sy: 1 + 0.06 * wave(t, 220) }, head: { dy: 2 + 1.2 * wave(t, 440) } } };
          case "test": // tilts one way, then the other, checking
            return { root: { rot: 0.12 * Math.sign(wave(t, 1800)) * Math.min(1, Math.abs(wave(t, 1800)) * 3) }, parts: { head: { rot: 0.05 * wave(t, 900) } } };
          case "watch": return { parts: { head: { rot: 0.05 * wave(t, 3400) } } };
          default: { const k = beat(t, 600); return { root: { y: -5 * k }, parts: { torso: { sy: 1 + 0.03 * k } } }; } // run: pulses its lantern
        }
      },
      waiting(t) { return { root: { y: 2 * wave(t, 3600) }, parts: { head: { rot: 0.06 * wave(t, 4200) } } }; },
      approval(t) { // flies up to you and waves its legs
        return { root: { y: -8 - 6 * beat(t, 900) }, parts: { head: { rot: -0.08 }, legs: { sx: 1 + 0.12 * wave(t, 260) } } };
      },
      retry(t) { const k = Math.max(0, 1 - t / 1400); return { root: { rot: 0.14 * wave(t, 260) * k } }; },
      success(t) { // a loop in the air
        const spin = t < 1000 ? 2 * Math.PI * VoolPetRig.H.ease(t / 1000) : 0, r = 123;   // turns about its middle
        return { root: { x: -r * Math.sin(spin), y: -18 * once(t, 1000) - r + r * Math.cos(spin), rot: spin } };
      },
      failure() { return { root: { y: 8 }, parts: { head: { dy: 5, rot: 0.08 }, wingL: { rot: -0.2 }, wingR: { rot: 0.2 } } }; },
      cancelled(t) { const k = once(t, 900); return { parts: { head: { rot: 0.12 * k }, wingL: { rot: -0.2 * k }, wingR: { rot: 0.2 * k } } }; },
      unknown(t) { return this.idle(t); },
    },
    lights: {
      glows: [["head", 107, 82, 14, "255,207,106", 0.6], ["head", 141, 82, 14, "255,207,106", 0.6],
              ["torso", 126, 150, 22, "255,154,60", 0.55], ["head", 64, 17, 12, "255,190,90", 0.5], ["head", 186, 17, 12, "255,190,90", 0.5]],
      lids: [["head", 107, 82, 12, "rgba(255,214,140,0.85)", "rgba(58,40,18,0.96)"], ["head", 141, 82, 12, "rgba(255,214,140,0.85)", "rgba(58,40,18,0.96)"]],
      zs: ["head", 150, 50],
    },
  };
};
""",
    "rascal": r"""
/* RASCAL, the steam courier tank: idles with a rumble, settles on its tracks to sit, shuts the
   engine off to sleep with the cannon lowered. Sweeps its turret to search, fires to run. */
VoolPetRig.rigs.rascal = function (meta) {
  const { wave, beat, pulse, ramp, once } = VoolPetRig.H;
  return {
    parts: meta.parts, groundPivot: [135, 221],
    loose: { cannon: { y: 0.8 }, hose: { y: -1.6, x: 1.5 } },
    rest: { sitAfter: 25000, lieAfter: 90000 },
    durations: { "stand>sit": 800, "sit>stand": 700, "sit>lie": 1200, "lie>sit": 900 },
    postures: {
      stand: {},
      sit: { parts: { torso: { dy: 9, sy: 0.95 }, cannon: { rot: -0.14, dx: 4 }, hose: { rot: 0.22 } } },        // parked: hull down, cannon lowered
      lie: { parts: { torso: { dy: 14, sy: 0.92 }, cannon: { rot: -0.3, dx: 6 }, hose: { rot: 0.4 } } },        // engine off
    },
    // The engine rumble: quick and small while it runs, slow and still when it is off.
    breathFor(posture) { return posture === "stand" ? { ms: 420, rumble: 0.5, amp: 1 } : posture === "sit" ? { ms: 700, rumble: 0.25, amp: 1 } : { ms: 4600, rumble: 0, amp: 1.4 }; },
    breath(b, p, now) {
      return { parts: { torso: { dy: p.rumble * b, sy: 1 + 0.006 * p.amp * Math.sin(2 * Math.PI * now / 3600) }, hose: { rot: 0.02 * p.rumble * b } } };
    },
    moves: {
      idle(t) { return { parts: { cannon: { rot: 0.06 * wave(t, 7000) } } }; },
      starting(t) { const k = ramp(t, 400); return { parts: { cannon: { rot: 0.08 * k }, torso: { dy: -1.5 * k } } }; },
      thinking(t) { return { parts: { cannon: { rot: 0.14 + 0.04 * wave(t, 2400) } } }; },
      tool(t, activity) {
        switch (activity) {
          case "read": return { parts: { cannon: { rot: -0.08 + 0.03 * wave(t, 1800) } } };
          case "search": { const w = wave(t, 2400); return { root: { x: 10 * w }, parts: { cannon: { rot: 0.18 * wave(t, 1200) } } }; }
          case "dig": { const d = beat(t, 800); return { root: { rot: 0.05 * d, x: 4 * d }, parts: { cannon: { rot: -0.1 * d } } }; }
          case "code": return { parts: { cannon: { rot: 0.03 * wave(t, 200) }, hose: { rot: 0.08 * wave(t, 300) } } };
          case "test": return { parts: { torso: { dy: 1.6 * wave(t, 90) }, hose: { rot: 0.12 * wave(t, 160) }, cannon: { rot: 0.05 } } };   // revs the engine
          case "watch": return { parts: { cannon: { rot: 0.03 * wave(t, 4000) } } };
          default: { // run: fires -- recoil, then rolls back into place
            const p = (t % 1400) / 1400, kick = p < 0.08 ? p / 0.08 : Math.max(0, 1 - (p - 0.08) / 0.4);
            return { root: { x: 3 * kick }, parts: { cannon: { dx: 7 * kick, rot: 0.05 * kick } } };
          }
        }
      },
      waiting(t) { return { parts: { cannon: { rot: 0.04 * wave(t, 4400) } } }; },
      approval(t) { return { parts: { cannon: { rot: 0.32 + 0.1 * wave(t, 520) } } }; },
      retry(t) { const k = Math.max(0, 1 - t / 1400); return { root: { rot: 0.03 * wave(t, 240) * k }, parts: { hose: { rot: 0.15 * wave(t, 240) * k } } }; },
      success(t) { const h = t < 1100 ? beat(t, 550) : 0; return { root: { y: -10 * h, rot: -0.08 * h }, parts: { cannon: { rot: 0.3 } } }; },   // little wheelies
      failure() { return { parts: { torso: { dy: 4 }, cannon: { rot: -0.22 }, hose: { rot: 0.2 } } }; },
      cancelled(t) { const k = once(t, 900); return { parts: { cannon: { rot: 0.15 * k } } }; },
      unknown(t) { return this.idle(t); },
    },
    lights: { glows: [["cannon", 28, 58, 14, "255,179,71", 0.5], ["torso", 175, 80, 10, "255,200,120", 0.35]], sleepGlow: 0.05, zs: ["torso", 150, 20] },
  };
};
""",
    "prime": r"""
/* PRIME, the key knight: a small armoured knight with a great key. Sits and lies like the golem,
   lighter; works with the key (holds it up as a lantern, turns it, checks it). */
VoolPetRig.rigs.prime = function (meta) {
  const { wave, beat, pulse, ramp } = VoolPetRig.H;
  return {
    parts: meta.parts, groundPivot: [100, 254],
    loose: { head: { x: 1.2 }, armL: { x: -1.4, y: 1.2 }, armR: { x: -0.5, y: -0.4 } },
    rest: { sitAfter: 25000, lieAfter: 90000 },
    durations: { "stand>sit": 800, "sit>stand": 700, "sit>lie": 1200, "lie>sit": 1000 },
    postures: {
      stand: {},
      sit: { parts: {
        torso: { dy: 22 },
        legL: { dy: -5, rot: 0.95, sy: 0.85, z: 4 }, legR: { dy: -5, rot: -0.95, sy: 0.85, z: 4 },   // feet stay on the ground
        armL: { rot: -0.15 }, armR: { rot: 0.04, dy: -22 }, head: { dy: 1 } } },   // the key stays planted; the hand slides up it
      // Lies down facing you, the key leaned against him.
      lie: { parts: {
        torso: { dy: 32, sy: 0.93 },
        legL: { dy: 3, rot: 1.0, sy: 0.55, z: 0.5 }, legR: { dy: 3, rot: -1.0, sy: 0.55, z: 0.5 },
        armL: { rot: 0.45, z: 1.5 }, armR: { rot: 0.2, dy: -34 }, head: { dy: 10, rot: 0.1 } } },
    },
    breathFor(posture) { return posture === "lie" ? { ms: 4400, amp: 1.5 } : { ms: 3200, amp: 1 }; },
    breath(b, p) {
      const a = p.amp;
      return { parts: { torso: { sy: 1 + 0.012 * a * b }, head: { dy: -0.8 * a * b }, armL: { rot: 0.012 * b }, armR: { rot: -0.008 * b } } };
    },
    moves: {
      idle(t) { // looks around, now and then plants the key
        return { parts: { head: { rot: 0.06 * Math.sin(2 * Math.PI * t / 6500) * (Math.sin(2 * Math.PI * t / 12000) > 0 ? 1 : 0.3) },
          armR: { rot: -0.03 * pulse(t, 9000, 700) } } };
      },
      starting(t) { const k = ramp(t, 400); return { parts: { armR: { rot: -0.15 * k }, head: { dy: -2 * k }, armL: { rot: 0.15 * k } } }; },
      thinking(t) { // hand to chin, head tilted
        return { parts: { armL: { rot: -1.25, z: 1.5 }, head: { rot: 0.1 + 0.03 * wave(t, 2400), dy: 1 } } };
      },
      tool(t, activity) {
        switch (activity) {
          case "read": // holds a page up with the free hand and reads it
            return { parts: { armL: { rot: -1.0 }, head: { rot: 0.03 * wave(t, 1600), dy: 3 } } };
          case "search": // lifts the key like a lantern and looks around
            return { parts: { armR: { rot: -0.45 + 0.08 * wave(t, 1800) }, head: { rot: 0.14 * wave(t, 1800) } } };
          case "dig": { // levers with the key
            const a = Math.max(0, wave(t, 800));
            return { parts: { armR: { rot: 0.35 * a }, torso: { dy: 2 * a, rot: 0.03 * a }, head: { dy: 2 * a } } };
          }
          case "code": { const a = wave(t, 260); return { parts: { armL: { rot: -0.8 + 0.12 * a }, armR: { rot: -0.05 + 0.04 * a }, head: { dy: 2 } } }; }
          case "test": { // holds the key up to the eye, checks it, nods
            const n = pulse(t, 1700, 450);
            return { parts: { armR: { rot: -0.75 }, head: { rot: -0.08, dy: 3 * n }, armL: { rot: -0.3 } } };
          }
          case "watch": return { parts: { head: { rot: 0.05 * wave(t, 3200) }, armR: { rot: -0.05 } } };
          default: { // run: thrusts the key in and turns it
            const p = (t % 1200) / 1200;
            const out = p < 0.35 ? Math.sin((p / 0.35) * Math.PI / 2) : p < 0.75 ? 1 : 1 - (p - 0.75) / 0.25;
            const turn = p >= 0.4 && p < 0.7 ? Math.sin(Math.PI * (p - 0.4) / 0.3) : 0;
            return { parts: { armR: { rot: -0.55 * out, dx: -4 * out }, torso: { rot: -0.03 * out }, head: { rot: -0.05 * out, dy: turn } } };
          }
        }
      },
      waiting(t) { return { parts: { legR: { rot: -0.12 * Math.max(0, wave(t, 900)) }, head: { rot: 0.04 * wave(t, 4000) } } }; },
      approval(t) { // looks at you and waves the free hand
        return { parts: { head: { rot: -0.1 }, armL: { rot: 2.3 + 0.3 * wave(t, 520) } } };
      },
      retry(t) { const k = Math.max(0, 1 - t / 1400); return { parts: { head: { rot: 0.14 * wave(t, 260) * k }, armL: { rot: 0.1 * wave(t, 260) * k } } }; },
      success(t) { // hops, key raised high
        const hop = t < 1100 ? beat(t, 550) : 0;
        return { root: { y: -14 * hop }, parts: { armL: { rot: 2.3 }, armR: { rot: -0.6 }, torso: { sy: 1 + 0.03 * hop }, head: { dy: -2 * hop } } };
      },
      failure() { return { parts: { torso: { dy: 5, sy: 0.98 }, head: { dy: 7, rot: 0.06 }, armL: { rot: -0.12 }, armR: { rot: 0.22 } } }; },
      cancelled(t) { const k = t < 900 ? Math.sin(Math.PI * t / 900) : 0; return { parts: { armL: { rot: 0.7 * k }, armR: { rot: -0.15 * k }, head: { rot: 0.12 * k } } }; },
      unknown(t) { return this.idle(t); },
    },
    lights: {
      glows: [["head", 77, 72, 13, "255,210,120", 0.6], ["head", 110, 72, 13, "255,210,120", 0.6],
              ["torso", 95, 176, 26, "95,230,255", 0.45], ["armR", 190, 45, 16, "95,230,255", 0.5]],
      sleepGlow: 0.08, zs: ["head", 115, 20],
    },
  };
};
""",
    "prism": r"""
/* PRISM, the crystal fish: swims in place with its tail, sinks to rest, lies on the floor asleep
   with its fins flat. Works like a fish: noses down to read, darts to search, dives to dig. */
VoolPetRig.rigs.prism = function (meta) {
  const { wave, beat, pulse, ramp, once } = VoolPetRig.H;
  return {
    parts: meta.parts, groundPivot: [150, 240],
    loose: { tail: { y: 1.6 }, finTop: { x: 0.9 }, finBot: { x: -1.2 } },
    rest: { sitAfter: 25000, lieAfter: 90000 },
    durations: { "stand>sit": 1100, "sit>stand": 800, "sit>lie": 1300, "lie>sit": 1000 },
    postures: {
      stand: { root: { y: -12 } },
      sit: { root: { y: -2 }, parts: { finTop: { sy: 0.94 }, finBot: { sy: 0.9 } } },
      lie: { root: { y: 6, rot: 0.06 }, parts: { finTop: { sy: 0.85, rot: 0.04 }, finBot: { sy: 0.7 }, tail: { rot: 0.12, sx: 0.9 }, torso: { sy: 0.96 } } },
    },
    breathFor(posture) {
      return posture === "stand" ? { ms: 1300, tail: 0.2, fin: 0.04, bob: 4 } : posture === "sit" ? { ms: 2200, tail: 0.1, fin: 0.025, bob: 1 } : { ms: 4600, tail: 0.03, fin: 0.012, bob: 0 };
    },
    breath(b, p, now) {
      return { root: { y: p.bob * Math.sin(2 * Math.PI * now / 3000) },
        parts: { tail: { rot: p.tail * b }, finTop: { rot: p.fin * Math.sin(2 * Math.PI * now / 1700) }, finBot: { rot: -p.fin * Math.sin(2 * Math.PI * now / 1900) } } };
    },
    moves: {
      idle(t) { return { root: { rot: 0.04 * wave(t, 6000), x: 3 * wave(t, 8000) } }; },
      starting(t) { const k = ramp(t, 400); return { root: { y: -4 * k }, parts: { finTop: { sy: 1 + 0.05 * k } } }; },
      thinking(t) { return { root: { rot: -0.06 + 0.02 * wave(t, 2600) }, parts: { tail: { rot: 0.05 * wave(t, 900) } } }; },
      tool(t, activity) {
        switch (activity) {
          case "read": return { root: { rot: 0.07 + 0.02 * wave(t, 1600) }, parts: { finBot: { rot: 0.05 * wave(t, 800) } } };
          case "search": return { root: { x: 14 * wave(t, 2400), rot: 0.06 * wave(t, 2400, 0.25) }, parts: { tail: { rot: 0.25 * wave(t, 300) } } };
          case "dig": { const d = beat(t, 1000); return { root: { y: 10 * d, rot: 0.22 * d }, parts: { tail: { rot: 0.3 * wave(t, 250) * d } } }; }
          case "code": return { parts: { finTop: { rot: 0.06 * wave(t, 240) }, finBot: { rot: 0.07 * wave(t, 240, 0.5) }, tail: { rot: 0.08 * wave(t, 480) } } };
          case "test": { const n = pulse(t, 1600, 500); return { root: { rot: -0.1 * n }, parts: { finTop: { sy: 1 + 0.08 * n } } }; }
          case "watch": return { root: { rot: 0.02 * wave(t, 3600) } };
          default: { const k = beat(t, 700); return { root: { x: -4 * k }, parts: { tail: { rot: 0.35 * wave(t, 200) }, torso: { sx: 1 + 0.02 * k } } }; }
        }
      },
      waiting(t) { return { root: { rot: 0.03 * wave(t, 4400) } }; },
      approval(t) { const h = beat(t, 900); return { root: { y: -10 * h, rot: -0.08 * h }, parts: { finTop: { sy: 1 + 0.1 * h }, finBot: { sy: 1 + 0.1 * h } } }; },
      retry(t) { const k = Math.max(0, 1 - t / 1400); return { root: { rot: 0.1 * wave(t, 260) * k } }; },
      success(t) { // a leap with fins flared
        const h = once(t, 900);
        return { root: { y: -22 * h, rot: -0.25 * h * (t < 450 ? 1 : 1) }, parts: { finTop: { sy: 1 + 0.12 * h }, finBot: { sy: 1 + 0.12 * h }, tail: { rot: 0.3 * wave(t, 180) * h } } };
      },
      failure() { return { root: { y: 8, rot: 0.1 }, parts: { finTop: { sy: 0.9 }, finBot: { sy: 0.85 }, tail: { rot: 0.15 } } }; },
      cancelled(t) { const k = once(t, 900); return { root: { rot: 0.08 * k } }; },
      unknown(t) { return this.idle(t); },
    },
    lights: { glows: [["torso", 154, 121, 34, "143,233,255", 0.45], ["finTop", 180, 50, 20, "200,250,255", 0.25]], sleepGlow: 0.3, zs: ["torso", 226, 96] },
  };
};
""",
    "veil": r"""
/* VEIL, the dusk moth: hovers on four slow wings, lands with its wings raised, sleeps with them
   folded down and every eye on its wings shut. Its wing eyes light up as it works. */
VoolPetRig.rigs.veil = function (meta) {
  const { wave, beat, pulse, ramp, once } = VoolPetRig.H;
  const wings = (u, l) => ({ wingUL: { rot: u }, wingUR: { rot: -u }, wingLL: { rot: l }, wingLR: { rot: -l } });
  return {
    parts: meta.parts, groundPivot: [128, 232],
    loose: { wingUL: { y: 1 }, wingUR: { y: -1 }, wingLL: { y: 0.8 }, wingLR: { y: -0.8 } },
    rest: { sitAfter: 25000, lieAfter: 90000 },
    durations: { "stand>sit": 1100, "sit>stand": 800, "sit>lie": 1300, "lie>sit": 1000 },
    postures: {
      stand: { root: { y: -12 } },
      sit: { parts: Object.assign(wings(0.12, 0.08), { wingUL: { rot: 0.12, sx: 0.9 }, wingUR: { rot: -0.12, sx: 0.9 } }) },   // upper and lower wings turn together: no tear between them
      lie: { root: { y: 4 }, parts: { torso: { sy: 0.95 }, wingUL: { rot: -0.16, sx: 0.84, dy: 6 }, wingUR: { rot: 0.16, sx: 0.84, dy: 6 },
        wingLL: { rot: -0.1, sx: 0.86 }, wingLR: { rot: 0.1, sx: 0.86 } } },
    },
    breathFor(posture) { return posture === "stand" ? { ms: 1500, flap: 0.12, bob: 4 } : posture === "sit" ? { ms: 2600, flap: 0.05, bob: 0 } : { ms: 4800, flap: 0.015, bob: 0 }; },
    breath(b, p, now) {
      const f = p.flap * b;
      return { root: { y: p.bob * Math.sin(2 * Math.PI * now / 3400) },
        parts: { wingUL: { rot: f, sx: 1 - 0.6 * p.flap * Math.abs(b) }, wingUR: { rot: -f, sx: 1 - 0.6 * p.flap * Math.abs(b) },
          wingLL: { rot: 0.6 * f }, wingLR: { rot: -0.6 * f }, torso: { sy: 1 + 0.01 * Math.sin(2 * Math.PI * now / 3600) } } };
    },
    moves: {
      idle(t) { return { root: { x: 4 * wave(t, 8000), rot: 0.03 * wave(t, 6000) } }; },
      starting(t) { const k = ramp(t, 400); return { root: { y: -4 * k }, parts: wings(0.08 * k, 0.04 * k) }; },
      thinking(t) { const w = 0.1 * wave(t, 2600); return { parts: Object.assign(wings(w, w * 0.6), { torso: { rot: 0.03 * wave(t, 2600) } }) }; },
      tool(t, activity) {
        switch (activity) {
          case "read": return { root: { rot: 0.02 * wave(t, 1800) }, parts: wings(-0.06, -0.03) };
          case "search": return { root: { x: 14 * wave(t, 2600), rot: 0.07 * wave(t, 2600, 0.25) }, parts: wings(0.12, 0.06) };
          case "dig": { const d = beat(t, 1000); return { root: { y: 10 * d }, parts: wings(0.18 * d, 0.1 * d) }; }
          case "code": { const f = 0.06 * wave(t, 260); return { parts: wings(f, f) }; }
          case "test": return { root: { rot: 0.05 * wave(t, 1600) }, parts: wings(0.05, 0.02) };
          case "watch": return { parts: wings(0.12, 0.06) };   // wings wide: every eye open
          default: { const f = 0.22 * wave(t, 500); return { root: { y: -3 * Math.abs(f) * 10 }, parts: wings(f, f * 0.6) }; } // run: strong beats
        }
      },
      waiting(t) { return { root: { rot: 0.03 * wave(t, 4400) } }; },
      approval(t) { const h = beat(t, 1000); return { root: { y: -10 * h }, parts: wings(0.3 * h, 0.15 * h) }; },
      retry(t) { const k = Math.max(0, 1 - t / 1400); return { root: { rot: 0.1 * wave(t, 260) * k } }; },
      success(t) { const h = once(t, 1000), f = t < 1000 ? 0.3 * wave(t, 330) : 0; return { root: { y: -18 * h }, parts: wings(0.15 + f, 0.08 + f * 0.5) }; },
      failure() { return { root: { y: 8 }, parts: Object.assign(wings(-0.25, -0.15), { torso: { dy: 2 } }) }; },
      cancelled(t) { const k = once(t, 900); return { parts: wings(0.2 * k, 0.1 * k) }; },
      unknown(t) { return this.idle(t); },
    },
    lights: {
      glows: [["torso", 115, 92, 9, "225,170,255", 0.7], ["torso", 141, 92, 9, "225,170,255", 0.7],
              ["wingUL", 56, 75, 14, "180,108,255", 0.5], ["wingUR", 194, 75, 14, "180,108,255", 0.5], ["wingUL", 61, 107, 12, "180,108,255", 0.45],
              ["wingUR", 194, 107, 12, "180,108,255", 0.45], ["wingLL", 76, 150, 12, "180,108,255", 0.45], ["wingLR", 179, 150, 12, "180,108,255", 0.45]],
      glowOf: { tool: 1.45 },
      lids: [["torso", 115, 92, 8, "rgba(220,170,255,0.85)", "rgba(28,12,46,0.96)"], ["torso", 141, 92, 8, "rgba(220,170,255,0.85)", "rgba(28,12,46,0.96)"]],
      sleepGlow: 0.12, zs: ["torso", 150, 40],
    },
  };
};
""",
    "ember": r"""
/* EMBER, the living furnace: its fire flickers as it breathes, it settles to sit and banks its
   fire low to sleep with its eye shutters closed. Stokes up to work, roars when done. */
VoolPetRig.rigs.ember = function (meta) {
  const { wave, beat, pulse, ramp, once } = VoolPetRig.H;
  return {
    parts: meta.parts, groundPivot: [123, 254],
    loose: { flame: { x: 3 }, chimneyFlame: { x: 4 } },
    rest: { sitAfter: 25000, lieAfter: 90000 },
    durations: { "stand>sit": 800, "sit>stand": 700, "sit>lie": 1300, "lie>sit": 1000 },
    postures: {
      stand: {},
      sit: { parts: { torso: { sy: 0.96 }, flame: { sy: 0.8 }, chimneyFlame: { sy: 0.8 } } },
      lie: { parts: { torso: { sy: 0.93, sx: 1.02 }, flame: { sy: 0.35, a: 0.6 }, chimneyFlame: { sy: 0.3, a: 0.4 } } },   // banked fire
    },
    breathFor(posture) { return posture === "lie" ? { ms: 4600, flick: 0.3, amp: 1.4 } : { ms: 3000, flick: 1, amp: 1 }; },
    breath(b, p, now) {
      const f = Math.sin(2 * Math.PI * now / 170) * 0.5 + Math.sin(2 * Math.PI * now / 290) * 0.5;
      return { parts: { torso: { sy: 1 + 0.01 * p.amp * b }, flame: { sy: 1 + 0.05 * p.flick * f + 0.04 * b, rot: 0.03 * p.flick * Math.sin(2 * Math.PI * now / 900) },
        chimneyFlame: { sy: 1 + 0.08 * p.flick * Math.sin(2 * Math.PI * now / 230) } } };
    },
    moves: {
      idle(t) { return { parts: { flame: { rot: 0.04 * wave(t, 5000) } } }; },
      starting(t) { const k = ramp(t, 400); return { parts: { flame: { sy: 1 + 0.2 * k }, torso: { sy: 1 + 0.01 * k } } }; },
      thinking(t) { return { parts: { flame: { rot: 0.08 * wave(t, 1600), sy: 1.08 }, torso: { rot: 0.015 * wave(t, 2600) } } }; },
      tool(t, activity) {
        switch (activity) {
          case "read": return { root: { rot: 0.025 }, parts: { flame: { rot: 0.06, sy: 1.05 } } };
          case "search": { const w = wave(t, 2200); return { root: { x: 8 * w, rot: 0.03 * w }, parts: { flame: { rot: -0.1 * w } } }; }
          case "dig": { const d = beat(t, 700); return { parts: { torso: { sy: 1 - 0.03 * d }, flame: { sy: 1 + 0.35 * d } } }; }   // stoking
          case "code": { const p = pulse(t, 400, 200); return { parts: { chimneyFlame: { sy: 1 + 0.5 * p }, flame: { sy: 1.1 } } }; }
          case "test": { const n = pulse(t, 1500, 500); return { root: { y: -4 * n, rot: -0.02 }, parts: { flame: { sy: 1.12 + 0.2 * n, rot: -0.05 } } }; }   // leans in, checks, flares
          case "watch": return { parts: { flame: { sy: 1.05 } } };
          default: { const k = beat(t, 800); return { parts: { flame: { sy: 1 + 0.5 * k }, chimneyFlame: { sy: 1 + 0.4 * k }, torso: { sy: 1 + 0.02 * k } } }; } // run: whoosh
        }
      },
      waiting(t) { return { parts: { flame: { sy: 0.92 } } }; },
      approval(t) { const h = beat(t, 900); return { root: { y: -8 * h }, parts: { flame: { sy: 1 + 0.3 * h } } }; },
      retry(t) { const k = Math.max(0, 1 - t / 1400); return { root: { rot: 0.04 * wave(t, 240) * k } }; },
      success(t) { const h = t < 1100 ? beat(t, 550) : 0; return { root: { y: -12 * h }, parts: { flame: { sy: 1.5 }, chimneyFlame: { sy: 1.4 } } }; },
      failure() { return { parts: { torso: { sy: 0.96 }, flame: { sy: 0.45, a: 0.7 }, chimneyFlame: { sy: 0.4, a: 0.6 } } }; },
      cancelled(t) { const k = once(t, 900); return { parts: { flame: { sy: 1 - 0.3 * k } } }; },
      unknown(t) { return this.idle(t); },
    },
    lights: {
      glows: [["torso", 81, 130, 18, "255,154,60", 0.6], ["torso", 138, 130, 18, "255,154,60", 0.6], ["torso", 115, 205, 40, "255,122,26", 0.4],
              ["flame", 110, 45, 36, "255,138,42", 0.35]],
      lids: [["torso", 81, 130, 14, "rgba(255,150,70,0.85)", "rgba(36,30,28,0.96)"], ["torso", 138, 130, 14, "rgba(255,150,70,0.85)", "rgba(36,30,28,0.96)"]], blink: false,
      sleepGlow: 0.3, zs: ["torso", 150, 70],
    },
  };
};
""",
}
