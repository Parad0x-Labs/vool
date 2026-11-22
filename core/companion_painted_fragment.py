"""Painted companions: the ten pets of the v0.8 remaster (owner's concept sheet, 2026-11-22).

The owner painted the Sidekick family again at high detail and chose ONE look per creature
(2026-11-22, thread "Replace pets with new remastered set"): six keep the original look (beetle,
raven, tide, rascal, veil, ember) and four take the "II" look (golem, spark, prime, prism). These
ten are the only pets. Each is a cut-out of the owner's painting (``core/companion_pet_art.py``).

Motion: each painting is also cut into parts on bones (``core/companion_pet_rig_art.py``,
``core/companion_pet_rigs.py``). The owner asked for continuity: a pet sits down, lies down and
gets up again, never jumps from one pose to the next, and shows what kind of work it is doing.
Each drawn canvas keeps a tracker, so every frame starts from the pose the last one showed. Until
the parts are decoded (or if they cannot be) the whole painting is drawn and moved as one picture:
breathing, a work rhythm, wing beats and tentacle sway by bending strips of it. Particles (sparks,
smoke, embers, bubbles, glints, dust), glows and the work sign are drawn on top in both cases.
Nothing here is a new drawing of a pet.

Ids stay the ten old ids, so a saved choice survives. The ten retired "II" ids (``rm_*``) map to
the same creature's kept look (:data:`PAINTED_ALIASES`). The old pixel-art drawings are kept only
as the picture of last resort when a webview cannot decode the art.

Size: the pet keeps its old on-screen box (144 px inline, 96 px chip, the same desktop window).
The canvas is now backed at the screen's own pixel density so the painting is sharp on Retina;
the pet's body fills about the same share of the box as the old 48x48 pets did.
"""

from __future__ import annotations

import json

from core.companion_pet_art import PET_ART
from core.companion_pet_rig_art import RIG_ART, RIG_META
from core.companion_pet_rigs import RIG_ENGINE_JS, RIG_PETS_JS

PAINTED_ROSTER = (
    "beetle", "raven", "golem", "tide", "spark",
    "rascal", "prime", "prism", "veil", "ember",
)
# The retired looks. A choice saved as one of these is the same creature's kept look.
PAINTED_ALIASES = {"rm_" + pet: pet for pet in PAINTED_ROSTER}
# The old drawing of the SAME look, used only if the painting cannot be decoded.
_LEGACY_LOOK = {
    "beetle": "beetle", "raven": "raven", "tide": "tide", "rascal": "rascal", "veil": "veil",
    "ember": "ember", "golem": "rm_golem", "spark": "rm_spark", "prime": "rm_prime", "prism": "rm_prism",
}

_NAMES = {
    "beetle": ("MORROW BEETLE", "rune-shell beetle"),
    "raven": ("CLOCKWORK RAVEN", "brass clockwork raven"),
    "golem": ("STONE GOLEM", "mossy crystal golem"),
    "tide": ("TIDE WISP", "diving-helmet octopus"),
    "spark": ("SPARK · Lamplighter", "brass lantern bee"),
    "rascal": ("RASCAL · Courier", "steam courier tank"),
    "prime": ("PRIME · Keykeeper", "golden key knight"),
    "prism": ("PRISM · Lanternfin", "crystal shard fish"),
    "veil": ("VEIL · Duskmoth", "many-eyed dusk moth"),
    "ember": ("EMBER · Kiln", "living furnace"),
}

# Each creature's rig, in fractions of its own picture (0..1 from the top-left of the cut-out).
#   float  : hovers (bobs in the air) instead of standing on the floor
#   wings  : [centre x, half-width of the body that does not bend] -> wing beats
#   wave   : y where tentacles start -> tentacle sway below it
#   glow   : [x, y, radius, colour] eyes, cores and flames that pulse
#   fx     : the creature's own particles, with where they come from
_RIGS: dict[str, dict] = {
    "beetle": {"glow": [[.37, .63, .07, "#ffb347"], [.61, .63, .07, "#ffb347"], [.48, .27, .07, "#ffd27a"],
                        [.34, .43, .06, "#ffd27a"], [.49, .43, .06, "#ffd27a"], [.66, .43, .06, "#ffd27a"]],
               "fx": [["arcs", "#9fd4ff"]]},
    "raven": {"wings": [.5, .17], "glow": [[.36, .33, .04, "#ffd27a"]],
              "fx": [["smoke", [[.52, .2], [.6, .2]], "#c9ced6"]]},
    "golem": {"glow": [[.4, .33, .06, "#6fe3ff"], [.6, .33, .06, "#6fe3ff"], [.5, .58, .1, "#58c8ff"]],
              "fx": [["glints", [[.47, .06], [.17, .45], [.86, .46], [.5, .58]], "#c8f4ff"]]},
    "tide": {"wave": .52,
             "glow": [[.37, .4, .07, "#ffb347"], [.62, .4, .07, "#ffb347"]],
             "fx": [["bubbles", [[.2, .75], [.5, .9], [.8, .72]], "#bfefff"]]},
    "spark": {"wings": [.5, .16], "float": True,
              "glow": [[.42, .27, .05, "#ffcf6a"], [.58, .27, .05, "#ffcf6a"], [.5, .52, .09, "#ff9a3c"]],
              "fx": [["embers", [[.5, .62]], "#ffb347"], ["smoke", [[.33, .08], [.67, .08]], "#b9bcc4"]]},
    "rascal": {"glow": [[.06, .27, .05, "#ffb347"]],
               "fx": [["smoke", [[.68, .2], [.8, .25]], "#9aa0a8"], ["embers", [[.05, .3]], "#ffc14d"]]},
    "prime": {"glow": [[.42, .3, .05, "#ffd27a"], [.55, .3, .05, "#ffd27a"], [.42, .6, .1, "#5fe6ff"],
                       [.86, .2, .05, "#5fe6ff"]],
              "fx": [["arcs", "#ffe58a"]]},
    "prism": {"float": True, "aura": "#3fb8ff", "glow": [[.6, .5, .1, "#8fe9ff"]],
              "fx": [["glints", [[.85, .5], [.5, .25], [.4, .72], [.22, .45]], "#e6fbff"]]},
    "veil": {"wings": [.5, .15], "float": True, "aura": "#8b3dff",
             "glow": [[.43, .4, .04, "#d79bff"], [.57, .4, .04, "#d79bff"], [.2, .3, .05, "#b46cff"],
                      [.8, .3, .05, "#b46cff"]],
             "fx": [["motes", [[.5, .45]], "#c98bff"]]},
    "ember": {"glow": [[.33, .5, .07, "#ff9a3c"], [.62, .5, .07, "#ff9a3c"], [.48, .78, .14, "#ff7a1a"],
                       [.35, .12, .12, "#ff8a2a"]],
              "fx": [["embers", [[.35, .1], [.85, .18]], "#ffb347"], ["smoke", [[.35, .02]], "#8f8a86"]]},
}


