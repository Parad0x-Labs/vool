"""Remastered companions: ten extra pets in a cleaner shaded pixel style.

The legacy ten (``core/companion_art_fragment.py``) are untouched. These are separate ids with an
``rm_`` prefix, registered into the same ``A_PETS`` registry, so they go through the same
dispatcher, pose law, activity props and reduced-motion rule as every other pet.
"""

from __future__ import annotations

REMASTER_ROSTER = (
    "rm_beetle", "rm_raven", "rm_golem", "rm_tide",
    "rm_spark", "rm_rascal", "rm_prime", "rm_prism", "rm_veil", "rm_ember",
)

REMASTER_JS = r"""/* VOOL remastered companions -- ten extra pets drawn in a cleaner, shaded pixel style.
   The legacy ten stay exactly as they are; these are separate ids (rm_*), registered into the
   same A_PETS registry and drawn through the same dispatcher, pose law and activity props.
   Every part is shaded from its own shape (rim light top-left, form shadow bottom-right) and
   outlined in a colour tinted toward the body, so the family reads as one hand. */
const RM_N=48;
let RM_S={st:'idle',act:null,mood:'calm',T:0,beat:0,work:false,lift:0,shake:0,drop:0,reduced:false};
/* Vertical offset for the whole body: idle bob, work tick, celebration lift, failure droop. */
function rmBob(base){const S=RM_S;let b=S.reduced?0:base;if(S.work&&!S.reduced)b+=(S.beat&1);return b-S.lift+S.drop;}
/* Arm offset for the two-armed pets: up for approval and success, tapping at work, down on failure. */
function rmArm(side){const S=RM_S;
 if(S.st==='approval'||S.st==='success')return -8;
 if(S.st==='failure')return 1;
 if(S.work&&!S.reduced)return ((S.beat+(side>0?1:0))&1)?-2:0;
 return 0;}
/* Wing position for the winged pets: raised for approval/success, drooped on failure, fast at work. */
function rmFlap(idle,t){const S=RM_S;
 if(S.st==='approval'||S.st==='success')return -3;
 if(S.st==='failure')return 8;
 if(S.work&&!S.reduced)return Math.floor(t/110)%4;
 return idle;}
/* One eye grammar for every pet: calm and bright show the eye, focus narrows it under a brow,
   happy closes it into an arch, low and blink close it to a line. */
function rmEye(g,x,y,bob,c){const m=RM_S.mood,W=c.W||3,H=c.H||3;
 if(m==='blink'||m==='low'){rmBlock(g,x,y+H-2,W,1,c.lineDark||c.line,0,bob);return;}
 if(m==='happy'){rmPx(g,x,y+H-1,c.line,0,bob);rmBlock(g,x+1,y+H-2,W-2,1,c.line,0,bob);rmPx(g,x+W-1,y+H-1,c.line,0,bob);return;}
 if(m==='focus'){rmBlock(g,x,y+1,W,H-1,c.w,0,bob);if(c.p)rmBlock(g,x+1,y+1,Math.min(c.pw||2,W-1),Math.max(1,(c.ph||2)-1),c.p,0,bob);rmBlock(g,x,y,W,1,c.lineDark||c.line,0,bob);return;}
 rmBlock(g,x,y,W,H,c.w,0,bob);
 if(c.p)rmBlock(g,x+1,y+1,c.pw||2,c.ph||2,c.p,0,bob);
 if(c.hi)rmPx(g,x,y,c.hi,0,bob);
 if(m==='bright')rmPx(g,x+W,y-1,c.hi||'#ffffff',0,bob);}

function rmMask(f){const m=new Uint8Array(RM_N*RM_N);for(let y=0;y<RM_N;y++)for(let x=0;x<RM_N;x++)if(f(x+.5,y+.5))m[y*RM_N+x]=1;return m;}
const rmEll=(cx,cy,rx,ry)=>rmMask((x,y)=>((x-cx)/rx)**2+((y-cy)/ry)**2<=1);
const rmRect=(x0,y0,x1,y1,r)=>rmMask((x,y)=>{if(x<x0||x>x1||y<y0||y>y1)return false;if(!r)return true;
 const dx=Math.max(x0+r-x,0,x-(x1-r)),dy=Math.max(y0+r-y,0,y-(y1-r));return dx*dx+dy*dy<=r*r+.3;});
const rmPoly=pts=>rmMask((x,y)=>{let c=false;for(let i=0,j=pts.length-1;i<pts.length;j=i++){const[a,b]=pts[i],[p,q]=pts[j];
 if((b>y)!==(q>y)&&x<(p-a)*(y-b)/(q-b)+a)c=!c;}return c;});
function rmLine(pts,w){const m=new Uint8Array(RM_N*RM_N);for(let k=0;k<pts.length-1;k++){const[a,b]=pts[k],[c,d]=pts[k+1];
 const s=Math.max(Math.abs(c-a),Math.abs(d-b),1);for(let i=0;i<=s;i++){const x=Math.round(a+(c-a)*i/s),y=Math.round(b+(d-b)*i/s);
 for(let u=0;u<w;u++)for(let v=0;v<w;v++){const X=x+u-((w-1)>>1),Y=y+v-((w-1)>>1);if(X>=0&&Y>=0&&X<RM_N&&Y<RM_N)m[Y*RM_N+X]=1;}}}return m;}
const rmAt=(m,x,y)=>x>=0&&y>=0&&x<RM_N&&y<RM_N&&m[y*RM_N+x]===1;
/* ramps: l light, b base, d shade, o outline (tinted toward the body, never black) */
const RM_PAL={
 shell:{l:'#cfdcd9',b:'#93aaae',d:'#5b7478',o:'#1b2c32'},
 shellHi:{l:'#e6eeea',b:'#b5c7c6',d:'#7a9295',o:'#1b2c32'},
 metal:{l:'#b9c6c0',b:'#7e9092',d:'#4a5f63',o:'#1b2c32'},
 amber:{l:'#ffe2a0',b:'#f4b860',d:'#c27f2e',o:'#5a3a14'},
 mint:{l:'#e6fff0',b:'#bff6d0',d:'#7fcfa0',o:'#1b2c32'},
 raven:{l:'#6f8fa2',b:'#3f5e70',d:'#26404f',o:'#0f1b24'},
 ravenWing:{l:'#527286',b:'#2f4a5a',d:'#1d3140',o:'#0f1b24'},
 beak:{l:'#ffd98a',b:'#e0a64c',d:'#a8722a',o:'#4a2f10'},
 stone:{l:'#c3cdc2',b:'#8a9792',d:'#586764',o:'#1f2b2b'},
 stoneD:{l:'#a2afa8',b:'#6f7d7a',d:'#465452',o:'#1f2b2b'},
 moss:{l:'#c5df9e',b:'#8fb26a',d:'#5d8148',o:'#2b3b22'},
 gold:{l:'#fff0b8',b:'#f1cf78',d:'#c49a3c',o:'#5a4216'},
 tide:{l:'#8fe0e0',b:'#3f9bb4',d:'#235f84',o:'#10304a'},
 tideD:{l:'#5fc0cc',b:'#2e7f9f',d:'#1c4f73',o:'#10304a'},
 foam:{l:'#ffffff',b:'#d9fff6',d:'#9fe8d8',o:'#235f84'}
};
function rmDraw(g,parts,dx,dy){
 const out=new Array(RM_N*RM_N).fill(null);
 const put=(x,y,c)=>{x+=dx|0;y+=dy|0;if(x>=0&&y>=0&&x<RM_N&&y<RM_N)g.fillStyle=c,g.fillRect(x,y,1,1);};
 for(const p of parts){const m=p.m,r=RM_PAL[p.mat],flat=p.flat,s=p.ext||m,jn=p.join;
  if(!p.noOutline)for(let y=0;y<RM_N;y++)for(let x=0;x<RM_N;x++)if(!m[y*RM_N+x]&&!(jn&&jn[y*RM_N+x])&&(rmAt(m,x-1,y)||rmAt(m,x+1,y)||rmAt(m,x,y-1)||rmAt(m,x,y+1)))put(x,y,r.o);
  for(let y=0;y<RM_N;y++)for(let x=0;x<RM_N;x++)if(m[y*RM_N+x]){let c=r.b;
   if(!flat){const dark=!rmAt(s,x+1,y)||!rmAt(s,x,y+1)||(p.deep&&(!rmAt(s,x+2,y+1)||!rmAt(s,x+1,y+2)));
    const lite=!rmAt(s,x-1,y)||!rmAt(s,x,y-1);
    c=dark&&!lite?r.d:lite&&!dark?r.l:dark&&lite?r.b:r.b;
    if(p.dither&&c===r.b&&((x+y)&1)===0&&(!rmAt(s,x+2,y)||!rmAt(s,x,y+2)))c=r.d;}
   put(x,y,c);}}}
function rmOr(a,b){const r=new Uint8Array(RM_N*RM_N);for(let i=0;i<r.length;i++)r[i]=a[i]|b[i];return r;}
function rmPx(g,x,y,c,dx,dy){g.fillStyle=c;g.fillRect((x+(dx|0))|0,(y+(dy|0))|0,1,1);}
function rmBlock(g,x,y,w,h,c,dx,dy){g.fillStyle=c;g.fillRect((x+(dx|0))|0,(y+(dy|0))|0,w,h);}
const rmP=(m,mat,o)=>Object.assign({m,mat},o||{});
function rmBeetle(g,t){
 const bob=rmBob([0,0,1,1,0,0,0,0][Math.floor(t/500)%8]),leg=RM_S.work?RM_S.beat&1:Math.floor(t/400)%2,up=(RM_S.st==='approval'||RM_S.st==='success')?-3:0;
 const L=(a,b)=>rmLine([a,b],2);
 const parts=[
  rmP(L([14,31],[8,34+leg]),'metal',{flat:1}),rmP(L([13,36],[7,41]),'metal',{flat:1}),rmP(L([15,40],[11,45]),'metal',{flat:1}),
  rmP(L([34,31],[40,34+leg]),'metal',{flat:1}),rmP(L([35,36],[41,41]),'metal',{flat:1}),rmP(L([33,40],[37,45]),'metal',{flat:1}),
  rmP(rmLine([[19,11],[16,7],[14,4+up]],1),'metal',{flat:1}),rmP(rmLine([[29,11],[32,7],[34,4+up]],1),'metal',{flat:1}),
  rmP(rmEll(24,31,13,12),'shell',{deep:1,dither:1}),
  rmP(rmEll(24,17,8.5,6.5),'shellHi',{deep:1}),
  rmP(rmRect(23,24,24,42),'shell',{flat:1,noOutline:1}),
  rmP(rmEll(24,32,3,3),'amber',{}),
 ];
 rmDraw(g,parts,0,bob);
 rmBlock(g,13,3+up,2,2,'#f4b860',0,bob);rmBlock(g,33,3+up,2,2,'#f4b860',0,bob);
 rmEye(g,19,15,bob,{w:'#bff6d0',p:null,hi:'#ffffff',line:'#1b2c32'});rmEye(g,26,15,bob,{w:'#bff6d0',p:null,hi:'#ffffff',line:'#1b2c32'});
 rmPx(g,24,31,'#ffe2a0',0,bob);
 rmPx(g,17,28,'#e6eeea',0,bob);rmPx(g,16,31,'#e6eeea',0,bob);rmPx(g,31,28,'#5b7478',0,bob);rmPx(g,32,31,'#5b7478',0,bob);
 rmPx(g,18,38,'#5b7478',0,bob);rmPx(g,30,38,'#5b7478',0,bob);}
function rmRaven(g,t){
 const bob=rmBob([0,0,1,0][Math.floor(t/800)%4]),tilt=RM_S.work?(RM_S.beat&1):(Math.floor(t/1100)%5===2)?1:0,tw=Math.round(Math.sin(t/(RM_S.work?250:700)))+(RM_S.st==='failure'?2:0);
 const parts=[
  rmP(rmPoly([[17,36],[4,40+tw],[9,43],[3,46],[12,46],[18,41]]),'ravenWing',{deep:1}),
  rmP(rmPoly([[18,34],[7,34],[11,37],[4,38],[16,39]]),'raven',{flat:1}),
  rmP(rmLine([[20,40],[19,44],[16,45]],2),'beak',{flat:1}),rmP(rmLine([[28,40],[29,44],[32,45]],2),'beak',{flat:1}),
  rmP(rmEll(24,29,10.5,12),'raven',{deep:1,dither:1}),
  rmP(rmEll(19,30,7,9.5),'ravenWing',{deep:1}),
  rmP(rmEll(28+tilt,15,8,7.5),'raven',{deep:1}),
  rmP(rmPoly([[35+tilt,12],[45,16.5],[35+tilt,20]]),'beak',{deep:1}),
  rmP(rmPoly([[22+tilt,9],[19+tilt,2],[25+tilt,5],[28+tilt,8]]),'raven',{flat:1}),
  rmP(rmEll(28,31,4.2,4.2),'gold',{}),
 ];
 rmDraw(g,parts,0,bob);
 rmBlock(g,13,29,1,1,'#6f8fa2',0,bob);rmBlock(g,12,33,5,1,'#527286',0,bob);rmBlock(g,14,36,5,1,'#527286',0,bob);rmBlock(g,15,26,4,1,'#527286',0,bob);
 const ex=31+tilt;
 rmEye(g,ex,13,bob,{w:'#fff0a6',p:'#0f1b24',pw:1,ph:1,hi:'#ffffff',line:'#fff0a6'});
 rmBlock(g,37+tilt,14,3,1,'#ffd98a',0,bob);
 rmBlock(g,27,29,2,4,'#26404f',0,bob);rmBlock(g,26,30,4,2,'#26404f',0,bob);rmPx(g,28,28,'#fff0b8',0,bob);rmPx(g,24,31,'#c49a3c',0,bob);rmPx(g,32,31,'#c49a3c',0,bob);rmPx(g,28,35,'#c49a3c',0,bob);
 rmPx(g,24,24,'#6f8fa2',0,bob);rmPx(g,23,22,'#6f8fa2',0,bob);}
function rmGolem(g,t){
 const bob=rmBob([0,0,0,1][Math.floor(t/900)%4]),al=rmArm(-1),ar=rmArm(1);
 const parts=[
  rmP(rmRect(15,38,22,45,1),'stoneD',{deep:1}),rmP(rmRect(26,38,33,45,1),'stoneD',{deep:1}),
  rmP(rmRect(6,22+al,13,40+al,2),'stone',{deep:1,dither:1}),rmP(rmRect(35,22+ar,42,40+ar,2),'stone',{deep:1,dither:1}),
  rmP(rmRect(13,22,35,40,2),'stone',{deep:1,dither:1}),
  rmP(rmRect(15,7,33,22,2),'stone',{deep:1}),
  rmP(rmEll(10,21.5,4,2.5),'moss',{}),rmP(rmRect(23,3,24,7),'metal',{flat:1}),rmP(rmRect(12,10,14,15),'metal',{flat:1}),rmP(rmRect(34,10,36,15),'metal',{flat:1}),
 ];
 rmDraw(g,parts,0,bob);
 rmEye(g,18,12,bob,{W:4,H:4,w:'#3a4645',p:'#ffe9a2',hi:null,line:'#f1cf78'});rmEye(g,26,12,bob,{W:4,H:4,w:'#3a4645',p:'#ffe9a2',hi:null,line:'#f1cf78'});
  rmReactor(g,24,30,t,bob,false);rmPx(g,23,1,'#8fe6ff',0,bob);
 rmPx(g,10,26,'#c3cdc2',0,bob);rmPx(g,39,29,'#586764',0,bob);rmPx(g,17,35,'#586764',0,bob);rmPx(g,31,25,'#586764',0,bob);rmPx(g,16,33,'#586764',0,bob);rmPx(g,15,34,'#586764',0,bob);
 rmPx(g,8,24,'#c5df9e',0,bob);rmPx(g,12,22,'#5d8148',0,bob);rmPx(g,9,26,'#8fb26a',0,bob);rmPx(g,8,27,'#8fb26a',0,bob);}
function rmTide(g,t){
 const bob=rmBob([0,1,1,0,-1,-1,0,0][Math.floor(t/700)%8]),ph=RM_S.work?t/230:RM_S.st==='failure'?0:t/500;
 const tent=(x0,k)=>{const pts=[];for(let i=0;i<=8;i++){pts.push([x0+k*i*.6+Math.round(Math.sin(ph+i*.7+x0)*1.6*(i/8)),30+i*1.7]);}return rmLine(pts,2);};
 const parts=[
  rmP(tent(15,-.7),'tideD',{flat:1}),rmP(tent(32,.7),'tideD',{flat:1}),rmP(tent(20,-.2),'tide',{flat:1}),rmP(tent(27,.2),'tide',{flat:1}),
  rmP(rmPoly([[24,4],[33,16],[35,25],[32,33],[16,33],[13,25],[15,16]]),'tide',{deep:1,dither:1}),
 ];
 rmDraw(g,parts,0,bob);
 for(let i=0;i<4;i++){const x=15+i*5;rmPx(g,x+Math.round(Math.sin(ph+i)),44-(i&1),'#c6fff0',0,bob);}
 rmPx(g,20,14,'#8fe0e0',0,bob);rmPx(g,19,16,'#8fe0e0',0,bob);rmPx(g,20,16,'#c6fff0',0,bob);rmPx(g,18,19,'#c6fff0',0,bob);
 rmEye(g,19,21,bob,{w:'#e8ffff',p:'#10304a',hi:'#ffffff',line:'#10304a'});rmEye(g,26,21,bob,{w:'#e8ffff',p:'#10304a',hi:'#ffffff',line:'#10304a'});
 rmBlock(g,23,27,2,1,'#10304a',0,bob);rmPx(g,24,9,'#70e0d0',0,bob);}
Object.assign(RM_PAL,{
 brass:{l:'#f5dfa8',b:'#c79a52',d:'#7a5a34',o:'#2e2112'},
 glass:{l:'#fff6d8',b:'#ffd98a',d:'#e0a64c',o:'#5a3a14'},
 leather:{l:'#d49a68',b:'#a86b3e',d:'#6e4224',o:'#2c1a0e'},
 cloth:{l:'#e8d9b8',b:'#c9b48a',d:'#8f7a52',o:'#3a2e1a'},
 keystone:{l:'#f5e1a6',b:'#d9b45c',d:'#946f2c',o:'#33260f'},
 cut:{l:'#e4f6ff',b:'#8fd0e8',d:'#4f8fb8',o:'#1c3c5a'},
 cutD:{l:'#b9e2f6',b:'#62aed0',d:'#376f9c',o:'#1c3c5a'},
 velvet:{l:'#a88bc4',b:'#6e5496',d:'#43325f',o:'#1e1530'},
 velvetD:{l:'#8b72ae',b:'#54407a',d:'#33264a',o:'#1e1530'},
 clay:{l:'#f0b08a',b:'#d2774f',d:'#8f4a2f',o:'#3a1d12'},
 clayD:{l:'#dd9068',b:'#b25a38',d:'#74381f',o:'#3a1d12'},
 flame:{l:'#fff0b0',b:'#ffb347',d:'#e2603f',o:'#7a2f16'}
});
function rmSpark(g,t){
 const bob=rmBob([0,0,1,1,0,0,-1,-1][Math.floor(t/450)%8]),fl=rmFlap(Math.floor(t/260)%3,t);
 const wing=(s)=>rmPoly(s<0?[[20,24],[7,14+fl],[5,26],[8,34],[19,32]]:[[28,24],[41,14+fl],[43,26],[40,34],[29,32]]);
 const parts=[
  rmP(wing(-1),'cloth',{deep:1}),rmP(wing(1),'cloth',{deep:1}),
  rmP(rmLine([[21,38],[19,44]],2),'brass',{flat:1}),rmP(rmLine([[27,38],[29,44]],2),'brass',{flat:1}),
  rmP(rmEll(24,30,8,10),'brass',{deep:1,dither:1}),
  rmP(rmEll(24,15,7,6.5),'brass',{deep:1}),
  rmP(rmLine([[21,10],[18,5],[16,4]],1),'brass',{flat:1}),rmP(rmLine([[27,10],[30,5],[32,4]],1),'brass',{flat:1}),
  rmP(rmRect(21,26,27,35,2),'glass',{flat:1}),
 ];
 rmDraw(g,parts,0,bob);
 rmBlock(g,15,3,2,2,'#fff2c4',0,bob);rmBlock(g,31,3,2,2,'#fff2c4',0,bob);
 rmEye(g,20,14,bob,{w:'#fff6d8',p:'#2e2112',hi:'#ffffff',line:'#2e2112'});rmEye(g,26,14,bob,{w:'#fff6d8',p:'#2e2112',hi:'#ffffff',line:'#2e2112'});
 rmBlock(g,23,28+(fl===0?1:0),2,5,RM_S.st==='failure'?'#8f4a2f':'#ffb347',0,bob);rmPx(g,23,28,'#fff0b0',0,bob);rmPx(g,13,21,'#e8d9b8',0,bob);rmPx(g,36,25,'#8f7a52',0,bob);rmPx(g,10,27,'#8f7a52',0,bob);}
Object.assign(RM_PAL,{fur:{l:'#f0a868',b:'#d4743c',d:'#8f4526',o:'#3a1c10'},cream:{l:'#fffaf0',b:'#f2e3c8',d:'#c9b08a',o:'#5a4430'},bag:{l:'#c98a5a',b:'#8f5a34',d:'#573419',o:'#241408'},
 brick:{l:'#f2b08a',b:'#d2774f',d:'#93482e',o:'#3a1d12'},brickD:{l:'#c98a68',b:'#a45a3a',d:'#6a3520',o:'#3a1d12'}});
function rmRascal(g,t){
 const HD=rmEll(28,17,8,7),SN=rmPoly([[27,13],[10,20],[9,23],[12,26],[27,25]]);
 const bob=rmBob([0,0,1,0][Math.floor(t/600)%4]),tl=RM_S.st==='failure'?3:Math.round(Math.sin(t/(RM_S.work?200:420))*(RM_S.work?3:2)),ear=RM_S.st==='failure'?-3:(RM_S.st==='approval'||RM_S.st==='success')?2:(Math.floor(t/1700)%6===3)?1:0;
 const parts=[
  rmP(rmLine([[33,34],[40,31+tl],[43,24+tl],[41,17+tl]],4),'fur',{flat:1}),
  rmP(rmLine([[41,19+tl],[41,17+tl]],4),'cream',{flat:1}),
  rmP(rmLine([[19,38],[18,44]],3),'bag',{flat:1}),rmP(rmLine([[28,38],[29,44]],3),'bag',{flat:1}),
  rmP(rmEll(25,30,9.5,10.5),'fur',{deep:1,dither:1}),
  rmP(rmEll(23,34,5,6.5),'cream',{deep:1}),
  rmP(rmPoly([[22,13],[22,2-ear],[29,10]]),'fur',{deep:1}),rmP(rmPoly([[31,12],[34,2-ear],[36,14]]),'fur',{deep:1}),
  rmP(HD,'fur',{deep:1}),
  rmP(SN,'cream',{flat:1,join:HD,ext:rmOr(HD,SN)}),
  rmP(rmRect(26,26,38,38,2),'bag',{deep:1}),
 ];
 rmDraw(g,parts,0,bob);
 rmPx(g,9,21,'#2a1810',0,bob);rmBlock(g,8,21,3,2,'#2a1810',0,bob);rmBlock(g,12,25,9,1,'#c9b08a',0,bob);rmBlock(g,12,24,4,1,'#f2e3c8',0,bob);

 rmEye(g,16,14,bob,{W:5,H:4,w:'#fffaf0',p:'#2a1810',pw:3,ph:3,hi:null,line:'#fffaf0',lineDark:'#2a1810'});
 rmBlock(g,23,26,2,12,'#573419',0,bob);rmBlock(g,24,29,1,3,'#f0c46a',0,bob);
 rmBlock(g,27,25,10,2,'#8f5a34',0,bob);rmBlock(g,29,28,6,3,'#fffaf0',0,bob);rmBlock(g,30,29,3,1,'#c9b08a',0,bob);
 rmBlock(g,31,33,2,2,'#f0c46a',0,bob);rmPx(g,31,33,'#fff0b8',0,bob);
 rmBlock(g,17,44,4,1,'#573419',0,bob);rmBlock(g,27,44,5,1,'#573419',0,bob);rmPx(g,25,24,'#f0a868',0,bob);rmPx(g,26,20,'#f0a868',0,bob);}
function rmPrime(g,t){
 const bob=rmBob([0,0,0,1][Math.floor(t/850)%4]),al=rmArm(-1),ar=rmArm(1);
 const parts=[
  rmP(rmRect(15,39,22,45,1),'brass',{deep:1}),rmP(rmRect(26,39,33,45,1),'brass',{deep:1}),
  rmP(rmRect(8,24+al,14,38+al,2),'brass',{deep:1}),rmP(rmRect(34,24+ar,40,38+ar,2),'brass',{deep:1}),
  rmP(rmPoly([[14,22],[34,22],[31,40],[17,40]]),'keystone',{deep:1,dither:1}),
  rmP(rmPoly([[16,5],[32,5],[35,22],[13,22]]),'keystone',{deep:1}),
    rmP(rmLine([[24,2],[24,5]],2),'brass',{flat:1}),
 ];
 rmDraw(g,parts,0,bob);
 rmEye(g,18,11,bob,{W:4,H:4,w:'#33260f',p:'#fff0b8',hi:null,line:'#33260f',lineLight:1});rmEye(g,26,11,bob,{W:4,H:4,w:'#33260f',p:'#fff0b8',hi:null,line:'#33260f'});
  rmReactor(g,24,31,t,bob,true);
 rmPx(g,23,1,'#fff0b8',0,bob);rmBlock(g,17,24,2,1,'#946f2c',0,bob);rmBlock(g,29,24,2,1,'#946f2c',0,bob);rmPx(g,10,28,'#f5dfa8',0,bob);rmPx(g,16,8,'#f5e1a6',0,bob);}
function rmPrism(g,t){
 const bob=rmBob([0,1,1,0,-1,-1,0,0][Math.floor(t/600)%8]),sw=RM_S.st==='failure'?0:Math.round(Math.sin(t/(RM_S.work?200:420))*(RM_S.work?3:2));
 const parts=[
  rmP(rmPoly([[8,24+sw],[2,14+sw],[2,34+sw]]),'cutD',{deep:1}),
  rmP(rmPoly([[22,12],[18,4],[28,10]]),'cutD',{flat:1}),rmP(rmPoly([[22,36],[18,44],[28,38]]),'cutD',{flat:1}),
  rmP(rmPoly([[8,24],[18,10],[34,10],[44,24],[34,38],[18,38]]),'cut',{deep:1,dither:1}),
  rmP(rmPoly([[14,24],[22,16],[30,16],[36,24],[30,32],[22,32]]),'cutD',{}),
 ];
 rmDraw(g,parts,0,bob);
 rmEye(g,31,19,bob,{W:4,H:4,w:'#fff6d8',p:'#1c3c5a',hi:'#ffffff',line:'#1c3c5a'});
 rmPx(g,26,26,'#1c3c5a',0,bob);rmBlock(g,28,28,3,1,'#1c3c5a',0,bob);
 rmBlock(g,20,22,5,3,'#ffd98a',0,bob);rmPx(g,21,22,'#fff6d8',0,bob);
 rmPx(g,14,18,'#fff',0,bob);rmPx(g,15,19,'#e4f6ff',0,bob);rmPx(g,24,13,'#fff',0,bob);rmPx(g,12,26,'#4f8fb8',0,bob);}
function rmVeil(g,t){
 const bob=rmBob([0,0,1,1,0,0,-1,-1][Math.floor(t/500)%8]),fl=rmFlap(Math.round(Math.sin(t/260)*2),t);
 const wing=s=>rmPoly(s<0?[[20,24],[5,13+fl],[3,26],[7,38],[20,34]]:[[28,24],[43,13+fl],[45,26],[41,38],[28,34]]);
 const parts=[
  rmP(wing(-1),'velvetD',{deep:1,dither:1}),rmP(wing(1),'velvetD',{deep:1,dither:1}),
  rmP(rmLine([[22,38],[20,44]],2),'velvetD',{flat:1}),rmP(rmLine([[26,38],[28,44]],2),'velvetD',{flat:1}),
  rmP(rmEll(24,29,7.5,11),'velvet',{deep:1,dither:1}),
  rmP(rmEll(24,15,7,6.5),'velvet',{deep:1}),
  rmP(rmLine([[21,10],[17,4],[14,3]],1),'velvet',{flat:1}),rmP(rmLine([[27,10],[31,4],[34,3]],1),'velvet',{flat:1}),
 ];
 rmDraw(g,parts,0,bob);
 rmEye(g,20,14,bob,{w:'#f6eeff',p:'#1e1530',hi:'#ffffff',line:'#f0e6ff'});rmEye(g,26,14,bob,{w:'#f6eeff',p:'#1e1530',hi:'#ffffff',line:'#f0e6ff'});
 rmPx(g,10,22+fl,'#f0c46a',0,bob);rmPx(g,38,22+fl,'#f0c46a',0,bob);rmPx(g,12,30,'#f0c46a',0,bob);rmPx(g,36,30,'#f0c46a',0,bob);rmPx(g,9,28,'#a88bc4',0,bob);rmPx(g,39,28,'#a88bc4',0,bob);
 rmBlock(g,22,26,4,1,'#a88bc4',0,bob);rmBlock(g,22,30,4,1,'#a88bc4',0,bob);rmBlock(g,23,34,2,1,'#a88bc4',0,bob);}
function rmEmber(g,t){
 const BD=rmRect(11,14,37,43,9),CH=rmRect(29,3,36,20,0),CAP=rmRect(27,0,38,4,1);
 const bob=rmBob([0,0,1,0][Math.floor(t/650)%4]),fl=Math.floor(t/(RM_S.work?85:180))%4,dim=RM_S.st==='failure';
 const parts=[
  rmP(rmRect(13,43,21,46,1),'brickD',{flat:1}),rmP(rmRect(27,43,35,46,1),'brickD',{flat:1}),
  rmP(rmRect(8,27,13,38,2),'brickD',{deep:1}),rmP(rmRect(35,27,40,38,2),'brickD',{deep:1}),
    rmP(BD,'brick',{deep:1,dither:1}),
  rmP(CH,'brick',{deep:1,join:BD,ext:rmOr(CH,BD)}),
  rmP(CAP,'brickD',{deep:1,join:CH,ext:rmOr(CAP,CH)}),
 ];
 rmDraw(g,parts,0,bob);
 for(let y=19;y<41;y+=5){rmBlock(g,12,y,24,1,'#93482e',0,bob);for(let x=(y%10===4?15:19);x<35;x+=8)rmBlock(g,x,y+1,1,4,'#93482e',0,bob);}
 rmBlock(g,29,8,8,1,'#93482e',0,bob);rmBlock(g,29,12,8,1,'#93482e',0,bob);rmBlock(g,29,16,8,1,'#93482e',0,bob);rmBlock(g,33,5,1,3,'#93482e',0,bob);rmBlock(g,31,9,1,3,'#93482e',0,bob);rmBlock(g,34,13,1,3,'#93482e',0,bob);
 const m=[[18,30],[30,30]];
 rmBlock(g,18,28,12,13,'#3a1d12',0,bob);rmBlock(g,19,27,10,1,'#3a1d12',0,bob);rmBlock(g,20,26,8,1,'#3a1d12',0,bob);
 rmBlock(g,19,29,10,11,dim?'#6a3520':'#e2603f',0,bob);rmBlock(g,20,28,8,1,dim?'#6a3520':'#e2603f',0,bob);
 if(dim){rmBlock(g,21,36,6,4,'#a45a3a',0,bob);rmBlock(g,23,38,2,2,'#e2603f',0,bob);}else{rmBlock(g,20,32,8,8,'#ffb347',0,bob);rmBlock(g,22,29+(fl&1),4,3,'#ffb347',0,bob);rmBlock(g,22,35,4,5,'#fff0b0',0,bob);rmPx(g,23+(fl%2),31+(fl>1?1:0),'#fff0b0',0,bob);rmPx(g,21,29,'#ffb347',0,bob);}
 rmEye(g,16,20,bob,{W:4,H:4,w:'#3a1d12',p:'#ffe0a0',hi:null,line:'#3a1d12'});rmEye(g,28,20,bob,{W:4,H:4,w:'#3a1d12',p:'#ffe0a0',hi:null,line:'#3a1d12'});
 rmPx(g,15,17,'#f2b08a',0,bob);rmPx(g,16,16,'#f2b08a',0,bob);rmPx(g,10,30,'#f2b08a',0,bob);
}

function rmReactor(g,cx,cy,t,bob,warm){const pu=RM_S.reduced?1:.75+.25*Math.sin(t/(RM_S.work?160:500)),bad=RM_S.st==='failure',win=RM_S.st==='success';
 for(let dy=-5;dy<=5;dy++)for(let dx=-5;dx<=5;dx++){const d=Math.hypot(dx,dy);let c=null;
  if(d<=5.2&&d>4.2)c=warm?'#7a5a34':'#2f3b3b';else if(d<=4.2&&d>3.4)c=warm?'#c79a52':'#7e9092';
  else if(d<=3.4){c=warm?(pu>.9?'#ffe9a8':'#ffd27a'):(pu>.9?'#d8fbff':'#8fe6ff');
   if(d<=1.7)c='#ffffff';if(bad)c=d<=1.7?'#a8483f':'#6a2a2a';else if(win&&d>1.7)c='#ffffff';}
  if(c)rmPx(g,cx+dx,cy+dy,c,0,bob);}
 const sp=warm?'#9a6b2c':'#3d7f95';for(const a of [-90,30,150]){const r=a*Math.PI/180;for(let k=2;k<=3.2;k+=.5)rmPx(g,Math.round(cx+Math.cos(r)*k),Math.round(cy+Math.sin(r)*k),sp,0,bob);}}

/* ---- registration ---------------------------------------------------------------- */
/* A mirrored, offset view of the canvas: the raven faces left and the shake moves the body. */
function rmView(g,flip,dx){return{set fillStyle(c){g.fillStyle=c;},get fillStyle(){return g.fillStyle;},
 fillRect(x,y,w,h){g.fillRect(flip?RM_N-x-w+dx:x+dx,y,w,h);}};}
const RM_MARK={outline:'#2b2a28',metal:'#98a3a0',metalLight:'#ecf1ea',accent:'#f4b860',danger:'#ef6f65',body:'#9aa6a2',dark:'#586764',light:'#cdd8d3'};
function rmBang(g,x,y){aRect(g,x,y,2,5,'#f4b860');aRect(g,x,y+6,2,2,'#f4b860');}
function rmGlint(g,x,y,T){if(Math.floor(T/200)%2){aRect(g,x,y-1,1,3,'#fff0b8');aRect(g,x-1,y,3,1,'#fff0b8');}}
function rmMake(fn,o){return function(g,pal,P,t,reduced){
 const T=reduced?0:t,st=P.raw;
 RM_S={st:st,act:P.act,mood:P.expr,T:T,beat:Math.floor(T/150)%4,work:!!P.working,lift:Math.round(P.lift||0),shake:reduced?0:(P.shake|0),
  drop:st==='failure'?Math.min(2,Math.round(Math.max(0,P.headDrop||0)*2)):0,reduced:!!reduced};
 fn(rmView(g,!!o.flip,RM_S.shake),T);
 /* Markers and activity props sit outside the body; the pet's own side is chosen by the creature. */
 const pg=rmView(g,!!o.propLeft,0);
 aActProp(pg,RM_MARK,P,T);
 if(st==='approval')rmBang(pg,40,5);
 if(st==='success'){rmGlint(pg,40,8,T);rmGlint(pg,6,12,T+200);}
 if(st==='failure'){aRect(pg,40,9,2,3,'#9fd8ee');aRect(pg,40,12,2,1,'#6fb2d2');}};}
const RM_ROSTER={
 rm_beetle:{name:'MORROW BEETLE · REMASTERED',note:'remastered mechanical beetle',draw:rmMake(rmBeetle,{})},
 rm_raven:{name:'CLOCKWORK RAVEN · REMASTERED',note:'remastered clockwork raven',draw:rmMake(rmRaven,{flip:1})},
 rm_golem:{name:'STONE GOLEM · REMASTERED',note:'remastered stone golem',draw:rmMake(rmGolem,{})},
 rm_tide:{name:'TIDE WISP · REMASTERED',note:'remastered tide wisp',draw:rmMake(rmTide,{})},
 rm_spark:{name:'SPARK · Lamplighter · REMASTERED',note:'remastered brass moth-lantern',draw:rmMake(rmSpark,{})},
 rm_rascal:{name:'RASCAL · Courier · REMASTERED',note:'remastered fox courier',draw:rmMake(rmRascal,{})},
 rm_prime:{name:'PRIME · Keykeeper · REMASTERED',note:'remastered keystone keeper',draw:rmMake(rmPrime,{})},
 rm_prism:{name:'PRISM · Lanternfin · REMASTERED',note:'remastered cut-glass fish',draw:rmMake(rmPrism,{})},
 rm_veil:{name:'VEIL · Duskmoth · REMASTERED',note:'remastered velvet moth',draw:rmMake(rmVeil,{})},
 rm_ember:{name:'EMBER · Kiln · REMASTERED',note:'remastered brick kiln sprite',draw:rmMake(rmEmber,{propLeft:1})}};
Object.keys(RM_ROSTER).forEach(function(id){A_PETS[id]=Object.assign({remaster:true},RM_ROSTER[id]);});
"""
