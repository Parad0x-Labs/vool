"""Authored 48x48 pixel-art companions in the original VOOL detailed family.

The drawing routines here are a direct port of the approved prototypes
(``missions/pet-original-style-20260930/references/collection.html`` lines 324-826). The
approved art is procedural drawing code -- parameterised ``rect``/``pixelLine`` calls -- not
palette-indexed bitmaps, so it is ported as code to keep creature coordinates intact.
External ground bars and cast shadows are omitted.
Re-typing it as bitmap rows is exactly how the quartet gets simplified.

``STYLE-CONTRACT.md`` in this mission's evidence folder is the authority for every rule applied
here. The rejected ``pet-state-redesign`` candidate is not an input.
"""

from __future__ import annotations

# The ten-member roster, in one place so the renderer's JS registry, the payload normaliser and
# the chooser cannot drift apart. The four originals keep stable ids so saved positions, packs
# and per-chat state survive the change.
COMPANION_ROSTER = (
    "beetle", "raven", "golem", "tide",
    "spark", "rascal", "prime", "prism", "veil", "ember",
)
COMPANION_ORIGINALS = ("beetle", "raven", "golem", "tide")

COMPANION_ART_JS = r"""/* VOOL companions -- authored 48x48 detailed pixel-art family (original quartet + six).
   Style authority: pet-original-style-20260930/STYLE-CONTRACT.md. */
const A_EASE_OUT=v=>1-Math.pow(1-Math.max(0,Math.min(1,v)),3);
const A_EASE_IO=v=>{const t=Math.max(0,Math.min(1,v));return t<.5?4*t*t*t:1-Math.pow(-2*t+2,3)/2;};
/* Recoil table, copied from the beetle's authored failure shake. Bounded, then held. */
const A_RECOIL=[0,-1,1,0,0,-1,0,0];

/* ---- shared primitives: the approved drawing grammar ------------------------------- */
/* An unparseable colour is NOT skipped: the assignment is made and the canvas ignores it,
   keeping the previous fillStyle -- which is exactly what the prototype relies on (the golem
   asks for a metalLight its palette does not have, and the rect repaints in the carried-over
   tone). Skipping the rect instead would change the approved picture. */
function aRect(g,x,y,w,h,c){g.fillStyle=c;g.fillRect(Math.round(x),Math.round(y),Math.max(0,Math.round(w)),Math.max(0,Math.round(h)));}
function aLine(g,x0,y0,x1,y1,c,w){w=w||1;const s=Math.max(Math.abs(x1-x0),Math.abs(y1-y0),1),h=(w-1)>>1;
 for(let i=0;i<=s;i++){const t=i/s;aRect(g,x0+(x1-x0)*t-h,y0+(y1-y0)*t-h,w,w,c);}}
function aMir(side,x,y,dx,dy){return side<0?{x:48-x+(dx||0),y:y+(dy||0)}:{x:x+(dx||0),y:y+(dy||0)};}
/* Outline-then-fill: every limb carries a 1px dark rail on both sides. */
function aLimb(g,x0,y0,x1,y1,p,w){aLine(g,x0,y0,x1,y1,p.outline,w||3);aLine(g,x0,y0,x1,y1,p.body,(w||3)-2);}
function aSpark(g,x,y,c,s){s=s||1;aRect(g,x,y,s,1,c);aRect(g,x+(s>>1),y-(s>>1),1,s+1,c);}
function aCheck(g,x,y,c){aLine(g,x,y,x+1,y+1,c,1);aLine(g,x+1,y+1,x+4,y-2,c,1);}
/* One face grammar across the family -- this is what makes ten creatures read as one culture.
   Branches are the prototype's own: happy, focus, low, blink, and a shared default. Note the
   prototype's focus branch IS its default branch, so calm/focus/bright share one eye form;
   that is the approved face language and it is reproduced rather than "improved". */
function aFace(g,p,x,y,e,s){
 s=s||1;const ew=Math.max(2,3*s),eh=Math.max(2,3*s);
 if(e==='happy'){aRect(g,x,y+1,ew,1,p.eye);aRect(g,x+ew-1,y,1,2,p.eye);aRect(g,x+ew+5,y,1,2,p.eye);aRect(g,x+ew+6,y+1,ew,1,p.eye);}
 else if(e==='low'){aRect(g,x,y+1,ew,1,p.outline);aRect(g,x+ew+5,y+1,ew,1,p.outline);}
 else if(e==='blink'){aRect(g,x,y+1,ew,1,p.eye);aRect(g,x+ew+5,y+1,ew,1,p.eye);}
 else{aRect(g,x,y,ew,eh,p.eye);aRect(g,x+1,y,Math.max(1,s),eh,p.outline);
  aRect(g,x+ew+5,y,ew,eh,p.eye);aRect(g,x+ew+6,y,Math.max(1,s),eh,p.outline);}}

/* ---- activity identity ------------------------------------------------------------
   Presentation-only substates derived from the existing typed categories. Six distinct
   work activities, each with its own authored pose -- never inferred from a clock or from
   natural language. Values arrive from the presentation owner as typed category names. */
const A_ACT={READ:'read',SEARCH:'search',DIG:'dig',CODE:'code',RUN:'exec',TEST:'test',WATCH:'watch'};
const A_ACT_CATEGORY={
 UNDERSTANDING:A_ACT.DIG,PLANNING:A_ACT.CODE,WEB_RESEARCH:A_ACT.SEARCH,READING_FILES:A_ACT.READ,
 APPLYING_CHANGES:A_ACT.CODE,GIT_INSPECTION:A_ACT.CODE,TESTING_CI:A_ACT.TEST,RUNNING_TOOLS:A_ACT.RUN,
 OBSERVING:A_ACT.WATCH};
function aActivityFor(category){
 const k=String(category||'').toUpperCase();
 return A_ACT_CATEGORY[k]||null;}
/* Six activities -> six distinct arm poses, so reading is not the same picture as testing. */
function aActArm(a){return a===A_ACT.READ?'read':a===A_ACT.SEARCH?'read':a===A_ACT.DIG?'dig':
 a===A_ACT.CODE?'type':a===A_ACT.RUN?'run':a===A_ACT.TEST?'test':'watch';}

/* ---- the shared pose resolver -----------------------------------------------------
   One scheduler, one pose law, ten personalities. Each creature interprets these numbers in
   its own anatomy and material language, so states stay legible without collapsing into one rig.

   For the original quartet the values below reproduce the PROTOTYPE's own expressions
   exactly (its lift arc, recoil table, head offsets, wing and arm poses, tentacle sway), so a
   null-activity frame is pixel-identical to the approved art. The six activity substates and
   the four states the prototype never modelled (waiting, cancelled, unknown) are the only
   extensions, and they are extensions in the same drawing language. */
function aPoseRaw(state,act,t,reduced,liftAmp){
 t=(reduced?10000:Math.max(0,t|0));
 const tap=Math.floor(t/300)%4;
 const P={raw:state,act:act||null,lift:0,shake:0,headDrop:0,expr:'calm',
  foreleg:[0,0],core:null,working:false,tool:false,wing:'folded',
  headX:0,headY:0,armL:'rest',armR:'rest',toolL:false,toolR:false,
  sway:0,spread:0,tilt:0,width:19,rightRaised:0};
 /* The prototype's tide breathes on this table in EVERY state, not only at rest. It is the
    family\'s one continuous motion and it is amplitude +-1 -- deliberately not a busy loop. */
 P.sway=reduced?0:[0,1,1,0,-1,-1,0,0][Math.floor(t/700)%8];
 /* The prototype's five states, reproduced exactly. */
 if(state==='starting'||state==='thinking'||state==='retry'||(state==='tool'&&!act)){
  P.working=true;P.expr='focus';
  P.foreleg=[0,tap===0?1:tap===1?2:0];P.core=tap<2?null:null;
  P.wing='work';P.tool=true;P.headX=1;P.headY=1;
  P.armL='rest';P.armR='work';P.tilt=1;
  P.core=(tap<2)?'glow':'metalLight';
  return P;}
 if(state==='approval'){
  P.expr='bright';P.foreleg=[2+(Math.floor(t/1000)%2),2+(Math.floor(t/1000)%2)];
  P.wing='raised';P.headY=-1;P.armL='raised';P.armR='raised';
  P.spread=4;P.width=21;P.rightRaised=3;return P;}
 if(state==='success'){
  P.lift=t<700?A_EASE_OUT(t/700)*liftAmp:liftAmp*(1-A_EASE_IO((t-700)/900));
  P.expr='happy';P.wing='raised';P.armL='raised';P.armR='raised';
  P.foreleg=[0,0];P.spread=2;P.width=20;P.rightRaised=2;P.core='metalLight';return P;}
 if(state==='failure'){
  P.shake=t<480?A_RECOIL[Math.floor(t/60)%8]:0;P.headDrop=A_EASE_OUT((t-260)/620);
  P.expr='low';P.armL='droop';P.armR='droop';P.tilt=-1;P.core='danger';
  P.headY=2;                       /* the raven's authored failure head-drop */
  return P;}
 /* States and substates the prototype never modelled. Held still and legible, never jittery. */
 if(state==='waiting'){P.expr='calm';return P;}
 if(state==='cancelled'){P.expr='low';P.armL='cross';P.armR='cross';P.wing='furl';return P;}
 if(state==='unknown'){P.expr=(Math.floor(t/1000)%2)?'blink':'low';P.wing='furl';return P;}
 if(state==='tool'&&act){
  P.expr='focus';P.working=true;
  P.foreleg=aLimbPose(aActArm(act),t);
  P.wing=(act===A_ACT.CODE||act===A_ACT.RUN)?'work':'folded';
  P.tool=(act===A_ACT.CODE||act===A_ACT.RUN);
  P.headX=(act===A_ACT.READ||act===A_ACT.SEARCH||act===A_ACT.CODE)?1:0;
  P.headY=(act===A_ACT.DIG)?1:0;
  P.armL=act===A_ACT.CODE?'work':'rest';
  P.armR=(act===A_ACT.CODE||act===A_ACT.RUN)?'work':'rest';
  P.tilt=(act===A_ACT.DIG||act===A_ACT.RUN)?1:0;
  return P;}
 /* idle holds still. Life arrives as sparse events on a long period, never a loop. */
 P.expr=(!reduced&&t%6400>=6220&&t%6400<6340)?'blink':'calm';
 if(!reduced&&Math.floor(t/900)%8===3)P.headX=1;
 if(!reduced&&Math.floor(t/1000)%5===2)P.tilt=1;
 return P;}
/* Single derived limb field for the six newer companions, which express their poses through
   one arm selector. The quartet reads the specific fields above and is left untouched. */
function aPose(state,act,t,reduced,liftAmp){
 const P=aPoseRaw(state,act,t,reduced,liftAmp);
 P.liftAmp=liftAmp;
 P.arm=P.act?aActArm(P.act)
  :P.armL==='raised'?'raised':P.armL==='cross'?'cross':P.armL==='droop'?'droop'
  :(P.armR==='work'?'type':'rest');
 return P;}

/* Two-limb foreleg lift values from the pose law. Beetle + tide read these. */
function aLimbPose(arm,t){
 const beat=Math.floor(t/300)%4;
 switch(arm){
  case 'reach':return [3,1];
  case 'type':return [1,1];
  case 'run':return beat===0?[1,0]:beat===1?[0,2]:[1,0];
  case 'read':return [0,1];
  case 'dig':return [3,3];
  case 'test':return [2,0];
  case 'raised':return [3,3];
  case 'droop':return [0,0];
  case 'cross':return [1,1];
  case 'temple':return [0,2];
  default:return [0,0];}}


/* ---- palettes: four tones per creature, drawn from the prototype's own sixteen --------
   outline is never pure black, always tinted toward its body. glow/accent stay under ~3% of
   the body so the focal points do not spread. */
/* The quartet palettes are the prototype's own, slot for slot. The golem deliberately has NO
   metal/metalLight: the prototype's golemBlock and golemArm ask for palette.metalLight, which is
   undefined there, so those highlight bands never paint. That is the approved appearance, and
   aRect skips an absent colour, so the golem reproduces it exactly rather than "fixing" it. */
const A_PAL={
 beetle:{body:'#8fa3a8',light:'#d8e0de',dark:'#526a6d',outline:'#17262b',metal:'#9badaa',metalLight:'#e8eee8',accent:'#f4b860',glow:'#f4b860',eye:'#bff6d0',danger:'#ef6f65',shadow:'rgba(23,38,43,.18)'},
 raven:{body:'#304957',light:'#668394',dark:'#1c2d39',outline:'#111d26',metal:'#82959a',metalLight:'#d5dfd4',accent:'#dfa74c',glow:'#ffd36a',eye:'#fff0a6',danger:'#ec6d62',shadow:'rgba(17,29,38,.2)'},
 golem:{body:'#687575',light:'#aeb9ae',dark:'#414c4b',outline:'#202b2b',accent:'#b9a16a',glow:'#f1cf78',eye:'#ffe9a2',danger:'#df6b5b',shadow:'rgba(32,43,43,.2)'},
 tide:{body:'rgba(35,91,117,.82)',light:'rgba(113,188,194,.78)',dark:'rgba(20,52,78,.9)',outline:'#122c43',accent:'#70e0d0',glow:'#c6fff0',eye:'#e8ffff',danger:'#ef777c',shadow:'rgba(18,44,67,.2)'},
 spark:{body:'#7a5a34',light:'#d8ab6a',dark:'#4a361f',outline:'#241a10',metal:'#c79a52',metalLight:'#f5dfa8',accent:'#ffb347',glow:'#fff2c4',eye:'#fff6d8',danger:'#e2603f',shadow:'rgba(36,26,16,.2)'},
 rascal:{body:'#6d5a4a',light:'#b39a80',dark:'#40342a',outline:'#1f1811',metal:'#9a8f7d',metalLight:'#ded4c2',accent:'#d98b4a',glow:'#ffd9a0',eye:'#ffe9c4',danger:'#d1553f',shadow:'rgba(31,24,17,.2)'},
 prime:{body:'#4a4f63',light:'#8f97ad',dark:'#2b2f3c',outline:'#15171f',metal:'#b9a06a',metalLight:'#f0dfae',accent:'#e0c07a',glow:'#fff0c0',eye:'#e8f0ff',danger:'#c8524f',shadow:'rgba(21,23,31,.2)'},
 prism:{body:'#5a6a8a',light:'#a8bcd8',dark:'#37415c',outline:'#1a1f30',metal:'#cfe0f0',metalLight:'#ffffff',accent:'#8fd4ff',glow:'#e8f8ff',eye:'#ffffff',danger:'#e07a7a',shadow:'rgba(26,31,48,.18)'},
 veil:{body:'#4a4152',light:'#8f85a0',dark:'#2b2531',outline:'#171319',metal:'#8c8a99',metalLight:'#cfc9d8',accent:'#a88fd0',glow:'#e6d8ff',eye:'#ded0f5',danger:'#b8567a',shadow:'rgba(23,19,25,.2)'},
 ember:{body:'#8a4a30',light:'#cf8a5c',dark:'#54291a',outline:'#231009',metal:'#a8714a',metalLight:'#e0b184',accent:'#ff7a2f',glow:'#ffcf7a',eye:'#ffe4a8', danger:'#e04a2f',shadow:'rgba(35,16,9,.2)'}};

/* ================= THE ORIGINAL QUARTET -- preserved, not redrawn =================
   Coordinates below are the approved prototype's, unchanged. Poses are the prototype's own
   parameter set (foreleg lift, wing pose, arm pose, tentacle sway) extended to the ten
   authoritative states and six activity substates. Anatomy and material language untouched. */

function aBeetleLeg(g,p,side,points,lift){
 const m=points.map((q,i)=>aMir(side,q[0],q[1],0,i===points.length-1?-lift:0));
 for(let i=0;i<m.length-1;i++){aLine(g,m[i].x,m[i].y,m[i+1].x,m[i+1].y,p.outline,2);aLine(g,m[i].x,m[i].y,m[i+1].x,m[i+1].y,p.metal,1);}
 m.forEach(q=>{aRect(g,q.x-1,q.y-1,3,3,p.outline);aRect(g,q.x,q.y,1,1,p.metalLight);});
 const foot=m[m.length-1];
 aRect(g,foot.x-(side<0?1:2),foot.y,4,1,p.dark);aRect(g,foot.x-(side<0?1:2),foot.y-1,4,1,p.metal);}
function aBeetleAntenna(g,p,side,lift){
 const pts=[aMir(side,15,28),aMir(side,11,23,0,-lift),aMir(side,8,19,0,-lift),aMir(side,7,14,0,-lift)];
 for(let i=0;i<pts.length-1;i++){aLine(g,pts[i].x,pts[i].y,pts[i+1].x,pts[i+1].y,p.outline,2);aLine(g,pts[i].x,pts[i].y,pts[i+1].x,pts[i+1].y,p.metal,1);}
 const tip=pts[3];aRect(g,tip.x-1,tip.y-1,3,3,p.outline);aRect(g,tip.x,tip.y,1,1,p.glow);}

function aBeetle(g,p,P,t){
 const S=P.raw;
 const L=P.lift+P.shake, alift=(S==='approval')?1:0;
 aBeetleAntenna(g,p,-1,alift);aBeetleAntenna(g,p,1,alift);
 aBeetleLeg(g,p,-1,[[13,23],[8,25],[5,30],[3,34]]);aBeetleLeg(g,p,1,[[35,23],[40,25],[43,30],[45,34]]);
 aBeetleLeg(g,p,-1,[[12,28],[7,30],[5,35],[3,38]]);aBeetleLeg(g,p,1,[[36,28],[41,30],[43,35],[45,38]]);
 aBeetleLeg(g,p,-1,[[14,32],[10,35],[9,39],[7,42]],P.foreleg[0]);
 aBeetleLeg(g,p,1,[[34,32],[38,35],[39,39],[41,42]],P.foreleg[1]);
 const y=v=>v+L;
 aRect(g,20,y(7),8,2,p.outline);aRect(g,17,y(9),14,2,p.outline);aRect(g,14,y(11),20,2,p.outline);aRect(g,12,y(13),24,2,p.outline);
 aRect(g,10,y(15),28,13,p.outline);aRect(g,11,y(28),26,2,p.outline);aRect(g,13,y(30),22,2,p.outline);
 aRect(g,21,y(8),6,1,p.light);aRect(g,18,y(10),12,1,p.body);aRect(g,15,y(12),18,1,p.light);
 aRect(g,12,y(14),24,2,p.body);aRect(g,11,y(16),26,12,p.body);aRect(g,12,y(28),24,1,p.body);aRect(g,14,y(30),20,1,p.dark);
 aRect(g,12,y(16),2,10,p.light);aRect(g,11,y(15),6,1,p.metalLight);aRect(g,34,y(16),2,11,p.dark);
 aRect(g,15,y(16),8,10,p.body);aRect(g,25,y(16),8,10,p.body);aRect(g,15,y(16),7,1,p.light);aRect(g,26,y(17),6,8,p.body);
 aRect(g,23,y(11),2,16,p.outline);aRect(g,24,y(12),1,14,p.light);aRect(g,22,y(13),1,6,p.metalLight);
 aRect(g,21,y(18),6,7,p.outline);aRect(g,22,y(19),4,5,p.dark);
 aRect(g,23,y(20),2,3,P.core?p[P.core]:p.glow);aRect(g,23,y(21),2,1,p.metalLight);
 [[14,17],[32,17],[15,25],[31,25]].forEach(q=>{aRect(g,q[0],y(q[1]),2,2,p.outline);aRect(g,q[0],y(q[1]),1,1,p.metalLight);});
 const hy=v=>v+P.headDrop+P.shake;
 aRect(g,20,hy(27),8,3,p.outline);aRect(g,18,hy(28),12,2,p.outline);aRect(g,15,hy(30),18,2,p.outline);
 aRect(g,13,hy(32),22,7,p.outline);aRect(g,15,hy(39),18,2,p.outline);aRect(g,18,hy(41),12,1,p.outline);
 aRect(g,19,hy(28),10,1,p.metal);aRect(g,16,hy(31),16,8,p.body);aRect(g,16,hy(31),16,1,p.light);
 aRect(g,14,hy(33),20,4,p.outline);aRect(g,15,hy(34),18,2,p.metal);
 if(P.expr==='blink'||P.expr==='low'){const c=P.expr==='low'?p.outline:p.eye;
  aRect(g,17,hy(34),3,1,c);aRect(g,28,hy(34),3,1,c);}
 else if(P.expr==='focus'){aRect(g,18,hy(34),3,2,p.eye);aRect(g,19,hy(34),1,2,p.outline);aRect(g,27,hy(34),3,2,p.eye);aRect(g,28,hy(34),1,2,p.outline);}
 else{aRect(g,17,hy(33),4,3,p.eye);aRect(g,27,hy(33),4,3,p.eye);aRect(g,18,hy(34),1,2,p.outline);aRect(g,28,hy(34),1,2,p.outline);}
 if(P.expr==='happy'){aRect(g,22,hy(36),1,1,p.metalLight);aRect(g,23,hy(37),2,1,p.metalLight);aRect(g,25,hy(36),1,1,p.metalLight);}
 else{aRect(g,22,hy(36),4,1,p.outline);aRect(g,21,hy(37),1,1,p.outline);aRect(g,26,hy(37),1,1,p.outline);}
 aRect(g,16,hy(38),3,1,p.light);aRect(g,29,hy(38),3,1,p.light);aRect(g,15,hy(39),3,1,p.metal);aRect(g,30,hy(39),3,1,p.metal);
 /* the prototype's own state punctuation, kept at its authored positions */
 if(P.working){const b=Math.floor(t/300)%4;aRect(g,37,13+b*2,3,1,p.glow);aRect(g,38,14+b*2,1,1,p.glow);}
 if(S==='approval'){aRect(g,34,7,8,8,p.outline);aRect(g,35,8,6,6,p.glow);aRect(g,37,9,2,1,p.outline);aRect(g,37,11,2,1,p.outline);}
 if(S==='success'){const pr=Math.max(0,Math.min(1,t/1500));if(pr<.58){const tv=Math.floor(pr*5);aRect(g,10,19-tv,2,1,p.glow);aRect(g,36,16-tv,1,2,p.glow);}}
 if(S==='failure'){aRect(g,38,8,4,4,p.outline);aRect(g,39,9,2,1,p.danger);aRect(g,39,11,2,1,p.danger);}
 aActProp(g,p,P,t);}

function aRavenWing(g,p,side,pose){
 const rows=pose==='raised'
  ?[[40,8,4,2],[37,10,6,2],[34,12,7,2],[31,15,7,2],[28,18,7,2],[25,21,6,2]]
  :pose==='work'
  ?[[27,19,7,2],[31,22,7,2],[35,25,7,2],[38,28,6,2],[40,31,4,2]]
  :pose==='furl'
  ?[[27,19,7,2],[31,22,6,2],[34,25,5,2],[31,28,5,2],[28,30,4,2]]
  :[[27,19,7,2],[31,22,6,2],[33,25,6,2],[30,28,6,2],[27,30,5,2]];
 rows.forEach((q,i)=>{const x=side<0?48-q[0]-q[2]:q[0];
  aRect(g,x,q[1],q[2],q[3],p.outline);
  aRect(g,side<0?48-q[0]-q[2]+1:q[0]+1,q[1]+1,Math.max(1,q[2]-2),Math.max(1,q[3]-1),i%2?p.dark:p.body);
  if(i>1)aRect(g,side<0?48-q[0]-q[2]+2:q[0]+2,q[1]+1,Math.max(1,q[2]-4),1,p.metal);});}

function aRaven(g,p,P,t){
 const S=P.raw;
 const cy=v=>v+P.lift, recoil=P.expr==='low'?(t<420?[0,-1,1,0][Math.floor(t/105)%4]:0):0;
 aRect(g,17,cy(15),15,2,p.outline);aRect(g,15,cy(17),19,16,p.outline);aRect(g,17,cy(33),15,3,p.outline);
 aRect(g,19,cy(16),11,2,p.light);aRect(g,17,cy(19),15,13,p.body);aRect(g,19,cy(32),11,1,p.dark);
 aRect(g,20,cy(22),9,8,p.dark);aRect(g,21,cy(23),7,6,p.body);aRect(g,22,cy(25),5,2,p.accent);
 aRect(g,21,cy(24),1,1,p.glow);aRect(g,25,cy(27),1,1,p.metalLight);
 [[18,20],[29,20],[18,30],[29,30]].forEach(q=>aRect(g,q[0],cy(q[1]),1,1,p.metal));
 aRavenWing(g,p,-1,P.wing);aRavenWing(g,p,1,P.wing);
 const lw=P.wing==='raised'?[[21,19],[16,16],[11,12],[7,8]]:P.wing==='work'?[[21,19],[16,22],[12,26]]:[[21,19],[18,23],[18,28]];
 for(let i=0;i<lw.length-1;i++){aLine(g,lw[i][0],cy(lw[i][1]),lw[i+1][0],cy(lw[i+1][1]),p.outline,3);aLine(g,lw[i][0],cy(lw[i][1]),lw[i+1][0],cy(lw[i+1][1]),p.body,1);}
 const hx=2+P.headX+recoil,hy=8+P.headY+P.lift;
 aRect(g,hx+13,hy,9,2,p.outline);aRect(g,hx+10,hy+2,15,8,p.outline);aRect(g,hx+12,hy+10,11,2,p.outline);
 aRect(g,hx+15,hy+1,6,1,p.metal);aRect(g,hx+12,hy+3,11,6,p.body);aRect(g,hx+14,hy+3,7,1,p.light);
 aRect(g,hx+2,hy+5,12,3,p.outline);aRect(g,hx+4,hy+6,10,1,p.accent);aRect(g,hx+3,hy+7,3,1,p.metalLight);
 aFace(g,p,hx+18,hy+4,P.expr,1);
 aRect(g,19,40,4,1,p.outline);aRect(g,29,40,4,1,p.outline);aRect(g,18,41,6,1,p.metal);aRect(g,28,41,6,1,p.metal);
 aRect(g,15,43,8,1,p.metalLight);aRect(g,28,43,8,1,p.metalLight);
 if(P.tool){aLine(g,hx+1,hy+7,2,hy+12,p.metalLight,2);aRect(g,1,hy+12,3,2,p.accent);aSpark(g,4,hy+14,p.glow,2);}
 if(S==='approval'){aSpark(g,23,4,p.glow,3);aRect(g,22,8,4,1,p.accent);}
 if(S==='success'&&t<1050){aSpark(g,8,14,p.glow,2);aSpark(g,39,15,p.glow,2);}
 if(S==='failure'){aRect(g,29,cy(11),2,7,p.danger);aRect(g,31,cy(17),2,2,p.danger);aLine(g,18,39,15,42,p.danger,1);}
 aActProp(g,p,P,t);}

function aGolemBlock(g,p,x,y,w,h,light){
 aRect(g,x,y,w,h,p.outline);
 aRect(g,x+1,y+1,w-2,h-2,light?p.light:p.body);
 aRect(g,x+2,y+1,Math.max(1,w-5),1,light?p.metalLight:p.light);
 aRect(g,x+2,y+h-2,Math.max(1,w-5),1,p.dark);}
function aGolemArm(g,p,side,pose,tool){
 const x=side<0?5:36;
 const shoulderY=pose==='raised'?10:14;
 const elbow=pose==='raised'?{x:side<0?7:36,y:8}:pose==='work'?{x:side<0?8:40,y:13}:{x:side<0?4:39,y:30};
 const hand=pose==='raised'?{x:side<0?8:37,y:6}:pose==='work'?{x:side<0?7:42,y:9}:{x:side<0?6:38,y:34};
 aLine(g,side<0?13:35,shoulderY,elbow.x,elbow.y,p.outline,5);
 aLine(g,elbow.x,elbow.y,hand.x,hand.y,p.outline,5);
 aLine(g,side<0?13:35,shoulderY,elbow.x,elbow.y,p.body,3);
 aLine(g,elbow.x,elbow.y,hand.x,hand.y,p.body,3);
 aRect(g,hand.x-2,hand.y-1,4,3,p.outline);aRect(g,hand.x-1,hand.y,2,1,p.metalLight);
 if(pose==='work'&&side>0){aLine(g,42,9,44,4,p.metalLight,2);aRect(g,43,2,2,3,p.accent);}
 if(pose==='raised'){aRect(g,hand.x-1,hand.y-2,2,1,p.glow);}
 if(tool){aRect(g,hand.x-1,hand.y,2,1,p.glow);}}

function aGolem(g,p,P,t){
 const S=P.raw;
 const y=v=>v+P.lift;
 aGolemArm(g,p,-1,P.armL,P.toolL);aGolemArm(g,p,1,P.armR,P.toolR);
 aGolemBlock(g,p,11,y(13),26,25,true);
 aGolemBlock(g,p,14,y(8),20,10,true);
 aGolemBlock(g,p,16,y(11),16,10,false);
 aRect(g,17,y(12),14,2,p.dark);aRect(g,17,y(14),5,4,p.outline);aRect(g,26,y(14),5,4,p.outline);
 aFace(g,p,18,y(14),P.expr,1);
 if(P.expr==='happy'){aRect(g,20,y(19),8,1,p.outline);aRect(g,21,y(18),6,1,p.outline);}
 else{aRect(g,20,y(19),8,1,p.outline);aRect(g,19,y(20),2,1,p.outline);aRect(g,27,y(20),2,1,p.outline);}
 aRect(g,14,y(23),20,2,p.dark);aRect(g,17,y(26),14,5,p.body);aRect(g,19,y(27),10,2,p.accent);
 aRect(g,11,y(29),5,4,p.dark);aRect(g,32,y(29),5,4,p.dark);
 [[14,17],[33,17],[14,31],[33,31]].forEach(q=>{aRect(g,q[0],y(q[1]),2,2,p.outline);aRect(g,q[0],y(q[1]),1,1,p.metalLight);});
 const footY=38+P.lift;
 aGolemBlock(g,p,8,footY,12,6,false);aGolemBlock(g,p,28,footY,12,6,false);
 aRect(g,10,footY+5,8,1,p.metalLight);aRect(g,30,footY+5,8,1,p.metalLight);
 if(S==='approval'){aSpark(g,23,3,p.glow,3);aRect(g,21,7,5,1,p.accent);}
 if(S==='success'&&t<1000){aSpark(g,8,8,p.glow,2);aSpark(g,38,8,p.glow,2);}
 if(S==='failure'){aLine(g,23,23,26,26,p.danger,1);aLine(g,26,23,23,26,p.danger,1);aRect(g,38,3,5,5,p.outline);aRect(g,40,4,1,3,p.danger);}
 aActProp(g,p,P,t);}

function aTentacle(g,p,pts,color){
 for(let i=0;i<pts.length-1;i++){aLine(g,pts[i][0],pts[i][1],pts[i+1][0],pts[i+1][1],p.outline,3);aLine(g,pts[i][0],pts[i][1],pts[i+1][0],pts[i+1][1],color,1);}
 const tip=pts[pts.length-1];aRect(g,tip[0]-1,tip[1]-1,2,2,p.glow);}

function aTide(g,p,P,t){
 const S=P.raw;
 /* The tide settles rather than lifts: a late 2px drop, authored on its own clock. */
 if(S==='failure')P.lift=t<500?0:A_EASE_OUT((t-500)/700)*2;
 const y=v=>v+P.lift, sway=P.sway;
 /* The prototype raises only the right-hand reach on approval/success; its leftRaised is
    declared and never used. Reproduced as authored, not "fixed". */
 const R=P.rightRaised;
 aTentacle(g,p,[[16,y(29)],[11,y(34)],[7,y(37)+sway],[5,y(42)]],p.light);
 aTentacle(g,p,[[20,y(31)],[16,y(36)],[14,y(41)-sway],[15,y(45)]],p.accent);
 aTentacle(g,p,[[19,y(32)],[20,y(38)],[18,y(43)+sway],[20,y(46)]],p.light);
 aTentacle(g,p,[[29,y(32)],[31,y(37)],[30,y(42)-sway],[32,y(46)]],p.light);
 aTentacle(g,p,[[32,y(30)],[36,y(35)],[39,y(39)+sway],[42,y(42)-R]],p.accent);
 aTentacle(g,p,[[35,y(28)],[40,y(32)+P.spread],[43,y(36)-P.spread],[45,y(39)+R]],p.light);
 const top=y(9),bx=14+P.tilt,w=P.width;
 aRect(g,bx+4,top,w-8,2,p.outline);aRect(g,bx+2,top+2,w-4,3,p.outline);aRect(g,bx,top+5,w,11,p.outline);
 aRect(g,bx+2,top+16,w-4,5,p.outline);aRect(g,bx+6,top+21,w-12,3,p.outline);
 aRect(g,bx+5,top+1,w-10,1,p.light);aRect(g,bx+3,top+3,w-6,12,p.body);aRect(g,bx+3,top+16,w-6,4,p.body);aRect(g,bx+7,top+21,w-14,1,p.dark);
 aRect(g,bx+5,top+6,w-10,2,p.light);aRect(g,bx+4,top+9,w-8,1,p.dark);aRect(g,bx+5,top+12,w-10,1,p.light);aRect(g,bx+4,top+15,w-8,1,p.dark);
 aFace(g,p,bx+6,top+8,P.expr,1);
 aRect(g,bx+9,top+14,3,2,p.glow);
 if(P.working){aLine(g,bx+17,y(25),43,y(25),p.outline,2);aLine(g,bx+17,y(25),43,y(25),p.accent,1);aRect(g,42,y(23),3,3,p.outline);aRect(g,43,y(24),1,1,p.glow);}
 if(S==='approval'){aSpark(g,23,3,p.glow,3);aRect(g,22,7,4,1,p.accent);}
 if(S==='success'&&t<1100){aSpark(g,10,10,p.glow,2);aSpark(g,38,10,p.glow,2);}
 if(S==='failure'){aRect(g,21,y(4),2,5,p.danger);aRect(g,21,y(10),2,2,p.danger);aLine(g,13,y(25),10,y(29),p.danger,1);}
 aActProp(g,p,P,t);}


/* ---- activity punctuation only ----------------------------------------------------
   The original quartet's OWN state punctuation stays inside each creature at its authored
   position. This adds exactly one thing the prototype never had: a legible marker for WHICH
   activity is running, derived from the typed category. It never carries truth -- the bubble
   above the pet does. It is drawn outside the body and is inert when there is no activity. */
function aActProp(g,p,P,t){
 /* Markers for the three states the approved prototype never modelled, so they are legible
    rather than indistinguishable from idle. These are punctuation, not truth -- the bubble
    carries truth. This is the only thing added to the quartet's own drawing. */
 const S=P.raw, beat=Math.floor(t/300)%4;
 if(S==='waiting'){
  aRect(g,35,7,6,2,p.metal);aRect(g,36,9,4,5,p.metalLight);aRect(g,35,14,6,2,p.metal);return;}
 if(S==='cancelled'){
  aRect(g,34,7,9,7,p.outline);aRect(g,35,8,7,5,p.metal);aRect(g,36,10,5,1,p.metalLight);return;}
 if(S==='unknown'){
  aRect(g,34,8,5,2,p.metal);aRect(g,37,10,2,3,p.metal);aRect(g,35,13,3,2,p.metal);
  if(beat<4)aRect(g,35,17,2,2,p.metal);return;}
 const act=P.act;
 if(!act)return;
 const b=beat;
 if(act===A_ACT.READ){aRect(g,35,7,8,10,p.outline);aRect(g,36,9,6,6,p.metalLight);aRect(g,36,16,4,1,p.metal);}
 else if(act===A_ACT.SEARCH){aRect(g,35,9,6,6,p.outline);aRect(g,36,10,4,4,p.metalLight);aLine(g,41,14,44,17,p.metal,2);}
 else if(act===A_ACT.DIG){aRect(g,34,15,9,2,p.dark);aRect(g,35,13,7,2,p.body);aRect(g,38,11,2,2,p.light);}
 else if(act===A_ACT.CODE){aRect(g,35,9,7,6,p.outline);aRect(g,36,10,2,2,p.metalLight);aRect(g,39,10,2,2,p.metalLight);aRect(g,36,13,2,1,p.metalLight);}
 else if(act===A_ACT.RUN){aRect(g,38,8,2,3+b,p.accent);aRect(g,37,11+b,4,2,p.accent);}
 else if(act===A_ACT.TEST){aRect(g,34,9,9,6,p.outline);aRect(g,35,10,7,4,p.metalLight);aRect(g,36+b,12,2,2,p.danger);}
 else if(act===A_ACT.WATCH){aRect(g,35,8,8,6,p.outline);aRect(g,36,9,6,4,p.accent);aLine(g,43,11,45,15,p.metalLight,2);}}

/* ================= SIX NEW COMPANIONS ==============================================
   Same helpers, same outline law, same face grammar, same shadow -- different materials and
   anatomy, so they read as neighbours of the quartet rather than recolours of it. Each keeps a
   distinct silhouette: tall-narrow, wide-low, diamond, wide-soft, round-low. */

/* SPARK -- a lamplighter moth. Brass, wax and a glass dome. Small, narrow, tall. */
function aSparkMoth(g,p,P,t){
 const y=v=>v+P.lift+P.shake, L=aLimbPose(P.arm,t);
 // glass dome over a live wick
 aRect(g,19,y(6),10,2,p.outline);aRect(g,17,y(8),14,3,p.outline);aRect(g,16,y(11),16,7,p.outline);
 aRect(g,15,y(18),18,2,p.outline);
 aRect(g,18,y(8),12,2,p.metalLight);aRect(g,17,y(10),14,7,p.accent);aRect(g,17,y(10),14,1,p.glow);
 aRect(g,17,y(11),2,5,p.metalLight);aRect(g,30,y(11),2,5,p.metal);
 aRect(g,22,y(11),4,6,p.outline);aRect(g,23,y(12),2,4,p.glow);aRect(g,23,y(13),2,2,p.metalLight);
 // wax body, three bands
 aRect(g,18,y(20),12,2,p.outline);aRect(g,16,y(22),16,3,p.outline);aRect(g,15,y(25),18,2,p.outline);
 aRect(g,16,y(27),16,2,p.outline);aRect(g,18,y(29),12,2,p.outline);
 aRect(g,19,y(20),10,1,p.light);aRect(g,17,y(22),14,2,p.body);aRect(g,16,y(25),16,1,p.light);
 aRect(g,17,y(27),14,1,p.body);aRect(g,19,y(29),10,1,p.dark);
 aRect(g,16,y(25),2,4,p.light);aRect(g,30,y(22),2,5,p.dark);
 aRect(g,19,y(23),10,2,p.metal);aRect(g,19,y(28),10,2,p.metal);
 // antennae
 [[20,20],[17,15],[15,11]].forEach((q,i)=>{aLine(g,q[0],y(q[1]),q[0],y(q[1]-3-i),p.outline,2);});
 aRect(g,14,y(10),2,2,p.metalLight);aRect(g,32,y(10),2,2,p.metalLight);
 // wings, folded flat against the body
 const wf=P.arm==='raised'?5:2;
 aRect(g,11,y(23),6,10,p.outline);aRect(g,12,y(24),4,8,p.light);aRect(g,13,y(25),2,6,p.metalLight);
 aRect(g,31,y(23),6,10,p.outline);aRect(g,32,y(24),4,8,p.body);aRect(g,33,y(25),2,6,p.metal);
 aRect(g,11,y(23-wf),6,3,p.outline);aRect(g,31,y(23-wf),6,3,p.outline);
 // face plate on the body
 aFace(g,p,20,y(23),P.expr,1);
 // feet + held items
 aRect(g,19,y(31),4,3,p.outline);aRect(g,20,y(32),2,2,p.metal);
 aRect(g,25,y(31),4,3,p.outline);aRect(g,26,y(32),2,2,p.metal);
 if(P.arm==='type'){aRect(g,29,y(28),6,3,p.metalLight);aRect(g,31,y(29),3,1,p.metal);}
 if(P.arm==='read'){aRect(g,30,y(24),5,6,p.metalLight);aRect(g,31,y(25),3,4,p.body);}
 if(P.arm==='test'){aLine(g,30,y(27),35,y(23),p.metalLight,2);aRect(g,34,y(21),3,3,p.accent);}
 if(P.arm==='droop'){aRect(g,30,y(30),5,2,p.dark);}
 aActProp(g,p,P,t);}

/* RASCAL — a courier with a bandolier and a satchel. Leather and tin. Wide, low, hunched. */
function aRascalTail(g,p,side,t){
 const pts=[aMir(side,18,32),aMir(side,24,30,-0),aMir(side,29,26),aMir(side,32,20),aMir(side,31,15)];
 for(let i=0;i<pts.length-1;i++){aLine(g,pts[i].x,pts[i].y,pts[i+1].x,pts[i+1].y,p.outline,4);aLine(g,pts[i].x,pts[i].y,pts[i+1].x,pts[i+1].y,p.body,2);}
 const tip=pts[4];aRect(g,tip.x-2,tip.y-2,4,4,p.light);aRect(g,tip.x-1,tip.y-1,2,2,p.body);}
function aRascal(g,p,P,t){
 const y=v=>v+P.lift+P.shake;
 aRascalTail(g,p,-1,t);aRascalTail(g,p,1,t);
 // low hunched torso
 aRect(g,14,y(18),20,3,p.outline);aRect(g,11,y(21),26,3,p.outline);
 aRect(g,9,y(24),30,12,p.outline);aRect(g,11,y(36),26,3,p.outline);
 aRect(g,15,y(19),18,2,p.light);aRect(g,12,y(22),26,2,p.body);aRect(g,10,y(25),28,10,p.body);
 aRect(g,10,y(25),2,10,p.light);aRect(g,36,y(25),2,10,p.dark);
 aRect(g,10,y(35),28,1,p.dark);
 // bandolier
 aLine(g,13,y(23),33,y(33),p.outline,4);aLine(g,13,y(23),33,y(33),p.accent,2);
 [[16,25],[21,28],[26,31]].forEach(q=>aRect(g,q[0],y(q[1]),2,2,p.metalLight));
 // satchel
 aRect(g,31,y(28),9,9,p.outline);aRect(g,32,y(29),7,7,p.body);aRect(g,32,y(29),7,1,p.light);
 aRect(g,34,y(31),3,2,p.metalLight);
 // rivets
 [[13,25],[35,25],[13,34],[35,34]].forEach(q=>{aRect(g,q[0],y(q[1]),2,2,p.outline);aRect(g,q[0],y(q[1]),1,1,p.metalLight);});
 // arms
 const up=P.arm==='raised'?-5:P.arm==='droop'?4:P.arm==='reach'?-3:0;
 aLimb(g,12,y(25),7,y(27+up),p,5);aLimb(g,36,y(25),41,y(27+up),p,5);
 aRect(g,5,y(26+up),4,4,p.outline);aRect(g,6,y(27+up),2,2,p.metalLight);
 aRect(g,39,y(26+up),4,4,p.outline);aRect(g,40,y(27+up),2,2,p.metalLight);
 if(P.arm==='type'){aRect(g,38,y(24+up),7,3,p.metalLight);aRect(g,40,y(25+up),4,1,p.metal);}
 if(P.arm==='read'){aRect(g,38,y(23+up),6,6,p.metalLight);aRect(g,39,y(24+up),4,4,p.body);}
 if(P.arm==='run'){aRect(g,39,y(24+up),5,2,P.arm==='run'&&Math.floor(t/150)%2?p.accent:p.metalLight);}
 if(P.arm==='test'){aLine(g,41,y(24+up),45,y(20+up),p.metalLight,2);aRect(g,44,y(18+up),3,3,p.accent);}
 if(P.arm==='dig'){aRect(g,38,y(28+up),7,2,p.dark);aRect(g,40,y(26+up),3,2,p.body);}
 if(P.arm==='cross'){aLimb(g,14,y(29),34,y(29),p,4);aRect(g,21,y(28),6,3,p.metalLight);}
 // head, low-set
 const hx=2+(P.arm==='read'||P.arm==='type'?1:0)+P.shake, hy=7+P.lift+(P.arm==='dig'?2:0);
 aRect(g,hx+13,hy,9,2,p.outline);aRect(g,hx+10,hy+2,15,9,p.outline);aRect(g,hx+13,hy+11,11,2,p.outline);
 aRect(g,hx+12,hy+3,11,7,p.body);aRect(g,hx+12,hy+3,11,1,p.light);
 aRect(g,hx+2,hy+5,11,4,p.outline);aRect(g,hx+3,hy+6,9,2,p.body);
 aRect(g,hx+4,hy+6,3,1,p.dark);aRect(g,hx+10,hy+6,2,1,p.dark);
 aFace(g,p,hx+18,hy+4,P.expr,1);
 aRect(g,hx+14,hy+12,10,1,p.accent);
 aActProp(g,p,P,t);}

/* PRIME — a keystone keeper. Polished brass over obsidian. Tall, narrow, formal. */
function aPrime(g,p,P,t){
 const y=v=>v+P.lift+P.shake;
 // obsidian plinth base
 aRect(g,14,y(38),20,3,p.outline);aRect(g,16,y(41),16,2,p.outline);
 aRect(g,15,y(38),18,2,p.dark);aRect(g,17,y(41),14,1,p.metal);
 // brass column
 aRect(g,19,y(20),10,3,p.outline);aRect(g,17,y(23),14,3,p.outline);
 aRect(g,16,y(26),16,12,p.outline);
 aRect(g,20,y(21),8,1,p.metalLight);aRect(g,18,y(24),12,2,p.body);aRect(g,17,y(26),14,11,p.body);
 aRect(g,17,y(26),2,11,p.light);aRect(g,29,y(26),2,11,p.dark);
 aRect(g,20,y(28),8,8,p.outline);aRect(g,21,y(29),6,6,p.dark);aRect(g,22,y(30),4,3,p.accent);
 aRect(g,22,y(30),4,1,p.glow);
 aRect(g,20,y(34),8,2,p.metal);aRect(g,18,y(36),12,2,p.metal);
 // keystone head, taller than wide
 aRect(g,17,y(9),14,2,p.outline);aRect(g,15,y(11),18,3,p.outline);
 aRect(g,14,y(14),20,8,p.outline);aRect(g,17,y(22),14,2,p.outline);
 aRect(g,18,y(10),12,1,p.metalLight);aRect(g,16,y(12),16,2,p.body);
 aRect(g,15,y(14),18,7,p.body);aRect(g,15,y(14),2,7,p.light);aRect(g,31,y(14),2,7,p.dark);
 aRect(g,18,y(18),12,2,p.metal);aRect(g,20,y(20),8,2,p.metal);
 aFace(g,p,19,y(14),P.expr,1);
 // arms, formal and narrow
 const sw=P.arm==='raised'?-4:P.arm==='reach'?-2:0;
 aLimb(g,16,y(27),9,y(25+sw),p,4);aLimb(g,32,y(27),39,y(25+sw),p,4);
 aRect(g,7,y(24+sw),4,3,p.outline);aRect(g,8,y(25+sw),2,1,p.metalLight);
 aRect(g,37,y(24+sw),4,3,p.outline);aRect(g,38,y(25+sw),2,1,p.metalLight);
 if(P.arm==='type'){aRect(g,36,y(22+sw),8,3,p.metalLight);aRect(g,38,y(23+sw),5,1,p.metal);}
 if(P.arm==='read'){aRect(g,36,y(21+sw),7,6,p.metalLight);aRect(g,37,y(22+sw),5,4,p.body);}
 if(P.arm==='test'){aLine(g,39,y(23+sw),44,y(19+sw),p.metalLight,2);aRect(g,43,y(17+sw),3,3,p.accent);}
 if(P.arm==='dig'){aLimb(g,16,y(30),12,y(36),p,4);aLimb(g,32,y(30),36,y(36),p,4);aRect(g,9,y(35),7,2,p.dark);aRect(g,32,y(35),7,2,p.dark);}
 if(P.arm==='cross'){aRect(g,20,y(27),8,2,p.metal);aRect(g,18,y(26),3,3,p.metalLight);aRect(g,27,y(26),3,3,p.metalLight);}
 if(P.arm==='droop'){aRect(g,7,y(30),4,2,p.dark);aRect(g,37,y(30),4,2,p.dark);}
 aActProp(g,p,P,t);}

/* PRISM — a faceted lantern-fish. Cut glass, hard facets, internal light. Diamond, tall. */
function aPrism(g,p,P,t){
 const y=v=>v+P.lift+P.shake, tilt=P.arm==='dig'||P.arm==='run'?1:0;
 // tail fan, drawn as stepped facets
 [[30,36,7,2],[33,39,6,2],[35,42,5,2]].forEach((q,i)=>{
  aRect(g,q[0]+tilt,y(q[1]),q[2],q[3],p.outline);
  aRect(g,q[0]+tilt+1,y(q[1]+1),q[2]-2,1,i%2?p.light:p.metalLight);});
 aRect(g,28,y(34),5,4,p.outline);aRect(g,29,y(35),3,2,p.metalLight);
 // faceted body -- a diamond, hard edges, no curves
 aRect(g,21,y(6),6,2,p.outline);aRect(g,19,y(8),10,2,p.outline);
 aRect(g,17,y(10),14,2,p.outline);aRect(g,15,y(12),18,3,p.outline);
 aRect(g,13,y(15),22,8,p.outline);aRect(g,15,y(23),18,3,p.outline);
 aRect(g,17,y(26),14,2,p.outline);aRect(g,19,y(28),10,2,p.outline);
 aRect(g,22,y(7),4,1,p.metalLight);aRect(g,20,y(9),8,1,p.light);
 aRect(g,18,y(11),12,1,p.body);aRect(g,16,y(13),16,2,p.body);
 aRect(g,14,y(16),20,6,p.body);aRect(g,16,y(24),16,2,p.body);
 aRect(g,18,y(27),12,1,p.body);aRect(g,20,y(29),8,1,p.dark);
 aRect(g,13,y(16),2,6,p.metalLight);aRect(g,33,y(16),2,6,p.dark);
 // internal facet seams
 aLine(g,15,y(15),33,y(15),p.metalLight,1);
 aLine(g,14,y(20),34,y(20),p.metal,1);
 aLine(g,15,y(25),33,y(25),p.metalLight,1);
 aRect(g,19,y(17),10,3,p.outline);aRect(g,20,y(18),8,2,p.glow);
 aFace(g,p,20,y(13),P.expr,1);
 // side fins, glass
 aRect(g,9,y(16),5,3,p.outline);aRect(g,10,y(17),3,1,p.metalLight);
 aRect(g,34,y(16),5,3,p.outline);aRect(g,35,y(17),3,1,p.metal);
 if(P.arm==='raised'){aRect(g,8,y(10),4,5,p.outline);aRect(g,9,y(11),2,3,p.metalLight);aRect(g,36,y(10),4,5,p.outline);aRect(g,37,y(11),2,3,p.metalLight);}
 if(P.arm==='type'){aRect(g,34,y(22),7,3,p.outline);aRect(g,35,y(23),5,1,p.metalLight);}
 if(P.arm==='read'){aRect(g,34,y(14),7,6,p.outline);aRect(g,35,y(15),5,4,p.metalLight);}
 if(P.arm==='test'){aLine(g,36,y(20),42,y(16),p.metalLight,2);aRect(g,41,y(14),3,3,p.accent);}
 if(P.arm==='droop'){aRect(g,9,y(22),5,2,p.dark);aRect(g,34,y(22),5,2,p.dark);}
 if(P.arm==='cross'){aRect(g,20,y(22),8,2,p.metal);}
 aActProp(g,p,P,t);}

/* VEIL — a dusk moth under a folded wing cloak. Velvet and dull pewter. Wide, soft, tall. */
function aVeilWing(g,p,side,pose,lift){
 const y=v=>v+lift;
 /* Wing rows are authored on the RIGHT of the body and mirrored for the left, so the wings
    read as spread plumage outside the silhouette rather than filling it. */
 const rows=pose==='raised'?[[33,12,11,3],[31,16,12,3],[30,20,12,3],[31,24,11,3]]
  :pose==='work'?[[34,16,10,3],[35,20,10,3],[36,24,9,3],[37,28,8,3]]
  :[[35,15,9,3],[36,19,9,3],[37,23,8,3],[38,27,7,3]];
 rows.forEach((q,i)=>{const x=side<0?48-q[0]-q[2]:q[0];
  aRect(g,x,y(q[1]),q[2],q[3],p.outline);
  aRect(g,side<0?48-q[0]-q[2]+1:q[0]+1,y(q[1])+1,q[2]-2,1,i%2?p.metalLight:p.light);
  aRect(g,side<0?48-q[0]-q[2]+1:q[0]+1,y(q[1])+2,q[2]-2,1,p.dark);});}
function aVeil(g,p,P,t){
 const y=v=>v+P.lift+P.shake;
 const pose=P.arm==='raised'?'raised':P.arm==='type'||P.arm==='run'?'work':'fold';
 aVeilWing(g,p,-1,pose,P.lift);aVeilWing(g,p,1,pose,P.lift);
 // furred thorax
 aRect(g,18,y(10),12,2,p.outline);aRect(g,15,y(12),18,3,p.outline);aRect(g,14,y(15),20,12,p.outline);
 aRect(g,17,y(24),14,2,p.outline);aRect(g,19,y(26),10,2,p.outline);
 aRect(g,19,y(11),10,1,p.light);aRect(g,16,y(13),16,2,p.body);aRect(g,15,y(15),18,11,p.body);
 aRect(g,15,y(15),2,11,p.light);aRect(g,32,y(15),2,11,p.dark);
 aRect(g,18,y(17),12,2,p.metal);aRect(g,19,y(19),10,4,p.dark);aRect(g,20,y(20),8,2,p.accent);
 aRect(g,20,y(20),8,1,p.glow);
 [[16,16],[32,16],[16,24],[32,24]].forEach(q=>{aRect(g,q[0],y(q[1]),2,2,p.outline);aRect(g,q[0],y(q[1]),1,1,p.metalLight);});
 aFace(g,p,19,y(20),P.expr,1);
 // feathered antennae
 aLine(g,21,y(11),18,y(5),p.outline,2);aLine(g,21,y(11),18,y(5),p.metal,1);
 aLine(g,27,y(11),30,y(5),p.outline,2);aLine(g,27,y(11),30,y(5),p.metal,1);
 aRect(g,17,y(4),2,2,p.metalLight);aRect(g,29,y(4),2,2,p.metalLight);
 // small forelimbs
 const fw=P.arm==='raised'?-4:0;
 aLimb(g,16,y(20),10,y(18+fw),p,3);aLimb(g,32,y(20),38,y(18+fw),p,3);
 aRect(g,8,y(17+fw),3,3,p.outline);aRect(g,37,y(17+fw),3,3,p.outline);
 if(P.arm==='type'){aRect(g,36,y(22),7,3,p.metalLight);}
 if(P.arm==='read'){aRect(g,36,y(16),6,6,p.metalLight);aRect(g,37,y(17),4,4,p.body);}
 if(P.arm==='test'){aLine(g,38,y(20),43,y(16),p.metalLight,2);aRect(g,42,y(14),3,3,p.accent);}
 if(P.arm==='droop'){aRect(g,8,y(22),3,2,p.dark);aRect(g,37,y(22),3,2,p.dark);}
 if(P.arm==='cross'){aRect(g,20,y(22),8,2,p.metal);}
 // soft undercarriage
 aRect(g,20,y(28),4,3,p.outline);aRect(g,24,y(28),4,3,p.outline);
 aRect(g,21,y(29),2,2,p.metal);aRect(g,25,y(29),2,2,p.metal);
 aActProp(g,p,P,t);}

/* EMBER — a kiln sprite carrying a live coal. Terracotta and fire. Round, wide, low. */
function aEmber(g,p,P,t){
 const y=v=>v+P.lift+P.shake, glow=P.expr==='low'?p.danger:P.expr==='happy'?p.metalLight:p.glow;
 // squat kiln body, stepped silhouette
 aRect(g,19,y(12),10,2,p.outline);aRect(g,16,y(14),16,3,p.outline);
 aRect(g,13,y(17),22,3,p.outline);aRect(g,11,y(20),26,3,p.outline);
 aRect(g,10,y(23),28,9,p.outline);aRect(g,12,y(32),24,3,p.outline);aRect(g,16,y(35),16,2,p.outline);
 aRect(g,20,y(13),8,1,p.metalLight);aRect(g,17,y(15),14,2,p.body);aRect(g,14,y(18),20,2,p.body);
 aRect(g,12,y(21),24,2,p.body);aRect(g,11,y(23),26,8,p.body);
 aRect(g,11,y(23),2,8,p.light);aRect(g,35,y(23),2,8,p.dark);
 aRect(g,13,y(31),22,1,p.dark);
 // belly firebox, the one saturated area
 aRect(g,18,y(24),12,8,p.outline);aRect(g,19,y(25),10,6,p.dark);
 aRect(g,20,y(26),8,4,p.accent);aRect(g,20,y(26),8,2,glow);aRect(g,22,y(28),4,2,p.metalLight);
 aRect(g,19,y(31),10,1,p.dark);
 // brick courses
 aRect(g,13,y(20),22,1,p.metal);aRect(g,11,y(27),26,1,p.metal);
 [[14,19],[33,19],[14,33],[33,33]].forEach(q=>{aRect(g,q[0],y(q[1]),2,2,p.outline);aRect(g,q[0],y(q[1]),1,1,p.metalLight);});
 aFace(g,p,19,y(19),P.expr,1);
 // stubby arms
 const aw=P.arm==='raised'?-4:P.arm==='reach'?-2:0;
 aLimb(g,12,y(24),6,y(22+aw),p,5);aLimb(g,36,y(24),42,y(22+aw),p,5);
 aRect(g,3,y(21+aw),4,4,p.outline);aRect(g,4,y(22+aw),2,2,p.metalLight);
 aRect(g,41,y(21+aw),4,4,p.outline);aRect(g,42,y(22+aw),2,2,p.metalLight);
 if(P.arm==='type'){aRect(g,40,y(24+aw),6,3,p.metalLight);aRect(g,42,y(25+aw),3,1,p.metal);}
 if(P.arm==='read'){aRect(g,40,y(19+aw),6,6,p.metalLight);aRect(g,41,y(20+aw),4,4,p.body);}
 if(P.arm==='test'){aLine(g,42,y(22+aw),46,y(18+aw),p.metalLight,2);aRect(g,45,y(16+aw),3,3,p.accent);}
 if(P.arm==='dig'){aRect(g,40,y(27+aw),6,2,p.dark);aRect(g,42,y(25+aw),3,2,p.body);}
 if(P.arm==='droop'){aRect(g,3,y(25),4,2,p.dark);aRect(g,41,y(25),4,2,p.dark);}
 if(P.arm==='cross'){aRect(g,20,y(24),8,2,p.metal);}
 // coal on a forked stick
 aLine(g,30,y(16),36,y(11),p.outline,2);aLine(g,30,y(16),36,y(11),p.metal,1);
 aRect(g,35,y(8),5,5,p.outline);aRect(g,36,y(9),3,3,p.accent);aRect(g,36,y(9),3,1,p.glow);
 aSpark(g,33,y(9),p.glow,2);
 aActProp(g,p,P,t);}

/* ---- registry + dispatcher --------------------------------------------------------- */
const A_PETS={
 beetle:{name:'MORROW BEETLE',note:'mechanical beetle',draw:aBeetle},
 raven:{name:'CLOCKWORK RAVEN',note:'clockwork raven',draw:aRaven},
 golem:{name:'STONE GOLEM',note:'stone golem',draw:aGolem},
 tide:{name:'TIDE WISP',note:'tide wisp',draw:aTide},
 spark:{name:'SPARK · Lamplighter',note:'brass moth-lantern',draw:aSparkMoth},
 rascal:{name:'RASCAL · Courier',note:'leather courier',draw:aRascal},
 prime:{name:'PRIME · Keykeeper',note:'brass keystone keeper',draw:aPrime},
 prism:{name:'PRISM · Lanternfin',note:'cut-glass fish',draw:aPrism},
 veil:{name:'VEIL · Duskmoth',note:'velvet moth',draw:aVeil},
 ember:{name:'EMBER · Kiln',note:'terracotta kiln sprite',draw:aEmber}};
const A_ORIGINAL=['beetle','raven','golem','tide'];

/* Authored ids, for the roster law and the chooser. */
function aPetIds(){return Object.keys(A_PETS);}
function aIsOriginal(id){return A_ORIGINAL.indexOf(id)>=0;}

/* One entry point. ``state`` is the authoritative product state; ``activity`` is the
   presentation-only substate resolved from the typed category. Animation time selects the
   pose only -- it never decides state. */
function vcwDrawPet(g,id,state,activity,elapsed,opts){
 opts=opts||{};
 const def=A_PETS[id];
 if(!def)return false;
 const t=Math.max(0,Number(elapsed)||0);
 const reduced=!!opts.reduced;
 /* The golem's celebration arc is authored at amplitude 2; the other three are authored at 3.
    This is the prototype's per-creature difference, kept rather than normalised. */
 const liftAmp=(id==='golem')?2:3;
 const P=aPose(state,activity,t,reduced,liftAmp);
 const pal=opts.palette||A_PAL[id];
 g.clearRect(0,0,48,48);
 def.draw(g,pal,P,t);
 return true;}
"""