def painted_registry() -> dict[str, dict]:
    """Name, note, rig and fallback look of every pet, in roster order."""
    return {
        pet: {"name": _NAMES[pet][0], "note": _NAMES[pet][1], "rig": _RIGS[pet], "legacy": _LEGACY_LOOK[pet]}
        for pet in PAINTED_ROSTER
    }


def resolve_pet_id(value: object, default: str = "spark") -> str:
    """A saved or sent pet id as one of the ten: retired looks map to their creature."""
    pet = str(value or "").strip().lower()
    pet = PAINTED_ALIASES.get(pet, pet)
    return pet if pet in PAINTED_ROSTER else default


_PAINTED_ENGINE_JS = r"""
/* ---- painted pets ---------------------------------------------------------------------
   One picture per pet, animated by transforming and bending that picture. Logical space is the
   old 48-unit grid (so hosts that draw extras in 48 units still line up); the canvas itself is
   backed at device pixels by vcwFitCanvas so the painting stays sharp. Time drives the pose only;
   state and activity come from the typed owner and are never inferred here. No Math.random: all
   particle "noise" is a hash of the particle index, so a frame is the same picture every time. */
const VP_IMG={},VP_GREY={},VP_FAIL={},VP_READY_HOOKS=[],VP_RIG_IMG={};
/* The same painting cut into parts, for motion (VoolPetRig). Until every part is decoded the whole
   painting is drawn; a part that cannot be decoded leaves the pet on its whole painting. */
function vpRigLoad(id){
 if(VP_RIG_IMG[id]||!VP_RIG_ART[id]||typeof Image==='undefined')return;
 const o={imgs:{},left:0,fail:false};VP_RIG_IMG[id]=o;
 const done=function(){for(let k=0;k<VP_READY_HOOKS.length;k++){try{VP_READY_HOOKS[k](id);}catch(e){}}};
 Object.keys(VP_RIG_ART[id]).forEach(function(n){
  const im=new Image();o.imgs[n]=im;o.left++;
  im.onload=function(){if(--o.left===0)done();};
  im.onerror=function(){if(!o.fail){o.fail=true;done();}};
  im.src=VP_RIG_ART[id][n];});}
function vpRigReady(id){const o=VP_RIG_IMG[id];return !!(o&&!o.fail&&o.left===0);}
function vpLoad(id){
 vpRigLoad(id);
 if(VP_IMG[id]||VP_FAIL[id]||!VP_ART[id]||typeof Image==='undefined')return;
 const im=new Image();VP_IMG[id]=im;
 im.onload=function(){
  try{
   if(typeof document!=='undefined'&&document.createElement){
    const c=document.createElement('canvas');c.width=im.naturalWidth;c.height=im.naturalHeight;
    const x=c.getContext('2d');x.drawImage(im,0,0);
    const d=x.getImageData(0,0,c.width,c.height),p=d.data;
    for(let i=0;i<p.length;i+=4){const l=(p[i]*.3+p[i+1]*.59+p[i+2]*.11)*.8;p[i]=p[i+1]=p[i+2]=l;}
    x.putImageData(d,0,0);VP_GREY[id]=c;}
  }catch(e){/* no grey copy: a failure pose just dims instead */}
  for(let k=0;k<VP_READY_HOOKS.length;k++){try{VP_READY_HOOKS[k](id);}catch(e){}}};
 im.onerror=function(){VP_FAIL[id]=true;delete VP_IMG[id];
  for(let k=0;k<VP_READY_HOOKS.length;k++){try{VP_READY_HOOKS[k](id);}catch(e){}}};
 im.src=VP_ART[id];}
/* Ready: the painting is decoded and its parts are settled (decoded, or failed and not used). */
function vpReady(id){
 const im=VP_IMG[id];if(!(im&&im.complete&&im.naturalWidth>0))return false;
 if(!VP_RIG_ART[id])return true;
 const o=VP_RIG_IMG[id];return !!o&&(o.fail||o.left===0);}
function vpOnReady(fn){if(typeof fn==='function')VP_READY_HOOKS.push(fn);}
Object.keys(VP_ART).forEach(vpLoad);
/* Back a canvas at the screen's pixel density for a given CSS size. */
function vcwFitCanvas(canvas,css){
 const dpr=Math.max(1,Math.min(3,(typeof window!=='undefined'&&window.devicePixelRatio)||1));
 const px=Math.max(48,Math.round(css*dpr));
 if(canvas.width!==px)canvas.width=px;if(canvas.height!==px)canvas.height=px;
 return canvas;}
function vpHash(n){n=Math.imul(n^0x9e3779b9,0x85ebca6b);n^=n>>>13;n=Math.imul(n,0xc2b2ae35);n^=n>>>16;return (n>>>0)/4294967296;}
const VP_TAU=Math.PI*2;
function vpWave(t,period,phase){return Math.sin(VP_TAU*(t/period+(phase||0)));}
const VP_ONE_SHOT={success:1300,failure:900,cancelled:900};
/* The pose for a state. Units are the 48-unit grid; s* are scale factors. */
function vpPose(state,activity,t,reduced,rig){
 const P={dy:0,dx:0,sx:1,sy:1,rot:0,flap:0,wave:0,glow:.7,grey:0,dim:0,badge:null,fx:1,burst:-1,dots:false};
 const fl=!!rig.float;
 if(reduced){
  P.fx=0;
  if(state==='approval')P.badge='ask';else if(state==='failure'){P.grey=.6;P.dy=.8;P.badge='fail';}
  else if(state==='cancelled'){P.grey=.4;}else if(state==='success')P.badge='done';
  else if(state==='waiting')P.badge='wait';else if(state==='retry')P.badge='retry';
  else if(state==='tool')P.badge=activity||'exec';else if(state==='thinking'||state==='starting')P.dots=true;
  return P;}
 const breathe=(period,amt)=>{const b=vpWave(t,period);P.sy=1+amt*b;P.sx=1-amt*.5*b;};
 if(state==='idle'){
  breathe(3400,.014);P.glow=.62+.22*(.5+.5*vpWave(t,2600));P.flap=.22+.22*vpWave(t,2400);P.wave=.8;
  if(fl)P.dy=-1.1*vpWave(t,2800);
 }else if(state==='starting'||state==='thinking'){
  breathe(2200,.016);P.rot=.035*vpWave(t,3000);P.glow=.8+.2*vpWave(t,1100);P.flap=.35+.3*vpWave(t,1300);P.wave=1.1;
  P.dots=true;if(fl)P.dy=-1.2*vpWave(t,2000);
 }else if(state==='tool'){
  const beat=Math.abs(vpWave(t,700,0)),bh=activity==='exec'||activity==='dig'||activity==='code'?.9:.55;
  P.dy=-bh*beat;P.sy=1+.02*beat;P.sx=1-.01*beat;P.glow=.85+.15*vpWave(t,500);
  P.flap=.5+.5*vpWave(t,520);P.wave=1.5;P.fx=1.5;P.badge=activity||'exec';
 }else if(state==='waiting'){
  breathe(4200,.01);P.glow=.45+.15*vpWave(t,3600);P.dim=.12;P.flap=.1;P.wave=.5;P.fx=.5;P.badge='wait';
  if(fl)P.dy=-.7*vpWave(t,3600);
 }else if(state==='approval'){
  const c=t%1500,h=c<340?Math.sin(Math.PI*c/340):0;
  P.dy=-2.4*h;P.sy=c>=340&&c<460?.95:1+.04*h;P.sx=c>=340&&c<460?1.04:1-.02*h;
  P.glow=.75+.25*vpWave(t,700);P.flap=h>0?-.6:.2;P.wave=1.2;P.badge='ask';
 }else if(state==='retry'){
  P.rot=.06*vpWave(t,420);P.glow=.7+.2*vpWave(t,420);P.flap=.6*vpWave(t,420);P.wave=1.4;P.badge='retry';
 }else if(state==='success'){
  const u=Math.min(t,VP_ONE_SHOT.success);
  if(u<560){const k=u/560,h=Math.sin(Math.PI*k);P.dy=-4.2*h;P.sy=1+.06*h;P.sx=1-.04*h;P.flap=-.9;}
  else if(u<760){const k=(u-560)/200,q=Math.sin(Math.PI*k);P.sy=1-.07*q;P.sx=1+.05*q;}
  P.glow=1;P.burst=Math.min(1,u/1100);P.badge='done';P.wave=1;
 }else if(state==='failure'){
  const u=Math.min(t,VP_ONE_SHOT.failure),k=Math.min(1,u/500);
  P.dx=u<500?1.2*(1-k)*vpWave(u,120):0;P.dy=1.1*k;P.sy=1-.035*k;P.sx=1+.015*k;
  P.grey=.55*k;P.glow=.25;P.flap=.9*k;P.wave=.3;P.fx=.2;P.badge='fail';
 }else if(state==='cancelled'){
  const k=Math.min(1,t/VP_ONE_SHOT.cancelled);P.dy=.6*k;P.grey=.4*k;P.glow=.35;P.fx=.3;P.flap=.5*k;P.wave=.4;
 }else{ /* unknown: finished, not independently reviewed -- calm, a small settled nod */
  const k=Math.min(1,t/700);P.rot=-.03*Math.sin(Math.PI*k);breathe(3400,.012);P.glow=.65;P.wave=.7;
  if(fl)P.dy=-.9*vpWave(t,2800);}
 return P;}
/* The body: the picture, optionally bent (wings or tentacles), at a placement in canvas px. */
function vpBody(g,img,W,H,rig,P,t){
 if(rig.wings){
  const cx=rig.wings[0]*W,core=rig.wings[1]*W,half=Math.max(1,W/2-core),step=Math.max(2,Math.round(W/96));
  for(let x=0;x<W;x+=step){
   const d=Math.abs(x+step/2-cx),f=d<=core?0:Math.min(1,(d-core)/half);
   const lift=P.flap*f*f*H*.13;
   g.drawImage(img,x,0,Math.min(step,W-x),H,x,-lift,Math.min(step,W-x)+.6,H);}
  return;}
 if(rig.wave&&P.wave){
  const y0=Math.round(rig.wave*H),step=Math.max(2,Math.round(H/96));
  g.drawImage(img,0,0,W,y0,0,0,W,y0+.6);
  for(let y=y0;y<H;y+=step){
   const k=(y-y0)/Math.max(1,H-y0),dx=P.wave*k*W*.028*Math.sin(VP_TAU*(t/1900)+k*4.2);
   g.drawImage(img,0,y,W,Math.min(step,H-y),dx,y,W,Math.min(step,H-y)+.6);}
  return;}
 g.drawImage(img,0,0,W,H);}
function vpGlow(g,x,y,r,col,a){
 if(a<=0.01)return;
 const gr=g.createRadialGradient(x,y,0,x,y,r);
 gr.addColorStop(0,col);gr.addColorStop(1,'rgba(0,0,0,0)');
 g.globalAlpha=Math.min(1,a);g.fillStyle=gr;g.beginPath();g.arc(x,y,r,0,VP_TAU);g.fill();g.globalAlpha=1;}
/* Particles. (bx,by,bw,bh) is the body box in canvas px; u is one grid unit in px. */
function vpFx(g,fx,t,u,bx,by,bw,bh,amt,seed){
 const kind=fx[0];
 if(amt<=0)return;
 if(kind==='arcs'){
  const col=fx[1],slot=Math.floor(t/90);
  g.save();g.globalCompositeOperation='lighter';g.strokeStyle=col;g.lineCap='round';g.lineJoin='round';
  for(let a=0;a<3;a++){
   const on=vpHash(seed*31+a*977+slot)<.32*amt;if(!on)continue;
   const ang=VP_TAU*vpHash(seed*7+a*131+Math.floor(t/420)),cx=bx+bw/2,cy=by+bh*.5;
   const r0=Math.max(bw,bh)*.36,r1=Math.max(bw,bh)*.56;
   g.lineWidth=Math.max(1,u*.32);g.globalAlpha=.85;g.beginPath();
   for(let s=0;s<=5;s++){const rr=r0+(r1-r0)*s/5,j=(vpHash(slot*13+a*7+s)-.5)*.5;
    const x=cx+Math.cos(ang+j)*rr,y=cy+Math.sin(ang+j)*rr*.85;s?g.lineTo(x,y):g.moveTo(x,y);}
   g.stroke();}
  g.restore();return;}
 const pts=fx[1]||[],col=fx[2]||'#ffffff';
 g.save();
 if(kind==='smoke'){
  for(let p=0;p<pts.length;p++)for(let k=0;k<4;k++){
   const ph=((t/2600)+k/4+vpHash(p*17)*.5)%1,sw=vpHash(p*5+k)*2-1;
   const x=bx+pts[p][0]*bw+sw*u*1.2*ph+u*1.5*ph,y=by+pts[p][1]*bh-u*9*ph,r=u*(1+2.6*ph);
   g.globalAlpha=.32*(1-ph)*Math.min(1,ph*6)*Math.min(1,amt);g.fillStyle=col;
   g.beginPath();g.arc(x,y,r,0,VP_TAU);g.fill();}}
 else if(kind==='embers'){
  g.globalCompositeOperation='lighter';
  for(let p=0;p<pts.length;p++)for(let k=0;k<6;k++){
   const ph=((t/1700)+k/6+vpHash(p*11+k)*.3)%1,sw=Math.sin(VP_TAU*(ph*1.5+vpHash(k*3+p)));
   const x=bx+pts[p][0]*bw+sw*u*1.4,y=by+pts[p][1]*bh-u*8*ph;
   g.globalAlpha=(1-ph)*Math.min(1,amt);g.fillStyle=col;g.fillRect(x-u*.3,y-u*.3,u*.6,u*.6);}}
 else if(kind==='bubbles'){
  g.strokeStyle=col;g.lineWidth=Math.max(1,u*.22);
  for(let p=0;p<pts.length;p++)for(let k=0;k<3;k++){
   const ph=((t/2300)+k/3+vpHash(p*19+k)*.4)%1,x=bx+pts[p][0]*bw+Math.sin(VP_TAU*ph*2+p)*u*.8;
   const y=by+pts[p][1]*bh-u*12*ph,r=u*(.35+.5*vpHash(p*3+k));
   g.globalAlpha=.75*(1-ph)*Math.min(1,amt);g.beginPath();g.arc(x,y,r,0,VP_TAU);g.stroke();}}
 else if(kind==='glints'){
  g.globalCompositeOperation='lighter';g.fillStyle=col;
  for(let p=0;p<pts.length;p++){
   const ph=((t/(1600+400*p))+vpHash(p*23))%1,a=Math.max(0,Math.sin(Math.PI*Math.min(1,ph*3)));
   if(a<=0)continue;const x=bx+pts[p][0]*bw,y=by+pts[p][1]*bh,r=u*1.6*a;
   g.globalAlpha=Math.min(1,amt)*a;g.beginPath();
   g.moveTo(x,y-r);g.lineTo(x+r*.18,y-r*.18);g.lineTo(x+r,y);g.lineTo(x+r*.18,y+r*.18);
   g.lineTo(x,y+r);g.lineTo(x-r*.18,y+r*.18);g.lineTo(x-r,y);g.lineTo(x-r*.18,y-r*.18);g.closePath();g.fill();}}
 else if(kind==='motes'){
  g.globalCompositeOperation='lighter';g.fillStyle=col;
  for(let k=0;k<9;k++){
   const ang=VP_TAU*(vpHash(k*7)+t/(5200+700*k)),rr=(.38+.2*vpHash(k*13))*Math.max(bw,bh);
   const x=bx+pts[0][0]*bw+Math.cos(ang)*rr,y=by+pts[0][1]*bh+Math.sin(ang)*rr*.6;
   g.globalAlpha=(.35+.45*(.5+.5*Math.sin(VP_TAU*t/900+k)))*Math.min(1,amt);
   g.beginPath();g.arc(x,y,u*.35,0,VP_TAU);g.fill();}}
 g.restore();}
/* The work sign beside the pet: what kind of work, or what state needs a look. Each pet wears
   its own emblem (a rune stone, a gear, a crystal, a bubble, a lantern, a riveted plate, a shield,
   a facet, an eye, a flame) in its own colour, and the glyph inside is alive: pages turn, the lens
   sweeps, the pick swings, code is typed, the flask bubbles, the eye looks about, the tick draws
   itself. Vector, so sharp at any size; the pet stays the main picture. `still` (reduced motion)
   draws the same sign at rest. */
const VP_SIGN={
 beetle:['hex','#ffcf6a','#17181d'],raven:['gear','#e8b860','#1b1712'],golem:['shard','#7fe3ff','#12201b'],
 tide:['bubble','#a5ecff','#0c2836'],spark:['lantern','#ffd27a','#211808'],rascal:['plate','#ffb347','#221d1a'],
 prime:['shield','#6fe9ff','#1f1808'],prism:['facet','#c9f6ff','#151a3c'],veil:['eye','#dba4ff','#160a26'],
 ember:['flame','#ff9a3c','#1c1210']};
const VP_SIGN_TONE={ask:'#ffb057',done:'#5fe0a0',fail:'#ff7b7b'};
function vpSignFrame(g,shape,r,t){
 g.beginPath();
 if(shape==='hex'){for(let k=0;k<6;k++){const a=VP_TAU*k/6+VP_TAU/12;k?g.lineTo(Math.cos(a)*r*1.08,Math.sin(a)*r*1.08):g.moveTo(Math.cos(a)*r*1.08,Math.sin(a)*r*1.08);}g.closePath();}
 else if(shape==='gear'){const n=10,spin=t/5200;for(let k=0;k<n*4;k++){const a=VP_TAU*k/(n*4)+spin,rr=(k%4<2)?r*1.1:r*.9;k?g.lineTo(Math.cos(a)*rr,Math.sin(a)*rr):g.moveTo(Math.cos(a)*rr,Math.sin(a)*rr);}g.closePath();}
 else if(shape==='shard'){g.moveTo(0,-r*1.25);g.lineTo(r*.9,-r*.4);g.lineTo(r*.66,r*.95);g.lineTo(-r*.66,r*.95);g.lineTo(-r*.9,-r*.4);g.closePath();}
 else if(shape==='lantern'){const w=r*.92;g.moveTo(-w+r*.3,-w);g.lineTo(w-r*.3,-w);g.quadraticCurveTo(w,-w,w,-w+r*.3);g.lineTo(w,w-r*.3);g.quadraticCurveTo(w,w,w-r*.3,w);g.lineTo(-w+r*.3,w);g.quadraticCurveTo(-w,w,-w,w-r*.3);g.lineTo(-w,-w+r*.3);g.quadraticCurveTo(-w,-w,-w+r*.3,-w);g.closePath();}
 else if(shape==='plate'){for(let k=0;k<8;k++){const a=VP_TAU*k/8+VP_TAU/16;k?g.lineTo(Math.cos(a)*r*1.08,Math.sin(a)*r*1.08):g.moveTo(Math.cos(a)*r*1.08,Math.sin(a)*r*1.08);}g.closePath();}
 else if(shape==='shield'){g.moveTo(-r*.95,-r*.95);g.lineTo(r*.95,-r*.95);g.lineTo(r*.95,r*.05);g.quadraticCurveTo(r*.9,r*.9,0,r*1.2);g.quadraticCurveTo(-r*.9,r*.9,-r*.95,r*.05);g.closePath();}
 else if(shape==='facet'){g.moveTo(0,-r*1.25);g.lineTo(r*1.05,0);g.lineTo(0,r*1.25);g.lineTo(-r*1.05,0);g.closePath();}
 else if(shape==='eye'){g.moveTo(-r*1.3,0);g.quadraticCurveTo(0,-r*1.75,r*1.3,0);g.quadraticCurveTo(0,r*1.75,-r*1.3,0);g.closePath();}
 else if(shape==='flame'){g.moveTo(0,-r*1.4);g.bezierCurveTo(r*.3,-r*.7,r,-r*.3,r,r*.25);g.bezierCurveTo(r,r*.85,r*.5,r*1.05,0,r*1.05);g.bezierCurveTo(-r*.5,r*1.05,-r,r*.85,-r,r*.25);g.bezierCurveTo(-r,-r*.3,-r*.3,-r*.7,0,-r*1.4);g.closePath();}
 else{g.arc(0,0,r,0,VP_TAU);}}
/* The frame's own detail, after the fill: rivets, facets, a highlight, a handle. */
function vpSignTrim(g,shape,r,u,col){
 g.strokeStyle=col;g.fillStyle=col;g.lineWidth=Math.max(1,u*.22);g.globalAlpha*=.55;g.beginPath();
 if(shape==='plate'){for(let k=0;k<4;k++){const a=VP_TAU*k/4+VP_TAU/8;g.moveTo(Math.cos(a)*r*.86+u*.28,Math.sin(a)*r*.86);g.arc(Math.cos(a)*r*.86,Math.sin(a)*r*.86,u*.28,0,VP_TAU);}g.fill();}
 else if(shape==='bubble'){g.arc(0,0,r*.8,-2.5,-1.7);g.stroke();}
 else if(shape==='facet'){g.moveTo(0,-r*1.25);g.lineTo(-r*.42,0);g.lineTo(0,r*1.25);g.moveTo(-r*1.05,0);g.lineTo(-r*.42,0);g.stroke();}
 else if(shape==='shard'){g.moveTo(0,-r*1.25);g.lineTo(-r*.3,-r*.3);g.lineTo(-r*.66,r*.95);g.moveTo(-r*.9,-r*.4);g.lineTo(-r*.3,-r*.3);g.stroke();}
 else if(shape==='lantern'){g.arc(0,-r*.92,r*.42,Math.PI,VP_TAU);g.stroke();}
 else if(shape==='hex'){for(let k=0;k<6;k++){const a=VP_TAU*k/6+VP_TAU/12;g.moveTo(Math.cos(a)*r*.86,Math.sin(a)*r*.86);g.lineTo(Math.cos(a)*r*1.0,Math.sin(a)*r*1.0);}g.stroke();}
 else if(shape==='shield'){g.moveTo(-r*.7,-r*.7);g.lineTo(r*.7,-r*.7);g.stroke();}
 g.globalAlpha/=.55;}
function vpSignGlyph(g,kind,L,u,t,still,col){
 const w=(ms,ph)=>still?0:Math.sin(VP_TAU*(t/ms+(ph||0)));
 g.strokeStyle=col;g.fillStyle=col;g.lineCap='round';g.lineJoin='round';g.lineWidth=Math.max(1,u*.5);
 g.beginPath();
 if(kind==='read'){ /* an open book; a page lifts and turns */
  g.moveTo(0,-L*.5);g.lineTo(-L*.95,-L*.75);g.lineTo(-L*.95,L*.6);g.lineTo(0,L*.8);g.lineTo(L*.95,L*.6);g.lineTo(L*.95,-L*.75);g.closePath();
  g.moveTo(0,-L*.5);g.lineTo(0,L*.8);g.stroke();
  const p=still?0:(t%1500)/1500,k=p<.45?Math.cos(Math.PI*p/.45):-1;
  g.beginPath();g.lineWidth=Math.max(1,u*.32);g.moveTo(0,-L*.5);g.lineTo(L*.9*k,-L*(.72-.3*(1-k*k)));g.stroke();
  g.globalAlpha*=.6;g.beginPath();g.moveTo(-L*.7,-L*.25);g.lineTo(-L*.25,-L*.12);g.moveTo(-L*.7,L*.12);g.lineTo(-L*.25,L*.25);g.stroke();g.globalAlpha/=.6;}
 else if(kind==='search'){ /* a lens that sweeps about, with a glint */
  const ox=L*.22*w(1700),oy=L*.16*w(1700,.25);
  g.arc(-L*.2+ox,-L*.2+oy,L*.55,0,VP_TAU);g.stroke();g.beginPath();g.lineWidth=Math.max(1,u*.7);
  g.moveTo(L*.22+ox,L*.22+oy);g.lineTo(L*.85+ox,L*.85+oy);g.stroke();
  g.beginPath();g.lineWidth=Math.max(1,u*.28);g.arc(-L*.2+ox,-L*.2+oy,L*.3,-2.6+w(1700)*.6,-1.8+w(1700)*.6);g.stroke();}
 else if(kind==='dig'){ /* a pick that swings and throws chips */
  const p=still?.3:(t%760)/760,sw=p<.6?-1.0+1.7*Math.pow(p/.6,3):.7-1.7*(p-.6)/.4;
  g.save();g.translate(-L*.1,L*.1);g.rotate(sw*.55);
  g.moveTo(-L*.75,L*.75);g.lineTo(L*.45,-L*.45);g.stroke();
  g.beginPath();g.moveTo(-L*.15,-L*.85);g.quadraticCurveTo(L*.75,-L*.75,L*.85,L*.15);g.stroke();g.restore();
  if(!still&&p>.6){const k=(p-.6)/.4;g.globalAlpha*=1-k;for(let c=0;c<3;c++){g.beginPath();g.arc(L*(.55+.25*c*k),L*(.75-.9*k+.25*c*k*k*2),u*.26,0,VP_TAU);g.fill();}g.globalAlpha/=Math.max(.001,1-k);}}
 else if(kind==='code'){ /* brackets; the line between them is typed out, cursor blinking */
  g.moveTo(-L*.45,-L*.7);g.lineTo(-L*.98,0);g.lineTo(-L*.45,L*.7);g.moveTo(L*.45,-L*.7);g.lineTo(L*.98,0);g.lineTo(L*.45,L*.7);g.stroke();
  const n=still?3:Math.floor((t%1400)/280);
  for(let c=0;c<Math.min(3,n);c++){g.beginPath();g.arc(-L*.3+c*L*.3,0,u*.3,0,VP_TAU);g.fill();}
  if(!still&&n<3&&Math.floor(t/140)%2===0){g.beginPath();g.lineWidth=Math.max(1,u*.3);g.moveTo(-L*.3+n*L*.3,-L*.22);g.lineTo(-L*.3+n*L*.3,L*.22);g.stroke();}}
 else if(kind==='test'){ /* a flask: the liquid sloshes and bubbles rise */
  g.moveTo(-L*.3,-L*.85);g.lineTo(-L*.3,-L*.15);g.lineTo(-L*.85,L*.8);g.lineTo(L*.85,L*.8);g.lineTo(L*.3,-L*.15);g.lineTo(L*.3,-L*.85);g.stroke();
  g.beginPath();g.moveTo(-L*.5,-L*.85);g.lineTo(L*.5,-L*.85);g.stroke();
  const sl=L*.1*w(900);g.beginPath();g.globalAlpha*=.55;g.moveTo(-L*.6,L*.36+sl);g.lineTo(L*.6,L*.36-sl);g.lineTo(L*.78,L*.72);g.lineTo(-L*.78,L*.72);g.closePath();g.fill();g.globalAlpha/=.55;
  if(!still)for(let c=0;c<3;c++){const ph=((t/1100)+c/3)%1;g.globalAlpha*=1-ph;g.beginPath();g.arc(L*(-.22+.22*c)+L*.08*Math.sin(ph*9+c),L*.3-L*1.0*ph,u*(.2+.16*c),0,VP_TAU);g.stroke();g.globalAlpha/=Math.max(.001,1-ph);}}
 else if(kind==='watch'){ /* an eye that looks about and blinks */
  const bl=still?1:(t%3100<150?Math.max(.12,Math.abs(Math.cos(Math.PI*(t%3100)/150))):1),px=L*.32*w(2600);
  g.moveTo(-L*.98,0);g.quadraticCurveTo(0,-L*1.1*bl,L*.98,0);g.quadraticCurveTo(0,L*1.1*bl,-L*.98,0);g.stroke();
  if(bl>.5){g.beginPath();g.arc(px,0,L*.3,0,VP_TAU);g.fill();}}
 else if(kind==='ask'){ /* waiting on you: the mark hops and a ring goes out */
  const hop=still?0:Math.max(0,w(900))*L*.22;
  g.lineWidth=Math.max(1,u*.7);g.moveTo(0,-L*.95-hop);g.lineTo(0,L*.2-hop);g.stroke();g.beginPath();g.arc(0,L*.72,u*.5,0,VP_TAU);g.fill();
  if(!still){const ph=(t%1400)/1400;g.globalAlpha*=(1-ph)*.7;g.beginPath();g.lineWidth=Math.max(1,u*.25);g.arc(0,0,L*(1.1+1.2*ph),0,VP_TAU);g.stroke();g.globalAlpha/=Math.max(.001,(1-ph)*.7);}}
 else if(kind==='done'){ /* the tick draws itself, then a sparkle */
  const k=still?1:Math.min(1,t/420),a=[-L*.85,L*.05],b=[-L*.2,L*.65],c=[L*.9,-L*.65];
  g.lineWidth=Math.max(1,u*.75);g.moveTo(a[0],a[1]);
  if(k<.4){const q=k/.4;g.lineTo(a[0]+(b[0]-a[0])*q,a[1]+(b[1]-a[1])*q);}
  else{const q=(k-.4)/.6;g.lineTo(b[0],b[1]);g.lineTo(b[0]+(c[0]-b[0])*q,b[1]+(c[1]-b[1])*q);}
  g.stroke();
  if(!still&&t>380&&t<1100){const q=(t-380)/720,rr=L*.5*Math.sin(Math.PI*q);g.beginPath();g.moveTo(L*.9,-L*.95-rr);g.lineTo(L*.9,-L*.95+rr);g.moveTo(L*.9-rr,-L*.95);g.lineTo(L*.9+rr,-L*.95);g.lineWidth=Math.max(1,u*.3);g.stroke();}}
 else if(kind==='fail'){ /* a cross that shudders once */
  const sh=still?0:(t<420?Math.sin(t/28)*(1-t/420)*L*.2:0);
  g.lineWidth=Math.max(1,u*.7);g.moveTo(-L*.65+sh,-L*.65);g.lineTo(L*.65+sh,L*.65);g.moveTo(L*.65+sh,-L*.65);g.lineTo(-L*.65+sh,L*.65);g.stroke();}
 else if(kind==='wait'){ /* an hourglass that turns over; sand runs */
  const p=still?0:(t%2600)/2600,turn=p>.82?Math.PI*(p-.82)/.18:0;
  g.save();g.rotate(turn);g.moveTo(-L*.6,-L*.85);g.lineTo(L*.6,-L*.85);g.lineTo(-L*.6,L*.85);g.lineTo(L*.6,L*.85);g.closePath();g.stroke();
  const q=Math.min(1,p/.82);g.globalAlpha*=.6;g.beginPath();g.moveTo(-L*.4*(1-q),-L*.75+L*.6*q);g.lineTo(L*.4*(1-q),-L*.75+L*.6*q);g.lineTo(0,-L*.08);g.closePath();g.fill();
  g.beginPath();g.moveTo(-L*.5*q,L*.8);g.lineTo(L*.5*q,L*.8);g.lineTo(0,L*.8-L*.6*q);g.closePath();g.fill();g.globalAlpha/=.6;g.restore();}
 else if(kind==='retry'){ /* an arrow going round again */
  g.save();g.rotate(still?0:VP_TAU*(t%1500)/1500);g.arc(0,0,L*.75,-.3,VP_TAU*.8);g.stroke();g.beginPath();
  const ex=Math.cos(VP_TAU*.8)*L*.75,ey=Math.sin(VP_TAU*.8)*L*.75;g.moveTo(ex+L*.45,ey-L*.05);g.lineTo(ex,ey);g.lineTo(ex+L*.05,ey+L*.5);g.stroke();g.restore();}
 else{ /* run: a gear turning against a smaller one */
  const sp=still?0:t/620;
  const cog=(cx,cy,rr,n,rot)=>{g.save();g.translate(cx,cy);g.rotate(rot);for(let k=0;k<n;k++){g.save();g.rotate(VP_TAU*k/n);g.fillRect(-u*.38,-rr*1.32,u*.76,rr*.5);g.restore();}
   g.beginPath();g.lineWidth=Math.max(1,u*.45);g.arc(0,0,rr*.78,0,VP_TAU);g.stroke();g.restore();};
  cog(-L*.2,L*.12,L*.62,8,sp);cog(L*.62,-L*.6,L*.3,6,-sp*1.6+.3);}}
function vpBadge(g,kind,u,W,t,id,still,pop){
 const sg=VP_SIGN[id]||['ring','#5eead4','#10121a'],shape=sg[0],tone=VP_SIGN_TONE[kind]||sg[1];
 const r=u*4.5,x=W-r-u*1.1,y=W-r-u*1.1,s=(pop==null?1:pop)*(kind==='ask'&&!still?1+.05*Math.sin(VP_TAU*t/700):1);
 g.save();g.translate(x,y);g.scale(s,s);
 /* a soft glow in the pet's colour, then the emblem */
 const gl=g.createRadialGradient(0,0,r*.6,0,0,r*1.7);gl.addColorStop(0,tone);gl.addColorStop(1,'rgba(0,0,0,0)');
 g.globalAlpha=still?.22:.2+.1*Math.sin(VP_TAU*t/1800);g.fillStyle=gl;g.beginPath();g.arc(0,0,r*1.7,0,VP_TAU);g.fill();g.globalAlpha=1;
 vpSignFrame(g,shape,r,still?0:t);
 const body=g.createLinearGradient(0,-r,0,r);body.addColorStop(0,sg[2]);body.addColorStop(1,'#07080c');
 g.fillStyle=body;g.globalAlpha=.94;g.fill();g.globalAlpha=1;
 g.strokeStyle=tone;g.lineWidth=Math.max(1,u*.5);g.lineJoin='round';g.stroke();
 vpSignTrim(g,shape,r,u,tone);
 const L=r*(shape==='eye'||shape==='facet'||shape==='shard'?.42:.5);
 g.save();if(shape==='flame')g.translate(0,r*.12);if(shape==='shield')g.translate(0,-r*.05);
 vpSignGlyph(g,kind,L,u,t,!!still,tone);g.restore();
 g.restore();g.globalAlpha=1;}
function vpDots(g,u,W,t,reduced,id){
 const tint=(VP_SIGN[id]||[0,'#e7ecf3'])[1];
 for(let k=0;k<3;k++){
  const a=reduced?.8:.35+.65*Math.max(0,Math.sin(VP_TAU*(t/1200-k/6)));
  g.globalAlpha=a;g.fillStyle=tint;g.shadowColor=tint;g.shadowBlur=u*1.2;g.beginPath();g.arc(W-u*(9-k*3),u*4.2-(reduced?0:u*.6*a),u*.9,0,VP_TAU);g.fill();}
 g.shadowBlur=0;
 g.globalAlpha=1;}
/* Motion: one rig per pet, and one tracker per canvas so each drawn pet carries on from the pose it
   last showed (VoolPetRig's continuity rule). The tracker's clock starts at the state's elapsed time,
   so a pet's first frame depends only on what it is doing and for how long; after that it runs on
   the page clock. */
const VP_RIGS={},VP_TRACK=(typeof WeakMap!=='undefined')?new WeakMap():null;
function vpRig(id){
 if(VP_RIGS[id]===undefined){
  let r=null;
  try{if(typeof VoolPetRig!=='undefined'&&VoolPetRig.rigs[id]&&VP_RIG_META[id])r=VoolPetRig.rigs[id](VP_RIG_META[id]);}catch(e){r=null;}
  VP_RIGS[id]=r;}
 return VP_RIGS[id];}
/* Where a drawn pet is in its posture chain (for the host's own checks; drawing never needs it). */
function vpPostureOf(canvas){
 const k=VP_TRACK&&canvas?VP_TRACK.get(canvas):null;
 return k?{pet:k.id,posture:k.tr.posture,to:k.tr.step?k.tr.step.to:null}:null;}
function vpClock(){return (typeof performance!=='undefined'&&performance.now)?performance.now():Date.now();}
function vpDrawRig(g,id,def,rig,state,activity,t,opts,cw,ch,u){
 const reduced=!!opts.reduced,R=def.rig||{},meta=VP_RIG_META[id];
 const P=vpPose(state,activity,t,reduced,R);
 const W=meta.size[0],H=meta.size[1],fit=(R.size||40)*u/Math.max(W,H);
 const bw=W*fit,bh=H*fit,bx=(cw-bw)/2,floor=R.float?(ch-bh)/2+bh+u*1.5:ch-u*1.6,by=floor-bh;
 const key=g.canvas||g,host=(typeof opts.now==='number'&&isFinite(opts.now))?opts.now:vpClock();
 let k=VP_TRACK?VP_TRACK.get(key):null;
 if(!k||k.id!==id){
  k={id:id,off:host-t,fresh:true};k.tr=VoolPetRig.newTracker(rig,t,state,activity,t);
  if(VP_TRACK)VP_TRACK.set(key,k);}
 const now=host-k.off,tr=k.tr;
 // particles quieten while it sleeps
 const asleep=tr.posture==='lie'||(tr.step&&tr.step.to==='lie');
 // Lightning arcs flash in and out at random-looking moments; on a pet that moves smoothly they
 // read as glitches, so the moving pets leave them out (their runes and gems glow instead).
 const amt=P.fx*(asleep?.15:1),fxs=(R.fx||[]).filter(function(f){return f[0]!=='arcs';});
 for(let i=0;i<fxs.length;i++)if(fxs[i][0]==='smoke'&&!reduced)vpFx(g,fxs[i],now,u,bx,by,bw,bh,amt,i+1);
 if(R.aura){g.save();g.globalCompositeOperation='lighter';
  vpGlow(g,bx+bw/2,by+bh*.45,Math.max(bw,bh)*.62,R.aura,(.28+.12*P.glow)*(asleep?.5:1));g.restore();}
 VoolPetRig.frame(g,rig,VP_RIG_IMG[id].imgs,tr,state,activity,t,now,{x:bx,y:by,s:fit},reduced);
 g.setTransform(1,0,0,1,0,0);
 for(let i=0;i<fxs.length;i++)if(fxs[i][0]!=='smoke'&&!reduced)vpFx(g,fxs[i],now,u,bx,by,bw,bh,amt,i+1);
 if(P.burst>=0&&P.burst<1){g.save();g.globalCompositeOperation='lighter';g.fillStyle='#fff3b0';
  for(let q=0;q<10;q++){const a=VP_TAU*q/10,rr=(.25+.55*P.burst)*Math.max(bw,bh);
   g.globalAlpha=1-P.burst;g.beginPath();g.arc(bx+bw/2+Math.cos(a)*rr,floor-bh*.5+Math.sin(a)*rr*.8,u*.7,0,VP_TAU);g.fill();}
  g.restore();}
 if(P.dots)vpDots(g,u,cw,t,reduced,id);
 if(P.badge){
  // The sign pops in when the work changes, and its own animation starts from there.
  if(!k.sign||k.sign.kind!==P.badge)k.sign={kind:P.badge,t0:(k.fresh?now-t:now)};
  const st=Math.max(0,now-k.sign.t0),q=Math.min(1,st/240),c1=2.2;
  vpBadge(g,P.badge,u,cw,st,id,reduced,reduced?1:1+(c1+1)*Math.pow(q-1,3)+c1*Math.pow(q-1,2));
 }else k.sign=null;
 k.fresh=false;
 g.globalAlpha=1;g.globalCompositeOperation='source-over';
 g.setTransform(u,0,0,u,0,0);
 return true;}
/* One frame of a painted pet into g (any canvas size). False when the picture is not decoded
   (yet, or ever): the caller then shows the fallback drawing instead of an empty box. */
function vcwDrawPainted(g,id,state,activity,elapsed,opts){
 opts=opts||{};
 const def=VP_PETS[id];
 if(!def)return false;
 vpLoad(id);
 if(!vpReady(id))return false;
 const img=VP_IMG[id],rig=def.rig||{},t=Math.max(0,Number(elapsed)||0),reduced=!!opts.reduced;
 const cw=(g.canvas&&g.canvas.width)||48,ch=(g.canvas&&g.canvas.height)||48,u=cw/48;
 g.setTransform(1,0,0,1,0,0);g.clearRect(0,0,cw,ch);
 g.imageSmoothingEnabled=true;try{g.imageSmoothingQuality='high';}catch(e){}
 const moving=vpRig(id);
 if(moving&&vpRigReady(id))return vpDrawRig(g,id,def,moving,state,activity,t,opts,cw,ch,u);
 const P=vpPose(state,activity,t,reduced,rig);
 const iw=img.naturalWidth,ih=img.naturalHeight,fit=(rig.size||40)*u/Math.max(iw,ih);
 const bw=iw*fit,bh=ih*fit,bx=(cw-bw)/2+P.dx*u,floor=rig.float?(ch-bh)/2+bh+u*1.5:ch-u*1.6;
 const by=floor-bh+P.dy*u;
 // particles behind the body (smoke rises behind; sparks and glints go in front)
 const fxs=rig.fx||[],amt=P.fx;
 for(let i=0;i<fxs.length;i++)if(fxs[i][0]==='smoke'&&!reduced)vpFx(g,fxs[i],t,u,bx,by,bw,bh,amt,i+1);
 // a soft aura behind the dusk creatures, so a dark pet still reads on a dark window
 if(rig.aura){g.save();g.globalCompositeOperation='lighter';
  vpGlow(g,bx+bw/2,by+bh*.45,Math.max(bw,bh)*.62,rig.aura,(.28+.12*P.glow)*(1-P.grey));g.restore();}
 g.save();
 const ax=bx+bw/2,ay=floor+P.dy*u;
 g.translate(ax,ay);g.rotate(P.rot);g.scale(P.sx,P.sy);g.translate(-bw/2,-bh);
 g.scale(fit,fit);
 g.globalAlpha=1-P.dim;
 vpBody(g,img,iw,ih,rig,P,t);
 if(P.grey>0){g.globalAlpha=P.grey;vpBody(g,VP_GREY[id]||img,iw,ih,rig,P,t);}
 g.restore();g.globalAlpha=1;
 // glows: eyes, cores, flames
 const gl=rig.glow||[];
 if(gl.length){g.save();g.globalCompositeOperation='lighter';
  for(let i=0;i<gl.length;i++){
   const x=ax+(gl[i][0]-.5)*bw*P.sx,y=ay-(1-gl[i][1])*bh*P.sy,r=gl[i][2]*Math.max(bw,bh)*1.6;
   const flick=reduced?1:.85+.15*Math.sin(VP_TAU*(t/(700+97*i)));
   vpGlow(g,x,y,r,gl[i][3],P.glow*.55*flick*(1-P.grey));}
  g.restore();}
 for(let i=0;i<fxs.length;i++)if(fxs[i][0]!=='smoke'&&!reduced)vpFx(g,fxs[i],t,u,bx,by,bw,bh,amt,i+1);
 // a finished job: a ring of sparkles opening out once
 if(P.burst>=0&&P.burst<1){g.save();g.globalCompositeOperation='lighter';g.fillStyle='#fff3b0';
  for(let k=0;k<10;k++){const a=VP_TAU*k/10,rr=(.25+.55*P.burst)*Math.max(bw,bh);
   g.globalAlpha=1-P.burst;g.beginPath();g.arc(ax+Math.cos(a)*rr,ay-bh*.5+Math.sin(a)*rr*.8,u*.7,0,VP_TAU);g.fill();}
  g.restore();}
 if(P.dots)vpDots(g,u,cw,t,reduced,id);
 if(P.badge)vpBadge(g,P.badge,u,cw,t,id,reduced,1);
 g.globalAlpha=1;g.globalCompositeOperation='source-over';
 // Leave the context in 48-unit space so a host's extras (idle zzz) land where they always did.
 g.setTransform(u,0,0,u,0,0);
 return true;}
/* Frame cadence for painted pets, on time and never on the screen's refresh rate: 20 frames a
   second at rest and while waiting (a calm breath costs little), 30 while working, frozen under
   reduced motion, and the one-shot states (done, problem, stopped) hold their last pose. */
const VP_FRAME_MS={idle:50,waiting:50,unknown:50};
function vpFrameAt(state,elapsed,reduced){
 if(reduced)return 0;
 const ms=Math.max(0,Number(elapsed)||0),step=VP_FRAME_MS[state]||33;
 if(VP_ONE_SHOT[state])return Math.floor(Math.min(ms,VP_ONE_SHOT[state])/step);
 return Math.floor(ms/step);}
"""


