"""Shared web pet art, gesture-gated audio, and the Toybox import controls."""
from __future__ import annotations

import base64
import json
from functools import lru_cache
from io import BytesIO

from core.i18n.catalog import load_catalog_data
from core.pet_cosmetics import EVENTS, EXPRESSIONS
from core.pet_world.catalog import ROSTER


@lru_cache(maxsize=1)
def art_data():
    from PIL import Image

    from core.pet_world.anime_art import EXPRESSIONS as POSES
    from core.pet_world.anime_art import MASCOTS, mascot_pixels

    data = {}
    for kind in MASCOTS:
        image = Image.new('RGBA', (48*len(POSES), 48))
        for index, pose in enumerate(POSES):
            for y, row in enumerate(mascot_pixels(kind, pose)):
                for x, color in enumerate(row):
                    if color:
                        image.putpixel((index*48+x, y), (*bytes.fromhex(color[1:]), 255))
        out = BytesIO()
        image.save(out, 'PNG')
        data[kind] = {'url': 'data:image/png;base64,'+base64.b64encode(out.getvalue()).decode(),
                      'poses': {pose: i for i, pose in enumerate(POSES)}}
    return data


def render_pet_cosmetics_fragment():
    messages = {k: v for k, v in load_catalog_data('en')['messages'].items() if k.startswith('yard.')}
    return '<style>'+CSS+'</style><script>'+JS.replace('__ART__', json.dumps(art_data())).replace('__TEXT__', json.dumps(messages)).replace('__ROSTER__', json.dumps(ROSTER)).replace('__EVENTS__', json.dumps(EVENTS)).replace('__EXPRESSIONS__', json.dumps(EXPRESSIONS))+'</script>'


CSS = """
.pc-panel{margin:18px 0;padding:18px;border:1px solid var(--border,#696b79);border-radius:12px;background:var(--panel,#20232c);color:var(--ink,var(--text,#eee))}
.pc-panel label{display:inline-flex;gap:8px;align-items:center;margin:6px 12px 6px 0;flex-wrap:wrap}.pc-panel select,.pc-panel input,.pc-panel button{font:inherit;max-width:100%;padding:7px;border:1px solid var(--border,#696b79);border-radius:6px;background:var(--bg,#151820);color:inherit}.pc-panel button{cursor:pointer;margin:4px}.pc-panel canvas{image-rendering:pixelated;background:var(--bg,#151820);border:1px solid var(--border,#696b79)}.pc-row{display:flex;align-items:center;gap:10px;flex-wrap:wrap}.pc-notice{min-height:1.5em}.pc-panel summary{cursor:pointer;padding:8px}.pc-panel a{display:inline-block;margin-inline-end:12px;color:var(--accent,#c4b4f4)}
"""