def painted_js() -> str:
    """The painted renderer with its art and registry, for the shared companion world."""
    registry = painted_registry()
    art = {pet: "data:image/webp;base64," + PET_ART[pet] for pet in PAINTED_ROSTER}
    rig_art = {pet: {part: "data:image/webp;base64," + b64 for part, b64 in RIG_ART[pet].items()}
               for pet in PAINTED_ROSTER if pet in RIG_ART}
    return (
        "const VP_ART=" + json.dumps(art) + ";\n"
        + "const VP_PETS=" + json.dumps(registry, ensure_ascii=False) + ";\n"
        + "const VP_ALIASES=" + json.dumps(PAINTED_ALIASES) + ";\n"
        + "const VP_RIG_ART=" + json.dumps(rig_art) + ";\n"
        + "const VP_RIG_META=" + json.dumps({pet: RIG_META[pet] for pet in PAINTED_ROSTER if pet in RIG_META}) + ";\n"
        # The rig kit lives in its own object, not on window: hosts that run this under node or
        # in a module get the same names, and the page gets no new global but VoolPetRig.
        + "const VP_RIG_ROOT={rigs:{}};\n"
        + RIG_ENGINE_JS + "\n"
        + "const VoolPetRig=VP_RIG_ROOT.VoolPetRig;\n"
        + "".join(RIG_PETS_JS[pet] + "\n" for pet in PAINTED_ROSTER if pet in RIG_PETS_JS)
        + _PAINTED_ENGINE_JS
    )