JS = r"""
(()=>{'use strict';if(window.VoolPetCosmetics)return;
const art=__ART__,english=__TEXT__,events=__EVENTS__,expressions=__EXPRESSIONS__;
const t=(k)=>{let key='yard.'+k,v=window.VOOLT?.(key);return v&&v!==key?v:english[key]||k;};
const el=(tag,text)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;return n;};
const option=(value,label)=>{const n=el('option',label);n.value=value;return n;};
async function request(action='',body){const r=await fetch('/api/pet-cosmetics'+(action?'/'+action:''),body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{cache:'no-store'});const j=await r.json();if(!r.ok||!j.ok)throw Object.assign(new Error(j.error),{code:j.error});return j;}
let library={pets:[],sounds:[],config:{selected:'',sound:false,voice:'auto',language:'auto',bindings:{}},revision:0},ctx=null,playing=null,lastSound=-Infinity,playEpoch=0;
const images=new Map(),audio=new Map(),views=new Map();let mediaVersion=0;let currentState='unknown',currentCategory='',started=false,error='',plays=0,activePet='spark',mainDraw=null,initialRead=null;
function image(url){if(images.has(url)){const v=images.get(url);images.delete(url);images.set(url,v);return v;}const v=new Image();v.onload=()=>{mediaVersion++;repaintMain();window.dispatchEvent(new Event('vool-pet-media'));};v.onerror=()=>{error=t('asset_error');};v.src=url;images.set(url,v);while(images.size>4)images.delete(images.keys().next().value);return v;}
const meta=id=>library.pets.find(p=>p.id===id);
function binding(id,event){const p=meta(id),b=library.config.bindings[id]?.[event]||p?.bindings[event];return b?{expression:b.expression,sound:p?.clips[b.sound]?.sha256||b.sound}:null;}
const workEvents={READING_FILES:'read',WEB_RESEARCH:'search',APPLYING_CHANGES:'code',TESTING_CI:'test',GIT_INSPECTION:'git',RUNNING_TOOLS:'run'};
function workEvent(state,category,id){const event=workEvents[category];return ['starting','thinking','tool','waiting','retry'].includes(state)&&event&&binding(id,event)?event:state;}
function draw(g,id,state,frame=0,direction='front'){
 const p=meta(id),choice=binding(id,state),pose=choice?.expression||state;let img,index,width;
 if(p){const list=(p.frames[pose]||p.frames[state]||p.frames.unknown)[direction]||p.frames.unknown.front;index=list[Math.max(0,frame)%list.length];width=p.atlas.width;img=image('/api/pet-cosmetics/assets/'+p.atlas.sha256);}
 else if(art[id]){const a=art[id],mapped={success:'happy',approval:'wave',failure:'unknown',cancelled:'unknown',starting:'surprise',thinking:'work',tool:'work',waiting:'nap',retry:'spar'};index=a.poses[pose]??a.poses[mapped[pose]]??a.poses.idle;width=48*Object.keys(a.poses).length;img=image(a.url);}
 else return false;
 if(!img.complete||!img.naturalWidth){if(img.complete)error=t('asset_error');return false;}g.clearRect(0,0,48,48);g.imageSmoothingEnabled=false;g.drawImage(img,(index%(width/48))*48,Math.floor(index/(width/48))*48,48,48,0,0,48,48);return true;
}
function stop(){playEpoch++;if(playing){try{playing.stop();}catch(_){}playing=null;}}
async function unlock(){if(!library.config.sound)return false;ctx??=new (window.AudioContext||window.webkitAudioContext)();await ctx.resume();return ctx.state==='running';}
document.addEventListener('pointerdown',()=>{unlock().catch(()=>{error=t('audio_error');});},{passive:true});document.addEventListener('keydown',()=>{unlock().catch(()=>{error=t('audio_error');});});document.addEventListener('visibilitychange',()=>{if(document.hidden)stop();});window.addEventListener('blur',stop);
function autoSound(id,event){const b=binding(id,event);if(b&&b.sound!=='auto')return b.sound;const emotion=['love','happy','surprise','spar'].includes(event)?event:event==='success'?'happy':event==='approval'?'surprise':'';if(!emotion)return '';const voice=library.config.voice==='auto'?(id==='dusk-lynx'?'deep':'bright'):library.config.voice;const lang=library.config.language==='auto'?(document.documentElement.lang.startsWith('ja')?'ja':document.documentElement.lang.startsWith('zh')?'zh':'en'):library.config.language;return 'builtin:'+voice+'-'+lang+'-'+emotion;}
async function play(id,event,preview=false){if(!library.config.sound||document.hidden||(!preview&&performance.now()-lastSound<4000))return false;if(!ctx||ctx.state!=='running')return false;const sound=autoSound(id,event);if(!sound)return false;const ticket=++playEpoch;try{let buffer=audio.get(sound);if(!buffer){const url=sound.startsWith('builtin:')?'/api/pet-cosmetics/voices/'+sound.slice(8):'/api/pet-cosmetics/assets/'+sound;const r=await fetch(url);if(!r.ok)throw Error();buffer=await ctx.decodeAudioData(await r.arrayBuffer());audio.set(sound,buffer);while(audio.size>4)audio.delete(audio.keys().next().value);}if(document.hidden||!library.config.sound||ticket!==playEpoch)return false;stop();const source=ctx.createBufferSource();playing=source;source.buffer=buffer;const gain=ctx.createGain();gain.gain.value=.65;playing.connect(gain).connect(ctx.destination);source.onended=()=>{if(playing===source)playing=null;};playing.start();plays++;lastSound=performance.now();return true;}catch(_){error=t('audio_error');return false;}}
function activity(state,token,category=''){if(['unknown','failure','cancelled'].includes(state))stop();currentState=state;currentCategory=category;const key=state+':'+category,old=views.get(token);views.delete(token);views.set(token,key);while(views.size>64)views.delete(views.keys().next().value);if(!old||old===key||document.hidden)return;const id=library.config.selected||activePet,event=workEvent(state,category,id);if(!autoSound(id,event))stop();else play(id,event);}
function ready(){return initialRead||(initialRead=refresh());}
function repaintMain(){if(mainDraw&&!document.hidden&&mainDraw.g.canvas.isConnected){const w=window.VoolCompanionWorld;if(mainDraw.scene)w?.scene(mainDraw.g,mainDraw.scene,mainDraw.frame,mainDraw.opts);else w?.draw(mainDraw.g,mainDraw.state,mainDraw.frame,mainDraw.opts);}}
// An answer without the library's shape (an older server, a proxy, a test double) keeps the defaults instead of breaking every page that hosts the pet.
function acceptLibrary(value){stop();const base={pets:[],sounds:[],config:{selected:'',sound:false,voice:'auto',language:'auto',bindings:{}},revision:0};if(!value||typeof value!=='object'){library=base;return;}const config=value.config&&typeof value.config==='object'?{...base.config,...value.config}:base.config;library={...value,pets:Array.isArray(value.pets)?value.pets:[],sounds:Array.isArray(value.sounds)?value.sounds:[],config:{...config,bindings:config.bindings&&typeof config.bindings==='object'?config.bindings:{}},revision:Number.isInteger(value.revision)?value.revision:0};}
async function refresh(){try{acceptLibrary(await request());repaintMain();window.dispatchEvent(new Event('vool-pets-changed'));}catch(_){error=t('unavailable');}return library;}
async function duration(file){const media=document.createElement('audio'),url=URL.createObjectURL(file);try{return await new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(Error('audio')),8000);const end=(error)=>{clearTimeout(timer);if(error||!Number.isFinite(media.duration)||media.duration<.1||media.duration>5)reject(Error('audio'));else resolve(media.duration);};media.preload='metadata';media.onloadedmetadata=()=>end(false);media.onerror=()=>end(true);media.src=url;});}finally{media.removeAttribute('src');media.load();URL.revokeObjectURL(url);}}
async function wavFile(file){if(file.size>2*1024*1024)throw Error('size');await duration(file);const ac=new (window.AudioContext||window.webkitAudioContext)();try{const decoded=await ac.decodeAudioData(await file.arrayBuffer());if(decoded.duration<.1||decoded.duration>5||decoded.numberOfChannels>2)throw Error('audio');const off=new OfflineAudioContext(1,Math.ceil(decoded.duration*24000),24000),src=off.createBufferSource();src.buffer=decoded;src.connect(off.destination);src.start();const samples=(await off.startRendering()).getChannelData(0),raw=new ArrayBuffer(44+samples.length*2),v=new DataView(raw),write=(p,s)=>{for(let i=0;i<s.length;i++)v.setUint8(p+i,s.charCodeAt(i));};write(0,'RIFF');v.setUint32(4,36+samples.length*2,true);write(8,'WAVEfmt ');v.setUint32(16,16,true);v.setUint16(20,1,true);v.setUint16(22,1,true);v.setUint32(24,24000,true);v.setUint32(28,48000,true);v.setUint16(32,2,true);v.setUint16(34,16,true);write(36,'data');v.setUint32(40,samples.length*2,true);for(let i=0;i<samples.length;i++)v.setInt16(44+i*2,Math.round(Math.max(-1,Math.min(1,samples[i]))*32767),true);let s='';for(const b of new Uint8Array(raw))s+=String.fromCharCode(b);return btoa(s);}finally{await ac.close();}}
function mount(host){if(host.querySelector('.pc-panel'))return;const panel=el('section');panel.className='pc-panel';panel.id='pet-cosmetics';host.insertBefore(panel,host.querySelector('.tb-grid'));let staged='',draft=null;
 const status=el('p');status.className='pc-notice';status.setAttribute('aria-live','polite');const say=(key)=>status.textContent=t(key);
 const btn=(key,fn)=>{const b=el('button',t(key));b.type='button';b.onclick=async()=>{b.disabled=true;try{await fn();}catch(e){say(e.code||e.message||'invalid');}finally{b.disabled=false;}};return b;};
 const select=(key,values)=>{const l=el('label',t(key)),s=el('select');s.setAttribute('aria-label',t(key));for(const [v,k] of values)s.append(option(v,k));l.append(s);return [l,s];};
 async function mutate(action,body){acceptLibrary(await request(action,{revision:library.revision,...body}));render();say('saved');window.dispatchEvent(new Event('vool-pets-changed'));}
 function render(){panel.replaceChildren(el('h2',t('pets_sounds')),el('p',t('imports_help')));const link=el('a',t('open'));link.href='/pet-yard';if(location.pathname!=='/pet-yard')panel.append(link);
 const [sl,selected]=select('pet',[['',t('default_pet')],...__ROSTER__.map(id=>[id,t(id)]),...library.pets.map(p=>[p.id,p.name])]);selected.value=library.config.selected;const sound=el('input');sound.type='checkbox';sound.checked=library.config.sound;sound.onchange=()=>{if(!sound.checked)stop();};const soundLabel=el('label',t('sound'));soundLabel.append(sound);const [vl,voice]=select('voice',[['auto',t('automatic')],['bright',t('bright')],['deep',t('deep')]]);voice.value=library.config.voice;const [ll,language]=select('language',[['auto',t('automatic')],['en','English'],['ja','日本語'],['zh','中文']]);language.value=library.config.language;
 panel.append(sl,soundLabel,vl,ll,btn('save',async()=>{await mutate('configure',{config:{...library.config,selected:selected.value,sound:sound.checked,voice:voice.value,language:language.value}});await unlock();}));
 const details=el('details'),summary=el('summary',t('assign'));details.append(summary);const [pl,pet]=select('pet',[...Object.keys(art).map(id=>[id,t(id)]),...__ROSTER__.filter(id=>!art[id]).map(id=>[id,t(id)]),...library.pets.map(p=>[p.id,p.name])]);
 const [evl,event]=select('event',events.map(k=>[k,t(k)])),[exl,expression]=select('reaction',[]),[sol,clip]=select('voice',[]);const preview=el('canvas');preview.width=96;preview.height=96;preview.setAttribute('aria-label',t('preview'));
 function choices(){expression.replaceChildren();for(const k of Object.keys(meta(pet.value)?.frames||Object.fromEntries(expressions.map(x=>[x,true]))))expression.append(option(k,t(k)));clip.replaceChildren(option('auto',t('automatic')),option('',t('silent')));for(const v of ['bright','deep'])for(const l of ['en','ja','zh'])for(const e of ['love','happy','surprise','spar'])clip.append(option('builtin:'+v+'-'+l+'-'+e,t(v)+' · '+l+' · '+t(e)));for(const p of library.pets)for(const c of Object.values(p.clips))clip.append(option(c.sha256,p.name+' · '+c.name));for(const s of library.sounds)clip.append(option(s.id,s.name));const b=binding(pet.value,event.value);expression.value=b?.expression||((event.value in (meta(pet.value)?.frames||{}))?event.value:'idle');clip.value=b?.sound??'auto';if(['unknown','failure','cancelled'].includes(event.value)){expression.value=event.value;expression.disabled=true;}else expression.disabled=false;}
 pet.onchange=choices;event.onchange=choices;choices();details.append(pl,evl,exl,sol,preview,btn('preview',async()=>{const off=document.createElement('canvas');off.width=off.height=48;const g=off.getContext('2d');draw(g,pet.value,expression.value);const p=preview.getContext('2d');p.imageSmoothingEnabled=false;p.clearRect(0,0,96,96);p.drawImage(off,0,0,96,96);await unlock();const previous=library.config.bindings[pet.value];library.config.bindings[pet.value]={...previous,[event.value]:{expression:expression.value,sound:clip.value}};try{await play(pet.value,event.value,true);}finally{if(previous)library.config.bindings[pet.value]=previous;else delete library.config.bindings[pet.value];}if(error)status.textContent=error;}),btn('save_assignment',()=>mutate('configure',{config:{...library.config,bindings:{...library.config.bindings,[pet.value]:{...library.config.bindings[pet.value],[event.value]:{expression:expression.value,sound:clip.value}}}}})));panel.append(details);
 const files=el('details');files.append(el('summary',t('import_pet')),el('p',t('pack_help')));const file=el('input');file.type='file';file.accept='.voolpet,.json,application/json';file.setAttribute('aria-label',t('import_pet'));const review=el('p');const importButton=btn('import',()=>mutate('import',{document:staged}));importButton.disabled=true;file.onchange=async()=>{importButton.disabled=true;try{const f=file.files[0];if(!f||f.size>4194304)throw Error('size');staged=await f.text();draft=await request('preview',{document:staged});review.textContent=draft.pet.name+' · '+draft.pet.author+' · '+draft.pet.license;const im=el('img');im.alt=t('preview');im.width=192;im.style.imageRendering='pixelated';im.style.maxHeight='240px';im.style.objectFit='contain';im.src=draft.preview;review.append(im);importButton.disabled=false;say('review_import');}catch(e){say(e.code||e.message||'invalid');}};files.append(file,review,importButton);panel.append(files);
 const sf=el('input');sf.type='file';sf.accept='audio/wav,audio/mpeg,.wav,.mp3';sf.setAttribute('aria-label',t('import_sound'));sf.onchange=async()=>{try{const f=sf.files[0];if(!f)return;const wav=await wavFile(f);await mutate('sound',{name:f.name.slice(0,64),wav});}catch(e){say(e.code||e.message||'audio');}};const sfl=el('label',t('import_sound'));sfl.append(sf);panel.append(sfl);
 for(const p of [...library.pets,...library.sounds]){const row=el('div');row.className='pc-row';row.append(el('span',p.name));if(p.atlas)row.append(btn('export',async()=>{const j=await request('export',{id:p.id});const url=URL.createObjectURL(new Blob([j.document],{type:'application/json'})),a=el('a');a.href=url;a.download=p.source_id+'.voolpet';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}));row.append(btn('remove',async()=>{if(confirm(t('confirm_remove')))await mutate('remove',{id:p.id,confirmed:true});}));panel.append(row);}
 const creator=el('details');creator.append(el('summary',t('create')),el('p',t('creator_help')));panel.append(creator);if(library.read_only){say(library.notice);panel.append(btn('reset_options',async()=>{if(confirm(t('confirm_reset')))await mutate('reset',{stamp:library.stamp,confirmed:true});}));}panel.append(status);}
 refresh().then(render);
}
function wrap(){const world=window.VoolCompanionWorld;if(!world||world.draw.__custom)return;const original=world.draw;function custom(g,state,frame,opts){if(!g.canvas.matches?.('#companionLayer > .vool-ninja > canvas'))return original(g,state,frame,opts);try{mainDraw={g,state,frame,opts};activePet=library.config.selected||opts?.character||'spark';const event=workEvent(state,currentCategory,activePet);
 if(draw(g,activePet,event,frame))return;const choice=binding(activePet,event),pose=choice?.expression||state;original(g,['love','happy','surprise','spar','nap','work','wave'].includes(pose)?state:pose,frame,{...opts,character:activePet});if(['love','happy','surprise','spar'].includes(pose)){g.fillStyle=pose==='love'?'#ee9eb4':'#f0d69b';if(pose==='love'){g.fillRect(1,3,4,5);g.fillRect(7,3,4,5);g.fillRect(3,7,6,5);g.fillRect(5,12,2,2);}else{g.fillRect(1,4,10,3);g.fillRect(5,0,3,11);}}}finally{window.dispatchEvent(new Event('vool-companion-painted'));}}custom.__custom=true;world.draw=custom;const sceneDraw=world.scene;world.scene=function(g,scene,frame,opts){if(!g.canvas.matches?.('#companionLayer > .vool-ninja > canvas'))return sceneDraw(g,scene,frame,opts);try{activePet=library.config.selected||opts?.character||'spark';const sceneState=['starting','thinking','tool','retry'].includes(currentState)?currentState:'tool';mainDraw={g,scene,state:sceneState,frame,opts};if(['pair','huddle'].includes(scene)&&draw(g,activePet,sceneState,frame)){g.fillStyle='#f2dda8';for(let i=0;i<(scene==='pair'?2:3);i++)g.fillRect(2+i*5,43,3,3);return true;}return sceneDraw(g,scene,frame,{...opts,character:activePet});}finally{window.dispatchEvent(new Event('vool-companion-painted'));}};}


window.VoolPetCosmetics={draw,activity,play,refresh,ready,mount,t,meta,get library(){return library;},get error(){return error;},get activePet(){return activePet;},get mediaVersion(){return mediaVersion;},get renderedState(){return mainDraw?.state||'unknown';},get currentState(){return currentState;},get currentCategory(){return currentCategory;},get diagnostics(){return {images:images.size,audio:audio.size,sound:library.config.sound,started,plays,context:ctx?.state||'closed',active:!!playing};},unlock};
function boot(){if(started)return;started=true;wrap();ready();}if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',boot);else boot();window.addEventListener('focus',refresh);
})();
"""
